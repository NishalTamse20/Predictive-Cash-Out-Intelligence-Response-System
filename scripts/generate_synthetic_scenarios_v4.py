"""Generate isolated PCIRS v4 synthetic prediction scenarios.

This generator creates fictional prediction inputs and separate outcome labels.
Its results describe simulator behavior and are not evidence of real-world
cash-out prediction accuracy. The private seed is accepted only in memory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "pcirs-synthetic-v4-phase1-location-signal"
BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)
MAX_CANDIDATE_LOCATIONS = 30
OUTCOME_HORIZON_HOURS = 72
TARGET_DISTANCE_DECAY_KM = 2.5
TARGET_UNIFORM_WEIGHT_FLOOR = 0.15
INPUT_SCHEMAS: dict[str, list[str]] = {
    "cases.csv": ["case_id", "scenario_id", "record_origin", "created_at", "available_at"],
    "accounts.csv": ["account_id", "scenario_id", "account_role", "record_origin", "created_at", "available_at"],
    "transactions.csv": ["transaction_id", "scenario_id", "source_account_id", "destination_account_id", "transaction_type", "amount", "event_time", "available_at", "record_origin"],
    "complaints.csv": ["complaint_id", "case_id", "scenario_id", "incident_start_at", "reported_at", "report_delay_seconds", "available_at", "record_origin"],
    "candidate_locations.csv": ["location_id", "latitude", "longitude", "location_kind", "source_name", "source_observed_at", "available_at", "record_origin"],
    "scenario_candidate_universe.csv": ["scenario_id", "location_id", "eligible", "eligibility_reason", "origin_distance_km", "distance_rank", "available_at", "record_origin"],
    "prediction_scenarios.csv": ["scenario_id", "case_id", "complaint_id", "prediction_cutoff_at", "origin_latitude", "origin_longitude", "origin_kind", "origin_available_at", "observed_transaction_ids", "observed_account_ids", "candidate_location_ids", "record_origin", "scenario_rule_version"],
}
LABEL_SCHEMA = ["cashout_event_id", "scenario_id", "cashout_present", "location_id", "occurred_at", "ground_truth_window_start", "ground_truth_window_end", "outcome_observation_end_at", "label_status", "record_origin"]
PKS = {"cases.csv": "case_id", "accounts.csv": "account_id", "transactions.csv": "transaction_id", "complaints.csv": "complaint_id", "candidate_locations.csv": "location_id", "prediction_scenarios.csv": "scenario_id", "ground_truth_cashouts.csv": "cashout_event_id"}
FORBIDDEN_INPUT_FIELDS = {"cashout_present", "cashout_event_id", "ground_truth_window_start", "ground_truth_window_end", "outcome_observation_end_at", "scenario_label", "outcome", "label_status", "latent_outcome_propensity", "seed", "private_seed", "random_seed", "occurred_at"}


@dataclass(frozen=True)
class Config:
    # Required runtime secret. It is deliberately never serialized or printed.
    private_seed: int
    output_dir: Path
    scenarios: int = 100
    cashout_present_ratio: float = 0.5
    min_hops: int = 2
    max_hops: int = 4
    min_candidates: int = 6
    max_candidates: int = 12
    report_delay_min_hours: int = 8
    report_delay_max_hours: int = 36


def _rng(seed: int, purpose: str) -> random.Random:
    digest = hashlib.sha256(f"pcirs-v4\0{seed}\0{purpose}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return fictional point-to-point distance in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    arc = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(arc))


def _target_selection_weights(candidates: list[dict[str, Any]]) -> list[float]:
    """Favor nearer candidates while retaining a nonzero chance for every one."""
    return [
        TARGET_UNIFORM_WEIGHT_FLOOR + math.exp(-float(candidate["origin_distance_km"]) / TARGET_DISTANCE_DECAY_KM)
        for candidate in candidates
    ]


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _validate_config(config: Config) -> Path:
    if config.scenarios < 4:
        raise ValueError("scenarios must be at least 4")
    if not 0 < config.cashout_present_ratio < 1:
        raise ValueError("cashout_present_ratio must be between 0 and 1")
    if config.min_hops < 2 or config.max_hops > 4 or config.min_hops > config.max_hops:
        raise ValueError("hop range must satisfy 2 <= min_hops <= max_hops <= 4")
    if config.min_candidates < 2 or config.max_candidates < config.min_candidates or config.max_candidates > MAX_CANDIDATE_LOCATIONS:
        raise ValueError(f"candidate range must satisfy 2 <= min_candidates <= max_candidates <= {MAX_CANDIDATE_LOCATIONS}")
    if config.report_delay_min_hours < 6 or config.report_delay_max_hours < config.report_delay_min_hours:
        raise ValueError("report delay range must satisfy 6 <= min <= max hours")
    output = Path(config.output_dir).expanduser().absolute()
    if output.exists():
        raise FileExistsError("output directory already exists; choose a new output path")
    if not output.parent.is_dir():
        raise ValueError("output directory parent must already exist")
    if output.is_symlink():
        raise ValueError("output directory cannot be a symlink")
    return output


def _publish_staging_directory(staging: Path, destination: Path) -> None:
    # Windows can briefly deny a directory move while an external scanner has
    # a newly written file open. Retry only that transient OS error; all other
    # failures (and persistent access denials) remain visible to the caller.
    for attempt in range(6):
        if destination.exists():
            raise FileExistsError(f"Output directory already exists: {destination}")
        try:
            staging.rename(destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 5 or attempt == 5:
                raise
            time.sleep(0.05 * (2**attempt))


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def _parse_time(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamps must be UTC ISO-8601 values ending in Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def validate_v4_dataset(output_dir: Path) -> dict[str, Any]:
    """Validate v4 tables; return checks and raise on any failed invariant."""
    root = Path(output_dir)
    inp = root / "prediction_inputs"
    checks: list[dict[str, str]] = []
    tables: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, list[str]] = {}

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail})

    for name, schema in INPUT_SCHEMAS.items():
        path = inp / name
        if not path.is_file():
            check(f"schema:{name}", False, "required input file is missing")
            tables[name], headers[name] = [], []
            continue
        headers[name], tables[name] = _read_csv(path)
        check(f"schema:{name}", headers[name] == schema, "headers match the v4 schema" if headers[name] == schema else f"expected {schema}; found {headers[name]}")
        if name == "scenario_candidate_universe.csv":
            keys = [(row.get("scenario_id", ""), row.get("location_id", "")) for row in tables[name]]
            unique_ok = len(set(keys)) == len(keys) and all(all(key) for key in keys)
        else:
            pk = PKS[name]
            keys = [row.get(pk, "") for row in tables[name]]
            unique_ok = len(set(keys)) == len(keys) and all(keys)
        check(f"unique:{name}", bool(unique_ok), "primary or composite identifiers are present and unique")

    labels_path = root / "labels_private" / "ground_truth_cashouts.csv"
    if labels_path.is_file():
        label_headers, labels = _read_csv(labels_path)
        check("schema:ground_truth_cashouts.csv", label_headers == LABEL_SCHEMA, "private label headers match the v4 schema" if label_headers == LABEL_SCHEMA else f"expected {LABEL_SCHEMA}; found {label_headers}")
        event_ids = [r.get("cashout_event_id", "") for r in labels if r.get("cashout_present") == "true"]
        check("unique:ground_truth_cashouts.csv", len(set(event_ids)) == len(event_ids) and all(event_ids), "positive label event identifiers are present and unique")
    else:
        labels = []
        check("schema:ground_truth_cashouts.csv", False, "required private label file is missing")

    malformed_tables = [
        f"{item['check']}: {item['detail']}"
        for item in checks
        if item["status"] == "fail" and (item["check"].startswith("schema:") or item["check"].startswith("unique:"))
    ]
    if malformed_tables:
        raise ValueError("v4 validation failed: " + "; ".join(malformed_tables))

    leakage = [(name, sorted(set(h) & FORBIDDEN_INPUT_FIELDS)) for name, h in headers.items() if set(h) & FORBIDDEN_INPUT_FIELDS]
    check("no-label-fields-in-inputs", not leakage, "no ground-truth or post-cutoff fields are present" if not leakage else f"forbidden fields found: {leakage}")

    cases = {r.get("case_id", ""): r for r in tables.get("cases.csv", [])}
    accounts = {r.get("account_id", ""): r for r in tables.get("accounts.csv", [])}
    tx_by_id = {r.get("transaction_id", ""): r for r in tables.get("transactions.csv", [])}
    complaints = {r.get("complaint_id", ""): r for r in tables.get("complaints.csv", [])}
    candidates = {r.get("location_id", ""): r for r in tables.get("candidate_locations.csv", [])}
    scenario_rows = {r.get("scenario_id", ""): r for r in tables.get("prediction_scenarios.csv", [])}
    universe = {(r.get("scenario_id", ""), r.get("location_id", "")): r for r in tables.get("scenario_candidate_universe.csv", [])}
    label_by_scenario = {r.get("scenario_id", ""): r for r in labels}
    refs_ok = True
    cutoff_ok = True
    candidate_features_ok = True
    listed_tx: set[str] = set()
    for sid, scenario in scenario_rows.items():
        case_id, complaint_id = scenario.get("case_id", ""), scenario.get("complaint_id", "")
        refs_ok &= case_id in cases and cases.get(case_id, {}).get("scenario_id") == sid
        case = cases.get(case_id)
        complaint = complaints.get(complaint_id)
        refs_ok &= bool(complaint and complaint.get("case_id") == case_id and complaint.get("scenario_id") == sid)
        cutoff = _parse_time(scenario.get("prediction_cutoff_at", ""))
        try:
            origin_lat = float(scenario["origin_latitude"])
            origin_lon = float(scenario["origin_longitude"])
            refs_ok &= math.isfinite(origin_lat) and -90 <= origin_lat <= 90
            refs_ok &= math.isfinite(origin_lon) and -180 <= origin_lon <= 180
            refs_ok &= scenario.get("origin_kind") == "fictional_synthetic_origin"
            cutoff_ok &= _parse_time(scenario["origin_available_at"]) <= cutoff
        except (KeyError, ValueError):
            refs_ok = False
            cutoff_ok = False
            origin_lat = origin_lon = 0.0
        if case:
            cutoff_ok &= _parse_time(case["created_at"]) <= cutoff and _parse_time(case["available_at"]) <= cutoff
        if complaint:
            cutoff_ok &= _parse_time(complaint["available_at"]) <= cutoff and _parse_time(complaint["reported_at"]) <= cutoff
            incident, reported = _parse_time(complaint["incident_start_at"]), _parse_time(complaint["reported_at"])
            try:
                delay_seconds = int(complaint["report_delay_seconds"])
                refs_ok &= delay_seconds >= 0 and delay_seconds == int((reported - incident).total_seconds())
            except (KeyError, ValueError):
                refs_ok = False
            cutoff_ok &= _parse_time(complaint["available_at"]) >= reported
        try:
            observed_txids = json.loads(scenario.get("observed_transaction_ids", "[]"))
            observed_accounts = json.loads(scenario.get("observed_account_ids", "[]"))
            eligible_ids = json.loads(scenario.get("candidate_location_ids", "[]"))
        except (ValueError, TypeError):
            refs_ok = False
            continue
        if not all(isinstance(value, list) for value in (observed_txids, observed_accounts, eligible_ids)):
            refs_ok = False
            continue
        if not all(isinstance(value, str) for values in (observed_txids, observed_accounts, eligible_ids) for value in values):
            refs_ok = False
            continue
        refs_ok &= len(observed_txids) in range(2, 5)
        refs_ok &= len(observed_txids) == len(set(observed_txids))
        refs_ok &= len(observed_accounts) == len(set(observed_accounts))
        refs_ok &= len(eligible_ids) == len(set(eligible_ids))
        listed_tx.update(txid for txid in observed_txids if isinstance(txid, str))
        refs_ok &= all(txid in tx_by_id and tx_by_id[txid].get("scenario_id") == sid for txid in observed_txids)
        refs_ok &= all(aid in accounts for aid in observed_accounts)
        refs_ok &= all(accounts.get(aid, {}).get("scenario_id") == sid for aid in observed_accounts)
        cutoff_ok &= all(_parse_time(accounts[aid]["available_at"]) <= cutoff for aid in observed_accounts if aid in accounts)
        refs_ok &= all((sid, lid) in universe and universe[(sid, lid)].get("eligible") == "true" and lid in candidates for lid in eligible_ids)
        ranked_locations = []
        for lid in eligible_ids:
            candidate, eligibility = candidates.get(lid), universe.get((sid, lid))
            if candidate and eligibility:
                cutoff_ok &= _parse_time(candidate["available_at"]) <= cutoff
                cutoff_ok &= _parse_time(eligibility["available_at"]) <= cutoff
                refs_ok &= candidate.get("record_origin") == "synthetic" and bool(candidate.get("source_name"))
                try:
                    distance = _haversine_km(origin_lat, origin_lon, float(candidate["latitude"]), float(candidate["longitude"]))
                    recorded_distance = float(eligibility["origin_distance_km"])
                    candidate_features_ok &= math.isfinite(recorded_distance) and math.isclose(recorded_distance, distance, rel_tol=0, abs_tol=0.002)
                    ranked_locations.append((distance, float(candidate["latitude"]), float(candidate["longitude"]), lid))
                except (KeyError, ValueError):
                    candidate_features_ok = False
        expected_ranks = {
            lid: rank
            for rank, (_distance, _latitude, _longitude, lid) in enumerate(sorted(ranked_locations), start=1)
        }
        for lid, expected_rank in expected_ranks.items():
            try:
                candidate_features_ok &= int(universe[(sid, lid)]["distance_rank"]) == expected_rank
            except (KeyError, ValueError):
                candidate_features_ok = False
        event_times = []
        chain_edges = []
        for txid in observed_txids:
            tx = tx_by_id.get(txid)
            if not tx:
                continue
            source, destination = tx.get("source_account_id"), tx.get("destination_account_id")
            chain_edges.append((source, destination))
            refs_ok &= source in accounts and destination in accounts
            refs_ok &= accounts.get(source, {}).get("scenario_id") == sid and accounts.get(destination, {}).get("scenario_id") == sid
            event_time, available_at = _parse_time(tx["event_time"]), _parse_time(tx["available_at"])
            event_times.append(event_time)
            cutoff_ok &= event_time <= available_at <= cutoff
            refs_ok &= tx.get("record_origin") == "synthetic" and tx.get("transaction_type") in {"TRANSFER", "PAYMENT", "CASH_OUT"}
            try:
                amount = float(tx["amount"])
                refs_ok &= amount >= 0 and amount < float("inf")
            except (KeyError, ValueError):
                refs_ok = False
        cutoff_ok &= event_times == sorted(event_times) and len(event_times) == len(set(event_times))
        refs_ok &= all(chain_edges[index - 1][1] == chain_edges[index][0] for index in range(1, len(chain_edges)))
        chain_accounts = {account for edge in chain_edges for account in edge}
        refs_ok &= len(observed_accounts) == len(observed_txids) + 1 and set(observed_accounts) == chain_accounts
        eligible_universe_ids = {lid for (universe_sid, lid), row in universe.items() if universe_sid == sid and row.get("eligible") == "true"}
        refs_ok &= eligible_universe_ids == set(eligible_ids)
        refs_ok &= all(aid in {tx_by_id[tid][field] for tid in observed_txids if tid in tx_by_id for field in ("source_account_id", "destination_account_id")} for aid in observed_accounts)
        target = label_by_scenario.get(sid)
        if target:
            is_positive = target.get("cashout_present") == "true"
            if is_positive:
                refs_ok &= target.get("location_id") in eligible_ids and target.get("location_id") in candidates
                cutoff_ok &= _parse_time(target["occurred_at"]) > cutoff
                cutoff_ok &= _parse_time(target["occurred_at"]) <= _parse_time(target["outcome_observation_end_at"])
                if observed_txids:
                    last_event = max(_parse_time(tx_by_id[t]["event_time"]) for t in observed_txids if t in tx_by_id)
                    cutoff_ok &= _parse_time(target["occurred_at"]) > last_event
                cutoff_ok &= _parse_time(target["ground_truth_window_start"]) <= _parse_time(target["occurred_at"]) <= _parse_time(target["ground_truth_window_end"])
                cutoff_ok &= _parse_time(target["ground_truth_window_end"]) <= _parse_time(target["outcome_observation_end_at"])
                refs_ok &= target.get("label_status") == "known" and bool(target.get("cashout_event_id"))
            else:
                refs_ok &= target.get("location_id", "") == "" and target.get("occurred_at", "") == "" and target.get("label_status") == "no_event"
                refs_ok &= not target.get("cashout_event_id")
                refs_ok &= target.get("ground_truth_window_start", "") == "" and target.get("ground_truth_window_end", "") == ""
        if target:
            try:
                cutoff_ok &= _parse_time(target["outcome_observation_end_at"]) == cutoff + timedelta(hours=OUTCOME_HORIZON_HOURS)
            except (KeyError, ValueError):
                cutoff_ok = False
    # Also validate rows that are not reached through a prediction manifest;
    # otherwise an orphan or unlisted future row could sit unnoticed in inputs.
    for case in tables.get("cases.csv", []):
        sid = case.get("scenario_id", "")
        refs_ok &= sid in scenario_rows
        if sid in scenario_rows:
            cutoff = _parse_time(scenario_rows[sid]["prediction_cutoff_at"])
            cutoff_ok &= _parse_time(case["created_at"]) <= _parse_time(case["available_at"]) <= cutoff
    for account in tables.get("accounts.csv", []):
        sid = account.get("scenario_id", "")
        refs_ok &= sid in scenario_rows and account.get("record_origin") == "synthetic"
        if sid in scenario_rows:
            cutoff_ok &= _parse_time(account["available_at"]) <= _parse_time(scenario_rows[sid]["prediction_cutoff_at"])
    for tx in tables.get("transactions.csv", []):
        sid = tx.get("scenario_id", "")
        refs_ok &= sid in scenario_rows and tx.get("transaction_id") in listed_tx
        refs_ok &= tx.get("source_account_id") in accounts and tx.get("destination_account_id") in accounts
        if sid in scenario_rows:
            cutoff = _parse_time(scenario_rows[sid]["prediction_cutoff_at"])
            cutoff_ok &= _parse_time(tx["event_time"]) <= _parse_time(tx["available_at"]) <= cutoff
    for complaint in tables.get("complaints.csv", []):
        sid, case_id = complaint.get("scenario_id", ""), complaint.get("case_id", "")
        refs_ok &= sid in scenario_rows and case_id in cases and cases.get(case_id, {}).get("scenario_id") == sid
    for (sid, lid), eligibility in universe.items():
        refs_ok &= sid in scenario_rows and lid in candidates and eligibility.get("eligible") in {"true", "false"}
        if sid in scenario_rows and lid in candidates:
            cutoff = _parse_time(scenario_rows[sid]["prediction_cutoff_at"])
            cutoff_ok &= _parse_time(eligibility["available_at"]) <= cutoff
            cutoff_ok &= _parse_time(candidates[lid]["available_at"]) <= cutoff
    refs_ok &= all(row.get("scenario_id", "") in scenario_rows for row in labels)
    candidates_valid = True
    for candidate in candidates.values():
        try:
            latitude, longitude = float(candidate["latitude"]), float(candidate["longitude"])
            candidates_valid &= math.isfinite(latitude) and -90 <= latitude <= 90
            candidates_valid &= math.isfinite(longitude) and -180 <= longitude <= 180
            candidates_valid &= _parse_time(candidate["source_observed_at"]) <= _parse_time(candidate["available_at"])
            candidates_valid &= candidate.get("record_origin") == "synthetic" and bool(candidate.get("source_name"))
        except (KeyError, ValueError):
            candidates_valid = False
    check("scenario-references-and-candidate-eligibility", bool(refs_ok), "scenario, case, complaint, account, transaction and candidate references are valid" if refs_ok else "one or more references or target eligibility rules are invalid")
    check("cutoff-observability-and-target-chronology", bool(cutoff_ok), "input records and fictional origins are available by cutoff; outcomes follow the cutoff and share the declared horizon" if cutoff_ok else "an input is unavailable at cutoff or outcome chronology/horizon is invalid")
    check("candidate-distance-features", bool(candidate_features_ok), "candidate distances and eligible-set ranks match the cutoff-available fictional origin" if candidate_features_ok else "candidate distance or rank does not match the fictional origin")
    check("candidate-schema-and-provenance", bool(candidates_valid), "candidate coordinates, provenance and source availability are valid" if candidates_valid else "candidate coordinate or provenance values are invalid")
    check("outcome-consistency", len(labels) == len(scenario_rows) and set(label_by_scenario) == set(scenario_rows) and all(r.get("cashout_present") in {"true", "false"} and r.get("record_origin") == "synthetic" for r in labels), "one valid private positive or negative outcome exists per scenario")
    result = {"dataset_version": "v4", "status": "passed" if all(c["status"] == "pass" for c in checks) else "failed", "scenario_count": len(scenario_rows), "checks": checks, "limitations": ["Structural checks do not prove absence of every possible leakage path.", "Labels describe the synthetic simulator only."]}
    if result["status"] != "passed":
        failed = "; ".join(f"{c['check']}: {c['detail']}" for c in checks if c["status"] == "fail")
        raise ValueError(f"v4 validation failed: {failed}")
    return result


def generate_v4(config: Config) -> dict[str, Any]:
    final_output = _validate_config(config)
    # Build beside the destination and publish only after generation and
    # validation succeed. The temporary name contains no seed material.
    output = Path(tempfile.mkdtemp(prefix=".pcirs-v4-staging-", dir=final_output.parent))
    try:
        inputs = output / "prediction_inputs"
        (inputs).mkdir()
        (output / "labels_private").mkdir()
        (output / "reports").mkdir()
        chains = _rng(config.private_seed, "transaction-chains")
        identifiers = _rng(config.private_seed, "identifiers")
        outcomes_rng = _rng(config.private_seed, "outcome-assignment")
        target_rng = _rng(config.private_seed, "target-selection")
        timing_rng = _rng(config.private_seed, "outcome-timing")
        universe_rng = _rng(config.private_seed, "candidate-universes")
        origins_rng = _rng(config.private_seed, "scenario-origins")
        row_order = _rng(config.private_seed, "row-order")

        locations: list[dict[str, Any]] = []
        for index in range(MAX_CANDIDATE_LOCATIONS):
            row, col = divmod(index, 6)
            locations.append({"location_id": _id(identifiers), "latitude": f"{18.45 + row * .012:.6f}", "longitude": f"{73.82 + col * .012:.6f}", "location_kind": "synthetic_candidate_area", "source_name": "pcirs_synthetic_candidate_grid", "source_observed_at": "2024-12-01T00:00:00Z", "available_at": "2024-12-01T00:00:00Z", "record_origin": "synthetic"})

        cases: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        transactions: list[dict[str, Any]] = []
        complaints: list[dict[str, Any]] = []
        universes: list[dict[str, Any]] = []
        scenarios: list[dict[str, Any]] = []
        labels: list[dict[str, Any]] = []
        positive_count = max(1, min(config.scenarios - 1, round(config.scenarios * config.cashout_present_ratio)))
        # Outcome status is shuffled independently of the stream that creates the
        # transaction chains, IDs, and row ordering.
        outcomes = [True] * positive_count + [False] * (config.scenarios - positive_count)
        outcomes_rng.shuffle(outcomes)

        for index in range(config.scenarios):
            sid, case_id, complaint_id = _id(identifiers), _id(identifiers), _id(identifiers)
            start = BASE_TIME + timedelta(days=index)
            hops = chains.randint(config.min_hops, config.max_hops)
            account_rows = [{"account_id": _id(identifiers), "scenario_id": sid, "account_role": "chain_account", "record_origin": "synthetic", "created_at": _time(start), "available_at": _time(start)} for _ in range(hops + 1)]
            accounts.extend(account_rows)
            txids: list[str] = []
            current = start
            for hop in range(hops):
                if hop:
                    current += timedelta(minutes=chains.randint(8, 110))
                event_type = chains.choices(["TRANSFER", "PAYMENT", "CASH_OUT"], weights=[0.62, 0.28, 0.10])[0]
                transaction_id = _id(identifiers)
                txids.append(transaction_id)
                transactions.append({"transaction_id": transaction_id, "scenario_id": sid, "source_account_id": account_rows[hop]["account_id"], "destination_account_id": account_rows[hop + 1]["account_id"], "transaction_type": event_type, "amount": f"{chains.triangular(20, 25000, 4500):.2f}", "event_time": _time(current), "available_at": _time(current + timedelta(minutes=5)), "record_origin": "synthetic"})
            cutoff = start + timedelta(hours=chains.randint(config.report_delay_min_hours, config.report_delay_max_hours))
            # The configured delay range exceeds the maximum chain duration, so
            # the complete observed trail and its availability precede cutoff.
            delay_seconds = int((cutoff - start).total_seconds())
            cases.append({"case_id": case_id, "scenario_id": sid, "record_origin": "synthetic", "created_at": _time(cutoff), "available_at": _time(cutoff)})
            complaints.append({"complaint_id": complaint_id, "case_id": case_id, "scenario_id": sid, "incident_start_at": _time(start), "reported_at": _time(cutoff), "report_delay_seconds": delay_seconds, "available_at": _time(cutoff), "record_origin": "synthetic"})

            origin_lat = float(f"{origins_rng.uniform(18.45, 18.45 + 4 * .012):.6f}")
            origin_lon = float(f"{origins_rng.uniform(73.82, 73.82 + 5 * .012):.6f}")
            chosen = universe_rng.sample(locations, universe_rng.randint(config.min_candidates, min(config.max_candidates, len(locations))))
            distances = {
                loc["location_id"]: float(f"{_haversine_km(origin_lat, origin_lon, float(loc['latitude']), float(loc['longitude'])):.6f}")
                for loc in chosen
            }
            ranked = sorted(chosen, key=lambda loc: (distances[loc["location_id"]], float(loc["latitude"]), float(loc["longitude"])))
            ranks = {loc["location_id"]: rank for rank, loc in enumerate(ranked, start=1)}
            for loc in chosen:
                universes.append({"scenario_id": sid, "location_id": loc["location_id"], "eligible": "true", "eligibility_reason": "candidate available before prediction cutoff", "origin_distance_km": f"{distances[loc['location_id']]:.6f}", "distance_rank": ranks[loc["location_id"]], "available_at": loc["available_at"], "record_origin": "synthetic"})
            scenario = {"scenario_id": sid, "case_id": case_id, "complaint_id": complaint_id, "prediction_cutoff_at": _time(cutoff), "origin_latitude": f"{origin_lat:.6f}", "origin_longitude": f"{origin_lon:.6f}", "origin_kind": "fictional_synthetic_origin", "origin_available_at": _time(cutoff), "observed_transaction_ids": _json(txids), "observed_account_ids": _json([a["account_id"] for a in account_rows]), "candidate_location_ids": _json([loc["location_id"] for loc in chosen]), "record_origin": "synthetic", "scenario_rule_version": GENERATOR_VERSION}
            scenarios.append(scenario)
            observation_end = cutoff + timedelta(hours=OUTCOME_HORIZON_HOURS)
            positive = outcomes[index]
            # Consume a label-event identifier for every scenario so later IDs
            # cannot reveal whether an earlier scenario was positive.
            cashout_event_id = _id(identifiers)
            if positive:
                target_candidates = [
                    {**loc, "origin_distance_km": distances[loc["location_id"]]}
                    for loc in chosen
                ]
                target = target_rng.choices(target_candidates, weights=_target_selection_weights(target_candidates), k=1)[0]
                occurred = cutoff + timedelta(hours=timing_rng.randint(1, OUTCOME_HORIZON_HOURS - 1))
                labels.append({"cashout_event_id": cashout_event_id, "scenario_id": sid, "cashout_present": "true", "location_id": target["location_id"], "occurred_at": _time(occurred), "ground_truth_window_start": _time(occurred), "ground_truth_window_end": _time(occurred + timedelta(minutes=60)), "outcome_observation_end_at": _time(observation_end), "label_status": "known", "record_origin": "synthetic"})
            else:
                labels.append({"cashout_event_id": "", "scenario_id": sid, "cashout_present": "false", "location_id": "", "occurred_at": "", "ground_truth_window_start": "", "ground_truth_window_end": "", "outcome_observation_end_at": _time(observation_end), "label_status": "no_event", "record_origin": "synthetic"})

        for rows in (cases, accounts, transactions, complaints, locations, universes, scenarios, labels):
            row_order.shuffle(rows)
        for name, schema in INPUT_SCHEMAS.items():
            _write_csv(inputs / name, {"cases.csv": cases, "accounts.csv": accounts, "transactions.csv": transactions, "complaints.csv": complaints, "candidate_locations.csv": locations, "scenario_candidate_universe.csv": universes, "prediction_scenarios.csv": scenarios}[name], schema)
        _write_csv(output / "labels_private" / "ground_truth_cashouts.csv", labels, LABEL_SCHEMA)

        code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        manifest = {"dataset_version": "v4", "generator_version": GENERATOR_VERSION, "generator_sha256": code_hash, "configuration": {"scenarios": config.scenarios, "cashout_present_ratio": config.cashout_present_ratio, "min_hops": config.min_hops, "max_hops": config.max_hops, "min_candidates": config.min_candidates, "max_candidates": config.max_candidates, "report_delay_hours": [config.report_delay_min_hours, config.report_delay_max_hours]}, "scenario_occurrence_assignment": "configured prevalence shuffled independently of prediction inputs; no intentional occurrence signal", "outcome_observation_horizon_hours": OUTCOME_HORIZON_HOURS, "target_selection_rule": {"method": "weighted_random_choice", "weight_formula": "uniform_weight_floor + exp(-origin_distance_km / distance_decay_km)", "uniform_weight_floor": TARGET_UNIFORM_WEIGHT_FLOOR, "distance_decay_km": TARGET_DISTANCE_DECAY_KM}, "model_input_files": [f"prediction_inputs/{name}" for name in INPUT_SCHEMAS], "outcome_only_files": ["labels_private/ground_truth_cashouts.csv"], "split_assignments_implemented": False, "warning": "Origins, locations, selection patterns, and outcomes are fictional simulator constructs, not observed cash-outs or evidence of real-world predictive accuracy. A private seed alone does not create a blind evaluation; freeze the protocol and restrict test labels and revealing information."}
        (output / "generation_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "dataset_card.md").write_text("# PCIRS synthetic dataset v4\n\nThis fictional dataset supports two distinct tasks: scenario-level prediction of whether any cash-out occurs within a 72-hour ascertainment horizon, and candidate-level ranking of eligible fictional locations for positive scenarios. Scenario occurrence status is assigned independently of prediction inputs at the configured prevalence; this release has no intentional scenario-level occurrence signal. Outcomes are simulator-generated and do not establish real-world predictive accuracy.\n\nPrediction inputs are in `prediction_inputs/`. Ground-truth outcomes are isolated in `labels_private/ground_truth_cashouts.csv` and must not be joined into model features. Each scenario has a fictional origin explicitly marked `fictional_synthetic_origin`; eligible-candidate distances and within-scenario distance ranks are available by the prediction cutoff. Positive target selection uses weighted random choice with weight `0.15 + exp(-origin_distance_km / 2.5)`, leaving every eligible candidate a nonzero chance. The target-selection rule is documented in the public generation manifest.\n\nBoth positive and negative outcomes are assessed through `outcome_observation_end_at`, exactly 72 hours after cutoff. Positive cash-out events occur after cutoff and within that horizon. This horizon can inform later temporal split purging and embargo design; split assignments are not generated in Phase 1.\n\nThe fictional origin, grid, distance rule, and outcomes are simulator constructs, not observed incidents, ATMs, or withdrawals. This dataset can test recovery of its declared synthetic location signal only. Real-world evaluation still requires point-in-time sourced candidate data, meaningful observed origins, a frozen feature contract, component-aware chronological splits, label isolation, leakage diagnostics, and independent outcome evidence. The private generation seed is never stored; withholding it alone does not make a blind test. Structural validation cannot establish absence of every possible leakage path.\n", encoding="utf-8")
        report = validate_v4_dataset(output)
        (output / "reports" / "validation_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary = "# Synthetic v4 validation\n\n" + f"Status: **{report['status']}**\n\nScenarios: {report['scenario_count']}\n\n" + "\n".join(f"- {item['status'].upper()}: {item['check']} - {item['detail']}" for item in report["checks"]) + "\n\nStructural checks do not prove absence of every possible leakage path.\n"
        (output / "reports" / "validation_summary.md").write_text(summary, encoding="utf-8")
        (output / "reports" / "split_report.md").write_text(
            "# Synthetic v4 split report\n\n"
            "Split assignments are not generated in Phase 1. No split file or `splits_private/` directory is present.\n\n"
            "A later phase must define and freeze the temporal/grouped split protocol before exposing private test labels. "
            "Withholding the generation seed alone does not provide a blind test.\n",
            encoding="utf-8",
        )
        _publish_staging_directory(output, final_output)
        return {"status": report["status"], "scenarios": len(scenarios), "positive_scenarios": positive_count, "output_dir": str(final_output)}
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        # Do not include config, exception reprs, or seed material in diagnostics.
        raise


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-seed", type=int, required=True, help="private reproducibility seed; never written to outputs")
    parser.add_argument("--output-dir", type=Path, required=True, help="new output directory; it must not already exist")
    parser.add_argument("--scenarios", type=int, default=100)
    parser.add_argument("--cashout-present-ratio", type=float, default=0.5)
    parser.add_argument("--min-hops", type=int, default=2)
    parser.add_argument("--max-hops", type=int, default=4)
    parser.add_argument("--min-candidates", type=int, default=6)
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--report-delay-min-hours", type=int, default=8)
    parser.add_argument("--report-delay-max-hours", type=int, default=36)
    args = parser.parse_args(argv)
    return Config(private_seed=args.private_seed, output_dir=args.output_dir, scenarios=args.scenarios, cashout_present_ratio=args.cashout_present_ratio, min_hops=args.min_hops, max_hops=args.max_hops, min_candidates=args.min_candidates, max_candidates=args.max_candidates, report_delay_min_hours=args.report_delay_min_hours, report_delay_max_hours=args.report_delay_max_hours)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    result = generate_v4(parse_args(argv))
    print(f"Generated and validated {result['scenarios']} v4 scenarios at {result['output_dir']}")
    return result


if __name__ == "__main__":
    main()
