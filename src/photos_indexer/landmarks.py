"""Offline landmark matching for user-approved, locally retained coordinates.

The bundled entries are a deliberately small seed dataset.  Runtime matching
never calls Apple Maps or another geocoder; Apple Maps may be used manually to
curate an entry, after which only the local name and geofence are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class Landmark:
    name: str
    latitude: float
    longitude: float
    radius_m: float = 150.0
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("landmark coordinates and name are invalid")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("landmark coordinates are invalid")
        if not math.isfinite(self.radius_m) or self.radius_m <= 0:
            raise ValueError("landmark radius must be positive")
        if any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases):
            raise ValueError("landmark aliases must be non-empty strings")

    @property
    def confirmation_names(self) -> tuple[str, ...]:
        """Names the vision model may use to explicitly confirm this landmark."""
        return (self.name, *self.aliases)


# Seeded from the user-approved Apple Maps verification for photo 49F027C6.
BUNDLED_LANDMARKS = (
    Landmark(
        "Basílica de Santa María de la Salud",
        45.4313917,
        12.3348283,
        150.0,
        ("Basilica della Salute", "Basilica di Santa Maria della Salute"),
    ),
)


def _distance_meters(first: tuple[float, float], second: tuple[float, float]) -> float:
    earth_radius_m = 6_371_000.0
    lat1, lon1 = (math.radians(value) for value in first)
    lat2, lon2 = (math.radians(value) for value in second)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(haversine))


class OfflineLandmarkResolver:
    def __init__(self, landmarks: Iterable[Landmark] = BUNDLED_LANDMARKS) -> None:
        self._landmarks = tuple(landmarks)

    def resolve(self, location: tuple[float, float] | None) -> Landmark | None:
        if location is None:
            return None
        matches = (
            landmark for landmark in self._landmarks
            if _distance_meters(location, (landmark.latitude, landmark.longitude)) <= landmark.radius_m
        )
        return min(matches, key=lambda landmark: _distance_meters(location, (landmark.latitude, landmark.longitude)), default=None)
