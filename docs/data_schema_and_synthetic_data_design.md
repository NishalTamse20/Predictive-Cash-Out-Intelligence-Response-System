# PCIRS Data Schema and Synthetic Data Design

**Status:** Proposed relational design; only the current local demo subset is implemented (see Section 11).  
**Scope:** Relational schema and synthetic scenario specification for the PCIRS research prototype

## 1. Purpose and evidence boundaries

PCIRS is intended to study whether transaction-network and behavioral signals can rank **candidate cash-out locations and likely time windows** after a reported incident. The schema separates source records, synthetic scenario labels, geographic reference data, and model outputs so their provenance is visible.

The inspected PaySim file contains 6,362,620 rows and 11 columns, with no missing or duplicate rows in the inspection run. PaySim is simulated mobile-money transaction data. Its `isFraud` and `isFlaggedFraud` fields are labels/flags in that simulation; they are not verified Indian cybercrime reports, mule-account findings, or observed cash-out outcomes. OpenStreetMap (OSM) provides mapped geographic features, such as ATM points and tags. It does not provide historical transactions or observed cash-out activity. Any PCIRS cash-out event created by the proposed generator is **synthetic ground truth** and must be labelled as such in storage, plots, demos, and reports.

No observed cash-out history is assumed or invented by this design. In particular, an OSM ATM point is only a candidate geographic location, not evidence that a withdrawal happened there.

## 2. Proposed entities and relationships

Use relational tables with UUID primary keys for PCIRS records, UTC timestamps, `NUMERIC(18,2)` for currency amounts, and explicit provenance columns. Source-native identifiers are retained only as source-scoped references; they are not global PCIRS IDs. Enumerated values below are proposed controlled values, not claims about all future data sources.

### 2.1 `accounts`

**Purpose/grain:** One row per source-scoped account identity in the modeled transaction data. A PaySim name such as `C123...` is a simulator token, not a verified bank account.

| Field | Type | Key / meaning |
|---|---|---|
| `account_id` | UUID | **PK**, PCIRS surrogate key |
| `source_name` | VARCHAR(40) | Provenance, e.g. `paysim` or `pcirs_synthetic` |
| `source_account_id` | VARCHAR(128) | Original/source token; unique with `source_name` |
| `account_role` | VARCHAR(24), nullable | `unknown`, `victim`, `mule`, or `other`; synthetic roles must be marked synthetic |
| `first_seen_at` | TIMESTAMP WITH TIME ZONE, nullable | Earliest known event in this dataset; not necessarily account creation |
| `is_synthetic` | BOOLEAN | True for generator-created identity/role records |
| `created_at` | TIMESTAMP WITH TIME ZONE | PCIRS ingestion time |

**Constraints:** Unique (`source_name`, `source_account_id`). Do not infer a real person's identity or real mule status from PaySim tokens or a synthetic role.

### 2.2 `transactions`

**Purpose/grain:** One transfer record. Raw PaySim rows should remain immutable; this normalized table is a mapped/derived analytical representation with source lineage.

| Field | Type | Key / meaning |
|---|---|---|
| `transaction_id` | UUID | **PK**, PCIRS transaction key |
| `source_name` | VARCHAR(40) | Dataset/source identifier |
| `source_transaction_id` | VARCHAR(128), nullable | Source row identifier if available; unique within source when populated |
| `sender_account_id` | UUID | **FK** → `accounts.account_id` |
| `receiver_account_id` | UUID | **FK** → `accounts.account_id` |
| `case_id` | UUID, nullable | **FK** → `cases.case_id`; nullable for source/background transactions not linked to a scenario |
| `event_time` | TIMESTAMP WITH TIME ZONE | Event time, or explicitly documented derived timestamp |
| `source_step` | BIGINT, nullable | Original PaySim relative step; PaySim step is not a real calendar timestamp |
| `transaction_type` | VARCHAR(24) | Source transaction type, e.g. `PAYMENT`, `TRANSFER`, `CASH_OUT` |
| `amount` | NUMERIC(18,2) | Transfer amount; nonnegative |
| `sender_balance_before` | NUMERIC(18,2), nullable | Source balance before event |
| `sender_balance_after` | NUMERIC(18,2), nullable | Source balance after event |
| `receiver_balance_before` | NUMERIC(18,2), nullable | Source balance before event |
| `receiver_balance_after` | NUMERIC(18,2), nullable | Source balance after event |
| `source_is_fraud` | BOOLEAN, nullable | Source-provided label only; PaySim simulated label, not verified truth |
| `source_is_flagged_fraud` | BOOLEAN, nullable | Source-provided flag only; not an independent PCIRS outcome |
| `scenario_id` | UUID, nullable | **FK** → `cases.scenario_id` or scenario registry if implemented; groups generator provenance |
| `data_origin` | VARCHAR(24) | `paysim`, `synthetic`, or another declared source |
| `is_synthetic` | BOOLEAN | True for generator-created transaction rows |

**Constraints:** Sender and receiver must exist; `amount >= 0`; balances, when present, must be nonnegative. Source labels must retain their source-specific names and meanings.

### 2.3 `complaints`

**Purpose/grain:** A complaint/report linked to an incident case. PaySim has no complaint or report time; complaint rows for generated scenarios are synthetic.

| Field | Type | Key / meaning |
|---|---|---|
| `complaint_id` | UUID | **PK** |
| `case_id` | UUID | **FK** → `cases.case_id` |
| `reported_at` | TIMESTAMP WITH TIME ZONE | Time the report becomes available to the system |
| `incident_time_estimate` | TIMESTAMP WITH TIME ZONE, nullable | Reported/assumed incident time; may be uncertain |
| `reporting_delay_minutes` | INTEGER, nullable | Generated delay or explicitly supplied value, derived as report time minus incident time |
| `reported_loss_amount` | NUMERIC(18,2), nullable | Amount stated by complainant/simulator |
| `victim_account_id` | UUID, nullable | **FK** → `accounts.account_id` |
| `linked_transaction_id` | UUID, nullable | **FK** → `transactions.transaction_id`, when known at report time |
| `data_origin` | VARCHAR(24) | `synthetic` or documented real source if later approved |
| `is_synthetic` | BOOLEAN | True for generated reports |

**Constraints:** Delay cannot be negative when both times are known. Model features at a prediction cutoff may use only complaint fields that were available by that cutoff.

### 2.4 `atm_locations`

**Purpose/grain:** A versioned reference point or area derived from OSM, used to define candidate geographic locations. OSM data is geography only, not cash-out evidence.

| Field | Type | Key / meaning |
|---|---|---|
| `atm_location_id` | UUID | **PK**, PCIRS surrogate key |
| `osm_element_type` | VARCHAR(12) | OSM element kind, e.g. `node`, `way`, `relation` |
| `osm_element_id` | VARCHAR(32) | OSM identifier; unique with element type and snapshot/version |
| `osm_version` | INTEGER, nullable | OSM object version if provided |
| `name` | VARCHAR(256), nullable | OSM name tag, if present |
| `latitude` | NUMERIC(9,6) | WGS84 latitude, range -90..90 |
| `longitude` | NUMERIC(9,6) | WGS84 longitude, range -180..180 |
| `tags_json` | JSON, nullable | Relevant OSM tags with source values preserved |
| `candidate_area_id` | VARCHAR(80), nullable | Optional derived spatial cell/cluster ID; not an OSM cash-out label |
| `osm_snapshot_at` | TIMESTAMP WITH TIME ZONE | Retrieval/snapshot time |
| `data_origin` | VARCHAR(24) | `openstreetmap` |

**Constraints:** Unique (`osm_element_type`, `osm_element_id`, `osm_snapshot_at`) or an equivalent snapshot key. Coordinates must be valid. Preserve OSM attribution and applicable licensing/source metadata with the dataset. If an OSM feature is a bank/branch rather than an ATM, model it with a clear feature type rather than silently calling it an ATM.

### 2.5 `cashout_events`

**Purpose/grain:** One cash-out outcome associated with a case. In the initial prototype this table is for **simulator-generated labels only**; it must not be populated with OSM points as if they represented observed withdrawals.

| Field | Type | Key / meaning |
|---|---|---|
| `cashout_event_id` | UUID | **PK** |
| `case_id` | UUID | **FK** → `cases.case_id` |
| `account_id` | UUID | **FK** → `accounts.account_id`, account at the simulated final hop |
| `transaction_id` | UUID, nullable | **FK** → `transactions.transaction_id`, if an explicit cash-out transaction is modeled |
| `atm_location_id` | UUID, nullable | **FK** → `atm_locations.atm_location_id`; selected simulated target if candidate locations are being used |
| `cashout_time` | TIMESTAMP WITH TIME ZONE | Simulated event time; never assert as an observed time |
| `amount` | NUMERIC(18,2) | Simulated cashed-out amount, nonnegative |
| `ground_truth_origin` | VARCHAR(24) | Must be `synthetic_simulation` for generated labels |
| `is_synthetic_ground_truth` | BOOLEAN | Must be true for this initial dataset |
| `scenario_id` | UUID | **FK** → scenario registry / `cases.scenario_id` |

**Constraint:** A non-synthetic observed event cannot be inserted without a documented, authorized source and separate provenance review. An OSM-linked simulated location means only “the simulator selected this candidate point”; it does not imply historical cash-out activity there.

### 2.6 `cases`

**Purpose/grain:** One reported incident/scenario and its prediction anchor. This is the unit for case-level modeling, evaluation splits, and grouping generated multi-hop chains.

| Field | Type | Key / meaning |
|---|---|---|
| `case_id` | UUID | **PK** |
| `scenario_id` | UUID | **FK/unique** scenario provenance identifier |
| `case_origin` | VARCHAR(24) | `paysim_adapted` or `synthetic` |
| `incident_time` | TIMESTAMP WITH TIME ZONE | Simulated/known incident time; explicitly marked as derived where appropriate |
| `prediction_cutoff_at` | TIMESTAMP WITH TIME ZONE | Latest time allowed for inference features; typically report/alert time |
| `reporting_delay_minutes` | INTEGER, nullable | Synthetic incident-to-report delay |
| `reported_loss_amount` | NUMERIC(18,2), nullable | Amount known at cutoff |
| `source_case_label` | VARCHAR(40), nullable | Source-specific label such as PaySim fraud flag; preserve semantics |
| `target_cashout_event_id` | UUID, nullable | **FK** → `cashout_events.cashout_event_id`; evaluation label only |
| `split_group_id` | UUID | Group key so related/reused-account scenarios stay in one split where required |
| `is_synthetic` | BOOLEAN | Provenance flag |
| `created_at` | TIMESTAMP WITH TIME ZONE | Record creation time |

**Constraints:** `incident_time <= prediction_cutoff_at <= cashout_time` for a scenario with known target. Do not expose target foreign key, cash-out outcome, or later events to inference features.

### 2.7 `audit_log`

**Purpose/grain:** Append-only record of access or state-changing actions in the prototype. It is operational/security metadata, not a model-training source.

| Field | Type | Key / meaning |
|---|---|---|
| `audit_id` | UUID | **PK** |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | UTC action time |
| `actor_id` | VARCHAR(128), nullable | Authenticated principal identifier or pseudonym; no credentials/secrets |
| `action` | VARCHAR(80) | Controlled action name |
| `object_type` | VARCHAR(48) | Entity affected/read |
| `object_id` | UUID, nullable | Relevant entity identifier |
| `case_id` | UUID, nullable | **FK** → `cases.case_id`, if applicable |
| `request_id` | UUID, nullable | Correlation key |
| `outcome` | VARCHAR(16) | `success`, `denied`, or `error` |
| `details_json` | JSON, nullable | Minimal non-sensitive metadata; redact personal/financial values |
| `previous_entry_hash` | CHAR(64), nullable | Prior-record hash for tamper-evidence |
| `entry_hash` | CHAR(64) | Hash of canonical audit record plus previous hash |

**Constraints:** Append only; application roles should not update/delete records. Hash chaining supports tamper detection but is not a substitute for access control or protected storage. Exclude audit fields from all predictive features.

### 2.8 Prediction outputs

Represent a single model invocation and its ranked candidates separately to preserve a one-to-many relationship.

#### `prediction_runs`

| Field | Type | Key / meaning |
|---|---|---|
| `prediction_id` | UUID | **PK** |
| `case_id` | UUID | **FK** → `cases.case_id` |
| `prediction_cutoff_at` | TIMESTAMP WITH TIME ZONE | Feature cutoff actually used |
| `generated_at` | TIMESTAMP WITH TIME ZONE | Inference time |
| `model_name` | VARCHAR(80) | Rule/ML/hybrid model identifier |
| `model_version` | VARCHAR(40) | Immutable version or commit/hash |
| `feature_schema_version` | VARCHAR(40) | Feature contract version |
| `predicted_window_start` | TIMESTAMP WITH TIME ZONE | Overall predicted interval start |
| `predicted_window_end` | TIMESTAMP WITH TIME ZONE | Overall predicted interval end |
| `risk_score` | NUMERIC(7,6), nullable | Calibrated/defined score in [0,1], if supported |
| `explanation_json` | JSON, nullable | Human-readable feature contribution/reason codes, not private raw data |
| `is_synthetic_input` | BOOLEAN | Whether the input scenario is generated |

#### `prediction_candidates`

| Field | Type | Key / meaning |
|---|---|---|
| `prediction_candidate_id` | UUID | **PK** |
| `prediction_id` | UUID | **FK** → `prediction_runs.prediction_id` |
| `rank` | INTEGER | One-based rank, unique within prediction |
| `candidate_type` | VARCHAR(20) | `atm_location` or `candidate_area` |
| `atm_location_id` | UUID, nullable | **FK** → `atm_locations.atm_location_id` when point candidate |
| `candidate_area_id` | VARCHAR(80), nullable | Derived area/cell ID when area candidate |
| `score` | NUMERIC(12,8) | Ranking score; probability only if model is calibrated and documented |
| `time_window_start` | TIMESTAMP WITH TIME ZONE | Candidate-specific likely interval start |
| `time_window_end` | TIMESTAMP WITH TIME ZONE | Candidate-specific likely interval end |

**Constraints:** Exactly one of ATM point or area identifier must be supplied according to candidate type; rank is positive and unique per run; window start cannot exceed end. Clearly distinguish uncalibrated ranking scores from probabilities. Candidate ranks are hypotheses for prioritization, not an exact-location assertion.

## 3. Relationships at a glance

- `accounts` 1—many `transactions` as sender and receiver.
- `cases` 1—many `transactions`, `complaints`, `cashout_events`, `prediction_runs`, and optional `audit_log` rows.
- `cashout_events` provides an evaluation target to a `cases` row; it is kept separate from model inputs.
- `atm_locations` 1—many `cashout_events` (simulated target assignment only) and `prediction_candidates`.
- `prediction_runs` 1—many `prediction_candidates`.
- Scenario provenance groups case, transactions, complaint delay, and cash-out labels generated from one simulation.

## 4. PaySim-to-PCIRS transaction mapping

The original file remains read-only. Load it into a versioned raw/staging layer before creating normalized rows. Keep source file checksum, retrieval date, source documentation, and source row number for lineage. PaySim has no native unique transaction ID; assign a deterministic source-row key such as `(dataset_version, row_number)` rather than pretending one exists.

| PaySim field | PCIRS destination | Mapping / caveat |
|---|---|---|
| `step` | `transactions.source_step`; optionally `event_time` | Preserve the integer unchanged. It is a relative simulation step (PaySim convention: hours), not a real timestamp. Any derived timestamp needs a declared synthetic epoch and must not be represented as observed time. |
| `type` | `transactions.transaction_type` | Preserve categories `CASH_OUT`, `PAYMENT`, `CASH_IN`, `TRANSFER`, `DEBIT`; do not assume every row is a mule-chain transfer. |
| `amount` | `transactions.amount` | Convert the decimal representation to currency precision (`NUMERIC(18,2)`); retain the original source value in immutable raw data. |
| `nameOrig` | `accounts.source_account_id` for sender; `sender_account_id` FK | Namespace by dataset/version. This token is not a real-world bank identity. |
| `oldbalanceOrg` | `sender_balance_before` | Preserve as source balance. |
| `newbalanceOrig` | `sender_balance_after` | Preserve as source balance. |
| `nameDest` | `accounts.source_account_id` for receiver; `receiver_account_id` FK | Namespace by dataset/version; destination prefixes/types may have simulator-specific semantics. |
| `oldbalanceDest` | `receiver_balance_before` | Preserve as source balance. |
| `newbalanceDest` | `receiver_balance_after` | Preserve as source balance. |
| `isFraud` | `source_is_fraud` | Retain as PaySim's simulated source label; not verified cybercrime truth. Use only for a clearly described PaySim task, not as cash-out-location ground truth. |
| `isFlaggedFraud` | `source_is_flagged_fraud` | Retain as the dataset's flag; it is not equivalent to a PCIRS prediction or independent label. |

PaySim does not supply complaint records, OSM coordinates, historical ATMs, mule adjudications, or cash-out event locations/times. Those must remain absent unless explicitly generated with synthetic provenance or acquired from a separately documented authorized source.

## 5. Synthetic scenario generation specification

This section is a design only; it does not generate records. The generator should produce a reproducible, seeded scenario and all related rows with `is_synthetic=true`, a `scenario_id`, generator version, parameters, and a documented random seed.

1. **Choose source/background context.** Optionally sample PaySim rows for transaction-type/amount/step distributions, documenting filters and preserving source lineage. PaySim's fraud labels do not decide geographic cash-out truth.
2. **Create a case and participants.** Create a victim/source account and 2–4 downstream transfer hops. Select some mule identities from a reusable synthetic pool so an account can appear in multiple scenarios. Assign `account_role='mule'` only as a simulator label.
3. **Create a directed chain.** For hop count `h ∈ {2,3,4}`, create ordered transfers `A0→A1→...→Ah`, with event times strictly increasing, nonnegative amounts, and optional documented fees/retained amounts. Verify each hop's receiver equals the next hop's sender. PaySim `nameOrig`/`nameDest` can inform source-style account tokens but do not falsely claim these are real linked mule accounts.
4. **Reuse accounts explicitly.** Reuse only generated pool IDs, record their reuse across distinct cases, and retain a generated-role/provenance flag. Do not create multiple copies of a reused mule that obscure graph connectivity. Ensure split design prevents the same identity or scenario family from leaking between train and held-out sets when evaluating generalization to unseen networks.
5. **Add complaint delay.** Sample a reporting delay from a documented parameter distribution, then set `reported_at = incident_time + delay`. Store both times and delay. Mark both the generated complaint and case as synthetic. Do not draw the delay from PaySim, which has no complaints.
6. **Assign cash-out ground truth.** Choose a synthetic target time after the last known transfer and choose an eligible candidate ATM point/area using a documented simulator rule (for example, a seeded choice among candidate areas). Store exactly one generated ground-truth event with `ground_truth_origin='synthetic_simulation'`. If it references an OSM feature, that only means the simulator selected that geographic candidate; it is not observed activity.
7. **Version the process.** Persist generator version, seed, scenario parameters, candidate-location snapshot/version, and lineage. Be able to regenerate the same scenario from the same inputs and seed.

Avoid rules that make the target trivially recoverable from a single feature unless the experiment is explicitly designed to test that rule. Document the label-generation mechanism so performance is interpreted as performance on the simulator, not real-world predictive validity.

## 6. Provenance inventory

| Origin | Fields/data | Limits |
|---|---|---|
| **PaySim** | Source transaction type, amount, balances, relative step, simulator account tokens, `isFraud`, `isFlaggedFraud` | Simulated financial transactions and labels; no verified Indian cybercrime case, geolocation, complaint delay, mule determination, or cash-out location history. |
| **OpenStreetMap** | OSM element ID/type, coordinates, tags/name when present, snapshot/version metadata; candidate points/areas derived from these | Geographic reference only. No historical ATM usage, withdrawal events, cash-out frequency, or fraud labels. |
| **PCIRS synthetic generator** | Generated account roles/reuse, linked 2–4 hop chains, scenario/case IDs, complaint/report delays, cash-out event time/location/amount, synthetic target labels | Artificial ground truth controlled by generator assumptions; label every row and output as synthetic. |
| **PCIRS derived** | Normalized IDs, relative/derived time, graph metrics, spatial clusters, feature values, ranks and windows | Record transformation/version and source lineage; never rebrand derived or generated values as source-observed facts. |

Each persisted data table should expose or inherit `data_origin`, `is_synthetic`, source/version lineage, and (where relevant) generator or OSM snapshot version. Keep immutable source data separate from normalized and generated outputs.

## 7. Candidate locations and time-window representation

Candidate points are rows in `atm_locations`; candidate areas are stable spatial cell/cluster IDs with separately versioned geometry derived from OSM point locations. The candidate universe for a prediction must be recorded or reproducible from the OSM snapshot and spatial grouping configuration. A location's presence on the map does not imply cash-out history.

For each prediction, store a ranked list in `prediction_candidates`, with either a point FK or area ID, an explicitly named score, and a candidate-specific start/end time. Store overall time bounds at run level where useful. Define interval semantics (for example, estimated likely interval after `prediction_cutoff_at`) and timezone as UTC. Do not label a score as a percentage/probability unless calibration and normalization have been measured and documented. The system's claim is ranked probable locations/time windows, not deterministic identification of an ATM.

## 8. Validation rules

- Check required source columns and unexpected columns at ingestion; preserve raw source data unchanged and report schema drift.
- Enforce unique primary keys and declared foreign keys; ensure every transaction sender/receiver exists.
- Validate nonnegative monetary values and balances, finite numeric values, timestamp parseability, valid UTC ordering, and allowed transaction categories.
- For generated chains: require 2–4 hops, continuous sender/receiver linkage, strictly increasing event times, and case/scenario consistency.
- For complaints: delay must be nonnegative and agree with stored incident/report timestamps within the documented precision.
- For synthetic cash-outs: require `is_synthetic_ground_truth=true`, valid case/account references, target time after the last preceding transfer and cutoff, and provenance metadata. Never infer a real event from an OSM point.
- For OSM points: validate coordinate ranges, OSM identity/version uniqueness, retrieval metadata, feature type/tags, and attribution requirements.
- For predictions: require cutoff and model/feature versions; ranks must be positive and unique within a run; candidate windows must be ordered; enforce candidate-type/FK consistency.
- Validate no nulls in required fields, expected row counts/joins, duplicate IDs, and data-origin/synthetic-flag consistency. Report failures; do not silently repair raw records.

## 9. Target leakage controls and feature exclusions

Define a prediction row as features available **as of `prediction_cutoff_at`**. The training target may be the future cash-out area/cluster and/or time-to-cash-out bucket, but target fields are never predictors.

Exclude from inference/training features:

- `cashout_events` fields, including `cashout_time`, `atm_location_id`, cash-out amount, ground-truth area, and synthetic target flags;
- `cases.target_cashout_event_id`, target area/time, future outcomes, and any label derived from them;
- transaction records with `event_time > prediction_cutoff_at` and features aggregated using those future transactions;
- complaint fields or later corrections that became available after the prediction cutoff;
- prediction outputs, rank/score fields, audit-log fields, train/validation/test membership, and post-outcome investigation results;
- direct identifiers (`account_id`, source account tokens, transaction IDs, case IDs, complaint IDs, OSM IDs) as model inputs. Use IDs only for joins/grouping; prefer permitted aggregate graph features;
- raw `isFraud` and `isFlaggedFraud` as PCIRS cash-out-location features. If modeling PaySim's own fraud label as a separate baseline, make that a distinct task and never interpret it as geographic cash-out prediction;
- synthetic generator seed, scenario ID, target-selection rule variables, or any simulator-only value that directly encodes the target.

Fit imputers, scalers, encoders, feature selection, calibration, and thresholds using training data only. Keep an explicit feature allowlist and cutoff-aware feature-generation API. Audit feature timestamps and label joins before each evaluation.

## 10. Time-based evaluation splits

Split on the case's `prediction_cutoff_at` (or a documented comparable scenario-time axis) in chronological order: earlier cases for training, a later interval for validation, and the latest interval for final testing. Freeze boundaries before model comparison. Keep all rows and events for a case in one partition. Use `split_group_id` to keep linked/reused-account networks or related generator families together when the evaluation claims generalization to unseen entities; alternatively report a separately labeled known-entity temporal evaluation.

Build features using only data available by each case's cutoff. For labels, use only future outcome rows after the cutoff. Apply a purge/embargo where scenario generation or overlapping outcome windows could allow information to cross boundaries. Do not randomly split transaction rows: that would place linked hops from one case/network on both sides and can leak identity and future behavior. Report the split dates, case counts, entity overlap policy, synthetic share, and the fact that synthetic-target metrics measure simulator performance only.

## 11. Current project context (implementation snapshot, 2026-10-10)

**Geographic-candidate integration update (2026-10-15):** The validated
`data/external/osm_pune/processed/pune_atms.geojson` can now be selected in the
dashboard or with `POST /api/analyze?candidate_source=osm`. The default remains
`synthetic_fixture` to preserve the existing demo. OSM source errors fail
explicitly; the loader does not silently substitute fabricated candidates.
The current complaint schema still has no verified, explicitly sourced
incident-origin coordinate. Its demo coordinate is synthetic and is never used
for geographic ranking. Location candidates remain unranked until a verified,
explicitly sourced origin is available. OSM points do not represent withdrawals
or cash-out ground truth.

This section describes the code currently in the workspace. It supersedes the original draft note that said no pipeline or generator existed. The relational schema and data-generation specification in Sections 2–10 remain a **proposal**; the running demo uses Python dictionaries and JSON files, not those database tables.

### Implemented in the local demo

- `src/data_inspection/inspect_paysim.py` reads PaySim and prints schema and descriptive summaries without changing the CSV.
- `src/preprocessing/complaint_validation.py` validates and normalizes the four complaint inputs, derives a stable case ID, and calculates incident time from report time and delay.
- `src/data_generation/demo_scenario.py` creates a reproducible synthetic three-hop chain and synthetic complaint/case records. It samples PaySim `TRANSFER` type/step references from the first 100,000 rows; user-reported amount is copied unchanged to each synthetic hop. Cash-out time and target candidate are generated separately as synthetic ground truth.
- `src/prediction/transaction_graph.py` traverses a directed transaction chain and checks hop count, continuity, and increasing timestamps.
- `src/prediction/candidate_locations.py` explicitly loads either the processed OSM ATM GeoJSON or the fabricated candidate fixture in `data/external/synthetic_atm_candidates_pune.json`. The fixture remains the default; each source is marked in the result.
- `src/prediction/ranker.py` calculates transparent heuristic scores using chain timing/hop count and candidate distance only when a verified incident-origin coordinate has explicit source metadata. Synthetic demo coordinates and unprovenanced coordinates do not enable geographic ranking. It returns a fixed illustrative 30–60 minute window after the report cutoff. Scores are not probabilities; ground truth is not used as a ranking input.
- `src/prediction/analysis_pipeline.py` combines validation, generation, candidate loading, and ranking.
- `backend/main.py` exposes the FastAPI routes and serves `dashboard/index.html`. `backend/case_state.py` stores statuses in process memory only.
- `dashboard/index.html` provides the complaint form, workflow and overview panels, transaction graph, source selector for synthetic or OSM candidates, candidate details, synthetic-label disclosure, and local status controls.
- `tests/test_demo.py` contains smoke tests for validation, analysis, ranking, routes, and dashboard integration.

### Not implemented from the proposal

There is no relational database schema/migration or persistence layer, trained ML/hybrid model, calibrated probability, validated cash-out prediction, real complaint or cash-out history, authentication/RBAC, encryption/tokenization, tamper-evident audit log, or external response integration. PaySim remains simulated data; its labels do not represent verified cybercrime or geographic cash-out truth. Candidate fixture points are fabricated. Selected OSM candidates are mapped geography only, and no trustworthy incident/account origin is available to support a real geographic ranking.

For current run instructions, routes, provenance boundaries, and demo limitations, see `README.md` and `docs/demo_and_progress_review_guide.md`.

## 12. Batch graph feature tables and model-matrix boundary

The Phase 5/6 batch graph builder reads only `accounts.csv`, `transactions.csv`,
and `prediction_scenarios.csv`. Each input has an explicit required/optional
header allowlist in `scripts/build_transaction_graph.py`; any other header is
rejected with the input filename and offending columns. In particular, source
fraud labels, cash-out outcomes, target locations/times, seed/configuration
fields, and unreviewed columns must not be added to these graph inputs. The
builder enforces event and availability timestamps at or before each scenario's
`prediction_cutoff_at`, and uses only transaction IDs listed as observed.

The builder writes five tables:

| File | Columns and role |
|---|---|
| `graph_nodes.csv` | `account_id`, `scenario_group_id`, `scenario_ids`; graph/join metadata only. |
| `graph_edges.csv` | `transaction_id`, `scenario_id`, `scenario_group_id`, `source_account_id`, `destination_account_id`, `transaction_type`, `amount`, `event_time`, `available_at`, `prediction_cutoff_at`; event-level graph representation, not a model matrix. |
| `account_graph_features.csv` | Metadata: `scenario_id`, `scenario_group_id`, `account_id`. Features: `in_degree`, `out_degree`, `transaction_count`, `total_observed_amount`, `amount_in`, `amount_out`, `unique_counterparties`, `fan_in`, `fan_out`, `weak_component_size`, `shortest_hops_from_source`. |
| `scenario_graph_features.csv` | Metadata: `scenario_id`, `scenario_group_id`, `prediction_cutoff_at`. Features: `account_count`, `transaction_count`, `unique_directed_pairs`, `repeated_directed_pair_count`, `total_observed_amount`, `mean_transaction_amount`, `weak_component_count`, `largest_weak_component_size`, `source_account_count`, `sink_account_count`, `accounts_reachable_from_sources`, `max_shortest_hops_from_source`, `max_in_degree`, `max_out_degree`, `max_fan_in`, `max_fan_out`. |
| `group_graph_features.csv` | `scenario_group_id`, `scenario_count`, `account_count`, `transaction_count`, `weak_component_count`, `largest_weak_component_size`; split-planning summary only. `scenario_count` is not a prediction feature. |

All graph feature CSV values are serialized as text. Parse the count, degree,
fan, component-size, reachability, and hop-count features as integers. Parse
`total_observed_amount`, `amount_in`, `amount_out`, and
`mean_transaction_amount` as numeric amounts. `shortest_hops_from_source` may
be blank for an account unreachable from any directed source; the scenario
`mean_transaction_amount` may be blank when there are no transaction edges.
Treat those blanks as missing values and choose/document an explicit handling
policy during model preparation. The graph builder and contract do not impute
values or alter feature definitions.

The final model matrix must be selected with the explicit
`MODEL_FEATURE_ALLOWLIST` in `scripts/feature_contract.py`; retain IDs, cutoff,
and group fields separately for joins, temporal checks, and grouped data
splits. Do not include `group_graph_features.csv` as predictors. Never join
`ground_truth_cashouts.csv` or other outcome/target fields into predictors.
`classify_columns` validates feature, metadata, and forbidden column names; it
does not parse dtypes or perform imputation. V2 is unsuitable for blind
evaluation because its labels are reconstructible from the public generation
seed/configuration. V3 remains uncleared for blind evaluation while the
identifier-stream concern is unresolved.

## 13. Synthetic-data v4 (Phase 1)

The isolated Phase 1 generator is `scripts/generate_synthetic_scenarios_v4.py`.
It writes to a **new, explicitly supplied output directory** and refuses to
overwrite an existing path. Run it with `--private-seed` and `--output-dir`;
use a small `--scenarios` value for a focused run. The private seed is consumed
only in memory. It is not included in the manifest, dataset card, reports,
generated files, or normal console output. The manifest records the generator
version, source-code SHA-256, and non-sensitive configuration.

V4 places `cases.csv`, `accounts.csv`, `transactions.csv`, `complaints.csv`,
`candidate_locations.csv`, `scenario_candidate_universe.csv`, and
`prediction_scenarios.csv` under `prediction_inputs/`. The scenario manifest
contains an explicitly fictional origin (`origin_kind`, latitude, longitude,
and `origin_available_at`). Each per-scenario candidate row contains
`origin_distance_km` and `distance_rank` within that scenario's eligible set.
These features and the candidate catalog are available by the prediction
cutoff. Candidate locations and origins are fictional coordinates, not mapped
ATMs or observed incident locations.

The generator now supports two distinct synthetic tasks:

1. **Scenario-level cash-out occurrence:** predict whether any event occurs
   within the declared 72-hour outcome-ascertainment horizon. A positive or
   negative outcome row remains in
   `labels_private/ground_truth_cashouts.csv`; the common
   `outcome_observation_end_at` records the horizon end for both classes.
   Occurrence status is assigned at the configured prevalence independently
   of prediction inputs, so this version provides no intentional signal for
   scenario-level occurrence prediction.
2. **Candidate-level location ranking:** for a positive scenario, rank only
   its eligible candidate set. The fictional target is selected by weighted
   random choice using `0.15 + exp(-origin_distance_km / 2.5)`. Distance is a
   cutoff-available feature, each eligible candidate retains a nonzero chance,
   and the target ID remains in the private outcome file. This rule is a
   simulator assumption, not evidence that real cash-outs favor nearby ATMs.

All scenarios use the same transaction-generation pathway; the positive label
does not control chain hops, transaction types, amounts, or timing. Namespaced
random streams separate chains, identifiers, outcome assignment, target
selection, outcome timing, candidate universes, fictional origins, and row
ordering. The private seed is consumed only in memory and is not stored or
printed. Same seed and configuration reproduce the generated files.

The 72-hour horizon provides a declared basis for later chronological split
purging and embargo, but Phase 1 does not create train/validation/test splits.
Future evaluation must keep scenarios and linked-account components intact,
purge or embargo cases whose outcome horizons cross split boundaries, and keep
test labels inaccessible until the protocol and model are frozen. No adapter,
candidate feature pipeline, or split implementation is part of this phase.

These data can test whether a method recovers the generator's declared
distance-weighted signal. They do not demonstrate predictive value on real
cash-outs. The origin and candidates are fictional, target assignment is an
explicit simplification, candidate coordinates are not point-in-time OSM
evidence, and the current graph feature contract has not yet been integrated
with v4's candidate-level schema. Real-world claims require point-in-time
candidate provenance, a verified origin source, suitable real outcome labels,
a frozen feature contract, component-aware chronological evaluation, test
label isolation, and diagnostics for ID, ordering, timestamp, and simulator
shortcuts. Structural validation and a private seed do not prove the absence
of leakage or create a blind test. V2 remains unsuitable for blind evaluation
because its labels are reconstructible from its public seed; V3 remains
uncleared while its class-dependent fingerprints are unresolved.

Phase 1 writes `dataset_card.md`, `generation_manifest.json`, structural
validation JSON/Markdown reports, and a split report that states assignments
are deferred. The validator checks schemas, primary-key uniqueness, references,
cutoff observability, origin-distance and rank consistency, candidate
eligibility, outcome consistency, target chronology, and the common outcome
horizon. These checks cannot establish the absence of every possible leakage
path. All generated data is fictional simulator output, not evidence of
real-world predictive accuracy.
