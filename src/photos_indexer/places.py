"""Apple Maps nearby-place lookup, isolated behind a testable adapter."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any


class PlacesLookupError(RuntimeError):
    """Raised only when the MapKit bridge cannot be created."""


PLACE_LOOKUP_STATES = frozenset({
    "not_requested",
    "no_location",
    "cancelled",
    "error",
    "timeout",
    "no_results",
    "results",
    "results_filtered",
})


@dataclass(frozen=True, slots=True)
class PlaceLookupResult:
    """Sanitized Apple Maps lookup result with non-sensitive diagnostics."""

    names: tuple[str, ...]
    state: str

    def __post_init__(self) -> None:
        if self.state not in PLACE_LOOKUP_STATES:
            raise ValueError("invalid place lookup state")
        sanitized = sanitize_place_context(self.names)
        if sanitized != tuple(self.names):
            raise ValueError("place lookup result names must be sanitized")


# Apple Maps may return labels using either a dot or a locale decimal comma.
# Keep this shape-only guard deliberately strict: a pair of numeric components
# separated by punctuation/whitespace is never a useful POI label, while
# ordinary names such as ``Ruta 66`` remain unaffected.
_COORDINATE_COMPONENT = r"[+-]?\d{1,3}(?:(?:\.\d{1,8})|(?:,\d{1,8}))?"
_COORDINATE_LIKE_PLACE = re.compile(
    rf"^{_COORDINATE_COMPONENT}\s*(?:[,;]|\s+)\s*{_COORDINATE_COMPONENT}$",
)
_URI_OR_CONTACT_LIKE_PLACE = re.compile(
    r"(?i)(?:\b(?:https?|ftp|file|data|javascript|mailto|tel):|(?:^|\s)www\.)|\b[^\s@]+@[^\s@]+\b",
)
_ADDRESS_LIKE_PLACE = re.compile(
    r"(?i)^\d{1,6}(?:[A-Za-zÀ-ÿ0-9.'-]*\s+){0,5}"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|terrace|place|plaza|"
    r"calle|avenida|av|carretera|camino)\b"
)
_PROMPT_CONTROL_WORDS = frozenset({
    "assistant", "caption", "change", "cambia", "dime", "developer", "do", "ejecuta", "execute",
    "forget", "follow", "haz", "ignore", "ignora", "instruction", "instructions", "instruccion",
    "instrucciones", "json", "keyword", "keywords", "omite", "olvida", "output", "override", "prompt",
    "reveal", "return", "respond", "responde", "say", "sigue", "system", "tell", "user", "devuelve",
})
_LOCALIZED_INSTRUCTION_WORD_PAIRS = (
    (
        frozenset({"segui", "ignorare", "restituisci", "rispondi", "rivela", "esegui"}),
        frozenset({"istruzione", "istruzioni", "regola", "regole"}),
    ),
    (
        frozenset({"siga", "seguir", "ignorar", "devolva", "retorne", "responda", "revele", "execute"}),
        frozenset({"instrucao", "instrucoes", "regra", "regras", "etiqueta", "etiquetas"}),
    ),
    (
        frozenset({"befolge", "befolgen", "ignoriere", "ignorieren", "antworte", "enthulle", "enthullen"}),
        frozenset({"anweisung", "anweisungen", "regel", "regeln", "schlusselwort", "schlusselworter"}),
    ),
)
_LOCALIZED_INSTRUCTION_FRAGMENTS = (
    ("指示", "無視"),
    ("命令", "無視"),
    ("지시", "무시"),
)
_INSTRUCTION_LIKE_PLACE = re.compile(
    r"(?:\b(?:ignore|disregard|follow|override|return|output|respond|reveal|system|prompt|forget|execute|change|act|tell|say|do)\b"
    r".*\b(?:instruction|instructions|prompt|json|keywords?|caption|only|instead|previous|rules?|dog|cat|all)\b)"
    r"|(?:\b(?:sigue|ignora|omite|devuelve|responde|obedece|revela|olvida|ejecuta|cambia|actúa|actua|haz|dime)\b"
    r".*\b(?:todo|anterior|reglas?|instrucciones?|prompt|json|keywords?|etiquetas?|caption|únicamente|solo|perro|gato)\b)",
    re.IGNORECASE,
)

_ALFREDO_HARP_HELU_ALIASES = frozenset({
    "estadio alfredo harp helu",
    "estadio alfredo harp helú",
})
_ALFREDO_HARP_HELU_ENTITIES = (
    "Estadio Alfredo Harp Helú",
    "Diablos Rojos del México",
    "Rocco",
    "Roccy",
)


def _semantic_key(value: str) -> str:
    folded = "".join(
        character for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(folded.split())


def _has_alfredo_harp_helu_context(values: object) -> bool:
    if not isinstance(values, (tuple, list)):
        return False
    return any(
        isinstance(value, str) and _semantic_key(value) in _ALFREDO_HARP_HELU_ALIASES
        for value in values
    )


def trusted_venue_prompt_context(values: object) -> str:
    """Return source-curated venue facts only for an exact Maps alias."""
    if not _has_alfredo_harp_helu_context(values):
        return ""
    return (
        " Contexto factual local curado: el Estadio Alfredo Harp Helú es sede de los Diablos Rojos del México. "
        "Rocco es la mascota gris con cuernos rojos, ojos grandes y uniforme rojo o blanco del equipo; Roccy es "
        "otra mascota distinta. Usa estos nombres solo si la imagen confirma el estadio y los rasgos visibles. "
        "Si la mascota coincide claramente con Rocco, escribe exactamente «Rocco» en una keyword y en el caption; "
        "si no puedes distinguirla, usa «mascota de Diablos Rojos» sin inventar el nombre."
    )


def sanitize_trusted_context_names(values: object) -> tuple[str, ...]:
    """Canonicalize only names present in the bundled venue knowledge."""
    if not isinstance(values, (tuple, list)):
        return ()
    canonical = {_semantic_key(value): value for value in _ALFREDO_HARP_HELU_ENTITIES}
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = canonical.get(_semantic_key(value))
        if name is not None and name not in result:
            result.append(name)
    return tuple(result)


def confirmed_venue_entities(
    place_context: object,
    candidates: object,
    caption: object,
) -> tuple[str, ...]:
    """Confirm curated names only when Maps and visible model evidence agree."""
    if (
        not _has_alfredo_harp_helu_context(place_context)
        or not isinstance(candidates, (tuple, list))
        or not isinstance(caption, str)
    ):
        return ()
    candidate_keys = {
        _semantic_key(value)
        for value in candidates
        if isinstance(value, str)
    }
    caption_key = _semantic_key(caption)
    venue_mentioned = any(
        alias in candidate_keys or alias in caption_key
        for alias in _ALFREDO_HARP_HELU_ALIASES
    )
    venue_cues = {
        "estadio", "estadio deportivo", "evento deportivo", "gradas", "publico", "campo de beisbol",
    }
    if not venue_mentioned or not candidate_keys & venue_cues:
        return ()
    combined = " ".join((*candidate_keys, caption_key))
    result = ["Estadio Alfredo Harp Helú"]
    if "diablos rojos del mexico" in combined:
        result.append("Diablos Rojos del México")
    if candidate_keys & {"mascota", "mascota oficial", "personaje"}:
        if re.search(r"\brocco\b", combined):
            result.append("Rocco")
        elif re.search(r"\broccy\b", combined):
            result.append("Roccy")
    return tuple(result)


def _safe_place_name(value: object) -> str | None:
    """Return a bounded inert POI label, never an instruction channel."""
    if not isinstance(value, str):
        return None
    name = " ".join(unicodedata.normalize("NFC", value).split())
    if not name or len(name) > 160 or any(unicodedata.category(character).startswith("C") for character in name):
        return None
    if (
        _COORDINATE_LIKE_PLACE.fullmatch(name)
        or _ADDRESS_LIKE_PLACE.fullmatch(name)
        or _INSTRUCTION_LIKE_PLACE.search(name)
        or _URI_OR_CONTACT_LIKE_PLACE.search(name)
    ):
        return None
    # Apple Maps labels are external data and are interpolated into a vision
    # prompt.  Reject directive vocabulary even when it does not form one of
    # the longer instruction phrases above (for example ``SYSTEM: say perro``).
    folded = "".join(
        character for character in unicodedata.normalize("NFKD", name.casefold())
        if not unicodedata.combining(character)
    )
    words = set(re.findall(r"[^\W\d_]+", folded))
    if (
        words & _PROMPT_CONTROL_WORDS
        or any(words & directives and words & targets for directives, targets in _LOCALIZED_INSTRUCTION_WORD_PAIRS)
        or any(all(fragment in folded for fragment in fragments) for fragments in _LOCALIZED_INSTRUCTION_FRAGMENTS)
    ):
        return None
    return name


def sanitize_place_context(values: object, *, max_results: int = 8) -> tuple[str, ...]:
    """Sanitize and bound external POI labels before prompt interpolation."""
    if not isinstance(values, (tuple, list)) or type(max_results) is not int or max_results < 1:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = _safe_place_name(value)
        if name is None:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) == max_results:
            break
    return tuple(names)


def _objc_value(value: Any) -> Any:
    return value() if callable(value) else value


def _safe_location(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    latitude, longitude = value
    if type(latitude) not in (int, float) or type(longitude) not in (int, float):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None
    return latitude, longitude


def _default_search_factory(location: tuple[float, float], radius_m: float) -> Any:
    try:
        import MapKit  # type: ignore[import-not-found]
    except ImportError as error:
        raise PlacesLookupError("pyobjc-framework-MapKit is required for Apple Maps context") from error
    return _MapKitBatchSearch(MapKit, location, radius_m)


class _MapKitBatchSearch:
    # Run several narrow POI searches because MapKit's natural-language search
    # does not expose a stable "historic buildings" category.  The extra
    # categories materially improve recall for cathedrals, palaces and named
    # attractions while the caller still bounds and sanitizes the final names.
    _queries = (
        "landmark", "monument", "historic landmark", "attraction", "basilica", "church", "museum",
        "cathedral", "palace", "historic site",
        "stadium", "baseball stadium", "sports venue", "baseball park",
    )
    _max_parallel = 4

    def __init__(self, mapkit: Any, location: tuple[float, float], radius_m: float) -> None:
        self._mapkit = mapkit
        self._location = location
        self._radius_m = radius_m
        self._searches: list[Any] = []
        self._result_batches: dict[int, tuple[Any, ...]] = {}
        self._next_query_index = 0
        self._active_searches = 0
        self._successful = False
        self._last_error: Any = None
        self._completion: Callable[[Any, Any], None] | None = None
        self._finished = False
        self._cancelled = False

    @property
    def partial_items(self) -> tuple[Any, ...]:
        """Expose only already-returned items when the batch times out."""
        return tuple(
            item
            for index in sorted(self._result_batches)
            for item in self._result_batches[index]
        )

    def cancel(self) -> None:
        """Cancel the active and already-created category searches."""
        self._cancelled = True
        for search in tuple(self._searches):
            _cancel_search(search)

    def startWithCompletionHandler_(self, completion: Callable[[Any, Any], None]) -> None:
        self._completion = completion
        self._launch_available()

    def _launch_available(self) -> None:
        while (
            not self._cancelled
            and not self._finished
            and self._active_searches < self._max_parallel
            and self._next_query_index < len(self._queries)
        ):
            index = self._next_query_index
            self._next_query_index += 1
            region = self._mapkit.MKCoordinateRegionMakeWithDistance(
                self._location, self._radius_m * 2, self._radius_m * 2
            )
            request = self._mapkit.MKLocalSearchRequest.alloc().initWithNaturalLanguageQuery_region_(
                self._queries[index], region
            )
            request.setResultTypes_(
                self._mapkit.MKLocalSearchResultTypePointOfInterest | self._mapkit.MKLocalSearchResultTypePhysicalFeature
            )
            search = self._mapkit.MKLocalSearch.alloc().initWithRequest_(request)
            self._searches.append(search)
            self._active_searches += 1

            def finished(response: Any, error: Any, query_index: int = index) -> None:
                self._finish_query(query_index, response, error)

            try:
                search.startWithCompletionHandler_(finished)
            except Exception as error:
                self._finish_query(index, None, error)
        self._finish_if_complete()

    def _finish_query(self, index: int, response: Any, error: Any) -> None:
        if self._cancelled or self._finished:
            return
        if error is not None:
            # A single category can fail because the query is not available
            # in a locale or MapKit returns a transient error. Continue with
            # the remaining categories; only report an error when every
            # category failed.
            self._last_error = error
        else:
            self._successful = True
            items = _objc_value(getattr(response, "mapItems", None)) if response is not None else ()
            self._result_batches[index] = tuple(items or ())
        self._active_searches = max(0, self._active_searches - 1)
        self._launch_available()

    def _finish_if_complete(self) -> None:
        if (
            self._cancelled
            or self._finished
            or self._active_searches != 0
            or self._next_query_index < len(self._queries)
            or self._completion is None
        ):
            return
        self._finished = True
        response = type("Response", (), {"mapItems": list(self.partial_items)})()
        self._completion(response, None if self._successful else self._last_error)


class AppleMapsPlacesClient:
    """Fetch nearby Apple Maps POI names without retaining coordinates/results."""

    def __init__(
        self,
        *,
        search_factory: Callable[[tuple[float, float], float], Any] | None = None,
        radius_m: float = 250.0,
        max_results: int = 8,
        # The batch performs several narrow POI searches sequentially.  Eight
        # seconds was too short on a real Photos run: every lookup reached the
        # deadline before the final categories (monuments/attractions) could
        # return, so Apple Maps was reported as used but yielded no context.
        timeout: float = 20.0,
    ) -> None:
        if (
            type(radius_m) not in (int, float)
            or type(max_results) is not int
            or type(timeout) not in (int, float)
            or radius_m <= 0
            or max_results < 1
            or timeout <= 0
        ):
            raise ValueError("invalid Apple Maps lookup limits")
        self._search_factory = search_factory or _default_search_factory
        self._radius_m = radius_m
        self._max_results = max_results
        self._timeout = timeout

    def nearby(
        self,
        location: tuple[float, float] | None,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[str, ...]:
        """Return sanitized POI names, stopping an active lookup on cancellation."""
        return self.nearby_with_status(location, cancel_requested=cancel_requested).names

    def nearby_with_status(
        self,
        location: tuple[float, float] | None,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> PlaceLookupResult:
        """Return sanitized POI names and the non-sensitive lookup outcome."""
        cancel_requested = cancel_requested or (lambda: False)
        if location is None:
            return PlaceLookupResult((), "no_location")
        safe_location = _safe_location(location)
        if safe_location is None:
            return PlaceLookupResult((), "no_location")
        location = safe_location
        if cancel_requested():
            return PlaceLookupResult((), "cancelled")
        try:
            search = self._search_factory(location, self._radius_m)
        except Exception:
            return PlaceLookupResult((), "error")
        if cancel_requested():
            _cancel_search(search)
            return PlaceLookupResult((), "cancelled")
        event = threading.Event()
        result: list[Any] = [None, None]

        def complete(response: Any, error: Any) -> None:
            result[0] = response
            result[1] = error
            event.set()

        try:
            search.startWithCompletionHandler_(complete)
        except Exception:
            return PlaceLookupResult((), "error")
        completed = _wait_for_mapkit(event, self._timeout, cancel_requested=cancel_requested)
        if cancel_requested():
            _cancel_search(search)
            return PlaceLookupResult((), "cancelled")
        if not completed:
            _cancel_search(search)
            partial_names = [
                _objc_value(getattr(item, "name", None))
                for item in getattr(search, "partial_items", ())
            ]
            sanitized = sanitize_place_context(
                partial_names,
                max_results=self._max_results,
            )
            if sanitized:
                return PlaceLookupResult(sanitized, "timeout")
            return PlaceLookupResult((), "timeout")
        if result[1] is not None:
            return PlaceLookupResult((), "error")
        response = result[0]
        items = _objc_value(getattr(response, "mapItems", None))
        if items is None:
            return PlaceLookupResult((), "no_results")
        raw_names = [_objc_value(getattr(item, "name", None)) for item in items]
        sanitized = sanitize_place_context(
            raw_names,
            max_results=self._max_results,
        )
        if sanitized:
            return PlaceLookupResult(sanitized, "results")
        if raw_names:
            return PlaceLookupResult((), "results_filtered")
        return PlaceLookupResult((), "no_results")


def _cancel_search(search: Any) -> None:
    cancel = getattr(search, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass


def _wait_for_mapkit(
    event: threading.Event,
    timeout: float,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> bool:
    """Pump the macOS run loop so MapKit's main-queue callback can arrive."""
    cancel_requested = cancel_requested or (lambda: False)
    try:
        from Foundation import NSDate, NSRunLoop  # type: ignore[import-not-found]
    except ImportError:
        deadline = time.monotonic() + timeout
        while not event.is_set():
            if cancel_requested():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            event.wait(min(remaining, 0.25))
        return True
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if cancel_requested():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        until = NSDate.dateWithTimeIntervalSinceNow_(min(remaining, 0.25))
        NSRunLoop.currentRunLoop().runMode_beforeDate_("kCFRunLoopDefaultMode", until)
    return True
