"""Load explicitly selected OSM or synthetic geographic candidate records."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "data" / "external" / "synthetic_atm_candidates_pune.json"
OSM_PATH = PROJECT_ROOT / "data" / "external" / "osm_pune" / "processed" / "pune_atms.geojson"
OPTIONAL_OSM_TAGS = ("name", "operator", "brand", "access")


def _read_json(path: Path, source_name: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {source_name} candidate data at {path}: {exc}") from exc


def _valid_osm_candidate(feature: Any, index: int, seen_ids: set[tuple[str, str]]) -> tuple[dict | None, str | None]:
    prefix = f"feature {index}"
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return None, f"{prefix}: expected a GeoJSON Feature"
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None, f"{prefix}: geometry must be a Point"
    if not isinstance(properties, dict):
        return None, f"{prefix}: properties must be an object"
    if properties.get("amenity") != "atm":
        return None, f"{prefix}: amenity must be 'atm'"

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        return None, f"{prefix}: Point coordinates must contain longitude and latitude"
    lon_value, lat_value = coordinates
    if isinstance(lon_value, bool) or isinstance(lat_value, bool):
        return None, f"{prefix}: coordinates must be numeric"
    try:
        longitude, latitude = float(lon_value), float(lat_value)
    except (TypeError, ValueError, OverflowError):
        return None, f"{prefix}: coordinates must be numeric"
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None, f"{prefix}: coordinates must be finite"
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, f"{prefix}: coordinates are outside WGS 84 ranges"

    try:
        property_latitude = float(properties["latitude"])
        property_longitude = float(properties["longitude"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, f"{prefix}: latitude/longitude properties are missing or invalid"
    if not (math.isclose(latitude, property_latitude, abs_tol=1e-7)
            and math.isclose(longitude, property_longitude, abs_tol=1e-7)):
        return None, f"{prefix}: properties coordinates do not match Point geometry"

    osm_id = properties.get("osm_id")
    osm_type = properties.get("osm_type")
    if (isinstance(osm_id, bool) or not isinstance(osm_id, (str, int))
            or str(osm_id).strip() == "" or not isinstance(osm_type, str)
            or osm_type not in {"node", "way", "relation"}):
        return None, f"{prefix}: valid osm_id and osm_type are required"
    osm_key = (str(osm_type), str(osm_id))
    if osm_key in seen_ids:
        return None, f"{prefix}: duplicate OSM element {osm_type}/{osm_id}"

    for tag in OPTIONAL_OSM_TAGS:
        if properties.get(tag) is not None and not isinstance(properties[tag], str):
            return None, f"{prefix}: optional OSM tag {tag!r} must be text or null"
    for field in ("source", "retrieved_at"):
        if properties.get(field) is not None and not isinstance(properties[field], str):
            return None, f"{prefix}: {field!r} must be text or null"

    seen_ids.add(osm_key)
    name = properties.get("name")
    candidate = {
        "candidate_id": f"OSM-{osm_type}-{osm_id}",
        "name": name,
        "name_status": "OSM name" if name and name.strip() else "No name tag in OSM",
        "address": None,
        "latitude": latitude,
        "longitude": longitude,
        "source": properties.get("source") or "OpenStreetMap",
        "is_synthetic_fixture": False,
        "osm_id": str(osm_id),
        "osm_type": osm_type,
        "amenity": "atm",
        "operator": properties.get("operator"),
        "brand": properties.get("brand"),
        "access": properties.get("access"),
        "retrieved_at": properties.get("retrieved_at"),
    }
    return candidate, None


def load_osm_candidates(path: Path = OSM_PATH) -> tuple[list[dict], dict]:
    """Load valid ATM Points from the processed OSM GeoJSON; never use fixture fallback."""
    payload = _read_json(path, "OpenStreetMap")
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError(f"Invalid OSM ATM GeoJSON at {path}: expected a FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError(f"Invalid OSM ATM GeoJSON at {path}: features must be an array")
    attribution = payload.get("attribution")
    license_name = payload.get("license")
    if not isinstance(attribution, str) or not attribution.strip():
        raise ValueError(f"Invalid OSM ATM GeoJSON at {path}: attribution is required")
    if not isinstance(license_name, str) or not license_name.strip():
        raise ValueError(f"Invalid OSM ATM GeoJSON at {path}: license is required")

    candidates: list[dict] = []
    errors: list[str] = []
    seen_ids: set[tuple[str, str]] = set()
    for index, feature in enumerate(features, start=1):
        candidate, error = _valid_osm_candidate(feature, index, seen_ids)
        if error:
            errors.append(error)
        elif candidate is not None:
            candidates.append(candidate)
    if not candidates:
        detail = "; ".join(errors[:5]) or "the FeatureCollection contains no features"
        raise ValueError(f"No valid ATM candidates found in {path}; rejected {len(errors)} feature(s). {detail}")

    metadata = {
        "mode": "osm",
        "source": "OpenStreetMap",
        "attribution": attribution,
        "license": license_name,
        "retrieved_at": next((item["retrieved_at"] for item in candidates if item["retrieved_at"]), None),
        "source_feature_count": len(features),
        "valid_candidate_count": len(candidates),
        "rejected_feature_count": len(errors),
        "validation_errors": errors[:10],
        "disclaimer": "Mapped ATM locations only; no withdrawal history or confirmed cash-out locations are represented.",
    }
    return candidates, metadata


def load_synthetic_candidates(path: Path = FIXTURE_PATH) -> tuple[list[dict], dict]:
    """Load the clearly labelled synthetic fallback fixture."""
    fixture = _read_json(path, "synthetic fixture")
    if not isinstance(fixture, dict) or fixture.get("is_synthetic_fixture") is not True:
        raise ValueError(f"Synthetic candidate fixture at {path} is missing its synthetic marker")
    source_candidates = fixture.get("candidates")
    if not isinstance(source_candidates, list) or not source_candidates:
        raise ValueError(f"Synthetic candidate fixture at {path} must contain candidates")
    candidates = []
    for index, candidate in enumerate(source_candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"Synthetic candidate {index} in {path} must be an object")
        try:
            latitude, longitude = float(candidate["latitude"]), float(candidate["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Synthetic candidate {index} in {path} has invalid coordinates") from exc
        if not (math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"Synthetic candidate {index} in {path} has out-of-range coordinates")
        candidates.append({
            **candidate,
            "name": f"Simulated Location {index}",
            "name_status": "Fabricated demo candidate; not a real ATM",
            "address": None,
            "latitude": latitude,
            "longitude": longitude,
            "source": "synthetic_fixture",
            "is_synthetic_fixture": True,
        })
    return candidates, {
        "mode": "synthetic_fixture",
        "source": fixture.get("source", "PCIRS synthetic demo fixture"),
        "is_synthetic_fixture": True,
        "disclaimer": fixture.get("disclaimer", "Fabricated demo candidates; not real ATM locations."),
    }


def load_candidates(
    source: str = "synthetic_fixture",
    *,
    osm_path: Path = OSM_PATH,
    fixture_path: Path = FIXTURE_PATH,
) -> tuple[list[dict], dict]:
    """Load the named source. Synthetic fallback is explicit and never automatic."""
    if source == "osm":
        return load_osm_candidates(osm_path)
    if source == "synthetic_fixture":
        return load_synthetic_candidates(fixture_path)
    raise ValueError("candidate source must be 'osm' or 'synthetic_fixture'")
