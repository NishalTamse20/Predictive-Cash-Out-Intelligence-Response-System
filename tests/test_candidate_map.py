"""Tests for mapping candidates returned by analysis to map marker data."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from src.preprocessing.complaint_validation import SAMPLE_CASE_INPUT
from src.prediction.analysis_pipeline import run_analysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPER_PATH = PROJECT_ROOT / "dashboard" / "candidate_map.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the dashboard map mapper")
class CandidateMapMappingTests(unittest.TestCase):
    def map_records(self, candidates, mode):
        script = r"""
const { mapCandidateRecords, sourceLabel, formatCandidateNumber } = require(process.argv[1]);
const { candidates, mode } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const records = mapCandidateRecords(candidates, mode);
console.log(JSON.stringify({
  sourceLabel: sourceLabel(mode),
  nullNumber: formatCandidateNumber(null, 3),
  validZero: formatCandidateNumber(0, 3),
  records: records.map(record => ({
    candidateId: record.candidateId,
    latitude: record.latitude,
    longitude: record.longitude,
    sourceLabel: record.sourceLabel,
    candidateName: record.candidate.name ?? null,
  })),
}));
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(MAPPER_PATH)],
            check=True,
            capture_output=True,
            text=True,
            input=json.dumps({"candidates": candidates, "mode": mode}),
        )
        return json.loads(completed.stdout)

    def test_candidate_response_records_map_to_coordinates_and_identifiers(self):
        result = self.map_records(
            [{"candidate_id": "OSM-node-42", "name": "Mapped ATM", "latitude": 18.5, "longitude": 73.8}],
            "osm",
        )
        self.assertEqual(result["sourceLabel"], "OpenStreetMap mapped ATM candidates")
        self.assertEqual(result["nullNumber"], "Unavailable")
        self.assertEqual(result["validZero"], "0.000")
        self.assertEqual(result["records"], [{
            "candidateId": "OSM-node-42", "latitude": 18.5, "longitude": 73.8,
            "sourceLabel": "OpenStreetMap mapped ATM candidates", "candidateName": "Mapped ATM",
        }])

    def test_missing_or_invalid_coordinates_are_not_mapped(self):
        result = self.map_records([
            {"candidate_id": "good", "latitude": 18.5, "longitude": 73.8},
            {"candidate_id": "missing", "latitude": None, "longitude": 73.8},
            {"candidate_id": "string", "latitude": "18.5", "longitude": 73.8},
            {"candidate_id": "range", "latitude": 91, "longitude": 73.8},
        ], "osm")
        self.assertEqual([row["candidateId"] for row in result["records"]], ["good"])

    def test_synthetic_source_label_and_empty_results(self):
        result = self.map_records([], "synthetic_fixture")
        self.assertEqual(result["sourceLabel"], "Synthetic demo candidates")
        self.assertEqual(result["records"], [])

    def test_both_analysis_sources_map_exactly_the_returned_candidate_records(self):
        for mode in ("synthetic_fixture", "osm"):
            response = run_analysis(SAMPLE_CASE_INPUT, candidate_source=mode)
            mapped = self.map_records(response["ranked_candidates"], mode)
            with self.subTest(source=mode):
                self.assertEqual(len(response["ranked_candidates"]), 312 if mode == "osm" else 5)
                self.assertEqual(len(mapped["records"]), len(response["ranked_candidates"]))
                expected_label = ("OpenStreetMap mapped ATM candidates" if mode == "osm"
                                  else "Synthetic demo candidates")
                self.assertEqual(mapped["sourceLabel"], expected_label)

    def test_spatially_filtered_analysis_maps_only_candidates_returned_by_pipeline(self):
        origin = {
            "latitude": 18.5204, "longitude": 73.8567,
            "origin": "verified_incident_origin", "source": "controlled test coordinate",
            "is_verified_incident_origin": True,
        }
        response = run_analysis(
            {**SAMPLE_CASE_INPUT, "location_hint": origin},
            candidate_source="synthetic_fixture", radius_km=0.01,
        )
        mapped = self.map_records(response["ranked_candidates"], "synthetic_fixture")
        self.assertTrue(response["spatial_filter"]["radius_applied"])
        self.assertEqual(len(mapped["records"]), len(response["ranked_candidates"]))
        self.assertEqual([row["candidateId"] for row in mapped["records"]],
                         [row["candidate_id"] for row in response["ranked_candidates"]])

    def test_candidate_renderer_handles_null_scores_distances_and_coordinates(self):
        script = r"""
const fs = require('fs');
const helper = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.dashboardPath, 'utf8');
const start = html.indexOf('function renderCandidate(candidate, mode)');
const end = html.indexOf('\nfunction appendMapDetail', start);
if (start < 0 || end < 0) throw new Error('Could not locate dashboard candidate renderer');
const source = html.slice(start, end);
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const render = new Function('window', 'esc', 'candidate', source + '; return renderCandidate(candidate, "osm");');
console.log(render({ PCIRSCandidateMap: helper }, esc, input.candidate));
"""
        candidate = {
            "candidate_id": "OSM-node-7", "name": "Mapped ATM",
            "latitude": None, "longitude": None, "risk_score": None,
            "rank": None, "distance_km_from_origin_hint": None,
            "spatial_distance_km": None, "ranking_reason": None, "explanation": None,
        }
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(MAPPER_PATH)],
            check=True,
            capture_output=True,
            text=True,
            input=json.dumps({
                "dashboardPath": str(PROJECT_ROOT / "dashboard" / "index.html"),
                "candidate": candidate,
            }),
        )
        rendered = completed.stdout
        self.assertIn("Not calculated", rendered)
        self.assertIn("Coordinates: Unavailable", rendered)
        self.assertNotIn("0.000000", rendered)

    def test_empty_result_refresh_clears_existing_markers(self):
        dashboard = (PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (candidateMapMarkers) candidateMapMarkers.clearLayers();", dashboard)
        self.assertIn("No candidate locations were returned for this analysis.", dashboard)
        self.assertIn("height:360px;min-height:300px", dashboard)


if __name__ == "__main__":
    unittest.main()
