from __future__ import annotations

import fcntl
import hashlib
import math
import os
import platform
import re
import signal as signal_library
import shutil
import stat
import threading
import time
import unicodedata
import uuid as uuid_module
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .adapters import (
    AutonomousPhotoKitSelector,
    LocalExportStorageError,
    OllamaEndpointUnavailableError,
    OllamaImagePreparationError,
    OllamaModelMissingError,
    OllamaModelPolicyError,
    OllamaNoVisionError,
    OllamaRequestError,
    OllamaResponseError,
    OllamaVisionClient,
    OllamaVersionTooOldError,
    PhotoScriptPermissionError,
    PhotoScriptUnavailableError,
    PhotoKitSelector,
    PhotosAccessError,
    PhotoScriptBridge,
    ScriptPhotoRecord,
    TemporaryExportWorkspace,
    _vision_prompt,
    validate_ollama_model_name,
)
from .caption_policy import caption_from_visible_keywords as _fallback_caption
from .caption_policy import caption_needs_fallback as _caption_needs_fallback
from .caption_policy import sanitize_caption as _safe_caption
from .landmarks import OfflineLandmarkResolver
from .places import AppleMapsPlacesClient, confirmed_venue_entities, sanitize_place_context
from .manifest import (
    MANIFEST_FILENAME,
    PhotoRecord,
    ScanManifest,
    TechnicalTrace,
    build_scan_manifest,
    compute_mutation_digest,
    compute_rollback_digest,
    load_manifest,
    reviewed_rows_match_source,
    _reject_symlinked_ancestors,
    write_manifest,
    write_preview_csv,
    safe_projection_text,
)
from .taxonomy import canonical_keyword_key, proposed_keywords


APP_NAME = "photos-local-keyword-indexer"
APP_VERSION = "0.1.1"
DEFAULT_MODEL = "qwen3-vl:4b"
DEFAULT_DETAILED_MODEL = DEFAULT_MODEL
DEFAULT_LIMIT = 20
CONFIDENCE_THRESHOLD = 0.60
RUN_DIRECTORY_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")
_PERMISSION_ERROR_CODES = frozenset({"PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_global_lock_path() -> Path:
    return Path("/tmp") / f".photos-local-keyword-indexer-{os.getuid()}" / "mutation.lock"


def _recover_stale_workspaces(
    runs_root: Path,
    *,
    recover: Callable[[Path], list[Path]] = TemporaryExportWorkspace.recover,
) -> list[Path]:
    removed: list[Path] = []
    for candidate in sorted(Path(runs_root).iterdir()):
        if RUN_DIRECTORY_PATTERN.fullmatch(candidate.name) is None:
            continue
        try:
            details = candidate.lstat()
        except OSError:
            continue
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            continue
        removed.extend(recover(candidate))
    return removed


def _discard_failed_run(run_dir: Path) -> None:
    """Remove only the private run directory created by a failed persist."""
    try:
        details = run_dir.lstat()
    except OSError:
        return
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        return
    try:
        shutil.rmtree(run_dir)
    except OSError:
        # Keep the original persistence failure as the workflow result. The
        # next scan's stale-workspace recovery remains responsible for owned
        # export remnants.
        pass


@contextmanager
def _scoped_signal_cleanup(workspace: Any, *, signal_module: Any = signal_library) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous: dict[int, object] = {}

    def handler(signum: int, frame: object) -> None:
        workspace.cleanup()
        prior = previous[signum]
        if callable(prior):
            prior(signum, frame)
        elif prior == signal_module.SIG_IGN:
            return
        elif signum == signal_module.SIGINT:
            raise KeyboardInterrupt
        else:
            raise SystemExit(128 + signum)

    try:
        for signum in (signal_module.SIGINT, signal_module.SIGTERM):
            previous[signum] = signal_module.signal(signum, handler)
        yield
    finally:
        for signum, prior in previous.items():
            signal_module.signal(signum, prior)


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    exit_code: int
    manifest_path: Path | None = None
    manifest: ScanManifest | None = None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    safe_instruction: str | None = None
    counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    status_summary: Mapping[str, Any] = field(default_factory=dict)
    next_action: str = "none"


@dataclass(frozen=True, slots=True)
class ModelPlan:
    """Deterministic local model selection for one scan."""

    policy: str
    fast_model: str
    detailed_model: str

    @property
    def required_models(self) -> tuple[str, ...]:
        if self.policy == "adaptive" and self.fast_model != self.detailed_model:
            return self.fast_model, self.detailed_model
        return (self.fast_model,)

    def model_for_location(self, location: tuple[float, float] | None) -> tuple[str, str]:
        if self.policy == "adaptive" and location is not None:
            return self.detailed_model, "location_context"
        if self.policy == "adaptive":
            return self.fast_model, "no_location"
        return self.fast_model, "single_policy"


def resolve_model_plan(
    *,
    policy: str = "single",
    model: str | None = None,
    fast_model: str = DEFAULT_MODEL,
    detailed_model: str = DEFAULT_DETAILED_MODEL,
) -> ModelPlan:
    """Validate a scan's routing policy without changing Ollama state."""
    def local_model(value: str) -> str:
        validated = validate_ollama_model_name(value)
        if "cloud" in validated.casefold():
            raise ValueError("cloud models are not allowed")
        return validated

    if type(policy) is not str or policy not in {"single", "adaptive"}:
        raise ValueError("model policy is invalid")
    if policy == "adaptive" and model is not None:
        raise ValueError("adaptive policy cannot include an explicit single model")
    if policy == "single":
        selected = local_model(model or fast_model)
        return ModelPlan("single", selected, selected)
    return ModelPlan(
        "adaptive",
        local_model(fast_model),
        local_model(detailed_model),
    )


@dataclass(frozen=True, slots=True)
class ScanDependencies:
    platform_name: Callable[[], str] = platform.system
    now_utc: Callable[[], datetime] = _utc_now
    uuid4: Callable[[], uuid_module.UUID] = uuid_module.uuid4
    selector_factory: Callable[[], Any] = PhotoKitSelector
    bridge_factory: Callable[[], Any] = PhotoScriptBridge
    vision_factory: Callable[[], Any] = OllamaVisionClient
    landmark_resolver_factory: Callable[[], OfflineLandmarkResolver] = OfflineLandmarkResolver
    places_factory: Callable[[], AppleMapsPlacesClient] = AppleMapsPlacesClient
    workspace_factory: Callable[[Path, str], Any] = TemporaryExportWorkspace
    recover_workspaces: Callable[[Path], list[Path]] = _recover_stale_workspaces
    signal_scope: Callable[[Any], Any] = _scoped_signal_cleanup


@dataclass(frozen=True, slots=True)
class ApplyDependencies:
    selector_factory: Callable[[], Any] = PhotoKitSelector
    bridge_factory: Callable[[], Any] = PhotoScriptBridge
    global_lock_path: Path = field(default_factory=_default_global_lock_path)


def _mutation_dependencies(manifest_path: Path, dependencies: ApplyDependencies | None) -> ApplyDependencies:
    # Import lazily: the campaign validator itself shares manifest/workflow
    # primitives. The link must validate before constructing native adapters.
    from .autonomous_runtime import autonomous_run_provenance

    provenance = autonomous_run_provenance(manifest_path)
    if dependencies is not None:
        return dependencies
    if provenance is not None:
        return ApplyDependencies(selector_factory=AutonomousPhotoKitSelector)
    return ApplyDependencies()


def _permission_next_action(code: str) -> str | None:
    """Return the only safe recovery action for a Photos permission error."""
    return {
        "PHOTOS_ACCESS_DENIED": "grant_photos_access",
        "PHOTOS_AUTOMATION_DENIED": "grant_photos_automation",
    }.get(code)


def _manifest_permission_codes(manifest: ScanManifest) -> tuple[str, ...]:
    """Return permission errors in stable order for safe recovery guidance."""
    return tuple(sorted({
        error["code"]
        for photo in manifest.photos
        for error in photo.errors
        if error["code"] in _PERMISSION_ERROR_CODES
    } | {
        error["code"]
        for error in manifest.run_errors
        if error["code"] in _PERMISSION_ERROR_CODES
    }))


def _manifest_mutation_permission_codes(manifest: ScanManifest) -> tuple[str, ...]:
    """Return TCC failures observed while an apply or rollback was active."""
    return tuple(sorted({
        error["code"]
        for photo in manifest.photos
        for error in photo.errors
        if error["stage"] in {"apply", "rollback"} and error["code"] in _PERMISSION_ERROR_CODES
    }))


def _manifest_mutation_has_fatal_compatibility_error(manifest: ScanManifest) -> bool:
    """Return whether a mutation cannot be retried until PhotoScript is fixed."""
    return any(
        error["stage"] in {"apply", "rollback"}
        and error["code"] == "PHOTOSCRIPT_UNAVAILABLE"
        for photo in manifest.photos
        for error in photo.errors
    )


def _manifest_scan_has_fatal_compatibility_error(manifest: ScanManifest) -> bool:
    """Return whether a scan cannot be retried until PhotoScript is fixed."""
    return any(
        error["stage"] in {"metadata", "export", "analysis"}
        and error["code"] == "PHOTOSCRIPT_UNAVAILABLE"
        for photo in manifest.photos
        for error in photo.errors
    )


def _fatal(
    code: str,
    *,
    safe_instruction: str | None = None,
    next_action: str = "fix_fatal_error",
) -> WorkflowResult:
    return WorkflowResult(
        exit_code=2,
        error_codes=(code,),
        safe_instruction=safe_instruction,
        next_action=_permission_next_action(code) or next_action,
    )


def _ollama_preflight_code(error: BaseException) -> str:
    """Map local preflight failures to bounded, actionable UI diagnostics."""
    if isinstance(error, OllamaModelPolicyError):
        return "MODEL_INVALID"
    if isinstance(error, OllamaEndpointUnavailableError):
        return "OLLAMA_UNAVAILABLE"
    if isinstance(error, OllamaVersionTooOldError):
        return "OLLAMA_VERSION_OLD"
    if isinstance(error, OllamaNoVisionError):
        return "OLLAMA_NO_VISION"
    return "OLLAMA_PREFLIGHT_FAILED"


def _ollama_fatal(error: BaseException) -> WorkflowResult:
    code = _ollama_preflight_code(error)
    return WorkflowResult(
        exit_code=2,
        error_codes=(code,),
        next_action="fix_fatal_error" if code == "MODEL_INVALID" else "retry_preflight",
    )


def _adapter_error_code(error: BaseException, fallback: str) -> str:
    """Expose bounded permission diagnostics without persisting AppleScript text."""
    if isinstance(error, (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError)):
        return error.code
    if isinstance(error, OllamaEndpointUnavailableError):
        return "OLLAMA_UNAVAILABLE"
    if isinstance(error, OllamaVersionTooOldError):
        return "OLLAMA_VERSION_OLD"
    if isinstance(error, OllamaNoVisionError):
        return "OLLAMA_NO_VISION"
    if isinstance(error, OllamaModelPolicyError):
        return "MODEL_INVALID"
    if isinstance(error, OllamaModelMissingError):
        return "OLLAMA_MODEL_MISSING"
    if isinstance(error, OllamaResponseError):
        return "OLLAMA_RESPONSE_INVALID"
    if isinstance(error, OllamaRequestError):
        return "OLLAMA_REQUEST_FAILED"
    if isinstance(error, OllamaImagePreparationError):
        return "VISION_IMAGE_INVALID"
    return fallback


class _IdentityMismatchError(ValueError):
    pass


def _safe_photo_metadata(value: object, expected_local_id: str) -> ScriptPhotoRecord | None:
    if isinstance(value, ScriptPhotoRecord):
        if (
            type(value.uuid) is not str
            or type(value.local_id) is not str
            or value.local_id != expected_local_id
            or type(value.title) is not str
            or not isinstance(value.date, datetime)
            or value.date.tzinfo is not None
            or type(value.existing_keywords) not in {tuple, list}
            or any(type(keyword) is not str for keyword in value.existing_keywords)
            or type(value.description) is not str
            or not _safe_metadata_location(value.location)
        ):
            return None
        try:
            uuid_module.UUID(value.uuid)
        except (TypeError, ValueError, AttributeError):
            pass
        else:
            if value.local_id == expected_local_id:
                return value
    return None


def _safe_metadata_location(value: object) -> bool:
    if value is None:
        return True
    if type(value) not in {tuple, list} or len(value) != 2:
        return False
    if type(value[0]) not in {int, float} or type(value[1]) not in {int, float}:
        return False
    try:
        latitude = float(value[0])
        longitude = float(value[1])
    except (OverflowError, TypeError, ValueError):
        return False
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _landmark_visual_match(
    candidates: tuple[str, ...],
    landmark_name: str,
    *,
    aliases: tuple[str, ...] = (),
) -> bool:
    """Require explicit model confirmation plus an independent visual cue.

    GPS proximity only supplies a candidate.  A generic description such as
    ``edificio`` or ``cúpula`` must never turn that candidate into a named
    landmark.  The model must return the landmark name (or a curated alias)
    as one keyword and a separate architecture cue visible in the image.
    """
    confirmation_names = {
        canonical_keyword_key(name)
        for name in (landmark_name, *aliases)
        if isinstance(name, str) and name.strip()
    }
    if not confirmation_names:
        return False
    normalized_candidates = tuple(canonical_keyword_key(candidate) for candidate in candidates)
    if not any(candidate in confirmation_names for candidate in normalized_candidates):
        return False
    architecture_cues = {
        "iglesia", "cúpula", "edificio", "arquitectura", "monumento", "basílica",
        "edificio histórico", "monumento arquitectónico", "fachada de piedra", "fachadas de ladrillo",
        "arquitectura veneciana", "canal veneciano", "historic building", "architectural landmark",
        "stone facade", "brick facades", "venetian architecture", "venetian canal",
        "estadio", "estadio deportivo", "gradas", "campo de béisbol", "baseball stadium",
    }
    # A canal/boat is useful evidence for a waterway POI, but cannot identify
    # a nearby building that the model may have echoed from Apple Maps.
    if not set(canonical_keyword_key(landmark_name).split()) & _BUILDING_LANDMARK_MARKERS:
        architecture_cues.update({"canal", "góndola"})
    # Keep this cue independent from the named candidate: the landmark name
    # itself (for example, ``Basílica ...``) is not visual confirmation.
    return any(
        _visual_architecture_cue_matches(candidate, architecture_cues)
        for candidate in candidates
        if canonical_keyword_key(candidate) not in confirmation_names
    )


def _visual_architecture_cue_matches(candidate: str, cues: set[str]) -> bool:
    """Match visible architecture wording across accents, plurals and modifiers.

    Model wording is not a stable taxonomy: ``edificios historicos`` and
    ``edificio histórico de piedra`` are both useful visual evidence.  This
    comparison is intentionally limited to the fixed architecture cue set and
    never broadens the set of names that can be written to Photos.
    """
    def tokens(value: str) -> tuple[str, ...]:
        folded = "".join(
            character for character in unicodedata.normalize("NFKD", value.casefold())
            if not unicodedata.combining(character)
        )
        result: list[str] = []
        for token in folded.split():
            if len(token) > 3 and token.endswith("s"):
                token = token[:-1]
            result.append(token)
        return tuple(result)

    candidate_tokens = tokens(candidate)
    if not candidate_tokens:
        return False
    for cue in cues:
        cue_tokens = tokens(cue)
        if len(cue_tokens) > len(candidate_tokens):
            continue
        if any(
            candidate_tokens[index:index + len(cue_tokens)] == cue_tokens
            for index in range(len(candidate_tokens) - len(cue_tokens) + 1)
        ):
            return True
    return False


_NAMED_LANDMARK_MARKERS = frozenset({
    "basílica", "basilica", "catedral", "cathedral", "castello", "castillo", "castle", "chiesa", "church",
    "iglesia", "monumento", "monument", "museo", "museum", "museu", "palacio", "palace",
    "palazzo", "ponte", "puente", "bridge", "templo", "temple", "torre", "tower", "canal",
    "estadio", "stadium",
})
_BUILDING_LANDMARK_MARKERS = frozenset({
    "basílica", "basilica", "catedral", "cathedral", "castello", "castillo", "castle", "chiesa", "church",
    "iglesia", "monumento", "monument", "museo", "museum", "museu", "palacio", "palace",
    "palazzo", "ponte", "puente", "bridge", "templo", "temple", "torre", "tower",
    "estadio", "stadium",
})

# A small allowlist lets a curated, single-word place from Apple Maps survive
# echo filtering when the model also returns an independent, visible cue. It
# improves useful labels such as Epcot without treating GPS proximity alone as
# visual evidence.
_CURATED_CONTEXTUAL_PLACE_CUES = {
    "amalfi": frozenset({"costa", "mar", "paisaje"}),
    "disney": frozenset({"parque temático", "parque"}),
    "epcot": frozenset({"parque temático", "parque"}),
    "florencia": frozenset({"arquitectura", "edificio", "iglesia", "monumento"}),
    "florence": frozenset({"arquitectura", "edificio", "iglesia", "monumento"}),
    "florida": frozenset({"parque temático", "parque"}),
    "italia": frozenset({"arquitectura", "edificio", "iglesia", "monumento"}),
    "orlando": frozenset({"parque temático", "parque"}),
    "venecia": frozenset({"canal", "góndola", "arquitectura veneciana", "puente"}),
    "venice": frozenset({"canal", "góndola", "arquitectura veneciana", "puente"}),
}


def _looks_like_named_landmark(value: str) -> bool:
    return bool(set(canonical_keyword_key(value).split()) & _NAMED_LANDMARK_MARKERS)


def _filter_unverified_place_echoes(
    candidates: tuple[str, ...],
    place_context: tuple[str, ...],
    *,
    resolved_landmark_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Persist a nearby POI name only with independent visible evidence.

    Apple Maps names are contextual hints, not visual evidence.  A vision model
    can repeat a nearby POI simply because it appeared in the prompt; suppress
    exact echoes unless the response also contains a separate architecture cue.
    """
    if not place_context:
        return candidates
    nearby_keys = {
        canonical_keyword_key(value)
        for value in place_context
        if isinstance(value, str) and value.strip()
    }
    resolved_landmark_keys = {
        canonical_keyword_key(value)
        for value in resolved_landmark_names
        if isinstance(value, str) and value.strip()
    }
    visually_confirmed_keys = {
        canonical_keyword_key(value)
        for value in place_context
        if (
            (_looks_like_named_landmark(value) or canonical_keyword_key(value) in resolved_landmark_keys)
            and _landmark_visual_match(candidates, value)
        )
    }
    contextual_place_keys = {
        canonical_keyword_key(value): value
        for value in place_context
        if isinstance(value, str) and value.strip()
    }
    contextual_visual_keys = {
        place_key
        for place_key, place_name in contextual_place_keys.items()
        if place_key in _CURATED_CONTEXTUAL_PLACE_CUES
        and any(
            _visual_architecture_cue_matches(candidate, set(_CURATED_CONTEXTUAL_PLACE_CUES[place_key]))
            for candidate in candidates
        )
    }
    return tuple(
        candidate
        for candidate in candidates
        if (
            canonical_keyword_key(candidate) not in nearby_keys
            or canonical_keyword_key(candidate) in visually_confirmed_keys
            or canonical_keyword_key(candidate) in contextual_visual_keys
        )
    )


def _caption_context_names(
    candidates: tuple[str, ...],
    place_context: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep nearby names for captions only when structural landmarks are seen.

    Generic geographic context such as a city or waterway can support a
    caption.  A nearby museum, church or monument is different: allowing its
    name merely because it came from Maps would turn contextual proximity into
    a false visual identification.  The same independent-cue rule used for
    keyword echoes applies to those structural names.
    """
    result: list[str] = []
    for value in place_context:
        key = canonical_keyword_key(value)
        is_structural = bool(set(key.split()) & _BUILDING_LANDMARK_MARKERS)
        if not is_structural or _landmark_visual_match(candidates, value):
            result.append(value)
    return tuple(result)


def _place_evidence_state(
    *,
    place_context: tuple[str, ...],
    raw_candidates: tuple[str, ...],
    filtered_candidates: tuple[str, ...],
    proposals: tuple[str, ...],
    trusted_entities: tuple[str, ...],
    landmark_visually_confirmed: bool,
) -> str:
    """Explain whether non-visual place context survived visual checks."""
    if not place_context:
        return "not_applicable"
    context_keys = {
        canonical_keyword_key(value)
        for value in place_context
        if isinstance(value, str) and value.strip()
    }
    final_keys = {
        canonical_keyword_key(value)
        for value in (*filtered_candidates, *proposals, *trusted_entities)
        if isinstance(value, str) and value.strip()
    }
    if landmark_visually_confirmed or (context_keys and context_keys & final_keys):
        return "visually_confirmed"
    raw_keys = {
        canonical_keyword_key(value)
        for value in raw_candidates
        if isinstance(value, str) and value.strip()
    }
    filtered_keys = {
        canonical_keyword_key(value)
        for value in filtered_candidates
        if isinstance(value, str) and value.strip()
    }
    if context_keys and context_keys & raw_keys and not context_keys & filtered_keys:
        return "discarded_by_visual_evidence"
    return "context_available"


def _ensure_generic_people_keyword(candidates: tuple[str, ...], contains_people: bool) -> tuple[str, ...]:
    """Keep the visible people flag and keywords consistent without identities."""
    if not contains_people:
        return candidates
    generic_people = {"persona", "grupo", "retrato", "mascota", "personaje"}
    if any(canonical_keyword_key(candidate) in generic_people for candidate in candidates):
        return candidates
    return ("persona", *candidates)


def _ensure_generic_text_keyword(candidates: tuple[str, ...], contains_text: bool) -> tuple[str, ...]:
    """Keep the visible-text flag discoverable without copying OCR content."""
    if not contains_text:
        return candidates
    if any(canonical_keyword_key(candidate) == "texto" for candidate in candidates):
        return candidates
    return ("texto", *candidates)


def _require_fresh_identity(fresh: object, photo: PhotoRecord) -> ScriptPhotoRecord:
    if not isinstance(fresh, ScriptPhotoRecord) or photo.uuid is None:
        raise _IdentityMismatchError
    try:
        same_uuid = uuid_module.UUID(fresh.uuid) == uuid_module.UUID(photo.uuid)
    except (TypeError, ValueError, AttributeError) as error:
        raise _IdentityMismatchError from error
    if not same_uuid or fresh.local_id != photo.photos_local_identifier:
        raise _IdentityMismatchError
    return fresh


def _ensure_runs_root(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ValueError("runs root is unsafe")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def _validate_runs_root_candidate(path: Path) -> None:
    """Reject an existing symlinked/unsafe root without creating it."""
    path = Path(path)
    candidate = path
    while True:
        try:
            details = candidate.lstat()
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                return
            candidate = parent
            continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ValueError("runs root is unsafe")
        return


def _persist_scan(
    run_dir: Path,
    *,
    run_id: str,
    created_at: datetime,
    model_plan: ModelPlan,
    ollama_versions: Mapping[str, str],
    selection: Any,
    photos: list[PhotoRecord],
    scan_status: str,
    run_errors: list[dict[str, str]] | None = None,
    captions_requested: bool = False,
) -> tuple[ScanManifest, Path]:
    summary = {
        "ready": sum(photo.scan_state == "ready" for photo in photos),
        "noop": sum(photo.scan_state == "noop" for photo in photos),
        "analysis_failed": sum(photo.scan_state == "analysis_failed" for photo in photos),
    }
    selection_payload = {
        "requested": selection.requested,
        "eligible": selection.eligible,
        "screenshots_excluded": selection.screenshots_excluded,
        "access": selection.access,
        "captions_requested": captions_requested,
    }
    if selection.strategy != "recent":
        selection_payload["strategy"] = selection.strategy
    manifest = build_scan_manifest(
        run_id=run_id,
        app_name=APP_NAME,
        app_version=APP_VERSION,
        model_name=model_plan.fast_model,
        ollama_version=str(ollama_versions[model_plan.fast_model]),
        endpoint=OllamaVisionClient.base_url.removesuffix("/api"),
        selection=selection_payload,
        photos=photos,
        summary=summary,
        scan_status=scan_status,
        created_at=created_at,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        run_errors=run_errors or [],
        model_policy=model_plan.policy,
        fast_model=model_plan.fast_model,
        detailed_model=model_plan.detailed_model,
        ollama_versions=dict(ollama_versions),
    )
    manifest_path = write_manifest(run_dir, manifest)
    write_preview_csv(run_dir, manifest)
    return manifest, manifest_path


def _notify_scan_progress(
    progress_callback: Callable[[PhotoRecord], None] | None,
    photo: PhotoRecord,
) -> None:
    """Notify an optional observer without changing scan correctness.

    Progress is a presentation concern.  A broken UI/IPC observer must never
    turn an otherwise completed dry-run into a failed Photos workflow.
    """
    if progress_callback is None:
        return
    try:
        progress_callback(photo)
    except Exception:
        return


class _PhotoAnalysisCancelled(RuntimeError):
    """Stop before inference while preserving ownership of the exported raster."""


@dataclass(frozen=True, slots=True)
class _PreparedPhotoAnalysis:
    """Serial Photos/Maps output that is safe to hand to an Ollama worker."""

    local_id: str
    selected_date: datetime
    metadata: ScriptPhotoRecord | None = None
    active_model: str | None = None
    model_reason: str | None = None
    exported: Path | None = None
    landmark: Any | None = None
    place_context: tuple[str, ...] = ()
    used_gps: bool = False
    used_apple_maps: bool = False
    place_lookup_state: str = "not_requested"
    place_evidence_state: str = "not_applicable"
    metadata_duration_ms: int = 0
    export_duration_ms: int = 0
    context_duration_ms: int = 0
    ollama_version: str | None = None
    errors: tuple[tuple[str, str], ...] = ()


def _elapsed_ms(start_ns: int) -> int:
    return max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)


def _technical_trace(
    prepared: _PreparedPhotoAnalysis,
    *,
    inference_ms: int,
    postprocess_ms: int,
    place_evidence_state: str | None = None,
    analysis_profile: str | None = None,
    analysis_layers: Mapping[str, bool] | None = None,
    additional_information: str | None = None,
    analysis_prompt: str | None = None,
) -> TechnicalTrace:
    evidence_state = place_evidence_state or prepared.place_evidence_state
    prompt = " ".join(_vision_prompt(
        landmark_hint=(prepared.landmark.name if prepared.landmark is not None else None),
        place_context=prepared.place_context,
        analysis_profile=analysis_profile,
        analysis_layers=analysis_layers,
        additional_information=additional_information,
        analysis_prompt=analysis_prompt,
    ).split())
    if prepared.used_gps:
        prompt += (
            " Se utilizó geolocalización local de solo lectura para orientar el análisis; "
            "las coordenadas no se guardan."
        )
    if prepared.used_apple_maps:
        if prepared.place_context:
            prompt += (
                " Se consultó Apple Maps para obtener contexto de lugares; el contexto se sanitizó "
                "y no se guardan coordenadas."
            )
        else:
            lookup_copy = {
                "no_results": "Apple Maps se consultó y no devolvió lugares sanitizables.",
                "timeout": "Apple Maps agotó el tiempo de espera sin devolver contexto sanitizable.",
                "error": "Apple Maps devolvió un error o no pudo completar la consulta.",
                "cancelled": "La consulta de Apple Maps fue cancelada.",
                "results_filtered": "Apple Maps devolvió lugares, pero todos se descartaron por sanitización.",
            }.get(
                prepared.place_lookup_state,
                "Apple Maps se consultó, pero no se obtuvo contexto de lugar en esta ejecución.",
            )
            prompt += f" {lookup_copy} No se guardan coordenadas."
    elif prepared.used_gps:
        prompt += " Apple Maps no se consultó en esta ejecución."
    if evidence_state == "discarded_by_visual_evidence":
        prompt += (
            " Se obtuvieron candidatos de lugar, pero no se usaron como nombres específicos porque "
            "la imagen no aportó evidencia visual suficiente."
        )
    elif evidence_state == "visually_confirmed":
        prompt += " El contexto de lugar coincidió con evidencia visual suficiente."
    elif evidence_state == "context_available":
        prompt += " El contexto de lugar quedó disponible solo como apoyo no visual."
    durations = {
        "metadata": prepared.metadata_duration_ms,
        "export": prepared.export_duration_ms,
        "context": prepared.context_duration_ms,
        "inference": inference_ms,
        "postprocess": postprocess_ms,
    }
    durations["total"] = sum(durations.values())
    return TechnicalTrace(
        prompt_effective=prompt,
        prompt_version="vision-prompt-v1",
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        ollama_version=prepared.ollama_version,
        used_gps=prepared.used_gps,
        used_apple_maps=prepared.used_apple_maps,
        used_landmark=prepared.landmark is not None,
        place_context=prepared.place_context,
        place_lookup_state=prepared.place_lookup_state,
        place_evidence_state=evidence_state,
        durations_ms=durations,
    )


def _remove_prepared_export(exported: Path | None) -> tuple[tuple[str, str], ...]:
    if exported is None:
        return ()
    try:
        exported.unlink()
    except Exception:
        return (("cleanup", "EXPORT_DELETE_FAILED"),)
    return ()


def _prepare_photo_analysis(
    selected: Any,
    *,
    bridge: Any,
    workspace: Any,
    model_plan: ModelPlan,
    landmark_resolver: Any,
    places_client: Any | None,
    cancel_requested: Callable[[], bool],
    ollama_versions: Mapping[str, str] | None = None,
    use_location_context: bool = True,
) -> _PreparedPhotoAnalysis:
    """Read/export one photo and resolve local context on the serial lane."""
    metadata_started = time.perf_counter_ns()
    read_error_code = "READ_FAILED"
    try:
        read_value: object = bridge.read(selected.local_id)
    except Exception as error:
        read_value = None
        read_error_code = _adapter_error_code(error, "READ_FAILED")
    metadata = _safe_photo_metadata(read_value, selected.local_id)
    metadata_duration_ms = _elapsed_ms(metadata_started)
    if metadata is None:
        error_code = "IDENTITY_MISMATCH" if isinstance(read_value, ScriptPhotoRecord) else read_error_code
        return _PreparedPhotoAnalysis(
            local_id=selected.local_id,
            selected_date=selected.creation_date,
            metadata_duration_ms=metadata_duration_ms,
            errors=(("metadata", error_code),),
        )

    location = getattr(metadata, "location", None) if use_location_context else None
    used_gps = location is not None
    active_model, model_reason = model_plan.model_for_location(location)
    ollama_version = (
        str(ollama_versions[active_model])
        if ollama_versions is not None and active_model in ollama_versions
        else None
    )
    export_started = time.perf_counter_ns()
    try:
        destination = workspace.destination_for(metadata.uuid)
        exported = bridge.export(metadata.uuid, destination)
    except (LocalExportStorageError, OSError):
        # Export destination failures are owner-level storage faults. Preserve
        # their classification instead of manufacturing a per-photo result.
        raise
    except Exception as error:
        export_duration_ms = _elapsed_ms(export_started)
        return _PreparedPhotoAnalysis(
            local_id=selected.local_id,
            selected_date=selected.creation_date,
            metadata=metadata,
            active_model=active_model,
            model_reason=model_reason,
            used_gps=used_gps,
            metadata_duration_ms=metadata_duration_ms,
            export_duration_ms=export_duration_ms,
            ollama_version=ollama_version,
            errors=(("export", _adapter_error_code(error, "EXPORT_FAILED")),),
        )
    export_duration_ms = _elapsed_ms(export_started)

    context_started = time.perf_counter_ns()
    used_apple_maps = places_client is not None and location is not None
    place_lookup_state = "no_location" if use_location_context and location is None else "not_requested"
    try:
        if cancel_requested():
            raise _PhotoAnalysisCancelled
        landmark = landmark_resolver.resolve(location)
        raw_place_context: tuple[str, ...] = ()
        if used_apple_maps:
            try:
                nearby_with_status = getattr(places_client, "nearby_with_status", None)
                if callable(nearby_with_status):
                    lookup = nearby_with_status(location, cancel_requested=cancel_requested)
                    raw_place_context = tuple(getattr(lookup, "names", ()))
                    place_lookup_state = str(getattr(lookup, "state", "results" if raw_place_context else "no_results"))
                else:
                    raw_place_context = tuple(places_client.nearby(location, cancel_requested=cancel_requested))
                    place_lookup_state = "results" if raw_place_context else "no_results"
            except _PhotoAnalysisCancelled:
                raise
            except Exception:
                raw_place_context = ()
                place_lookup_state = "error"
        place_context = tuple(sanitize_place_context(raw_place_context))
        if used_apple_maps and raw_place_context and not place_context and place_lookup_state == "results":
            place_lookup_state = "results_filtered"
        if cancel_requested():
            raise _PhotoAnalysisCancelled
    except _PhotoAnalysisCancelled:
        _remove_prepared_export(exported)
        raise
    except Exception as error:
        context_duration_ms = _elapsed_ms(context_started)
        return _PreparedPhotoAnalysis(
            local_id=selected.local_id,
            selected_date=selected.creation_date,
            metadata=metadata,
            active_model=active_model,
            model_reason=model_reason,
            exported=exported,
            used_gps=used_gps,
            used_apple_maps=used_apple_maps,
            place_lookup_state=place_lookup_state,
            metadata_duration_ms=metadata_duration_ms,
            export_duration_ms=export_duration_ms,
            context_duration_ms=context_duration_ms,
            ollama_version=ollama_version,
            errors=(("analysis", _adapter_error_code(error, "ANALYSIS_FAILED")),),
        )
    context_duration_ms = _elapsed_ms(context_started)
    return _PreparedPhotoAnalysis(
        local_id=selected.local_id,
        selected_date=selected.creation_date,
        metadata=metadata,
        active_model=active_model,
        model_reason=model_reason,
        exported=exported,
        landmark=landmark,
        place_context=place_context,
        used_gps=used_gps,
        used_apple_maps=used_apple_maps,
        place_lookup_state=place_lookup_state,
        metadata_duration_ms=metadata_duration_ms,
        export_duration_ms=export_duration_ms,
        context_duration_ms=context_duration_ms,
        ollama_version=ollama_version,
    )


def _analyze_prepared_photo(
    prepared: _PreparedPhotoAnalysis,
    *,
    vision: Any,
    include_caption: bool,
    analysis_profile: str | None = None,
    analysis_layers: Mapping[str, bool] | None = None,
    additional_information: str | None = None,
    analysis_prompt: str | None = None,
) -> PhotoRecord:
    """Run inference/postprocessing without opening Photos or Apple Maps."""
    errors = [{"stage": stage, "code": code} for stage, code in prepared.errors]
    metadata = prepared.metadata
    if metadata is None:
        return PhotoRecord(
            uuid=None,
            photos_local_identifier=prepared.local_id,
            title="",
            date=prepared.selected_date.replace(tzinfo=None),
            existing_keywords=[],
            proposed_keywords=[],
            contains_people=None,
            contains_text=None,
            confidence=None,
            scan_state="analysis_failed",
            errors=errors,
            technical_trace=_technical_trace(prepared, inference_ms=0, postprocess_ms=0),
        )

    candidates: tuple[str, ...] = ()
    raw_candidates: tuple[str, ...] = ()
    trusted_entities: tuple[str, ...] = ()
    proposed_caption: str | None = None
    contains_people: bool | None = None
    contains_text: bool | None = None
    confidence: float | None = None
    analysis_failed = bool(errors)
    landmark = prepared.landmark
    place_context = prepared.place_context
    landmark_visually_confirmed = False
    caption_place_context: tuple[str, ...] = ()
    inference_ms = 0
    analysis_started = time.perf_counter_ns()
    try:
        if not analysis_failed and prepared.exported is not None:
            location = getattr(metadata, "location", None)
            inference_started = time.perf_counter_ns()
            try:
                vision_options: dict[str, object] = {}
                if location is not None:
                    vision_options["location"] = location
                if landmark is not None:
                    vision_options["landmark_hint"] = landmark.name
                if place_context:
                    vision_options["place_context"] = place_context
                if analysis_profile is not None:
                    vision_options["analysis_profile"] = analysis_profile
                if analysis_layers is not None:
                    vision_options["analysis_layers"] = dict(analysis_layers)
                if additional_information is not None:
                    vision_options["additional_information"] = additional_information
                if analysis_prompt is not None:
                    vision_options["analysis_prompt"] = analysis_prompt
                vision_result = vision.analyze(
                    prepared.active_model,
                    prepared.exported,
                    **vision_options,
                )
            finally:
                inference_ms = _elapsed_ms(inference_started)
            raw_candidates = tuple(vision_result.keywords)
            candidates = _filter_unverified_place_echoes(
                raw_candidates,
                place_context,
                resolved_landmark_names=(landmark.name, *landmark.aliases) if landmark is not None else (),
            )
            trusted_entities = confirmed_venue_entities(place_context, candidates, vision_result.caption)
            if trusted_entities:
                candidates = (*trusted_entities, *candidates)
            caption_place_context = _caption_context_names(candidates, place_context)
            candidates = _ensure_generic_people_keyword(candidates, vision_result.contains_people)
            candidates = _ensure_generic_text_keyword(candidates, vision_result.contains_text)
            landmark_visually_confirmed = (
                landmark is not None
                and vision_result.confidence >= CONFIDENCE_THRESHOLD
                and _landmark_visual_match(candidates, landmark.name, aliases=landmark.aliases)
            )
            if landmark_visually_confirmed:
                candidates = (*candidates, landmark.name)
            contains_people = vision_result.contains_people
            contains_text = vision_result.contains_text
            confidence = vision_result.confidence
            if include_caption and confidence >= CONFIDENCE_THRESHOLD:
                caption_place_names = caption_place_context
                if landmark_visually_confirmed:
                    caption_place_names += (landmark.name, *landmark.aliases)
                proposed_caption = _safe_caption(
                    vision_result.caption,
                    allowed_place_names=caption_place_names,
                    allowed_context_names=trusted_entities,
                )
                if proposed_caption is not None and _caption_needs_fallback(proposed_caption, candidates):
                    proposed_caption = None
    except Exception as error:
        analysis_failed = True
        errors.append({"stage": "analysis", "code": _adapter_error_code(error, "ANALYSIS_FAILED")})
    finally:
        errors.extend(
            {"stage": stage, "code": code}
            for stage, code in _remove_prepared_export(prepared.exported)
        )

    if analysis_failed:
        proposals: list[str] = []
        scan_state = "analysis_failed"
    elif confidence is not None and confidence < CONFIDENCE_THRESHOLD:
        proposals = []
        scan_state = "noop"
        errors.append({"stage": "analysis", "code": "LOW_CONFIDENCE"})
    else:
        contextual_keywords = place_context
        if landmark is not None and landmark_visually_confirmed:
            contextual_keywords += (landmark.name, *landmark.aliases)
        proposals = proposed_keywords(
            metadata.existing_keywords,
            candidates,
            allowed_contextual_keywords=contextual_keywords,
        )
        scan_state = "ready" if proposals else "noop"
    if include_caption and confidence is not None and confidence >= CONFIDENCE_THRESHOLD and proposed_caption is None:
        caption_context = caption_place_context
        if landmark is not None and landmark_visually_confirmed:
            caption_context += (landmark.name, *landmark.aliases)
        proposed_caption = _fallback_caption(
            candidates,
            allowed_place_names=caption_context,
            allowed_context_names=trusted_entities,
        )
    postprocess_ms = max(0, _elapsed_ms(analysis_started) - inference_ms)
    place_evidence_state = _place_evidence_state(
        place_context=place_context,
        raw_candidates=raw_candidates,
        filtered_candidates=candidates,
        proposals=tuple(proposals),
        trusted_entities=trusted_entities,
        landmark_visually_confirmed=landmark_visually_confirmed,
    )
    return PhotoRecord(
        uuid=metadata.uuid,
        photos_local_identifier=prepared.local_id,
        title=metadata.title,
        date=metadata.date,
        existing_keywords=list(metadata.existing_keywords),
        proposed_keywords=proposals,
        contains_people=contains_people,
        contains_text=contains_text,
        confidence=confidence,
        model_used=prepared.active_model if not analysis_failed else None,
        model_reason=prepared.model_reason if not analysis_failed else None,
        scan_state=scan_state,
        proposed_caption=proposed_caption,
        caption_state="proposed" if proposed_caption else "not_requested",
        errors=errors,
        technical_trace=_technical_trace(
            prepared,
            inference_ms=inference_ms,
            postprocess_ms=postprocess_ms,
            place_evidence_state=place_evidence_state,
            analysis_profile=analysis_profile,
            analysis_layers=analysis_layers,
            additional_information=additional_information,
            analysis_prompt=analysis_prompt,
        ),
    )


def run_scan(
    runs_root: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    model: str | None = DEFAULT_MODEL,
    random_selection: bool = False,
    apple_maps: bool = False,
    model_policy: str = "single",
    fast_model: str = DEFAULT_MODEL,
    detailed_model: str = DEFAULT_DETAILED_MODEL,
    include_caption: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
    progress_callback: Callable[[PhotoRecord], None] | None = None,
    dependencies: ScanDependencies | None = None,
) -> WorkflowResult:
    dependencies = dependencies or ScanDependencies()
    cancel_requested = cancel_requested or (lambda: False)
    if dependencies.platform_name() != "Darwin":
        return _fatal("PLATFORM_UNSUPPORTED")
    if type(limit) is not int or not 1 <= limit <= 500:
        return _fatal("LIMIT_INVALID")
    if (
        type(random_selection) is not bool
        or type(apple_maps) is not bool
        or type(include_caption) is not bool
    ):
        return _fatal("SCAN_OPTIONS_INVALID")
    try:
        model_plan = resolve_model_plan(
            policy=model_policy, model=model, fast_model=fast_model, detailed_model=detailed_model,
        )
    except Exception:
        return _fatal("MODEL_INVALID")

    try:
        vision = dependencies.vision_factory()
        ollama_versions = {model_name: str(vision.check_model(model_name)) for model_name in model_plan.required_models}
    except OllamaModelMissingError as error:
        return _fatal("OLLAMA_MODEL_MISSING", safe_instruction=error.pull_command)
    except Exception as error:
        return _ollama_fatal(error)

    try:
        runs_root = Path(runs_root)
    except (OSError, TypeError, ValueError):
        return _fatal("RUNS_ROOT_INVALID")
    try:
        # Request/read PhotoKit authorization before creating the application
        # support root.  A denied request must leave no empty ``runs``
        # directory behind and must not look like a run that can be applied.
        _validate_runs_root_candidate(runs_root)
        selector = dependencies.selector_factory()
        selection = (
            selector.select(limit=limit, randomize=True)
            if random_selection
            else selector.select(limit=limit)
        )
        # Keep the workflow boundary fail-closed even if a selector binding
        # returns a malformed result instead of raising on denied PhotoKit
        # authorization.  Durable run storage must never be created for a
        # selection that is not explicitly authorized or limited.
        selection_access = getattr(selection, "access", None)
        if type(selection_access) is not str or selection_access not in {"authorized", "limited"}:
            raise PhotosAccessError()
        # Recover exports from an already-existing root before constructing
        # PhotoScript.  A bridge compatibility/TCC failure must not leave
        # stale private exports behind until a later successful scan.
        root_existed = runs_root.exists()
        if root_existed:
            dependencies.recover_workspaces(runs_root)
        # Initialize PhotoScript before creating durable run storage. If TCC
        # denies Apple Events at construction time, return an actionable
        # permission result without leaving an empty root behind.
        bridge = dependencies.bridge_factory()
        _ensure_runs_root(runs_root)
        if not root_existed:
            dependencies.recover_workspaces(runs_root)
        landmark_resolver = dependencies.landmark_resolver_factory()
        places_client = dependencies.places_factory() if apple_maps else None
        run_uuid = dependencies.uuid4()
        run_id = str(run_uuid)
        now = dependencies.now_utc()
        if now.tzinfo is None:
            raise ValueError("UTC clock must return an aware datetime")
        timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = runs_root / f"{timestamp}-{run_id[:8]}"
        run_dir.mkdir(mode=0o700)
        run_dir.chmod(0o700)
    except PhotosAccessError as error:
        return _fatal(error.code)
    except PhotoScriptPermissionError as error:
        return _fatal(error.code)
    except PhotoScriptUnavailableError as error:
        return _fatal(error.code)
    except Exception:
        return _fatal("SCAN_SETUP_FAILED")

    photos: list[PhotoRecord] = []
    cancelled = False
    try:
        workspace_context = dependencies.workspace_factory(run_dir, run_id)
        with workspace_context as workspace:
            with dependencies.signal_scope(workspace):
                for selected in selection.photos:
                    if cancel_requested():
                        cancelled = True
                        break
                    try:
                        prepared = _prepare_photo_analysis(
                            selected,
                            bridge=bridge,
                            workspace=workspace,
                            model_plan=model_plan,
                            landmark_resolver=landmark_resolver,
                            places_client=places_client,
                            cancel_requested=cancel_requested,
                            ollama_versions=ollama_versions,
                        )
                    except _PhotoAnalysisCancelled:
                        cancelled = True
                        break
                    photo = _analyze_prepared_photo(
                        prepared,
                        vision=vision,
                        include_caption=include_caption,
                    )
                    photos.append(photo)
                    _notify_scan_progress(progress_callback, photo)
    except (KeyboardInterrupt, SystemExit):
        # A real SIGINT/SIGTERM raises a BaseException subclass, so it does
        # not reach the generic Exception handler below. Persist the same
        # cancellation checkpoint as cooperative cancellation; otherwise the
        # private run directory would remain without an auditable manifest.
        try:
            manifest, manifest_path = _persist_scan(
                run_dir, run_id=run_id, created_at=now, model_plan=model_plan, ollama_versions=ollama_versions,
                selection=selection, photos=photos, scan_status="failed", captions_requested=include_caption,
                run_errors=[{"stage": "workspace", "code": "CANCELLED"}],
            )
        except Exception:
            _discard_failed_run(run_dir)
            return _fatal("MANIFEST_WRITE_FAILED")
        return WorkflowResult(
            exit_code=1,
            manifest_path=manifest_path,
            manifest=manifest,
            error_codes=("CANCELLED",),
            next_action="fix_failed_scan",
        )
    except Exception:
        try:
            manifest, manifest_path = _persist_scan(
                run_dir, run_id=run_id, created_at=now, model_plan=model_plan, ollama_versions=ollama_versions,
                selection=selection, photos=photos, scan_status="failed", captions_requested=include_caption,
                run_errors=[{"stage": "workspace", "code": "WORKSPACE_FAILED"}],
            )
        except Exception:
            _discard_failed_run(run_dir)
            return _fatal("MANIFEST_WRITE_FAILED")
        return WorkflowResult(
            exit_code=2,
            manifest_path=manifest_path,
            manifest=manifest,
            error_codes=("WORKSPACE_FAILED",),
            next_action="fix_fatal_error",
        )

    if cancelled:
        try:
            manifest, manifest_path = _persist_scan(
                run_dir, run_id=run_id, created_at=now, model_plan=model_plan, ollama_versions=ollama_versions,
                selection=selection, photos=photos, scan_status="failed", captions_requested=include_caption,
                run_errors=[{"stage": "workspace", "code": "CANCELLED"}],
            )
        except Exception:
            _discard_failed_run(run_dir)
            return _fatal("MANIFEST_WRITE_FAILED")
        return WorkflowResult(
            exit_code=1,
            manifest_path=manifest_path,
            manifest=manifest,
            error_codes=("CANCELLED",),
            next_action="fix_failed_scan",
        )

    has_errors = any(photo.errors for photo in photos)
    scan_status = "ready_with_errors" if has_errors else "ready"
    permission_codes = sorted({
        error["code"]
        for photo in photos
        for error in photo.errors
        if _permission_next_action(error.get("code", "")) is not None
    })
    permission_action = next(
        (_permission_next_action(code) for code in permission_codes),
        None,
    )
    try:
        manifest, manifest_path = _persist_scan(
            run_dir, run_id=run_id, created_at=now, model_plan=model_plan, ollama_versions=ollama_versions,
            selection=selection, photos=photos, scan_status=scan_status, captions_requested=include_caption,
        )
    except Exception:
        _discard_failed_run(run_dir)
        return _fatal("MANIFEST_WRITE_FAILED")
    warnings = tuple(
        code for condition, code in (
            (selection.eligible < selection.requested, "FEWER_PHOTOS_AVAILABLE"),
            (selection.access == "limited", "PHOTOS_ACCESS_LIMITED"),
        ) if condition
    )
    scan_error_codes = tuple(sorted({
        error["code"]
        for photo in photos
        for error in photo.errors
    }))
    reviewable_rows = any(
        not photo.errors
        and (
            photo.scan_state == "ready"
            or (photo.scan_state == "noop" and photo.proposed_caption)
        )
        for photo in photos
    )
    return WorkflowResult(
        exit_code=1 if has_errors else 0,
        manifest_path=manifest_path,
        manifest=manifest,
        error_codes=scan_error_codes,
        warning_codes=warnings,
        # Permission failures require fixing TCC first.  In particular, do
        # not advertise apply when a PhotoScript/Apple Events denial was
        # recorded in an otherwise auditable partial manifest.
        next_action=permission_action or (
            "fix_fatal_error"
            if _manifest_scan_has_fatal_compatibility_error(manifest)
            else (
                "review_then_apply"
                if reviewable_rows
                else ("fix_failed_scan" if has_errors else "none")
            )
        ),
    )


def _load_manifest_path(manifest_path: Path) -> ScanManifest:
    manifest_path = Path(manifest_path)
    if manifest_path.name != MANIFEST_FILENAME:
        raise ValueError("manifest basename is invalid")
    return load_manifest(manifest_path.parent)


def _reviewed_rows_match_source(source: ScanManifest, reviewed: ScanManifest) -> bool:
    """Compatibility wrapper for the shared review/source comparison."""
    return reviewed_rows_match_source(source, reviewed)


def _review_provenance_is_valid(manifest_path: Path, reviewed: ScanManifest) -> bool:
    """Require a reviewed manifest's source dry-run to still be present.

    Reviewed manifests are private sibling runs.  Looking up the source by
    UUID and digest prevents a copied/forged schema-v3 file from becoming an
    implicit authorization to mutate Photos, while keeping ``status`` purely
    manifest-local.
    """
    if reviewed.schema_version not in {3, 4}:
        return True
    if reviewed.reviewed_from_run_id is None or reviewed.source_scan_digest is None:
        return False
    try:
        runs_root = manifest_path.parent.parent
        root_details = runs_root.lstat()
        if (
            stat.S_ISLNK(root_details.st_mode)
            or not stat.S_ISDIR(root_details.st_mode)
            or root_details.st_uid != os.getuid()
            or stat.S_IMODE(root_details.st_mode) != 0o700
        ):
            return False
        for candidate in runs_root.iterdir():
            if candidate == manifest_path.parent:
                continue
            try:
                candidate_details = candidate.lstat()
                if (
                    stat.S_ISLNK(candidate_details.st_mode)
                    or not stat.S_ISDIR(candidate_details.st_mode)
                    or candidate_details.st_uid != os.getuid()
                    or stat.S_IMODE(candidate_details.st_mode) != 0o700
                ):
                    continue
                source = load_manifest(candidate)
            except Exception:
                continue
            if source.run_id != reviewed.reviewed_from_run_id:
                continue
            return (
                source.schema_version in {1, 2}
                and source.reviewed_from_run_id is None
                and source.scan_status in {"ready", "ready_with_errors"}
                and _reviewed_manifest_is_pristine(source)
                and source.scan_digest == reviewed.source_scan_digest
                and _reviewed_rows_match_source(source, reviewed)
            )
    except Exception:
        return False
    return False


def _reviewed_manifest_is_pristine(manifest: ScanManifest) -> bool:
    """Require a reviewed copy to contain approval state, never mutation state.

    A reviewed manifest is the hand-off between the UI and the mutating
    workflow.  If it already contains a write/rollback result, treating it as
    a fresh approval could silently skip rows while returning a misleading
    success.  This is an auditability guard; the source dry-run digest still
    protects the immutable photo/proposal data separately.
    """
    for photo in manifest.photos:
        if (
            photo.apply_state != "not_run"
            or photo.rollback_state != "not_run"
            or photo.applied_keywords
            or photo.rolled_back_keywords
            or photo.applied_caption is not None
            or photo.mutation_digest is not None
        ):
            return False
        expected_caption_state = "proposed" if photo.proposed_caption else "not_requested"
        if photo.caption_state != expected_caption_state:
            return False
    return True


def _reviewed_manifest_can_retry_failed_rows(manifest: ScanManifest) -> bool:
    """Allow an audited partial apply to resume only safe pending rows.

    A process can stop after persisting a verified row and before it reaches
    the next approved row.  That next ``not_run`` row has no mutation to
    guess about, so it can be revalidated and processed normally. Verified
    rows remain immutable, while failed/cancelled rows must carry a real
    apply-stage error. Uncertain or rollback states stay fail-closed.
    """
    retryable = False
    for photo in manifest.photos:
        if photo.rollback_state != "not_run":
            return False
        if photo.apply_state in {"not_run", "verified", "noop"}:
            if photo.apply_state != "verified" and photo.mutation_digest is not None:
                return False
            if (
                photo.apply_state == "not_run"
                and not _photo_has_scan_errors(photo)
                and (
                    photo.scan_state == "ready"
                    or (photo.scan_state == "noop" and photo.proposed_caption)
                )
            ):
                retryable = True
            continue
        if photo.mutation_digest is not None:
            return False
        if photo.apply_state not in {"failed", "cancelled"}:
            return False
        if not any(error["stage"] == "apply" for error in photo.errors):
            return False
        retryable = True
    return retryable


def _mutation_evidence_is_valid(manifest: ScanManifest) -> bool:
    """Require matching apply and rollback receipts before trusting state."""
    for photo in manifest.photos:
        has_applied_data = photo.apply_state in {"verified", "uncertain"} and bool(
            photo.applied_keywords or photo.applied_caption
        )
        interrupted_caption_without_readback = (
            photo.apply_state == "uncertain"
            and not photo.applied_keywords
            and photo.applied_caption is not None
            and photo.caption_state == "uncertain"
            and photo.mutation_digest is None
            and any(
                error["stage"] == "apply" and error["code"] == "INTERRUPTED_WRITE"
                for error in photo.errors
            )
        )
        if has_applied_data and not interrupted_caption_without_readback:
            if photo.mutation_digest is None or photo.mutation_digest != compute_mutation_digest(manifest, photo):
                return False
        elif photo.mutation_digest is not None:
            return False
        if photo.rollback_state == "not_run":
            if photo.rollback_digest is not None:
                return False
        elif photo.rollback_digest is None or photo.rollback_digest != compute_rollback_digest(manifest, photo):
            # A rollback state without its own receipt may be an edited or
            # truncated journal. Never let status present it as verified or
            # let a subsequent rollback consume unproven removal evidence.
            return False
    return True


def _has_valid_apply_receipt(manifest: ScanManifest, photo: PhotoRecord) -> bool:
    """Return whether this row has read-back evidence for applied data."""
    return (
        photo.apply_state in {"verified", "uncertain"}
        and bool(photo.applied_keywords or photo.applied_caption)
        and photo.mutation_digest is not None
        and photo.mutation_digest == compute_mutation_digest(manifest, photo)
    )


def _has_valid_rollback_receipt(manifest: ScanManifest, photo: PhotoRecord) -> bool:
    """Return whether this row has read-back evidence for rollback data."""
    return (
        photo.rollback_state != "not_run"
        and photo.rollback_digest is not None
        and photo.rollback_digest == compute_rollback_digest(manifest, photo)
    )


def _mutation_error_state_is_consistent(manifest: ScanManifest) -> bool:
    """Require failed/cancelled mutation rows to retain their stage cause."""
    for photo in manifest.photos:
        if photo.apply_state in {"failed", "uncertain", "cancelled"} and not any(
            error["stage"] == "apply" for error in photo.errors
        ):
            return False
        if photo.rollback_state in {"failed", "uncertain", "cancelled", "casing_conflict"} and not any(
            error["stage"] == "rollback" for error in photo.errors
        ):
            return False
    return True


def _rollback_error_state_is_consistent(manifest: ScanManifest) -> bool:
    """Require rollback retry states to retain their persisted cause."""
    return all(
        photo.rollback_state not in {"failed", "uncertain", "cancelled", "casing_conflict"}
        or any(error["stage"] == "rollback" for error in photo.errors)
        for photo in manifest.photos
    )


def _reviewed_manifest_can_recover_or_repeat(manifest: ScanManifest) -> bool:
    """Allow an interrupted write checkpoint to be converted to uncertain."""
    if any(photo.rollback_state != "not_run" for photo in manifest.photos):
        return False
    if any(photo.apply_state == "writing" for photo in manifest.photos):
        return all(photo.apply_state in {"not_run", "writing", "verified", "noop"} for photo in manifest.photos)
    return False


def _reviewed_caption_state_is_invalid(manifest: ScanManifest) -> bool:
    """Reject caption states that hide an approved value from mutation."""
    return manifest.schema_version in {3, 4} and any(
        photo.caption_state == "preserved" and photo.apply_state == "not_run"
        or photo.caption_state == "proposed" and photo.apply_state == "noop"
        or photo.caption_state == "failed" and photo.apply_state in {"not_run", "noop"}
        for photo in manifest.photos
    )


def _open_operation_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise ValueError("operation lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_private_lock_parent(path: Path) -> None:
    _reject_symlinked_ancestors(path)
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ValueError("global lock directory is unsafe")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


@contextmanager
def _operation_locks(run_dir: Path, global_lock_path: Path) -> Iterator[None]:
    descriptors: list[int] = []
    try:
        global_lock_path = Path(global_lock_path)
        _ensure_private_lock_parent(global_lock_path.parent)
        descriptors.append(_open_operation_lock(global_lock_path))
        descriptors.append(_open_operation_lock(run_dir / ".run.lock"))
        yield
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _replace_stage_errors(photo: PhotoRecord, stage: str, code: str | None = None) -> None:
    photo.errors = [error for error in photo.errors if error["stage"] != stage]
    if code is not None:
        photo.errors.append({"stage": stage, "code": code})


def _readback_contains(readback: tuple[str, ...], existing: list[str], additions: list[str]) -> bool:
    exact_counts = Counter(readback)
    if any(exact_counts[value] < count for value, count in Counter(existing).items()):
        return False
    return all(exact_counts[value] >= count for value, count in Counter(additions).items())


def _remaining_applied_keywords(photo: PhotoRecord) -> list[str]:
    audited = Counter(photo.rolled_back_keywords)
    remaining: list[str] = []
    for value in photo.applied_keywords:
        if audited[value]:
            audited[value] -= 1
        else:
            remaining.append(value)
    return remaining


def _workflow_counts(manifest: ScanManifest) -> dict[str, dict[str, int]]:
    return {
        "scan": dict(Counter(photo.scan_state for photo in manifest.photos)),
        "apply": dict(Counter(photo.apply_state for photo in manifest.photos)),
        "rollback": dict(Counter(photo.rollback_state for photo in manifest.photos)),
    }


def _manifest_warning_codes(manifest: ScanManifest) -> tuple[str, ...]:
    """Preserve scan-selection warnings across later workflow stages."""
    selection = manifest.selection
    return tuple(
        code for condition, code in (
            (selection["eligible"] < selection["requested"], "FEWER_PHOTOS_AVAILABLE"),
            (selection["access"] == "limited", "PHOTOS_ACCESS_LIMITED"),
        ) if condition
    )


def _workflow_error_codes(manifest: ScanManifest, stage: str, *, cancelled: bool = False) -> tuple[str, ...]:
    """Project persisted, schema-validated row errors into a terminal result."""
    codes = {
        error["code"]
        for photo in manifest.photos
        for error in photo.errors
        if error["stage"] == stage
    }
    if cancelled:
        codes.add("CANCELLED")
    return tuple(sorted(codes))


def _scan_error_codes(manifest: ScanManifest) -> tuple[str, ...]:
    """Project scan-stage errors so mutation results retain partial-run cause."""
    codes = {
        error["code"]
        for photo in manifest.photos
        for error in photo.errors
        if error["stage"] not in {"apply", "rollback"}
    }
    codes.update(error["code"] for error in manifest.run_errors)
    return tuple(sorted(codes))


def _persist_mutation_checkpoint(run_dir: Path, manifest: ScanManifest) -> None:
    """Keep the human-review CSV aligned with each durable mutation state."""
    write_manifest(run_dir, manifest)
    write_preview_csv(run_dir, manifest)


def _photo_has_scan_errors(photo: PhotoRecord) -> bool:
    """Identify rows that cannot be safely applied after a partial scan."""
    scan_errors = [
        error for error in photo.errors
        if error["stage"] not in {"apply", "rollback"}
    ]
    if not scan_errors:
        return False
    # Schema-4 continuous review may recover a photo whose identity and
    # metadata were read successfully but whose local model failed.  The
    # reviewed payload is actionable only when every value is explicitly
    # manual and the persisted failure belongs solely to the analysis stage.
    manual_analysis_recovery = (
        photo.uuid is not None
        and photo.scan_state == "ready"
        and photo.model_proposed_keywords == []
        and photo.model_proposed_caption is None
        and bool(photo.proposed_keywords or photo.proposed_caption)
        and photo.keyword_origins is not None
        and all(origin == "manual" for origin in photo.keyword_origins.values())
        and (photo.proposed_caption is None or photo.caption_origin == "manual")
        and all(error["stage"] == "analysis" for error in scan_errors)
    )
    return not manual_analysis_recovery


def sanitized_rows(manifest: ScanManifest) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for photo in manifest.photos:
        if photo.rollback_state != "not_run":
            status = photo.rollback_state
        elif photo.apply_state != "not_run":
            status = photo.apply_state
        else:
            status = photo.scan_state
        title = safe_projection_text(photo.title if len(photo.title) <= 24 else photo.title[:23] + "…")
        caption_status = {
            "not_requested": "—",
            "proposed": "pendiente",
            "preserved": "preservado",
            "verified": "verificado",
            "removed": "retirado",
            "uncertain": "incierto",
        }.get(photo.caption_state, "—") if photo.caption_state != "not_requested" else "—"
        rows.append({
            "uuid": photo.uuid[:8] if photo.uuid is not None else "—",
            "title": title,
            "date": photo.date.isoformat(timespec="minutes"),
            "existing": ", ".join(safe_projection_text(keyword) for keyword in photo.existing_keywords),
            "proposed": ", ".join(safe_projection_text(keyword) for keyword in photo.proposed_keywords),
            "caption_status": caption_status,
            "confidence": "—" if photo.confidence is None else f"{photo.confidence:.2f}",
            "status": status,
            "error_codes": ", ".join(error["code"] for error in photo.errors),
        })
    return rows


def run_apply(
    manifest_path: Path,
    *,
    dependencies: ApplyDependencies | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> WorkflowResult:
    try:
        manifest_path = Path(manifest_path)
    except (OSError, TypeError, ValueError):
        return _fatal("MANIFEST_INVALID")
    cancel_requested = cancel_requested or (lambda: False)
    try:
        initial = _load_manifest_path(manifest_path)
    except Exception:
        return _fatal("MANIFEST_INVALID")
    if initial.schema_version == 1:
        return _fatal("MANIFEST_NOT_REVIEWED")
    permission_codes = _manifest_permission_codes(initial)
    if permission_codes and not any(
        photo.scan_state == "ready" or (photo.scan_state == "noop" and photo.proposed_caption)
        for photo in initial.photos
    ):
        # A permission-only run contains no stable UUID/proposal that can be
        # applied.  Treat it as a recovery checkpoint, not as a successful
        # no-op: the user must grant TCC access and perform a fresh dry-run.
        return _fatal(permission_codes[0], next_action="rescan_after_permissions")
    if initial.scan_status not in {"ready", "ready_with_errors"}:
        return _fatal("SCAN_NOT_READY")
    if initial.schema_version >= 2 and initial.reviewed_from_run_id is None:
        return _fatal("MANIFEST_NOT_REVIEWED")
    if not _review_provenance_is_valid(manifest_path, initial):
        return _fatal("REVIEW_PROVENANCE_INVALID")
    if not _mutation_evidence_is_valid(initial):
        return _fatal("MUTATION_EVIDENCE_INVALID")
    if any(photo.rollback_state != "not_run" for photo in initial.photos):
        return _fatal("ROLLBACK_ALREADY_STARTED")
    if (
        initial.schema_version >= 2
        and not _reviewed_manifest_is_pristine(initial)
        and not _reviewed_manifest_can_retry_failed_rows(initial)
        and not _reviewed_manifest_can_recover_or_repeat(initial)
    ):
        return _fatal("REVIEW_NOT_PRISTINE")

    try:
        dependencies = _mutation_dependencies(manifest_path, dependencies)
    except (ValueError, OSError):
        return _fatal("REVIEW_PROVENANCE_INVALID")
    try:
        with _operation_locks(manifest_path.parent, dependencies.global_lock_path):
            manifest = _load_manifest_path(manifest_path)
            if manifest.schema_version == 1:
                return _fatal("MANIFEST_NOT_REVIEWED")
            permission_codes = _manifest_permission_codes(manifest)
            if permission_codes and not any(
                photo.scan_state == "ready" or (photo.scan_state == "noop" and photo.proposed_caption)
                for photo in manifest.photos
            ):
                return _fatal(permission_codes[0], next_action="rescan_after_permissions")
            if manifest.scan_status not in {"ready", "ready_with_errors"}:
                return _fatal("SCAN_NOT_READY")
            if manifest.schema_version >= 2 and manifest.reviewed_from_run_id is None:
                return _fatal("MANIFEST_NOT_REVIEWED")
            if not _review_provenance_is_valid(manifest_path, manifest):
                return _fatal("REVIEW_PROVENANCE_INVALID")
            if not _mutation_evidence_is_valid(manifest):
                return _fatal("MUTATION_EVIDENCE_INVALID")
            if any(photo.rollback_state != "not_run" for photo in manifest.photos):
                return _fatal("ROLLBACK_ALREADY_STARTED")
            if (
                manifest.schema_version >= 2
                and not _reviewed_manifest_is_pristine(manifest)
                and not _reviewed_manifest_can_retry_failed_rows(manifest)
                and not _reviewed_manifest_can_recover_or_repeat(manifest)
            ):
                return _fatal("REVIEW_NOT_PRISTINE")

            for photo in manifest.photos:
                if photo.apply_state == "writing":
                    photo.apply_state = "uncertain"
                    if photo.proposed_caption and photo.caption_state == "proposed":
                        photo.applied_caption = photo.proposed_caption
                        photo.caption_state = "uncertain"
                    _replace_stage_errors(photo, "apply", "INTERRUPTED_WRITE")
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)

            actionable = [
                photo for photo in manifest.photos
                if photo.apply_state in {"not_run", "failed", "cancelled"}
                and not _photo_has_scan_errors(photo)
            ]
            if actionable:
                try:
                    selector = dependencies.selector_factory()
                    bridge = dependencies.bridge_factory()
                except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
                    # Adapter initialization can be the first point where TCC
                    # or PhotoScript compatibility is evaluated. Persist the
                    # bounded cause on the reviewed row so History keeps the
                    # same recovery guidance after the immediate result is
                    # gone, while leaving the row safely retryable.
                    photo = actionable[0]
                    photo.apply_state = "failed"
                    _replace_stage_errors(photo, "apply", _adapter_error_code(error, "APPLY_FAILED"))
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    actionable = []
                    selector = None
                    bridge = None
            else:
                selector = None
                bridge = None

            cancelled = False
            for photo in actionable:
                if cancel_requested():
                    photo.apply_state = "cancelled"
                    photo.applied_keywords = []
                    _replace_stage_errors(photo, "apply", "CANCELLED")
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    cancelled = True
                    break
                _replace_stage_errors(photo, "apply")
                photo.applied_keywords = []
                if photo.scan_state != "ready" and not photo.proposed_caption:
                    photo.apply_state = "noop"
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue
                if not photo.proposed_keywords and not photo.proposed_caption:
                    photo.apply_state = "noop"
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue

                try:
                    assert selector is not None and bridge is not None
                    selected = selector.revalidate(photo.photos_local_identifier)
                    if selected.local_id != photo.photos_local_identifier:
                        raise _IdentityMismatchError
                    if photo.uuid is None:
                        raise ValueError("actionable photo UUID is unavailable")
                    fresh = _require_fresh_identity(bridge.read(photo.uuid), photo)
                    existing = list(fresh.existing_keywords)
                    existing_keys = {canonical_keyword_key(value) for value in existing}
                    actual_added = [
                        value for value in photo.proposed_keywords if canonical_keyword_key(value) not in existing_keys
                    ]
                    caption_to_apply = None
                    initial_caption_present = bool(fresh.description)
                    if photo.proposed_caption:
                        if initial_caption_present:
                            photo.caption_state = "preserved"
                        else:
                            caption_to_apply = photo.proposed_caption
                    # Refresh all mutable metadata before deciding the write
                    # set. This preserves keywords added after the first
                    # identity read and also catches a concurrent caption.
                    latest = _require_fresh_identity(bridge.read(photo.uuid), photo)
                    existing = list(latest.existing_keywords)
                    existing_keys = {canonical_keyword_key(value) for value in existing}
                    actual_added = [
                        value for value in photo.proposed_keywords if canonical_keyword_key(value) not in existing_keys
                    ]
                    if photo.proposed_caption:
                        if initial_caption_present or latest.description:
                            photo.caption_state = "preserved"
                            caption_to_apply = None
                        else:
                            caption_to_apply = photo.proposed_caption
                except _IdentityMismatchError:
                    photo.apply_state = "failed"
                    _replace_stage_errors(photo, "apply", "IDENTITY_MISMATCH")
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue
                except Exception as error:
                    photo.apply_state = "failed"
                    _replace_stage_errors(photo, "apply", _adapter_error_code(error, "APPLY_READ_FAILED"))
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue

                if not actual_added and caption_to_apply is None:
                    photo.apply_state = "noop"
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue

                desired = existing + actual_added
                photo.apply_state = "writing"
                _persist_mutation_checkpoint(manifest_path.parent, manifest)
                assert photo.uuid is not None
                write_error: Exception | None = None
                write_error_code = "WRITE_UNCERTAIN"
                try:
                    if actual_added:
                        readback = bridge.replace_keywords(photo.uuid, desired)
                        if not _readback_contains(readback, existing, actual_added):
                            raise ValueError("keyword readback did not verify")
                        # Record each field immediately after its own read-back.
                        # A later caption failure leaves the keyword write
                        # uncertain as a combined operation, but preserves
                        # evidence that this part was verified.  Rollback
                        # still refuses uncertain applies automatically.
                        photo.applied_keywords = list(actual_added)
                except Exception as error:
                    write_error = error
                    write_error_code = _adapter_error_code(error, "KEYWORD_WRITE_UNCERTAIN")

                if caption_to_apply is not None:
                    try:
                        # Keywords and captions are separate Apple Events. A
                        # concurrent editor can fill the description while
                        # keywords are being written, so guard the setter
                        # with one final read and preserve that external text.
                        latest = _require_fresh_identity(bridge.read(photo.uuid), photo)
                        if latest.description:
                            photo.caption_state = "preserved"
                            caption_to_apply = None
                        else:
                            readback_caption = bridge.replace_description(photo.uuid, caption_to_apply)
                            if readback_caption != caption_to_apply:
                                raise ValueError("caption readback did not verify")
                            photo.applied_caption = caption_to_apply
                            photo.caption_state = "verified"
                    except Exception as error:
                        write_error = write_error or error
                        if write_error is error:
                            write_error_code = _adapter_error_code(error, "CAPTION_WRITE_UNCERTAIN")

                if write_error is not None:
                    photo.apply_state = "uncertain"
                    _replace_stage_errors(photo, "apply", write_error_code)
                    if photo.applied_keywords or photo.applied_caption:
                        # Preserve a receipt only for fields whose own
                        # read-back already succeeded before a later field
                        # failed. Unknown writes remain receipt-free and are
                        # blocked from status/apply/rollback decisions.
                        photo.mutation_digest = compute_mutation_digest(manifest, photo)
                    _persist_mutation_checkpoint(manifest_path.parent, manifest)
                    continue

                photo.apply_state = "verified"
                photo.applied_keywords = actual_added
                if caption_to_apply is not None:
                    photo.applied_caption = caption_to_apply
                    photo.caption_state = "verified"
                photo.mutation_digest = compute_mutation_digest(manifest, photo)
                _replace_stage_errors(photo, "apply")
                _persist_mutation_checkpoint(manifest_path.parent, manifest)
                if cancel_requested():
                    cancelled = True

            # A cancellation can race with the final read-back. If no row is
            # left actionable, the operation completed successfully and must
            # not disagree with the subsequent status (complete/rollback).
            if cancelled and not any(
                photo.apply_state in {"not_run", "failed", "cancelled"}
                and not _photo_has_scan_errors(photo)
                and (
                    photo.scan_state == "ready"
                    or (photo.scan_state == "noop" and photo.proposed_caption)
                )
                for photo in manifest.photos
            ):
                cancelled = False
            if cancelled:
                partial = True
            else:
                partial = False
    except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
        code = "PHOTOS_AUTOMATION_DENIED" if isinstance(error, PhotoScriptPermissionError) else "PHOTOS_ACCESS_DENIED"
        if isinstance(error, PhotoScriptUnavailableError):
            code = error.code
        return _fatal(code)
    except Exception:
        return _fatal("LOCK_OR_MANIFEST_FAILED")

    scan_errors = _scan_error_codes(manifest)
    partial = partial or bool(scan_errors) or any(photo.apply_state in {"failed", "uncertain", "writing", "cancelled"} for photo in manifest.photos)
    counts = _workflow_counts(manifest)
    has_uncertain = counts["apply"].get("uncertain", 0) + counts["apply"].get("writing", 0) > 0
    has_failed_or_cancelled = counts["apply"].get("failed", 0) + counts["apply"].get("cancelled", 0) > 0
    mutation_permission_codes = _manifest_mutation_permission_codes(manifest)
    mutation_permission_action = next(
        (_permission_next_action(code) for code in mutation_permission_codes),
        None,
    )
    if has_uncertain:
        next_action = "manual_review"
    elif _manifest_mutation_has_fatal_compatibility_error(manifest):
        next_action = "fix_fatal_error"
    elif mutation_permission_action is not None:
        next_action = mutation_permission_action
    elif has_failed_or_cancelled:
        next_action = "retry_failed_operation"
    elif counts["apply"].get("verified", 0):
        next_action = "rollback_available"
    elif scan_errors:
        # No mutation was verified, so the inherited scan failure is the
        # actionable recovery rather than an unexplained successful-looking
        # no-op.
        next_action = "fix_failed_scan"
    else:
        next_action = "none"
    return WorkflowResult(
        exit_code=1 if partial else 0,
        manifest_path=manifest_path,
        manifest=manifest,
        error_codes=tuple(sorted(set(_workflow_error_codes(manifest, "apply", cancelled=cancelled)) | set(scan_errors))),
        warning_codes=_manifest_warning_codes(manifest),
        counts=counts,
        next_action=next_action,
    )


def run_rollback(
    manifest_path: Path,
    *,
    dependencies: ApplyDependencies | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> WorkflowResult:
    try:
        manifest_path = Path(manifest_path)
    except (OSError, TypeError, ValueError):
        return _fatal("MANIFEST_INVALID")
    cancel_requested = cancel_requested or (lambda: False)
    try:
        initial = _load_manifest_path(manifest_path)
    except Exception:
        return _fatal("MANIFEST_INVALID")
    if initial.schema_version == 1:
        return _fatal("MANIFEST_NOT_REVIEWED")
    if initial.scan_status not in {"ready", "ready_with_errors"}:
        return _fatal("SCAN_NOT_READY")
    if initial.schema_version >= 2 and initial.reviewed_from_run_id is None:
        return _fatal("MANIFEST_NOT_REVIEWED")
    if initial.schema_version >= 2 and not _review_provenance_is_valid(manifest_path, initial):
        return _fatal("REVIEW_PROVENANCE_INVALID")
    if initial.schema_version >= 2 and not _mutation_evidence_is_valid(initial):
        return _fatal("MUTATION_EVIDENCE_INVALID")
    if not _rollback_error_state_is_consistent(initial):
        return _fatal("MANIFEST_INVALID")

    try:
        dependencies = _mutation_dependencies(manifest_path, dependencies)
    except (ValueError, OSError):
        return _fatal("REVIEW_PROVENANCE_INVALID")
    try:
        with _operation_locks(manifest_path.parent, dependencies.global_lock_path):
            manifest = _load_manifest_path(manifest_path)
            def persist_rollback_state(photo: PhotoRecord) -> None:
                photo.rollback_digest = compute_rollback_digest(manifest, photo)
                _persist_mutation_checkpoint(manifest_path.parent, manifest)

            if manifest.schema_version == 1:
                return _fatal("MANIFEST_NOT_REVIEWED")
            if manifest.scan_status not in {"ready", "ready_with_errors"}:
                return _fatal("SCAN_NOT_READY")
            if manifest.schema_version >= 2 and manifest.reviewed_from_run_id is None:
                return _fatal("MANIFEST_NOT_REVIEWED")
            if manifest.schema_version >= 2 and not _review_provenance_is_valid(manifest_path, manifest):
                return _fatal("REVIEW_PROVENANCE_INVALID")
            if manifest.schema_version >= 2 and not _mutation_evidence_is_valid(manifest):
                return _fatal("MUTATION_EVIDENCE_INVALID")
            if not _rollback_error_state_is_consistent(manifest):
                return _fatal("MANIFEST_INVALID")

            for photo in manifest.photos:
                if photo.rollback_state == "removing":
                    photo.rollback_state = "uncertain"
                    if photo.applied_caption is not None and photo.caption_state != "removed":
                        photo.caption_state = "uncertain"
                    _replace_stage_errors(photo, "rollback", "INTERRUPTED_REMOVAL")
                    persist_rollback_state(photo)

            actionable = [
                photo for photo in manifest.photos
            if photo.apply_state == "verified"
                and (photo.applied_keywords or photo.applied_caption)
                and photo.rollback_state in {"not_run", "failed", "cancelled"}
            ]
            if actionable:
                try:
                    selector = dependencies.selector_factory()
                    bridge = dependencies.bridge_factory()
                except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
                    # Preserve initialization failures in the rollback journal
                    # as well as the immediate terminal result. The receipt
                    # makes the failed checkpoint auditable and retryable once
                    # the local permission or compatibility issue is fixed.
                    photo = actionable[0]
                    photo.rollback_state = "failed"
                    _replace_stage_errors(photo, "rollback", _adapter_error_code(error, "ROLLBACK_FAILED"))
                    persist_rollback_state(photo)
                    actionable = []
                    selector = None
                    bridge = None
            else:
                selector = None
                bridge = None

            cancelled = False
            for photo in actionable:
                if cancel_requested():
                    photo.rollback_state = "cancelled"
                    _replace_stage_errors(photo, "rollback", "CANCELLED")
                    persist_rollback_state(photo)
                    cancelled = True
                    break
                _replace_stage_errors(photo, "rollback")
                remaining_applied = _remaining_applied_keywords(photo)
                if not remaining_applied:
                    remaining_applied = []
                try:
                    assert selector is not None and bridge is not None
                    selected = selector.revalidate(photo.photos_local_identifier)
                    if selected.local_id != photo.photos_local_identifier:
                        raise _IdentityMismatchError
                    if photo.uuid is None:
                        raise ValueError("rollback photo UUID is unavailable")
                    fresh = _require_fresh_identity(bridge.read(photo.uuid), photo)
                    existing = list(fresh.existing_keywords)
                    caption_to_remove = False
                    if photo.applied_caption:
                        if fresh.description == photo.applied_caption:
                            caption_to_remove = True
                        elif fresh.description == "":
                            photo.caption_state = "removed"
                        else:
                            photo.rollback_state = "uncertain"
                            photo.caption_state = "uncertain"
                            _replace_stage_errors(photo, "rollback", "REMOVAL_UNCERTAIN")
                            persist_rollback_state(photo)
                            continue
                    # Refresh mutable keywords immediately before calculating
                    # the replacement list. This preserves a keyword added
                    # by another editor after the identity read above.
                    latest = _require_fresh_identity(bridge.read(photo.uuid), photo)
                    existing = list(latest.existing_keywords)
                    if photo.applied_caption:
                        if latest.description == photo.applied_caption:
                            caption_to_remove = True
                        elif latest.description == "":
                            caption_to_remove = False
                            photo.caption_state = "removed"
                        else:
                            photo.rollback_state = "uncertain"
                            photo.caption_state = "uncertain"
                            _replace_stage_errors(photo, "rollback", "REMOVAL_UNCERTAIN")
                            persist_rollback_state(photo)
                            continue
                except _IdentityMismatchError:
                    photo.rollback_state = "failed"
                    _replace_stage_errors(photo, "rollback", "IDENTITY_MISMATCH")
                    persist_rollback_state(photo)
                    continue
                except Exception as error:
                    photo.rollback_state = "failed"
                    _replace_stage_errors(photo, "rollback", _adapter_error_code(error, "ROLLBACK_FAILED"))
                    persist_rollback_state(photo)
                    continue

                desired = list(existing)
                missing: list[str] = []
                removed: list[str] = []
                for applied in remaining_applied:
                    try:
                        desired.remove(applied)
                        removed.append(applied)
                    except ValueError:
                        missing.append(applied)

                removed_count = len(existing) - len(desired)
                existing_keys = {canonical_keyword_key(value) for value in existing}
                has_casing_conflict = any(
                    canonical_keyword_key(value) in existing_keys for value in missing
                )
                if removed_count == 0 and not caption_to_remove:
                    if any(canonical_keyword_key(value) in existing_keys for value in remaining_applied):
                        photo.rollback_state = "casing_conflict"
                        _replace_stage_errors(photo, "rollback", "CASING_CONFLICT")
                    else:
                        photo.rollback_state = "already_absent"
                    persist_rollback_state(photo)
                    continue

                photo.rollback_state = "removing"
                persist_rollback_state(photo)
                try:
                    assert photo.uuid is not None
                    if removed_count:
                        readback = bridge.replace_keywords(photo.uuid, desired)
                        if Counter(readback) != Counter(desired):
                            raise ValueError("keyword removal readback did not verify")
                        # Persist the effective keyword removal before trying
                        # to remove a caption. A caption failure (or an
                        # interruption between fields) must not erase this
                        # audit evidence from the manifest.
                        photo.rolled_back_keywords.extend(removed)
                        persist_rollback_state(photo)
                    if caption_to_remove:
                        try:
                            if bridge.replace_description(photo.uuid, "") != "":
                                raise ValueError("caption removal readback did not verify")
                        except Exception:
                            # The caption may still exist (or may have been
                            # changed by an external editor). Preserve its
                            # ownership evidence but make the field's outcome
                            # explicitly manual-review-only.
                            photo.caption_state = "uncertain"
                            raise
                except Exception as error:
                    photo.rollback_state = "uncertain"
                    _replace_stage_errors(photo, "rollback", _adapter_error_code(error, "REMOVAL_UNCERTAIN"))
                    persist_rollback_state(photo)
                    continue

                if not removed_count:
                    photo.rolled_back_keywords.extend(removed)
                if caption_to_remove:
                    photo.caption_state = "removed"
                if has_casing_conflict:
                    photo.rollback_state = "casing_conflict"
                    _replace_stage_errors(photo, "rollback", "CASING_CONFLICT")
                elif missing:
                    photo.rollback_state = "failed"
                    _replace_stage_errors(photo, "rollback", "APPLIED_KEYWORD_MISSING")
                else:
                    photo.rollback_state = "verified_removed"
                    _replace_stage_errors(photo, "rollback")
                persist_rollback_state(photo)
                if cancel_requested():
                    cancelled = True

            # A cancellation can race with the final read-back. If no row is
            # left actionable, the operation completed successfully and must
            # not disagree with the subsequent status (complete/none).
            if cancelled and not any(
                photo.apply_state == "verified"
                and (photo.applied_keywords or photo.applied_caption)
                and photo.rollback_state in {"not_run", "failed", "cancelled"}
                for photo in manifest.photos
            ):
                cancelled = False
            if cancelled:
                partial = True
            else:
                partial = False
    except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
        code = "PHOTOS_AUTOMATION_DENIED" if isinstance(error, PhotoScriptPermissionError) else "PHOTOS_ACCESS_DENIED"
        if isinstance(error, PhotoScriptUnavailableError):
            code = error.code
        return _fatal(code)
    except Exception:
        return _fatal("LOCK_OR_MANIFEST_FAILED")

    scan_errors = _scan_error_codes(manifest)
    partial = partial or bool(scan_errors) or any(
        photo.rollback_state in {"failed", "uncertain", "removing", "casing_conflict", "cancelled"}
        for photo in manifest.photos
    ) or any(photo.apply_state in {"writing", "uncertain"} for photo in manifest.photos)
    counts = _workflow_counts(manifest)
    has_uncertain = (
        counts["rollback"].get("uncertain", 0)
        + counts["rollback"].get("removing", 0)
        + counts["rollback"].get("casing_conflict", 0)
        + counts["apply"].get("writing", 0)
        + counts["apply"].get("uncertain", 0)
    ) > 0
    has_failed_or_cancelled = (
        counts["rollback"].get("failed", 0)
        + counts["rollback"].get("cancelled", 0)
    ) > 0
    mutation_permission_codes = _manifest_mutation_permission_codes(manifest)
    mutation_permission_action = next(
        (_permission_next_action(code) for code in mutation_permission_codes),
        None,
    )
    return WorkflowResult(
        exit_code=1 if partial else 0,
        manifest_path=manifest_path,
        manifest=manifest,
        counts=counts,
        error_codes=tuple(sorted(set(_workflow_error_codes(manifest, "rollback", cancelled=cancelled)) | set(scan_errors))),
        warning_codes=_manifest_warning_codes(manifest),
        next_action=(
            "manual_review"
            if has_uncertain
            else (
                "fix_fatal_error"
                if _manifest_mutation_has_fatal_compatibility_error(manifest)
                else (
                    mutation_permission_action
                    if mutation_permission_action is not None
                    else (
                        "retry_failed_operation"
                        if has_failed_or_cancelled
                        else ("fix_failed_scan" if scan_errors else "none")
                    )
                )
            )
        ),
    )


def run_status(manifest_path: Path) -> WorkflowResult:
    try:
        manifest_path = Path(manifest_path)
    except (OSError, TypeError, ValueError):
        return _fatal("MANIFEST_INVALID")
    try:
        manifest = _load_manifest_path(manifest_path)
    except Exception:
        return _fatal("MANIFEST_INVALID")
    if not _mutation_error_state_is_consistent(manifest):
        # A retry/rollback action without a persisted cause is not auditable;
        # fail closed instead of presenting an incomplete history as safe.
        return _fatal("MANIFEST_INVALID")
    provenance_invalid = manifest.schema_version in {3, 4} and not _review_provenance_is_valid(manifest_path, manifest)
    mutation_evidence_invalid = manifest.schema_version >= 2 and not _mutation_evidence_is_valid(manifest)
    reviewed_caption_state_invalid = _reviewed_caption_state_is_invalid(manifest)
    counts = _workflow_counts(manifest)
    apply_eligible = [
        photo
        for photo in manifest.photos
        if (
            not _photo_has_scan_errors(photo)
            and (photo.scan_state == "ready" or (photo.scan_state == "noop" and photo.proposed_caption))
        ) or photo.apply_state not in {"not_run", "noop"}
    ]
    rollback_eligible = [
        photo
        for photo in manifest.photos
        if photo.apply_state == "verified" and (photo.applied_keywords or photo.applied_caption)
    ]
    scan_has_errors = bool(manifest.run_errors) or any(
        error["stage"] in {"metadata", "export", "analysis", "cleanup", "workspace"}
        for photo in manifest.photos
        for error in photo.errors
    )
    permission_codes = _manifest_permission_codes(manifest)
    apply_counts = Counter(photo.apply_state for photo in apply_eligible)
    rollback_counts = Counter(photo.rollback_state for photo in rollback_eligible)
    has_uncertain = (
        apply_counts.get("uncertain", 0)
        + apply_counts.get("writing", 0)
        + rollback_counts.get("uncertain", 0)
        + rollback_counts.get("removing", 0)
        + rollback_counts.get("casing_conflict", 0)
    ) > 0
    has_failed = apply_counts.get("failed", 0) + rollback_counts.get("failed", 0) > 0
    has_cancelled = apply_counts.get("cancelled", 0) + rollback_counts.get("cancelled", 0) > 0
    mutation_started = any(
        photo.apply_state != "not_run" or photo.rollback_state != "not_run"
        for photo in manifest.photos
    )
    # Schema 1 and schema 2 are scan artifacts, not reviewed mutation
    # manifests.  A migrated or externally edited scan may still carry a
    # syntactically valid mutation receipt; status must not turn that into an
    # apparent rollback target without schema-3 review provenance.
    legacy_unreviewed = manifest.schema_version < 3 and any(
        photo.apply_state in {"writing", "verified", "failed", "uncertain", "cancelled"}
        or photo.rollback_state != "not_run"
        or bool(photo.applied_keywords)
        or bool(photo.rolled_back_keywords)
        or photo.applied_caption is not None
        or photo.mutation_digest is not None
        for photo in manifest.photos
    )
    if manifest.scan_status == "failed":
        next_action = "fix_failed_scan"
    elif permission_codes and not mutation_started:
        # A permission-denied row is not an apply candidate.  Surface the
        # exact TCC recovery action even when the manifest contains no
        # eligible rows, so status never suggests a blind/no-op apply.
        next_action = _permission_next_action(permission_codes[0]) or "rescan_after_permissions"
    elif _manifest_scan_has_fatal_compatibility_error(manifest) and not mutation_started:
        next_action = "fix_fatal_error"
    elif has_uncertain:
        next_action = "manual_review"
    elif _manifest_mutation_has_fatal_compatibility_error(manifest):
        next_action = "fix_fatal_error"
    elif _manifest_mutation_permission_codes(manifest):
        next_action = _permission_next_action(_manifest_mutation_permission_codes(manifest)[0]) or "rescan_after_permissions"
    elif has_failed or has_cancelled:
        next_action = "retry_failed_operation"
    elif rollback_counts.get("not_run", 0):
        next_action = "rollback_available"
    elif counts["rollback"].get("verified_removed", 0) or counts["rollback"].get("already_absent", 0):
        next_action = "fix_failed_scan" if scan_has_errors else "none"
    elif counts["apply"].get("verified", 0):
        next_action = "rollback_available"
    elif any(photo.apply_state == "not_run" for photo in apply_eligible):
        next_action = "review_then_apply"
    elif scan_has_errors:
        next_action = "fix_failed_scan"
    else:
        next_action = "none"
    partial = has_uncertain or has_failed or has_cancelled or scan_has_errors
    if not apply_eligible:
        apply_status = "not_started"
    elif apply_counts.get("uncertain", 0) or apply_counts.get("writing", 0):
        apply_status = "uncertain"
    elif apply_counts.get("failed", 0) or apply_counts.get("cancelled", 0):
        apply_status = "partial"
    elif apply_counts.get("not_run", 0):
        apply_status = "pending"
    else:
        apply_status = "complete"
    rollback_terminal = sum(
        rollback_counts.get(state, 0)
        for state in ("verified_removed", "already_absent", "casing_conflict")
    )
    if rollback_counts.get("uncertain", 0) or rollback_counts.get("removing", 0):
        rollback_status = "uncertain"
    elif rollback_counts.get("failed", 0) or rollback_counts.get("casing_conflict", 0) or rollback_counts.get("cancelled", 0):
        rollback_status = "partial"
    elif rollback_terminal and rollback_counts.get("not_run", 0):
        rollback_status = "partial"
    elif rollback_terminal:
        rollback_status = "complete"
    else:
        rollback_status = "not_started"
    if manifest.scan_status == "failed":
        apply_status = "blocked"
        rollback_status = "blocked"
    if legacy_unreviewed:
        # Legacy manifests have no review provenance or mutation receipt. A
        # historical mutation state must not look like an authorized rollback
        # target; require a fresh dry-run instead.
        apply_status = "blocked"
        rollback_status = "blocked"
        next_action = "rescan_after_provenance"
    elif provenance_invalid:
        # A detached reviewed copy can still be parsed, but it is not an
        # authorization to mutate Photos.  Keep status useful while making
        # the same fail-closed decision as apply/rollback.
        apply_status = "blocked"
        rollback_status = "blocked"
        next_action = "rescan_after_provenance"
    elif mutation_evidence_invalid:
        apply_status = "blocked"
        rollback_status = "blocked"
        next_action = "rescan_after_mutation_evidence"
    elif reviewed_caption_state_invalid:
        apply_status = "blocked"
        rollback_status = "blocked"
        next_action = "rescan_after_provenance"
    error_counts = Counter(error["code"] for photo in manifest.photos for error in photo.errors)
    error_counts.update(error["code"] for error in manifest.run_errors)
    # A crash can leave a checkpoint persisted before its recovery error is
    # written. Derive the stable cause from the checkpoint so status/support
    # remain actionable immediately after restart; apply/rollback will still
    # persist the per-photo error when they perform the recovery step.
    if any(photo.apply_state == "writing" for photo in manifest.photos):
        error_counts.setdefault("INTERRUPTED_WRITE", 1)
    if any(photo.rollback_state == "removing" for photo in manifest.photos):
        error_counts.setdefault("INTERRUPTED_REMOVAL", 1)
    reviewed_scope = manifest.schema_version in {3, 4} and manifest.reviewed_from_run_id is not None
    approved_rows = [
        photo for photo in manifest.photos
        if reviewed_scope and not _photo_has_scan_errors(photo)
    ]
    no_change = sum(
        (photo.scan_state == "noop" and not photo.proposed_caption)
        or photo.apply_state == "noop"
        or photo.rollback_state == "already_absent"
        for photo in manifest.photos
    )
    status_summary: dict[str, Any] = {
        "scan_status": manifest.scan_status,
        "photos_access": manifest.selection["access"],
        # Older manifests omit the optional field; their selector was the
        # default recent strategy.
        "selection_strategy": manifest.selection.get("strategy", "recent"),
        "apply_status": apply_status,
        "rollback_status": rollback_status,
        "selected": manifest.selection["eligible"],
        "processed": len(manifest.photos),
        "no_change": no_change,
        "failed": (
            len(manifest.run_errors)
            + sum(
                _photo_has_scan_errors(photo)
                or photo.apply_state in {"failed", "cancelled"}
                or photo.rollback_state in {"failed", "cancelled"}
                for photo in manifest.photos
            )
        ),
        "uncertain": (
            counts["apply"].get("uncertain", 0)
            + counts["apply"].get("writing", 0)
            + counts["rollback"].get("uncertain", 0)
            + counts["rollback"].get("removing", 0)
            + counts["rollback"].get("casing_conflict", 0)
        ),
        "screenshots_excluded": manifest.selection["screenshots_excluded"],
        # PhotoKitSelector fetches only image assets, so non-image assets are
        # outside the enumeration and the truthful reported count is zero.
        "non_image_excluded": 0,
        "keywords_proposed": sum(len(photo.proposed_keywords) for photo in manifest.photos),
        "keywords_verified": sum(
            len(photo.applied_keywords)
            for photo in manifest.photos
            if _has_valid_apply_receipt(manifest, photo)
        ),
        "keywords_removed": sum(
            len(photo.rolled_back_keywords)
            for photo in manifest.photos
            if _has_valid_rollback_receipt(manifest, photo)
        ),
        # A no-op or verified apply can prove that a proposed keyword was
        # already present when the write was evaluated. Pending proposals
        # remain distinct until an apply attempt starts.
        "keywords_preserved": sum(
            sum(
                canonical_keyword_key(value) not in {
                    canonical_keyword_key(applied) for applied in photo.applied_keywords
                }
                for value in photo.proposed_keywords
            )
            for photo in manifest.photos
            if photo.apply_state in {"noop", "verified"}
        ),
        "keywords_pending": sum(
            len(photo.proposed_keywords)
            for photo in manifest.photos
            if photo.apply_state == "not_run" and not _photo_has_scan_errors(photo)
        ),
        # Aggregate caption states without exposing caption text to status or
        # support projections. These counts distinguish a caption-only
        # approval from a true no-op.
        "captions_proposed": sum(photo.proposed_caption is not None for photo in manifest.photos),
        "captions_verified": sum(
            photo.caption_state == "verified" and _has_valid_apply_receipt(manifest, photo)
            for photo in manifest.photos
        ),
        "captions_removed": sum(
            photo.caption_state == "removed" and _has_valid_rollback_receipt(manifest, photo)
            for photo in manifest.photos
        ),
        "captions_preserved": sum(photo.caption_state == "preserved" for photo in manifest.photos),
        "captions_pending": sum(
            photo.proposed_caption is not None
            and photo.caption_state == "proposed"
            and photo.apply_state == "not_run"
            and not _photo_has_scan_errors(photo)
            for photo in manifest.photos
        ),
        # selected is the original PhotoKit scan count. Keep it stable and
        # expose the post-review write scope separately.
        "approved_photos": sum(
            reviewed_scope and bool(photo.proposed_keywords or photo.proposed_caption)
            for photo in approved_rows
        ),
        "approved_keywords": sum(
            len(photo.proposed_keywords) for photo in approved_rows
        ),
        "approved_captions": sum(
            photo.proposed_caption is not None for photo in approved_rows
        ),
        "errors_by_code": dict(sorted(error_counts.items())),
    }
    if legacy_unreviewed:
        status_error_codes = ("MANIFEST_NOT_REVIEWED",)
    elif provenance_invalid:
        status_error_codes = ("REVIEW_PROVENANCE_INVALID",)
    elif mutation_evidence_invalid:
        status_error_codes = ("MUTATION_EVIDENCE_INVALID",)
    elif reviewed_caption_state_invalid:
        status_error_codes = ("REVIEW_NOT_PRISTINE",)
    elif error_counts:
        # All persisted error pairs were validated by the manifest schema;
        # expose their stable codes so CLI/support cannot report a partial
        # run as a silent success.
        status_error_codes = tuple(sorted(error_counts))
    else:
        status_error_codes = ()
    warning_codes = tuple(
        code for condition, code in (
            (manifest.selection["eligible"] < manifest.selection["requested"], "FEWER_PHOTOS_AVAILABLE"),
            (manifest.selection["access"] == "limited", "PHOTOS_ACCESS_LIMITED"),
        ) if condition
    )
    return WorkflowResult(
        exit_code=2 if legacy_unreviewed or provenance_invalid or mutation_evidence_invalid or reviewed_caption_state_invalid else (1 if partial or manifest.scan_status == "failed" else 0),
        manifest_path=manifest_path,
        manifest=manifest,
        counts=counts,
        error_codes=status_error_codes,
        status_summary=status_summary,
        warning_codes=warning_codes,
        next_action=next_action,
    )
