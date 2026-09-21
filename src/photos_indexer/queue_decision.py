"""Strict private artifacts carrying one queue review decision.

The JSONL control plane contains only opaque identifiers.  User-edited
keywords and captions cross the Swift/Python boundary through this private,
bounded file and are revalidated before a schema-4 review is materialized.
"""

from __future__ import annotations

import json
import os
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_DECISION_BYTES = 64 * 1024
MAX_KEYWORDS = 8
MAX_KEYWORD_CHARS = 128
MAX_CAPTION_CHARS = 240
_EXPECTED_KEYS = {
    "session_id",
    "item_id",
    "revision",
    "decision_id",
    "approved_keywords",
    "approved_caption",
}
_TRUSTED_SYSTEM_SYMLINKS = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


class QueueDecisionArtifactError(ValueError):
    """Raised when a queue decision artifact is unsafe or malformed."""


def _canonical_uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise QueueDecisionArtifactError(f"{label} is invalid")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise QueueDecisionArtifactError(f"{label} is invalid") from error
    if str(parsed) != value:
        raise QueueDecisionArtifactError(f"{label} is invalid")
    return value


def _safe_text(value: object, *, label: str, maximum: int, allow_empty: bool) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value.strip()):
        raise QueueDecisionArtifactError(f"{label} is invalid")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise QueueDecisionArtifactError(f"{label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class QueueDecisionArtifact:
    session_id: str
    item_id: str
    revision: int
    decision_id: str
    approved_keywords: tuple[str, ...]
    approved_caption: str | None

    @classmethod
    def from_dict(cls, value: object) -> QueueDecisionArtifact:
        if type(value) is not dict or set(value) != _EXPECTED_KEYS:
            raise QueueDecisionArtifactError("decision keys do not match the schema")
        session_id = _canonical_uuid(value["session_id"], "session_id")
        item_id = _canonical_uuid(value["item_id"], "item_id")
        decision_id = _canonical_uuid(value["decision_id"], "decision_id")
        revision = value["revision"]
        if type(revision) is not int or not 1 <= revision <= 2**31 - 1:
            raise QueueDecisionArtifactError("revision is invalid")
        keywords = value["approved_keywords"]
        if type(keywords) is not list or len(keywords) > MAX_KEYWORDS:
            raise QueueDecisionArtifactError("approved keywords are invalid")
        approved_keywords = tuple(
            _safe_text(keyword, label="approved keyword", maximum=MAX_KEYWORD_CHARS, allow_empty=False)
            for keyword in keywords
        )
        if len({keyword.casefold() for keyword in approved_keywords}) != len(approved_keywords):
            raise QueueDecisionArtifactError("approved keywords contain duplicates")
        caption_value = value["approved_caption"]
        approved_caption = (
            None
            if caption_value is None
            else _safe_text(
                caption_value,
                label="approved caption",
                maximum=MAX_CAPTION_CHARS,
                allow_empty=False,
            )
        )
        return cls(
            session_id=session_id,
            item_id=item_id,
            revision=revision,
            decision_id=decision_id,
            approved_keywords=approved_keywords,
            approved_caption=approved_caption,
        )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise QueueDecisionArtifactError("decision JSON contains duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise QueueDecisionArtifactError(f"JSON constant {value!r} is not allowed")


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
            raise QueueDecisionArtifactError("decision path is unsafe") from error
        if not stat.S_ISLNK(details.st_mode):
            continue
        try:
            resolved = current.resolve(strict=True)
        except OSError as error:
            raise QueueDecisionArtifactError("decision path is unsafe") from error
        if _TRUSTED_SYSTEM_SYMLINKS.get(current) != resolved:
            raise QueueDecisionArtifactError("decision path must not traverse symlinks")


def _private_directory(path: Path) -> os.stat_result:
    try:
        details = os.lstat(path)
    except OSError as error:
        raise QueueDecisionArtifactError("decision directory is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise QueueDecisionArtifactError("decision directory must be private")
    return details


def _read_private_file(path: Path) -> bytes:
    _reject_symlinked_ancestors(path.parent)
    parent = _private_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(path.parent, flags)
    except OSError as error:
        raise QueueDecisionArtifactError("decision directory is unsafe") from error
    try:
        opened_parent = os.fstat(parent_descriptor)
        if (opened_parent.st_dev, opened_parent.st_ino) != (parent.st_dev, parent.st_ino):
            raise QueueDecisionArtifactError("decision directory changed while opening")
        try:
            details = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise QueueDecisionArtifactError("decision file is unavailable") from error
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size > MAX_DECISION_BYTES
        ):
            raise QueueDecisionArtifactError("decision file must be private")
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            raise QueueDecisionArtifactError("decision file changed while opening") from error
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
                or opened.st_nlink != 1
                or opened.st_size > MAX_DECISION_BYTES
            ):
                raise QueueDecisionArtifactError("decision file changed while opening")
            payload = os.read(descriptor, MAX_DECISION_BYTES + 1)
            if len(payload) > MAX_DECISION_BYTES:
                raise QueueDecisionArtifactError("decision file exceeds the size limit")
            return payload
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def load_queue_decision(
    root: Path,
    *,
    session_id: str,
    item_id: str,
    revision: int,
    decision_id: str,
) -> QueueDecisionArtifact:
    """Load the one decision path derived entirely from trusted identifiers."""
    session_id = _canonical_uuid(session_id, "session_id")
    item_id = _canonical_uuid(item_id, "item_id")
    decision_id = _canonical_uuid(decision_id, "decision_id")
    if type(revision) is not int or not 1 <= revision <= 2**31 - 1:
        raise QueueDecisionArtifactError("revision is invalid")
    try:
        root = Path(root)
    except (TypeError, ValueError) as error:
        raise QueueDecisionArtifactError("decision root is invalid") from error
    path = root / session_id / "decisions" / f"{decision_id}.json"
    try:
        value = json.loads(
            _read_private_file(path).decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        artifact = QueueDecisionArtifact.from_dict(value)
    except QueueDecisionArtifactError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise QueueDecisionArtifactError("decision file is invalid") from error
    if (
        artifact.session_id != session_id
        or artifact.item_id != item_id
        or artifact.revision != revision
        or artifact.decision_id != decision_id
    ):
        raise QueueDecisionArtifactError("decision does not match the requested action")
    return artifact
