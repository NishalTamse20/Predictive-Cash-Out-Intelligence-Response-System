# PCIRS Demo and Progress Review Guide

## Run the local demo

From the PCIRS project root in PowerShell, run:
.venv-pcirs-test\Scripts\python.exe -m uvicorn backend.main:app --reload

Open http://127.0.0.1:8000, load the sample complaint, and analyze it. Review the risk indicator, linked transaction trail and graph, candidate source and locations, illustrative IST time window, and local case status. The candidate-source selector defaults to the synthetic fixture; choose the OpenStreetMap option to display validated mapped ATM features.

## End-to-end flow

1. Data Inputs: complaint details come from the form or sample endpoint. The scenario generator creates a deterministic synthetic chain. PaySim supplies simulated transaction patterns where configured. Candidate points use the explicitly selected source: the synthetic fixture by default, or the validated Pune OSM ATM GeoJSON.
2. Secure Ingestion: complaint validation, timestamp normalization, and chain-integrity checks run before ranking. Authentication, encryption, tokenization, RBAC, and tamper-evident audit storage are not implemented.
3. Feature and Graph Processing: directed traversal returns linked accounts, transfers, hop count, and basic timing and amount checks. The graph is drawn from those returned transactions. Historical account behavior is unavailable.
4. Prediction Engine: the current complaint flow has no verified, explicitly sourced incident-origin coordinate. Candidates from either source are returned without geographic ranking. A future sourced origin may enable distance scoring; the synthetic demo coordinate is never used for geographic ranking. No trained ML model, hybrid model, or SHAP explanation is implemented.
5. Risk Intelligence: the API returns a case-level heuristic indicator, candidate scores and reasons, and a fixed illustrative time window. Scores are not probabilities and have not been validated against observed cash-out events.
6. Response and Dashboard: the dashboard displays results and process-local case status. It sends no external alert, contacts no bank or law-enforcement agency, and does not freeze accounts.

## Overview metric interpretation

Overview values are limited to the bundled synthetic sample and this running process. Do not describe them as real transaction totals or observed fraud counts. The high-risk display uses a heuristic threshold of 70/100. Case status counts reset when the API process restarts.

## Provenance and limitations

PaySim is simulated data, not verified real-world cybercrime or Pune cash-out activity. Complaint chains and cash-out ground truth are synthetic. Synthetic target labels are separate from prediction inputs. The candidate JSON fixture contains fabricated locations, not verified ATMs. The OSM source contains mapped ATM locations only; it does not provide historical withdrawal activity or connect a transfer to an ATM. No real incident/account origin is available in the current complaint input. The time window is a fixed heuristic illustration.

## Verification

Run the tests with:
.venv-pcirs-test\Scripts\python.exe -m pytest -v

API routes include /health, /api/sample-case, /api/analyze, /api/cases/{case_id}/status, /api/overview, and /api/demo-prediction. POST `/api/analyze` accepts optional `candidate_source=synthetic_fixture|osm`; the default preserves the original synthetic demo behavior.

## Optional spatial candidate filtering

The complaint form has an expandable geographic-filter section. A radius requires latitude, longitude, a nonblank origin source, and the verified-origin checkbox. The app validates the WGS 84 ranges and the existing provenance marker; it does not independently verify the user's source. The built-in synthetic demo coordinate is rejected for this purpose. If radius is requested without valid verified-origin metadata, the radius filter is skipped and the response explains why.

An explicit `bbox=west,south,east,north` query filter does not need an origin; its WGS 84 bounds are inclusive. Radius and box filters combine by intersection. Distances use the haversine great-circle formula with a mean Earth radius of 6,371.0088 km, and the radius boundary is included. Filtering returns map candidates and optional `spatial_distance_km` values; for any requested filter, candidate risk scores and ranks remain unset. No spatial filter is applied when its query parameters are omitted.
