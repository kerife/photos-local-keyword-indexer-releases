from __future__ import annotations

import unittest

from photos_indexer.landmarks import Landmark, OfflineLandmarkResolver


class OfflineLandmarkResolverTests(unittest.TestCase):
    def test_resolves_landmark_inside_radius(self) -> None:
        resolver = OfflineLandmarkResolver((Landmark(
            name="Basílica de Santa María de la Salud",
            latitude=45.4314,
            longitude=12.3348,
            radius_m=150,
        ),))

        result = resolver.resolve((45.4313917, 12.3348283))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "Basílica de Santa María de la Salud")

    def test_rejects_location_outside_landmark_radius(self) -> None:
        resolver = OfflineLandmarkResolver((Landmark(
            name="Basílica de Santa María de la Salud",
            latitude=45.4314,
            longitude=12.3348,
            radius_m=150,
        ),))

        self.assertIsNone(resolver.resolve((45.44, 12.34)))

if __name__ == "__main__":
    unittest.main()
