# PCIRS Project Handover

**Project:** Predictive Cash-Out Intelligence & Response System (PCIRS)  
**Workspace:** `C:\Users\Sujal Pise\Desktop\4th Year\PBL\PCIRS`  
**Updated:** 16 October 2026  
**Purpose:** Give a new ChatGPT/Codex session accurate project context and safe next steps.

## 1. Current verified status

PCIRS is a local research prototype with an interactive FastAPI dashboard, a synthetic complaint-analysis workflow, optional candidate-source selection and spatial filters, and a separate Pune OpenStreetMap (OSM) data preview. It is not a validated cash-out prediction product.

The retained project test environment is `.venv-pcirs-test` (Python 3.12.2); the user has confirmed Python works in the actual Windows environment. An earlier full suite passed **74 tests and 41 subtests**. A later user-reported full run collected 81 tests and finished with **80 passed, 1 failed, 41 subtests passed**; the failure was a Windows `PermissionError` during v4 staging-directory publication. A bounded retry for transient WinError 5 and a regression test were added, but the current execution environment could not launch the venv to verify that fix.

The latest v4 change adds a synthetic candidate-location signal (details below). Its focused tests have also not run here: the venv launcher exited before pytest with `No Python at '"C:\Users\Sujal Pise\AppData\Local\Programs\Python\Python312\python.exe'`. This is an execution-environment limitation, not evidence that Python is missing in the user's Windows terminal. Do not report the latest changes as passing until the user runs the commands in Section 4.

Earlier app smoke checks returned HTTP 200 for `/health`, `/`, `/api/overview`, and `/api/sample-case`; a sample `/api/analyze` request returned 5 candidates for `synthetic_fixture` and 312 for `osm`. The temporary server was stopped after those checks. These checks predate the latest v4 changes.

No browser click-through was performed during that verification. Unit/API tests and HTTP checks do not establish visual browser behavior.

## 2. Run the project

From the project root in PowerShell:

```powershell
.\.venv-pcirs-test\Scripts\Activate.ps1
python -m uvicorn backend.main:app --reload
```

Open <http://127.0.0.1:8000>. Stop the server with **Ctrl+C**. Alternatively, invoke the environment directly:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Run tests:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m pytest -v
```

The only intended project environment is `.venv-pcirs-test`. `.venv` and `.venv-pcirs-312` were deleted on 16 October 2026. Do not recreate them unless there is a specific need. The declared dependencies in `requirements.txt` are FastAPI, Uvicorn, pandas, and NetworkX. Do not install packages globally.

## 3. What is implemented

### Synthetic v4 candidate-location signal (latest work; not yet test-verified)

- `scripts/generate_synthetic_scenarios_v4.py` keeps the fictional Pune-area candidate grid for the existing 2025 scenario dates and adds an explicitly fictional scenario origin to `prediction_scenarios.csv` (`origin_kind`, coordinates, and `origin_available_at`). `scenario_candidate_universe.csv` now contains `origin_distance_km` and `distance_rank`; the validator checks both against the origin and ensures they are available by the prediction cutoff.
- Positive target locations are selected with weighted random choice: `0.15 + exp(-origin_distance_km / 2.5)`. This gives nearer candidates higher probability while retaining a nonzero chance for every eligible candidate. The formula and parameters are public in the generation manifest; the selected target ID remains in private labels.
- Scenario occurrence is assigned at configured prevalence independently of prediction inputs, so the current v4 generator intentionally provides no scenario-level occurrence signal. The useful simulated task is candidate-level ranking for positive scenarios.
- The observed transaction generation path is shared across positive and negative cases. Independent namespaced streams cover transaction chains, identifiers, class assignment, candidate selection, origins, target choice, outcome timing, and row order. The private seed is neither stored nor printed.
- Private label rows include `outcome_observation_end_at`, exactly 72 hours after cutoff for both positive and negative scenarios. Positive event windows must fit inside that horizon. The horizon is intended to inform future temporal purging/embargo; no splits exist yet.
- `tests/test_synthetic_v4.py` adds cutoff-feature, class-independent-transaction, large-sample target-selection, horizon, and invalid candidate-count checks. The target-selection test creates an 800-scenario temporary fixture and cleans it up; no standalone dataset was generated for this work.
- `docs/data_schema_and_synthetic_data_design.md` distinguishes occurrence prediction from candidate ranking and documents the synthetic assumptions and evaluation limitations.
- The fictional origin/grid/rule are not evidence of actual incident origins, mapped ATMs, or withdrawals. V4 still has no graph adapter, candidate feature pipeline, train/validation/test split, or test-label isolation. The existing graph feature contract is not yet integrated with these v4 candidate features.

### FastAPI complaint demo

- `backend/main.py` serves `dashboard/index.html` and provides `/health`, `/api/sample-case`, `/api/analyze`, `/api/overview`, `/api/cases/{case_id}/status`, and the compatibility route `/api/demo-prediction`.
- `src/preprocessing/complaint_validation.py` validates and normalizes complaint inputs.
- `src/data_generation/demo_scenario.py` creates a deterministic fictional three-hop transfer trail. PaySim is simulated data; configured PaySim records supply transaction type/step references, while the complaint amount is copied to the generated hops.
- `src/prediction/transaction_graph.py` checks and traverses the directed transaction trail.
- `src/prediction/analysis_pipeline.py` connects complaint validation, synthetic trail generation, candidate loading, and ranking/filtering into the API analysis result.
- `src/prediction/ranker.py` produces explainable heuristic outputs. These are not calibrated probabilities, validated predictions, or evidence of fraud.
- `backend/case_state.py` stores case status in process memory. Status resets when the server restarts.
- `dashboard/index.html` contains the complaint form, synthetic demo overview, six-stage workflow summary, analysis results, transaction trail, risk indicator, candidate-source controls, spatial-filter controls, candidate details/map, and case status controls.
- `dashboard/candidate_map.js` maps candidates returned by the latest analysis response. It should not independently load/filter a second candidate dataset. Missing coordinates are excluded from map markers; OSM-supplied text is rendered safely.

The request's synthetic origin is not considered a verified real-world origin. The API contract requires valid coordinates plus explicit verified-origin provenance before applying a radius filter; the app checks the submitted contract but cannot independently corroborate its source. A bounding box can filter without an origin. Spatial distance is separate from risk score; a requested spatial filter does not establish that an ATM was used for cash-out.

### Candidate sources and geographic filters

- `src/prediction/candidate_locations.py` supports explicit `synthetic_fixture` and `osm` source selection. Synthetic fixture remains the default; OSM mode does not silently fall back to synthetic candidates.
- `data/external/synthetic_atm_candidates_pune.json` contains fabricated candidate points and must be labelled synthetic.
- `data/external/osm_pune/processed/pune_atms.geojson` supplies 312 validated OSM ATM Point records. They are mapped ATM locations, not withdrawal events or confirmed cash-out locations.
- `src/prediction/spatial_filter.py` implements radius filtering with the Haversine great-circle formula (mean Earth radius 6,371.0088 km) and inclusive bounding-box filtering. A radius boundary is inclusive; radius and box filters combine by intersection.
- `POST /api/analyze` accepts `candidate_source=synthetic_fixture|osm`, `radius_km`, and `bbox=west,south,east,north`. Without filter parameters, existing demo behavior is preserved. If radius is requested without a qualifying origin, it is skipped with an explanation. Candidate risk scores remain distinct from spatial distances.

### Standalone OSM preview and data processing

- `tools/osm_pune_preview/` provides a separate local map preview, not part of the FastAPI application. It loads the processed GeoJSON locally, supports name search and ATM details, and displays OSM attribution and a coverage disclaimer. Leaflet and map tiles use online services, so internet access is required for the map background and map library.
- Start the preview from the project root with:

  ```powershell
  .\.venv-pcirs-test\Scripts\python.exe tools\osm_pune_preview\server.py
  ```

  Open <http://127.0.0.1:8765/> and stop with **Ctrl+C**.
- `scripts/process_osm_pune_atms.py` verifies and parses the raw gzip OSM XML into processed data and a validation report. The recorded source is a BBBike custom extract, retrieved 12 October 2026, bounds WGS 84: west 73.70, south 18.40, east 74.00, north 18.65. This urban-area envelope includes adjacent suburbs and is not the full Pune administrative area.
- The raw OSM extract is `data/external/osm_pune/raw/planet_73.70,18.40_74.00,18.65.osm.gz`; acquisition source/checksum metadata is in `data/external/osm_pune/metadata/acquisition.json`. Processed GeoJSON/CSV, validation report, source details, and attribution are in `data/external/osm_pune/processed/` and `data/external/osm_pune/README.md`.
- Last recorded data validation documents 2,215,288 raw OSM elements, 312 ATM-tagged nodes, and 312 valid processed ATM points. No duplicate IDs/coordinates, invalid geometry, unresolved ATM ways/relations, or out-of-bounds processed features were reported. Optional tags are often absent: 164 names, 30 operators, 66 brands, and all access tags were missing. Re-check the validation report if these data are processed again.

### Six-phase research/batch pipeline

- **Phase 1 — Dataset/problem definition:** PaySim inspection and limitations are documented; PaySim is simulated, not verified Pune cybercrime data.
- **Phase 2 — Schema/contracts:** proposed data schema and boundaries are documented in `docs/data_schema_and_synthetic_data_design.md`; these do not represent a deployed production database.
- **Phase 3 — Synthetic multi-hop data:** `scripts/generate_synthetic_scenarios.py` produces fictional scenario artifacts. Do not run with default outputs without first inspecting the target paths.
- **Phase 4 — Validation:** `scripts/validate_synthetic_data.py` validates synthetic datasets and checks chronology, foreign keys, cutoff, and leakage concerns.
- **Phase 5 — Transaction graph:** `scripts/build_transaction_graph.py` builds cutoff-aware directed NetworkX `MultiDiGraph` structures, preserving direction, parallel transfers, amount, and timestamps.
- **Phase 6 — Features/GIS:** `scripts/feature_contract.py` maintains an explicit numeric feature allowlist and rejects identifiers/outcome-derived fields at the model-matrix boundary. OSM processing/preview and explicit app candidate-source loading provide geographic data handling.

The batch graph/features are research utilities, not a trained model and not yet a live FastAPI prediction pipeline. Graph builder defaults may write to processed/report locations; use explicit temporary paths for exploratory runs. Feature CSV numeric parsing and a missing-value policy are not silently performed by the feature contract. `group_graph_features.csv` is split-planning metadata; `scenario_count` is not a prediction feature. Build any future model matrix from an explicit allowlist and exclude IDs, grouping metadata, seeds, target locations/times, and all ground-truth fields.

## 4. Tests and what has been verified

Historical full-suite results:

```text
.venv-pcirs-test\Scripts\python.exe -m pytest -v
Earlier run: 74 passed, 41 subtests passed
Later user-reported run: 80 passed, 1 failed, 41 subtests passed
```

The later failure was the Windows staging-directory rename in the v4 generator. A WinError 5 retry and regression test were added. The latest candidate-signal modifications and focused tests remain unverified because the execution environment could not start the existing venv. Run the following in the actual Windows PowerShell terminal:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -m pytest tests\test_synthetic_v4.py tests\test_synthetic_validation.py tests\test_transaction_graph.py -v
```

Do not claim the Windows retry or candidate-signal tests pass until this command completes successfully. The failed launcher attempt did not run pytest. Browser interaction (clicking markers, changing sources, console/network inspection) also remains to be done manually.

Test files cover application/API behavior, candidate loading, map-data mapping, spatial filtering, feature contracts, synthetic validation, NetworkX graph construction, OSM extraction/processing, and standalone preview loading/search. Recent runtime smoke checks verified HTTP response and candidate counts for both API source modes. Browser interaction (clicking markers, changing sources, console/network inspection) remains to be done manually.

## 5. Data, evaluation, and safety limits

- The complaint trail and cash-out ground truth are synthetic. Synthetic labels are kept separate from prediction inputs.
- PaySim is simulated transaction data, not verified crime reports or Pune cash-out history.
- The synthetic candidate fixture contains invented points; OSM records represent mapped ATM features only. Neither proves that an ATM received a particular withdrawal.
- There is no trained or validated ML model, calibrated probability, validated lead-time window, or evaluation on verified real cash-out outcomes.
- The current app has no authentication/role control, production database, bank/law-enforcement integration, account freeze, or external response action. Case status is process-local.
- Keep `data/raw/paysim.csv`, the raw OSM extract, processed OSM data, current demo artifacts, and candidate fixtures unchanged unless a specific approved task requires a change.
- Do not use v2/v3 synthetic artifacts for blind evaluation. V2 has seed replay leakage concerns; v3 remains uncleared. Never run generators using defaults without checking where they write.
- Preserve the distinction between risk heuristics and spatial distance. Do not make a cash-out claim from candidate proximity.

## 6. Recommended next work

1. **Run the focused v4 and regression tests** using the PowerShell command in Section 4; record the actual summary and fix any failures before generating data.
2. **Continue v4 integration planning.** Define the adapter from v4 schemas into the existing transaction graph, candidate-level feature matrix, and component-aware chronological split. Keep graph IDs and candidate IDs as metadata, not model features.
3. **Freeze evaluation safeguards before data generation or training.** Specify test-label isolation, the 72-hour outcome-horizon purge/embargo, leakage diagnostics, and the candidate-ranking evaluation protocol. V4 performance measures only recovery of the synthetic distance rule.
4. **Manually verify browser behavior.** Start the FastAPI app, test both source modes, radius/bounding-box states, empty results and candidate-map refresh, and inspect browser console/network errors. Separately open the standalone OSM preview and test search, marker details, and attribution.
5. **Keep real-world and production work separate.** Point-in-time OSM provenance and a verified origin source are needed for real geographic claims. Security/authentication, authorization, persistence, audit, monitoring, and external response integrations need separate design; they are not current capabilities.

## 7. Useful project files

- [README](../README.md) — app run commands and core limitations.
- [Demo and progress guide](demo_and_progress_review_guide.md) — dashboard walkthrough and feature status.
- [Schema and synthetic-data design](data_schema_and_synthetic_data_design.md) — schemas, graph features, and model-matrix boundaries.
- [OSM dataset documentation](../data/external/osm_pune/README.md) — source, extraction, processing, and attribution.
- [OSM preview guide](../tools/osm_pune_preview/README.md) — standalone map run instructions.
- `backend/main.py` — FastAPI routes.
- `src/prediction/analysis_pipeline.py` — complaint analysis orchestration.
- `src/prediction/candidate_locations.py` and `src/prediction/spatial_filter.py` — candidate sources and geographic filtering.
- `scripts/build_transaction_graph.py` and `scripts/feature_contract.py` — batch graph and feature contracts.
