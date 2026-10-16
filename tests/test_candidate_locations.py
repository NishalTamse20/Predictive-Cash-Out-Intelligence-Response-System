import json
import tempfile
import unittest
from pathlib import Path

from src.prediction.candidate_locations import OSM_PATH, load_candidates, load_osm_candidates
from src.prediction.ranker import rank_candidates


def atm_feature(osm_id="10", *, longitude=73.81, latitude=18.51, amenity="atm", name=None, **tags):
    properties = {
        "osm_id": str(osm_id), "osm_type": "node", "amenity": amenity,
        "name": name, "operator": None, "brand": None, "access": None,
        "source": "OpenStreetMap", "retrieved_at": "2026-10-12",
        "latitude": latitude, "longitude": longitude,
    }
    properties.update(tags)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": properties,
    }


def save_geojson(path: Path, features):
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "attribution": "© OpenStreetMap contributors",
        "license": "Open Database License (ODbL)",
        "features": features,
    }), encoding="utf-8")


class CandidateLocationTests(unittest.TestCase):
    def test_processed_osm_dataset_loads_all_atm_features_and_preserves_ids(self):
        candidates, source = load_osm_candidates(OSM_PATH)
        self.assertEqual(len(candidates), 312)
        self.assertEqual(source["mode"], "osm")
        self.assertEqual(source["valid_candidate_count"], 312)
        self.assertEqual(source["attribution"], "© OpenStreetMap contributors")
        self.assertEqual(source["license"], "Open Database License (ODbL)")
        self.assertFalse(any(candidate["is_synthetic_fixture"] for candidate in candidates))
        self.assertTrue(all(candidate["candidate_id"] == f"OSM-{candidate['osm_type']}-{candidate['osm_id']}" for candidate in candidates))
        self.assertTrue(all(candidate["amenity"] == "atm" for candidate in candidates))

    def test_missing_optional_osm_tags_remain_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "atms.geojson"
            save_geojson(path, [atm_feature(operator="Bank name only")])
            candidates, _ = load_osm_candidates(path)
        candidate = candidates[0]
        self.assertIsNone(candidate["name"])
        self.assertEqual(candidate["name_status"], "No name tag in OSM")
        self.assertEqual(candidate["operator"], "Bank name only")
        self.assertIsNone(candidate["brand"])
        self.assertIsNone(candidate["access"])

    def test_invalid_geometry_coordinates_and_non_atms_are_rejected_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.geojson"
            valid = atm_feature("valid")
            outside = atm_feature("outside", longitude=181)
            non_atm = atm_feature("bank", amenity="bank")
            malformed = atm_feature("line")
            malformed["geometry"] = {"type": "LineString", "coordinates": []}
            save_geojson(path, [valid, outside, non_atm, malformed])
            candidates, source = load_osm_candidates(path)
        self.assertEqual([candidate["osm_id"] for candidate in candidates], ["valid"])
        self.assertEqual(source["rejected_feature_count"], 3)
        self.assertEqual(len(source["validation_errors"]), 3)

    def test_coordinate_property_mismatch_and_duplicate_osm_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicates.geojson"
            valid = atm_feature("same")
            mismatch = atm_feature("different")
            mismatch["properties"]["latitude"] = 19.0
            duplicate = atm_feature("same")
            save_geojson(path, [valid, mismatch, duplicate])
            candidates, source = load_osm_candidates(path)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(source["rejected_feature_count"], 2)
        self.assertTrue(any("do not match" in error for error in source["validation_errors"]))
        self.assertTrue(any("duplicate OSM element" in error for error in source["validation_errors"]))

    def test_malformed_feature_collection_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.geojson"
            path.write_text('{"type":"FeatureCollection","features":{}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "features must be an array"):
                load_osm_candidates(path)

    def test_source_selection_is_explicit_and_osm_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_osm = Path(directory) / "missing.geojson"
            with self.assertRaises(FileNotFoundError):
                load_candidates("osm", osm_path=missing_osm)
        fixture_candidates, fixture_source = load_candidates()
        self.assertEqual(fixture_source["mode"], "synthetic_fixture")
        self.assertTrue(all(candidate["is_synthetic_fixture"] for candidate in fixture_candidates))
        with self.assertRaisesRegex(ValueError, "candidate source must be"):
            load_candidates("automatic")

    def test_synthetic_demo_coordinate_does_not_enable_geographic_ranking(self):
        complaint, transactions = self.ranking_inputs({"latitude": 18.52, "longitude": 73.85, "origin": "synthetic_demo_coordinate"})
        result = rank_candidates(complaint, transactions, [self.rank_candidate()])
        candidate = result["ranked_candidates"][0]
        self.assertFalse(result["location_ranking_available"])
        self.assertEqual(candidate["distance_basis"], "unavailable")
        self.assertIsNone(candidate["distance_km_from_origin_hint"])
        self.assertIsNone(candidate["risk_score"])
        self.assertIn("not geographically ranked", result["location_ranking_note"])

    def test_verified_origin_requires_explicit_source_and_provenance(self):
        complaint, transactions = self.ranking_inputs({
            "latitude": 18.52, "longitude": 73.85,
            "is_verified_incident_origin": True,
            "origin": "verified_incident_origin",
            "source": "verified complaint address",
        })
        result = rank_candidates(complaint, transactions, [self.rank_candidate()])
        candidate = result["ranked_candidates"][0]
        self.assertEqual(candidate["distance_basis"], "verified_incident_origin")
        self.assertIn("verified incident-origin", candidate["distance_description"])

        complaint["location_hint"].pop("source")
        unverified = rank_candidates(complaint, transactions, [self.rank_candidate()])
        self.assertFalse(unverified["location_ranking_available"])

    def test_missing_or_invalid_origin_keeps_candidates_but_does_not_rank_them(self):
        for hint in (
            None,
            {"latitude": 91, "longitude": 73.8},
            {"latitude": "bad", "longitude": 73.8},
            {"latitude": 18.52, "longitude": 73.85},
            {"latitude": 18.52, "longitude": 73.85, "origin": "synthetic_demo_coordinate"},
        ):
            complaint, transactions = self.ranking_inputs(hint)
            result = rank_candidates(complaint, transactions, [self.rank_candidate()])
            candidate = result["ranked_candidates"][0]
            self.assertFalse(result["location_ranking_available"])
            self.assertIsNone(candidate["distance_km_from_origin_hint"])
            self.assertIsNone(candidate["risk_score"])
            self.assertIsNone(candidate["rank"])
            self.assertIn("not ranked", candidate["ranking_reason"].lower())

    @staticmethod
    def rank_candidate():
        return {
            "candidate_id": "OSM-node-1", "name": None,
            "latitude": 18.51, "longitude": 73.82,
            "source": "OpenStreetMap", "is_synthetic_fixture": False,
            "osm_id": "1", "osm_type": "node",
        }

    @staticmethod
    def ranking_inputs(location_hint):
        complaint = {
            "victim_account_id": "A", "reported_at": "2026-10-12T10:00:00+05:30",
            "location_hint": location_hint,
        }
        transactions = [
            {"transaction_id": "T1", "sender_id": "A", "receiver_id": "B", "event_time": "2026-10-12T09:00:00+05:30"},
            {"transaction_id": "T2", "sender_id": "B", "receiver_id": "C", "event_time": "2026-10-12T09:05:00+05:30"},
        ]
        return complaint, transactions


if __name__ == "__main__":
    unittest.main()
