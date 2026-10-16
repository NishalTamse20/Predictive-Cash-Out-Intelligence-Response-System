"""FastAPI app serving the PCIRS review demo and API."""

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse

from backend.case_state import get_case_status, register_case, update_case_status
from backend.case_state import get_case_status_counts
from src.prediction.analysis_pipeline import run_analysis, sample_case_input

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = PROJECT_ROOT / "dashboard" / "index.html"
CANDIDATE_MAP_SCRIPT_PATH = PROJECT_ROOT / "dashboard" / "candidate_map.js"
app = FastAPI(title="PCIRS Local Demo", version="0.1.0", description="Synthetic research prototype; not validated on real cash-out events.")


class ComplaintRequest(BaseModel):
    reported_amount_inr: float
    report_time: str
    starting_account_id: str
    reporting_delay_minutes: int
    location_hint: dict[str, Any] | None = None


class CaseStatusRequest(BaseModel):
    status: Literal["New", "Under Investigation", "Reviewed"]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "PCIRS Local Demo"}


@app.get("/api/sample-case")
def sample_case() -> dict:
    """Return clearly labelled prefilled inputs for the interactive sample case."""
    return sample_case_input()



@app.get("/api/overview")
def overview() -> dict:
    """Metrics for the bundled synthetic sample and this API process."""
    try:
        sample = run_analysis(sample_case_input())
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Demo overview unavailable: {exc}") from exc
    return {"available": True, "synthetic_demo_statistics": True,
        "metrics": {"total_demo_transactions": len(sample["transactions"]),
        "heuristic_high_risk_scenarios": int(sample["case_risk_score"] >= 70),
        "sample_case_risk_score": sample["case_risk_score"], "high_risk_threshold": 70,
        "case_status_counts": get_case_status_counts()},
        "metric_notes": ["One bundled synthetic sample only; not real incidents or observed suspicious cases.",
        "High-risk count uses the existing heuristic and is not a probability or validated prediction.",
        "Case statuses are process-local and reset when the server restarts."]}


@app.post("/api/analyze")
def analyze_complaint(
    request: ComplaintRequest,
    candidate_source: Literal["synthetic_fixture", "osm"] = "synthetic_fixture",
    radius_km: float | None = None,
    bbox: str | None = None,
) -> dict:
    """Analyze a complaint and optionally spatially filter its candidate locations."""
    try:
        result = run_analysis(
            request.model_dump(exclude_none=True),
            candidate_source=candidate_source,
            radius_km=radius_km,
            bbox=bbox,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=f"Analysis could not be completed: {exc}") from exc
    result["case_status"] = register_case(result["case"]["case_id"])
    return result


@app.get("/api/demo-prediction")
def demo_prediction() -> dict:
    """Backward-compatible GET route for the reproducible sample prediction."""
    try:
        result = run_analysis(sample_case_input())
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=f"Demo could not be generated: {exc}") from exc
    result["case_status"] = register_case(result["case"]["case_id"])
    return result


@app.put("/api/cases/{case_id}/status")
def set_case_status(case_id: str, request: CaseStatusRequest) -> dict:
    """Update in-memory status for a case in this local demo process only."""
    try:
        status = update_case_status(case_id, request.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"case_id": case_id, "status": status, "persistence": "process-local; resets when server restarts"}


@app.get("/")
def dashboard() -> FileResponse:
    if not DASHBOARD_PATH.is_file():
        raise HTTPException(status_code=500, detail=f"Dashboard file not found: {DASHBOARD_PATH}")
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/assets/candidate-map.js")
def candidate_map_script() -> FileResponse:
    if not CANDIDATE_MAP_SCRIPT_PATH.is_file():
        raise HTTPException(status_code=500, detail=f"Candidate map script not found: {CANDIDATE_MAP_SCRIPT_PATH}")
    return FileResponse(CANDIDATE_MAP_SCRIPT_PATH, media_type="text/javascript")
