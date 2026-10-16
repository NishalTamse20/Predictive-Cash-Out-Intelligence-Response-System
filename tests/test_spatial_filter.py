import math
import unittest

from src.prediction.candidate_locations import load_candidates
from src.prediction.spatial_filter import (
    filter_candidates_spatially,
    haversine_km,
)


def verified_origin(latitude, longitude):
    return {
        "latitude": latitude,
        "longitude": longitude,
        "origin": "verified_incident_origin",
        "source": "verified test fixture",
        "is_verified_incident_origin": True,
    }


class SpatialFilterTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"candidate_id": "A", "latitude": 0, "longitude": 1, "source": "fixture", "is_synthetic_fixture": True},
            {"candidate_id": "B", "latitude": 0, "longitude": 1.01, "source": "fixture", "is_synthetic_fixture": True},
            {"candidate_id": "C", "latitude": 1, "longitude": 1, "source": "fixture", "is_synthetic_fixture": True},
        ]

    def test_haversine_known_one_degree_distance_is_in_kilometres(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 1), 111.195080, places=5)

    def test_radius_filter_includes_exact_boundary_and_excludes_outside(self):
        boundary_km = haversine_km(0, 0, 0, 1)
        selected, metadata = filter_candidates_spatially(
            self.candidates,
            location_hint=verified_origin(0, 0),
            radius_km=boundary_km,
        )
        self.assertEqual([item["candidate_id"] for item in selected], ["A"])
        self.assertAlmostEqual(selected[0]["spatial_distance_km"], boundary_km, places=6)
        self.assertTrue(metadata["radius_applied"])
        self.assertEqual(metadata["result_candidate_count"], 1)
        self.assertIn("km", metadata["distance_method"])

    def test_bounding_box_includes_edges_and_excludes_outside(self):
        selected, metadata = filter_candidates_spatially(self.candidates, bbox="1,0,1.01,1")
        self.assertEqual([item["candidate_id"] for item in selected], ["A", "B", "C"])
        self.assertTrue(metadata["bbox_applied"])
        selected, _ = filter_candidates_spatially(self.candidates, bbox="1,0,1.005,0.5")
        self.assertEqual([item["candidate_id"] for item in selected], ["A"])

    def test_radius_and_bbox_combine_as_intersection(self):
        selected, metadata = filter_candidates_spatially(
            self.candidates,
            location_hint=verified_origin(0, 0),
            radius_km=120,
            bbox="0.9,-0.1,1.1,0.1",
        )
        self.assertEqual([item["candidate_id"] for item in selected], ["A", "B"])
        self.assertTrue(metadata["radius_applied"] and metadata["bbox_applied"])

    def test_invalid_radius_and_bbox_are_rejected(self):
        for radius in (0, -1, math.nan, math.inf, 20016):
            with self.subTest(radius=radius), self.assertRaisesRegex(ValueError, "Radius"):
                filter_candidates_spatially(self.candidates, location_hint=verified_origin(0, 0), radius_km=radius)
        for bbox in ("1,2,3", "181,0,182,1", "2,1,1,0", "0,91,1,92", "0,0,1,nan"):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                filter_candidates_spatially(self.candidates, bbox=bbox)

    def test_invalid_and_unverified_origins_skip_radius_without_filtering(self):
        invalid_origins = (
            None,
            {**verified_origin(0, 0), "origin": "synthetic_demo_coordinate"},
            {**verified_origin(0, 0), "is_verified_incident_origin": False},
            {**verified_origin(0, 0), "source": "  "},
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                selected, metadata = filter_candidates_spatially(
                    self.candidates, location_hint=origin, radius_km=10,
                )
                self.assertEqual(selected, self.candidates)
                self.assertFalse(metadata["applied"])
                self.assertFalse(metadata["radius_applied"])
                self.assertEqual(metadata["result_candidate_count"], len(self.candidates))
                self.assertNotIn("spatial_distance_km", selected[0])

    def test_malformed_coordinates_claiming_verified_origin_are_rejected(self):
        for origin in (
            {**verified_origin(91, 0)},
            {**verified_origin(0, math.inf)},
            {**verified_origin(True, 0)},
            {**verified_origin("bad", 0)},
        ):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "Origin"):
                filter_candidates_spatially(
                    self.candidates, location_hint=origin, radius_km=10,
                )

    def test_no_origin_never_assumes_demo_coordinate_for_radius(self):
        selected, metadata = filter_candidates_spatially(self.candidates, radius_km=10)
        self.assertEqual(selected, self.candidates)
        self.assertFalse(metadata["applied"])
        self.assertIn("No location origin", metadata["message"])

    def test_explicit_bounding_box_can_filter_without_origin(self):
        selected, metadata = filter_candidates_spatially(self.candidates, bbox="0.9,-0.1,1.005,0.5")
        self.assertEqual([item["candidate_id"] for item in selected], ["A"])
        self.assertTrue(metadata["applied"])
        self.assertFalse(metadata["origin_contract_accepted"])

    def test_osm_and_synthetic_records_keep_identity_and_provenance_after_radius_filter(self):
        for source_name in ("osm", "synthetic_fixture"):
            candidates, source_metadata = load_candidates(source_name)
            anchor = candidates[0]
            selected, metadata = filter_candidates_spatially(
                candidates,
                location_hint=verified_origin(anchor["latitude"], anchor["longitude"]),
                radius_km=0.01,
            )
            self.assertTrue(metadata["applied"])
            self.assertIn(anchor["candidate_id"], {item["candidate_id"] for item in selected})
            self.assertEqual(selected[0]["source"], anchor["source"])
            if source_name == "osm":
                self.assertEqual(selected[0]["osm_id"], anchor["osm_id"])
                self.assertEqual(selected[0]["osm_type"], anchor["osm_type"])
                self.assertEqual(source_metadata["attribution"], "© OpenStreetMap contributors")
            else:
                self.assertTrue(selected[0]["is_synthetic_fixture"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
