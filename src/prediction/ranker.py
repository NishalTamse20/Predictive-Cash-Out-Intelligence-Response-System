"""Transparent heuristic ranking; synthetic ground truth is never an input."""

from __future__ import annotations

from datetime import datetime, timedelta

from .transaction_graph import find_trail, validate_chain
from .spatial_filter import haversine_km, verified_incident_origin


def rank_candidates(complaint: dict, transactions: list[dict], candidates: list[dict]) -> dict:
    """Rank by proximity only when a valid coordinate hint exists; never read target labels."""
    trail = find_trail(transactions, complaint["victim_account_id"])
    validate_chain(trail, complaint["victim_account_id"])

    final_time = datetime.fromisoformat(trail[-1]["event_time"])
    first_time = datetime.fromisoformat(trail[0]["event_time"])
    duration_minutes = max((final_time - first_time).total_seconds() / 60, 1)
    velocity_score = min(100.0, len(trail) / duration_minutes * 100)
    hop_score = min(100.0, len(trail) / 4 * 100)
    case_risk = round(0.65 * velocity_score + 0.35 * hop_score, 1)

    # The current demo does not supply a verified, sourced incident origin.
    origin, _origin_error = verified_incident_origin(complaint.get("location_hint"))
    origin_label = "verified_incident_origin" if origin is not None else None
    distances = None
    max_distance = None
    if origin is not None and candidates:
        distances = [haversine_km(origin["latitude"], origin["longitude"], float(c["latitude"]), float(c["longitude"])) for c in candidates]
        max_distance = max(max(distances), 0.1)
    distance_basis = origin_label or "unavailable"

    ranked = []
    if distances is None:
        for candidate in candidates:
            ranked.append({
                "candidate_id": candidate["candidate_id"],
                "name": candidate.get("name"),
                "latitude": float(candidate["latitude"]),
                "longitude": float(candidate["longitude"]),
                "name_status": candidate.get("name_status", "Name unavailable"),
                "address": candidate.get("address"),
                "source": candidate["source"],
                "is_synthetic_fixture": bool(candidate["is_synthetic_fixture"]),
                "distance_km_from_synthetic_complaint_hint": None,
                "distance_km_from_origin_hint": None,
                "spatial_distance_km": candidate.get("spatial_distance_km"),
                "distance_basis": "unavailable",
                "risk_score": None,
                "score_label": "not ranked; no valid geographic origin coordinate",
                "explanation": "No location-based score was produced because the complaint has no valid geographic origin coordinate.",
                "ranking_reason": "Not ranked: no valid geographic origin coordinate was supplied.",
                "rank": None,
                **{key: candidate.get(key) for key in ("osm_id", "osm_type", "amenity", "operator", "brand", "access", "retrieved_at") if key in candidate},
            })
    else:
        for candidate, distance in zip(candidates, distances):
            proximity_score = 100 * (1 - distance / max_distance) if max_distance > 0 else 100
            proximity_score = max(0.0, proximity_score)
            score = round(0.70 * proximity_score + 0.30 * case_risk, 1)
            distance_rank = sorted(distances).index(distance) + 1
            if distance_rank == 1:
                reason = f"Nearest candidate to the {origin_label} coordinate hint ({distance:.1f} km); receives the strongest distance component."
            else:
                reason = f"{distance:.1f} km from the {origin_label} coordinate hint; ranks lower on distance than {distance_rank - 1} closer candidate(s)."
            distance_description = "Geographic distance from verified incident-origin coordinate"
            ranked.append({
                "candidate_id": candidate["candidate_id"],
                "name": candidate.get("name"),
                "latitude": float(candidate["latitude"]),
                "longitude": float(candidate["longitude"]),
                "name_status": candidate.get("name_status", "Name unavailable"),
                "address": candidate.get("address"),
                "source": candidate["source"],
                "is_synthetic_fixture": bool(candidate["is_synthetic_fixture"]),
                "distance_km_from_synthetic_complaint_hint": None,
                "distance_km_from_origin_hint": round(distance, 2),
                "spatial_distance_km": candidate.get("spatial_distance_km"),
                "distance_basis": distance_basis,
                "distance_description": distance_description,
                "risk_score": score,
                "score_label": "heuristic ranking score (0–100), not a probability",
                "explanation": f"Distance score {proximity_score:.1f}/100; Transaction trail score {case_risk:.1f}/100.",
                "ranking_reason": reason,
                **{key: candidate.get(key) for key in ("osm_id", "osm_type", "amenity", "operator", "brand", "access", "retrieved_at") if key in candidate},
            })
    if distances is not None:
        ranked.sort(key=lambda item: (-item["risk_score"], item["candidate_id"]))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
    else:
        ranked.sort(key=lambda item: item["candidate_id"])
    cutoff = datetime.fromisoformat(complaint["reported_at"])
    window_start = cutoff + timedelta(minutes=30)
    window_end = cutoff + timedelta(minutes=60)
    return {
        "chain": trail,
        "hop_count": len(trail),
        "case_risk_score": case_risk,
        "score_label": "heuristic risk score (0–100), not a probability",
        "estimated_time_window": {"start": window_start.isoformat(), "end": window_end.isoformat(), "basis": "fixed 30–60 minutes after complaint cutoff; demo heuristic"},
        "ranked_candidates": ranked,
        "prediction_inputs_exclude_ground_truth": True,
        "location_ranking_available": distances is not None,
        "location_ranking_note": (
            "Distances use a verified, explicitly sourced incident-origin coordinate."
            if distances is not None
            else "No candidates matched the requested spatial filter."
            if not candidates
            else "No verified, explicitly sourced incident-origin coordinate was provided; candidate locations are not geographically ranked."
        ),
    }
