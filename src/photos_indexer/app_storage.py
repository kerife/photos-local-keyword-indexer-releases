"""Private, user-owned storage locations for the distributed macOS application."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import (
    MANIFEST_FILENAME,
    MAX_MANIFEST_BYTES,
    ManifestError,
    ScanManifest,
    decode_strict_json,
    reviewed_rows_match_source,
    write_preview_csv,
)


APP_STORAGE_NAME = "Photos Local Keyword Indexer"
IMPORT_STATUS_FILENAME = "import-status.json"
MAX_SETTINGS_NESTING = 128


class StorageError(ValueError):
    """Raised when an application-storage path is unsafe or malformed."""


def _settings_text_is_safe(value: object) -> bool:
    # Keep preferences safely below the JSON encoder/parser recursion boundary.
    # This is deliberately iterative so a hostile or malformed UI value cannot
    # overflow this validation step before we return a normal storage error.
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_SETTINGS_NESTING:
            return False
        if isinstance(current, str):
            if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in current):
                return False
        elif isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    return False
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return True


def _reject_symlink_components(path: Path, *, root: Path | None = None) -> None:
    """Reject symlinks below the app-owned root, not system path aliases."""
    path = Path(path)
    if root is None:
        current = Path(path.anchor) if path.is_absolute() else Path()
        parts = path.parts[1:] if path.is_absolute() else path.parts
    else:
        root = Path(root)
        try:
            parts = path.relative_to(root).parts
        except ValueError as error:
            raise StorageError("application storage path is outside its home") from error
        current = root
        try:
            root_details = os.lstat(current)
        except FileNotFoundError:
            root_details = None
        if root_details is not None and stat.S_ISLNK(root_details.st_mode):
            raise StorageError("application storage directory is unsafe")
    for part in parts:
        current /= part
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode):
            raise StorageError("application storage directory is unsafe")


def _ensure_private_directory(path: Path, *, root: Path | None = None) -> None:
    """Create a user-owned directory without following a final symlink."""
    _reject_symlink_components(path, root=root)
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise StorageError("application storage directory is unsafe")
    os.chmod(path, 0o700)


def _read_private_regular_file(path: Path) -> bytes:
    try:
        initial = os.lstat(path)
    except OSError as error:
        raise StorageError("manifest does not exist") from error
    if (
        not stat.S_ISREG(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or initial.st_uid != os.getuid()
        or stat.S_IMODE(initial.st_mode) != 0o600
        or initial.st_nlink != 1
        or initial.st_size > MAX_MANIFEST_BYTES
    ):
        raise StorageError("manifest must be a private regular file")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise StorageError("manifest is not readable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > MAX_MANIFEST_BYTES
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise StorageError("manifest changed while being opened")
        chunks: list[bytes] = []
        remaining = MAX_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise StorageError("manifest exceeds the size limit")
        return payload
    finally:
        os.close(descriptor)


def _atomic_private_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def _validate_manifest_payload(payload: bytes) -> ScanManifest:
    try:
        value = decode_strict_json(payload.decode("utf-8"))
        return ScanManifest.from_dict(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError, TypeError, RecursionError, ValueError) as error:
        raise StorageError("manifest is invalid") from error


def _private_source_for_review(
    reviewed_manifest_path: Path,
    reviewed: ScanManifest,
) -> tuple[bytes, ScanManifest] | None:
    """Find a matching private dry-run sibling for an imported review."""
    if reviewed.schema_version not in {3, 4} or reviewed.reviewed_from_run_id is None:
        return None
    runs_root = reviewed_manifest_path.parent.parent
    try:
        root_details = os.lstat(runs_root)
        reviewed_details = os.lstat(reviewed_manifest_path.parent)
    except OSError:
        return None
    if any(
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
        for details in (root_details, reviewed_details)
    ):
        return None
    try:
        candidates = list(runs_root.iterdir())
    except OSError:
        return None
    for candidate in candidates:
        if candidate == reviewed_manifest_path.parent:
            continue
        try:
            details = os.lstat(candidate)
            if (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o700
            ):
                continue
            payload = _read_private_regular_file(candidate / MANIFEST_FILENAME)
            source = _validate_manifest_payload(payload)
        except (OSError, StorageError):
            continue
        if (
            source.schema_version in {1, 2}
            and source.run_id == reviewed.reviewed_from_run_id
            and source.scan_status in {"ready", "ready_with_errors"}
            and source.scan_digest == reviewed.source_scan_digest
            and reviewed_rows_match_source(source, reviewed)
        ):
            return payload, source
    return None


def _reviewed_manifest_is_pristine_for_import(reviewed: ScanManifest) -> bool:
    """Return whether an imported review is still an approval hand-off."""
    if reviewed.schema_version not in {3, 4}:
        return False
    for photo in reviewed.photos:
        if (
            photo.apply_state != "not_run"
            or photo.rollback_state != "not_run"
            or photo.applied_keywords
            or photo.rolled_back_keywords
            or photo.applied_caption is not None
            or photo.mutation_digest is not None
            or photo.rollback_digest is not None
        ):
            return False
        expected_caption_state = "proposed" if photo.proposed_caption else "not_requested"
        if photo.caption_state != expected_caption_state:
            return False
    return True


def _write_import_status(run_dir: Path, *, mode: str, reason: str, source_run_id: str | None = None) -> None:
    value: dict[str, str] = {"mode": mode, "reason": reason}
    if source_run_id is not None:
        value["source_run_id"] = source_run_id
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _atomic_private_write(run_dir / IMPORT_STATUS_FILENAME, payload)


def _remove_new_private_run(path: Path) -> None:
    """Best-effort cleanup for a run created by a failed import."""
    try:
        details = os.lstat(path)
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
        shutil.rmtree(path)
    except OSError:
        # Preserve the original import error; a later private-storage cleanup
        # can remove a run whose directory became temporarily unavailable.
        pass


@dataclass(frozen=True, slots=True)
class AppStorage:
    """The app-specific locations below a supplied user home directory."""

    home: Path

    @classmethod
    def default(cls) -> "AppStorage":
        return cls.for_home(Path.home())

    @classmethod
    def for_home(cls, home: Path) -> "AppStorage":
        try:
            return cls(Path(home))
        except (OSError, TypeError, ValueError) as error:
            raise StorageError("home path is invalid") from error

    @property
    def application_support_root(self) -> Path:
        return self.home / "Library" / "Application Support" / APP_STORAGE_NAME

    @property
    def runs_root(self) -> Path:
        return self.application_support_root / "runs"

    @property
    def settings_path(self) -> Path:
        return self.application_support_root / "settings.json"

    @property
    def cache_root(self) -> Path:
        return self.home / "Library" / "Caches" / APP_STORAGE_NAME

    @property
    def exports_root(self) -> Path:
        return self.cache_root / "exports"

    @property
    def logs_root(self) -> Path:
        return self.home / "Library" / "Logs" / APP_STORAGE_NAME

    def ensure_directories(self) -> None:
        for path in (
            self.application_support_root,
            self.runs_root,
            self.cache_root,
            self.exports_root,
            self.logs_root,
        ):
            _ensure_private_directory(path, root=self.home)

    def load_settings(self) -> dict[str, Any]:
        """Return local UI preferences, treating an absent file as first launch."""
        _reject_symlink_components(self.settings_path, root=self.home)
        try:
            os.lstat(self.settings_path)
        except FileNotFoundError:
            return {}
        payload = _read_private_regular_file(self.settings_path)
        try:
            value = decode_strict_json(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ManifestError, RecursionError, ValueError) as error:
            raise StorageError("settings are invalid") from error
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value) or not _settings_text_is_safe(value):
            raise StorageError("settings must be a JSON object")
        return value

    def write_settings(self, settings: dict[str, Any]) -> Path:
        """Atomically store app preferences in the private Application Support directory."""
        if not isinstance(settings, dict) or not _settings_text_is_safe(settings):
            raise StorageError("settings must be a JSON object")
        try:
            payload = (json.dumps(
                settings,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n").encode("utf-8")
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as error:
            raise StorageError("settings must be JSON serializable") from error
        if len(payload) > MAX_MANIFEST_BYTES:
            raise StorageError("settings exceed the size limit")
        _ensure_private_directory(self.application_support_root, root=self.home)
        _atomic_private_write(self.settings_path, payload)
        return self.settings_path

    def import_manifest(self, source_manifest: Path) -> Path:
        """Import a manifest and preserve safe review provenance when present."""
        try:
            source_manifest = Path(source_manifest)
        except (OSError, TypeError, ValueError) as error:
            raise StorageError("manifest path is invalid") from error
        if source_manifest.name != MANIFEST_FILENAME:
            raise StorageError("only manifest.json files can be imported")
        payload = _read_private_regular_file(source_manifest)
        manifest = _validate_manifest_payload(payload)
        pristine_review = _reviewed_manifest_is_pristine_for_import(manifest)
        source = _private_source_for_review(source_manifest, manifest) if pristine_review else None
        self.ensure_directories()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        imported_run = self.runs_root / f"{timestamp}-imported-{uuid.uuid4().hex[:8]}"
        _ensure_private_directory(imported_run, root=self.home)
        created_runs = [imported_run]
        try:
            _atomic_private_write(imported_run / MANIFEST_FILENAME, payload)
            write_preview_csv(imported_run, manifest)
            if source is not None:
                source_payload, source_manifest = source
                source_run = self.runs_root / f"source-{source_manifest.run_id[:8]}-{uuid.uuid4().hex[:8]}"
                _ensure_private_directory(source_run, root=self.home)
                created_runs.append(source_run)
                _atomic_private_write(source_run / MANIFEST_FILENAME, source_payload)
                write_preview_csv(source_run, source_manifest)
                _write_import_status(
                    imported_run,
                    mode="mutation_ready",
                    reason="SOURCE_RUN_IMPORTED",
                    source_run_id=source_manifest.run_id,
                )
            elif manifest.schema_version in {3, 4}:
                _write_import_status(
                    imported_run,
                    mode="query_only",
                    reason="SOURCE_RUN_UNAVAILABLE" if pristine_review else "REVIEW_NOT_PRISTINE",
                    source_run_id=manifest.reviewed_from_run_id,
                )
            else:
                _write_import_status(imported_run, mode="review_required", reason="REVIEW_REQUIRED")
        except BaseException:
            for created_run in reversed(created_runs):
                _remove_new_private_run(created_run)
            raise
        return imported_run
