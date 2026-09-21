"""Strict private artifacts carrying advanced per-photo rescan options."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .adapters import validate_ollama_model_name
from .queue_decision import (
    QueueDecisionArtifactError,
    _canonical_uuid,
    _read_private_file,
    _reject_constant,
    _strict_object,
)


MAX_ADDITIONAL_INFORMATION_CHARS = 1_024
MAX_ANALYSIS_PROMPT_CHARS = 4_096
RESCAN_PROFILES = frozenset({"free_local", "balanced", "conservative"})
_EXPECTED_KEYS = {
    "session_id", "item_id", "revision", "decision_id", "model", "profile", "layers",
    "additional_information", "analysis_prompt", "reset_prompt", "reset_edits",
}
_LAYER_KEYS = {
    "places", "documents_text", "people_accessories", "semantic_normalization",
}
_DENIED_TERMS = (
    "password", "passwd", "token", "api key", "api_key", "secret", "credential",
    "bearer", "cookie", "contraseña", "contrasena", "latitud", "longitud",
    "latitude", "longitude",
)
_COORDINATE_PATTERN = re.compile(
    r"[-+]?\d{1,3}(?:\.\d+)?\s*[,;]\s*[-+]?\d{1,3}(?:\.\d+)?",
)


class QueueRescanArtifactError(QueueDecisionArtifactError):
    """Raised when an advanced-rescan artifact is unsafe or malformed."""


def _safe_optional_text(value: object, *, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise QueueRescanArtifactError(f"{label} is invalid")
    lowered = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    if any(term in lowered for term in _DENIED_TERMS) or _COORDINATE_PATTERN.search(lowered):
        raise QueueRescanArtifactError(f"{label} contains sensitive data")
    return value


@dataclass(frozen=True, slots=True)
class QueueRescanLayers:
    places: bool
    documents_text: bool
    people_accessories: bool
    semantic_normalization: bool

    @classmethod
    def from_dict(cls, value: object) -> QueueRescanLayers:
        if type(value) is not dict or set(value) != _LAYER_KEYS or any(
            type(value[key]) is not bool for key in _LAYER_KEYS
        ):
            raise QueueRescanArtifactError("rescan layers are invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class QueueRescanArtifact:
    session_id: str
    item_id: str
    revision: int
    decision_id: str
    model: str
    profile: str
    layers: QueueRescanLayers
    additional_information: str | None
    analysis_prompt: str | None
    reset_prompt: bool
    reset_edits: bool

    @classmethod
    def from_dict(cls, value: object) -> QueueRescanArtifact:
        if type(value) is not dict or set(value) != _EXPECTED_KEYS:
            raise QueueRescanArtifactError("rescan keys do not match the schema")
        session_id = _canonical_uuid(value["session_id"], "session_id")
        item_id = _canonical_uuid(value["item_id"], "item_id")
        decision_id = _canonical_uuid(value["decision_id"], "decision_id")
        revision = value["revision"]
        if type(revision) is not int or not 1 <= revision <= 2**31 - 1:
            raise QueueRescanArtifactError("revision is invalid")
        try:
            model = validate_ollama_model_name(value["model"])
        except Exception as error:
            raise QueueRescanArtifactError("rescan model is invalid") from error
        if "cloud" in model.casefold():
            raise QueueRescanArtifactError("rescan model is invalid")
        profile = value["profile"]
        if type(profile) is not str or profile not in RESCAN_PROFILES:
            raise QueueRescanArtifactError("rescan profile is invalid")
        reset_prompt = value["reset_prompt"]
        if type(reset_prompt) is not bool:
            raise QueueRescanArtifactError("reset_prompt is invalid")
        reset_edits = value["reset_edits"]
        if type(reset_edits) is not bool:
            raise QueueRescanArtifactError("reset_edits is invalid")
        analysis_prompt = _safe_optional_text(
            value["analysis_prompt"],
            label="analysis_prompt",
            maximum=MAX_ANALYSIS_PROMPT_CHARS,
        )
        if reset_prompt and analysis_prompt is not None:
            raise QueueRescanArtifactError("reset_prompt conflicts with analysis_prompt")
        return cls(
            session_id=session_id,
            item_id=item_id,
            revision=revision,
            decision_id=decision_id,
            model=model,
            profile=profile,
            layers=QueueRescanLayers.from_dict(value["layers"]),
            additional_information=_safe_optional_text(
                value["additional_information"],
                label="additional_information",
                maximum=MAX_ADDITIONAL_INFORMATION_CHARS,
            ),
            analysis_prompt=analysis_prompt,
            reset_prompt=reset_prompt,
            reset_edits=reset_edits,
        )


def load_queue_rescan(
    root: Path,
    *,
    session_id: str,
    item_id: str,
    revision: int,
    decision_id: str,
) -> QueueRescanArtifact:
    """Load one rescan path derived entirely from trusted opaque identifiers."""
    session_id = _canonical_uuid(session_id, "session_id")
    item_id = _canonical_uuid(item_id, "item_id")
    decision_id = _canonical_uuid(decision_id, "decision_id")
    if type(revision) is not int or not 1 <= revision <= 2**31 - 1:
        raise QueueRescanArtifactError("revision is invalid")
    path = Path(root) / session_id / "rescans" / f"{decision_id}.json"
    try:
        value = json.loads(
            _read_private_file(path).decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        artifact = QueueRescanArtifact.from_dict(value)
    except QueueRescanArtifactError:
        raise
    except QueueDecisionArtifactError as error:
        raise QueueRescanArtifactError("rescan file is unsafe") from error
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise QueueRescanArtifactError("rescan file is invalid") from error
    if (
        artifact.session_id != session_id
        or artifact.item_id != item_id
        or artifact.revision != revision
        or artifact.decision_id != decision_id
    ):
        raise QueueRescanArtifactError("rescan does not match the requested action")
    return artifact
