"""Shared input-to-prediction pipeline used by the UI and FastAPI routes."""

from src.preprocessing.complaint_validation import SAMPLE_CASE_INPUT, validate_complaint_input
from src.data_generation.demo_scenario import make_demo_scenario
from .candidate_locations import load_candidates
from .ranker import rank_candidates
from .spatial_filter import filter_candidates_spatially


DISCLAIMER = (
    "PaySim is simulated. Complaint, chain links, and cash-out ground truth are synthetic. "
    "The synthetic candidate fixture contains fabricated points. OSM, when selected, provides mapped "
    "locations only, not cash-out history. Heuristic scores are not probabilities and are not validated predictions."
)


def run_analysis(
    raw_input: dict,
    *,
    candidate_source: str = "synthetic_fixture",
    radius_km: float | None = None,
    bbox: str | tuple[float, float, float, float] | None = None,
) -> dict:
    """Validate a complaint, generate a pre-cutoff chain, traverse it, and rank locations."""
    normalized = validate_complaint_input(raw_input)
    scenario = make_demo_scenario(complaint_input=normalized)
    candidates, source_metadata = load_candidates(candidate_source)
    location_hint = raw_input.get("location_hint")
    filtered_candidates, spatial_metadata = filter_candidates_spatially(
        candidates, location_hint=location_hint, radius_km=radius_km, bbox=bbox,
    )
    ranking_complaint = dict(scenario["complaint"])
    # Candidate filtering is a separate geographic operation. Keep location
    # proximity out of candidate risk scores in the complaint-analysis path.
    ranking_complaint["location_hint"] = None
    result = rank_candidates(ranking_complaint, scenario["transactions"], filtered_candidates)
    return {
        "case": scenario["case"],
        "complaint": scenario["complaint"],
        "accounts": scenario["accounts"],
        "transactions": result["chain"],
        "hop_count": result["hop_count"],
        "case_risk_score": result["case_risk_score"],
        "score_label": result["score_label"],
        "estimated_time_window": result["estimated_time_window"],
        "ranked_candidates": result["ranked_candidates"],
        "candidate_source": source_metadata,
        "spatial_filter": spatial_metadata,
        "provenance": scenario["provenance"],
        "disclaimer": DISCLAIMER,
        "case_status": "New",
        "location_ranking_available": result["location_ranking_available"],
        "location_ranking_note": result["location_ranking_note"],
    }


def sample_case_input() -> dict:
    """Return a copy of the clearly synthetic prefilled sample-case inputs."""
    return dict(SAMPLE_CASE_INPUT)
