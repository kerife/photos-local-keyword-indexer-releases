"""Strict line-delimited JSON protocol for the bundled macOS helper.

There is deliberately no listening socket: the native app owns the helper
process and communicates only through its standard input/output streams.
"""

from __future__ import annotations

import json
import inspect
import os
from queue import Queue
import re
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from typing import TextIO

from .adapters import (
    OllamaEndpointUnavailableError,
    OllamaModelMissingError,
    OllamaModelPolicyError,
    OllamaNoVisionError,
    OllamaVersionTooOldError,
    OllamaVisionClient,
    PhotoScriptBridge,
    PhotoScriptPermissionError,
    PhotoScriptUnavailableError,
    PhotosAccessError,
    validate_ollama_model_name,
    validate_ollama_pull_instruction,
)
from .service import (
    CancellationToken,
    ScanRequest,
    app_runs_root,
    apply_manifest,
    review_manifest,
    rollback_manifest,
    scan,
    _SUCCESS_COMPLETION_ACTIONS,
    validate_app_runs_root,
    validate_app_manifest_path,
)
from .workflows import DEFAULT_DETAILED_MODEL, WorkflowResult
from .manifest import MODEL_REASONS


MAX_LINE_BYTES = 64 * 1024
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_QUEUE_COMMANDS = frozenset({
    "queue_start",
    "queue_resume",
    "queue_update",
    "queue_pause",
    "queue_persist",
    "queue_discard",
    "queue_rescan",
    "queue_stop",
})
_AUTONOMY_COMMANDS = frozenset({"autonomy_start", "autonomy_pause", "autonomy_resume", "autonomy_status"})
_AUTONOMY_COUNTERS = {"revision", "total", "examined", "analyzed", "saved", "no_change", "attention", "remaining", "in_flight", "invalid_count"}
_AUTONOMY_FIELDS = {"type", "campaign_id", "state", "reason"} | _AUTONOMY_COUNTERS
_AUTONOMY_ACTIVITY_FIELDS = {
    "type", "campaign_id", "revision", "position", "state", "photos_local_identifier",
}
_COMMANDS = frozenset({"preflight", "scan", "review", "apply", "rollback", "cancel"}) | _QUEUE_COMMANDS | _AUTONOMY_COMMANDS
# Ollama reports a semantic version.  Do not forward arbitrary loopback-server
# text (for example a path or shell fragment) through the UI event projection.
_PREFLIGHT_VALUE_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
_SAFE_INSTRUCTION_PATTERN = re.compile(r"ollama pull [A-Za-z0-9._:/-]{1,128}")
_ERROR_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,64}")
_PHOTO_PROGRESS_STATES = frozenset({
    "ready", "noop", "analysis_failed", "cancelled",
    "writing", "verified", "failed", "uncertain",
    "removing", "verified_removed", "casing_conflict", "already_absent",
})
_QUEUE_SESSION_STATES = frozenset({"running", "paused", "stopped", "attention"})
_QUEUE_ITEM_STATES = frozenset({
    "discovered",
    "queued",
    "preparing",
    "analyzing",
    "validating",
    "ready",
    "edited",
    "save_queued",
    "saving",
    "verified",
    "discarded",
    "failed",
    "uncertain",
})
_AUTONOMY_ACTIVITY_STATES = frozenset({
    "preparing", "analyzing", "validating", "save_queued", "saving", "settled",
})


def _workflow_code_collection_is_safe(value: object) -> bool:
    """Validate runner code collections before projecting them to JSON."""
    return type(value) in {tuple, list} and len(value) <= 16 and all(
        type(code) is str and _ERROR_CODE_PATTERN.fullmatch(code) is not None
        for code in value
    )


def _manifest_event_path_is_safe(manifest: Path, manifest_root: Path) -> bool:
    """Require an event path to resolve to its private durable manifest.

    Service validation protects workflow results, but handlers can also emit
    progress events directly.  Inspect every component without following
    symlinks and require the private file contract used by manifests.  A
    completed event must not advertise a run that SwiftUI cannot open.
    """
    try:
        relative = manifest.relative_to(manifest_root)
    except ValueError:
        return False
    current = manifest_root
    try:
        root_details = os.lstat(current)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if (
        stat.S_ISLNK(root_details.st_mode)
        or not stat.S_ISDIR(root_details.st_mode)
        or root_details.st_uid != os.getuid()
        or stat.S_IMODE(root_details.st_mode) != 0o700
    ):
        return False
    for component in relative.parts:
        current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        if stat.S_ISLNK(details.st_mode) or details.st_uid != os.getuid():
            return False
        if current == manifest:
            return (
                stat.S_ISREG(details.st_mode)
                and details.st_nlink == 1
                and stat.S_IMODE(details.st_mode) == 0o600
            )
        if not stat.S_ISDIR(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o700:
            return False
    return False


class IPCProtocolError(ValueError):
    """Raised for an invalid or unsafe IPC request."""


class QueueDecisionInvalidError(ValueError):
    """Raised when a queue decision is rejected before write admission."""


@dataclass(frozen=True, slots=True)
class IPCRequest:
    request_id: str
    command: str
    payload: dict[str, object]


def _strict_object(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise IPCProtocolError(f"{label} has an invalid shape")
    return dict(value)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous request objects before last-value-wins decoding."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise IPCProtocolError("request contains duplicate JSON keys")
        value[key] = item
    return value


def _validate_payload(command: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise IPCProtocolError("payload must be an object")
    value = dict(payload)
    if command == "cancel":
        if value:
            raise IPCProtocolError("cancel does not accept payload fields")
        return value
    if command == "preflight":
        if set(value) != {"models"} or not isinstance(value["models"], list) or not value["models"]:
            raise IPCProtocolError("preflight requires a nonempty models list")
        if len(value["models"]) > 4 or any(type(model) is not str for model in value["models"]):
            raise IPCProtocolError("preflight models are invalid")
        try:
            for model in value["models"]:
                validate_ollama_model_name(model)
        except Exception as error:
            raise IPCProtocolError("preflight model is invalid") from error
        return value
    if command == "scan":
        allowed = {"limit", "model_policy", "model", "fast_model", "detailed_model", "random_selection", "apple_maps", "include_caption", "runs_root"}
        if set(value) - allowed:
            raise IPCProtocolError("scan contains unsupported payload fields")
        if "runs_root" not in value:
            raise IPCProtocolError("scan runs_root is required")
        if "limit" in value and (type(value["limit"]) is not int or not 1 <= value["limit"] <= 500):
            raise IPCProtocolError("scan limit is invalid")
        if "model_policy" in value and (
            type(value["model_policy"]) is not str
            or value["model_policy"] not in {"single", "adaptive"}
        ):
            raise IPCProtocolError("scan model policy is invalid")
        for key in ("model", "fast_model", "detailed_model"):
            if key in value and (key != "model" or value[key] is not None):
                if type(value[key]) is not str:
                    raise IPCProtocolError("scan model is invalid")
                try:
                    value[key] = validate_ollama_model_name(value[key])
                except Exception as error:
                    raise IPCProtocolError("scan model is invalid") from error
        if "apple_maps" in value and type(value["apple_maps"]) is not bool:
            raise IPCProtocolError("scan apple_maps is invalid")
        if "include_caption" in value and type(value["include_caption"]) is not bool:
            raise IPCProtocolError("scan include_caption is invalid")
        if "random_selection" in value and type(value["random_selection"]) is not bool:
            raise IPCProtocolError("scan random_selection is invalid")
        if type(value["runs_root"]) is not str or not value["runs_root"]:
            raise IPCProtocolError("scan runs_root is invalid")
        return value
    if command == "review":
        if set(value) not in ({"manifest", "selections"}, {"manifest", "selections", "caption_selections"}) or type(value["manifest"]) is not str or not value["manifest"]:
            raise IPCProtocolError("review requires a manifest path and selections")
        selections = value["selections"]
        if not isinstance(selections, Mapping) or len(selections) > 500:
            raise IPCProtocolError("review selections are invalid")
        normalized: dict[str, list[str]] = {}
        for photo_uuid, keywords in selections.items():
            if type(photo_uuid) is not str:
                raise IPCProtocolError("review selection UUID is invalid")
            try:
                uuid.UUID(photo_uuid)
            except ValueError as error:
                raise IPCProtocolError("review selection UUID is invalid") from error
            if not isinstance(keywords, list) or len(keywords) > 8 or any(type(keyword) is not str for keyword in keywords):
                raise IPCProtocolError("review selection keywords are invalid")
            if len(keywords) != len(set(keywords)):
                raise IPCProtocolError("review selection contains duplicates")
            normalized[photo_uuid] = list(keywords)
        value["selections"] = normalized
        if "caption_selections" in value:
            captions = value["caption_selections"]
            if not isinstance(captions, Mapping) or len(captions) > 500:
                raise IPCProtocolError("review caption selections are invalid")
            normalized_captions: dict[str, bool] = {}
            for photo_uuid, selected in captions.items():
                if type(photo_uuid) is not str or type(selected) is not bool:
                    raise IPCProtocolError("review caption selection is invalid")
                try:
                    uuid.UUID(photo_uuid)
                except ValueError as error:
                    raise IPCProtocolError("review caption selection UUID is invalid") from error
                normalized_captions[photo_uuid] = selected
            value["caption_selections"] = normalized_captions
        return value
    if command in {"apply", "rollback"}:
        if set(value) != {"manifest"} or type(value["manifest"]) is not str or not value["manifest"]:
            raise IPCProtocolError("mutation requires a manifest path")
        return value
    if command in _AUTONOMY_COMMANDS:
        expected = set() if command == "autonomy_status" else {"campaign_id", "decision_id"}
        if command == "autonomy_start":
            expected |= {"runs_root", "settings_path", "limit"}
        if set(value) != expected:
            raise IPCProtocolError("autonomy payload has an invalid shape")
        for field in ("campaign_id", "decision_id"):
            if field in value and (type(value[field]) is not str or _ID_PATTERN.fullmatch(value[field]) is None):
                raise IPCProtocolError("autonomy identity is invalid")
        if command == "autonomy_start":
            limit = value["limit"]
            if limit is not None and (type(limit) is not int or not 1 <= limit <= 2_147_483_647):
                raise IPCProtocolError("autonomy limit is invalid")
            for field in ("runs_root", "settings_path"):
                path = value[field]
                if (type(path) is not str or not path or len(path) > 2048
                    or not Path(path).is_absolute() or any(part in {".", ".."} for part in path.split("/"))
                    or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cs" for char in path)):
                    raise IPCProtocolError("autonomy artifact path is invalid")
        return value
    if command in _QUEUE_COMMANDS:
        common = {"session_id", "revision", "decision_id"}
        if command == "queue_start":
            expected = common | {"runs_root", "settings_path"}
        elif command == "queue_update":
            expected = common | {"settings_path"}
        elif command in {"queue_persist", "queue_discard", "queue_rescan"}:
            expected = common | {"item_id"}
        else:
            expected = common
        if set(value) != expected:
            raise IPCProtocolError("queue payload has an invalid shape")
        for field in ("session_id", "decision_id"):
            if type(value[field]) is not str or _ID_PATTERN.fullmatch(value[field]) is None:
                raise IPCProtocolError("queue identity is invalid")
        if "item_id" in value and (
            type(value["item_id"]) is not str
            or _ID_PATTERN.fullmatch(value["item_id"]) is None
        ):
            raise IPCProtocolError("queue item identity is invalid")
        if (
            type(value["revision"]) is not int
            or not 0 <= value["revision"] <= 2_147_483_647
        ):
            raise IPCProtocolError("queue revision is invalid")
        for field in ("runs_root", "settings_path"):
            if field not in value:
                continue
            path = value[field]
            if (
                type(path) is not str
                or not path
                or len(path) > 2048
                or not Path(path).is_absolute()
                or any(part in {".", ".."} for part in Path(path).parts)
                or any(
                    ord(character) < 32
                    or 0x7F <= ord(character) <= 0x9F
                    or unicodedata.category(character) == "Cs"
                    for character in path
                )
            ):
                raise IPCProtocolError("queue artifact path is invalid")
        return value
    raise IPCProtocolError("command is invalid")


def parse_request_line(line: str) -> IPCRequest:
    if type(line) is not str:
        raise IPCProtocolError("request line is invalid")
    try:
        encoded_line = line.encode("utf-8")
    except UnicodeEncodeError as error:
        raise IPCProtocolError("request line is invalid") from error
    if len(encoded_line) > MAX_LINE_BYTES:
        raise IPCProtocolError("request line is invalid")
    try:
        raw = json.loads(line, object_pairs_hook=_strict_json_object)
    except (TypeError, json.JSONDecodeError, RecursionError, IPCProtocolError) as error:
        raise IPCProtocolError("request is not valid JSON") from error
    value = _strict_object(raw, {"id", "command", "payload"}, "request")
    request_id = value["id"]
    command = value["command"]
    if type(request_id) is not str or _ID_PATTERN.fullmatch(request_id) is None:
        raise IPCProtocolError("request id is invalid")
    if type(command) is not str or command not in _COMMANDS:
        raise IPCProtocolError("command is invalid")
    return IPCRequest(request_id, command, _validate_payload(command, value["payload"]))


def _json_line(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _safe_service_event(
    event: Mapping[str, object],
    *,
    manifest_root: Path | None = None,
) -> dict[str, object]:
    if not isinstance(event, Mapping):
        raise IPCProtocolError("service emitted an unsafe event")
    event_type = event.get("type")
    allowed: dict[object, set[str]] = {
        "autonomy_campaign": _AUTONOMY_FIELDS,
        "autonomy_activity": _AUTONOMY_ACTIVITY_FIELDS,
        "started": {"type", "operation"},
        "photo_progress": {"type", "uuid", "state", "model_used", "model_reason", "keywords_count"},
        "completed": {"type", "exit_code", "manifest", "warning_codes", "error_codes", "safe_instruction", "next_action"},
        "queue_session": {
            "type", "session_id", "revision", "state", "queued", "analyzing",
            "ready", "save_queued", "saving", "saved", "attention",
        },
        "queue_item": {
            "type", "session_id", "item_id", "revision", "state", "decision_id", "manifest",
        },
    }
    required = {
        "autonomy_campaign": _AUTONOMY_FIELDS,
        "autonomy_activity": _AUTONOMY_ACTIVITY_FIELDS,
        "started": {"type", "operation"},
        "photo_progress": {"type", "uuid", "state"},
        "completed": {"type", "exit_code", "next_action"},
        "queue_session": {"type", "session_id", "revision", "state"},
        "queue_item": {"type", "session_id", "item_id", "revision", "state"},
    }
    if type(event_type) is not str or event_type not in allowed or set(event) - allowed[event_type] or not required[event_type].issubset(event):
        raise IPCProtocolError("service emitted an unsafe event")
    def bounded_string(name: str, maximum: int, *, pattern: re.Pattern[str] | None = None, allow_empty: bool = False) -> None:
        if name not in event:
            return
        value = event[name]
        if type(value) is not str or (not allow_empty and not value) or len(value) > maximum:
            raise IPCProtocolError("service emitted an invalid event value")
        if any(
            ord(character) < 32
            or 0x7F <= ord(character) <= 0x9F
            or unicodedata.category(character) == "Cs"
            for character in value
        ):
            raise IPCProtocolError("service emitted an invalid event value")
        if pattern is not None and pattern.fullmatch(value) is None:
            raise IPCProtocolError("service emitted an invalid event value")

    if event_type == "started":
        bounded_string("operation", 16, pattern=re.compile(r"[a-z_]+"))
    elif event_type == "photo_progress":
        bounded_string("uuid", 8, pattern=re.compile(r"[A-Za-z0-9_-]{1,8}"))
        bounded_string("state", 32, pattern=re.compile(r"[a-z_]+"))
        if event["state"] not in _PHOTO_PROGRESS_STATES:
            raise IPCProtocolError("service emitted an invalid event value")
        bounded_string("model_used", 128, pattern=re.compile(r"[A-Za-z0-9._:/-]+"))
        if "model_used" in event:
            try:
                validate_ollama_model_name(event["model_used"])
                if "cloud" in event["model_used"].casefold():
                    raise ValueError("cloud models are not permitted")
            except Exception as error:
                raise IPCProtocolError("service emitted an invalid event value") from error
        bounded_string("model_reason", 32, pattern=re.compile(r"[a-z_]+"))
        if "model_reason" in event and event["model_reason"] not in MODEL_REASONS:
            raise IPCProtocolError("service emitted an invalid event value")
        if "keywords_count" in event and (type(event["keywords_count"]) is not int or not 0 <= event["keywords_count"] <= 8):
            raise IPCProtocolError("service emitted an invalid event value")
    elif event_type == "completed":
        if type(event.get("exit_code")) is not int or event["exit_code"] not in {0, 1, 2}:
            raise IPCProtocolError("service emitted an invalid event value")
        bounded_string("manifest", 2048)
        if "manifest" in event:
            manifest = Path(str(event["manifest"]))
            # The native client may open this reference.  Keep it to a single
            # manifest filename and reject traversal before it reaches SwiftUI.
            if (
                not manifest.is_absolute()
                or manifest.name != "manifest.json"
                or any(part in {".", ".."} for part in manifest.parts)
                or any(part.startswith(".exports-") for part in manifest.parts)
            ):
                raise IPCProtocolError("service emitted an invalid event value")
            if manifest_root is not None:
                try:
                    relative = manifest.relative_to(manifest_root)
                except ValueError as error:
                    raise IPCProtocolError("service emitted an invalid event value") from error
                if len(relative.parts) != 2 or relative.parts[-1] != "manifest.json" or not _manifest_event_path_is_safe(manifest, manifest_root):
                    raise IPCProtocolError("service emitted an invalid event value")
        for name in ("warning_codes", "error_codes"):
            if name in event:
                values = event[name]
                if type(values) is not list or len(values) > 16 or any(
                    type(value) is not str or re.fullmatch(r"[A-Z0-9_]{1,64}", value) is None for value in values
                ):
                    raise IPCProtocolError("service emitted an invalid event value")
        if event["exit_code"] != 0 and (
            type(event.get("error_codes")) is not list or not event["error_codes"]
        ):
            raise IPCProtocolError("nonzero completion requires error codes")
        if event["exit_code"] == 0 and event.get("error_codes"):
            raise IPCProtocolError("successful completion cannot carry error codes")
        if event["exit_code"] == 0 and (
            type(event["next_action"]) is not str
            or event["next_action"] not in _SUCCESS_COMPLETION_ACTIONS
        ):
            raise IPCProtocolError("successful completion cannot request failure recovery")
        if "safe_instruction" in event:
            if event["exit_code"] == 0:
                raise IPCProtocolError("successful completion cannot carry safe instruction")
            try:
                validate_ollama_pull_instruction(event["safe_instruction"])
            except Exception as error:
                raise IPCProtocolError("service emitted an invalid event value") from error
        bounded_string("next_action", 64, pattern=re.compile(r"[a-z_]+"))
    elif event_type == "autonomy_campaign":
        bounded_string("campaign_id", 64, pattern=_ID_PATTERN)
        if type(event["state"]) is not str or type(event["reason"]) is not str or event["state"] not in {"preparing", "running", "pausing", "paused", "completed"} or event["reason"] not in {"none", "manual_drain", "snapshot", "user_pause", "recovered", "permission", "storage"}:
            raise IPCProtocolError("service emitted an invalid campaign state")
        for name in _AUTONOMY_COUNTERS:
            if type(event[name]) is not int or not 0 <= event[name] <= 2_147_483_647:
                raise IPCProtocolError("service emitted an invalid campaign counter")
    elif event_type == "autonomy_activity":
        bounded_string("campaign_id", 64, pattern=_ID_PATTERN)
        if type(event["revision"]) is not int or not 0 <= event["revision"] <= 2_147_483_647:
            raise IPCProtocolError("service emitted an invalid activity revision")
        if type(event["position"]) is not int or not 0 <= event["position"] <= 2_147_483_647:
            raise IPCProtocolError("service emitted an invalid activity position")
        if type(event["state"]) is not str or event["state"] not in _AUTONOMY_ACTIVITY_STATES:
            raise IPCProtocolError("service emitted an invalid activity state")
        bounded_string("photos_local_identifier", 1_024, allow_empty=False)
    elif event_type == "queue_session":
        bounded_string("session_id", 64, pattern=_ID_PATTERN)
        bounded_string("state", 32, pattern=re.compile(r"[a-z_]+"))
        if event["state"] not in _QUEUE_SESSION_STATES:
            raise IPCProtocolError("service emitted an invalid event value")
        if type(event["revision"]) is not int or not 0 <= event["revision"] <= 2_147_483_647:
            raise IPCProtocolError("service emitted an invalid event value")
        for name in ("queued", "analyzing", "ready", "save_queued", "saving", "saved", "attention"):
            if name in event and (
                type(event[name]) is not int or not 0 <= event[name] <= 2_147_483_647
            ):
                raise IPCProtocolError("service emitted an invalid event value")
    elif event_type == "queue_item":
        bounded_string("session_id", 64, pattern=_ID_PATTERN)
        bounded_string("item_id", 64, pattern=_ID_PATTERN)
        bounded_string("decision_id", 64, pattern=_ID_PATTERN)
        bounded_string("state", 32, pattern=re.compile(r"[a-z_]+"))
        if event["state"] not in _QUEUE_ITEM_STATES:
            raise IPCProtocolError("service emitted an invalid event value")
        if type(event["revision"]) is not int or not 0 <= event["revision"] <= 2_147_483_647:
            raise IPCProtocolError("service emitted an invalid event value")
        bounded_string("manifest", 2048)
        if "manifest" in event:
            manifest = Path(str(event["manifest"]))
            if (
                not manifest.is_absolute()
                or manifest.name != "manifest.json"
                or any(part in {".", ".."} for part in manifest.parts)
                or any(part.startswith(".exports-") for part in manifest.parts)
            ):
                raise IPCProtocolError("service emitted an invalid event value")
            if manifest_root is not None and not _manifest_event_path_is_safe(manifest, manifest_root):
                raise IPCProtocolError("service emitted an invalid event value")
    result = dict(event)
    result["event"] = str(result.pop("type"))
    return result


def _safe_preflight_details(details: Mapping[str, object]) -> dict[str, object]:
    """Project loopback preflight output to the small public IPC contract.

    Ollama is local, but the process listening on the loopback port is still an
    input boundary.  In particular, never forward a model-server version or a
    suggested command containing controls, paths, URLs, or shell operators to
    the native UI.
    """
    if not isinstance(details, Mapping):
        raise IPCProtocolError("preflight details are not an object")
    allowed = {"models", "error_codes", "safe_instruction", "next_action"}
    if set(details) - allowed:
        raise IPCProtocolError("preflight details contain unsupported fields")
    result: dict[str, object] = {}
    if "models" in details:
        models = details["models"]
        # An endpoint/version failure can happen before any model response is
        # available.  Preserve the bounded empty diagnostic instead of
        # replacing the useful ``OLLAMA_UNAVAILABLE`` result with a generic
        # unsafe-projection error.
        if not isinstance(models, Mapping) or len(models) > 4:
            raise IPCProtocolError("preflight models are invalid")
        projected: dict[str, str] = {}
        for model, version in models.items():
            if type(model) is not str:
                raise IPCProtocolError("preflight model name is invalid")
            try:
                validate_ollama_model_name(model)
                if "cloud" in model.casefold():
                    raise ValueError("cloud models are not permitted")
            except Exception as error:
                raise IPCProtocolError("preflight model name is invalid") from error
            if (
                type(version) is not str
                or len(version) > 64
                or _PREFLIGHT_VALUE_PATTERN.fullmatch(version) is None
            ):
                raise IPCProtocolError("preflight model version is invalid")
            projected[model] = version
        if projected:
            result["models"] = projected
    if "error_codes" in details:
        codes = details["error_codes"]
        if type(codes) is not list or len(codes) > 16 or any(
            type(code) is not str or _ERROR_CODE_PATTERN.fullmatch(code) is None for code in codes
        ):
            raise IPCProtocolError("preflight error codes are invalid")
        result["error_codes"] = list(codes)
    if "safe_instruction" in details:
        instruction = details["safe_instruction"]
        if type(instruction) is not str or _SAFE_INSTRUCTION_PATTERN.fullmatch(instruction) is None:
            raise IPCProtocolError("preflight instruction is invalid")
        try:
            instruction = validate_ollama_pull_instruction(instruction)
        except Exception as error:
            raise IPCProtocolError("preflight instruction is invalid") from error
        result["safe_instruction"] = instruction
    if "next_action" in details:
        next_action = details["next_action"]
        if type(next_action) is not str or not 1 <= len(next_action) <= 64 or re.fullmatch(r"[a-z_]+", next_action) is None:
            raise IPCProtocolError("preflight next action is invalid")
        result["next_action"] = next_action
    return result


def _photoscript_preflight() -> None:
    """Run the compile-only PhotoScript check without sending Apple Events."""
    PhotoScriptBridge.preflight_compatibility()


def _default_preflight(payload: dict[str, object], token: CancellationToken) -> tuple[int, dict[str, object]]:
    if token.is_cancelled():
        return 1, {"error_codes": ["CANCELLED"], "next_action": "none"}
    client = OllamaVisionClient()
    versions: dict[str, str] = {}
    missing: OllamaModelMissingError | None = None
    # Adaptive configuration may use the same model for both roles. Check a
    # local model once while preserving the caller's order in diagnostics.
    models = list(dict.fromkeys(payload["models"]))
    for model in models:
        if token.is_cancelled():
            return 1, {"error_codes": ["CANCELLED"], "next_action": "none"}
        assert isinstance(model, str)
        try:
            versions[model] = str(client.check_model(model))
        except OllamaModelMissingError as error:
            # Keep successful model versions in the diagnostic response. This
            # is especially useful for adaptive scans where only the detailed
            # model is absent; it also lets the UI show exactly what remains.
            missing = missing or error
        except OllamaEndpointUnavailableError:
            return 2, {"models": versions, "error_codes": ["OLLAMA_UNAVAILABLE"], "next_action": "retry_preflight"}
        except OllamaVersionTooOldError:
            return 2, {"models": versions, "error_codes": ["OLLAMA_VERSION_OLD"], "next_action": "retry_preflight"}
        except OllamaNoVisionError:
            return 2, {"models": versions, "error_codes": ["OLLAMA_NO_VISION"], "next_action": "retry_preflight"}
        except OllamaModelPolicyError:
            return 2, {"models": versions, "error_codes": ["MODEL_INVALID"], "next_action": "fix_fatal_error"}
        except Exception:
            return 2, {"models": versions, "error_codes": ["OLLAMA_PREFLIGHT_FAILED"], "next_action": "retry_preflight"}
    if token.is_cancelled():
        return 1, {"error_codes": ["CANCELLED"], "next_action": "none"}
    if missing is not None:
        return 2, {
            "models": versions,
            "error_codes": ["OLLAMA_MODEL_MISSING"],
            "safe_instruction": missing.pull_command,
            "next_action": "fix_fatal_error",
        }
    try:
        _photoscript_preflight()
    except PhotoScriptPermissionError:
        return 2, {"models": versions, "error_codes": ["PHOTOS_AUTOMATION_DENIED"], "next_action": "grant_photos_automation"}
    except PhotoScriptUnavailableError:
        return 2, {"models": versions, "error_codes": ["PHOTOSCRIPT_UNAVAILABLE"], "next_action": "fix_fatal_error"}
    except Exception:
        return 2, {"models": versions, "error_codes": ["PHOTOSCRIPT_UNAVAILABLE"], "next_action": "fix_fatal_error"}
    return 0, {"models": versions, "next_action": "none"}


class _WorkerServer:
    def __init__(
        self,
        output: TextIO,
        *,
        scan_handler: Callable[[dict[str, object], Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None,
        apply_handler: Callable[[Path, Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None,
        rollback_handler: Callable[[Path, Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None,
        review_handler: Callable[..., Path] | None,
        preflight_handler: Callable[[dict[str, object], CancellationToken], tuple[int, dict[str, object]]] | None,
        queue_handler: Callable[[IPCRequest, Callable[[dict[str, object]], None]], None] | None = None,
        autonomy_runtime: object | None = None,
    ) -> None:
        self.output = output
        self._output_queue: Queue[str | None] = Queue()
        self._output_thread = Thread(target=self._drain_output, name="photos-indexer-ipc-writer", daemon=True)
        self._output_thread.start()
        self._state_lock = Lock()
        self._active: tuple[str, CancellationToken, Thread] | None = None
        self._active_command: str | None = None
        self._active_completed = False
        self._queue_session_id: str | None = None
        self._queue_runs_root: Path | None = None
        self._queue_threads: set[Thread] = set()
        self.scan_handler = scan_handler or self._scan
        self.apply_handler = apply_handler or self._apply
        self.rollback_handler = rollback_handler or self._rollback
        self.review_handler = review_handler or review_manifest
        self.preflight_handler = preflight_handler or _default_preflight
        self.queue_handler = queue_handler
        self.autonomy_runtime = autonomy_runtime
        self._autonomy_owner: str | None = None
        self._autonomy_generation: Event | None = None
        self._autonomy_threads: set[Thread] = set()
        self._closing = False

    @staticmethod
    def _scan(payload: dict[str, object], emit: Callable[[dict[str, object]], None], token: CancellationToken) -> WorkflowResult:
        return scan(ScanRequest(
            runs_root=Path(payload.get("runs_root", "runs")),
            limit=int(payload.get("limit", 20)),
            model_policy=str(payload.get("model_policy", "single")),
            model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            fast_model=str(payload.get("fast_model", "qwen3-vl:4b")),
            detailed_model=str(payload.get("detailed_model", DEFAULT_DETAILED_MODEL)),
            random_selection=bool(payload.get("random_selection", False)),
            apple_maps=bool(payload.get("apple_maps", False)),
            include_caption=bool(payload.get("include_caption", False)),
        ), emit, cancellation=token)

    @staticmethod
    def _apply(path: Path, emit: Callable[[dict[str, object]], None], token: CancellationToken) -> WorkflowResult:
        return apply_manifest(path, emit, cancellation=token)

    @staticmethod
    def _rollback(path: Path, emit: Callable[[dict[str, object]], None], token: CancellationToken) -> WorkflowResult:
        return rollback_manifest(path, emit, cancellation=token)

    def _send(self, request_id: str, event: Mapping[str, object]) -> None:
        # Workers enqueue protocol lines instead of writing synchronously. A
        # blocked stdout consumer must not prevent a terminal worker state from
        # releasing the single-operation gate or allow a retry to deadlock.
        self._output_queue.put(_json_line({"id": request_id, **event}))

    def _drain_output(self) -> None:
        output_failed = False
        while True:
            line = self._output_queue.get()
            try:
                if line is None:
                    return
                if not output_failed:
                    try:
                        self.output.write(line)
                        self.output.flush()
                    except Exception:
                        # A closed or failed stdout cannot be repaired here, and
                        # retrying could duplicate a terminal event. Discard the
                        # remaining queued lines so EOF shutdown can still finish.
                        output_failed = True
            finally:
                self._output_queue.task_done()

    def _error(self, request_id: str, code: str) -> None:
        self._send(request_id, {"event": "error", "code": code})

    def _mark_active_completed(self, request_id: str) -> None:
        with self._state_lock:
            if self._active is not None and self._active[0] == request_id:
                self._active_completed = True

    def _send_safe_workflow_completion(
        self,
        request_id: str,
        result: WorkflowResult,
        *,
        cancellation_requested: bool = False,
        manifest_root: Path | None = None,
    ) -> None:
        # Validate before using the terminal code in set membership below.
        # Injected runners are an untrusted boundary; a list/dict exit code
        # must become the same stable unsafe-result diagnostic as other
        # malformed completion fields, not escape to the generic worker error.
        if type(result.exit_code) is not int or result.exit_code not in {0, 1, 2}:
            self._error(request_id, "UNSAFE_WORKFLOW_RESULT")
            return
        if not _workflow_code_collection_is_safe(result.warning_codes) or not _workflow_code_collection_is_safe(result.error_codes):
            self._error(request_id, "UNSAFE_WORKFLOW_RESULT")
            return
        if result.exit_code == 0 and (result.error_codes or result.safe_instruction is not None):
            # Do not let cancellation normalization hide a contradictory
            # successful result.  The direct service boundary rejects this
            # shape too; IPC must preserve the same fail-closed contract.
            self._error(request_id, "UNSAFE_WORKFLOW_RESULT")
            return
        completion: dict[str, object] = {
            "type": "completed",
            "exit_code": result.exit_code,
            "next_action": result.next_action,
        }
        if cancellation_requested and result.exit_code == 0:
            # Keep a recovery action computed by a completed mutation (for
            # example rollback_available) visible even when cancellation was
            # observed at the terminal boundary.
            completion.update(exit_code=1, error_codes=["CANCELLED"], next_action=result.next_action)
            if result.warning_codes:
                completion["warning_codes"] = list(result.warning_codes)
        elif cancellation_requested and result.exit_code in {1, 2} and result.error_codes != ("UNSAFE_WORKFLOW_RESULT",):
            # Validate before de-duplicating with a set. A malformed runner
            # result must not let an unhashable error code escape to the
            # worker's generic exception handler.
            error_codes = tuple(sorted(set(result.error_codes) | {"CANCELLED"}))
            completion["error_codes"] = list(error_codes)
            if result.warning_codes:
                completion["warning_codes"] = list(result.warning_codes)
        else:
            # Preserve stable terminal diagnostics when a handler returns a
            # result without emitting its own completion event.  This is the
            # path used during EOF/cancellation recovery and must not turn a
            # failed operation into an unexplained exit code.
            if result.warning_codes:
                completion["warning_codes"] = list(result.warning_codes)
            if result.error_codes:
                completion["error_codes"] = list(result.error_codes)
        if completion["exit_code"] in {1, 2} and result.safe_instruction is not None:
            completion["safe_instruction"] = result.safe_instruction
        try:
            event = _safe_service_event(completion, manifest_root=manifest_root)
        except IPCProtocolError:
            self._error(request_id, "UNSAFE_WORKFLOW_RESULT")
            return
        self._mark_active_completed(request_id)
        self._send(request_id, event)

    def cancel(self, request: IPCRequest) -> None:
        with self._state_lock:
            active = self._active
            active_completed = self._active_completed
            if active is None or active_completed:
                self._send(request.request_id, {"event": "completed", "exit_code": 0, "next_action": "none"})
                return
            active[1].cancel()
        self._send(request.request_id, {"event": "completed", "exit_code": 0, "next_action": "cancellation_requested"})

    def start(self, request: IPCRequest) -> None:
        if request.command in _AUTONOMY_COMMANDS:
            self._start_autonomy_command(request)
            return
        if request.command in _QUEUE_COMMANDS:
            self._start_queue_command(request)
            return
        with self._state_lock:
            if self._autonomy_owner is not None or self._closing:
                self._error(request.request_id, "AUTONOMY_ACTIVE")
                return
            if self._active is not None or self._queue_session_id is not None:
                self._error(request.request_id, "BUSY")
                return
        if request.command == "scan" and "runs_root" in request.payload:
            try:
                runs_root = validate_app_runs_root(str(request.payload["runs_root"]), create=False)
            except Exception:
                self._error(request.request_id, "RUNS_ROOT_INVALID")
                return
            request = IPCRequest(request.request_id, request.command, {**request.payload, "runs_root": str(runs_root)})
        elif request.command in {"review", "apply", "rollback"}:
            try:
                manifest = validate_app_manifest_path(str(request.payload["manifest"]))
            except Exception:
                self._error(request.request_id, "MANIFEST_PATH_INVALID")
                return
            request = IPCRequest(request.request_id, request.command, {**request.payload, "manifest": str(manifest)})
        with self._state_lock:
            if self._autonomy_owner is not None or self._closing:
                self._error(request.request_id, "AUTONOMY_ACTIVE")
                return
            if self._active is not None:
                self._error(request.request_id, "BUSY")
                return
            token = CancellationToken()
            thread = Thread(target=self._run, args=(request, token), daemon=True)
            self._active = (request.request_id, token, thread)
            self._active_command = request.command
            self._active_completed = False
            thread.start()

    def _start_queue_command(self, request: IPCRequest) -> None:
        if self.queue_handler is None:
            self._error(request.request_id, "QUEUE_UNAVAILABLE")
            return
        session_id = str(request.payload["session_id"])
        activates_queue = False
        with self._state_lock:
            if self._autonomy_owner is not None or self._closing:
                self._error(request.request_id, "AUTONOMY_ACTIVE")
                return
            if request.command == "queue_start":
                if self._active is not None or self._queue_session_id is not None:
                    self._error(request.request_id, "BUSY")
                    return
                try:
                    runs_root = validate_app_runs_root(str(request.payload["runs_root"]), create=False)
                except Exception:
                    self._error(request.request_id, "RUNS_ROOT_INVALID")
                    return
                self._queue_session_id = session_id
                self._queue_runs_root = runs_root
                request = IPCRequest(
                    request.request_id,
                    request.command,
                    {**request.payload, "runs_root": str(runs_root)},
                )
                activates_queue = True
            elif request.command == "queue_resume" and self._queue_session_id is None:
                if self._active is not None:
                    self._error(request.request_id, "BUSY")
                    return
                try:
                    runs_root = validate_app_runs_root(app_runs_root(), create=False)
                except Exception:
                    self._error(request.request_id, "RUNS_ROOT_INVALID")
                    return
                self._queue_session_id = session_id
                self._queue_runs_root = runs_root
                activates_queue = True
            elif self._queue_session_id != session_id:
                self._error(request.request_id, "QUEUE_NOT_RUNNING")
                return
            thread = Thread(
                target=self._run_queue_command,
                args=(request, activates_queue),
                daemon=True,
            )
            self._queue_threads.add(thread)
            thread.start()

    def _manual_runtime_hook(self, name: str) -> None:
        runtime = getattr(self.queue_handler, "__self__", None)
        hook = getattr(runtime, name, None)
        if callable(hook):
            hook()

    def _start_autonomy_command(self, request: IPCRequest) -> None:
        if self.autonomy_runtime is None:
            self._error(request.request_id, "AUTONOMY_UNAVAILABLE")
            return
        if request.command == "autonomy_start":
            try:
                root = validate_app_runs_root(str(request.payload["runs_root"]), create=False)
                if Path(str(request.payload["settings_path"])) != root.parent / "settings.json":
                    raise IPCProtocolError("settings path is invalid")
            except Exception:
                self._error(request.request_id, "RUNS_ROOT_INVALID")
                return
        with self._state_lock:
            if self._closing:
                self._error(request.request_id, "AUTONOMY_ACTIVE")
                return
            generation = None
            pause_admitted = None
            accepted = ()
            activation = request.command in {"autonomy_start", "autonomy_resume"}
            if activation and self._autonomy_owner is None:
                generation = Event()
                self._autonomy_owner = str(request.payload["campaign_id"])
                self._autonomy_generation = generation
                accepted = tuple(self._queue_threads) + ((self._active[2],) if self._active else ())
            elif activation and self._autonomy_owner != request.payload["campaign_id"]:
                self._error(request.request_id, "AUTONOMY_ACTIVE")
                return
            elif request.command == "autonomy_pause":
                if self._autonomy_owner is not None and self._autonomy_owner != request.payload["campaign_id"]:
                    self._error(request.request_id, "AUTONOMY_ACTIVE")
                    return
                try:
                    pause_admitted = self.autonomy_runtime.admit_pause(
                        request, pending_campaign_id=self._autonomy_owner,
                        pending_generation=self._autonomy_generation,
                    )
                except Exception:
                    self._error(request.request_id, "AUTONOMY_OPERATION_FAILED")
                    return
                if pause_admitted:
                    if self._autonomy_generation:
                        self._autonomy_generation.set()
                    accepted = tuple(self._autonomy_threads)
            thread = Thread(target=self._run_autonomy_command, args=(request, generation, accepted, pause_admitted), daemon=True)
            self._autonomy_threads.add(thread)
            thread.start()

    def _run_autonomy_command(self, request: IPCRequest, generation: Event | None, accepted: tuple[Thread, ...], pause_admitted: bool | None = None) -> None:
        def emit(event: dict[str, object]) -> None:
            self._send(request.request_id, _safe_service_event(event))

        def released() -> None:
            if generation is not None:
                with self._state_lock:
                    if self._autonomy_generation is generation:
                        self._autonomy_owner = None
                        self._autonomy_generation = None

        def drain() -> None:
            self._manual_runtime_hook("pause_for_handoff")
            for thread in accepted:
                while thread.is_alive():
                    thread.join(.05)
                    if generation is not None and generation.is_set():
                        return
            # QueueCoordinator.wait() can legitimately outlive its short IPC
            # command thread while an already-admitted manual save or analysis
            # is settling.  Keep that safety drain, but do not make a later
            # autonomous pause wait for it: activation is already revoked and
            # the autonomous runtime will not start after this returns.
            finished = Event()
            failures: list[BaseException] = []

            def drain_manual_runtime() -> None:
                try:
                    self._manual_runtime_hook("drain_for_handoff")
                except BaseException as error:
                    failures.append(error)
                finally:
                    finished.set()

            Thread(target=drain_manual_runtime, daemon=True).start()
            while not finished.wait(.05):
                if generation is not None and generation.is_set():
                    return
            if failures:
                raise failures[0]

        background = False
        try:
            from .autonomous_dispatcher import AutonomousActiveError
            if request.command == "autonomy_pause":
                for thread in accepted:
                    thread.join()
            background = self.autonomy_runtime.handle(request, emit, generation=generation, drain=drain, released=released, pause_admitted=pause_admitted)
            self._send(request.request_id, {"event": "completed", "exit_code": 0, "next_action": "none"})
        except AutonomousActiveError:
            self._error(request.request_id, "AUTONOMY_ACTIVE")
        except (PhotosAccessError, PhotoScriptPermissionError) as error:
            self._error(request.request_id, "PHOTOS_ACCESS_DENIED" if isinstance(error, PhotosAccessError) else "PHOTOS_AUTOMATION_DENIED")
        except Exception:
            self._error(request.request_id, "AUTONOMY_OPERATION_FAILED")
        finally:
            if not background:
                released()
            with self._state_lock:
                self._autonomy_threads.discard(current_thread())

    def _run_queue_command(self, request: IPCRequest, activates_queue: bool = False) -> None:
        def emit(event: dict[str, object]) -> None:
            try:
                safe_event = _safe_service_event(event, manifest_root=self._queue_runs_root)
            except IPCProtocolError:
                self._error(request.request_id, "UNSAFE_QUEUE_RESULT")
                return
            self._send(request.request_id, safe_event)

        succeeded = False
        try:
            assert self.queue_handler is not None
            self.queue_handler(request, emit)
            self._send(
                request.request_id,
                {"event": "completed", "exit_code": 0, "next_action": "none"},
            )
            succeeded = True
        except QueueDecisionInvalidError:
            self._error(request.request_id, "QUEUE_DECISION_INVALID")
        except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
            # Queue discovery/preparation can fail before a per-photo
            # manifest exists. Preserve the stable platform diagnosis so the
            # native client can offer the correct recovery instead of hiding
            # it behind QUEUE_OPERATION_FAILED.
            if isinstance(error, PhotosAccessError):
                code = "PHOTOS_ACCESS_DENIED"
            elif isinstance(error, PhotoScriptPermissionError):
                code = "PHOTOS_AUTOMATION_DENIED"
            else:
                code = "PHOTOSCRIPT_UNAVAILABLE"
            self._error(request.request_id, code)
        except Exception:
            self._error(request.request_id, "QUEUE_OPERATION_FAILED")
        finally:
            with self._state_lock:
                if request.command == "queue_stop" or (activates_queue and not succeeded):
                    self._queue_session_id = None
                    self._queue_runs_root = None
                self._queue_threads.discard(current_thread())

    def _run(self, request: IPCRequest, token: CancellationToken) -> None:
        emitted_completed = False
        if request.command == "scan":
            manifest_root = Path(str(request.payload["runs_root"]))
        elif request.command in {"apply", "rollback"}:
            manifest_root = Path(str(request.payload["manifest"])).parent.parent
        else:
            manifest_root = None

        def emit(event: dict[str, object]) -> None:
            nonlocal emitted_completed
            # Handlers are an internal extension boundary.  Do not access a
            # mapping method before the central projection has validated the
            # shape, otherwise ``emit(None)`` is downgraded to a generic
            # worker exception instead of the stable unsafe-result terminal.
            event_type = event.get("type") if isinstance(event, Mapping) else None
            if emitted_completed:
                return
            if event_type == "completed" and token.is_cancelled():
                exit_code = event.get("exit_code")
                # ``bool`` is an ``int`` subclass in Python, but it is not a
                # valid protocol exit code.  Only normalize strict integers;
                # malformed values must reach the central validator.
                if type(exit_code) is int and exit_code == 0:
                    event = {
                        **event,
                        "exit_code": 1,
                        "error_codes": ["CANCELLED"],
                        "next_action": event.get("next_action", "none"),
                    }
                elif type(exit_code) is int and exit_code == 1:
                    existing = event.get("error_codes")
                    if type(existing) is list and all(type(value) is str for value in existing):
                        error_codes = sorted(set(existing) | {"CANCELLED"})
                    elif existing is None:
                        error_codes = ["CANCELLED"]
                    else:
                        error_codes = existing
                    event = {**event, "error_codes": error_codes}
            try:
                safe_event = _safe_service_event(event, manifest_root=manifest_root)
            except IPCProtocolError:
                # A handler-provided event is still an untrusted boundary. If
                # validation fails, close the request with a stable terminal
                # error instead of silently losing completion (or forwarding
                # the raw event through the generic exception path).
                emitted_completed = True
                self._mark_active_completed(request.request_id)
                self._error(request.request_id, "UNSAFE_WORKFLOW_RESULT")
                return
            emitted_completed = event_type == "completed"
            if event_type == "completed":
                self._mark_active_completed(request.request_id)
            self._send(request.request_id, safe_event)

        try:
            if request.command == "preflight":
                self._send(request.request_id, {"event": "started", "operation": "preflight"})
                exit_code, details = self.preflight_handler(request.payload, token)
                if token.is_cancelled() and type(exit_code) is int and exit_code == 0:
                    exit_code = 1
                    details = {"error_codes": ["CANCELLED"], "next_action": "none"}
                elif token.is_cancelled() and type(exit_code) is int and exit_code in {1, 2}:
                    # Preserve a preflight failure cause while making the
                    # cancellation race visible to the native client. Leave
                    # malformed collections untouched so the normal strict
                    # validator still fails closed.
                    if isinstance(details, Mapping):
                        details = dict(details)
                        existing = details.get("error_codes")
                        if existing is None:
                            details["error_codes"] = ["CANCELLED"]
                        elif type(existing) is list and all(type(value) is str for value in existing):
                            details["error_codes"] = sorted(set(existing) | {"CANCELLED"})
                try:
                    if type(exit_code) is not int or exit_code not in {0, 1, 2}:
                        raise IPCProtocolError("preflight exit code is invalid")
                    safe_details = _safe_preflight_details(details)
                    if type(safe_details.get("next_action")) is not str:
                        raise IPCProtocolError("preflight completion requires next action")
                    if exit_code == 0 and (
                        safe_details.get("error_codes") or safe_details.get("safe_instruction")
                        or safe_details.get("next_action") != "none"
                    ):
                        raise IPCProtocolError("successful preflight cannot carry repair details")
                    if exit_code != 0 and (
                        type(safe_details.get("error_codes")) is not list
                        or not safe_details["error_codes"]
                    ):
                        raise IPCProtocolError("failed preflight requires error codes")
                except IPCProtocolError:
                    self._mark_active_completed(request.request_id)
                    self._error(request.request_id, "UNSAFE_PREFLIGHT_RESULT")
                    return
                self._mark_active_completed(request.request_id)
                self._send(request.request_id, {"event": "completed", "exit_code": exit_code, **safe_details})
            elif request.command == "scan":
                result = self.scan_handler(request.payload, emit, token)
                if not emitted_completed:
                    self._send_safe_workflow_completion(
                        request.request_id,
                        result,
                        cancellation_requested=token.is_cancelled(),
                        manifest_root=manifest_root,
                    )
            elif request.command == "review":
                self._send(request.request_id, {"event": "started", "operation": "review"})
                selections = request.payload["selections"]
                assert isinstance(selections, Mapping)
                if "caption_selections" in request.payload:
                    captions = request.payload["caption_selections"]
                    assert isinstance(captions, Mapping)
                    reviewed_path = self._review(Path(str(request.payload["manifest"])), selections, captions)
                else:
                    reviewed_path = self._review(Path(str(request.payload["manifest"])), selections, None)
                try:
                    reviewed_path = validate_app_manifest_path(reviewed_path)
                except Exception:
                    self._mark_active_completed(request.request_id)
                    self._error(request.request_id, "UNSAFE_REVIEW_RESULT")
                    return
                if token.is_cancelled():
                    # Review handlers materialize the reviewed manifest before
                    # returning.  A cancel can therefore race with the final
                    # validation just like it can with scan/apply completion;
                    # never turn that race into a successful terminal event.
                    try:
                        cancelled_event = _safe_service_event({
                            "type": "completed",
                            "exit_code": 1,
                            "manifest": str(reviewed_path),
                            "error_codes": ["CANCELLED"],
                            "next_action": "review_then_apply",
                        })
                    except IPCProtocolError:
                        self._mark_active_completed(request.request_id)
                        self._error(request.request_id, "UNSAFE_REVIEW_RESULT")
                        return
                    self._mark_active_completed(request.request_id)
                    self._send(request.request_id, cancelled_event)
                    return
                try:
                    reviewed_event = _safe_service_event({
                        "type": "completed",
                        "exit_code": 0,
                        "manifest": str(reviewed_path),
                        "next_action": "review_then_apply",
                    })
                except IPCProtocolError:
                    self._mark_active_completed(request.request_id)
                    self._error(request.request_id, "UNSAFE_REVIEW_RESULT")
                    return
                self._mark_active_completed(request.request_id)
                self._send(request.request_id, reviewed_event)
            elif request.command == "apply":
                result = self.apply_handler(Path(str(request.payload["manifest"])), emit, token)
                if not emitted_completed:
                    self._send_safe_workflow_completion(
                        request.request_id,
                        result,
                        cancellation_requested=token.is_cancelled(),
                        manifest_root=manifest_root,
                    )
            elif request.command == "rollback":
                result = self.rollback_handler(Path(str(request.payload["manifest"])), emit, token)
                if not emitted_completed:
                    self._send_safe_workflow_completion(
                        request.request_id,
                        result,
                        cancellation_requested=token.is_cancelled(),
                        manifest_root=manifest_root,
                    )
        except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError) as error:
            # Keep the protocol actionable even if a future adapter path raises
            # before reaching ``service.scan``.  Map by exception type rather
            # than forwarding ``error.code``: adapter error text is not a UI
            # contract and must never cross the helper boundary.
            if not emitted_completed:
                if isinstance(error, PhotosAccessError):
                    code, next_action = "PHOTOS_ACCESS_DENIED", "grant_photos_access"
                elif isinstance(error, PhotoScriptPermissionError):
                    code, next_action = "PHOTOS_AUTOMATION_DENIED", "grant_photos_automation"
                else:
                    code, next_action = "PHOTOSCRIPT_UNAVAILABLE", "fix_fatal_error"
                self._mark_active_completed(request.request_id)
                self._send(request.request_id, _safe_service_event({
                    "type": "completed",
                    "exit_code": 2,
                    "warning_codes": [],
                    "error_codes": [code],
                    "next_action": next_action,
                }))
        except Exception:
            if not emitted_completed:
                self._mark_active_completed(request.request_id)
                self._error(request.request_id, "WORKER_OPERATION_FAILED")
        finally:
            with self._state_lock:
                if self._active is not None and self._active[0] == request.request_id:
                    self._active = None
                    self._active_command = None
                    self._active_completed = False

    def join(self) -> None:
        while True:
            with self._state_lock:
                autonomy_threads = tuple(self._autonomy_threads)
            if not autonomy_threads:
                break
            for thread in autonomy_threads:
                thread.join()
        if self.autonomy_runtime is not None:
            self.autonomy_runtime.shutdown()
        with self._state_lock:
            active = self._active
        if active is not None:
            active[2].join()
        while True:
            with self._state_lock:
                queue_threads = tuple(self._queue_threads)
            if not queue_threads:
                break
            for thread in queue_threads:
                thread.join()
        self._output_queue.put(None)
        self._output_thread.join()

    def shutdown(self) -> None:
        """Cooperatively stop an unfinished worker before closing IPC output."""
        with self._state_lock:
            self._closing = True
            if self._autonomy_generation:
                self._autonomy_generation.set()
            if self.autonomy_runtime is not None:
                self.autonomy_runtime.revoke()
            active = self._active
            # Preflight is read-only and bounded; allow it to finish when the
            # request stream closes so an immediate EOF cannot turn a healthy
            # local check into a spurious CANCELLED result. Scans and
            # mutations still cancel cooperatively at EOF.
            if (
                active is not None
                and not self._active_completed
                and self._active_command != "preflight"
            ):
                active[1].cancel()
        self.join()

    def _review(
        self,
        manifest: Path,
        selections: Mapping[str, list[str]],
        captions: Mapping[str, bool] | None,
    ) -> Path:
        handler = self.review_handler
        if captions is None:
            return handler(manifest, selections)
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            return handler(manifest, selections, captions)
        positional = [
            parameter for parameter in signature.parameters.values()
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepts_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
        if accepts_varargs or len(positional) >= 3:
            return handler(manifest, selections, captions)
        return handler(manifest, selections)


def _bounded_input_lines(incoming: TextIO):
    """Yield input lines without materializing an arbitrarily long JSONL line."""
    readline = getattr(incoming, "readline", None)
    if not callable(readline):
        yield from incoming
        return
    while True:
        line = readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        oversized = False
        while "\n" not in line and len(line) == MAX_LINE_BYTES + 1:
            oversized = True
            line = readline(MAX_LINE_BYTES + 1)
            if not line:
                break
        if oversized:
            yield None
        else:
            yield line


def serve(
    incoming: TextIO = sys.stdin,
    outgoing: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
    *,
    scan_handler: Callable[[dict[str, object], Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None = None,
    apply_handler: Callable[[Path, Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None = None,
    rollback_handler: Callable[[Path, Callable[[dict[str, object]], None], CancellationToken], WorkflowResult] | None = None,
    review_handler: Callable[..., Path] | None = None,
    preflight_handler: Callable[[dict[str, object], CancellationToken], tuple[int, dict[str, object]]] | None = None,
    queue_handler: Callable[[IPCRequest, Callable[[dict[str, object]], None]], None] | None = None,
) -> None:
    """Serve helper commands until stdin closes; never expose exceptions on stdout."""
    del error_stream  # stderr remains reserved for the worker runtime, never protocol output.
    if queue_handler is None:
        # Import lazily because the queue runtime consumes the already-defined
        # IPCRequest contract.  Production gets a real continuous queue while
        # tests may still inject a small deterministic handler.
        from .queue_runtime import ContinuousQueueRuntime

        queue_handler = ContinuousQueueRuntime().handle
    from .autonomous_dispatcher import AutonomousCampaignRuntime
    server = _WorkerServer(
        outgoing,
        scan_handler=scan_handler,
        apply_handler=apply_handler,
        rollback_handler=rollback_handler,
        review_handler=review_handler,
        preflight_handler=preflight_handler,
        queue_handler=queue_handler,
        autonomy_runtime=AutonomousCampaignRuntime(),
    )
    for line in _bounded_input_lines(incoming):
        if line is None:
            server._error("invalid", "INVALID_REQUEST")
            continue
        try:
            request = parse_request_line(line.rstrip("\n"))
        except IPCProtocolError:
            server._error("invalid", "INVALID_REQUEST")
            continue
        if request.command == "cancel":
            server.cancel(request)
        else:
            server.start(request)
    server.shutdown()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
