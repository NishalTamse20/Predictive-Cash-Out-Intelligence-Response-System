import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from tools.osm_pune_preview.server import GEOJSON_PATH, PreviewHandler, load_geojson

PREVIEW_JS = Path(__file__).resolve().parents[1] / "tools" / "osm_pune_preview" / "preview.js"


def feature(osm_id="1", lon=73.8, lat=18.5, amenity="atm", geometry_type="Point", **optional):
    props = {"osm_id": osm_id, "osm_type": "node", "amenity": amenity,
             "latitude": lat, "longitude": lon, "name": None,
             "operator": None, "brand": None, "access": None}
    props.update(optional)
    coords = [lon, lat] if geometry_type == "Point" else []
    return {"type": "Feature", "geometry": {"type": geometry_type, "coordinates": coords}, "properties": props}


class OSMPreviewTests(unittest.TestCase):
    def save_collection(self, path: Path, features):
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")

    def test_actual_geojson_loads_all_312_records(self):
        result = load_geojson(GEOJSON_PATH)
        self.assertEqual(result["source_feature_count"], 312)
        self.assertEqual(result["valid_feature_count"], 312)
        self.assertEqual(result["invalid_feature_count"], 0)
        self.assertTrue(all(f["properties"]["amenity"] == "atm" for f in result["features"]))

    def test_missing_optional_tags_are_retained_without_invention(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.geojson"
            sample = feature(name=None, operator="Mapped operator", brand=None, access=None)
            self.save_collection(path, [sample])
            result = load_geojson(path)
            self.assertEqual(result["valid_feature_count"], 1)
            self.assertIsNone(result["features"][0]["properties"]["name"])
            self.assertIsNone(result["features"][0]["properties"]["brand"])

    def test_invalid_and_non_atm_features_are_excluded_and_counted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.geojson"
            self.save_collection(path, [feature(), feature(osm_id="2", amenity="bank"),
                feature(osm_id="3", lon=74.1), feature(osm_id="4", geometry_type="LineString")])
            result = load_geojson(path)
            self.assertEqual(result["source_feature_count"], 4)
            self.assertEqual(result["valid_feature_count"], 1)
            self.assertEqual(result["invalid_feature_count"], 3)

    def test_malformed_geojson_has_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "broken.geojson"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not read ATM GeoJSON"):
                load_geojson(path)

    def test_local_server_serves_preview_and_validated_geojson_only(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/") as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"Pune-area ATM locations", response.read())
            with urlopen(base + "/data/pune_atms.geojson") as response:
                body = json.load(response)
                self.assertEqual(body["valid_feature_count"], 312)
            with self.assertRaises(Exception):
                urlopen(base + "/../../README.md")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the browser search helper")
class PreviewSearchTests(unittest.TestCase):
    records = [
        {"properties": {"osm_id": "1", "name": "North Bank ATM"}, "visible": True},
        {"properties": {"osm_id": "2", "name": None}, "visible": True},
        {"properties": {"osm_id": "3", "name": "South Bank ATM"}, "visible": False},
        {"properties": {"osm_id": "4", "name": "Central Branch"}, "visible": True},
    ]

    def state(self, query):
        script = r"""
const { searchView } = require(process.argv[1]);
const records = JSON.parse(process.argv[2]);
const state = searchView(records, process.argv[3], record => record.visible);
console.log(JSON.stringify({
  namedCount: state.namedCount,
  queryActive: state.queryActive,
  matchCount: state.matchCount,
  filteredIds: state.filteredRecords.map(record => record.properties.osm_id),
  visibleIds: state.visibleRecords.map(record => record.properties.osm_id),
}));
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script, str(PREVIEW_JS), json.dumps(self.records), query],
            check=True, capture_output=True, text=True,
        )
        return json.loads(completed.stdout)

    def test_empty_search_reports_named_records_without_claiming_all_are_named(self):
        state = self.state("")
        self.assertEqual(state["namedCount"], 3)
        self.assertFalse(state["queryActive"])
        self.assertIsNone(state["matchCount"])
        self.assertEqual(len(state["filteredIds"]), 4)

    def test_matching_search_filters_actual_osm_names_and_updates_counts(self):
        state = self.state("bank")
        self.assertEqual(state["namedCount"], 3)
        self.assertTrue(state["queryActive"])
        self.assertEqual(state["matchCount"], 2)
        self.assertEqual(state["filteredIds"], ["1", "3"])
        self.assertEqual(state["visibleIds"], ["1"])

    def test_no_result_search_has_zero_matches_and_visible_markers(self):
        state = self.state("no such atm")
        self.assertEqual(state["matchCount"], 0)
        self.assertEqual(state["filteredIds"], [])
        self.assertEqual(state["visibleIds"], [])

    def test_clearing_search_restores_all_valid_records(self):
        filtered = self.state("north")
        cleared = self.state("")
        self.assertEqual(filtered["filteredIds"], ["1"])
        self.assertEqual(cleared["filteredIds"], ["1", "2", "3", "4"])
        self.assertEqual(cleared["visibleIds"], ["1", "2", "4"])

    def test_counter_counts_match_filtered_and_current_view_records(self):
        state = self.state("bank")
        self.assertEqual(state["matchCount"], len(state["filteredIds"]))
        self.assertEqual(len(state["visibleIds"]), 1)
        self.assertLessEqual(len(state["visibleIds"]), state["matchCount"])


if __name__ == "__main__":
    unittest.main()
