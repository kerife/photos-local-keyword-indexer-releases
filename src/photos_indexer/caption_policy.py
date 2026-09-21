"""Deterministic policy for short, visible captions.

Captions are model-controlled free text, so the prompt is not a security
boundary.  Keep this validator independent from the workflow/manifest layers
so scan, manifest loading, review and apply all enforce the same contract.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .places import sanitize_place_context, sanitize_trusted_context_names
from .taxonomy import (
    canonical_keyword_key,
    is_known_person_name_token,
    is_safe_visible_keyword,
    proposed_keywords,
)


_MAX_CAPTION_WORDS = 12
_CONTEXTUAL_PLACE_MARKERS = frozenset({
    "basílica", "basilica", "canal", "catedral", "cathedral", "castillo", "castle", "chiesa",
    "church", "edificio", "iglesia", "landmark", "monumento", "monument", "museo", "museum",
    "palacio", "palace", "palazzo", "plaza", "puente", "bridge", "templo", "temple", "torre",
})
_CURATED_SINGLE_WORD_PLACES = frozenset({
    "amalfi", "disney", "epcot", "florencia", "florence", "italia", "orlando", "paris", "parís",
    "venecia", "venice",
})
_CURATED_CONTEXTUAL_NAMES = frozenset({
    "mobil super",
    "estadio alfredo harp helú",
    "estadio alfredo harp helu",
    "diablos rojos del méxico",
    "diablos rojos del mexico",
    "diablos rojos",
    "rocco",
    "roccy",
})
# Captions may use the team short name even when the trusted local context
# supplies the full official name.  Keep this alias narrowly curated rather
# than accepting arbitrary partial person/place names.
_TRUSTED_CONTEXT_ALIASES = {"diablos rojos del mexico": ("Diablos Rojos",)}
_CAPTION_FALLBACK_NOUNS = frozenset({
    "persona", "grupo", "retrato", "animal", "mascota", "perro", "gato", "ave", "pez", "caballo", "insecto",
    "interior", "exterior", "casa", "habitacion", "cocina", "comedor", "sala", "oficina", "edificio", "arquitectura",
    "calle", "carretera", "puente", "parque", "jardin", "playa", "costa", "montana", "bosque", "campo", "desierto",
    "lago", "rio", "mar", "cielo", "ciudad", "pueblo", "sendero", "arbol", "flor", "planta", "hoja", "roca", "arena",
    "agua", "nieve", "lluvia", "nube", "sol", "sombra", "amanecer", "atardecer", "noche", "automovil", "bicicleta",
    "motocicleta", "tren", "avion", "barco", "autobus", "camion", "mesa", "silla", "cama", "sofa", "ventana", "puerta",
    "libro", "cuaderno", "mochila", "telefono", "computadora", "pantalla", "camara", "reloj", "lampara", "botella", "taza",
    "plato", "juguete", "herramienta", "comida", "bebida", "fruta", "verdura", "pan", "pastel", "ropa", "sombrero", "calzado",
    "gafas", "paisaje", "horizonte", "reflejo", "silueta", "texto", "documento", "letrero", "dibujo", "pintura", "escultura",
})
_CAPTION_FALLBACK_NOUNS = _CAPTION_FALLBACK_NOUNS | {"bebé"}
_CAPTION_FALLBACK_EMOTIONS = frozenset({"alegre", "triste", "llanto"})
_CAPTION_CONTEXT_ONLY_NOUNS = frozenset({"texto"})
_GENERIC_CAPTION_SUBJECTS = frozenset({
    "animal", "documento", "escena", "gente", "grupo", "mascota", "objeto", "paisaje", "persona",
    "personas", "retrato", "texto",
})
_CONTEXTUAL_NAME_PATTERN = re.compile(
    r"\b(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})(?:\s+(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})){0,7}\b"
)


def _fold_context(value: str) -> str:
    folded = value.casefold()
    return "".join(
        character for character in unicodedata.normalize("NFKD", folded)
        if not unicodedata.combining(character)
    )


def caption_needs_fallback(value: object, keywords: Iterable[str] = ()) -> bool:
    """Identify a low-quality model construction without rejecting old data."""
    if type(value) is not str:
        return False
    folded = _fold_context(" ".join(value.split()))
    if re.match(r"^\s*una\s+foto\s+de\s+texto\s*(?:,|\b(?:y|e)\b)", folded):
        return True
    match = re.fullmatch(
        r"una foto de (?:un |una )?(animal|documento|escena|gente|grupo|mascota|objeto|paisaje|persona|personas|retrato|texto)\.?",
        folded,
    )
    if match is None:
        return False
    accepted = proposed_keywords((), keywords)
    specific = {
        canonical_keyword_key(keyword)
        for keyword in accepted
        if canonical_keyword_key(keyword) not in _GENERIC_CAPTION_SUBJECTS
    }
    return len(specific) >= 2


def _safe_context_names(values: Iterable[str]) -> tuple[str, ...]:
    """Keep only inert, visible place labels suitable for caption masking."""
    names: list[str] = []
    for value in sanitize_place_context(tuple(values), max_results=8):
        folded = _fold_context(value)
        if folded not in _CURATED_CONTEXTUAL_NAMES and not is_safe_visible_keyword(value):
            continue
        words = folded.split()
        if len(words) == 1 and folded not in _CURATED_SINGLE_WORD_PLACES and folded not in _CURATED_CONTEXTUAL_NAMES:
            continue
        if len(words) > 1 and folded not in _CURATED_CONTEXTUAL_NAMES and not (
            folded in {"basilica de santa maria de la salud", "basilica della salute", "basilica di santa maria della salute"}
            or set(words) & _CONTEXTUAL_PLACE_MARKERS
        ):
            continue
        names.append(value)
    return tuple(names)


def _mask_contextual_names(
    caption: str,
    names: Iterable[str],
    *,
    infer_structural: bool,
    trusted_names: Iterable[str] = (),
) -> str:
    """Mask approved place spans before validating open semantic prose."""
    candidates = list(_safe_context_names(names))
    for name in sanitize_trusted_context_names(tuple(trusted_names)):
        if name not in candidates:
            candidates.append(name)
        aliases = _TRUSTED_CONTEXT_ALIASES.get(_fold_context(name), ())
        for alias in aliases:
            if alias not in candidates:
                candidates.append(alias)
    if infer_structural:
        for match in _CONTEXTUAL_NAME_PATTERN.finditer(caption):
            candidate = match.group(0)
            if candidate not in candidates and _safe_context_names((candidate,)):
                candidates.append(candidate)
    masked = caption
    for name in sorted(candidates, key=len, reverse=True):
        pattern = re.compile(rf"(?<![\wÁÉÍÓÚÜÑáéíóúüñ]){re.escape(name)}(?![\wÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)
        masked = pattern.sub("lugar", masked)
    return masked


def sanitize_caption(
    value: object,
    *,
    allowed_place_names: Iterable[str] = (),
    allowed_context_names: Iterable[str] = (),
    allow_contextual_places: bool = False,
) -> str | None:
    """Return a canonical safe caption, or ``None`` when it is unsafe.

    Reject control/format characters *before* whitespace normalization.  This
    prevents hidden NUL, bidi, zero-width and similar characters from being
    silently erased and turning an untrusted string into an apparently safe
    caption.  Semantic vocabulary is open, while literal names, OCR passages,
    URLs, contact data, coordinates and numeric field values do not pass.
    """
    if type(value) is not str:
        return None
    if any(unicodedata.category(character).startswith(("C",)) for character in value):
        return None
    caption = " ".join(value.split())
    if not caption or len(caption) > 240:
        return None
    if re.search(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s.,;:!?¡¿'\"()\-/]", caption):
        return None
    masked_caption = _mask_contextual_names(
        caption,
        allowed_place_names,
        infer_structural=allow_contextual_places,
        trusted_names=allowed_context_names,
    )
    # Count approved multi-word place names as one contextual entity. This
    # keeps captions brief without penalizing a verified landmark label.
    if len(masked_caption.split()) > _MAX_CAPTION_WORDS:
        return None
    lowered = masked_caption.casefold()
    folded = "".join(
        character for character in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(character)
    )
    literal_markers = (
        "http" + "://", "https" + "://", "www.", "@", "coordenada", "latitud", "longitud",
        "ocr", "transcripcion", "texto:", "dice:", "se lee", "texto literal",
        "se llama", "nombre completo", "identity", "called",
        "a child", "a dog", "a cat", "the dog", "the cat", "on the beach", "in the park", "with a",
    )
    if any(marker in folded for marker in literal_markers):
        return None
    if re.search(r"\b[^\s@]+@[^\s@]+\b|\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|mx|es)\b", folded):
        return None
    if any(character.isdigit() for character in caption):
        return None
    if re.search(r"\b[A-ZÁÉÍÓÚÜÑ]{2,}\b", masked_caption):
        return None
    if re.fullmatch(r"\s*[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}\s*", masked_caption):
        return None
    if re.match(r"\s*[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}\s+(?:está|esta|aparece|lleva|tiene|junto)\b", masked_caption):
        return None
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", masked_caption)
    if not words:
        return None
    folded_word_keys = [canonical_keyword_key(word) for word in words]
    if any(is_known_person_name_token(word) for word in words):
        return None
    if any(word in _CURATED_SINGLE_WORD_PLACES for word in folded_word_keys):
        return None
    if any(re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}", word) for word in words[1:]):
        return None
    return caption


def caption_from_visible_keywords(
    keywords: Iterable[str],
    *,
    allowed_place_names: Iterable[str] = (),
    allowed_context_names: Iterable[str] = (),
) -> str | None:
    """Build a short safe caption when free-form model text is rejected.

    This is deliberately a deterministic fallback: it can only use semantic
    keywords already accepted by the manifest policy and never transcribes
    literal image text.  The original model caption remains discarded.
    """
    contextual_names = tuple(allowed_place_names) + sanitize_trusted_context_names(
        tuple(allowed_context_names)
    )
    keyword_values = tuple(keywords)
    # If the model supplied only text/context markers plus a place echo, keep
    # the scene terms available to the bounded fallback instead of letting the
    # place consume the three-keyword budget.  A place paired with a concrete
    # subject (for example ``persona`` + ``Disney``) remains eligible below.
    has_scene_fallback = any(
        canonical_keyword_key(keyword) in _CAPTION_FALLBACK_NOUNS
        and canonical_keyword_key(keyword) not in _CAPTION_CONTEXT_ONLY_NOUNS
        for keyword in keyword_values
    )
    if not has_scene_fallback and not contextual_names:
        keyword_values = tuple(
            keyword for keyword in keyword_values
            if canonical_keyword_key(keyword) not in _CURATED_SINGLE_WORD_PLACES
        )
    accepted = proposed_keywords(
        (), keyword_values, maximum=3, allowed_contextual_keywords=contextual_names,
    )
    contextual_keys = {canonical_keyword_key(value) for value in contextual_names}
    visible_subjects = tuple(
        keyword for keyword in accepted
        if canonical_keyword_key(keyword) in _CAPTION_FALLBACK_NOUNS
        and canonical_keyword_key(keyword) not in _CAPTION_CONTEXT_ONLY_NOUNS
    )
    curated_place_names = tuple(
        keyword for keyword in accepted
        if visible_subjects
        and canonical_keyword_key(keyword) in _CURATED_SINGLE_WORD_PLACES
        and canonical_keyword_key(keyword) not in contextual_keys
    )
    curated_place_keys = {canonical_keyword_key(value) for value in curated_place_names}
    caption_contextual_names = contextual_names + tuple(
        keyword for keyword in curated_place_names
        if canonical_keyword_key(keyword) not in contextual_keys
    )
    contextual_keys = {canonical_keyword_key(value) for value in caption_contextual_names}
    nouns = [
        keyword for keyword in accepted
        if canonical_keyword_key(keyword) in _CAPTION_FALLBACK_NOUNS
        or canonical_keyword_key(keyword) in contextual_keys
    ]
    if not nouns:
        semantic_subjects = [
            keyword for keyword in accepted
            if canonical_keyword_key(keyword) not in _CAPTION_CONTEXT_ONLY_NOUNS
            and (
                canonical_keyword_key(keyword) not in _CURATED_SINGLE_WORD_PLACES
                or canonical_keyword_key(keyword) in contextual_keys
            )
        ]
        # Do not fall back to the complete accepted list: it may contain only
        # an uncontextualized curated place, which would produce a caption
        # rejected by the contextual-name policy.
        nouns = semantic_subjects
    if not nouns:
        return None
    contextual_nouns = [
        noun for noun in nouns if canonical_keyword_key(noun) in contextual_keys
    ]
    subject_nouns = [
        noun for noun in nouns if canonical_keyword_key(noun) not in contextual_keys
    ]
    scene_subject_nouns = [
        noun for noun in subject_nouns
        if canonical_keyword_key(noun) not in _CAPTION_CONTEXT_ONLY_NOUNS
    ]
    if scene_subject_nouns:
        subject_nouns = scene_subject_nouns
        nouns = subject_nouns + contextual_nouns
    elif subject_nouns:
        # ``texto`` is useful as a keyword but should not crowd out visible
        # scene terms when the model supplied no other fallback noun.
        semantic_subjects = [
            keyword for keyword in accepted
            if canonical_keyword_key(keyword) not in _CAPTION_CONTEXT_ONLY_NOUNS
            and (
                canonical_keyword_key(keyword) not in _CURATED_SINGLE_WORD_PLACES
                or canonical_keyword_key(keyword) in contextual_keys
            )
        ]
        if semantic_subjects:
            subject_nouns = semantic_subjects
            nouns = subject_nouns + contextual_nouns
    if subject_nouns and all(
        canonical_keyword_key(noun) in _GENERIC_CAPTION_SUBJECTS
        for noun in subject_nouns
    ):
        subject_keys = {canonical_keyword_key(noun) for noun in subject_nouns}
        specific_subjects = [
            keyword for keyword in accepted
            if canonical_keyword_key(keyword) not in subject_keys
            and canonical_keyword_key(keyword) not in _GENERIC_CAPTION_SUBJECTS
            and canonical_keyword_key(keyword) not in _CAPTION_CONTEXT_ONLY_NOUNS
            and canonical_keyword_key(keyword) not in contextual_keys
            and canonical_keyword_key(keyword) not in _CURATED_SINGLE_WORD_PLACES
        ]
        subject_nouns.extend(specific_subjects[: max(0, 3 - len(subject_nouns))])
        nouns = subject_nouns + contextual_nouns
    visible_emotions = [
        keyword for keyword in accepted
        if canonical_keyword_key(keyword) in _CAPTION_FALLBACK_EMOTIONS
    ]
    subject_base = [
        noun for noun in subject_nouns
        if canonical_keyword_key(noun) not in _CAPTION_FALLBACK_EMOTIONS
    ]
    if len(visible_emotions) == 1 and subject_base:
        emotion = visible_emotions[0]
        if canonical_keyword_key(emotion) == "llanto":
            emotion_subject = f"{subject_base[0]} con llanto" if len(subject_base) == 1 else None
        else:
            emotion_subject = f"{subject_base[0]} {emotion}" if len(subject_base) == 1 else None
        if emotion_subject is not None:
            subject_nouns = [emotion_subject]
            nouns = subject_nouns + contextual_nouns
    if contextual_nouns and subject_nouns:
        if len(subject_nouns) == 1:
            subject = subject_nouns[0]
        else:
            subject_conjunction = "e" if canonical_keyword_key(subject_nouns[-1]).startswith(("i", "hi")) else "y"
            subject = f"{', '.join(subject_nouns[:-1])} {subject_conjunction} {subject_nouns[-1]}"
        if len(contextual_nouns) == 1:
            place = contextual_nouns[0]
        else:
            place_conjunction = "e" if canonical_keyword_key(contextual_nouns[-1]).startswith(("i", "hi")) else "y"
            place = f"{', '.join(contextual_nouns[:-1])} {place_conjunction} {contextual_nouns[-1]}"
        joined = f"{subject} en {place}"
    elif len(nouns) == 1:
        joined = nouns[0]
    elif len(nouns) == 2 and sum(
        canonical_keyword_key(noun) in curated_place_keys for noun in nouns
    ) == 1:
        place = next(noun for noun in nouns if canonical_keyword_key(noun) in curated_place_keys)
        subject = next(noun for noun in nouns if noun != place)
        joined = f"{subject} en {place}"
    elif len(nouns) == 2:
        conjunction = "e" if canonical_keyword_key(nouns[1]).startswith(("i", "hi")) else "y"
        joined = f"{nouns[0]} {conjunction} {nouns[1]}"
    else:
        conjunction = "e" if canonical_keyword_key(nouns[-1]).startswith(("i", "hi")) else "y"
        joined = f"{', '.join(nouns[:-1])} {conjunction} {nouns[-1]}"
    return sanitize_caption(
        f"Una foto de {joined}.",
        allowed_place_names=caption_contextual_names,
        allowed_context_names=allowed_context_names,
    )
