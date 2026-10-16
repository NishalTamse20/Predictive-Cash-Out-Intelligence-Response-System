"""Smoke tests for interactive complaint analysis and local case actions."""

import unittest

from fastapi import HTTPException

from backend.case_state import register_case, update_case_status
from backend.main import (
    DASHBOARD_PATH,
    CaseStatusRequest,
    ComplaintRequest,
    analyze_complaint,
    app,
    demo_prediction,
    sample_case,
    set_case_status,
)
from src.prediction.analysis_pipeline import run_analysis
from src.prediction.candidate_locations import load_candidates
from src.prediction.ranker import rank_candidates
from src.preprocessing.complaint_validation import SAMPLE_CASE_INPUT, validate_complaint_input


def assert_no_target_labels(test_case, response):
    forbidden_keys = {
        "synthetic_ground_truth", "synthetic_ground_truth_scope", "is_synthetic_ground_truth",
        "target_candidate_id", "cashout_time", "cashout_event_id", "cashout_present",
        "ground_truth_cashouts", "synthetic_cashout_scenarios",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                test_case.assertNotIn(key, forbidden_keys)
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(response)


class InteractiveDemoTests(unittest.TestCase):
    def test_complaint_validation_accepts_and_rejects_inputs(self):
        valid = validate_complaint_input(SAMPLE_CASE_INPUT)
        self.assertEqual(valid["reported_amount_inr"], 50000.0)
        self.assertEqual(valid["starting_account_id"], "SYN-VICTIM-001")
        for changed, message in (
            ({"reported_amount_inr": 0}, "amount"),
            ({"reported_amount_inr": 10.123}, "decimal places"),
            ({"report_time": "not-a-time"}, "date and time"),
            ({"starting_account_id": "bad/account"}, "fictional ID"),
            ({"reporting_delay_minutes": 2}, "between 10 minutes"),
        ):
            payload = {**SAMPLE_CASE_INPUT, **changed}
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, message):
                validate_complaint_input(payload)

    def test_analysis_endpoint_uses_entered_values_and_preserves_amount(self):
        request = ComplaintRequest(**SAMPLE_CASE_INPUT)
        result = analyze_complaint(request)
        self.assertEqual(result["complaint"]["reported_loss_amount_inr"], SAMPLE_CASE_INPUT["reported_amount_inr"])
        self.assertEqual(result["complaint"]["victim_account_id"], SAMPLE_CASE_INPUT["starting_account_id"])
        self.assertTrue(all(tx["amount_inr"] == SAMPLE_CASE_INPUT["reported_amount_inr"] for tx in result["transactions"]))
        self.assertTrue(all(tx["event_time"] <= result["complaint"]["reported_at"] for tx in result["transactions"]))
        self.assertEqual(result["case_status"], "New")
        assert_no_target_labels(self, result)

    def test_api_validation_returns_http_422(self):
        payload = {**SAMPLE_CASE_INPUT, "reporting_delay_minutes": 5}
        with self.assertRaises(HTTPException) as error:
            analyze_complaint(ComplaintRequest(**payload))
        self.assertEqual(error.exception.status_code, 422)

    def test_candidate_ranking_is_ordered_explainable_and_simulated(self):
        result = run_analysis(SAMPLE_CASE_INPUT)
        candidates, _ = load_candidates()
        self.assertEqual(len(result["ranked_candidates"]), len(candidates))
        self.assertFalse(result["location_ranking_available"])
        self.assertTrue(all(candidate["risk_score"] is None for candidate in result["ranked_candidates"]))
        self.assertTrue(all(candidate["rank"] is None for candidate in result["ranked_candidates"]))
        self.assertTrue(all(candidate["ranking_reason"] for candidate in result["ranked_candidates"]))
        self.assertTrue(all(candidate["name"].startswith("Simulated Location") for candidate in result["ranked_candidates"]))
        assert_no_target_labels(self, result)
        prediction = rank_candidates(result["complaint"], result["transactions"], candidates)
        self.assertNotIn("synthetic_ground_truth", prediction)

    def test_osm_candidates_are_an_explicit_source_and_keep_provenance(self):
        result = analyze_complaint(ComplaintRequest(**SAMPLE_CASE_INPUT), candidate_source="osm")
        self.assertEqual(result["candidate_source"]["mode"], "osm")
        self.assertEqual(result["candidate_source"]["valid_candidate_count"], 312)
        self.assertEqual(len(result["ranked_candidates"]), 312)
        self.assertTrue(all(not item["is_synthetic_fixture"] for item in result["ranked_candidates"]))
        self.assertTrue(all(item["osm_id"] and item["osm_type"] for item in result["ranked_candidates"]))
        self.assertTrue(all(item["distance_basis"] == "unavailable" for item in result["ranked_candidates"]))
        self.assertTrue(all(item["risk_score"] is None and item["rank"] is None for item in result["ranked_candidates"]))
        self.assertIn("not geographically ranked", result["location_ranking_note"])
        assert_no_target_labels(self, result)

    def test_radius_filter_works_for_both_sources_without_changing_candidate_risk_scores(self):
        for source_name in ("synthetic_fixture", "osm"):
            candidates, _ = load_candidates(source_name)
            anchor = candidates[0]
            origin = {
                "latitude": anchor["latitude"], "longitude": anchor["longitude"],
                "origin": "verified_incident_origin", "source": "verified test origin",
                "is_verified_incident_origin": True,
            }
            result = run_analysis(
                {**SAMPLE_CASE_INPUT, "location_hint": origin},
                candidate_source=source_name,
                radius_km=0.01,
            )
            with self.subTest(source=source_name):
                self.assertTrue(result["spatial_filter"]["applied"])
                self.assertTrue(result["spatial_filter"]["radius_applied"])
                self.assertTrue(any(item["candidate_id"] == anchor["candidate_id"] for item in result["ranked_candidates"]))
                self.assertTrue(all(item["risk_score"] is None and item["rank"] is None for item in result["ranked_candidates"]))
                self.assertTrue(all(isinstance(item["spatial_distance_km"], float) for item in result["ranked_candidates"]))
                self.assertEqual(result["candidate_source"]["mode"], source_name)

    def test_radius_without_verified_origin_skips_filter_and_empty_results_are_safe(self):
        result = run_analysis(SAMPLE_CASE_INPUT, candidate_source="osm", radius_km=1)
        self.assertFalse(result["spatial_filter"]["applied"])
        self.assertFalse(result["spatial_filter"]["radius_applied"])
        self.assertEqual(len(result["ranked_candidates"]), 312)
        self.assertTrue(all(item["spatial_distance_km"] is None for item in result["ranked_candidates"]))

        origin = {
            "latitude": 0, "longitude": 0, "origin": "verified_incident_origin",
            "source": "verified test origin", "is_verified_incident_origin": True,
        }
        empty = run_analysis(
            {**SAMPLE_CASE_INPUT, "location_hint": origin},
            candidate_source="osm", radius_km=0.01,
        )
        self.assertEqual(empty["ranked_candidates"], [])
        self.assertEqual(empty["spatial_filter"]["result_candidate_count"], 0)
        self.assertIn("No candidates matched", empty["spatial_filter"]["message"])
        self.assertFalse(empty["location_ranking_available"])

    def test_invalid_spatial_parameters_are_rejected_and_bbox_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "Radius"):
            run_analysis(SAMPLE_CASE_INPUT, radius_km=0)
        candidates, _ = load_candidates("osm")
        anchor = candidates[0]
        bbox = f"{anchor['longitude']-0.0001},{anchor['latitude']-0.0001},{anchor['longitude']+0.0001},{anchor['latitude']+0.0001}"
        filtered = run_analysis(SAMPLE_CASE_INPUT, candidate_source="osm", bbox=bbox)
        self.assertTrue(filtered["spatial_filter"]["bbox_applied"])
        self.assertFalse(filtered["spatial_filter"]["origin_contract_accepted"])
        self.assertTrue(any(item["candidate_id"] == anchor["candidate_id"] for item in filtered["ranked_candidates"]))
        self.assertTrue(all(item["risk_score"] is None for item in filtered["ranked_candidates"]))

    def test_invalid_verified_origin_coordinates_return_http_422(self):
        payload = {
            **SAMPLE_CASE_INPUT,
            "location_hint": {
                "latitude": 91, "longitude": 73.85,
                "origin": "verified_incident_origin", "source": "controlled test input",
                "is_verified_incident_origin": True,
            },
        }
        with self.assertRaises(HTTPException) as error:
            analyze_complaint(ComplaintRequest(**payload), candidate_source="osm", radius_km=1)
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn("Origin latitude", error.exception.detail)

    def test_verified_origin_without_filter_does_not_change_candidate_risk_scores(self):
        result = run_analysis({
            **SAMPLE_CASE_INPUT,
            "location_hint": {
                "latitude": 18.52, "longitude": 73.85,
                "origin": "verified_incident_origin", "source": "verified test source",
                "is_verified_incident_origin": True,
            },
        }, candidate_source="osm")
        self.assertFalse(result["spatial_filter"]["requested"])
        self.assertFalse(result["location_ranking_available"])
        self.assertTrue(all(item["risk_score"] is None for item in result["ranked_candidates"]))

    def test_local_case_status_updates(self):
        case_id = "PCIRS-TEST-STATUS-0001"
        register_case(case_id)
        result = set_case_status(case_id, CaseStatusRequest(status="Under Investigation"))
        self.assertEqual(result["status"], "Under Investigation")
        self.assertEqual(update_case_status(case_id, "Reviewed"), "Reviewed")
        self.assertEqual(set_case_status(case_id, CaseStatusRequest(status="New"))["status"], "New")

    def test_overview_and_graph_support(self):
        from backend.main import overview
        summary = overview()
        self.assertTrue(summary["synthetic_demo_statistics"])
        self.assertEqual(summary["metrics"]["total_demo_transactions"], 3)
        self.assertNotIn("synthetic_cashout_scenarios", summary["metrics"])
        assert_no_target_labels(self, summary)
        result = run_analysis(SAMPLE_CASE_INPUT)
        self.assertEqual(len(result["transactions"]), result["hop_count"])
        self.assertTrue(all(tx["amount_inr"] == SAMPLE_CASE_INPUT["reported_amount_inr"] for tx in result["transactions"]))
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        self.assertIn("Transaction Network Graph", html)
        self.assertIn("renderNetworkGraph(data.transactions)", html)

    def test_dashboard_and_legacy_demo_route_remain_available(self):
        routes = {(route.path, tuple(route.methods or [])) for route in app.routes}
        self.assertIn(("/api/analyze", ("POST",)), routes)
        self.assertIn(("/api/overview", ("GET",)), routes)
        self.assertIn(("/api/sample-case", ("GET",)), routes)
        self.assertIn(("/api/demo-prediction", ("GET",)), routes)
        self.assertIn(("/api/cases/{case_id}/status", ("PUT",)), routes)
        self.assertIn(("/assets/candidate-map.js", ("GET",)), routes)
        analyze_route = next(route for route in app.routes if route.path == "/api/analyze")
        query_names = {parameter.name for parameter in analyze_route.dependant.query_params}
        self.assertTrue({"candidate_source", "radius_km", "bbox"}.issubset(query_names))
        self.assertTrue(DASHBOARD_PATH.is_file())
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        for label in ("Analyze Complaint", "Reload demo case", "Reset / New Complaint", "name=\"report_time\"", "Transaction Risk Indicator", "candidate-source-select", "radius-km", "bbox-filter", "origin-verified", "candidate-map", "candidate-map.js", "Candidate locations returned by this analysis"):
            self.assertIn(label, html)
        self.assertIn("OpenStreetMap contributors", html)
        self.assertIn("explicit verified-origin provenance", html)
        self.assertNotIn("Distance is measured from a synthetic complaint-origin hint", html)
        self.assertNotIn('id="synthetic-truth"', html)
        self.assertNotIn("data.synthetic_ground_truth", html)
        self.assertNotIn("m.synthetic_cashout_scenarios", html)
        self.assertEqual(sample_case()["starting_account_id"], SAMPLE_CASE_INPUT["starting_account_id"])
        legacy = demo_prediction()
        self.assertEqual(legacy["hop_count"], 3)
        self.assertIn("ranked_candidates", legacy)
        assert_no_target_labels(self, legacy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
