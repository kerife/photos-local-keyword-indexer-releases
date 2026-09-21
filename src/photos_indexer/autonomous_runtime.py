"""Private durable evidence for explicitly authorized autonomous campaigns.

Campaign storage is independent of manual queue sessions. The helper ownership
barrier and dispatcher must grant exclusive ownership before using an apply
gate; creating or reopening this store never touches Photos or starts work.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from .adapters import PhotoScriptBridge, PhotoScriptPermissionError, PhotosAccessError, SelectedPhoto
from .autonomous_inventory import InventoryError, InventorySnapshot
from .manifest import ManifestError, ScanManifest, load_manifest
from .queue_decision import (
    QueueDecisionArtifactError,
    _private_directory,
    _read_private_file,
    _reject_constant,
    _reject_symlinked_ancestors,
    _strict_object,
)
from .queue_session import SessionConfig
from .taxonomy import proposed_keywords
from .service import review_manifest_v4
from .workflows import WorkflowResult, _has_valid_apply_receipt, _has_valid_rollback_receipt, _safe_photo_metadata, run_apply, run_status


POLICY_VERSION = "autonomous-v1"
CONFIDENCE_THRESHOLD = 0.85
MAX_COUNTER = 2**31 - 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class CampaignStorageError(ValueError):
    """Campaign evidence is invalid or cannot be persisted safely."""


class CampaignValidationError(CampaignStorageError):
    """A caller supplied an invalid control or exceeded bounded capacity."""


def _store_mutation(operation: Callable[..., Any]) -> Callable[..., Any]:
    """Revoke admission at every public store mutation's storage boundary."""
    @wraps(operation)
    def guarded(self: CampaignStore, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            try:
                return operation(self, *args, **kwargs)
            except CampaignValidationError:
                raise
            except (CampaignStorageError, OSError, QueueDecisionArtifactError, InventoryError) as error:
                self.fail_closed("storage")
                if isinstance(error, CampaignStorageError):
                    raise
                raise CampaignStorageError("campaign evidence storage failed") from error
    return guarded


def _identifier(value: object) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise CampaignValidationError("campaign identifier is invalid")
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_COUNTER:
        raise CampaignValidationError("campaign count is invalid")
    return value


def _limit(value: object) -> int | None:
    return None if value is None else _integer(value, minimum=1)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CampaignValidationError("campaign clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_private_file(path), object_pairs_hook=_strict_object, parse_constant=_reject_constant)
        if type(value) is not dict:
            raise CampaignStorageError("campaign artifact is invalid")
        return value
    except (QueueDecisionArtifactError, UnicodeError, ValueError, OSError) as error:
        raise CampaignStorageError("campaign artifact is unavailable or invalid") from error


def _keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise CampaignStorageError("campaign artifact fields are invalid")


def _write(path: Path, value: object, *, replace: bool = False) -> None:
    """Publish fsynced private evidence, with no overwrite for immutable records."""
    temporary: str | None = None
    try:
        _reject_symlinked_ancestors(path.parent)
        _private_directory(path.parent)
        payload = _json(value)
        if len(payload) > 64 * 1024:
            raise CampaignStorageError("campaign artifact exceeds the size limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".campaign-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            # Validate an existing checkpoint before replacing its inode.
            if path.exists() or path.is_symlink():
                _read_private_file(path)
            os.replace(temporary, path)
            temporary = None
        else:
            os.link(temporary, path, follow_symlinks=False)
            os.unlink(temporary)
            temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, ValueError, QueueDecisionArtifactError) as error:
        raise CampaignStorageError("campaign evidence could not be persisted") from error
    finally:
        if temporary is not None:
            os.unlink(temporary)


class CampaignStore:
    """Immutable inventory, small checkpoint, and disk-backed position evidence."""

    def __init__(self, path: Path, metadata: dict[str, Any], snapshot: InventorySnapshot) -> None:
        self.path = path
        self.metadata = metadata
        self.snapshot = snapshot
        self.config = SessionConfig.from_dict(metadata["config"])
        self.total = min(snapshot.total_count, metadata["limit"] or MAX_COUNTER)
        self._lock = threading.RLock()
        self._in_flight: set[int] = set()
        self._authorization: str | None = None
        self._failure_reason: str | None = None
        self._checkpoint = {
            "type": "autonomy_campaign", "campaign_id": metadata["campaign_id"], "revision": 0,
            "state": "paused", "total": self.total, "examined": 0, "analyzed": 0,
            "saved": 0, "no_change": 0, "attention": 0, "remaining": self.total,
            "in_flight": 0, "invalid_count": snapshot.invalid_count, "reason": "none",
        }

    @classmethod
    def create(
        cls, path: Path, *, campaign_id: str, config: SessionConfig,
        records: Iterable[SelectedPhoto | None], limit: int | None, created_at: datetime,
    ) -> CampaignStore:
        campaign_id = _identifier(campaign_id)
        limit = _limit(limit)
        created = _timestamp(created_at)
        config = SessionConfig.from_dict(config.to_dict())
        path = Path(path)
        if not path.is_absolute() or path.name != campaign_id or ".." in path.parts:
            raise CampaignStorageError("campaign path is invalid")
        try:
            _reject_symlinked_ancestors(path)
            _private_directory(path.parent)
            path.mkdir(mode=0o700)
            for name in ("controls", "positions", "authorizations"):
                (path / name).mkdir(mode=0o700)
            snapshot = InventorySnapshot.create(path / "inventory", records)
            _integer(snapshot.total_count)
            _integer(snapshot.invalid_count)
            metadata = {
                "version": 1, "campaign_id": campaign_id, "created_at": created,
                "config": config.to_dict(), "settings_digest": _digest(config.to_dict()),
                "limit": limit, "inventory_digest": snapshot.digest,
            }
            _write(path / "campaign.json", metadata)
            store = cls(path, metadata, snapshot)
            _write(path / "checkpoint.json", store._checkpoint)
            return store
        except (OSError, QueueDecisionArtifactError, InventoryError) as error:
            raise CampaignStorageError("campaign could not be created") from error

    @classmethod
    def load(cls, path: Path) -> CampaignStore:
        path = Path(path)
        metadata = _read(path / "campaign.json")
        _keys(metadata, {"version", "campaign_id", "created_at", "config", "settings_digest", "limit", "inventory_digest"})
        if metadata["version"] != 1 or type(metadata["version"]) is not int:
            raise CampaignStorageError("campaign version is invalid")
        if _identifier(metadata["campaign_id"]) != path.name:
            raise CampaignStorageError("campaign identity is invalid")
        _limit(metadata["limit"])
        if _digest(metadata["config"]) != metadata["settings_digest"]:
            raise CampaignStorageError("campaign settings have changed")
        try:
            _timestamp(datetime.fromisoformat(metadata["created_at"]))
            snapshot = InventorySnapshot.load(path / "inventory")
            if snapshot.digest != metadata["inventory_digest"]:
                raise CampaignStorageError("campaign snapshot has changed")
            store = cls(path, metadata, snapshot)
        except (TypeError, ValueError, InventoryError) as error:
            raise CampaignStorageError("campaign artifact is invalid") from error
        checkpoint_path = path / "checkpoint.json"
        try:
            checkpoint_path.lstat()
        except FileNotFoundError:
            # Creation publishes the validated catalogue before its initial
            # checkpoint. Repair only the pristine, never-authorized boundary;
            # a missing operational checkpoint remains damaged evidence.
            try:
                for name in ("controls", "authorizations", "positions"):
                    directory = path / name
                    _private_directory(directory)
                    with os.scandir(directory) as entries:
                        if next(entries, None) is not None:
                            raise CampaignStorageError("missing operational checkpoint")
            except (OSError, QueueDecisionArtifactError) as error:
                raise CampaignStorageError("initial campaign evidence is unavailable") from error
            _write(checkpoint_path, store._checkpoint)
        except OSError as error:
            raise CampaignStorageError("campaign checkpoint is unavailable") from error
        checkpoint = _read(checkpoint_path)
        _keys(checkpoint, set(store._checkpoint))
        store._checkpoint["revision"] = _integer(_integer(checkpoint["revision"]) + 1)
        store._checkpoint["reason"] = "recovered"
        store._rebuild_counts()
        _write(path / "checkpoint.json", store._checkpoint, replace=True)
        return store

    @classmethod
    def load_status(cls, path: Path) -> CampaignStore:
        """Read the durable checkpoint without replaying every position.

        A status read never authorizes work. Resume still calls ``load`` to
        reconcile the position evidence before it can admit another write.
        """
        path = Path(path)
        metadata = _read(path / "campaign.json")
        _keys(metadata, {"version", "campaign_id", "created_at", "config", "settings_digest", "limit", "inventory_digest"})
        if metadata["version"] != 1 or type(metadata["version"]) is not int:
            raise CampaignStorageError("campaign version is invalid")
        if _identifier(metadata["campaign_id"]) != path.name:
            raise CampaignStorageError("campaign identity is invalid")
        _limit(metadata["limit"])
        if _digest(metadata["config"]) != metadata["settings_digest"]:
            raise CampaignStorageError("campaign settings have changed")
        try:
            _timestamp(datetime.fromisoformat(metadata["created_at"]))
            snapshot = InventorySnapshot.load(path / "inventory")
            if snapshot.digest != metadata["inventory_digest"]:
                raise CampaignStorageError("campaign snapshot has changed")
            store = cls(path, metadata, snapshot)
        except (TypeError, ValueError, InventoryError) as error:
            raise CampaignStorageError("campaign artifact is invalid") from error
        checkpoint_path = path / "checkpoint.json"
        try:
            checkpoint_path.lstat()
        except FileNotFoundError:
            try:
                for name in ("controls", "authorizations", "positions"):
                    directory = path / name
                    _private_directory(directory)
                    with os.scandir(directory) as entries:
                        if next(entries, None) is not None:
                            raise CampaignStorageError("missing operational checkpoint")
            except (OSError, QueueDecisionArtifactError) as error:
                raise CampaignStorageError("initial campaign evidence is unavailable") from error
            _write(checkpoint_path, store._checkpoint)
        except OSError as error:
            raise CampaignStorageError("campaign checkpoint is unavailable") from error
        checkpoint = _read(checkpoint_path)
        _keys(checkpoint, set(store._checkpoint))
        if checkpoint["type"] != "autonomy_campaign" or checkpoint["campaign_id"] != metadata["campaign_id"]:
            raise CampaignStorageError("campaign checkpoint is invalid")
        if checkpoint["state"] not in {"preparing", "running", "pausing", "paused", "completed"}:
            raise CampaignStorageError("campaign checkpoint state is invalid")
        if checkpoint["reason"] not in {"none", "recovered", "user_pause", "storage", "permission"}:
            raise CampaignStorageError("campaign checkpoint reason is invalid")
        counts = {name: _integer(checkpoint[name]) for name in (
            "revision", "total", "examined", "analyzed", "saved", "no_change",
            "attention", "remaining", "in_flight", "invalid_count",
        )}
        if (
            counts["total"] != store.total
            or counts["invalid_count"] != snapshot.invalid_count
            or counts["analyzed"] > counts["examined"]
            or counts["saved"] + counts["no_change"] + counts["attention"] + counts["remaining"] != counts["total"]
        ):
            raise CampaignStorageError("campaign checkpoint counters are invalid")
        store._checkpoint = dict(checkpoint)
        store._checkpoint["revision"] = _integer(counts["revision"] + 1)
        store._checkpoint["state"] = "paused"
        store._checkpoint["reason"] = "recovered"
        return store

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._checkpoint)

    @property
    def failure_reason(self) -> str | None:
        """Latched owner failure; later position settlement cannot erase it."""
        with self._lock:
            return self._failure_reason

    def pending(self) -> Iterator[tuple[int, SelectedPhoto]]:
        for position, selected in enumerate(self.snapshot.iter_from()):
            if position >= self.total:
                break
            if not (self._position_path(position) / "outcome.json").exists():
                yield position, selected

    def _position_path(self, position: int) -> Path:
        _integer(position)
        if position >= self.total:
            raise CampaignValidationError("position is outside the campaign cap")
        return self.path / "positions" / f"{position:010d}"

    def _checkpoint_state(self, **changes: Any) -> None:
        value = {**self._checkpoint, **changes}
        value["revision"] = _integer(value["revision"] + 1)
        value["remaining"] = self.total - value["saved"] - value["no_change"] - value["attention"]
        value["in_flight"] = len(self._in_flight)
        _write(self.path / "checkpoint.json", value, replace=True)
        self._checkpoint = value

    def _control(self, command: str, payload: dict[str, Any]) -> bool:
        if command not in {"autonomy_start", "autonomy_resume", "autonomy_pause"} or type(payload) is not dict:
            raise CampaignValidationError("campaign control is invalid")
        expected = {"campaign_id", "decision_id"}
        if command == "autonomy_start":
            expected |= {"runs_root", "settings_path", "limit"}
        if set(payload) != expected:
            raise CampaignValidationError("campaign control fields are invalid")
        decision_id = _identifier(payload["decision_id"])
        if _identifier(payload["campaign_id"]) != self.metadata["campaign_id"]:
            raise CampaignValidationError("campaign control identity changed")
        if command == "autonomy_start":
            if (
                _limit(payload["limit"]) != self.metadata["limit"]
                or payload["runs_root"] != str(self.path.parent.parent / "runs")
                or payload["settings_path"] != str(self.path.parent.parent / "settings.json")
            ):
                raise CampaignValidationError("campaign control settings changed")
        value = {"command": command, "payload": payload, "digest": _digest({"command": command, "payload": payload})}
        path = self.path / "controls" / f"{decision_id}.json"
        if path.exists() or path.is_symlink():
            if _read(path) != value:
                raise CampaignValidationError("campaign decision was reused")
            return False
        _write(path, value)
        return True

    @_store_mutation
    def authorize(self, command: str, payload: dict[str, Any], *, now: datetime) -> str | None:
        """Record a fresh explicit activation; exact replays never reopen admission.

        Caller must first finish the helper's manual ownership drain. The
        returned ID names immutable authorization evidence, not human review.
        """
        created = _timestamp(now)
        if command not in {"autonomy_start", "autonomy_resume"}:
            raise CampaignValidationError("campaign activation is invalid")
        with self._lock:
            if not self._control(command, payload):
                return None
            decision_id = payload["decision_id"]
            _write(self.path / "authorizations" / f"{decision_id}.json", {
                "version": 1, "campaign_id": self.metadata["campaign_id"],
                "control_decision_id": decision_id,
                "origin": "autonomous_toggle" if command == "autonomy_start" else "autonomous_resume",
                "policy_version": POLICY_VERSION, "threshold": CONFIDENCE_THRESHOLD,
                "settings_digest": self.metadata["settings_digest"],
                "inventory_digest": self.snapshot.digest, "limit": self.metadata["limit"],
                "activated_at": created,
            })
            self._checkpoint_state(state="running", reason="none")
            self._failure_reason = None
            self._authorization = decision_id
            return decision_id

    @_store_mutation
    def pause(self, command: str, payload: dict[str, Any]) -> None:
        if command != "autonomy_pause":
            raise CampaignValidationError("campaign pause is invalid")
        with self._lock:
            # Close in-memory admission even if persisting the pause fails.
            self._authorization = None
            self._control(command, payload)
            self._checkpoint_state(state="pausing" if self._in_flight else "paused", reason="user_pause")

    @_store_mutation
    def mark_examined(self, position: int) -> None:
        with self._lock:
            path = self._position_path(position)
            if position in self._in_flight:
                return
            if len(self._in_flight) >= self.config.photo_count:
                raise CampaignValidationError("campaign capacity is full")
            if (path / "outcome.json").exists():
                raise CampaignValidationError("campaign position is already settled")
            try:
                path.mkdir(mode=0o700, exist_ok=True)
                _private_directory(path)
            except (OSError, QueueDecisionArtifactError) as error:
                raise CampaignStorageError("campaign position storage is unavailable") from error
            examined = path / "examined.json"
            is_new = not examined.exists()
            if is_new:
                _write(examined, {"position": position})
            elif _read(examined) != {"position": position}:
                raise CampaignStorageError("campaign position evidence changed")
            self._in_flight.add(position)
            self._checkpoint_state(examined=self._checkpoint["examined"] + int(is_new))

    @_store_mutation
    def settle(self, position: int, outcome: str, *, reason: str = "none") -> None:
        if outcome not in {"saved", "no_change", "attention"} or reason not in {"none", "failed", "uncertain", "low_confidence", "invalid"}:
            raise CampaignValidationError("campaign outcome is invalid")
        with self._lock:
            path = self._position_path(position)
            if _read(path / "examined.json") != {"position": position}:
                raise CampaignStorageError("campaign position was not examined")
            value = {"position": position, "outcome": outcome, "reason": reason}
            destination = path / "outcome.json"
            if destination.exists() or destination.is_symlink():
                if _read(destination) != value:
                    raise CampaignValidationError("campaign outcome changed")
                return
            _write(destination, value)
            self._in_flight.discard(position)
            changes = {outcome: self._checkpoint[outcome] + 1}
            remaining = self._checkpoint["remaining"] - 1
            if self._failure_reason is not None:
                changes.update(state="pausing" if self._in_flight else "paused", reason=self._failure_reason)
            elif remaining == 0 and not self._in_flight:
                changes.update(state="completed", reason="none")
                self._authorization = None
            elif self._checkpoint["state"] == "pausing" and not self._in_flight:
                changes.update(state="paused")
            self._checkpoint_state(**changes)

    @_store_mutation
    def release(self, position: int) -> None:
        """Drain an analyzed but unadmitted position when activation is closed."""
        with self._lock:
            self._in_flight.discard(position)
            self._checkpoint_state(**({"state": "paused"} if self._checkpoint["state"] == "pausing" and not self._in_flight else {}))

    def fail_closed(self, reason: str) -> None:
        if reason not in {"permission", "storage"}:
            raise CampaignStorageError("campaign pause reason is invalid")
        with self._lock:
            self._authorization = None
            self._failure_reason = reason
            try:
                self._checkpoint_state(state="pausing" if self._in_flight else "paused", reason=reason)
            except (CampaignStorageError, OSError, QueueDecisionArtifactError):
                # Disk failure cannot keep the volatile gate enabled. A later
                # reopen reconstructs receipts and always starts paused.
                self._failure_reason = "storage"
                self._checkpoint.update(state="paused", reason="storage")

    @_store_mutation
    def record_analysis(self, position: int, source_path: Path) -> ScanManifest:
        with self._lock:
            path = self._position_path(position)
            if _read(path / "examined.json") != {"position": position}:
                raise CampaignStorageError("analysis has no examined position")
            source_path = Path(source_path)
            runs = self.path.parent.parent / "runs"
            if source_path.name != "manifest.json" or source_path.parent.parent != runs:
                raise CampaignValidationError("source manifest is outside campaign storage")
            try:
                source = load_manifest(source_path.parent)
            except (ManifestError, OSError) as error:
                raise CampaignStorageError("persisted analysis evidence is unavailable or corrupt") from error
            selected = next(self.snapshot.iter_from(position))
            if (
                source.schema_version not in {1, 2} or source.reviewed_from_run_id is not None
                or len(source.photos) != 1
                or source.photos[0].photos_local_identifier != selected.local_id
                or source.photos[0].apply_state != "not_run"
                or source.photos[0].rollback_state != "not_run"
            ):
                raise ManifestError("autonomous source identity or state is invalid")
            value = {
                "position": position, "run_name": source_path.parent.name,
                "run_id": source.run_id, "source_digest": source.scan_digest,
            }
            destination = path / "analysis.json"
            if destination.exists() or destination.is_symlink():
                if _read(destination) != value:
                    raise CampaignStorageError("autonomous source changed")
            else:
                _write(destination, value)
                self._checkpoint_state(analyzed=self._checkpoint["analyzed"] + 1)
            return source

    def _rebuild_counts(self) -> None:
        """Only a bounded directory entry and artifact are live at each step."""
        try:
            with os.scandir(self.path / "positions") as entries:
                for entry in entries:
                    if not entry.name.isascii() or not entry.name.isdecimal() or len(entry.name) != 10:
                        raise CampaignStorageError("campaign position path is invalid")
                    position = int(entry.name)
                    path = self._position_path(position)
                    _private_directory(path)
                    examined = path / "examined.json"
                    if not examined.exists():
                        if any(path.iterdir()):
                            raise CampaignStorageError("campaign position lacks admission evidence")
                        continue
                    if _read(examined) != {"position": position}:
                        raise CampaignStorageError("campaign position evidence changed")
                    self._checkpoint["examined"] += 1
                    analysis_path = path / "analysis.json"
                    if analysis_path.exists() or analysis_path.is_symlink():
                        analysis = _read(analysis_path)
                        _keys(analysis, {"position", "run_name", "run_id", "source_digest"})
                        if analysis["position"] != position or type(analysis["position"]) is not int:
                            raise CampaignStorageError("analysis position changed")
                        _identifier(analysis["run_name"])
                        _identifier(analysis["run_id"])
                        _sha256(analysis["source_digest"])
                        self._checkpoint["analyzed"] += 1
                    outcome_path = path / "outcome.json"
                    if not outcome_path.exists() and (path / "decision.json").exists():
                        outcome = _recover_decision(self.path, position)
                        _write(outcome_path, {"position": position, "outcome": outcome, "reason": "uncertain" if outcome == "attention" else "none"})
                    if outcome_path.exists() or outcome_path.is_symlink():
                        value = _settled_outcome(self.path, position)
                        self._checkpoint[value["outcome"]] += 1
        except (OSError, QueueDecisionArtifactError) as error:
            raise CampaignStorageError("campaign positions are unavailable") from error
        self._checkpoint["remaining"] = self.total - self._checkpoint["saved"] - self._checkpoint["no_change"] - self._checkpoint["attention"]


def _sha256(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CampaignStorageError("campaign digest is invalid")
    return value


def _authorization(path: Path, authorization_id: str) -> dict[str, Any]:
    metadata = _read(path / "campaign.json")
    value = _read(path / "authorizations" / f"{_identifier(authorization_id)}.json")
    _keys(value, {"version", "campaign_id", "control_decision_id", "origin", "policy_version", "threshold", "settings_digest", "inventory_digest", "limit", "activated_at"})
    if (
        value["version"] != 1 or type(value["version"]) is not int
        or value["campaign_id"] != path.name or value["campaign_id"] != metadata["campaign_id"]
        or value["control_decision_id"] != authorization_id
        or value["origin"] not in {"autonomous_toggle", "autonomous_resume"}
        or value["policy_version"] != POLICY_VERSION
        or type(value["threshold"]) is not float or value["threshold"] != CONFIDENCE_THRESHOLD
        or value["settings_digest"] != _digest(metadata["config"])
        or value["settings_digest"] != metadata["settings_digest"]
        or value["inventory_digest"] != metadata["inventory_digest"]
        or _limit(value["limit"]) != metadata["limit"]
    ):
        raise CampaignStorageError("autonomous authorization binding is invalid")
    try:
        _timestamp(datetime.fromisoformat(value["activated_at"]))
    except (TypeError, ValueError) as error:
        raise CampaignStorageError("autonomous authorization time is invalid") from error
    control = _read(path / "controls" / f"{authorization_id}.json")
    _keys(control, {"command", "payload", "digest"})
    command = "autonomy_start" if value["origin"] == "autonomous_toggle" else "autonomy_resume"
    if control["command"] != command or control["digest"] != _digest({"command": control["command"], "payload": control["payload"]}):
        raise CampaignStorageError("autonomous activation evidence is invalid")
    expected_payload = {"campaign_id": path.name, "decision_id": authorization_id}
    if command == "autonomy_start":
        expected_payload.update(runs_root=str(path.parent.parent / "runs"), settings_path=str(path.parent.parent / "settings.json"), limit=metadata["limit"])
    if control["payload"] != expected_payload:
        raise CampaignStorageError("autonomous activation binding changed")
    return value


def autonomous_run_provenance(manifest_path: Path) -> dict[str, Any] | None:
    """Validate bounded run-side evidence for an explicit autonomous History label.

    Missing link means no autonomous claim; malformed links raise instead of
    silently mislabeling an autonomous run as human reviewed. No recovery or
    Photos access is performed by this reader.
    """
    manifest_path = Path(manifest_path)
    link_path = manifest_path.parent / "autonomy.json"
    if not link_path.exists() and not link_path.is_symlink():
        return None
    link = _read(link_path)
    _keys(link, {"version", "campaign_id", "position", "decision_id", "authorization_id", "authorization_digest", "admission_digest"})
    if type(link["version"]) is not int or link["version"] != 1:
        raise CampaignStorageError("autonomous link version is invalid")
    campaign = _identifier(link["campaign_id"])
    position = _integer(link["position"])
    path = manifest_path.parent.parent.parent / "autonomous-campaigns" / campaign
    authorization = _authorization(path, link["authorization_id"])
    if _digest(authorization) != _sha256(link["authorization_digest"]):
        raise CampaignStorageError("autonomous authorization digest changed")
    admission = _read(path / "positions" / f"{position:010d}" / "decision.json")
    _keys(admission, {"version", "campaign_id", "position", "policy_version", "decision_id", "authorization_id", "authorization_digest", "source_run_id", "source_digest", "selection_digest"})
    identity = {"campaign_id": campaign, "position": position, "source_run_id": admission["source_run_id"], "source_digest": admission["source_digest"], "policy_version": POLICY_VERSION}
    if (
        admission["version"] != 1 or type(admission["version"]) is not int
        or admission["campaign_id"] != campaign or admission["position"] != position
        or type(admission["position"]) is not int or admission["policy_version"] != POLICY_VERSION
        or admission["decision_id"] != _digest(identity) or admission["decision_id"] != link["decision_id"]
        or admission["authorization_id"] != link["authorization_id"]
        or admission["authorization_digest"] != link["authorization_digest"]
        or _digest(admission) != _sha256(link["admission_digest"])
    ):
        raise CampaignStorageError("autonomous admission binding is invalid")
    reviewed = load_manifest(manifest_path.parent)
    receipt = _read(path / "positions" / f"{position:010d}" / "reviewed.json")
    expected_receipt = {"run_name": manifest_path.parent.name, "run_id": reviewed.run_id, "review_digest": reviewed.review_decision_digest}
    if (
        receipt != expected_receipt or reviewed.schema_version != 4 or len(reviewed.photos) != 1
        or reviewed.reviewed_from_run_id != admission["source_run_id"]
        or reviewed.source_scan_digest != admission["source_digest"]
        or _digest({"keywords": reviewed.photos[0].approved_keywords, "caption": reviewed.photos[0].approved_caption}) != admission["selection_digest"]
    ):
        raise CampaignStorageError("autonomous review binding is invalid")
    return {"campaign_id": campaign, "position": position, "origin": authorization["origin"], "decision_id": admission["decision_id"]}


def _receipt_outcome(reviewed_path: Path) -> str:
    if autonomous_run_provenance(reviewed_path) is None:
        return "attention"
    manifest = load_manifest(reviewed_path.parent)
    status = run_status(reviewed_path)
    row = manifest.photos[0]
    if status.exit_code not in {0, 1} or status.error_codes:
        return "attention"
    # Saved is the historical campaign outcome, not a claim that metadata is
    # still present. A verified rollback preserves that history, never admission.
    rolled_back = (
        row.rollback_state in {"verified_removed", "already_absent"}
        and _has_valid_rollback_receipt(manifest, row)
    )
    if row.rollback_state != "not_run" and not rolled_back:
        return "attention"
    if row.approved_caption is not None and not (
        (row.caption_state == "verified" and row.applied_caption == row.approved_caption)
        or (row.caption_state == "preserved" and row.applied_caption is None)
        or (rolled_back and row.caption_state == "removed" and row.applied_caption == row.approved_caption)
    ):
        return "attention"
    if row.apply_state == "verified" and _has_valid_apply_receipt(manifest, row):
        return "saved"
    if row.apply_state == "noop":
        return "no_change"
    return "attention"


def _recover_decision(path: Path, position: int) -> str:
    reviewed_path = path / "positions" / f"{position:010d}" / "reviewed.json"
    if not reviewed_path.exists():
        return "attention"
    reviewed = _read(reviewed_path)
    _keys(reviewed, {"run_name", "run_id", "review_digest"})
    run = path.parent.parent / "runs" / _identifier(reviewed["run_name"]) / "manifest.json"
    try:
        return _receipt_outcome(run)
    except (CampaignStorageError, ManifestError, OSError):
        return "attention"


def _settled_outcome(path: Path, position: int) -> dict[str, Any]:
    value = _read(path / "positions" / f"{position:010d}" / "outcome.json")
    _keys(value, {"position", "outcome", "reason"})
    if (
        type(value["position"]) is not int or value["position"] != position
        or value["outcome"] not in {"saved", "no_change", "attention"}
        or value["reason"] not in {"none", "failed", "uncertain", "low_confidence", "invalid"}
    ):
        raise CampaignStorageError("campaign outcome is invalid")
    # The settled ledger is a claim, not a substitute for linked apply evidence.
    # A damaged receipt remains fail-closed; never repair it by touching Photos.
    if value["outcome"] == "saved" and _recover_decision(path, position) != "saved":
        raise CampaignStorageError("settled save evidence is unavailable or invalid")
    return value


class AutomaticApplyGate:
    """One serialized automatic apply lane with durable, pause-gated admission.

    This is the campaign's persistence component, not the helper owner or
    dispatcher. Runtime ownership must prevent concurrent manual Photos calls.
    """

    def __init__(
        self, store: CampaignStore, *, bridge_factory: Callable[[], Any] = PhotoScriptBridge,
        review_runner: Callable[..., Path] = review_manifest_v4,
        apply_runner: Callable[[Path], WorkflowResult] = run_apply,
    ) -> None:
        self.store = store
        self.bridge_factory = bridge_factory
        self.review_runner = review_runner
        self.apply_runner = apply_runner
        self._write_lock = threading.Lock()

    def process(self, position: int, source_path: Path) -> str:
        try:
            return self._process(position, source_path)
        except CampaignStorageError:
            self.store.fail_closed("storage")
            raise

    def _process(self, position: int, source_path: Path) -> str:
        store = self.store
        path = store._position_path(position)
        with self._write_lock:
            if (path / "outcome.json").exists():
                return str(_settled_outcome(store.path, position)["outcome"])
            if (path / "decision.json").exists():
                outcome = _recover_decision(store.path, position)
                store.settle(position, outcome, reason="uncertain" if outcome == "attention" else "none")
                return outcome
            try:
                source = store.record_analysis(position, source_path)
            except ManifestError:
                store.settle(position, "attention", reason="invalid")
                return "attention"
            row = source.photos[0]
            if source.run_errors or row.errors or source.scan_status != "ready" or row.scan_state not in {"ready", "noop"} or row.uuid is None:
                store.settle(position, "attention", reason="invalid")
                if {error["code"] for error in source.run_errors + row.errors} & {"PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED"}:
                    store.fail_closed("permission")
                return "attention"
            if row.confidence is None or not math.isfinite(row.confidence) or row.confidence < CONFIDENCE_THRESHOLD:
                store.settle(position, "attention", reason="low_confidence")
                return "attention"
            with store._lock:
                enabled = store._authorization is not None
            if not enabled:
                store.release(position)
                return "paused"
            try:
                current = _safe_photo_metadata(self.bridge_factory().read(row.photos_local_identifier), row.photos_local_identifier)
            except (PhotosAccessError, PhotoScriptPermissionError):
                store.settle(position, "attention", reason="failed")
                store.fail_closed("permission")
                return "attention"
            except Exception:
                store.settle(position, "attention", reason="failed")
                return "attention"
            if current is None or current.uuid.casefold() != row.uuid.casefold():
                store.settle(position, "attention", reason="invalid")
                return "attention"
            keywords = proposed_keywords(current.existing_keywords, row.proposed_keywords)
            caption = row.proposed_caption if store.config.include_caption and not current.description else None
            if not keywords and not caption:
                store.settle(position, "no_change")
                return "no_change"
            with store._lock:
                authorization_id = store._authorization
                if authorization_id is None:
                    store.release(position)
                    return "paused"
                authorization = _authorization(store.path, authorization_id)
                identity = {"campaign_id": store.metadata["campaign_id"], "position": position, "source_run_id": source.run_id, "source_digest": source.scan_digest, "policy_version": POLICY_VERSION}
                decision_id = _digest(identity)
                admission = {
                    "version": 1, **identity, "decision_id": decision_id,
                    "authorization_id": authorization_id, "authorization_digest": _digest(authorization),
                    "selection_digest": _digest({"keywords": keywords, "caption": caption}),
                }
                # The lock is the pause linearization point. After this fsync,
                # the accepted operation finishes even when the toggle is OFF.
                _write(path / "decision.json", admission)
            try:
                reviewed_path = self.review_runner(source_path, {row.uuid: keywords}, {row.uuid: caption} if caption else {})
                reviewed_path = Path(reviewed_path)
                if reviewed_path.parent.parent != store.path.parent.parent / "runs" or reviewed_path.name != "manifest.json":
                    raise CampaignStorageError("automatic review escaped run storage")
                reviewed = load_manifest(reviewed_path.parent)
                _write(path / "reviewed.json", {"run_name": reviewed_path.parent.name, "run_id": reviewed.run_id, "review_digest": reviewed.review_decision_digest})
                _write(reviewed_path.parent / "autonomy.json", {
                    "version": 1, "campaign_id": store.metadata["campaign_id"], "position": position,
                    "decision_id": decision_id, "authorization_id": authorization_id,
                    "authorization_digest": _digest(authorization), "admission_digest": _digest(admission),
                })
                # Mandatory authorization linkage is checked before run_apply
                # may instantiate an adapter or invoke any metadata mutation.
                autonomous_run_provenance(reviewed_path)
                result = self.apply_runner(reviewed_path)
                outcome = _receipt_outcome(reviewed_path)
                if result.exit_code not in {0, 1}:
                    outcome = "attention"
                store.settle(position, outcome, reason="uncertain" if outcome == "attention" else "none")
                if set(result.error_codes) & {"PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED"}:
                    store.fail_closed("permission")
                elif set(result.error_codes) & {"LOCK_OR_MANIFEST_FAILED", "MANIFEST_INVALID", "MUTATION_EVIDENCE_INVALID", "REVIEW_PROVENANCE_INVALID"}:
                    store.fail_closed("storage")
                return outcome
            except (OSError, ManifestError) as error:
                raise CampaignStorageError("automatic apply evidence is unavailable") from error
            except CampaignStorageError:
                raise
            except Exception:
                store.settle(position, "attention", reason="uncertain")
                return "attention"
