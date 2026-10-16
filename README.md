# PCIRS — Predictive Cash-Out Intelligence & Response System

PCIRS is a prototype for analyzing reported incidents and transaction trails to rank potential cash-out candidate locations. The current demo uses a reproducible synthetic complaint, a three-hop transaction trail, and explainable heuristic scoring. It returns an illustrative 30–60 minute time window.

**Important:** This is a research prototype, not a validated cash-out prediction system. Scores are heuristic rankings, not probabilities, and have not been validated against observed cash-out events.

## Run locally (Windows PowerShell)

Run these commands from the project root.

### 1. Install dependencies

```powershell
.\.venv-clean\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Start the application

```powershell
.\.venv-clean\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Open the dashboard at <http://127.0.0.1:8000>.

### 3. Regenerate the demo sample

```powershell
.\.venv-clean\Scripts\python.exe -m src.data_generation.demo_scenario
```

This regenerates the deterministic JSON demo sample.

## API routes

The current application includes these routes:

- `GET /health` — health check.
- `GET /api/sample-case` — sample case.
- `POST /api/analyze` — analyze a complaint and rank candidate locations.
- `GET /api/overview` — demo overview metrics.
- `/api/cases/{case_id}/status` — case status.
- `/api/demo-prediction` — compatibility route for the demo prediction flow.

The dashboard is served at `/`. The six-stage workflow summary is displayed in the dashboard; it is not a separate architecture API.

`POST /api/analyze` supports the optional `candidate_source` query parameter:

- `synthetic_fixture` — the default candidate fixture.
- `osm` — mapped ATM locations from the processed OpenStreetMap data.

## Data provenance and limitations

### Synthetic data

The complaint, account links, multi-hop trail, and cash-out label are synthetic. PaySim is simulated transaction data; its first 100,000 rows are used for transfer-type and step references only. The reported complaint amount is copied unchanged across the generated hops.

Synthetic cash-out ground truth is not used as a prediction input. PaySim does not establish verified cybercrime or local cash-out activity.

### Candidate locations

The dashboard defaults to `data/external/synthetic_atm_candidates_pune.json`. This is a fixture of **fabricated candidate points, not real ATMs**.

The alternative OSM source contains mapped ATM locations, not withdrawal records or cash-out history. Mapped locations do not prove that a withdrawal occurred.

The demo does not have a verified, explicitly sourced incident origin. Its coordinate is synthetic, and no victim or account location is known. Consequently, geographic distance is not used to rank candidates by default.

### Scores and time windows

Risk and candidate scores are heuristic, not calibrated probabilities. The illustrative 30–60 minute window is not validated against observed cash-out events. Proximity to an ATM is not evidence of a withdrawal or cash-out event.

Overview metrics describe the bundled synthetic demo; they are not observed suspicious-case counts or verified cash-out counts.

### Case state and security

Case status is stored in process-local memory and resets when the server restarts. The workflow summary presents implemented validation and heuristic capabilities alongside planned machine-learning and security features. Do not interpret planned features as implemented or treat this local demo as production-ready.

## Optional geographic candidate filters

`POST /api/analyze` accepts optional `radius_km` and `bbox=west,south,east,north` query parameters.

### Radius filter

Radius filtering requires a `location_hint` in the request body containing:

- Valid WGS 84 latitude and longitude.
- `origin: "verified_incident_origin"`.
- A nonblank `source`.
- `is_verified_incident_origin: true`.

The application checks this provenance contract but cannot independently verify the source. Synthetic demo coordinates do not qualify.

If a radius is requested without a qualifying origin, radius filtering is skipped, and all valid candidates are retained with a status message.

### Bounding-box filter

An explicit bounding box can be applied independently of the radius filter. Bounds are inclusive and use WGS 84 coordinates. Antimeridian-crossing bounding boxes are unsupported.

### Distance behavior

Distance uses the haversine great-circle formula with a mean Earth radius of 6,371.0088 km. Points exactly on the radius boundary are included.

Spatial filtering is reported separately from candidate risk scores. When a spatial filter is requested, candidate risk scores and ranks remain unset. Without filter parameters, the existing demo defaults and API behavior are preserved.

## Run tests

Run the smoke tests:

```powershell
.\.venv-clean\Scripts\python.exe -m unittest discover -s tests -v
```

Run the complete pytest suite:

```powershell
.\.venv-clean\Scripts\python.exe -m pytest -q
```

The previously verified clean-environment baseline was **85 passed, with 43 subtests passed**. Rerun the suite after subsequent code changes to confirm the current result.

## Documentation

See these documents for more detail:

- `docs/PCIRS_PROJECT_HANDOVER.md`
- `docs/data_schema_and_synthetic_data_design.md`
- `docs/demo_and_progress_review_guide.md`
- `data/external/osm_pune/README.md`

