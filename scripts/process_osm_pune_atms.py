"""Validate a bounded BBBike OSM XML extract and write ATM-only GeoJSON/CSV."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "data" / "external" / "osm_pune"
DEFAULT_RAW = DEFAULT_BASE / "raw" / "planet_73.70,18.40_74.00,18.65.osm.gz"
DEFAULT_METADATA = DEFAULT_BASE / "metadata" / "acquisition.json"
DEFAULT_PROCESSED = DEFAULT_BASE / "processed"
OUTPUT_FIELDS = ["osm_id", "osm_type", "amenity", "name", "operator", "brand", "access",
                 "latitude", "longitude", "source", "retrieved_at"]
OPTIONAL_TAGS = ("name", "operator", "brand", "access")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def verify_raw(raw_path: Path, metadata_path: Path) -> dict[str, Any]:
    """Verify the compressed source against its acquisition record."""
    if not raw_path.is_file():
        raise FileNotFoundError(f"Raw OSM extract does not exist: {raw_path}")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Acquisition metadata does not exist: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    expected_name = metadata.get("raw_file", "").replace("\\", "/").split("/")[-1]
    if expected_name != raw_path.name:
        raise ValueError(f"Acquisition metadata raw_file does not match input name: {expected_name!r}")
    if metadata.get("source") != "OpenStreetMap data via BBBike custom extract":
        raise ValueError("Acquisition metadata does not identify the expected BBBike OSM source")
    boundary = metadata.get("boundary", {})
    expected_bounds = {"south": 18.40, "west": 73.70, "north": 18.65, "east": 74.00}
    if any(float(boundary.get(key, math.nan)) != value for key, value in expected_bounds.items()):
        raise ValueError("Acquisition metadata bounding box is missing or differs from the requested bounds")
    actual_hash = sha256_file(raw_path)
    if actual_hash != str(metadata.get("raw_sha256", "")).upper():
        raise ValueError(f"Raw OSM SHA-256 mismatch: recorded={metadata.get('raw_sha256')}, actual={actual_hash}")
    if raw_path.stat().st_size != int(metadata.get("raw_size_bytes", -1)):
        raise ValueError("Raw OSM file size differs from acquisition metadata")
    with raw_path.open("rb") as stream:
        if stream.read(2) != b"\x1f\x8b":
            raise ValueError("Raw input is not gzip-compressed")
    return metadata


def _polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Return planar lon/lat centroid for a closed, non-degenerate way ring."""
    twice_area = 0.0
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if math.isclose(twice_area, 0.0, abs_tol=1e-15):
        return None
    return cx / (3.0 * twice_area), cy / (3.0 * twice_area)


def parse_osm_gzip(raw_path: Path, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse ATM nodes and ways, resolving way geometry from node references.

    Closed ways use their planar polygon centroid in longitude/latitude. Open
    ways use the arithmetic mean of resolved vertices. Missing node references,
    degenerate geometry, and ATM relations are retained in the rejection audit.
    """
    boundary = metadata["boundary"]
    south, west = float(boundary["south"]), float(boundary["west"])
    north, east = float(boundary["north"]), float(boundary["east"])
    retrieval_date = metadata["retrieval_date"]
    element_counts: Counter[str] = Counter()
    atm_counts: Counter[str] = Counter()
    node_coordinates: dict[str, tuple[float, float]] = {}
    atm_nodes: list[tuple[str, dict[str, str], tuple[float, float] | None]] = []
    atm_ways: list[tuple[str, dict[str, str], list[str]]] = []
    atm_relations: list[tuple[str, dict[str, str]]] = []

    try:
        with gzip.open(raw_path, "rb") as stream:
            root_element = None
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    if root_element is None:
                        root_element = element
                    continue
                osm_type = element.tag
                if osm_type not in {"node", "way", "relation"}:
                    continue
                element_counts[osm_type] += 1
                osm_id = element.attrib.get("id", "")
                tags = {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")
                        if "k" in tag.attrib and "v" in tag.attrib}
                if osm_type == "node":
                    coordinate = None
                    try:
                        coordinate = (float(element.attrib["lat"]), float(element.attrib["lon"]))
                        if osm_id:
                            node_coordinates[osm_id] = coordinate
                    except (KeyError, ValueError):
                        pass
                    if tags.get("amenity") == "atm":
                        atm_counts[osm_type] += 1
                        atm_nodes.append((osm_id, tags, coordinate))
                elif tags.get("amenity") == "atm":
                    atm_counts[osm_type] += 1
                    if osm_type == "way":
                        atm_ways.append((osm_id, tags, [nd.attrib.get("ref", "") for nd in element.findall("nd")]))
                    else:
                        atm_relations.append((osm_id, tags))
                if root_element is not None:
                    root_element.remove(element)
                element.clear()
    except (OSError, EOFError, ET.ParseError) as exc:
        raise ValueError(f"Could not parse gzip-compressed OSM XML: {exc}") from exc

    records: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    osm_keys: set[tuple[str, str]] = set()
    duplicate_osm_ids = 0
    duplicate_coordinates = 0
    seen_coordinates: set[tuple[float, float]] = set()
    missing_tags: Counter[str] = Counter()
    outside_bounds = 0
    invalid_geometry = 0

    def add_record(osm_type: str, osm_id: str, tags: dict[str, str],
                   coordinate: tuple[float, float] | None) -> None:
        nonlocal duplicate_osm_ids, duplicate_coordinates, outside_bounds, invalid_geometry
        if not osm_id:
            rejected.append({"osm_type": osm_type, "osm_id": "", "reason": "missing OSM ID"})
            return
        key = (osm_type, osm_id)
        if key in osm_keys:
            duplicate_osm_ids += 1
            rejected.append({"osm_type": osm_type, "osm_id": osm_id, "reason": "duplicate OSM type/ID"})
            return
        osm_keys.add(key)
        if coordinate is None:
            invalid_geometry += 1
            rejected.append({"osm_type": osm_type, "osm_id": osm_id, "reason": "missing or unresolved geometry"})
            return
        lat, lon = coordinate
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            invalid_geometry += 1
            rejected.append({"osm_type": osm_type, "osm_id": osm_id, "reason": "invalid coordinate values"})
            return
        if not (south <= lat <= north and west <= lon <= east):
            outside_bounds += 1
            rejected.append({"osm_type": osm_type, "osm_id": osm_id, "reason": "representative coordinate outside extraction bounds"})
            return
        coordinate_key = (round(lat, 7), round(lon, 7))
        if coordinate_key in seen_coordinates:
            duplicate_coordinates += 1
        seen_coordinates.add(coordinate_key)
        for tag in OPTIONAL_TAGS:
            if not tags.get(tag):
                missing_tags[tag] += 1
        records.append({"osm_id": osm_id, "osm_type": osm_type, "amenity": tags["amenity"],
            "name": tags.get("name") or None, "operator": tags.get("operator") or None,
            "brand": tags.get("brand") or None, "access": tags.get("access") or None,
            "latitude": lat, "longitude": lon, "source": "OpenStreetMap",
            "retrieved_at": retrieval_date})

    for osm_id, tags, coordinate in atm_nodes:
        add_record("node", osm_id, tags, coordinate)
    unresolved_ways = 0
    for osm_id, tags, refs in atm_ways:
        if not refs or any(ref not in node_coordinates for ref in refs):
            unresolved_ways += 1
            add_record("way", osm_id, tags, None)
            continue
        points = [(node_coordinates[ref][1], node_coordinates[ref][0]) for ref in refs]  # lon, lat
        if len(points) < 2:
            add_record("way", osm_id, tags, None)
            continue
        if len(points) >= 4 and points[0] == points[-1]:
            coordinate = _polygon_centroid(points)
            if coordinate is None:
                add_record("way", osm_id, tags, None)
                continue
            lon, lat = coordinate
        else:
            lon = sum(point[0] for point in points) / len(points)
            lat = sum(point[1] for point in points) / len(points)
        add_record("way", osm_id, tags, (lat, lon))
    for osm_id, tags in atm_relations:
        invalid_geometry += 1
        rejected.append({"osm_type": "relation", "osm_id": osm_id,
                         "reason": "ATM relation geometry is not resolved by this parser"})

    report = {
        "total_raw_osm_elements": sum(element_counts.values()),
        "raw_elements_by_type": dict(sorted(element_counts.items())),
        "atm_tagged_by_type": dict(sorted(atm_counts.items())),
        "valid_processed_atm_records": len(records),
        "duplicate_osm_ids": duplicate_osm_ids,
        "duplicate_coordinates": duplicate_coordinates,
        "invalid_or_missing_geometry": invalid_geometry,
        "unresolved_atm_ways": unresolved_ways,
        "atm_relations_unresolved": len(atm_relations),
        "representative_coordinates_outside_boundary": outside_bounds,
        "missing_optional_tags": dict(sorted(missing_tags.items())),
        "rejected_records": rejected,
        "all_processed_records_tagged_amenity_atm": True,
        "coordinate_reference_system": "WGS 84 (EPSG:4326), GeoJSON coordinate order longitude, latitude",
        "way_coordinate_method": "Closed way: planar polygon centroid computed in lon/lat; open way: arithmetic mean of resolved vertices. All referenced nodes must resolve.",
    }
    return records, report


def write_outputs(records: list[dict[str, Any]], report: dict[str, Any], output_dir: Path,
                  metadata: dict[str, Any], raw_path: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / "pune_atms.geojson"
    csv_path = output_dir / "pune_atms.csv"
    report_path = output_dir / "validation_report.json"
    for path in (geojson_path, csv_path, report_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing processed output: {path}")
    feature_collection = {
        "type": "FeatureCollection",
        "attribution": metadata["attribution"],
        "license": metadata["license"],
        "features": [{"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
            "properties": {k: v for k, v in r.items() if k not in {"latitude", "longitude"}} | {
                "latitude": r["latitude"], "longitude": r["longitude"]}}
            for r in records],
    }
    geojson_path.write_text(json.dumps(feature_collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    report = {**report, "source": metadata["source"], "retrieval_date": metadata["retrieval_date"],
        "bounding_box": metadata["boundary"], "attribution": metadata["attribution"],
        "license": metadata["license"], "raw_file": str(raw_path),
        "raw_sha256_after_processing": sha256_file(raw_path),
        "outputs": [geojson_path.name, csv_path.name]}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return geojson_path, csv_path, report_path


def process(raw_path: Path, metadata_path: Path, output_dir: Path) -> dict[str, Any]:
    metadata = verify_raw(raw_path, metadata_path)
    records, report = parse_osm_gzip(raw_path, metadata)
    before = metadata["raw_sha256"]
    outputs = write_outputs(records, report, output_dir, metadata, raw_path)
    after = sha256_file(raw_path)
    if before.upper() != after.upper():
        raise RuntimeError("Raw OSM file changed during processing")
    return {**report, "raw_sha256_before": before, "raw_sha256_after": after,
            "processed_files": [str(path) for path in outputs]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_PROCESSED)
    args = parser.parse_args(argv)
    result = process(args.raw, args.metadata, args.output_dir)
    print(json.dumps({k: result[k] for k in ("total_raw_osm_elements", "raw_elements_by_type",
        "atm_tagged_by_type", "valid_processed_atm_records", "duplicate_osm_ids",
        "duplicate_coordinates", "invalid_or_missing_geometry", "unresolved_atm_ways",
        "atm_relations_unresolved", "representative_coordinates_outside_boundary",
        "missing_optional_tags", "processed_files")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
