"""Application-facing service functions with privacy-safe progress events.

The CLI intentionally remains a thin presentation layer over ``workflows``.
This module gives the native app the same workflow entry points without
exposing Photos metadata, model captions, image paths, or model responses.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import stat
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from .adapters import (
    OllamaVisionClient,
    PhotoScriptPermissionError,
    PhotoScriptUnavailableError,
    PhotosAccessError,
    validate_ollama_model_name,
    validate_ollama_pull_instruction,
)
from .manifest import MODEL_REASONS, MAX_MANIFEST_BYTES, MANIFEST_FILENAME, ManifestError, PhotoRecord, ScanManifest, load_manifest, write_manifest, write_preview_csv
from .taxonomy import canonical_keyword_key, proposed_keywords
from .workflows import DEFAULT_DETAILED_MODEL, DEFAULT_MODEL, WorkflowResult, run_apply, run_rollback, run_scan


ServiceEvent = dict[str, object]
EventEmitter = Callable[[ServiceEvent], None]
APP_STORAGE_NAME = "Photos Local Keyword Indexer"
MAX_SUPPORT_SCREENSHOTS_EXCLUDED = 1_000_000


def app_runs_root(home: Path | None = None) -> Path:
    """Return the only runs root accepted from the distributed app."""
    try:
        home_directory = Path.home() if home is None else Path(home)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("home path is invalid") from error
    return home_directory / "Library" / "Application Support" / APP_STORAGE_NAME / "runs"


def _reject_symlink_components(path: Path, *, home: Path) -> None:
    current = Path(home)
    try:
        components = path.relative_to(current).parts
    except ValueError as error:
        raise ValueError("app runs root is outside the user home") from error
    for component in ("", *components):
        if component:
            current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("app runs root must not traverse symlinks")


def _ensure_private_directory(path: Path, *, home: Path) -> None:
    _reject_symlink_components(path, home=home)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise ValueError("app runs root is unsafe")
    os.chmod(path, 0o700)


def _validate_existing_private_components(path: Path, *, home: Path) -> None:
    """Validate existing path components without creating application storage."""
    try:
        components = path.relative_to(home).parts
    except ValueError as error:
        raise ValueError("app runs root is outside the user home") from error
    current = Path(home)
    for component in components:
        current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
        ):
            raise ValueError("app runs root is unsafe")


def validate_app_runs_root(
    value: Path | str,
    *,
    home: Path | None = None,
    create: bool = True,
) -> Path:
    """Validate an app runs root, optionally creating its private directories.

    Scan preflight uses ``create=False`` so a missing Ollama model or endpoint
    failure cannot leave an empty Application Support run root behind.
    """
    candidate = Path(os.path.normpath(os.fspath(value)))
    try:
        home_directory = Path.home() if home is None else Path(home)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("home path is invalid") from error
    expected = app_runs_root(home_directory)
    if not candidate.is_absolute():
        raise ValueError("app runs root must be absolute")
    try:
        candidate.relative_to(expected)
    except ValueError as error:
        raise ValueError("app runs root is outside Application Support") from error
    if create:
        _ensure_private_directory(expected, home=home_directory)
        _ensure_private_directory(candidate, home=home_directory)
    else:
        _validate_existing_private_components(candidate, home=home_directory)
    return candidate


def validate_app_manifest_path(value: Path | str, *, home: Path | None = None) -> Path:
    """Validate a manifest path accepted by the distributed helper.

    Mutation commands must never be able to address arbitrary user files.  A
    manifest is therefore required to be the private ``manifest.json`` file
    directly inside one run directory below this app's Application Support
    root.  The checks intentionally mirror ``AppStorage`` and reject symlink,
    hardlink, ownership and permission surprises before a workflow is called.
    """
    candidate = Path(os.path.normpath(os.fspath(value)))
    home_directory = Path.home() if home is None else Path(home)
    expected = app_runs_root(home_directory)
    if not candidate.is_absolute() or candidate.name != MANIFEST_FILENAME:
        raise ValueError("manifest path is invalid")
    try:
        relative = candidate.relative_to(expected)
    except ValueError as error:
        raise ValueError("manifest path is outside Application Support") from error
    if len(relative.parts) != 2:
        raise ValueError("manifest path must be directly inside a run directory")
    _reject_symlink_components(candidate, home=home_directory)
    # A private manifest is not sufficient if its containing run directory is
    # traversable by another local user.  Repair the app-owned runs root (the
    # same directory creation contract used for new runs), but fail closed for
    # an existing run directory rather than silently changing its permissions.
    try:
        _ensure_private_directory(expected, home=home_directory)
        run_details = os.lstat(candidate.parent)
    except (FileNotFoundError, OSError) as error:
        raise ValueError("manifest run directory is unsafe") from error
    if (
        not stat.S_ISDIR(run_details.st_mode)
        or run_details.st_uid != os.getuid()
        or stat.S_IMODE(run_details.st_mode) != 0o700
    ):
        raise ValueError("manifest run directory is unsafe")
    try:
        details = os.lstat(candidate)
    except FileNotFoundError as error:
        raise ValueError("manifest does not exist") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or details.st_size > MAX_MANIFEST_BYTES
    ):
        raise ValueError("manifest file is unsafe")
    return candidate


class CancellationToken:
    """Thread-safe, cooperative cancellation signal for an app workflow."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class ScanRequest:
    runs_root: Path
    limit: int = 20
    model_policy: str = "single"
    model: str | None = None
    fast_model: str = DEFAULT_MODEL
    detailed_model: str = DEFAULT_DETAILED_MODEL
    apple_maps: bool = False
    include_caption: bool = False
    random_selection: bool = False

    def __post_init__(self) -> None:
        # Keep the service API as strict as the JSONL boundary. In particular,
        # truthy values must never silently opt a caller into sending GPS to
        # Apple Maps or requesting caption proposals.
        if type(self.limit) is not int or not 1 <= self.limit <= 500:
            raise ValueError("scan limit is invalid")
        if type(self.random_selection) is not bool or type(self.apple_maps) is not bool or type(self.include_caption) is not bool:
            raise ValueError("scan privacy options must be boolean")

    def selected_model(self) -> str | None:
        """Return an explicit model only for the single-model policy."""
        if type(self.model_policy) is not str or self.model_policy not in {"single", "adaptive"}:
            raise ValueError("model policy is invalid")
        if (
            type(self.fast_model) is not str
            or not self.fast_model.strip()
            or type(self.detailed_model) is not str
            or not self.detailed_model.strip()
        ):
            raise ValueError("model name is invalid")
        if self.model is not None and (type(self.model) is not str or not self.model.strip()):
            raise ValueError("model name is invalid")
        if self.model_policy == "adaptive":
            if self.model is not None:
                raise ValueError("adaptive policy cannot include an explicit model")
            return None
        return self.model or self.fast_model


def _cancelled_result() -> WorkflowResult:
    return WorkflowResult(exit_code=1, error_codes=("CANCELLED",), next_action="none")


def _unsafe_workflow_result() -> WorkflowResult:
    return WorkflowResult(
        exit_code=2,
        error_codes=("UNSAFE_WORKFLOW_RESULT",),
        next_action="fix_fatal_error",
    )


def _cancel_result_is_safe(result: WorkflowResult) -> bool:
    """Validate terminal fields before cancellation normalizes their codes."""
    if not isinstance(result, WorkflowResult):
        return False
    if type(result.exit_code) is not int or result.exit_code not in {0, 1, 2}:
        return False
    for values in (result.warning_codes, result.error_codes):
        if type(values) not in {tuple, list} or len(values) > 16 or any(
            type(code) is not str or re.fullmatch(r"[A-Z0-9_]{1,64}", code) is None
            for code in values
        ):
            return False
    if result.exit_code == 0 and result.error_codes:
        return False
    if result.exit_code != 0 and not result.error_codes:
        return False
    if type(result.next_action) is not str or re.fullmatch(r"[a-z_]{1,64}", result.next_action) is None:
        return False
    if result.safe_instruction is not None:
        if result.exit_code == 0:
            return False
        try:
            validate_ollama_pull_instruction(result.safe_instruction)
        except Exception:
            return False
    return True


def _cancel_success_result(result: WorkflowResult) -> WorkflowResult:
    """Make a runner success consistent with a cancellation observed at return."""
    if not _cancel_result_is_safe(result):
        return _unsafe_workflow_result()
    if result.exit_code in {1, 2} and result.error_codes != ("UNSAFE_WORKFLOW_RESULT",):
        error_codes = tuple(sorted(set(result.error_codes) | {"CANCELLED"}))
        return WorkflowResult(
            exit_code=result.exit_code,
            manifest_path=result.manifest_path,
            manifest=result.manifest,
            warning_codes=result.warning_codes,
            error_codes=error_codes,
            safe_instruction=result.safe_instruction,
            counts=result.counts,
            status_summary=result.status_summary,
            next_action=result.next_action,
        )
    if result.exit_code != 0:
        return result
    return WorkflowResult(
        exit_code=1,
        manifest_path=result.manifest_path,
        manifest=result.manifest,
        warning_codes=result.warning_codes,
        error_codes=("CANCELLED",),
        safe_instruction=result.safe_instruction,
        counts=result.counts,
        status_summary=result.status_summary,
        # The runner may have completed a mutation just before cancellation
        # was observed. Preserve its safe recovery action (notably
        # ``rollback_available``) instead of hiding it behind cancellation.
        next_action=result.next_action,
    )


_SUPPORT_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,64}")
_SUPPORT_ACTION_PATTERN = re.compile(r"[a-z_]{1,64}")
_SUCCESS_COMPLETION_ACTIONS = frozenset({"none", "review_then_apply", "rollback_available"})
_SUPPORT_INSTRUCTION_PATTERN = re.compile(r"ollama pull [A-Za-z0-9._:/-]{1,128}")
_SUPPORT_OLLAMA_VERSION_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
_SUPPORT_STATUS_VALUES = {
    "scan_status": frozenset({"ready", "ready_with_errors", "failed"}),
    "photos_access": frozenset({"authorized", "limited"}),
    "selection_strategy": frozenset({"recent", "random", "targeted"}),
    "apply_status": frozenset({"not_started", "pending", "partial", "complete", "uncertain", "blocked"}),
    "rollback_status": frozenset({"not_started", "partial", "complete", "uncertain", "blocked"}),
}
_SUPPORT_SUMMARY_KEYS = (
    "scan_status", "photos_access", "selection_strategy", "apply_status", "rollback_status",
    "selected", "processed", "no_change", "failed", "uncertain",
    "screenshots_excluded", "non_image_excluded", "keywords_proposed",
    "keywords_verified", "keywords_removed", "keywords_preserved",
    "keywords_pending", "captions_proposed", "captions_verified",
    "captions_removed", "captions_preserved", "captions_pending", "approved_photos",
    "approved_keywords", "approved_captions",
)
_SERVICE_EVENT_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,64}")
_SERVICE_EVENT_ACTION_PATTERN = re.compile(r"[a-z_]{1,64}")
_SERVICE_EVENT_MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
_SERVICE_EVENT_OPERATION_PATTERN = re.compile(r"[a-z_]{1,16}")
_SERVICE_EVENT_UUID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,8}")
_SERVICE_EVENT_STATE_PATTERN = re.compile(r"[a-z_]{1,32}")
_SERVICE_EVENT_PROGRESS_STATES = frozenset({
    "ready", "noop", "analysis_failed", "cancelled", "writing", "verified", "failed", "uncertain",
    "removing", "verified_removed", "casing_conflict", "already_absent",
})


def _support_code_list(values: object) -> list[str]:
    if not isinstance(values, (tuple, list)):
        return []
    return [value for value in values if type(value) is str and _SUPPORT_CODE_PATTERN.fullmatch(value)]


def _support_codes_are_valid(values: object) -> bool:
    return isinstance(values, (tuple, list)) and len(values) <= 16 and all(
        type(value) is str and _SUPPORT_CODE_PATTERN.fullmatch(value) for value in values
    )


def _support_counter(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key, item in value.items()
        if type(key) is str
        and _SUPPORT_ACTION_PATTERN.fullmatch(key)
        and type(item) is int
        and 0 <= item <= 500
    }


def _support_counts_are_valid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or len(value) > 16:
        return False
    for stage, counters in value.items():
        if type(stage) is not str or _SUPPORT_ACTION_PATTERN.fullmatch(stage) is None:
            return False
        if not isinstance(counters, Mapping) or len(counters) > 32:
            return False
        if any(
            type(key) is not str
            or _SUPPORT_ACTION_PATTERN.fullmatch(key) is None
            or type(item) is not int
            or not 0 <= item <= 500
            for key, item in counters.items()
        ):
            return False
    return True


def _support_status_values_are_valid(value: Mapping[str, object]) -> bool:
    """Reject malformed terminal states while allowing omitted fields."""
    return all(
        key not in value
        or (type(value[key]) is str and value[key] in allowed)
        for key, allowed in _SUPPORT_STATUS_VALUES.items()
    )


def _support_error_counter(value: object) -> dict[str, int]:
    """Project bounded uppercase workflow error counts for support reports."""
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key, item in value.items()
        if type(key) is str
        and _SUPPORT_CODE_PATTERN.fullmatch(key)
        and type(item) is int
        and 0 <= item <= 500
    }


def _support_error_counter_is_valid(value: object) -> bool:
    if not isinstance(value, Mapping) or len(value) > 32:
        return False
    return all(
        type(key) is str
        and _SUPPORT_CODE_PATTERN.fullmatch(key)
        and type(item) is int
        and 0 <= item <= 500
        for key, item in value.items()
    )


def _support_manifest_is_valid(manifest: ScanManifest) -> bool:
    """Reject mutated manifest objects before projecting their state."""
    try:
        # WorkflowResult is an in-memory boundary: a runner can return a
        # ScanManifest instance whose fields were changed after construction.
        # Re-parse its strict serialized shape so support cannot report a
        # malformed scan as a successful operation.
        ScanManifest.from_dict(manifest.to_dict())
        manifest.validate_mutable_state()
        if not isinstance(manifest.model, Mapping) or _support_model_projection(manifest.model) is None:
            return False
        if not isinstance(manifest.selection, Mapping):
            return False
        for key in ("requested", "eligible", "screenshots_excluded"):
            value = manifest.selection.get(key)
            maximum = MAX_SUPPORT_SCREENSHOTS_EXCLUDED if key == "screenshots_excluded" else 500
            if type(value) is not int or not 0 <= value <= maximum:
                return False
        access = manifest.selection.get("access")
        if type(access) is not str or _SUPPORT_ACTION_PATTERN.fullmatch(access) is None:
            return False
    except Exception:
        return False
    return True


def _support_has_failure_state(
    counts: Mapping[str, object],
    status_summary: Mapping[str, object],
    manifest: object,
) -> bool:
    """Detect failure evidence that would contradict a successful result."""
    failed_states = frozenset({"analysis_failed", "failed", "uncertain", "writing", "removing", "cancelled", "casing_conflict"})
    for values in counts.values():
        projected = _support_counter(values)
        if any(projected.get(state, 0) > 0 for state in failed_states):
            return True
    if any(type(status_summary.get(key)) is int and status_summary[key] > 0 for key in ("failed", "uncertain")):
        return True
    scan_status = status_summary.get("scan_status")
    if type(scan_status) is str and scan_status in {"failed", "ready_with_errors"}:
        return True
    if any(
        type(status_summary.get(key)) is str
        and status_summary[key] in {"blocked", "partial", "uncertain"}
        for key in ("apply_status", "rollback_status")
    ):
        return True
    if isinstance(manifest, ScanManifest):
        if manifest.scan_status == "failed":
            return True
        if manifest.scan_status == "ready_with_errors" or manifest.run_errors:
            return True
        if any(
            photo.scan_state == "analysis_failed"
            or any(error["stage"] not in {"apply", "rollback"} for error in photo.errors)
            for photo in manifest.photos
        ):
            return True
        return any(
            photo.apply_state in {"failed", "uncertain", "writing", "cancelled"}
            or photo.rollback_state in {"failed", "uncertain", "removing", "cancelled", "casing_conflict"}
            for photo in manifest.photos
        )
    return False


def _support_model_projection(model: Mapping[str, object]) -> dict[str, str] | None:
    """Project either the legacy or routed model shape without raw fields."""
    version = model.get("ollama_version")
    if (
        type(version) is not str
        or len(version) > 64
        or _SUPPORT_OLLAMA_VERSION_PATTERN.fullmatch(version) is None
    ):
        return None
    if set(model) == {"name", "ollama_version", "endpoint"}:
        try:
            name = validate_ollama_model_name(model["name"])
        except Exception:
            return None
        return {
            "name": name,
            "ollama_version": version,
            "endpoint": OllamaVisionClient.base_url.removesuffix("/api"),
        }
    if set(model) != {"policy", "fast_name", "detailed_name", "ollama_version", "endpoint"}:
        return None
    policy = model["policy"]
    if type(policy) is not str or policy not in {"single", "adaptive"}:
        return None
    try:
        fast_name = validate_ollama_model_name(model["fast_name"])
        detailed_name = validate_ollama_model_name(model["detailed_name"])
    except Exception:
        return None
    return {
        "policy": policy,
        "fast_name": fast_name,
        "detailed_name": detailed_name,
        "ollama_version": version,
        "endpoint": OllamaVisionClient.base_url.removesuffix("/api"),
    }


def support_snapshot(result: WorkflowResult) -> dict[str, object]:
    """Return a bounded report suitable for sharing with support.

    This is an explicit allowlist rather than a serialization of a workflow
    result or manifest.  It deliberately excludes photo rows, titles, dates,
    keywords, captions, locations, paths, and raw adapter responses.
    """
    exit_code = result.exit_code if type(result.exit_code) is int and result.exit_code in {0, 1, 2} else 2
    error_codes = _support_code_list(result.error_codes)
    warning_codes = _support_code_list(result.warning_codes)
    status_summary_malformed = not isinstance(result.status_summary, Mapping)
    status_summary = result.status_summary if isinstance(result.status_summary, Mapping) else {}
    counts_source = result.counts if isinstance(result.counts, Mapping) else {}
    projected_status_errors = _support_error_counter(status_summary.get("errors_by_code"))
    malformed_status_errors = (
        "errors_by_code" in status_summary
        and not _support_error_counter_is_valid(status_summary.get("errors_by_code"))
    )
    next_action = result.next_action if type(result.next_action) is str and _SUPPORT_ACTION_PATTERN.fullmatch(result.next_action) else None
    malformed_next_action = not (
        type(result.next_action) is str
        and _SUPPORT_ACTION_PATTERN.fullmatch(result.next_action) is not None
    )
    # Keep support output consistent with the IPC terminal contract. A result
    # claiming success while carrying errors (including status counters) is
    # unsafe to interpret; expose a stable fatal diagnostic instead of
    # reporting contradictory state.
    manifest = result.manifest
    malformed_codes = (
        not _support_codes_are_valid(result.error_codes)
        or not _support_codes_are_valid(result.warning_codes)
        or malformed_status_errors
        or status_summary_malformed
        or not _support_status_values_are_valid(status_summary)
        or not _support_counts_are_valid(result.counts)
        or malformed_next_action
            or (
                isinstance(manifest, ScanManifest)
                and not _support_manifest_is_valid(manifest)
            )
            or (manifest is not None and not isinstance(manifest, ScanManifest))
        )
    missing_failure_codes = exit_code != 0 and not error_codes
    unsafe_result = malformed_codes or missing_failure_codes or (exit_code == 0 and (
        error_codes
        or projected_status_errors
        or _support_has_failure_state(counts_source, status_summary, manifest)
        or result.safe_instruction is not None
        or (next_action not in _SUCCESS_COMPLETION_ACTIONS)
    ))
    if unsafe_result:
        exit_code = 2
        error_codes = ["UNSAFE_WORKFLOW_RESULT"]
        next_action = "fix_fatal_error"
    snapshot: dict[str, object] = {
        "format_version": 1,
        "exit_code": exit_code,
        "error_codes": error_codes,
        "warning_codes": warning_codes,
    }
    if not unsafe_result and type(result.safe_instruction) is str and _SUPPORT_INSTRUCTION_PATTERN.fullmatch(result.safe_instruction):
        try:
            snapshot["safe_instruction"] = validate_ollama_pull_instruction(result.safe_instruction)
        except Exception:
            pass
    if next_action is not None:
        snapshot["next_action"] = next_action
    if unsafe_result:
        # The stable fatal code is the only trustworthy diagnostic from an
        # internally contradictory result.  Do not mix it with otherwise
        # well-shaped counters, state, selection, or model fields supplied by
        # the same unsafe producer.
        return snapshot

    counts: dict[str, dict[str, int]] = {}
    for stage, values in counts_source.items():
        if type(stage) is str and _SUPPORT_ACTION_PATTERN.fullmatch(stage):
            projected = _support_counter(values)
            if projected:
                counts[stage] = projected
    if counts:
        snapshot["counts"] = counts

    summary: dict[str, object] = {}
    for key in _SUPPORT_SUMMARY_KEYS:
        value = status_summary.get(key)
        if type(value) is int and 0 <= value <= 500:
            summary[key] = value
        elif type(value) is str and _SUPPORT_ACTION_PATTERN.fullmatch(value):
            summary[key] = value
    if projected_status_errors:
        summary["errors_by_code"] = projected_status_errors
    if summary:
        snapshot["status"] = summary

    if isinstance(manifest, ScanManifest) and _support_manifest_is_valid(manifest):
        scan_status = manifest.scan_status
        if type(scan_status) is str and _SUPPORT_ACTION_PATTERN.fullmatch(scan_status):
            snapshot["scan_status"] = scan_status
        selection = manifest.selection
        selection_projection = {
            key: selection[key]
            for key in ("requested", "eligible", "screenshots_excluded")
            if type(selection.get(key)) is int and 0 <= selection[key] <= 500
        }
        access = selection.get("access")
        if type(access) is str and _SUPPORT_ACTION_PATTERN.fullmatch(access):
            selection_projection["access"] = access
        if selection_projection:
            snapshot["selection"] = selection_projection
        model_projection = _support_model_projection(manifest.model)
        if model_projection is not None:
            snapshot["model"] = model_projection
    return snapshot


def _emit(emit: EventEmitter, event: ServiceEvent) -> None:
    """Emit only a fixed, metadata-free projection intended for the UI.

    The normal IPC consumer performs an equivalent validation, but service
    callers are also a trust boundary: injected runners can return an
    in-memory ``WorkflowResult`` whose fields were mutated after construction.
    Drop unsafe progress and replace an unsafe terminal with a stable fatal
    event so paths, model text, or raw runner diagnostics never reach a
    callback.
    """
    event_type = event.get("type")
    if type(event_type) is not str or event_type not in {"started", "photo_progress", "completed"}:
        raise ValueError("service event type is invalid")
    allowed: dict[str, set[str]] = {
        "started": {"type", "operation"},
        "photo_progress": {"type", "uuid", "state", "model_used", "model_reason", "keywords_count"},
        "completed": {"type", "exit_code", "manifest", "warning_codes", "error_codes", "safe_instruction", "next_action"},
    }
    if set(event) - allowed[event_type]:
        raise ValueError("service event contains an unsafe field")
    valid = True
    if event_type == "started":
        operation = event.get("operation")
        valid = type(operation) is str and _SERVICE_EVENT_OPERATION_PATTERN.fullmatch(operation) is not None
    elif event_type == "photo_progress":
        uuid_value = event.get("uuid")
        state = event.get("state")
        valid = (
            type(uuid_value) is str
            and _SERVICE_EVENT_UUID_PATTERN.fullmatch(uuid_value) is not None
            and type(state) is str
            and _SERVICE_EVENT_STATE_PATTERN.fullmatch(state) is not None
            and state in _SERVICE_EVENT_PROGRESS_STATES
        )
        if "model_used" in event:
            model_used = event["model_used"]
            valid = valid and (
                type(model_used) is str
                and _SERVICE_EVENT_MODEL_PATTERN.fullmatch(model_used) is not None
                and "cloud" not in model_used.casefold()
            )
            if valid:
                try:
                    validate_ollama_model_name(model_used)
                except Exception:
                    valid = False
        if "model_reason" in event:
            model_reason = event["model_reason"]
            valid = valid and (
                type(model_reason) is str
                and _SERVICE_EVENT_STATE_PATTERN.fullmatch(model_reason) is not None
                and model_reason in MODEL_REASONS
            )
        if "keywords_count" in event:
            keywords_count = event["keywords_count"]
            valid = valid and type(keywords_count) is int and 0 <= keywords_count <= 8
    else:
        exit_code = event.get("exit_code")
        next_action = event.get("next_action")
        valid = (
            type(exit_code) is int
            and exit_code in {0, 1, 2}
            and type(next_action) is str
            and _SERVICE_EVENT_ACTION_PATTERN.fullmatch(next_action) is not None
        )
        if exit_code == 0 and (
            type(next_action) is not str or next_action not in _SUCCESS_COMPLETION_ACTIONS
        ):
            valid = False
        if "manifest" in event:
            manifest = event["manifest"]
            if type(manifest) is not str or len(manifest) > 2048:
                valid = False
            else:
                path = Path(manifest)
                relative_legacy_path = (
                    not path.is_absolute()
                    and len(path.parts) == 3
                    and path.parts[0] == "runs"
                )
                valid = valid and (path.is_absolute() or relative_legacy_path) and path.name == MANIFEST_FILENAME and not any(
                    part in {".", ".."} for part in path.parts
                ) and not any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F or 0xD800 <= ord(character) <= 0xDFFF
                    for character in manifest
                )
        for name in ("warning_codes", "error_codes"):
            if name in event:
                values = event[name]
                valid = valid and type(values) is list and len(values) <= 16 and all(
                    type(value) is str and _SERVICE_EVENT_CODE_PATTERN.fullmatch(value) is not None
                    for value in values
                )
        error_codes = event.get("error_codes")
        if exit_code != 0:
            valid = valid and type(error_codes) is list and bool(error_codes)
        elif error_codes:
            valid = False
        if "safe_instruction" in event:
            instruction = event["safe_instruction"]
            valid = valid and exit_code != 0
            if valid:
                try:
                    validate_ollama_pull_instruction(instruction)
                except Exception:
                    valid = False
    if not valid:
        if event_type == "completed":
            emit({
                "type": "completed",
                "exit_code": 2,
                "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
                "next_action": "fix_fatal_error",
            })
        return
    emit(event)


def _effective_keyword_count(photo: PhotoRecord) -> int:
    """Count only keywords actually added or removed for mutation events."""
    if photo.rollback_state != "not_run":
        return len(photo.rolled_back_keywords)
    if photo.apply_state != "not_run":
        return len(photo.applied_keywords)
    return len(photo.proposed_keywords)


def _progress_model_fields(photo: PhotoRecord, fallback_model: str) -> dict[str, str]:
    """Report a model only when analysis actually ran for this photo."""
    if photo.model_used is None and photo.scan_state == "analysis_failed":
        return {}
    return {
        "model_used": photo.model_used or fallback_model,
        "model_reason": photo.model_reason or "single_policy",
    }


def _emit_photo_progress(emit: EventEmitter, result: WorkflowResult) -> None:
    if result.manifest is None:
        return
    model_name = result.manifest.model.get("name", result.manifest.model.get("fast_name", ""))
    for photo in result.manifest.photos:
        # A failed lookup has no stable Photos UUID.  Do not project an empty
        # identifier: SwiftUI's strict event decoder must be able to consume
        # the completed event and keep the rest of the run reviewable.
        if photo.uuid is None:
            continue
        event: dict[str, object] = {
            "type": "photo_progress",
            "uuid": photo.uuid[:8],
            "state": photo.rollback_state if photo.rollback_state != "not_run" else (
                photo.apply_state if photo.apply_state != "not_run" else photo.scan_state
            ),
            "keywords_count": _effective_keyword_count(photo),
        }
        event.update(_progress_model_fields(photo, model_name))
        _emit(emit, event)


def _progress_manifest_is_safe(manifest: object) -> bool:
    """Ensure a runner manifest cannot crash projection before completion."""
    if manifest is None:
        return True
    if (
        not isinstance(manifest, ScanManifest)
        or not isinstance(manifest.model, Mapping)
        or not isinstance(manifest.photos, list)
    ):
        return False
    for photo in manifest.photos:
        if not isinstance(photo, PhotoRecord) or (photo.uuid is not None and type(photo.uuid) is not str):
            return False
        # ``PhotoRecord`` is mutable so an injected runner can alter one of
        # these collections after construction. Progress takes their length
        # before the event sanitizer runs; reject a malformed row here instead
        # of letting it abort the service without a terminal event.
        for values in (photo.proposed_keywords, photo.applied_keywords, photo.rolled_back_keywords):
            if type(values) is not list or any(type(value) is not str for value in values):
                return False
    return True


def _emit_one_photo_progress(emit: EventEmitter, photo: PhotoRecord, fallback_model: str) -> None:
    """Project one completed photo into the metadata-free UI event shape."""
    if photo.uuid is None:
        return
    event: dict[str, object] = {
        "type": "photo_progress",
        "uuid": photo.uuid[:8],
        "state": photo.rollback_state if photo.rollback_state != "not_run" else (
            photo.apply_state if photo.apply_state != "not_run" else photo.scan_state
        ),
        "keywords_count": _effective_keyword_count(photo),
    }
    event.update(_progress_model_fields(photo, fallback_model))
    _emit(emit, event)


def _runner_accepts_keyword(runner: Callable[..., object], name: str) -> bool:
    """Keep injected runners compatible while enabling live progress."""
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters) or any(
        parameter.name == name and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
        for parameter in parameters
    )


def _emit_completed(emit: EventEmitter, result: WorkflowResult) -> WorkflowResult:
    def project_codes(value: object) -> object:
        # Keep the normal tuple-based WorkflowResult representation compatible
        # with the event contract, while allowing _emit to fail closed on any
        # malformed runner value instead of raising before the terminal event.
        if type(value) in {tuple, list}:
            return list(value)
        return value

    # Validate the terminal discriminator before using it in a set lookup.
    # Runner results can be mutated after construction by an injected helper;
    # an unhashable value must become the same bounded fatal event as every
    # other unsafe terminal field, rather than escaping as ``TypeError``.
    if type(result.exit_code) is not int or result.exit_code not in {0, 1, 2}:
        _emit(emit, {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })
        # Preserve the runner object for direct callers; only the event
        # projection is the fail-closed contract for malformed discriminators.
        return result

    if result.exit_code == 0 and support_snapshot(result).get("exit_code") != 0:
        _emit(emit, {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })
        return _unsafe_workflow_result()

    event: ServiceEvent = {
        "type": "completed",
        "exit_code": result.exit_code,
        "warning_codes": project_codes(result.warning_codes),
        "error_codes": project_codes(result.error_codes),
        "next_action": result.next_action,
    }
    if result.manifest_path is not None:
        # This is a durable user-selected run path, never an export path.
        event["manifest"] = str(result.manifest_path)
    if result.exit_code in {1, 2}:
        try:
            event["safe_instruction"] = validate_ollama_pull_instruction(result.safe_instruction)
        except Exception:
            pass
    _emit(emit, event)
    return result


def _scan_result_is_within_runs_root(manifest_path: Path, runs_root: Path) -> bool:
    """Keep a runner's durable manifest reference scoped to its request.

    The normal workflow creates this file itself, but the service boundary
    also accepts injected runners for the bundled app and tests.  Never let a
    malformed runner result turn into an arbitrary file reference in IPC.
    """
    try:
        path = Path(manifest_path)
        path_text = os.fspath(path)
        root = Path(runs_root)
    except (OSError, TypeError, ValueError):
        return False
    if type(path_text) is not str or len(path_text) > 2048 or any(
        ord(character) < 32
        or 0x7F <= ord(character) <= 0x9F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in path_text
    ):
        return False
    if path.name != MANIFEST_FILENAME or any(part in {".", ".."} for part in path.parts):
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if len(relative.parts) != 2 or relative.parts[0].startswith(".exports-"):
        return False
    # A workflow normally creates a private regular file, but the service
    # boundary may receive a result just after another process replaced it.
    # Reject an existing symlink/hardlink (including ancestor components)
    # before forwarding the durable path to the native UI. Keep the
    # historical injected-runner behavior for paths that do not exist yet.
    current = Path(path.anchor) if path.is_absolute() else Path()
    for component in path.parts:
        if component == path.anchor:
            continue
        current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            # Relative ``runs/...`` references remain a compatibility surface
            # for advanced CLI callers and injected unit runners. The native
            # app, however, always supplies an absolute Application Support
            # root: its terminal event must point at a manifest that actually
            # exists, otherwise SwiftUI can present a successful dry-run whose
            # review artifact can never be opened or validated.
            return not path.is_absolute()
        except OSError:
            return False
        if stat.S_ISLNK(details.st_mode):
            return False
        if current == path:
            return (
                stat.S_ISREG(details.st_mode)
                and details.st_uid == os.getuid()
                and details.st_nlink == 1
                and stat.S_IMODE(details.st_mode) == 0o600
            )
        if not stat.S_ISDIR(details.st_mode):
            return False
    return True


def scan(
    request: ScanRequest,
    emit: EventEmitter,
    *,
    cancellation: CancellationToken | None = None,
    scan_runner: Callable[..., WorkflowResult] = run_scan,
) -> WorkflowResult:
    """Run the existing dry-run workflow and project safe progress events."""
    cancellation = cancellation or CancellationToken()
    if cancellation.is_cancelled():
        result = _cancelled_result()
        result = _emit_completed(emit, result)
        return result
    # The native app supplies an absolute Application Support root.  Keep the
    # historical relative ``runs`` value only for advanced/CLI callers; every
    # other value must pass the same containment check used by IPC before any
    # runner (and therefore Photos/Ollama) is invoked.
    if request.runs_root != Path("runs"):
        try:
            runs_root = validate_app_runs_root(request.runs_root, create=False)
        except (OSError, TypeError, ValueError):
            result = WorkflowResult(exit_code=2, error_codes=("RUNS_ROOT_INVALID",), next_action="fix_fatal_error")
            result = _emit_completed(emit, result)
            return result
    else:
        runs_root = request.runs_root
    try:
        model = request.selected_model()
    except ValueError as error:
        code = "MODEL_INVALID" if str(error) == "model name is invalid" else "MODEL_POLICY_UNSUPPORTED"
        result = WorkflowResult(exit_code=2, error_codes=(code,), next_action="fix_fatal_error")
        result = _emit_completed(emit, result)
        return result
    _emit(emit, {"type": "started", "operation": "scan"})
    progress_seen = False

    def emit_progress(photo: PhotoRecord) -> None:
        nonlocal progress_seen
        progress_seen = True
        _emit_one_photo_progress(emit, photo, model or request.fast_model)

    runner_kwargs: dict[str, object] = {
        "limit": request.limit,
        "random_selection": request.random_selection,
        "model": model,
        "apple_maps": request.apple_maps,
        "model_policy": request.model_policy,
        "fast_model": request.fast_model,
        "detailed_model": request.detailed_model,
        "include_caption": request.include_caption,
        "cancel_requested": cancellation.is_cancelled,
    }
    if _runner_accepts_keyword(scan_runner, "progress_callback"):
        runner_kwargs["progress_callback"] = emit_progress
    try:
        result = scan_runner(runs_root, **runner_kwargs)
    except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
        # Production ``run_scan`` already converts these errors, but keeping
        # the service boundary defensive also protects the native app from a
        # test/helper implementation that raises before returning a result.
        code = error.code
        action = {
            "PHOTOS_ACCESS_DENIED": "grant_photos_access",
            "PHOTOS_AUTOMATION_DENIED": "grant_photos_automation",
        }.get(code, "fix_fatal_error")
        result = WorkflowResult(exit_code=2, error_codes=(code,), next_action=action)
    except Exception:
        # Match the mutation-service boundary: an unexpected runner failure
        # must still produce one sanitized terminal event for the native app.
        result = WorkflowResult(
            exit_code=2,
            error_codes=("WORKER_OPERATION_FAILED",),
            next_action="fix_fatal_error",
        )
    if not isinstance(result, WorkflowResult):
        result = _unsafe_workflow_result()
    if cancellation.is_cancelled():
        result = _cancel_success_result(result)
    if result.manifest_path is not None and not _scan_result_is_within_runs_root(result.manifest_path, runs_root):
        result = WorkflowResult(
            exit_code=2,
            error_codes=("MANIFEST_PATH_INVALID",),
            next_action="fix_fatal_error",
        )
    # Test doubles and third-party callers may still implement the old runner
    # signature. Preserve their final projection while real workflows stream
    # each photo as soon as it finishes.
    if not _progress_manifest_is_safe(result.manifest):
        result = WorkflowResult(
            exit_code=2,
            error_codes=("UNSAFE_WORKFLOW_RESULT",),
            next_action="fix_fatal_error",
        )
    elif not progress_seen:
        _emit_photo_progress(emit, result)
    result = _emit_completed(emit, result)
    return result


def _run_mutation(
    operation: str,
    manifest_path: Path,
    emit: EventEmitter,
    *,
    cancellation: CancellationToken | None,
    runner: Callable[..., WorkflowResult],
) -> WorkflowResult:
    cancellation = cancellation or CancellationToken()
    if cancellation.is_cancelled():
        result = _cancelled_result()
        result = _emit_completed(emit, result)
        return result
    try:
        path = Path(manifest_path)
        path = validate_app_manifest_path(path)
    except (OSError, TypeError, ValueError):
        result = WorkflowResult(exit_code=2, error_codes=("MANIFEST_PATH_INVALID",), next_action="fix_fatal_error")
        result = _emit_completed(emit, result)
        return result
    _emit(emit, {"type": "started", "operation": operation})
    try:
        result = runner(path, cancel_requested=cancellation.is_cancelled)
    except PhotosAccessError:
        result = WorkflowResult(exit_code=2, error_codes=("PHOTOS_ACCESS_DENIED",), next_action="grant_photos_access")
    except PhotoScriptPermissionError:
        result = WorkflowResult(exit_code=2, error_codes=("PHOTOS_AUTOMATION_DENIED",), next_action="grant_photos_automation")
    except PhotoScriptUnavailableError:
        result = WorkflowResult(exit_code=2, error_codes=("PHOTOSCRIPT_UNAVAILABLE",), next_action="fix_fatal_error")
    except Exception:
        # Keep injected/future runner failures behind the same sanitized
        # terminal contract used by the bundled worker.
        result = WorkflowResult(exit_code=2, error_codes=("WORKER_OPERATION_FAILED",), next_action="fix_fatal_error")
    if not isinstance(result, WorkflowResult):
        result = _unsafe_workflow_result()
    if cancellation.is_cancelled():
        result = _cancel_success_result(result)
    try:
        manifest_path_drift = result.manifest_path is not None and Path(result.manifest_path) != path
    except (OSError, TypeError, ValueError):
        # A runner must not be able to smuggle an invalid manifest reference
        # into the terminal event or a later mutation attempt.
        manifest_path_drift = True
    if manifest_path_drift:
        # A mutation workflow may only report the manifest that was validated
        # before it started. Never forward a runner-provided path drift to the
        # native UI or let it become the next mutation target.
        result = WorkflowResult(
            exit_code=2,
            error_codes=("MANIFEST_PATH_INVALID",),
            next_action="fix_fatal_error",
        )
    if not _progress_manifest_is_safe(result.manifest):
        result = WorkflowResult(
            exit_code=2,
            error_codes=("UNSAFE_WORKFLOW_RESULT",),
            next_action="fix_fatal_error",
        )
    else:
        _emit_photo_progress(emit, result)
    result = _emit_completed(emit, result)
    return result


def apply_manifest(
    manifest_path: Path,
    emit: EventEmitter,
    *,
    cancellation: CancellationToken | None = None,
    apply_runner: Callable[..., WorkflowResult] = run_apply,
) -> WorkflowResult:
    """Apply a reviewed manifest through the established Photos workflow."""
    return _run_mutation("apply", manifest_path, emit, cancellation=cancellation, runner=apply_runner)


def rollback_manifest(
    manifest_path: Path,
    emit: EventEmitter,
    *,
    cancellation: CancellationToken | None = None,
    rollback_runner: Callable[..., WorkflowResult] = run_rollback,
) -> WorkflowResult:
    """Rollback only audited keywords through the established Photos workflow."""
    return _run_mutation("rollback", manifest_path, emit, cancellation=cancellation, runner=rollback_runner)


def _private_runs_root(source_manifest: Path) -> Path:
    runs_root = source_manifest.parent.parent
    details = os.lstat(runs_root)
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ManifestError("source runs root is unsafe")
    return runs_root


def _discard_failed_review_run(run_dir: Path) -> None:
    """Remove only a private review directory created by this workflow."""
    try:
        details = run_dir.lstat()
    except FileNotFoundError:
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
        # Preserve the original review failure; a later private cleanup can
        # remove an owned directory whose contents are temporarily busy.
        pass


def _review_model(source: ScanManifest) -> dict[str, str]:
    if source.schema_version == 1:
        return {
            "policy": "single",
            "fast_name": source.model["name"],
            "detailed_name": source.model["name"],
            "ollama_version": source.model["ollama_version"],
            "endpoint": source.model["endpoint"],
        }
    return dict(source.model)


def _reviewed_photo(
    photo: PhotoRecord,
    selected: list[str],
    caption_selected: bool,
    fallback_model: str | None,
) -> PhotoRecord:
    if len(selected) != len(set(selected)):
        raise ManifestError("review selection contains duplicate keywords")
    if photo.errors and (selected or caption_selected):
        raise ManifestError("review selection references a non-actionable photo")
    if photo.uuid is None:
        if selected or caption_selected:
            raise ManifestError("review selection references an unavailable photo")
        return PhotoRecord(
            uuid=photo.uuid, photos_local_identifier=photo.photos_local_identifier, title=photo.title, date=photo.date,
            existing_keywords=list(photo.existing_keywords), proposed_keywords=[], contains_people=photo.contains_people,
            contains_text=photo.contains_text, confidence=photo.confidence, scan_state=photo.scan_state, errors=list(photo.errors),
            proposed_caption=None, caption_state="not_requested",
            technical_trace=photo.technical_trace,
        )
    if photo.scan_state != "ready":
        if selected or (caption_selected and photo.scan_state != "noop"):
            raise ManifestError("review selection references a non-actionable photo")
        proposals: list[str] = []
        scan_state = photo.scan_state
    else:
        selected_counts = Counter(selected)
        proposed_counts = Counter(photo.proposed_keywords)
        if any(selected_counts[value] > proposed_counts[value] for value in selected_counts):
            raise ManifestError("review selection contains an unproposed keyword")
        proposals = [value for value in photo.proposed_keywords if selected_counts[value]]
        scan_state = "ready" if proposals else "noop"
    approved_caption = photo.proposed_caption if caption_selected else None
    routed_fallback = fallback_model if photo.scan_state != "analysis_failed" else None
    return PhotoRecord(
        uuid=photo.uuid, photos_local_identifier=photo.photos_local_identifier, title=photo.title, date=photo.date,
        existing_keywords=list(photo.existing_keywords), proposed_keywords=proposals, contains_people=photo.contains_people,
        contains_text=photo.contains_text, confidence=photo.confidence,
        proposed_caption=approved_caption,
        caption_state="proposed" if approved_caption else "not_requested",
        model_used=photo.model_used or routed_fallback,
        model_reason=photo.model_reason or ("single_policy" if routed_fallback is not None else None),
        scan_state=scan_state, apply_state="not_run", applied_keywords=[], rollback_state="not_run", rolled_back_keywords=[],
        errors=list(photo.errors),
        technical_trace=photo.technical_trace,
    )


def review_manifest(
    manifest_path: Path,
    selections: Mapping[str, list[str]],
    caption_selections: Mapping[str, bool] | None = None,
) -> Path:
    """Create a private reviewed copy without ever changing the source scan.

    The approved selection is intentionally materialized into a new manifest so
    the mutation workflow cannot accidentally apply the broader dry-run.
    """
    try:
        source_path = Path(manifest_path)
    except (OSError, TypeError, ValueError) as error:
        raise ManifestError("review manifest path is invalid") from error
    if source_path.name != MANIFEST_FILENAME or not isinstance(selections, Mapping):
        raise ManifestError("review request is invalid")
    if caption_selections is not None and not isinstance(caption_selections, Mapping):
        raise ManifestError("caption review selection is invalid")
    source = load_manifest(source_path.parent)
    if source.scan_status not in {"ready", "ready_with_errors"} or source.reviewed_from_run_id is not None:
        raise ManifestError("source manifest cannot be reviewed")
    if any(photo.apply_state != "not_run" or photo.rollback_state != "not_run" for photo in source.photos):
        raise ManifestError("mutated manifests cannot be reviewed")
    by_uuid = {photo.uuid: photo for photo in source.photos if photo.uuid is not None}
    normalized: dict[str, list[str]] = {}
    for photo_uuid, values in selections.items():
        if not isinstance(photo_uuid, str) or photo_uuid not in by_uuid:
            raise ManifestError("review selection references an unknown photo")
        if not isinstance(values, list) or any(type(value) is not str for value in values):
            raise ManifestError("review selection must contain keyword lists")
        normalized[photo_uuid] = list(values)
    normalized_captions: dict[str, bool] = {}
    for photo_uuid, selected in (caption_selections or {}).items():
        if not isinstance(photo_uuid, str) or photo_uuid not in by_uuid or type(selected) is not bool:
            raise ManifestError("caption review selection is invalid")
        if selected and not by_uuid[photo_uuid].proposed_caption:
            raise ManifestError("caption review selection has no proposed caption")
        normalized_captions[photo_uuid] = selected

    model = _review_model(source)
    fallback_model = model["fast_name"] if source.schema_version == 1 else None
    photos = [
        _reviewed_photo(
            photo,
            normalized.get(photo.uuid, []),
            normalized_captions.get(photo.uuid, False),
            fallback_model,
        )
        for photo in source.photos
    ]
    summary = {
        "ready": sum(photo.scan_state == "ready" for photo in photos),
        "noop": sum(photo.scan_state == "noop" for photo in photos),
        "analysis_failed": sum(photo.scan_state == "analysis_failed" for photo in photos),
    }
    reviewed = ScanManifest(
        run_id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc), app=dict(source.app), model=model,
        selection=dict(source.selection), photos=photos, summary=summary, run_errors=list(source.run_errors), policy=dict(source.policy),
        scan_status=source.scan_status, schema_version=3, reviewed_from_run_id=source.run_id,
        source_scan_digest=source.scan_digest,
    )
    runs_root = _private_runs_root(source_path)
    timestamp = reviewed.created_at.strftime("%Y%m%dT%H%M%SZ")
    review_dir = runs_root / f"{timestamp}-review-{uuid.UUID(reviewed.run_id).hex[:8]}"
    review_dir.mkdir(mode=0o700)
    try:
        review_dir.chmod(0o700)
        destination = write_manifest(review_dir, reviewed)
        write_preview_csv(review_dir, reviewed)
    except BaseException:
        _discard_failed_review_run(review_dir)
        raise
    return destination


def review_manifest_v4(
    manifest_path: Path,
    approved_keywords: Mapping[str, list[str]],
    approved_captions: Mapping[str, str] | None = None,
) -> Path:
    """Create a schema-4 one-photo review with explicit editable values.

    Manual values remain subject to the same storage/privacy envelope as model
    output. Their origin is derived here so callers cannot forge provenance.
    """
    try:
        source_path = Path(manifest_path)
    except (OSError, TypeError, ValueError) as error:
        raise ManifestError("review manifest path is invalid") from error
    if source_path.name != MANIFEST_FILENAME or not isinstance(approved_keywords, Mapping):
        raise ManifestError("review request is invalid")
    if approved_captions is not None and not isinstance(approved_captions, Mapping):
        raise ManifestError("caption review selection is invalid")
    source = load_manifest(source_path.parent)
    if (
        source.scan_status not in {"ready", "ready_with_errors"}
        or source.reviewed_from_run_id is not None
        or any(photo.apply_state != "not_run" or photo.rollback_state != "not_run" for photo in source.photos)
    ):
        raise ManifestError("source manifest cannot be reviewed")
    by_uuid = {photo.uuid: photo for photo in source.photos if photo.uuid is not None}
    if set(approved_keywords) - set(by_uuid) or set(approved_captions or {}) - set(by_uuid):
        raise ManifestError("review selection references an unknown photo")

    model = _review_model(source)
    fallback_model = model["fast_name"] if source.schema_version == 1 else None
    reviewed_photos: list[PhotoRecord] = []
    for photo in source.photos:
        values = approved_keywords.get(photo.uuid, [])
        if not isinstance(values, list) or any(type(value) is not str for value in values):
            raise ManifestError("review selection must contain keyword lists")
        safe_values = proposed_keywords(photo.existing_keywords, values)
        if len(safe_values) != len(values):
            raise ManifestError("review selection contains an unsafe or duplicate keyword")
        caption = (approved_captions or {}).get(photo.uuid)
        if caption is not None and type(caption) is not str:
            raise ManifestError("caption review selection is invalid")
        manual_analysis_recovery = (
            photo.uuid is not None
            and photo.scan_state == "analysis_failed"
            and bool(safe_values or caption)
            and not photo.proposed_keywords
            and photo.proposed_caption is None
            and bool(photo.errors)
            and all(error["stage"] == "analysis" for error in photo.errors)
        )
        if photo.errors and (safe_values or caption) and not manual_analysis_recovery:
            raise ManifestError("review selection references a non-actionable photo")
        if photo.scan_state == "analysis_failed" and (safe_values or caption) and not manual_analysis_recovery:
            raise ManifestError("review selection references an unavailable photo")

        model_keyword_keys = {
            canonical_keyword_key(value)
            for value in photo.proposed_keywords
        }
        origins = {
            value: ("model" if canonical_keyword_key(value) in model_keyword_keys else "manual")
            for value in safe_values
        }
        if caption is None:
            caption_origin = None
        elif photo.proposed_caption == caption:
            caption_origin = "model"
        else:
            caption_origin = "manual"
        if manual_analysis_recovery:
            scan_state = "ready"
        elif photo.scan_state == "ready":
            scan_state = "ready" if safe_values else "noop"
        else:
            scan_state = photo.scan_state
        routed_model = photo.model_used
        routed_reason = photo.model_reason
        if fallback_model is not None and photo.scan_state != "analysis_failed":
            routed_model = routed_model or fallback_model
            routed_reason = routed_reason or "single_policy"
        reviewed_photos.append(PhotoRecord(
            uuid=photo.uuid,
            photos_local_identifier=photo.photos_local_identifier,
            title=photo.title,
            date=photo.date,
            existing_keywords=list(photo.existing_keywords),
            proposed_keywords=safe_values,
            contains_people=photo.contains_people,
            contains_text=photo.contains_text,
            confidence=photo.confidence,
            model_used=routed_model,
            model_reason=routed_reason,
            scan_state=scan_state,
            errors=list(photo.errors),
            proposed_caption=caption,
            caption_state="proposed" if caption else "not_requested",
            model_proposed_keywords=list(photo.proposed_keywords),
            approved_keywords=safe_values,
            keyword_origins=origins,
            model_proposed_caption=photo.proposed_caption,
            approved_caption=caption,
            caption_origin=caption_origin,
            technical_trace=photo.technical_trace,
        ))

    summary = {
        "ready": sum(photo.scan_state == "ready" for photo in reviewed_photos),
        "noop": sum(photo.scan_state == "noop" for photo in reviewed_photos),
        "analysis_failed": sum(photo.scan_state == "analysis_failed" for photo in reviewed_photos),
    }
    reviewed = ScanManifest(
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        app=dict(source.app),
        model=model,
        selection=dict(source.selection),
        photos=reviewed_photos,
        summary=summary,
        run_errors=list(source.run_errors),
        policy=dict(source.policy),
        scan_status=source.scan_status,
        schema_version=4,
        reviewed_from_run_id=source.run_id,
        source_scan_digest=source.scan_digest,
    )
    runs_root = _private_runs_root(source_path)
    timestamp = reviewed.created_at.strftime("%Y%m%dT%H%M%SZ")
    review_dir = runs_root / f"{timestamp}-review-{uuid.UUID(reviewed.run_id).hex[:8]}"
    review_dir.mkdir(mode=0o700)
    try:
        review_dir.chmod(0o700)
        destination = write_manifest(review_dir, reviewed)
        write_preview_csv(review_dir, reviewed)
    except BaseException:
        _discard_failed_review_run(review_dir)
        raise
    return destination
