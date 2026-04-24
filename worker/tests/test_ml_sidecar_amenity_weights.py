import unittest

from ml_sidecar.data import _normalize_amenities
from ml_sidecar.model import (
    AMENITY_FEATURE_WEIGHTS,
    build_amenity_feature_map,
)


class MlSidecarAmenityWeightsTests(unittest.TestCase):
    def test_normalize_amenities_canonicalizes_aliases_and_deduplicates(self) -> None:
        self.assertEqual(
            _normalize_amenities(
                [
                    "Air conditioning",
                    "A/C",
                    "Kitchen",
                    "Washer",
                    "laundry",
                    "Dryer",
                    "Wi-Fi",
                    "Free parking on premises",
                    "Allows pets",
                    "Waterfront",
                    "Guest favorite",
                ]
            ),
            [
                "ac",
                "kitchen",
                "washer",
                "dryer",
                "wifi",
                "free_parking",
                "pets_allowed",
                "waterfront",
                "guest_favorite",
            ],
        )

    def test_normalize_amenities_handles_airbnb_verbose_strings(self) -> None:
        self.assertEqual(
            _normalize_amenities(
                [
                    "AC – split-type ductless system",
                    "Free dryer – In unit",
                    "Free parking on premises",
                    "Guest favorite",
                    "Pets allowed",
                ]
            ),
            [
                "ac",
                "dryer",
                "free_parking",
                "guest_favorite",
                "pets_allowed",
            ],
        )

    def test_build_amenity_feature_map_emphasizes_priority_amenities(self) -> None:
        features = build_amenity_feature_map(["ac", "kitchen", "washer", "dryer"])

        self.assertEqual(features["has_ac"], 1.0)
        self.assertEqual(features["has_kitchen"], 1.0)
        self.assertEqual(features["has_washer"], 1.0)
        self.assertEqual(features["has_dryer"], 1.0)
        self.assertEqual(features["laundry_amenity_count"], 2.0)
        self.assertAlmostEqual(features["priority_amenity_ratio"], 1.0)

        expected_priority_score = (
            AMENITY_FEATURE_WEIGHTS["ac"]
            + AMENITY_FEATURE_WEIGHTS["kitchen"]
            + AMENITY_FEATURE_WEIGHTS["washer"]
            + AMENITY_FEATURE_WEIGHTS["dryer"]
        )
        self.assertAlmostEqual(
            features["priority_amenity_score"],
            expected_priority_score,
        )
        self.assertGreaterEqual(
            features["amenity_weighted_score"],
            features["priority_amenity_score"],
        )

    def test_build_amenity_feature_map_includes_new_circled_features(self) -> None:
        features = build_amenity_feature_map(["pets_allowed", "waterfront", "guest_favorite"])

        self.assertEqual(features["has_pets_allowed"], 1.0)
        self.assertEqual(features["has_waterfront"], 1.0)
        self.assertEqual(features["has_guest_favorite"], 1.0)

    def test_build_amenity_feature_map_treats_water_access_as_waterfront(self) -> None:
        features = build_amenity_feature_map(["lake_access", "beach_access"])

        self.assertEqual(features["has_waterfront"], 1.0)


if __name__ == "__main__":
    unittest.main()
