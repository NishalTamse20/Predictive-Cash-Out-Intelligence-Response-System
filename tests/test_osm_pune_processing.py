import gzip
import json
import tempfile
import unittest
from pathlib import Path

from scripts.process_osm_pune_atms import parse_osm_gzip, process, sha256_file, verify_raw, write_outputs


BOUNDS = {"type": "Bounding box", "south": 18.40, "west": 73.70,
          "north": 18.65, "east": 74.00}


def write_osm(path: Path, xml: str) -> None:
    with gzip.open(path, "wb") as stream:
        stream.write(xml.encode("utf-8"))


def metadata_for(path: Path) -> dict:
    return {"source": "OpenStreetMap data via BBBike custom extract",
        "retrieval_date": "2026-10-12", "attribution": "© OpenStreetMap contributors",
        "license": "Open Database License (ODbL)", "raw_file": path.name,
        "raw_sha256": sha256_file(path), "raw_size_bytes": path.stat().st_size,
        "boundary": BOUNDS}


class OSMProcessingTests(unittest.TestCase):
    def fixture_xml(self) -> str:
        return '''<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
  <node id="1" lat="18.50" lon="73.80"><tag k="amenity" v="atm"/><tag k="name" v="Sample ATM"/><tag k="operator" v="Bank A"/></node>
  <node id="2" lat="18.50" lon="73.81"/><node id="3" lat="18.51" lon="73.81"/>
  <node id="4" lat="18.51" lon="73.80"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="amenity" v="atm"/><tag k="brand" v="Bank B"/></way>
  <way id="11"><nd ref="999"/><nd ref="2"/><tag k="amenity" v="atm"/></way>
  <way id="12"><nd ref="2"/><nd ref="3"/><tag k="building" v="yes"/></way>
  <relation id="20"><tag k="amenity" v="atm"/></relation>
</osm>'''

    def test_parser_keeps_atm_nodes_and_resolved_ways_and_audits_unresolved(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "sample.osm.gz"
            write_osm(raw, self.fixture_xml())
            records, report = parse_osm_gzip(raw, metadata_for(raw))
            self.assertEqual(report["total_raw_osm_elements"], 8)
            self.assertEqual(report["atm_tagged_by_type"], {"node": 1, "relation": 1, "way": 2})
            self.assertEqual([(r["osm_type"], r["osm_id"]) for r in records], [("node", "1"), ("way", "10")])
            self.assertTrue(all(r["amenity"] == "atm" for r in records))
            self.assertAlmostEqual(records[1]["latitude"], 18.505)
            self.assertAlmostEqual(records[1]["longitude"], 73.805)
            self.assertIsNone(records[0]["brand"])
            self.assertEqual(report["unresolved_atm_ways"], 1)
            self.assertEqual(report["atm_relations_unresolved"], 1)
            self.assertEqual(report["all_processed_records_tagged_amenity_atm"], True)

    def test_outputs_are_attributed_geojson_csv_and_do_not_modify_raw(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "sample.osm.gz"
            write_osm(raw, self.fixture_xml())
            before = sha256_file(raw)
            metadata = metadata_for(raw)
            records, report = parse_osm_gzip(raw, metadata)
            geojson, csv_path, report_path = write_outputs(records, report, root / "processed", metadata, raw)
            payload = json.loads(geojson.read_text(encoding="utf-8"))
            self.assertEqual(payload["attribution"], metadata["attribution"])
            self.assertEqual(payload["features"][0]["geometry"]["coordinates"], [73.8, 18.5])
            self.assertTrue(csv_path.is_file())
            self.assertTrue(report_path.is_file())
            self.assertEqual(sha256_file(raw), before)

    def test_raw_checksum_mismatch_stops_before_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "sample.osm.gz"
            write_osm(raw, self.fixture_xml())
            metadata = metadata_for(raw)
            metadata["raw_sha256"] = "0" * 64
            metadata_path = root / "acquisition.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                process(raw, metadata_path, root / "processed")
            self.assertFalse((root / "processed").exists())

    def test_raw_verification_checks_recorded_bounds_source_and_gzip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "sample.osm.gz"
            write_osm(raw, self.fixture_xml())
            metadata = metadata_for(raw)
            path = root / "acquisition.json"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(verify_raw(raw, path)["boundary"], BOUNDS)
            metadata["boundary"]["east"] = 75.0
            path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bounding box"):
                verify_raw(raw, path)

    def test_duplicate_coordinates_are_reported_but_records_are_preserved(self):
        xml = '''<osm version="0.6">
          <node id="1" lat="18.50" lon="73.80"><tag k="amenity" v="atm"/></node>
          <node id="2" lat="18.50" lon="73.80"><tag k="amenity" v="atm"/></node>
        </osm>'''
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "sample.osm.gz"
            write_osm(raw, xml)
            records, report = parse_osm_gzip(raw, metadata_for(raw))
            self.assertEqual(len(records), 2)
            self.assertEqual(report["duplicate_coordinates"], 1)
            self.assertEqual(report["duplicate_osm_ids"], 0)

    def test_out_of_bounds_atm_is_explicitly_rejected(self):
        xml = '<osm version="0.6"><node id="1" lat="18.90" lon="73.80"><tag k="amenity" v="atm"/></node></osm>'
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "sample.osm.gz"
            write_osm(raw, xml)
            records, report = parse_osm_gzip(raw, metadata_for(raw))
            self.assertEqual(records, [])
            self.assertEqual(report["representative_coordinates_outside_boundary"], 1)
            self.assertIn("outside extraction bounds", report["rejected_records"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
