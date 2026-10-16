"""Fail-closed geographic filtering for candidate coordinates in WGS 84."""

from __future__ import annotations

import math
from typing import Any

EARTH_MEAN_RADIUS_KM = 6371.0088
MAX_RADIUS_KM = math.pi * EARTH_MEAN_RADIUS_KM
BOUNDARY_TOLERANCE_KM = 1e-9


def _coordinate(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a numeric WGS 84 coordinate.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a numeric WGS 84 coordinate.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label} must be finite and within [{minimum}, {maximum}].")
    return number


def verified_incident_origin(location_hint: Any) -> tuple[dict[str, float] | None, str | None]:
    """Accept coordinates only with the existing explicit verified-origin contract."""
    if not isinstance(location_hint, dict):
        return None, "No location origin was supplied."
    if (location_hint.get("is_verified_incident_origin") is not True
            or location_hint.get("origin") != "verified_incident_origin"
            or not isinstance(location_hint.get("source"), str)
            or not location_hint["source"].strip()):
        return None, "Origin is missing verified incident-origin provenance and a nonblank source."
    try:
        latitude = _coordinate(location_hint.get("latitude"), "Origin latitude", -90, 90)
        longitude = _coordinate(location_hint.get("longitude"), "Origin longitude", -180, 180)
    except ValueError as exc:
        return None, str(exc)
    return {"latitude": latitude, "longitude": longitude}, None


def haversine_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Great-circle distance using the haversine formula and mean Earth radius, in km."""
    lat_a = _coordinate(latitude_a, "Latitude A", -90, 90)
    lon_a = _coordinate(longitude_a, "Longitude A", -180, 180)
    lat_b = _coordinate(latitude_b, "Latitude B", -90, 90)
    lon_b = _coordinate(longitude_b, "Longitude B", -180, 180)
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    return 2 * EARTH_MEAN_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def _parse_bbox(bbox: str | tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    if isinstance(bbox, str):
        parts = [part.strip() for part in bbox.split(",")]
    elif isinstance(bbox, (tuple, list)):
        parts = list(bbox)
    else:
        raise ValueError("Bounding box must be west,south,east,north.")
    if len(parts) != 4:
        raise ValueError("Bounding box must contain west,south,east,north.")
    west = _coordinate(parts[0], "Bounding-box west", -180, 180)
    south = _coordinate(parts[1], "Bounding-box south", -90, 90)
    east = _coordinate(parts[2], "Bounding-box east", -180, 180)
    north = _coordinate(parts[3], "Bounding-box north", -90, 90)
    if west >= east or south >= north:
        raise ValueError("Bounding box must have west < east and south < north; antimeridian-crossing boxes are not supported.")
    return west, south, east, north


def filter_candidates_spatially(
    candidates: list[dict],
    *,
    location_hint: Any = None,
    radius_km: float | None = None,
    bbox: str | tuple[float, float, float, float] | None = None,
) -> tuple[list[dict], dict]:
    """Apply requested filters without assigning or changing a fraud/risk score.

    Radius requires a verified, sourced origin. A bbox is itself an explicit
    area selection and can apply without an origin. When both are requested,
    their intersection is returned; radius is skipped if origin validation fails.
    """
    parsed_bbox = _parse_bbox(bbox)
    if radius_km is not None:
        radius = _coordinate(radius_km, "Radius", 0, MAX_RADIUS_KM)
        if radius <= 0:
            raise ValueError("Radius must be greater than 0 km.")
    else:
        radius = None

    requested = radius is not None or parsed_bbox is not None
    origin, origin_error = verified_incident_origin(location_hint)
    # A caller claiming the verified-origin contract must not have malformed
    # coordinates silently treated as if the origin were merely absent. This
    # distinction lets missing/unverified origins fail closed with a message,
    # while malformed verified inputs become an actionable API validation error.
    if (radius is not None and origin is None and isinstance(location_hint, dict)
            and location_hint.get("is_verified_incident_origin") is True
            and location_hint.get("origin") == "verified_incident_origin"
            and isinstance(location_hint.get("source"), str)
            and location_hint["source"].strip()):
        _coordinate(location_hint.get("latitude"), "Origin latitude", -90, 90)
        _coordinate(location_hint.get("longitude"), "Origin longitude", -180, 180)
    metadata = {
        "requested": requested,
        "applied": False,
        "radius_requested_km": radius,
        "radius_applied": False,
        "bbox_requested": list(parsed_bbox) if parsed_bbox is not None else None,
        "bbox_applied": False,
        "origin_contract_accepted": origin is not None,
        "origin_source": location_hint.get("source") if origin is not None else None,
        "distance_method": "Haversine great-circle distance using mean Earth radius 6371.0088 km",
        "input_candidate_count": len(candidates),
        "result_candidate_count": len(candidates),
        "message": "No spatial filter was requested; all valid candidates were retained." if not requested else "",
    }
    if not requested:
        return candidates, metadata

    selected = list(candidates)
    if parsed_bbox is not None:
        west, south, east, north = parsed_bbox
        selected = [
            candidate for candidate in selected
            if west <= float(candidate["longitude"]) <= east
            and south <= float(candidate["latitude"]) <= north
        ]
        metadata["bbox_applied"] = True

    if radius is not None:
        if origin is None:
            radius_message = f"Radius filter was not applied: {origin_error}"
        else:
            radius_selected = []
            for candidate in selected:
                distance = haversine_km(
                    origin["latitude"], origin["longitude"],
                    float(candidate["latitude"]), float(candidate["longitude"]),
                )
                if distance <= radius + BOUNDARY_TOLERANCE_KM:
                    radius_selected.append({**candidate, "spatial_distance_km": round(distance, 6)})
            selected = radius_selected
            metadata["radius_applied"] = True
            radius_message = f"Radius filter applied at {radius:g} km from the verified origin."
    else:
        radius_message = ""

    metadata["applied"] = metadata["bbox_applied"] or metadata["radius_applied"]
    metadata["result_candidate_count"] = len(selected)
    messages = []
    if metadata["bbox_applied"]:
        messages.append("Bounding-box filter applied.")
    if radius_message:
        messages.append(radius_message)
    if not metadata["applied"]:
        messages.append("No spatial filter was applied; all valid candidates were retained.")
    if metadata["applied"] and not selected:
        messages.append("No candidates matched the requested spatial filter.")
    elif metadata["applied"]:
        messages.append(f"Retained {len(selected)} of {len(candidates)} candidates.")
    metadata["message"] = " ".join(messages)
    return selected, metadata
