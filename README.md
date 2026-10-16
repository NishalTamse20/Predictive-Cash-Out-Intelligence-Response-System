# PCIRS local demo

The review demo uses a reproducible synthetic complaint and 3-hop transaction trail. PaySim's first 100,000 rows supply transfer type/step references only; the reported complaint amount is copied unchanged across the generated hops. The app ranks candidate locations with an explainable heuristic and returns an illustrative 30-60 minute window.

## Run locally (Windows PowerShell)

From the project root:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Open <http://127.0.0.1:8000>. The six-stage workflow summary is displayed in the dashboard; it is not a separate architecture API. Routes include `/health`, `/api/sample-case`, `/api/analyze`, `/api/overview`, `/api/cases/{case_id}/status`, and the compatibility route `/api/demo-prediction`.

Regenerate the deterministic JSON sample with:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m src.data_generation.demo_scenario
```

Install declared dependencies into the project environment, if needed, with `.\.venv-pcirs-test\Scripts\python.exe -m pip install -r requirements.txt`.

## Provenance notice

The complaint, account links, multi-hop trail, and cash-out label are synthetic. PaySim contributes simulated transfer type/step references only; generated hop amounts use the reported complaint amount. The dashboard defaults to `data/external/synthetic_atm_candidates_pune.json`, a fixture of **fabricated candidate points, not real ATMs**. A source selector can instead load the validated OSM ATM GeoJSON; those are mapped locations only, not cash-out history. The complaint flow has no verified, explicitly sourced incident origin, so neither source receives geographic distance ranking. Scores are heuristic, not probabilities, and have not been validated against observed cash-out events.

Run smoke checks with:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m unittest discover -s tests -v
```

Run the complete pytest suite and check the retained project environment with:

```powershell
.\scripts\run_tests.ps1
```

## Current implementation status

The dashboard shows metrics for its bundled synthetic sample, a six-stage workflow summary, interactive complaint analysis, a transaction trail graph, candidate locations from an explicitly selected source, and process-local case status. Overview figures are synthetic demo statistics only; there are no observed suspicious-case or verified cash-out counts.

The graph is rendered from transactions returned by the current analysis response. It highlights the starting account and intermediate synthetic mule accounts. The workflow summary reports implemented validation and heuristic capabilities alongside planned security and ML features.

PaySim is simulated transaction data and is not evidence of verified cybercrime or local cash-out activity. Candidate fixture points are simulated, not verified ATMs. OSM mode uses mapped ATM locations and does not provide withdrawal records. The demo's only coordinate is explicitly synthetic; no victim/account location is known. Heuristic risk and candidate scores are not probabilities, and the estimated time window is illustrative and unvalidated. Synthetic cash-out ground truth is not used as a prediction input. Case status is process-local and resets when the server restarts.

API routes include /health, /api/sample-case, /api/analyze, /api/cases/{case_id}/status, /api/overview, and the compatibility /api/demo-prediction. POST `/api/analyze` accepts the optional query parameter `candidate_source=synthetic_fixture|osm`; the default remains `synthetic_fixture`. The dashboard is served at /.

See docs/demo_and_progress_review_guide.md for the walkthrough, architecture status, limitations, and next steps.

## Optional geographic candidate filters

`POST /api/analyze` accepts optional `radius_km` and `bbox=west,south,east,north` query parameters. Radius filtering also requires a `location_hint` in the request body with valid WGS 84 latitude/longitude, `origin: "verified_incident_origin"`, a nonblank `source`, and `is_verified_incident_origin: true`. The app checks this provenance contract but cannot independently corroborate the source. Synthetic demo coordinates never qualify. If radius is requested without a qualifying origin, it is skipped and all valid candidates are retained with a clear status. An explicit bounding box can filter on its own; bounds are inclusive, WGS 84, and antimeridian-crossing boxes are unsupported.

Distance uses the haversine great-circle formula with a mean Earth radius of 6,371.0088 km; points exactly on the radius boundary are included. Spatial filtering reports its result separately from candidate risk scores. A requested spatial filter leaves candidate risk scores and ranks unset; proximity is not evidence of a withdrawal or cash-out event. With no filter parameters, the existing demo defaults and API behavior are preserved.
