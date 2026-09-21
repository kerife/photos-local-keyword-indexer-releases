from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
import uuid as uuid_module
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .caption_policy import sanitize_caption
from .places import sanitize_place_context
from .taxonomy import (
    SUPPORTED_POLICY_HASHES,
    TAXONOMY_ID,
    TAXONOMY_SHA256,
    canonical_keyword_key,
    normalize_existing_keywords,
    proposed_keywords,
)


MANIFEST_FILENAME = "manifest.json"
PREVIEW_FILENAME = "preview.csv"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SELECTION_COUNT = 500
LOCAL_OLLAMA_API_ENDPOINT = "http://127.0.0.1:11434/api"
SCAN_STATES = frozenset({"ready", "noop", "analysis_failed"})
MODEL_REASONS = frozenset({"single_policy", "no_location", "location_context"})
SCAN_STATUSES = frozenset({"ready", "ready_with_errors", "failed"})
APPLY_STATES = frozenset({"not_run", "writing", "verified", "noop", "failed", "uncertain", "cancelled"})
ROLLBACK_STATES = frozenset({"not_run", "removing", "verified_removed", "already_absent", "casing_conflict", "failed", "uncertain", "cancelled"})
CAPTION_STATES = frozenset({"not_requested", "proposed", "verified", "preserved", "failed", "uncertain", "removed"})
ACCESS_STATES = frozenset({"authorized", "limited"})
SELECTION_STRATEGIES = frozenset({"recent", "random", "targeted"})
TECHNICAL_TRACE_DURATION_KEYS = frozenset({
    "metadata", "export", "context", "inference", "postprocess", "total",
})
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
PLACE_EVIDENCE_STATES = frozenset({
    "not_applicable",
    "context_available",
    "discarded_by_visual_evidence",
    "visually_confirmed",
})
_PROMPT_VERSION_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CONCRETE_COORDINATE_PATTERN = re.compile(
    r"(?i)\b(?:latitud|latitude)\s*[=:]?\s*[+-]?\d{1,2}(?:[.,]\d+)"
    r".{0,48}\b(?:longitud|longitude)\s*[=:]?\s*[+-]?\d{1,3}(?:[.,]\d+)"
)
_OLLAMA_MODEL_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
)
_OLLAMA_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
MAX_ERRORS_PER_SCOPE = 16
# macOS exposes these stable system aliases; they are safe to traverse, while
# symlinks created below them remain rejected by ``_reject_symlinked_ancestors``.
_TRUSTED_SYSTEM_SYMLINKS = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}
ERROR_CATALOG = {
    "metadata": frozenset({
        "READ_FAILED",
        "IDENTITY_MISMATCH",
        "PHOTOS_ACCESS_DENIED",
        "PHOTOS_AUTOMATION_DENIED",
        "PHOTOSCRIPT_UNAVAILABLE",
    }),
    "export": frozenset({"EXPORT_FAILED", "PHOTOSCRIPT_UNAVAILABLE"}),
    "analysis": frozenset({
        "ANALYSIS_FAILED", "LOW_CONFIDENCE", "OLLAMA_UNAVAILABLE", "OLLAMA_VERSION_OLD",
        "OLLAMA_NO_VISION", "OLLAMA_MODEL_MISSING", "OLLAMA_RESPONSE_INVALID",
        "OLLAMA_REQUEST_FAILED", "VISION_IMAGE_INVALID", "MODEL_INVALID",
    }),
    "cleanup": frozenset({"EXPORT_DELETE_FAILED"}),
    "workspace": frozenset({"WORKSPACE_FAILED", "CANCELLED"}),
    "apply": frozenset({
        "INTERRUPTED_WRITE", "APPLY_FAILED", "APPLY_READ_FAILED", "IDENTITY_MISMATCH",
        "WRITE_UNCERTAIN", "KEYWORD_WRITE_UNCERTAIN", "CAPTION_WRITE_UNCERTAIN", "CANCELLED",
        "PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED", "PHOTOSCRIPT_UNAVAILABLE",
    }),
    "rollback": frozenset({
        "INTERRUPTED_REMOVAL", "ROLLBACK_FAILED", "IDENTITY_MISMATCH", "CASING_CONFLICT",
        "APPLIED_KEYWORD_MISSING", "REMOVAL_UNCERTAIN", "CANCELLED", "PHOTOS_ACCESS_DENIED",
        "PHOTOS_AUTOMATION_DENIED", "PHOTOSCRIPT_UNAVAILABLE",
    }),
}


def safe_projection_text(value: str, *, csv_cell: bool = False) -> str:
    """Make untrusted Photos text inert in terminal/CSV projections only."""
    sanitized = "".join(
        character if unicodedata.category(character) not in {"Cc", "Cf", "Cs"} else "�"
        for character in value
    )
    if csv_cell and sanitized.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + sanitized
    return sanitized


class ManifestError(ValueError):
    """Raised when a manifest is malformed, unsafe, or fails integrity validation."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate names before a JSON object loses parser provenance."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ManifestError("JSON objects must not contain duplicate keys")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ManifestError(f"JSON constant {value!r} is not allowed")


def decode_strict_json(value: str) -> object:
    """Decode JSON whose objects have one unambiguous value per key."""
    return json.loads(
        value,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


def _json_utf8_bytes(value: object, **kwargs: object) -> bytes:
    """Encode JSON while preserving valid values with isolated surrogates."""
    options = {"ensure_ascii": False, **kwargs}
    serialized = json.dumps(value, **options)
    try:
        return serialized.encode("utf-8")
    except UnicodeEncodeError:
        options["ensure_ascii"] = True
        return json.dumps(value, **options).encode("utf-8")


def _require_keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ManifestError(f"{label} keys do not match the schema")
    return value


def _strict_bool_or_none(value: object, label: str) -> bool | None:
    if value is None or type(value) is bool:
        return value
    raise ManifestError(f"{label} must be boolean or null")


def _strict_float_or_none(value: object, label: str) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ManifestError(f"{label} must be a number or null")
    try:
        return float(value)
    except OverflowError as error:
        raise ManifestError(f"{label} must be a finite number") from error


def _keyword_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(keyword, str) for keyword in value):
        raise ManifestError(f"{label} must be a list of strings")
    return normalize_existing_keywords(value)


def _keyword_collection(value: object, label: str) -> list[str]:
    """Validate direct-record keyword collections before normalizing them."""
    if not isinstance(value, (list, tuple)) or any(not isinstance(keyword, str) for keyword in value):
        raise ManifestError(f"{label} must be a list or tuple of strings")
    return normalize_existing_keywords(value)


def _error_list(value: object, label: str = "errors") -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_ERRORS_PER_SCOPE:
        raise ManifestError(f"{label} must be a bounded list")
    errors: list[dict[str, str]] = []
    for error in value:
        item = _require_keys(error, {"stage", "code"}, "error")
        if (
            type(item["stage"]) is not str
            or type(item["code"]) is not str
            or item["stage"] not in ERROR_CATALOG
            or item["code"] not in ERROR_CATALOG[item["stage"]]
        ):
            raise ManifestError("error stage and code must be an allowed pair")
        errors.append({"stage": item["stage"], "code": item["code"]})
    return errors


def _strict_nonnegative_counters(value: object, expected: set[str], label: str) -> dict[str, Any]:
    item = _require_keys(value, expected, label)
    for name, count in item.items():
        if type(count) is not int or count < 0:
            raise ManifestError(f"{label}.{name} must be a nonnegative integer")
    return dict(item)


def _selection(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError("selection keys do not match the schema")
    expected = {"requested", "eligible", "screenshots_excluded", "access"}
    keys = set(value)
    optional = {"strategy", "captions_requested"}
    if not expected <= keys or keys - expected - optional:
        raise ManifestError("selection keys do not match the schema")
    item = dict(value)
    for name in ("requested", "eligible", "screenshots_excluded"):
        if type(item[name]) is not int or item[name] < 0:
            raise ManifestError(f"selection.{name} must be a nonnegative integer")
    if item["requested"] > MAX_SELECTION_COUNT:
        raise ManifestError("selection.requested exceeds the operational limit")
    if type(item["access"]) is not str or item["access"] not in ACCESS_STATES:
        raise ManifestError("selection.access must be an allowed Photos access state")
    if item["eligible"] > item["requested"]:
        raise ManifestError("selection.eligible cannot exceed requested")
    if "strategy" in item and (type(item["strategy"]) is not str or item["strategy"] not in SELECTION_STRATEGIES):
        raise ManifestError("selection.strategy must be an allowed selection strategy")
    if "captions_requested" in item and type(item["captions_requested"]) is not bool:
        raise ManifestError("selection.captions_requested must be boolean")
    return item


@dataclass(slots=True)
class TechnicalTrace:
    """Sanitized, immutable scan evidence safe for a local inspector."""

    prompt_effective: str
    prompt_version: str
    prompt_sha256: str
    used_gps: bool
    used_apple_maps: bool
    used_landmark: bool
    place_context: tuple[str, ...]
    durations_ms: dict[str, int]
    ollama_version: str | None = None
    place_lookup_state: str = "not_requested"
    place_evidence_state: str = "not_applicable"
    _serialize_place_lookup_state: bool = field(default=True, repr=False, compare=False, kw_only=True)
    _serialize_place_evidence_state: bool = field(default=True, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if (
            type(self.prompt_effective) is not str
            or not self.prompt_effective
            or len(self.prompt_effective) > 16_384
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in self.prompt_effective)
            or _CONCRETE_COORDINATE_PATTERN.search(self.prompt_effective) is not None
        ):
            raise ManifestError("technical trace prompt must be sanitized and bounded")
        if (
            type(self.prompt_version) is not str
            or _PROMPT_VERSION_PATTERN.fullmatch(self.prompt_version) is None
        ):
            raise ManifestError("technical trace prompt version is invalid")
        expected_hash = hashlib.sha256(self.prompt_effective.encode("utf-8")).hexdigest()
        if type(self.prompt_sha256) is not str or self.prompt_sha256 != expected_hash:
            raise ManifestError("technical trace prompt hash does not match")
        if self.ollama_version is not None and (
            type(self.ollama_version) is not str
            or _OLLAMA_VERSION_PATTERN.fullmatch(self.ollama_version) is None
        ):
            raise ManifestError("technical trace Ollama version is invalid")
        if any(type(value) is not bool for value in (self.used_gps, self.used_apple_maps, self.used_landmark)):
            raise ManifestError("technical trace usage flags must be boolean")
        if not isinstance(self.place_context, (tuple, list)):
            raise ManifestError("technical trace place context must be a bounded string list")
        normalized_context = sanitize_place_context(self.place_context)
        if tuple(self.place_context) != normalized_context:
            raise ManifestError("technical trace place context is not sanitized")
        self.place_context = normalized_context
        if self.used_apple_maps and self.place_lookup_state == "not_requested":
            self.place_lookup_state = "results" if self.place_context else "no_results"
        if type(self.place_lookup_state) is not str or self.place_lookup_state not in PLACE_LOOKUP_STATES:
            raise ManifestError("technical trace place lookup state is invalid")
        if type(self.place_evidence_state) is not str or self.place_evidence_state not in PLACE_EVIDENCE_STATES:
            raise ManifestError("technical trace place evidence state is invalid")
        if not self.used_apple_maps and self.place_lookup_state not in {"not_requested", "no_location"}:
            raise ManifestError("Apple Maps lookup state requires Apple Maps usage")
        if not self.place_context and self.place_evidence_state not in {"not_applicable", "discarded_by_visual_evidence"}:
            raise ManifestError("place evidence state requires place context")
        counters = _strict_nonnegative_counters(
            self.durations_ms,
            set(TECHNICAL_TRACE_DURATION_KEYS),
            "technical_trace.durations_ms",
        )
        if counters["total"] != sum(
            value for name, value in counters.items() if name != "total"
        ):
            raise ManifestError("technical trace total duration must match its stages")
        self.durations_ms = counters

    def to_dict(self) -> dict[str, Any]:
        value = {
            "prompt_effective": self.prompt_effective,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "used_gps": self.used_gps,
            "used_apple_maps": self.used_apple_maps,
            "used_landmark": self.used_landmark,
            "place_context": list(self.place_context),
            "durations_ms": dict(self.durations_ms),
        }
        if self._serialize_place_lookup_state:
            value["place_lookup_state"] = self.place_lookup_state
        if self._serialize_place_evidence_state:
            value["place_evidence_state"] = self.place_evidence_state
        if self.ollama_version is not None:
            value["ollama_version"] = self.ollama_version
        return value

    @classmethod
    def from_dict(cls, value: object) -> "TechnicalTrace":
        if not isinstance(value, Mapping):
            raise ManifestError("technical_trace keys do not match the schema")
        required = {
            "prompt_effective", "prompt_version", "prompt_sha256",
            "used_gps", "used_apple_maps", "used_landmark",
            "place_context", "durations_ms",
        }
        optional = {"ollama_version", "place_lookup_state", "place_evidence_state"}
        if not required <= set(value) or set(value) - required - optional:
            raise ManifestError("technical_trace keys do not match the schema")
        item = value
        has_place_lookup_state = "place_lookup_state" in item
        has_place_evidence_state = "place_evidence_state" in item
        place_context = item["place_context"]
        if not isinstance(place_context, list) or any(type(name) is not str for name in place_context):
            raise ManifestError("technical trace place context must be a bounded string list")
        return cls(
            prompt_effective=item["prompt_effective"],
            prompt_version=item["prompt_version"],
            prompt_sha256=item["prompt_sha256"],
            ollama_version=item.get("ollama_version"),
            used_gps=item["used_gps"],
            used_apple_maps=item["used_apple_maps"],
            used_landmark=item["used_landmark"],
            place_context=tuple(place_context),
            place_lookup_state=item.get("place_lookup_state", "not_requested"),
            place_evidence_state=item.get("place_evidence_state", "not_applicable"),
            durations_ms=item["durations_ms"],
            _serialize_place_lookup_state=has_place_lookup_state,
            _serialize_place_evidence_state=has_place_evidence_state,
        )


@dataclass(slots=True)
class PhotoRecord:
    uuid: str | None
    photos_local_identifier: str
    title: str
    date: datetime
    existing_keywords: list[str]
    proposed_keywords: list[str]
    contains_people: bool | None
    contains_text: bool | None
    confidence: float | None
    model_used: str | None = None
    model_reason: str | None = None
    scan_state: str = "ready"
    apply_state: str = "not_run"
    applied_keywords: list[str] = field(default_factory=list)
    rollback_state: str = "not_run"
    rolled_back_keywords: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    proposed_caption: str | None = None
    applied_caption: str | None = None
    caption_state: str = "not_requested"
    mutation_digest: str | None = None
    rollback_digest: str | None = None
    model_proposed_keywords: list[str] | None = None
    approved_keywords: list[str] | None = None
    keyword_origins: dict[str, str] | None = None
    model_proposed_caption: str | None = None
    approved_caption: str | None = None
    caption_origin: str | None = None
    technical_trace: TechnicalTrace | None = None

    def __post_init__(self) -> None:
        if self.uuid is not None and (not isinstance(self.uuid, str) or not self.uuid):
            raise ManifestError("photo uuid must be a nonempty string or null")
        if self.uuid is not None:
            try:
                uuid_module.UUID(self.uuid)
            except (ValueError, TypeError, AttributeError) as error:
                raise ManifestError("photo uuid must use UUID syntax") from error
        if not isinstance(self.photos_local_identifier, str) or not self.photos_local_identifier or not isinstance(self.title, str):
            raise ManifestError("photo identifiers and title must be strings")
        if not isinstance(self.date, datetime) or self.date.tzinfo is not None:
            raise ManifestError("photo date must be a naive datetime")
        self.existing_keywords = _keyword_collection(self.existing_keywords, "existing_keywords")
        proposed = _keyword_collection(self.proposed_keywords, "proposed_keywords")
        self.proposed_keywords = proposed_keywords(self.existing_keywords, proposed)
        self.applied_keywords = _keyword_collection(self.applied_keywords, "applied_keywords")
        self.rolled_back_keywords = _keyword_collection(self.rolled_back_keywords, "rolled_back_keywords")
        self.contains_people = _strict_bool_or_none(self.contains_people, "contains_people")
        self.contains_text = _strict_bool_or_none(self.contains_text, "contains_text")
        self.confidence = _strict_float_or_none(self.confidence, "confidence")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ManifestError("confidence must be from 0 through 1")
        if self.model_used is not None and (not isinstance(self.model_used, str) or not self.model_used):
            raise ManifestError("model_used must be a nonempty string or null")
        if self.model_reason is not None and (type(self.model_reason) is not str or self.model_reason not in MODEL_REASONS):
            raise ManifestError("model_reason is invalid")
        if (self.model_used is None) != (self.model_reason is None):
            raise ManifestError("model routing fields must both be present or null")
        if (
            type(self.scan_state) is not str
            or type(self.apply_state) is not str
            or type(self.rollback_state) is not str
            or self.scan_state not in SCAN_STATES
            or self.apply_state not in APPLY_STATES
            or self.rollback_state not in ROLLBACK_STATES
        ):
            raise ManifestError("invalid photo state")
        self.errors = _error_list(self.errors)
        if any(error["stage"] == "apply" for error in self.errors) and self.apply_state not in {
            "writing", "uncertain", "failed", "cancelled",
        }:
            raise ManifestError("apply errors require a started apply state")
        if any(error["stage"] == "rollback" for error in self.errors) and self.rollback_state not in {
            "removing", "uncertain", "failed", "cancelled", "casing_conflict",
        }:
            raise ManifestError("rollback errors require a started rollback state")
        if self.apply_state in {"failed", "cancelled"} and not any(
            error["stage"] == "apply" for error in self.errors
        ):
            raise ManifestError("failed or cancelled apply requires an apply-stage error")
        for label, caption in (("proposed_caption", self.proposed_caption), ("applied_caption", self.applied_caption)):
            if caption is not None and (not isinstance(caption, str) or len(caption) > 240):
                raise ManifestError(f"{label} must be a string of at most 240 characters or null")
            if caption is not None and sanitize_caption(caption, allow_contextual_places=True) != caption:
                raise ManifestError(f"{label} does not satisfy the installed caption policy")
        if self.mutation_digest is not None and (
            not isinstance(self.mutation_digest, str)
            or len(self.mutation_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.mutation_digest.lower())
        ):
            raise ManifestError("mutation_digest must be a SHA-256 hex value or null")
        if self.rollback_digest is not None and (
            not isinstance(self.rollback_digest, str)
            or len(self.rollback_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.rollback_digest.lower())
        ):
            raise ManifestError("rollback_digest must be a SHA-256 hex value or null")
        review_fields = (
            self.model_proposed_keywords,
            self.approved_keywords,
            self.keyword_origins,
        )
        if any(value is not None for value in review_fields):
            if any(value is None for value in review_fields):
                raise ManifestError("schema 4 keyword review fields must be complete")
            self.model_proposed_keywords = _keyword_collection(
                self.model_proposed_keywords,
                "model_proposed_keywords",
            )
            self.approved_keywords = _keyword_collection(self.approved_keywords, "approved_keywords")
            if not isinstance(self.keyword_origins, Mapping):
                raise ManifestError("keyword_origins must be an object")
            if set(self.keyword_origins) != set(self.approved_keywords) or any(
                origin not in {"model", "manual"}
                for origin in self.keyword_origins.values()
            ):
                raise ManifestError("keyword_origins must cover approved keywords")
            self.keyword_origins = dict(self.keyword_origins)
            if self.approved_keywords != self.proposed_keywords:
                raise ManifestError("approved keywords must match the apply payload")
        caption_review_fields = (
            self.model_proposed_caption,
            self.approved_caption,
            self.caption_origin,
        )
        if any(value is not None for value in caption_review_fields):
            if self.model_proposed_caption is not None and (
                not isinstance(self.model_proposed_caption, str)
                or len(self.model_proposed_caption) > 240
                or sanitize_caption(self.model_proposed_caption, allow_contextual_places=True)
                != self.model_proposed_caption
            ):
                raise ManifestError("model_proposed_caption is invalid")
            if self.approved_caption is None:
                if self.caption_origin is not None or self.proposed_caption is not None:
                    raise ManifestError("rejected captions cannot enter the apply payload")
            elif self.caption_origin not in {"model", "manual"}:
                raise ManifestError("schema 4 caption origin is invalid")
            elif self.proposed_caption != self.approved_caption:
                raise ManifestError("approved caption must match the apply payload")
        if type(self.caption_state) is not str or self.caption_state not in CAPTION_STATES:
            raise ManifestError("invalid caption state")
        if self.technical_trace is not None and not isinstance(self.technical_trace, TechnicalTrace):
            raise ManifestError("technical_trace must be a technical trace object or null")
        if self.proposed_caption is not None and self.scan_state not in {"ready", "noop"}:
            raise ManifestError("captions require a successfully analyzed photo")
        if self.proposed_caption is not None and self.caption_state == "not_requested":
            raise ManifestError("a proposed caption requires a caption state")
        if self.applied_caption is not None and self.applied_caption != self.proposed_caption:
            raise ManifestError("applied caption must match the reviewed proposal")
        if self.caption_state in {"proposed", "preserved"} and not self.proposed_caption:
            raise ManifestError("caption state requires a reviewed caption")
        if self.caption_state == "preserved" and self.applied_caption is not None:
            raise ManifestError("preserved captions cannot carry an applied caption")
        if self.caption_state == "verified" and not self.applied_caption:
            raise ManifestError("verified caption state requires an applied caption")
        if self.caption_state == "removed" and not self.applied_caption:
            raise ManifestError("removed caption state requires an audited caption")
        if self.applied_caption is not None and self.caption_state not in {"verified", "preserved", "removed", "uncertain"}:
            raise ManifestError("applied caption requires a completed caption state")
        if self.caption_state == "uncertain" and self.applied_caption is None:
            raise ManifestError("uncertain caption state requires an audited caption")
        self.validate_state()

    def validate_state(self) -> None:
        if (
            type(self.scan_state) is not str
            or type(self.apply_state) is not str
            or type(self.rollback_state) is not str
            or type(self.caption_state) is not str
            or self.scan_state not in SCAN_STATES
            or self.apply_state not in APPLY_STATES
            or self.rollback_state not in ROLLBACK_STATES
            or self.caption_state not in CAPTION_STATES
        ):
            raise ManifestError("invalid photo state")
        if self.scan_state == "ready" and (self.uuid is None or not self.proposed_keywords):
            raise ManifestError("ready photos require a UUID and proposals")
        if self.scan_state != "ready" and self.proposed_keywords:
            raise ManifestError("non-ready photos cannot carry proposals")
        if self.scan_state == "analysis_failed" and not any(
            error["stage"] in {"metadata", "export", "analysis"}
            for error in self.errors
        ):
            raise ManifestError("analysis_failed photos require a persisted scan error")
        if self.uuid is None and self.scan_state != "analysis_failed":
            raise ManifestError("a missing photo UUID requires analysis_failed")
        if self.scan_state != "ready" and self.apply_state not in {"not_run", "noop"}:
            if not (self.proposed_caption and not self.proposed_keywords):
                raise ManifestError("non-ready photos cannot enter a mutation state")
        proposed_counts = Counter(self.proposed_keywords)
        applied_counts = Counter(self.applied_keywords)
        if any(applied_counts[value] > proposed_counts[value] for value in applied_counts):
            raise ManifestError("applied keywords must be an exact subset of proposed keywords")
        rolled_counts = Counter(self.rolled_back_keywords)
        if any(rolled_counts[value] > applied_counts[value] for value in rolled_counts):
            raise ManifestError("rolled back keywords must be an exact subset of applied keywords")
        if self.apply_state == "verified":
            if not self.applied_keywords and not self.applied_caption:
                raise ManifestError("verified apply requires an applied keyword or caption")
            allowed_verified_caption_states = {"verified", "preserved", "removed"}
            if self.rollback_state != "not_run":
                allowed_verified_caption_states.add("uncertain")
            if self.proposed_caption is not None and self.caption_state not in allowed_verified_caption_states:
                raise ManifestError("verified apply must finalize the reviewed caption")
        elif self.applied_caption is not None and not (
            self.apply_state == "uncertain"
            and (
                self.caption_state == "uncertain"
                or (self.caption_state == "verified" and self.mutation_digest is not None)
            )
        ):
            raise ManifestError("applied captions require a verified apply")
        elif self.applied_keywords and self.apply_state != "uncertain":
            raise ManifestError("only verified apply can carry applied keywords")
        if self.apply_state != "verified" and self.rolled_back_keywords:
            raise ManifestError("rollback audit requires a verified apply")
        if self.rollback_state == "not_run" and self.rolled_back_keywords:
            raise ManifestError("rollback audit requires a started rollback")
        if self.rollback_state == "not_run" and self.rollback_digest is not None:
            raise ManifestError("rollback receipt requires a started rollback")
        if self.caption_state == "removed" and self.rollback_state == "not_run":
            raise ManifestError("removed captions require a started rollback")
        if self.rollback_state != "not_run" and self.apply_state != "verified":
            raise ManifestError("rollback states require a verified apply")

    def to_dict(self, *, include_routing: bool = False, include_review: bool = False) -> dict[str, Any]:
        value = {
            "uuid": self.uuid,
            "photos_local_identifier": self.photos_local_identifier,
            "title": self.title,
            "date": self.date.isoformat(),
            "date_timezone": None,
            "existing_keywords": self.existing_keywords,
            "proposed_keywords": self.proposed_keywords,
            "contains_people": self.contains_people,
            "contains_text": self.contains_text,
            "confidence": self.confidence,
            "scan_state": self.scan_state,
            "apply_state": self.apply_state,
            "applied_keywords": self.applied_keywords,
            "rollback_state": self.rollback_state,
            "rolled_back_keywords": self.rolled_back_keywords,
            "errors": self.errors,
        }
        if include_routing:
            value["model_used"] = self.model_used
            value["model_reason"] = self.model_reason
        if self.proposed_caption is not None or self.applied_caption is not None or self.caption_state != "not_requested":
            value["proposed_caption"] = self.proposed_caption
            value["applied_caption"] = self.applied_caption
            value["caption_state"] = self.caption_state
        if self.mutation_digest is not None:
            value["mutation_digest"] = self.mutation_digest
        if self.rollback_digest is not None:
            value["rollback_digest"] = self.rollback_digest
        if self.technical_trace is not None:
            value["technical_trace"] = self.technical_trace.to_dict()
        if include_review:
            if (
                self.model_proposed_keywords is None
                or self.approved_keywords is None
                or self.keyword_origins is None
            ):
                raise ManifestError("schema 4 photos require explicit keyword review fields")
            value["model_proposed_keywords"] = self.model_proposed_keywords
            value["approved_keywords"] = self.approved_keywords
            value["keyword_origins"] = self.keyword_origins
            value["model_proposed_caption"] = self.model_proposed_caption
            value["approved_caption"] = self.approved_caption
            value["caption_origin"] = self.caption_origin
        return value

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        include_routing: bool = False,
        include_review: bool = False,
    ) -> "PhotoRecord":
        expected = {
            "uuid", "photos_local_identifier", "title", "date", "date_timezone", "existing_keywords", "proposed_keywords",
            "contains_people", "contains_text", "confidence", "scan_state", "apply_state", "applied_keywords", "rollback_state", "rolled_back_keywords", "errors",
        }
        if include_routing:
            expected |= {"model_used", "model_reason"}
        optional_caption = {
            "proposed_caption", "applied_caption", "caption_state", "mutation_digest", "rollback_digest",
            "technical_trace",
        }
        review_fields = {
            "model_proposed_keywords", "approved_keywords", "keyword_origins",
            "model_proposed_caption", "approved_caption", "caption_origin",
        }
        if include_review:
            expected |= review_fields
        if not isinstance(value, Mapping) or not set(value).issubset(expected | optional_caption):
            raise ManifestError("photo keys do not match the schema")
        if not expected.issubset(value):
            raise ManifestError("photo keys do not match the schema")
        item = value
        if item["date_timezone"] is not None or not isinstance(item["date"], str):
            raise ManifestError("photo date must be naive with a null timezone")
        try:
            date = datetime.fromisoformat(item["date"])
        except ValueError as error:
            raise ManifestError("photo date must be ISO-8601") from error
        return cls(
            uuid=item["uuid"], photos_local_identifier=item["photos_local_identifier"], title=item["title"], date=date,
            existing_keywords=_keyword_list(item["existing_keywords"], "existing_keywords"),
            proposed_keywords=_keyword_list(item["proposed_keywords"], "proposed_keywords"),
            contains_people=item["contains_people"], contains_text=item["contains_text"], confidence=item["confidence"],
            model_used=item.get("model_used"), model_reason=item.get("model_reason"),
            scan_state=item["scan_state"], apply_state=item["apply_state"],
            applied_keywords=_keyword_list(item["applied_keywords"], "applied_keywords"), rollback_state=item["rollback_state"],
            rolled_back_keywords=_keyword_list(item["rolled_back_keywords"], "rolled_back_keywords"),
            errors=_error_list(item["errors"]),
            proposed_caption=item.get("proposed_caption"),
            applied_caption=item.get("applied_caption"),
            caption_state=item.get("caption_state", "not_requested"),
            mutation_digest=item.get("mutation_digest"),
            rollback_digest=item.get("rollback_digest"),
            model_proposed_keywords=(
                _keyword_list(item["model_proposed_keywords"], "model_proposed_keywords")
                if include_review
                else None
            ),
            approved_keywords=(
                _keyword_list(item["approved_keywords"], "approved_keywords")
                if include_review
                else None
            ),
            keyword_origins=(dict(item["keyword_origins"]) if include_review and isinstance(item["keyword_origins"], Mapping) else item.get("keyword_origins")),
            model_proposed_caption=item.get("model_proposed_caption"),
            approved_caption=item.get("approved_caption"),
            caption_origin=item.get("caption_origin"),
            technical_trace=(
                TechnicalTrace.from_dict(item["technical_trace"])
                if "technical_trace" in item
                else None
            ),
        )

    def immutable_dict(self, *, include_routing: bool = False, include_review: bool = False) -> dict[str, Any]:
        value = self.to_dict(include_routing=include_routing, include_review=include_review)
        for field_name in ("apply_state", "applied_keywords", "rollback_state", "rolled_back_keywords", "applied_caption", "caption_state", "mutation_digest", "rollback_digest"):
            value.pop(field_name, None)
        value["errors"] = [error for error in self.errors if error["stage"] not in {"apply", "rollback"}]
        return value


@dataclass(slots=True)
class ScanManifest:
    run_id: str
    created_at: datetime
    app: dict[str, str]
    model: dict[str, str]
    selection: dict[str, Any]
    photos: list[PhotoRecord]
    summary: dict[str, Any]
    run_errors: list[dict[str, str]] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=lambda: {
        "id": TAXONOMY_ID,
        "taxonomy_sha256": TAXONOMY_SHA256,
        "max_keywords": 8,
        "confidence_threshold": 0.0,
    })
    scan_status: str = "ready"
    scan_digest: str = ""
    schema_version: int = 1
    reviewed_from_run_id: str | None = None
    source_scan_digest: str | None = None
    review_decision_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4}:
            raise ManifestError("unsupported manifest schema")
        try:
            uuid_module.UUID(self.run_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise ManifestError("run_id must be a UUID") from error
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ManifestError("created_at must include a timezone")
        if type(self.scan_status) is not str or self.scan_status not in SCAN_STATUSES:
            raise ManifestError("invalid scan_status")
        if (
            not isinstance(self.app, Mapping)
            or set(self.app) != {"name", "version"}
            or any(not isinstance(value, str) for value in self.app.values())
        ):
            raise ManifestError("invalid app object")
        if self.schema_version == 1:
            expected_model_keys = {"name", "ollama_version", "endpoint"}
        elif self.schema_version in {2, 3, 4}:
            expected_model_keys = {"policy", "fast_name", "detailed_name", "ollama_version", "endpoint"}
        else:
            raise ManifestError("unsupported manifest schema")
        if (
            not isinstance(self.model, Mapping)
            or set(self.model) != expected_model_keys
            or any(not isinstance(value, str) for value in self.model.values())
        ):
            raise ManifestError("invalid model object")
        model_name_fields = ("name",) if self.schema_version == 1 else ("fast_name", "detailed_name")
        if any(
            len(self.model[field]) > 128
            or _OLLAMA_MODEL_PATTERN.fullmatch(self.model[field]) is None
            for field in model_name_fields
        ):
            raise ManifestError("invalid Ollama model name")
        if any("cloud" in self.model[field].casefold() for field in model_name_fields):
            raise ManifestError("cloud models are not allowed")
        ollama_version = self.model["ollama_version"]
        if len(ollama_version) > 64 or _OLLAMA_VERSION_PATTERN.fullmatch(ollama_version) is None:
            raise ManifestError("invalid Ollama version")
        if self.model["endpoint"] != LOCAL_OLLAMA_API_ENDPOINT.removesuffix("/api"):
            raise ManifestError("model endpoint must use the fixed Ollama loopback address")
        if self.schema_version in {2, 3, 4} and self.model["policy"] not in {"single", "adaptive"}:
            raise ManifestError("invalid model policy")
        if self.schema_version in {3, 4}:
            try:
                uuid_module.UUID(self.reviewed_from_run_id)
            except (TypeError, ValueError, AttributeError) as error:
                raise ManifestError("reviewed manifest requires a source run UUID") from error
            if self.source_scan_digest is None:
                raise ManifestError("reviewed manifest requires the source scan digest")
        elif self.reviewed_from_run_id is not None:
            raise ManifestError("only reviewed manifests can carry review provenance")
        if self.source_scan_digest is not None:
            if self.schema_version not in {3, 4} or not isinstance(self.source_scan_digest, str) or len(self.source_scan_digest) != 64 or any(character not in "0123456789abcdef" for character in self.source_scan_digest.lower()):
                raise ManifestError("source_scan_digest must be a SHA-256 hex value on reviewed manifests")
        self.selection = _selection(self.selection)
        self.summary = _strict_nonnegative_counters(self.summary, {"ready", "noop", "analysis_failed"}, "summary")
        self.run_errors = _error_list(self.run_errors, "run_errors")
        if (
            not isinstance(self.policy, Mapping)
            or set(self.policy) != {"id", "taxonomy_sha256", "max_keywords", "confidence_threshold"}
        ):
            raise ManifestError("invalid policy object")
        if (
            type(self.policy["id"]) is not str
            or type(self.policy["taxonomy_sha256"]) is not str
            or type(self.policy["max_keywords"]) is not int
            or self.policy["max_keywords"] != 8
            or self.policy["taxonomy_sha256"] not in SUPPORTED_POLICY_HASHES.get(self.policy["id"], frozenset())
        ):
            raise ManifestError("manifest policy does not match the installed policy")
        threshold = self.policy["confidence_threshold"]
        if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
            raise ManifestError("confidence_threshold must be from 0 through 1")
        self.policy = {**self.policy, "confidence_threshold": float(threshold)}
        if not isinstance(self.photos, list) or any(not isinstance(photo, PhotoRecord) for photo in self.photos):
            raise ManifestError("photos must be photo records")
        if self.schema_version in {2, 3, 4}:
            allowed_models = {self.model["fast_name"], self.model["detailed_name"]}
            for photo in self.photos:
                if photo.model_used is not None and photo.model_used not in allowed_models:
                    raise ManifestError("photo model is not part of the scan plan")
        actual_summary = dict(Counter(photo.scan_state for photo in self.photos))
        expected_summary = {state: actual_summary.get(state, 0) for state in ("ready", "noop", "analysis_failed")}
        if self.summary != expected_summary:
            raise ManifestError("summary must match actual photo scan states")
        if self.scan_status in {"ready", "ready_with_errors"} and self.selection["eligible"] != len(self.photos):
            raise ManifestError("successful scans require one row per eligible photo")
        if self.scan_status == "failed" and len(self.photos) > self.selection["eligible"]:
            raise ManifestError("failed scans cannot contain more rows than eligible photos")
        if self.scan_status == "failed" and not self.run_errors:
            raise ManifestError("failed scans require a run error")
        if self.scan_status != "failed" and self.run_errors:
            raise ManifestError("run errors require a failed scan")
        self.validate_mutable_state()
        if self.schema_version == 4:
            if any(
                photo.model_proposed_keywords is None
                or photo.approved_keywords is None
                or photo.keyword_origins is None
                for photo in self.photos
            ):
                raise ManifestError("schema 4 requires explicit review decisions")
            expected_review_digest = self.compute_review_decision_digest()
            if self.review_decision_digest and self.review_decision_digest != expected_review_digest:
                raise ManifestError("review decision digest does not match approved values")
            self.review_decision_digest = expected_review_digest
        elif self.review_decision_digest is not None:
            raise ManifestError("only schema 4 can carry a review decision digest")
        if self.scan_digest and self.scan_digest != self.compute_scan_digest():
            raise ManifestError("scan digest does not match immutable scan data")
        self.scan_digest = self.compute_scan_digest()

    def validate_mutable_state(self) -> None:
        for photo in self.photos:
            photo.validate_state()
        has_row_scan_errors = any(
            error["stage"] not in {"apply", "rollback"}
            for photo in self.photos
            for error in photo.errors
        )
        if self.scan_status == "ready" and has_row_scan_errors:
            raise ManifestError("ready scan status cannot carry row scan errors")
        if self.scan_status == "ready_with_errors" and not has_row_scan_errors:
            raise ManifestError("ready_with_errors requires a persisted row scan error")
        uuids = [photo.uuid for photo in self.photos if photo.uuid is not None]
        local_ids = [photo.photos_local_identifier for photo in self.photos]
        if len(uuids) != len(set(uuids)):
            raise ManifestError("photo UUIDs must be unique")
        if len(local_ids) != len(set(local_ids)):
            raise ManifestError("photo local identifiers must be unique")

    def immutable_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app": self.app,
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(),
            "dry_run": True,
            "scan_status": self.scan_status,
            "model": self.model,
            "selection": self.selection,
            "policy": self.policy,
            "photos": [
                photo.immutable_dict(
                    include_routing=self.schema_version >= 2,
                    include_review=self.schema_version == 4,
                )
                for photo in self.photos
            ],
            "summary": self.summary,
            "run_errors": self.run_errors,
            **({"reviewed_from_run_id": self.reviewed_from_run_id} if self.schema_version in {3, 4} else {}),
            **({"source_scan_digest": self.source_scan_digest} if self.schema_version in {3, 4} and self.source_scan_digest is not None else {}),
            **({"review_decision_digest": self.review_decision_digest} if self.schema_version == 4 else {}),
        }

    def compute_scan_digest(self) -> str:
        encoded = _json_utf8_bytes(self.immutable_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded).hexdigest()

    def compute_review_decision_digest(self) -> str:
        if self.schema_version != 4:
            raise ManifestError("review decisions require schema 4")
        payload = {
            "run_id": self.run_id,
            "reviewed_from_run_id": self.reviewed_from_run_id,
            "source_scan_digest": self.source_scan_digest,
            "photos": [
                {
                    "uuid": photo.uuid,
                    "photos_local_identifier": photo.photos_local_identifier,
                    "approved_keywords": photo.approved_keywords,
                    "keyword_origins": photo.keyword_origins,
                    "approved_caption": photo.approved_caption,
                    "caption_origin": photo.caption_origin,
                }
                for photo in self.photos
            ],
        }
        encoded = _json_utf8_bytes(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.immutable_dict(),
            "photos": [
                photo.to_dict(
                    include_routing=self.schema_version >= 2,
                    include_review=self.schema_version == 4,
                )
                for photo in self.photos
            ],
            "scan_digest": self.scan_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ScanManifest":
        base_keys = {
            "schema_version", "app", "run_id", "created_at", "dry_run", "scan_status", "model", "selection", "policy", "photos", "summary", "run_errors", "scan_digest",
        }
        if not isinstance(value, Mapping):
            raise ManifestError("manifest keys do not match the schema")
        schema_version = value.get("schema_version")
        if schema_version in {3, 4}:
            base_keys |= {"reviewed_from_run_id", "source_scan_digest"}
        if schema_version == 4:
            base_keys |= {"review_decision_digest"}
        item = _require_keys(value, base_keys, "manifest")
        schema_version = item["schema_version"]
        if type(schema_version) is not int or schema_version not in {1, 2, 3, 4} or item["dry_run"] is not True:
            raise ManifestError("unsupported manifest schema")
        app = _require_keys(item["app"], {"name", "version"}, "app")
        model_keys = (
            {"name", "ollama_version", "endpoint"}
            if schema_version == 1
            else {"policy", "fast_name", "detailed_name", "ollama_version", "endpoint"}
        )
        model = _require_keys(item["model"], model_keys, "model")
        policy = _require_keys(item["policy"], {"id", "taxonomy_sha256", "max_keywords", "confidence_threshold"}, "policy")
        if not isinstance(item["created_at"], str) or not isinstance(item["scan_digest"], str):
            raise ManifestError("invalid manifest timestamp or digest")
        try:
            created_at = datetime.fromisoformat(item["created_at"])
        except ValueError as error:
            raise ManifestError("created_at must be ISO-8601") from error
        if not isinstance(item["photos"], list):
            raise ManifestError("invalid manifest collections")
        if len(item["scan_digest"]) != 64 or any(character not in "0123456789abcdef" for character in item["scan_digest"].lower()):
            raise ManifestError("scan_digest must be a SHA-256 hex value")
        return cls(
            run_id=item["run_id"], created_at=created_at, app=dict(app), model=dict(model), selection=_selection(item["selection"]),
            photos=[
                PhotoRecord.from_dict(
                    photo,
                    include_routing=schema_version >= 2,
                    include_review=schema_version == 4,
                )
                for photo in item["photos"]
            ], summary=_strict_nonnegative_counters(item["summary"], {"ready", "noop", "analysis_failed"}, "summary"),
            run_errors=_error_list(item["run_errors"], "run_errors"), policy=dict(policy), scan_status=item["scan_status"], scan_digest=item["scan_digest"],
            schema_version=schema_version,
            reviewed_from_run_id=item.get("reviewed_from_run_id"),
            source_scan_digest=item.get("source_scan_digest"),
            review_decision_digest=item.get("review_decision_digest"),
        )


def compute_mutation_digest(manifest: ScanManifest, photo: PhotoRecord) -> str:
    """Return a receipt for fields proven by an apply read-back."""
    payload = {
        "run_id": manifest.run_id,
        "scan_digest": manifest.scan_digest,
        "reviewed_from_run_id": manifest.reviewed_from_run_id,
        "source_scan_digest": manifest.source_scan_digest,
        "uuid": photo.uuid,
        "photos_local_identifier": photo.photos_local_identifier,
        "applied_keywords": photo.applied_keywords,
        "applied_caption": photo.applied_caption,
    }
    encoded = _json_utf8_bytes(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded).hexdigest()


def compute_review_decision_digest(manifest: ScanManifest) -> str:
    """Return the schema-4 receipt covering explicit user-approved values."""
    return manifest.compute_review_decision_digest()


def compute_rollback_digest(manifest: ScanManifest, photo: PhotoRecord) -> str:
    """Return a receipt for the persisted rollback read-back state."""
    payload = {
        "run_id": manifest.run_id,
        "mutation_digest": photo.mutation_digest,
        "uuid": photo.uuid,
        "photos_local_identifier": photo.photos_local_identifier,
        "rollback_state": photo.rollback_state,
        "rolled_back_keywords": photo.rolled_back_keywords,
        "caption_state": photo.caption_state,
    }
    encoded = _json_utf8_bytes(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded).hexdigest()


def build_scan_manifest(*, run_id: str, app_name: str, app_version: str, model_name: str, ollama_version: str,
                        endpoint: str, selection: dict[str, Any], photos: list[PhotoRecord], summary: dict[str, Any],
                        scan_status: str = "ready", created_at: datetime | None = None,
                        confidence_threshold: float = 0.0,
                        run_errors: list[dict[str, str]] | None = None,
                        model_policy: str | None = None,
                        fast_model: str | None = None,
                        detailed_model: str | None = None,
                        ollama_versions: dict[str, str] | None = None) -> ScanManifest:
    schema_version = 2 if model_policy is not None else 1
    if schema_version == 2:
        if model_policy not in {"single", "adaptive"} or not fast_model or not detailed_model or ollama_versions is None:
            raise ManifestError("invalid model routing plan")
        model = {
            "policy": model_policy,
            "fast_name": fast_model,
            "detailed_name": detailed_model,
            "ollama_version": str(ollama_versions.get(fast_model, ollama_version)),
            "endpoint": endpoint,
        }
    else:
        model = {"name": model_name, "ollama_version": ollama_version, "endpoint": endpoint}
    return ScanManifest(
        run_id=run_id, created_at=created_at or datetime.now(timezone.utc), app={"name": app_name, "version": app_version},
        model=model, selection=selection,
        photos=photos, summary=summary, scan_status=scan_status,
        run_errors=run_errors or [],
        policy={"id": TAXONOMY_ID, "taxonomy_sha256": TAXONOMY_SHA256, "max_keywords": 8, "confidence_threshold": confidence_threshold},
        schema_version=schema_version,
    )


def _ensure_private_run_directory(run_dir: Path) -> None:
    # A missing final directory is not sufficient evidence that its path is
    # safe: ``mkdir(parents=True)`` follows an existing symlink in any parent
    # component.  Reject those ancestors before creating or chmod'ing anything
    # so a crafted run path cannot redirect a manifest write outside its root.
    _reject_symlinked_ancestors(run_dir)
    try:
        details = os.lstat(run_dir)
    except FileNotFoundError:
        run_dir.mkdir(mode=0o700, parents=True)
    else:
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise ManifestError("run directory must be a real directory")
        if details.st_uid != os.getuid():
            raise ManifestError("run directory must belong to the current user")
        os.chmod(run_dir, 0o700)


def _atomic_private_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_manifest(run_dir: Path, manifest: ScanManifest) -> Path:
    manifest.validate_mutable_state()
    manifest.scan_digest = manifest.compute_scan_digest()
    serialized = manifest.to_dict()
    # ``ScanManifest`` is mutable so workflows can checkpoint apply/rollback.
    # Re-run the complete reader contract before persistence: validating only
    # row states would allow an invalid top-level field changed in memory to be
    # written successfully even though the next load must reject it.
    ScanManifest.from_dict(serialized)
    payload = _json_utf8_bytes(serialized, sort_keys=True, indent=2) + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest exceeds the size limit")
    _ensure_private_run_directory(run_dir)
    path = run_dir / MANIFEST_FILENAME
    _atomic_private_write(path, payload)
    return path


def _read_safe_manifest(path: Path, *, expected_directory: os.stat_result | None = None) -> bytes:
    directory_path = Path(path).parent
    filename = Path(path).name
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_descriptor = os.open(directory_path, directory_flags)
    except FileNotFoundError as error:
        raise ManifestError("manifest does not exist") from error
    except OSError as error:
        raise ManifestError("run directory is unsafe") from error
    try:
        directory = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or (
                expected_directory is not None
                and (directory.st_dev, directory.st_ino) != (expected_directory.st_dev, expected_directory.st_ino)
            )
        ):
            raise ManifestError("run directory changed while being opened")
        try:
            initial = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ManifestError("manifest does not exist") from error
        except OSError as error:
            raise ManifestError("manifest path is unsafe") from error
        if (
            not stat.S_ISREG(initial.st_mode)
            or stat.S_ISLNK(initial.st_mode)
            or initial.st_uid != os.getuid()
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_nlink != 1
            or initial.st_size > MAX_MANIFEST_BYTES
        ):
            raise ManifestError("manifest path is not a safe private regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
        except OSError as error:
            raise ManifestError("manifest changed while being opened") from error
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
                raise ManifestError("manifest changed while being opened")
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
                raise ManifestError("manifest exceeds the size limit")
            return payload
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def _reject_symlinked_ancestors(path: Path) -> None:
    """Reject a run path that reaches its directory through a symlink."""
    current = Path(path.anchor) if path.is_absolute() else Path()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for component in parts:
        current /= component
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ManifestError("run directory is unsafe") from error
        if stat.S_ISLNK(details.st_mode):
            try:
                resolved = current.resolve(strict=True)
            except OSError as error:
                raise ManifestError("run directory must not traverse symlinks") from error
            if _TRUSTED_SYSTEM_SYMLINKS.get(current) != resolved:
                raise ManifestError("run directory must not traverse symlinks")


def load_manifest(run_dir: Path) -> ScanManifest:
    try:
        run_dir = Path(run_dir)
        directory = os.lstat(run_dir)
    except (OSError, TypeError, ValueError) as error:
        raise ManifestError("run directory is unsafe") from error
    if (stat.S_ISLNK(directory.st_mode) or not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700):
        raise ManifestError("run directory must be a real directory")
    _reject_symlinked_ancestors(run_dir)
    try:
        payload = decode_strict_json(
            _read_safe_manifest(run_dir / MANIFEST_FILENAME, expected_directory=directory).decode("utf-8")
        )
    except UnicodeDecodeError as error:
        raise ManifestError("manifest must be UTF-8") from error
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ManifestError("manifest must be valid JSON") from error
    return ScanManifest.from_dict(payload)


def reviewed_rows_match_source(source: ScanManifest, reviewed: ScanManifest) -> bool:
    """Check that a reviewed manifest still matches its dry-run source."""
    if source.schema_version == 1 and reviewed.schema_version == 1:
        source_model = source.model
    elif source.schema_version == 1:
        source_model = {
            "policy": "single",
            "fast_name": source.model["name"],
            "detailed_name": source.model["name"],
            "ollama_version": source.model["ollama_version"],
            "endpoint": source.model["endpoint"],
        }
    else:
        source_model = source.model
    if (
        source.app != reviewed.app
        or source.selection != reviewed.selection
        or source.policy != reviewed.policy
        or source.scan_status != reviewed.scan_status
        or source_model != reviewed.model
    ):
        return False
    source_by_local_id = {photo.photos_local_identifier: photo for photo in source.photos}
    if len(source_by_local_id) != len(source.photos) or len(reviewed.photos) != len(source.photos):
        return False
    for photo in reviewed.photos:
        original = source_by_local_id.get(photo.photos_local_identifier)
        if original is None:
            return False
        expected_model_used = original.model_used
        expected_model_reason = original.model_reason
        if (
            source.schema_version == 1
            and original.uuid is not None
            and original.scan_state != "analysis_failed"
        ):
            expected_model_used = source.model["name"]
            expected_model_reason = "single_policy"
        source_errors = [error for error in original.errors if error["stage"] not in {"apply", "rollback"}]
        reviewed_errors = [error for error in photo.errors if error["stage"] not in {"apply", "rollback"}]
        if (
            photo.uuid != original.uuid
            or photo.title != original.title
            or photo.date != original.date
            or photo.existing_keywords != original.existing_keywords
            or photo.contains_people != original.contains_people
            or photo.contains_text != original.contains_text
            or photo.confidence != original.confidence
            or photo.model_used != expected_model_used
            or photo.model_reason != expected_model_reason
            or photo.technical_trace != original.technical_trace
            or reviewed_errors != source_errors
        ):
            return False
        if reviewed.schema_version == 4:
            if (
                photo.model_proposed_keywords != original.proposed_keywords
                or photo.model_proposed_caption != original.proposed_caption
                or photo.approved_keywords != photo.proposed_keywords
                or photo.approved_caption != photo.proposed_caption
            ):
                return False
            model_keys = {
                canonical_keyword_key(value)
                for value in original.proposed_keywords
            }
            expected_origins = {
                value: ("model" if canonical_keyword_key(value) in model_keys else "manual")
                for value in photo.proposed_keywords
            }
            if photo.keyword_origins != expected_origins:
                return False
            expected_caption_origin = (
                None
                if photo.proposed_caption is None
                else ("model" if photo.proposed_caption == original.proposed_caption else "manual")
            )
            if photo.caption_origin != expected_caption_origin:
                return False
        source_keywords = Counter(original.proposed_keywords)
        reviewed_keywords = Counter(photo.proposed_keywords)
        if reviewed.schema_version != 4 and any(
            count > source_keywords[key]
            for key, count in reviewed_keywords.items()
        ):
            return False
        if (
            reviewed.schema_version != 4
            and photo.proposed_caption is not None
            and photo.proposed_caption != original.proposed_caption
        ):
            return False
        manual_analysis_recovery = (
            reviewed.schema_version == 4
            and original.scan_state == "analysis_failed"
            and original.uuid is not None
            and not original.proposed_keywords
            and original.proposed_caption is None
            and bool(original.errors)
            and all(error["stage"] == "analysis" for error in original.errors)
            and photo.scan_state == "ready"
            and bool(photo.proposed_keywords or photo.proposed_caption)
            and photo.keyword_origins is not None
            and all(origin == "manual" for origin in photo.keyword_origins.values())
            and (photo.proposed_caption is None or photo.caption_origin == "manual")
        )
        if original.scan_state == "ready":
            if photo.scan_state not in {"ready", "noop"}:
                return False
        elif manual_analysis_recovery:
            pass
        elif photo.scan_state != original.scan_state:
            return False
    return True


def write_preview_csv(run_dir: Path, manifest: ScanManifest) -> Path:
    _ensure_private_run_directory(run_dir)
    fieldnames = ["uuid", "title", "date", "existing_keywords", "proposed_keywords", "caption_status", "contains_people", "contains_text", "confidence", "status", "error_codes"]
    with tempfile.TemporaryDirectory(dir=run_dir) as temporary_directory:
        temporary_path = Path(temporary_directory) / PREVIEW_FILENAME
        with temporary_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for photo in manifest.photos:
                writer.writerow({
                    "uuid": photo.uuid[:8] if photo.uuid is not None else "—",
                    "title": safe_projection_text(
                        photo.title if len(photo.title) <= 24 else photo.title[:23] + "…", csv_cell=True
                    ),
                    "date": photo.date.isoformat(),
                    "existing_keywords": json.dumps(
                        [safe_projection_text(keyword) for keyword in photo.existing_keywords], ensure_ascii=False
                    ),
                    "proposed_keywords": json.dumps(
                        [safe_projection_text(keyword) for keyword in photo.proposed_keywords], ensure_ascii=False
                    ),
                    "caption_status": {
                        "not_requested": "—",
                        "proposed": "pendiente",
                        "preserved": "preservado",
                        "verified": "verificado",
                        "removed": "retirado",
                        "uncertain": "incierto",
                    }.get(photo.caption_state, "—") if photo.caption_state != "not_requested" else "—",
                    "contains_people": json.dumps(photo.contains_people), "contains_text": json.dumps(photo.contains_text),
                    "confidence": json.dumps(photo.confidence),
                    "status": (
                        photo.rollback_state
                        if photo.rollback_state != "not_run"
                        else photo.apply_state
                        if photo.apply_state != "not_run"
                        else photo.scan_state
                    ),
                    "error_codes": json.dumps([error["code"] for error in photo.errors], ensure_ascii=False),
                })
        _atomic_private_write(run_dir / PREVIEW_FILENAME, temporary_path.read_bytes())
    return run_dir / PREVIEW_FILENAME
