from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


MAX_VISION_KEYWORD_LENGTH = 128


class VisionResultError(ValueError):
    """Raised when a local vision response violates the fixed response schema."""


@dataclass(frozen=True, slots=True)
class VisionResult:
    keywords: tuple[str, ...]
    caption: str
    contains_people: bool
    contains_text: bool
    confidence: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "VisionResult":
        if not isinstance(value, Mapping):
            raise VisionResultError("vision response must be an object")
        expected = {"keywords", "caption", "contains_people", "contains_text", "confidence"}
        if set(value) != expected:
            raise VisionResultError("vision response keys must exactly match the schema")
        keywords = value["keywords"]
        if type(keywords) is not list or len(keywords) > 8 or any(
            type(item) is not str
            or not item.strip()
            or len(item) > MAX_VISION_KEYWORD_LENGTH
            for item in keywords
        ):
            raise VisionResultError("keywords must be a list of at most eight nonempty strings")
        caption = value["caption"]
        if type(caption) is not str or len(caption) > 300:
            raise VisionResultError("caption must be a string of at most 300 characters")
        contains_people = value["contains_people"]
        contains_text = value["contains_text"]
        if type(contains_people) is not bool or type(contains_text) is not bool:
            raise VisionResultError("contains_people and contains_text must be booleans")
        confidence = value["confidence"]
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            raise VisionResultError("confidence must be a number from 0 through 1")
        return cls(tuple(keywords), caption, contains_people, contains_text, float(confidence))
