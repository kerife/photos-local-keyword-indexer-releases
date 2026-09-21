"""Persistent coordination metadata for per-photo review queue sessions.

The session ledger deliberately stores only opaque identifiers and workflow
state.  Photo proposals and user selections remain in the existing one-photo
manifests, where the established evidence and mutation gates apply.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import validate_ollama_model_name


SESSION_FILENAME = "session.json"
SESSION_SCHEMA_VERSION = 1
# A session is an append-only coordination ledger. Keep it bounded, but large
# enough for long-running cataloguing sessions; photo content remains outside.
MAX_SESSION_BYTES = 8 * 1024 * 1024
ITEM_STATES = frozenset(
    {
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
    }
)
SESSION_STATES = frozenset({"running", "paused", "stopped", "attention"})
TERMINAL_ITEM_STATES = frozenset({"verified", "discarded"})
DECISION_ACTIONS = frozenset({"persist", "discard", "rescan"})
_TRUSTED_SYSTEM_SYMLINKS = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


class QueueSessionError(ValueError):
    """Raised when queue-session state or persistence is invalid."""


class DecisionConflictError(QueueSessionError):
    """Raised when a decision identifier is reused for another command."""


class StaleRevisionError(QueueSessionError):
    """Raised when a decision targets a superseded item revision."""


def _bounded_int(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise QueueSessionError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Bounded work requested for one continuous queue session."""

    photo_count: int
    inference_concurrency: int
    auto_analyze: bool = True
    include_caption: bool = True
    apple_maps: bool = False
    random_selection: bool = True
    model_policy: str = "adaptive"
    model: str | None = None
    fast_model: str = "qwen3-vl:4b"
    detailed_model: str = "qwen3-vl:4b"

    def __post_init__(self) -> None:
        _bounded_int(self.photo_count, label="photo_count", minimum=1, maximum=50)
        _bounded_int(self.inference_concurrency, label="inference_concurrency", minimum=1, maximum=4)
        for value in (self.auto_analyze, self.include_caption, self.apple_maps, self.random_selection):
            if type(value) is not bool:
                raise QueueSessionError("session options must be booleans")
        if self.model_policy not in {"single", "adaptive"}:
            raise QueueSessionError("model_policy is unsupported")
        try:
            validate_ollama_model_name(self.fast_model)
            validate_ollama_model_name(self.detailed_model)
            if self.model is not None:
                validate_ollama_model_name(self.model)
        except Exception as error:
            raise QueueSessionError("session model is invalid") from error
        if self.model_policy == "single" and self.model is None:
            raise QueueSessionError("single policy requires a model")

    def to_dict(self) -> dict[str, object]:
        return {
            "photo_count": self.photo_count,
            "inference_concurrency": self.inference_concurrency,
            "auto_analyze": self.auto_analyze,
            "include_caption": self.include_caption,
            "apple_maps": self.apple_maps,
            "random_selection": self.random_selection,
            "model_policy": self.model_policy,
            "model": self.model,
            "fast_model": self.fast_model,
            "detailed_model": self.detailed_model,
        }

    @classmethod
    def from_dict(cls, value: object) -> SessionConfig:
        data = _require_keys(
            value,
            {
                "photo_count", "inference_concurrency", "auto_analyze", "include_caption",
                "apple_maps", "random_selection", "model_policy", "model", "fast_model",
                "detailed_model",
            },
            "session config",
        )
        return cls(
            photo_count=data["photo_count"],
            inference_concurrency=data["inference_concurrency"],
            auto_analyze=data["auto_analyze"],
            include_caption=data["include_caption"],
            apple_maps=data["apple_maps"],
            random_selection=data["random_selection"],
            model_policy=data["model_policy"],
            model=data["model"],
            fast_model=data["fast_model"],
            detailed_model=data["detailed_model"],
        )


def _require_keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise QueueSessionError(f"{label} keys do not match the schema")
    return value


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise QueueSessionError(f"{label} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise QueueSessionError(f"{label} must be a UUID") from error
    if str(parsed) != value:
        raise QueueSessionError(f"{label} must be a canonical lowercase UUID")
    return value


def _optional_uuid(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _uuid(value, label)


@dataclass(slots=True)
class QueueItem:
    """Opaque workflow state for one photo revision."""

    item_id: str
    revision: int
    state: str
    source_run_id: str | None = None
    reviewed_run_id: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.item_id, "item_id")
        _bounded_int(self.revision, label="revision", minimum=1, maximum=2**31 - 1)
        if not isinstance(self.state, str) or self.state not in ITEM_STATES:
            raise QueueSessionError("item state is unsupported")
        _optional_uuid(self.source_run_id, "source_run_id")
        _optional_uuid(self.reviewed_run_id, "reviewed_run_id")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "item_id": self.item_id,
            "revision": self.revision,
            "state": self.state,
            "source_run_id": self.source_run_id,
            "reviewed_run_id": self.reviewed_run_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> QueueItem:
        data = _require_keys(
            value,
            {"item_id", "revision", "state", "source_run_id", "reviewed_run_id"},
            "queue item",
        )
        return cls(
            item_id=data["item_id"],
            revision=data["revision"],
            state=data["state"],
            source_run_id=data["source_run_id"],
            reviewed_run_id=data["reviewed_run_id"],
        )


@dataclass(frozen=True, slots=True)
class QueueDecision:
    """An idempotency record for one user decision against one revision."""

    decision_id: str
    item_id: str
    revision: int
    action: str

    def __post_init__(self) -> None:
        _uuid(self.decision_id, "decision_id")
        _uuid(self.item_id, "item_id")
        _bounded_int(self.revision, label="revision", minimum=1, maximum=2**31 - 1)
        if not isinstance(self.action, str) or self.action not in DECISION_ACTIONS:
            raise QueueSessionError("decision action is unsupported")

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "item_id": self.item_id,
            "revision": self.revision,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, value: object) -> QueueDecision:
        data = _require_keys(value, {"decision_id", "item_id", "revision", "action"}, "queue decision")
        return cls(
            decision_id=data["decision_id"],
            item_id=data["item_id"],
            revision=data["revision"],
            action=data["action"],
        )


@dataclass(slots=True)
class QueueSession:
    """Recoverable queue coordination state, excluding all photo content."""

    session_id: str
    config: SessionConfig
    revision: int = 0
    items: list[QueueItem] = field(default_factory=list)
    decisions: list[QueueDecision] = field(default_factory=list)
    state: str = "running"
    schema_version: int = SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def new(cls, *, session_id: str, config: SessionConfig) -> QueueSession:
        return cls(session_id=session_id, config=config)

    def validate(self) -> None:
        _uuid(self.session_id, "session_id")
        if not isinstance(self.config, SessionConfig):
            raise QueueSessionError("config must be a SessionConfig")
        self.config.__post_init__()
        _bounded_int(self.revision, label="session revision", minimum=0, maximum=2**31 - 1)
        if type(self.schema_version) is not int or self.schema_version != SESSION_SCHEMA_VERSION:
            raise QueueSessionError("session schema version is unsupported")
        if not isinstance(self.state, str) or self.state not in SESSION_STATES:
            raise QueueSessionError("session state is unsupported")
        if not isinstance(self.items, list) or not isinstance(self.decisions, list):
            raise QueueSessionError("session items and decisions must be lists")

        item_keys: set[tuple[str, int]] = set()
        revisions_by_item: dict[str, list[int]] = {}
        current_by_item: dict[str, QueueItem] = {}
        for item in self.items:
            if not isinstance(item, QueueItem):
                raise QueueSessionError("session items must be QueueItem values")
            item.__post_init__()
            key = (item.item_id, item.revision)
            if key in item_keys:
                raise QueueSessionError("item revisions must be unique")
            item_keys.add(key)
            revisions_by_item.setdefault(item.item_id, []).append(item.revision)
            current = current_by_item.get(item.item_id)
            if current is None or item.revision > current.revision:
                current_by_item[item.item_id] = item
        for revisions in revisions_by_item.values():
            if sorted(revisions) != list(range(1, max(revisions) + 1)):
                raise QueueSessionError("item revisions must be contiguous")

        decision_ids: set[str] = set()
        for decision in self.decisions:
            if not isinstance(decision, QueueDecision):
                raise QueueSessionError("session decisions must be QueueDecision values")
            decision.__post_init__()
            if decision.decision_id in decision_ids:
                raise QueueSessionError("decision identifiers must be unique")
            if (decision.item_id, decision.revision) not in item_keys:
                raise QueueSessionError("decision references an unknown item revision")
            decision_ids.add(decision.decision_id)

    def enqueue(self, *, item_id: str, source_run_id: str | None = None, state: str = "queued") -> QueueItem:
        _uuid(item_id, "item_id")
        if any(item.item_id == item_id for item in self.items):
            raise QueueSessionError("item is already in the session")
        current_items = {
            existing.item_id: existing
            for existing in sorted(self.items, key=lambda candidate: candidate.revision)
        }
        active_count = sum(
            item.state not in TERMINAL_ITEM_STATES
            for item in current_items.values()
        )
        if active_count >= self.config.photo_count:
            raise QueueSessionError("session photo limit has been reached")
        item = QueueItem(item_id=item_id, revision=1, state=state, source_run_id=source_run_id)
        self.items.append(item)
        self._advance_revision()
        return item

    def _advance_revision(self) -> None:
        if self.revision >= 2**31 - 1:
            raise QueueSessionError("session revision limit has been reached")
        self.revision += 1

    def _require_revision(self, expected_revision: int) -> None:
        _bounded_int(
            expected_revision,
            label="expected session revision",
            minimum=0,
            maximum=2**31 - 1,
        )
        if expected_revision != self.revision:
            raise StaleRevisionError("control targets a stale session revision")

    def set_state(self, *, expected_revision: int, state: str) -> None:
        """Apply a session control only against the caller's current revision."""
        self._require_revision(expected_revision)
        if not isinstance(state, str) or state not in SESSION_STATES:
            raise QueueSessionError("session state is unsupported")
        if self.state == state:
            return
        self.state = state
        self._advance_revision()

    def update_config(self, *, expected_revision: int, config: SessionConfig) -> None:
        """Replace bounded session settings with optimistic concurrency control."""
        self._require_revision(expected_revision)
        if not isinstance(config, SessionConfig):
            raise QueueSessionError("config must be a SessionConfig")
        config.__post_init__()
        if self.config == config:
            return
        self.config = config
        self._advance_revision()

    def checkpoint_item(
        self,
        *,
        item_id: str,
        revision: int,
        state: str,
        source_run_id: str | None = None,
        reviewed_run_id: str | None = None,
    ) -> QueueItem:
        """Persist one deterministic item-stage transition in the session ledger."""
        current = self.current_item(item_id)
        if current.revision != revision:
            raise StaleRevisionError("checkpoint targets a stale item revision")
        if not isinstance(state, str) or state not in ITEM_STATES:
            raise QueueSessionError("item state is unsupported")
        _optional_uuid(source_run_id, "source_run_id")
        _optional_uuid(reviewed_run_id, "reviewed_run_id")

        changed = current.state != state
        current.state = state
        if source_run_id is not None and current.source_run_id != source_run_id:
            current.source_run_id = source_run_id
            changed = True
        if reviewed_run_id is not None and current.reviewed_run_id != reviewed_run_id:
            current.reviewed_run_id = reviewed_run_id
            changed = True
        if changed:
            self._advance_revision()
        return current

    def current_item(self, item_id: str) -> QueueItem:
        _uuid(item_id, "item_id")
        matches = [item for item in self.items if item.item_id == item_id]
        if not matches:
            raise QueueSessionError("item is not in the session")
        return max(matches, key=lambda item: item.revision)

    def record_decision(
        self,
        *,
        decision_id: str,
        item_id: str,
        revision: int,
        action: str,
    ) -> QueueDecision:
        proposed = QueueDecision(
            decision_id=decision_id,
            item_id=item_id,
            revision=revision,
            action=action,
        )
        for decision in self.decisions:
            if decision.decision_id != decision_id:
                continue
            if decision == proposed:
                return decision
            raise DecisionConflictError("decision_id is already bound to another command")

        current = self.current_item(item_id)
        if current.revision != revision:
            raise StaleRevisionError("decision targets a stale item revision")
        if action == "persist":
            allowed_states = {"ready", "edited"}
        elif action == "rescan":
            # Reanalysis is read-only and remains safe after an uncertain
            # write; it creates a new revision rather than retrying the write.
            allowed_states = {"ready", "failed", "uncertain"}
        else:
            # Discard never touches Photos, so it is safe for queued or
            # in-flight analysis as well as failed/uncertain results.
            allowed_states = ITEM_STATES - {"save_queued", "saving", "verified", "discarded"}
        if current.state not in allowed_states:
            raise QueueSessionError("decision is not allowed for the item state")

        if action == "persist":
            current.state = "save_queued"
        elif action == "discard":
            current.state = "discarded"
        else:
            self.items.append(QueueItem(item_id=item_id, revision=revision + 1, state="queued"))
        self.decisions.append(proposed)
        self._advance_revision()
        return proposed

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "revision": self.revision,
            "state": self.state,
            "config": self.config.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "decisions": [decision.to_dict() for decision in self.decisions],
        }

    @classmethod
    def from_dict(cls, value: object) -> QueueSession:
        data = _require_keys(
            value,
            {"schema_version", "session_id", "revision", "state", "config", "items", "decisions"},
            "queue session",
        )
        if not isinstance(data["items"], list) or not isinstance(data["decisions"], list):
            raise QueueSessionError("session items and decisions must be lists")
        return cls(
            schema_version=data["schema_version"],
            session_id=data["session_id"],
            revision=data["revision"],
            state=data["state"],
            config=SessionConfig.from_dict(data["config"]),
            items=[QueueItem.from_dict(item) for item in data["items"]],
            decisions=[QueueDecision.from_dict(decision) for decision in data["decisions"]],
        )


def _reject_symlinked_ancestors(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for component in parts:
        current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as error:
            raise QueueSessionError("session directory is unsafe") from error
        if not stat.S_ISLNK(details.st_mode):
            continue
        try:
            resolved = current.resolve(strict=True)
        except OSError as error:
            raise QueueSessionError("session directory is unsafe") from error
        if _TRUSTED_SYSTEM_SYMLINKS.get(current) != resolved:
            raise QueueSessionError("session directory must not traverse symlinks")


def _ensure_private_directory(path: Path) -> None:
    _reject_symlinked_ancestors(path)
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise QueueSessionError("session directory must be a user-owned real directory")
    os.chmod(path, 0o700)


def _atomic_private_write(path: Path, payload: bytes) -> None:
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode)
        or stat.S_ISLNK(existing.st_mode)
        or existing.st_uid != os.getuid()
        or existing.st_nlink != 1
        or stat.S_IMODE(existing.st_mode) != 0o600
    ):
        raise QueueSessionError("session file must be a private regular file")

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise QueueSessionError("JSON objects must not contain duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise QueueSessionError(f"JSON constant {value!r} is not allowed")


def _read_private_session(path: Path, directory: os.stat_result) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_descriptor = os.open(path.parent, flags)
    except OSError as error:
        raise QueueSessionError("session directory is unsafe") from error
    try:
        opened_directory = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or opened_directory.st_uid != os.getuid()
            or stat.S_IMODE(opened_directory.st_mode) != 0o700
            or (opened_directory.st_dev, opened_directory.st_ino) != (directory.st_dev, directory.st_ino)
        ):
            raise QueueSessionError("session directory changed while being opened")
        try:
            initial = os.stat(path.name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as error:
            raise QueueSessionError("session file does not exist") from error
        if (
            not stat.S_ISREG(initial.st_mode)
            or stat.S_ISLNK(initial.st_mode)
            or initial.st_uid != os.getuid()
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_nlink != 1
            or initial.st_size > MAX_SESSION_BYTES
        ):
            raise QueueSessionError("session file must be a private regular file")
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            raise QueueSessionError("session file changed while being opened") from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or opened.st_size > MAX_SESSION_BYTES
                or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
            ):
                raise QueueSessionError("session file changed while being opened")
            chunks: list[bytes] = []
            remaining = MAX_SESSION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_SESSION_BYTES:
                raise QueueSessionError("session file exceeds the size limit")
            return payload
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def write_session(session_dir: Path, session: QueueSession) -> Path:
    """Validate and atomically persist a content-free queue-session ledger."""
    try:
        session_dir = Path(session_dir)
    except (TypeError, ValueError) as error:
        raise QueueSessionError("session directory is invalid") from error
    if not isinstance(session, QueueSession):
        raise QueueSessionError("session must be a QueueSession")
    payload = (
        json.dumps(session.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_SESSION_BYTES:
        raise QueueSessionError("session file exceeds the size limit")
    _ensure_private_directory(session_dir)
    path = session_dir / SESSION_FILENAME
    _atomic_private_write(path, payload)
    return path


def load_session(session_dir: Path) -> QueueSession:
    """Load a private session ledger under its strict, content-free schema."""
    try:
        session_dir = Path(session_dir)
        directory = os.lstat(session_dir)
    except (OSError, TypeError, ValueError) as error:
        raise QueueSessionError("session directory is unsafe") from error
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.getuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise QueueSessionError("session directory must be a private real directory")
    _reject_symlinked_ancestors(session_dir)
    try:
        decoded = _read_private_session(session_dir / SESSION_FILENAME, directory).decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
        return QueueSession.from_dict(value)
    except QueueSessionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise QueueSessionError("session file is invalid") from error
