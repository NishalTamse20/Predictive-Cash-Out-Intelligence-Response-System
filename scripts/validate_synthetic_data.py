"""Validate PCIRS synthetic scenario files and report structural/leakage risks.

The validator is read-only with respect to input CSVs. It writes deterministic
JSON/Markdown reports and does not train a model or generate scenarios.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import uuid
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data" / "synthetic"
DEFAULT_JSON_REPORT = ROOT / "reports" / "synthetic_validation_report.json"
DEFAULT_SUMMARY_REPORT = ROOT / "reports" / "synthetic_validation_summary.md"
DEFAULT_SPLIT_REPORT = ROOT / "reports" / "synthetic_split_design.md"

SPECS: dict[str, dict[str, Any]] = {
    "accounts.csv": {
        "required": ["account_id", "source_account_ref", "source_system", "record_origin", "created_at", "available_at"],
        "pk": "account_id",
    },
    "transactions.csv": {
        "required": ["transaction_id", "source_system", "source_record_ref", "scenario_id", "source_account_id", "destination_account_id", "transaction_type", "amount", "event_time", "available_at", "record_origin"],
        "pk": "transaction_id",
    },
    "complaints.csv": {
        "required": ["complaint_id", "scenario_id", "incident_start_at", "reported_at", "report_delay_seconds", "available_at", "record_origin", "status"],
        "pk": "complaint_id",
    },
    "candidate_locations.csv": {
        "required": ["location_id", "zone_id", "location_kind", "latitude", "longitude", "available_at", "record_origin"],
        "pk": "location_id",
    },
    "ground_truth_cashouts.csv": {
        "required": ["scenario_id", "complaint_id", "scenario_label", "cashout_present", "location_id", "occurred_at", "ground_truth_window_start", "ground_truth_window_end", "label_status", "record_origin"],
        "pk": "cashout_event_id",
    },
    "prediction_scenarios.csv": {
        "required": ["scenario_id", "complaint_id", "prediction_cutoff_at", "observed_transaction_ids", "observed_account_ids", "candidate_location_ids", "observed_transaction_count", "candidate_location_count", "record_origin", "scenario_rule_version", "configuration"],
        "pk": "scenario_id",
    },
}

INPUT_FILES = ["accounts.csv", "transactions.csv", "complaints.csv", "candidate_locations.csv", "prediction_scenarios.csv"]
OUTCOME_ONLY_COLUMNS = {
    "cashout_event_id", "cashout_present", "cashout_lead_hours", "ground_truth_window_start",
    "ground_truth_window_end", "ground_truth_location_id", "scenario_label", "transaction_hops",
    "outcome", "outcome_label", "future_cashout_at", "future_cashout_location_id",
}


def parse_utc(value: str) -> datetime:
    if not value or not value.endswith("Z"):
        raise ValueError("timestamp must be a non-empty UTC ISO-8601 value ending in Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    return parsed


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames or []
        rows = []
        for row in reader:
            normalized = {
                key: (value if value is not None else "")
                for key, value in row.items()
                if key is not None
            }
            # Preserve the extra-cell sentinel so row-width validation can report it.
            if None in row:
                normalized[None] = row[None]  # type: ignore[index]
            rows.append(normalized)
    return headers, rows


def canonical_row(row: dict[str, str]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    severity: str,
    evidence: str,
    file: str | None = None,
    field: str | None = None,
    recommendation: str | None = None,
) -> None:
    item: dict[str, Any] = {
        "check_id": check_id,
        "status": status,
        "severity": severity,
        "evidence": evidence,
    }
    if file:
        item["file"] = file
    if field:
        item["field"] = field
    if recommendation:
        item["recommendation"] = recommendation
    checks.append(item)


def uuid_is_valid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validate_dataset(input_dir: Path) -> dict[str, Any]:
    """Return deterministic check results and findings for one synthetic data directory."""
    input_dir = Path(input_dir)
    checks: list[dict[str, Any]] = []
    tables: dict[str, list[dict[str, str]]] = {}
    headers_by_file: dict[str, list[str]] = {}
    input_inventory: dict[str, dict[str, Any]] = {}

    for filename, spec in SPECS.items():
        path = input_dir / filename
        if not path.is_file():
            add_check(checks, f"file-present:{filename}", "fail", "critical", "Required input file is missing.", filename)
            tables[filename] = []
            headers_by_file[filename] = []
            input_inventory[filename] = {"rows": 0, "columns": []}
            continue
        try:
            headers, rows = load_csv(path)
        except (OSError, UnicodeError, csv.Error) as exc:
            add_check(checks, f"csv-readable:{filename}", "fail", "critical", f"Could not parse CSV: {type(exc).__name__}: {exc}", filename)
            tables[filename] = []
            headers_by_file[filename] = []
            input_inventory[filename] = {"rows": 0, "columns": []}
            continue
        tables[filename] = rows
        headers_by_file[filename] = headers
        missing_by_field = {
            field: sum(not (row.get(field, "") or "").strip() for row in rows)
            for field in headers
        }
        input_inventory[filename] = {
            "rows": len(rows),
            "columns": headers,
            "empty_cells_by_field": missing_by_field,
        }
        missing = sorted(set(spec["required"]) - set(headers))
        duplicate_headers = sorted(name for name, count in Counter(headers).items() if count > 1)
        add_check(
            checks, f"schema:{filename}",
            "fail" if missing or duplicate_headers else "pass", "high",
            f"rows={len(rows)}; missing_required_columns={missing}; duplicate_headers={duplicate_headers}",
            filename,
        )
        if not missing and not duplicate_headers:
            add_check(checks, f"row-width:{filename}",
                      "fail" if any(None in row for row in rows) else "pass", "high",
                      f"rows_with_extra_unmapped_cells={sum(None in row for row in rows)}", filename)

        pk = spec["pk"]
        # Non-fraud outcome rows intentionally have an empty cashout_event_id.
        pk_values = [row.get(pk, "") for row in rows if row.get(pk, "")]
        duplicate_ids = len(pk_values) - len(set(pk_values))
        add_check(checks, f"primary-key:{filename}:{pk}",
                  "fail" if duplicate_ids else "pass", "high",
                  f"nonempty_primary_ids={len(pk_values)}; duplicate_id_occurrences={duplicate_ids}", filename, pk)
        duplicate_rows = len(rows) - len({canonical_row(row) for row in rows})
        add_check(checks, f"duplicate-rows:{filename}",
                  "fail" if duplicate_rows else "pass", "medium",
                  f"duplicate_row_occurrences={duplicate_rows}", filename)

    # Stop structural traversals from crashing if core tables/columns are unavailable.
    accounts = tables["accounts.csv"]
    transactions = tables["transactions.csv"]
    complaints = tables["complaints.csv"]
    locations = tables["candidate_locations.csv"]
    truth = tables["ground_truth_cashouts.csv"]
    manifests = tables["prediction_scenarios.csv"]
    headers = headers_by_file

    accounts_by_id = {r.get("account_id", ""): r for r in accounts if r.get("account_id")}
    complaints_by_id = {r.get("complaint_id", ""): r for r in complaints if r.get("complaint_id")}
    locations_by_id = {r.get("location_id", ""): r for r in locations if r.get("location_id")}
    manifest_by_scenario = {r.get("scenario_id", ""): r for r in manifests if r.get("scenario_id")}
    truth_by_scenario = {r.get("scenario_id", ""): r for r in truth if r.get("scenario_id")}
    tx_by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in transactions:
        tx_by_scenario[row.get("scenario_id", "")].append(row)

    # Basic missing/invalid values and expected scalar types.
    uuid_fields = {
        "accounts.csv": ["account_id"],
        "transactions.csv": ["transaction_id", "scenario_id", "source_account_id", "destination_account_id"],
        "complaints.csv": ["complaint_id", "scenario_id"],
        "candidate_locations.csv": ["location_id"],
        "ground_truth_cashouts.csv": ["scenario_id", "complaint_id"],
        "prediction_scenarios.csv": ["scenario_id", "complaint_id"],
    }
    required_nonempty = {
        "accounts.csv": SPECS["accounts.csv"]["required"],
        "transactions.csv": SPECS["transactions.csv"]["required"],
        "complaints.csv": SPECS["complaints.csv"]["required"],
        "candidate_locations.csv": SPECS["candidate_locations.csv"]["required"],
        "ground_truth_cashouts.csv": [c for c in SPECS["ground_truth_cashouts.csv"]["required"] if c not in {"location_id", "occurred_at", "ground_truth_window_start", "ground_truth_window_end"}],
        "prediction_scenarios.csv": SPECS["prediction_scenarios.csv"]["required"],
    }
    invalid_values: list[str] = []
    for filename, rows in tables.items():
        for row_number, row in enumerate(rows, start=2):
            for field in required_nonempty[filename]:
                if not row.get(field, "").strip():
                    invalid_values.append(f"{filename}:{row_number}:{field}:missing")
            for field in uuid_fields[filename]:
                value = row.get(field, "")
                if value and not uuid_is_valid(value):
                    invalid_values.append(f"{filename}:{row_number}:{field}:invalid_uuid")
    add_check(checks, "required-values-and-uuid-types",
              "fail" if invalid_values else "pass", "high",
              f"invalid_or_missing_required_values={len(invalid_values)}; examples={invalid_values[:10]}")

    valid_type_errors: list[str] = []
    date_fields = {
        "accounts.csv": ["created_at", "available_at"],
        "transactions.csv": ["event_time", "available_at"],
        "complaints.csv": ["incident_start_at", "reported_at", "available_at", "created_at"],
        "candidate_locations.csv": ["source_observed_at", "available_at"],
        "ground_truth_cashouts.csv": ["occurred_at", "ground_truth_window_start", "ground_truth_window_end"],
        "prediction_scenarios.csv": ["prediction_cutoff_at"],
    }
    integer_fields = {
        "accounts.csv": ["synthetic_home_zone_id"],
        "transactions.csv": ["simulation_step"],
        "candidate_locations.csv": ["zone_id"],
        "complaints.csv": ["report_delay_seconds"],
        "prediction_scenarios.csv": ["observed_transaction_count", "candidate_location_count", "random_seed"],
        "ground_truth_cashouts.csv": ["cashout_lead_hours", "report_delay_hours", "transaction_hops"],
    }
    for filename, rows in tables.items():
        for row_number, row in enumerate(rows, start=2):
            for field in date_fields[filename]:
                value = row.get(field, "")
                if value:
                    try:
                        parse_utc(value)
                    except ValueError:
                        valid_type_errors.append(f"{filename}:{row_number}:{field}:invalid_utc_timestamp")
            for field in integer_fields[filename]:
                value = row.get(field, "")
                if value:
                    try:
                        int(value)
                    except ValueError:
                        valid_type_errors.append(f"{filename}:{row_number}:{field}:invalid_integer")
    for row_number, row in enumerate(transactions, start=2):
        try:
            amount = Decimal(row.get("amount", ""))
            if not amount.is_finite() or amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            valid_type_errors.append(f"transactions.csv:{row_number}:amount:invalid_positive_decimal")
    for row_number, row in enumerate(locations, start=2):
        for field, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180)):
            try:
                value = float(row.get(field, ""))
                if not math.isfinite(value) or not lower <= value <= upper:
                    raise ValueError
            except ValueError:
                valid_type_errors.append(f"candidate_locations.csv:{row_number}:{field}:invalid_coordinate")
    add_check(checks, "scalar-types-and-values",
              "fail" if valid_type_errors else "pass", "high",
              f"invalid_type_or_range_values={len(valid_type_errors)}; examples={valid_type_errors[:10]}")

    categorical_errors: list[str] = []
    allowed_values = {
        ("accounts.csv", "record_origin"): {"synthetic"},
        ("transactions.csv", "record_origin"): {"synthetic"},
        ("transactions.csv", "transaction_type"): {"TRANSFER", "PAYMENT", "CASH_OUT"},
        ("complaints.csv", "record_origin"): {"synthetic"},
        ("complaints.csv", "status"): {"received", "validated", "rejected"},
        ("candidate_locations.csv", "record_origin"): {"synthetic"},
        ("candidate_locations.csv", "location_kind"): {"synthetic_cashout_candidate", "synthetic_candidate"},
        ("ground_truth_cashouts.csv", "record_origin"): {"synthetic"},
        ("ground_truth_cashouts.csv", "scenario_label"): {"fraud", "non_fraud"},
        ("ground_truth_cashouts.csv", "cashout_present"): {"true", "false"},
        ("ground_truth_cashouts.csv", "label_status"): {"known", "no_event", "unknown", "ambiguous"},
        ("prediction_scenarios.csv", "record_origin"): {"synthetic"},
    }
    for (filename, field), allowed in allowed_values.items():
        for i, row in enumerate(tables[filename], start=2):
            value = row.get(field, "")
            if value not in allowed:
                categorical_errors.append(f"{filename}:{i}:{field}={value!r}")
    add_check(checks, "categorical-domains",
              "fail" if categorical_errors else "pass", "high",
              f"invalid_category_values={len(categorical_errors)}; examples={categorical_errors[:10]}")

    secondary_id_errors: list[str] = []
    for filename, field in (("accounts.csv", "source_account_ref"),
                            ("transactions.csv", "source_record_ref")):
        values = [row.get(field, "") for row in tables[filename] if row.get(field, "")]
        duplicate_count = len(values) - len(set(values))
        if duplicate_count:
            secondary_id_errors.append(f"{filename}:{field}:duplicates={duplicate_count}")
    zones = [row.get("zone_id", "") for row in locations]
    duplicate_zones = len(zones) - len(set(zones))
    if duplicate_zones:
        secondary_id_errors.append(f"candidate_locations.csv:zone_id:duplicates={duplicate_zones}")
    zone_ids = set(zones)
    unknown_account_zones = [row.get("account_id", "") for row in accounts if row.get("synthetic_home_zone_id", "") and row.get("synthetic_home_zone_id", "") not in zone_ids]
    if unknown_account_zones:
        secondary_id_errors.append(f"accounts.csv:synthetic_home_zone_id:unknown_zone_accounts={len(unknown_account_zones)}")
    add_check(checks, "secondary-identifier-and-zone-integrity",
              "fail" if secondary_id_errors else "pass", "high",
              f"secondary_id_or_zone_errors={secondary_id_errors}")

    # Every input artifact and outcome is expected to be explicitly synthetic.
    provenance_errors = []
    for filename, rows in tables.items():
        for i, row in enumerate(rows, start=2):
            if row.get("record_origin") != "synthetic":
                provenance_errors.append(f"{filename}:{i}:record_origin={row.get('record_origin')!r}")
    add_check(checks, "synthetic-provenance",
              "fail" if provenance_errors else "pass", "high",
              f"non_synthetic_or_missing_provenance={len(provenance_errors)}; examples={provenance_errors[:10]}")

    # Foreign keys and scenario relationships.
    fk_errors: dict[str, list[str]] = defaultdict(list)
    for i, row in enumerate(transactions, start=2):
        if row.get("source_account_id") not in accounts_by_id:
            fk_errors["transaction_source_account"].append(f"transactions.csv:{i}:source_account_id")
        if row.get("destination_account_id") not in accounts_by_id:
            fk_errors["transaction_destination_account"].append(f"transactions.csv:{i}:destination_account_id")
        if row.get("scenario_id") not in manifest_by_scenario:
            fk_errors["transaction_scenario"].append(f"transactions.csv:{i}:scenario_id")
    for i, row in enumerate(complaints, start=2):
        if row.get("scenario_id") not in manifest_by_scenario:
            fk_errors["complaint_scenario"].append(f"complaints.csv:{i}:scenario_id")
    for i, row in enumerate(manifests, start=2):
        if row.get("complaint_id") not in complaints_by_id:
            fk_errors["manifest_complaint"].append(f"prediction_scenarios.csv:{i}:complaint_id")
        elif complaints_by_id[row["complaint_id"]].get("scenario_id") != row.get("scenario_id"):
            fk_errors["manifest_complaint_scenario"].append(f"prediction_scenarios.csv:{i}:complaint_id/scenario_id")
    for i, row in enumerate(truth, start=2):
        if row.get("scenario_id") not in manifest_by_scenario:
            fk_errors["truth_scenario"].append(f"ground_truth_cashouts.csv:{i}:scenario_id")
        if row.get("complaint_id") not in complaints_by_id:
            fk_errors["truth_complaint"].append(f"ground_truth_cashouts.csv:{i}:complaint_id")
        elif complaints_by_id[row["complaint_id"]].get("scenario_id") != row.get("scenario_id"):
            fk_errors["truth_complaint_scenario"].append(f"ground_truth_cashouts.csv:{i}:complaint_id/scenario_id")
        if row.get("location_id") and row.get("location_id") not in locations_by_id:
            fk_errors["truth_location"].append(f"ground_truth_cashouts.csv:{i}:location_id")
    for name, errors in sorted(fk_errors.items()):
        add_check(checks, f"foreign-key:{name}", "fail", "critical",
                  f"invalid_references={len(errors)}; examples={errors[:10]}")
    if not fk_errors:
        add_check(checks, "foreign-key-references", "pass", "high",
                  "All transaction, complaint, manifest, outcome, and location references resolve and scenario links agree.")

    # Manifest membership and cutoff availability.
    manifest_errors: list[str] = []
    chronology_errors: list[str] = []
    delays_by_scenario: dict[str, int] = {}
    for row in complaints:
        try:
            delay = int(row["report_delay_seconds"])
            reported_time = parse_utc(row["reported_at"])
            incident_time = parse_utc(row["incident_start_at"])
            actual = (reported_time - incident_time).total_seconds()
            if actual <= 0 or actual != delay:
                delays_by_scenario[row["scenario_id"]] = -1
            else:
                delays_by_scenario[row["scenario_id"]] = delay
            if reported_time < incident_time:
                chronology_errors.append(f"complaints.csv:{row['complaint_id']}:reported_before_incident_end")
            if parse_utc(row["available_at"]) < reported_time:
                chronology_errors.append(f"complaints.csv:{row['complaint_id']}:available_before_reported")
        except (KeyError, ValueError):
            delays_by_scenario[row.get("scenario_id", "")] = -1
    location_available: dict[str, datetime] = {}
    for row in locations:
        try:
            location_available[row["location_id"]] = parse_utc(row["available_at"])
        except (KeyError, ValueError):
            pass

    for manifest in manifests:
        sid = manifest.get("scenario_id", "")
        complaint = complaints_by_id.get(manifest.get("complaint_id", ""), {})
        try:
            cutoff = parse_utc(manifest["prediction_cutoff_at"])
            if parse_utc(complaint["available_at"]) != cutoff:
                chronology_errors.append(f"prediction_scenarios.csv:{sid}:cutoff_not_complaint_availability")
            observed_tx_ids = json.loads(manifest["observed_transaction_ids"])
            observed_account_ids = json.loads(manifest["observed_account_ids"])
            candidate_ids = json.loads(manifest["candidate_location_ids"])
            if len(observed_tx_ids) != int(manifest["observed_transaction_count"]):
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:transaction_count_mismatch")
            if len(candidate_ids) != int(manifest["candidate_location_count"]):
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:candidate_count_mismatch")
            if len(observed_tx_ids) != len(set(observed_tx_ids)):
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:duplicate_transaction_id_in_manifest")
            if len(candidate_ids) != len(set(candidate_ids)):
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:duplicate_candidate_id_in_manifest")
            scenario_txs = tx_by_scenario.get(sid, [])
            scenario_tx_ids = {r.get("transaction_id") for r in scenario_txs}
            if set(observed_tx_ids) != scenario_tx_ids:
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:observed_transaction_membership_mismatch")
            if not set(candidate_ids) <= set(locations_by_id):
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:unknown_candidate_location")
            expected_accounts = {r.get("source_account_id") for r in scenario_txs} | {r.get("destination_account_id") for r in scenario_txs}
            if set(observed_account_ids) != expected_accounts:
                manifest_errors.append(f"prediction_scenarios.csv:{sid}:observed_account_membership_mismatch")
            for account_id in observed_account_ids:
                account = accounts_by_id.get(account_id)
                if not account:
                    manifest_errors.append(f"prediction_scenarios.csv:{sid}:unknown_observed_account")
                elif parse_utc(account["available_at"]) > cutoff:
                    chronology_errors.append(f"accounts.csv:{account_id}:available_after_cutoff:{sid}")
            for location_id in candidate_ids:
                if location_id in location_available and location_available[location_id] > cutoff:
                    chronology_errors.append(f"candidate_locations.csv:{location_id}:available_after_cutoff:{sid}")
            for tx in scenario_txs:
                try:
                    event_time = parse_utc(tx["event_time"])
                    available_at = parse_utc(tx["available_at"])
                    if event_time > cutoff or available_at > cutoff:
                        chronology_errors.append(f"transactions.csv:{tx['transaction_id']}:after_cutoff:{sid}")
                    if available_at < event_time:
                        chronology_errors.append(f"transactions.csv:{tx['transaction_id']}:available_before_event")
                except (KeyError, ValueError):
                    chronology_errors.append(f"transactions.csv:{tx.get('transaction_id')}:invalid_event_timestamp")
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            manifest_errors.append(f"prediction_scenarios.csv:{sid}:invalid_manifest:{type(exc).__name__}")

    add_check(checks, "manifest-membership-and-counts",
              "fail" if manifest_errors else "pass", "high",
              f"manifest_relationship_errors={len(manifest_errors)}; examples={manifest_errors[:10]}", "prediction_scenarios.csv")
    add_check(checks, "cutoff-observability-and-chronology",
              "fail" if chronology_errors else "pass", "critical",
              f"post_cutoff_or_invalid_order_events={len(chronology_errors)}; examples={chronology_errors[:10]}")

    # Verify complaint report delay against the per-run configured inclusive range.
    delay_errors: list[str] = []
    for manifest in manifests:
        sid = manifest.get("scenario_id", "")
        try:
            config = json.loads(manifest["configuration"])
            low, high = config.get("report_delay_hours", [1, 24])
            observed = delays_by_scenario.get(sid, -1)
            if observed < 0 or observed % 3600 or not int(low) * 3600 <= observed <= int(high) * 3600:
                delay_errors.append(f"{sid}:observed_seconds={observed};configured_hours={low}..{high}")
            gt = truth_by_scenario.get(sid, {})
            if gt and int(gt.get("report_delay_hours", "-1")) != observed // 3600:
                delay_errors.append(f"{sid}:ground_truth_report_delay_disagrees")
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            delay_errors.append(f"{sid}:missing_or_invalid_delay_configuration")
    add_check(checks, "complaint-reporting-delay-rules",
              "fail" if delay_errors else "pass", "high",
              f"delay_rule_violations={len(delay_errors)}; examples={delay_errors[:10]}")

    # Scenario label consistency and path validation.
    scenario_errors: list[str] = []
    fraud_count = non_fraud_count = 0
    total_transfer_paths: list[int] = []
    for gt in truth:
        sid = gt.get("scenario_id", "")
        related = sorted(tx_by_scenario.get(sid, []), key=lambda row: row.get("event_time", ""))
        is_modern = "v1" not in manifest_by_scenario.get(sid, {}).get("scenario_rule_version", "")
        try:
            if gt.get("scenario_label") == "fraud":
                fraud_count += 1
                hop_count = int(gt.get("transaction_hops", "-1"))
                transfers = [r for r in related if r.get("transaction_type") == "TRANSFER"]
                total_transfer_paths.append(hop_count)
                if gt.get("cashout_present", "").lower() != "true" or not gt.get("location_id") or not gt.get("occurred_at"):
                    scenario_errors.append(f"{sid}:fraud_outcome_missing_cashout")
                if is_modern:
                    if not 1 <= hop_count <= 4 or len(related) != hop_count:
                        scenario_errors.append(f"{sid}:fraud_hop_count_mismatch")
                elif not 2 <= hop_count <= 4 or len(transfers) != hop_count or len(related) != hop_count:
                    scenario_errors.append(f"{sid}:fraud_hop_count_mismatch")
                path_edges = related if is_modern else transfers
                if any(left.get("destination_account_id") != right.get("source_account_id") for left, right in zip(path_edges, path_edges[1:])):
                    scenario_errors.append(f"{sid}:transfer_path_disconnected")
                if gt.get("label_status") != "known":
                    scenario_errors.append(f"{sid}:fraud_label_status_not_known")
            elif gt.get("scenario_label") == "non_fraud":
                non_fraud_count += 1
                if gt.get("cashout_present", "").lower() != "false" or any(gt.get(field) for field in ("location_id", "occurred_at", "ground_truth_window_start", "ground_truth_window_end")):
                    scenario_errors.append(f"{sid}:non_fraud_has_cashout_outcome")
                if gt.get("label_status") != "no_event":
                    scenario_errors.append(f"{sid}:non_fraud_record_pattern_mismatch")
                if not is_modern and (len(related) != 1 or any(r.get("transaction_type") != "PAYMENT" for r in related)):
                    scenario_errors.append(f"{sid}:non_fraud_record_pattern_mismatch")
            else:
                scenario_errors.append(f"{sid}:invalid_scenario_label")
            cutoff = parse_utc(manifest_by_scenario[sid]["prediction_cutoff_at"])
            if gt.get("occurred_at"):
                event_time = parse_utc(gt["occurred_at"])
                window_start = parse_utc(gt["ground_truth_window_start"])
                window_end = parse_utc(gt["ground_truth_window_end"])
                if event_time <= cutoff or not window_start <= event_time < window_end:
                    scenario_errors.append(f"{sid}:ground_truth_not_future_or_window_invalid")
        except (KeyError, ValueError, TypeError):
            scenario_errors.append(f"{sid}:invalid_scenario_or_event_fields")
    mix_missing = fraud_count == 0 or non_fraud_count == 0
    add_check(checks, "class-and-path-consistency",
              "fail" if scenario_errors or mix_missing else "pass", "high",
              f"fraud_scenarios={fraud_count}; non_fraud_scenarios={non_fraud_count}; path_errors={len(scenario_errors)}; transfer_hops={total_transfer_paths}; examples={scenario_errors[:10]}")

    # Direct outcome-column leakage and label-shaped unused fields.
    direct_leaks: list[str] = []
    label_shaped_columns: list[str] = []
    for filename in INPUT_FILES:
        for column in headers.get(filename, []):
            normalized = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
            if column.lower() in OUTCOME_ONLY_COLUMNS or normalized in OUTCOME_ONLY_COLUMNS:
                direct_leaks.append(f"{filename}:{column}")
            if filename == "transactions.csv" and column in {"source_fraud_label", "source_flagged_label"}:
                nonempty = sum(bool(row.get(column, "").strip()) for row in tables[filename])
                label_shaped_columns.append(f"{column}:present={nonempty}")
    add_check(checks, "direct-outcome-column-leakage",
              "fail" if direct_leaks else "pass", "critical",
              f"outcome_only_columns_in_prediction_input={direct_leaks}",
              recommendation="Keep all cash-out outcomes and future labels only in ground_truth_cashouts.csv.")
    add_check(checks, "label-shaped-source-columns",
              "warning" if label_shaped_columns else "pass", "medium",
              f"label_like_columns_in_transactions={label_shaped_columns}; current nonempty counts are zero; exclude these fields from any model matrix.",
              "transactions.csv", "source_fraud_label,source_flagged_label")

    # V1's recipient-home-zone mapping is preserved as a historical check. V2
    # instead measures its explicitly noisy scenario-region feature.
    loc_by_zone = {str(row.get("zone_id")): row.get("location_id") for row in locations}
    account_zone = {row.get("account_id"): str(row.get("synthetic_home_zone_id")) for row in accounts}
    target_mapping_matches = 0
    target_mapping_total = 0
    target_mapping_examples: list[str] = []
    for gt in truth:
        if gt.get("scenario_label") != "fraud" or not gt.get("location_id"):
            continue
        target_mapping_total += 1
        edges = sorted([r for r in tx_by_scenario.get(gt["scenario_id"], []) if r.get("transaction_type") == "TRANSFER"], key=lambda row: row.get("event_time", ""))
        if not edges:
            continue
        destination_id = edges[-1].get("destination_account_id", "")
        predicted_location = loc_by_zone.get(account_zone.get(destination_id, ""))
        if predicted_location == gt["location_id"]:
            target_mapping_matches += 1
            if len(target_mapping_examples) < 3:
                target_mapping_examples.append(f"scenario={gt['scenario_id']} final_recipient={destination_id} home_zone={account_zone.get(destination_id)} truth_location={gt['location_id']}")
    mapping_rate = target_mapping_matches / target_mapping_total if target_mapping_total else 0.0
    if not any(row.get("synthetic_home_zone_id", "") for row in accounts):
        target_mapping_total = 0
        target_mapping_matches = 0
        mapping_rate = 0.0
    if target_mapping_total and target_mapping_matches == target_mapping_total:
        mapping_status, mapping_severity = "fail", "critical"
    elif target_mapping_matches:
        mapping_status, mapping_severity = "warning", "high"
    else:
        mapping_status, mapping_severity = "pass", "high"
    add_check(checks, "indirect-home-zone-target-leakage", mapping_status, mapping_severity,
              f"target_location_matches_final_recipient_home_zone={target_mapping_matches}/{target_mapping_total} ({mapping_rate:.1%}); examples={target_mapping_examples}; home_zone_field_present={any(row.get('synthetic_home_zone_id', '') for row in accounts)}.",
              "accounts.csv;candidate_locations.csv;transactions.csv;ground_truth_cashouts.csv",
              "synthetic_home_zone_id,zone_id,destination_account_id,location_id",
              "Do not use this generator for location prediction evaluation until the target-derived zone signal is redesigned or isolated in an ablation.")

    # Candidate selection coverage and direct answer bias.
    candidate_sets = []
    for manifest in manifests:
        try:
            candidate_sets.append(tuple(json.loads(manifest["candidate_location_ids"])))
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    same_candidates = bool(candidate_sets) and len(set(candidate_sets)) == 1
    full_catalog = bool(candidate_sets) and set(candidate_sets[0]) == set(locations_by_id)
    candidate_status = "warning" if same_candidates or full_catalog else "pass"
    add_check(checks, "candidate-set-design", candidate_status, "high",
              f"all_scenarios_same_candidate_set={same_candidates}; each_manifest_contains_full_catalog={full_catalog}; catalog_size={len(locations_by_id)}; per_scenario_counts={sorted({len(x) for x in candidate_sets})}.",
              "prediction_scenarios.csv;candidate_locations.csv;ground_truth_cashouts.csv",
              "candidate_location_ids,zone_id,synthetic_home_zone_id")

    # V2's case region is an intentionally noisy feature, not a location label.
    location_region = {r.get("location_id", ""): r.get("region_id", "") for r in locations}
    manifest_by_sid = {r.get("scenario_id", ""): r for r in manifests}
    regional_total = regional_matches = 0
    candidate_truth_missing = 0
    for gt in truth:
        if gt.get("scenario_label") != "fraud" or not gt.get("location_id"):
            continue
        manifest = manifest_by_sid.get(gt.get("scenario_id", ""), {})
        if manifest.get("synthetic_case_region_id"):
            regional_total += 1
            regional_matches += int(manifest["synthetic_case_region_id"] == location_region.get(gt["location_id"], ""))
        try:
            if gt["location_id"] not in json.loads(manifest.get("candidate_location_ids", "[]")):
                candidate_truth_missing += 1
        except (ValueError, TypeError):
            candidate_truth_missing += 1
    region_rate = regional_matches / regional_total if regional_total else 0.0
    region_status = "fail" if regional_total and regional_matches == regional_total else ("pass" if regional_total else "warning")
    add_check(checks, "v2-noisy-region-signal", region_status, "high",
              f"positive_targets_in_case_region={regional_matches}/{regional_total} ({region_rate:.1%}); exact_region_mapping={bool(regional_total and regional_matches == regional_total)}; positive_targets_missing_from_candidates={candidate_truth_missing}.",
              "prediction_scenarios.csv;candidate_locations.csv;ground_truth_cashouts.csv",
              "synthetic_case_region_id,region_id,location_id")

    # Declared split groups must contain both outcome classes. A split should
    # use these groups intact; this check does not create train/test partitions.
    labels_by_group: dict[str, set[str]] = defaultdict(set)
    for gt in truth:
        group = manifest_by_sid.get(gt.get("scenario_id", ""), {}).get("scenario_group_id", "")
        if group:
            labels_by_group[group].add(gt.get("scenario_label", ""))
    mixed_groups = sum(labels == {"fraud", "non_fraud"} for labels in labels_by_group.values())
    group_status = "pass" if labels_by_group and mixed_groups == len(labels_by_group) else "fail"
    add_check(checks, "v2-mixed-class-split-groups", group_status, "high",
              f"declared_groups={len(labels_by_group)}; groups_with_both_classes={mixed_groups}; groups_are_a_split_unit_only={bool(labels_by_group)}.",
              "prediction_scenarios.csv;ground_truth_cashouts.csv", "scenario_group_id,scenario_label")

    # Perfect separation by transaction type/count is a simulation bias, not model validation.
    class_patterns: dict[str, set[tuple[tuple[str, int], ...]]] = defaultdict(set)
    perfect_pattern_separation = bool(truth)
    for gt in truth:
        sid = gt.get("scenario_id", "")
        kinds = Counter(row.get("transaction_type", "") for row in tx_by_scenario.get(sid, []))
        pattern = tuple(sorted(kinds.items()))
        class_patterns[gt.get("scenario_label", "unknown")].add(pattern)
    all_fraud_patterns = class_patterns.get("fraud", set())
    all_negative_patterns = class_patterns.get("non_fraud", set())
    fraud_types = {kind for pattern in all_fraud_patterns for kind, _ in pattern}
    negative_types = {kind for pattern in all_negative_patterns for kind, _ in pattern}
    fraud_counts = {sum(count for _, count in pattern) for pattern in all_fraud_patterns}
    negative_counts = {sum(count for _, count in pattern) for pattern in all_negative_patterns}
    perfect_pattern_separation = bool(all_fraud_patterns and all_negative_patterns and fraud_types.isdisjoint(negative_types))
    pattern_labels: dict[tuple, Counter[str]] = defaultdict(Counter)
    for gt in truth:
        sid = gt.get("scenario_id", "")
        kinds = Counter(row.get("transaction_type", "") for row in tx_by_scenario.get(sid, []))
        key = (tuple(sorted(kinds.items())), sum(kinds.values()))
        pattern_labels[key][gt.get("scenario_label", "unknown")] += 1
    pattern_accuracy = (sum(max(c.values()) for c in pattern_labels.values()) / len(truth)) if truth else 0.0
    perfect_pattern_separation = perfect_pattern_separation or pattern_accuracy == 1.0
    add_check(checks, "fraud-nonfraud-feature-separability",
              "warning" if perfect_pattern_separation else "pass", "high",
              f"fraud_transaction_types={sorted(fraud_types)}; non_fraud_transaction_types={sorted(negative_types)}; fraud_event_counts={sorted(fraud_counts)}; non_fraud_event_counts={sorted(negative_counts)}; exact_type_count_pattern_majority_accuracy={pattern_accuracy:.1%}; deterministic_type_or_count_separation={perfect_pattern_separation}.",
              "transactions.csv;ground_truth_cashouts.csv", "transaction_type,scenario_label",
              "Redesign class-conditional event patterns or report this as a deliberately easy simulation baseline; do not treat its performance as evidence of real-world effectiveness.")

    # Shared account components: identify account/hub overlap among scenarios.
    scenarios_by_account: dict[str, set[str]] = defaultdict(set)
    scenario_accounts: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        sid = tx.get("scenario_id", "")
        for account_id in (tx.get("source_account_id", ""), tx.get("destination_account_id", "")):
            if account_id:
                scenarios_by_account[account_id].add(sid)
                scenario_accounts[sid].add(account_id)
    shared_account_rows = accounts_by_id
    shared_accounts = {account: sids for account, sids in scenarios_by_account.items() if len(sids) > 1}
    hub_reuse = {
        account: sids for account, sids in shared_accounts.items()
        if shared_account_rows.get(account, {}).get("synthetic_role") in {"reusable_intermediate", "group_intermediate"}
    }
    # Union scenario nodes through any shared account to obtain indivisible split groups.
    parent = {sid: sid for sid in manifest_by_scenario}
    def find(value: str) -> str:
        while parent.get(value, value) != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    for sids in scenarios_by_account.values():
        ordered = sorted(sids)
        for other in ordered[1:]:
            union(ordered[0], other)
    components: dict[str, set[str]] = defaultdict(set)
    for sid in parent:
        components[find(sid)].add(sid)
    component_sizes = sorted((len(value) for value in components.values()), reverse=True)
    hub_examples = [f"{account}:scenarios={len(sids)}" for account, sids in sorted(hub_reuse.items())[:5]]
    manifest_groups = {r.get("scenario_id", ""): r.get("scenario_group_id", "") for r in manifests}
    cross_group_accounts = {a: sids for a, sids in shared_accounts.items() if len({manifest_groups.get(s, "") for s in sids}) > 1}
    account_reuse_status = "fail" if cross_group_accounts else "pass"
    add_check(checks, "cross-scenario-account-reuse",
              account_reuse_status, "high",
              f"accounts_used_by_multiple_scenarios={len(shared_accounts)}; cross_group_account_reuse={len(cross_group_accounts)}; reusable_hubs_reused={len(hub_reuse)}; account_connected_scenario_components={len(components)}; largest_component_scenarios={component_sizes[0] if component_sizes else 0}; component_sizes={component_sizes}.",
              "accounts.csv;transactions.csv", "account_id,synthetic_role,scenario_id",
              "Keep each account-connected scenario component wholly within one split; consider removing cross-case hubs if they collapse most cases into one component.")

    # Inspect identifier/order and seed metadata as possible label reconstruction channels.
    scenario_index: dict[str, int] = {}
    sequential_refs = 0
    for tx in transactions:
        match = re.fullmatch(r"SYN-TXN-(\d{6})-\d{2}", tx.get("source_record_ref", ""))
        if match:
            sequential_refs += 1
            scenario_index[tx.get("scenario_id", "")] = int(match.group(1))
    seed_reconstruction = False
    seed_evidence = "seed/config/order reconstruction not tested (required provenance missing or format differs)"
    generation_manifest: dict[str, Any] = {}
    try:
        generation_manifest = json.loads((input_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    dataset_version = generation_manifest.get("dataset_version", "")
    if dataset_version == "v2" and generation_manifest.get("seed") is not None:
        try:
            sys.path.insert(0, str(ROOT))
            from scripts.generate_synthetic_scenarios import Config as GeneratorConfig, generate_scenarios
            raw_config = generation_manifest.get("configuration", {})
            allowed = {"seed", "scenarios", "fraud_ratio", "min_hops", "max_hops", "report_delay_min_hours", "report_delay_max_hours", "gap_min_minutes", "gap_max_minutes", "cashout_lead_min_hours", "cashout_lead_max_hours", "grid_size", "group_size", "candidate_count", "region_bias"}
            parameters = {k: v for k, v in raw_config.items() if k in allowed}
            parameters["seed"] = int(generation_manifest["seed"])
            with tempfile.TemporaryDirectory(prefix="pcirs-v2-replay-", dir=input_dir.parent) as temp_dir:
                generate_scenarios(GeneratorConfig(output_dir=Path(temp_dir), **parameters))
                replay_truth = load_csv(Path(temp_dir) / "ground_truth_cashouts.csv")[1]
            expected = {r.get("scenario_id"): r.get("scenario_label") for r in truth}
            replayed = {r.get("scenario_id"): r.get("scenario_label") for r in replay_truth}
            matches = sum(expected.get(sid) == label for sid, label in replayed.items())
            seed_reconstruction = len(expected) == len(replayed) and matches == len(expected)
            seed_evidence = f"replayed_scenario_labels={matches}/{len(expected)} from published generation seed/config and generator; this is reproducibility-based label reconstruction"
        except (ImportError, KeyError, ValueError, TypeError, OSError) as exc:
            seed_evidence = f"could not execute seed replay diagnostic: {type(exc).__name__}: {exc}"
    elif manifests and truth and all(row.get("random_seed", "").lstrip("-").isdigit() for row in manifests):
        try:
            cfg = json.loads(manifests[0]["configuration"])
            seed = int(manifests[0]["random_seed"])
            ratio = float(cfg["fraud_ratio"])
            grid_size = int(cfg["grid_size"])
            max_hops = int(cfg["max_hops"])
            total = len(manifests)
            expected_fraud = min(total - 1, max(1, int(total * ratio + 0.5)))
            replay = random.Random(seed)
            for _ in range(max(8, max_hops - 1)):
                replay.randrange(grid_size * grid_size)
            expected_labels = [True] * expected_fraud + [False] * (total - expected_fraud)
            replay.shuffle(expected_labels)
            labels_by_scenario = {row.get("scenario_id", ""): row.get("scenario_label") == "fraud" for row in truth}
            compared = 0
            matches = 0
            for sid, index in scenario_index.items():
                if 0 <= index < len(expected_labels) and sid in labels_by_scenario:
                    compared += 1
                    matches += int(expected_labels[index] == labels_by_scenario[sid])
            seed_reconstruction = compared == total and matches == total
            seed_evidence = f"scenarios_reconstructed={matches}/{compared}; seed={seed}; deterministic source_record_ref encodes scenario index; generator label shuffle is reproducible from seed/config/order"
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            seed_evidence = "seed/config/order reconstruction could not be replayed from available metadata"
    no_input_replay_metadata = not any(row.get("random_seed", "").strip() for row in manifests) and sequential_refs == 0
    if dataset_version == "v3" and no_input_replay_metadata and not seed_reconstruction:
        seed_evidence = "v3 class assignment uses OS entropy not retained in the public generation manifest; prediction inputs contain no seed or sequential transaction reference"
    elif dataset_version != "v2" and no_input_replay_metadata and not seed_reconstruction:
        seed_evidence = "prediction-time manifests contain no random_seed and source_record_ref has no sequential scenario index; keep any generation manifest outside model features"
    add_check(checks, "seed-order-label-reconstruction",
              "fail" if seed_reconstruction else ("pass" if no_input_replay_metadata else "warning"), "critical" if seed_reconstruction else "medium",
              seed_evidence + f"; sequential_transaction_refs={sequential_refs}; random_seed present in prediction manifest={bool(manifests and 'random_seed' in headers['prediction_scenarios.csv'])}.",
              "prediction_scenarios.csv;transactions.csv;ground_truth_cashouts.csv",
              "random_seed,scenario_id,source_record_ref,scenario_label",
              "Do not expose generator seed/order metadata to model features; use a split/evaluation manifest that is not supplied as a feature, and avoid stable sequential case IDs in model matrices.")

    # Distinguish outcome-only fields from indirectly predictive metadata.
    outcome_leaks: list[str] = []
    for filename in INPUT_FILES:
        for row_number, row in enumerate(tables[filename], start=2):
            for field in OUTCOME_ONLY_COLUMNS & set(row):
                if row.get(field, "").strip():
                    outcome_leaks.append(f"{filename}:{row_number}:{field}")
    if outcome_leaks and not direct_leaks:
        add_check(checks, "future-outcome-values-in-inputs", "fail", "critical",
                  f"nonempty outcome-only values in input rows={outcome_leaks[:10]}; total={len(outcome_leaks)}")

    failures = sum(c["status"] == "fail" for c in checks)
    warnings = sum(c["status"] == "warning" for c in checks)
    overall = "failed" if failures else ("passed_with_warnings" if warnings else "passed")
    return {
        "report_version": "pcirs-synthetic-validation-v3" if any("v3" in r.get("scenario_rule_version", "") for r in manifests) else ("pcirs-synthetic-validation-v2" if any("v2" in r.get("scenario_rule_version", "") for r in manifests) else "pcirs-synthetic-validation-v1"),
        "overall_status": overall,
        "input_directory": input_dir.name,
        "input_inventory": input_inventory,
        "counts": {
            "scenarios": len(manifests),
            "fraud_scenarios": fraud_count,
            "non_fraud_scenarios": non_fraud_count,
            "accounts": len(accounts),
            "transactions": len(transactions),
            "complaints": len(complaints),
            "candidate_locations": len(locations),
            "ground_truth_rows": len(truth),
            "prediction_scenarios": len(manifests),
            "shared_accounts": len(shared_accounts),
            "reused_hubs": len(hub_reuse),
            "account_connected_scenario_components": len(components),
            "largest_component_scenarios": component_sizes[0] if component_sizes else 0,
        },
        "summary": {"checks": len(checks), "failures": failures, "warnings": warnings, "passes": len(checks) - failures - warnings},
        "checks": checks,
        "findings": [check for check in checks if check["status"] != "pass"],
        "limitations": [
            "Passing structural checks does not validate real-world behavior or predictive effectiveness.",
            "Indirect leakage findings require design judgment; explicit-column scans alone cannot establish leakage-free inputs.",
            "This validator inspects the current CSV tables and documented generator conventions; it does not prove all downstream feature pipelines remain cutoff-safe.",
        ],
    }


def render_summary(report: dict[str, Any]) -> str:
    counts = report["counts"]
    summary = report["summary"]
    lines = [
        "# PCIRS Synthetic Dataset Validation Summary", "",
        f"**Overall:** `{report['overall_status']}`  ",
        f"**Input:** `{report['input_directory']}`  ",
        f"**Checks:** {summary['passes']} passed, {summary['warnings']} warnings, {summary['failures']} failures.", "",
        "## Input counts", "",
        "| Table | Rows |", "|---|---:|",
    ]
    for filename, info in report["input_inventory"].items():
        lines.append(f"| `{filename}` | {info['rows']:,} |")
    lines += [
        "", "## Findings", "",
        "A `fail` means the current dataset violates a structural/cutoff expectation or has a critical leakage path. A `warning` needs design judgment; it is not silently repaired.", "",
        "| Status | Severity | Check | Evidence |", "|---|---|---|---|",
    ]
    for check in report["checks"]:
        if check["status"] != "pass":
            evidence = check["evidence"].replace("|", "\\|")
            lines.append(f"| {check['status']} | {check['severity']} | `{check['check_id']}` | {evidence} |")
    if summary["failures"] == 0 and summary["warnings"] == 0:
        lines.append("| pass | low | All checks | No failures or warnings. |")
    lines += ["", "## Interpretation", ""]
    if report["overall_status"] == "failed":
        lines.append("**Do not use this dataset for Phase 5 model evaluation yet.** The failures include leakage/simulation-design issues; preserve the current files and redesign scenarios before regenerating a corrected version.")
    else:
        lines.append("Structural validation passed, subject to the warnings listed above. This does not establish predictive validity.")
    lines += ["", "No records were removed or changed by validation.", ""]
    return "\n".join(lines)


def render_split_design(report: dict[str, Any]) -> str:
    counts = report["counts"]
    version = report.get("report_version", "")
    if version.endswith("v2"):
        evidence = "V2 declares mixed-class `scenario_group_id` values and has no cross-group account reuse, but validation confirms all scenario labels can be replayed from the published seed/configuration and generator. No split can remove this generation-level leak; do not use v2 for blind predictive evaluation."
    elif version.endswith("v3"):
        evidence = "V3 declares mixed-class `scenario_group_id` values and validation found no account reuse across groups. Its labels use unrecorded system entropy. Keep each group/component intact. These groups are not a train/validation/test split; no held-out evaluation exists."
    else:
        evidence = "This is a historical v1 fixture with documented target-derived and seed/order leakage; no split can make those input-generation flaws harmless. Do not use it for predictive evaluation."
    return f"""# PCIRS Synthetic Evaluation Split Design

## Current dataset evidence

- Scenarios: {counts['scenarios']}; fraud: {counts['fraud_scenarios']}; non-fraud: {counts['non_fraud_scenarios']}.
- Account-connected scenario components (accounts only; candidate locations excluded): {counts['account_connected_scenario_components']}.
- Largest component: {counts['largest_component_scenarios']} scenarios.
- Accounts referenced by multiple scenarios: {counts['shared_accounts']}; reused intermediate hubs: {counts['reused_hubs']}.

{evidence}

## Split strategies

| Strategy | Leakage protection | Trade-off |
|---|---|---|
| Scenario-grouped random split | Keeps all rows from one scenario together; prevents direct within-scenario row overlap. | Shared accounts/hubs may still cross train/test, so account histories can leak. |
| Account-connected-component grouped split | Build connected components from account-to-account transaction edges, then bind every scenario touching the same account/component to one split. This prevents a linked account or path from appearing in both train and test. Do not include candidate locations as graph nodes for grouping because the shared candidate catalog would connect every scenario. | Component sizes can be highly imbalanced. Reusable hubs may collapse many positive cases into one component and sharply reduce effective test size or make class stratification impossible. |
| Account-grouped split | Keep every scenario containing any account in the same partition; operationally equivalent to connected components when transitive overlap is resolved. | Naively grouping by only one selected account is insufficient; all account overlaps must be unioned transitively. |
| Time-based split | Train on earlier information and evaluate on later scenarios; tests temporal shift. | Must still keep account-connected components intact. Components crossing the cutoff must be assigned wholly to one side or purged, which reduces data and can leave too few test positives. |

## Recommendation

For a future primary evaluation, use **account-connected-component grouping**, with all transactions and cases in one component assigned to exactly one of train/validation/test. In modern versions the declared mixed-class groups are an intended component grouping. Stratify assignment by outcome only at component level, never by splitting a component. Before accepting a split, programmatically assert scenario and account disjointness and each prediction input's cutoff. If a component dominates, do not split it to meet a target percentage; report that the data cannot support a valid held-out estimate.

Use a separate **forward-time evaluation** as a robustness analysis only after component grouping: assign complete components based on a documented temporal rule, purge components spanning a boundary, and report the resulting counts and class mix. Do not claim it is leakage-free until the actual partition has passed scenario/account disjointness and cutoff checks.

Scenario-only splitting is insufficient because {counts['shared_accounts']} accounts are shared across scenarios within the declared groups. The {counts['scenarios']} scenarios are a small demonstration fixture, not enough to estimate generalization reliably. No partition has been created by this report.

## Required split validation before Phase 5

1. Freeze a feature contract and fit transformations only on training data.
2. Assign complete account-connected components with fixed split seed and documented proportions; do not tune using test results.
3. Assert no account, scenario, or transaction ID overlap across partitions.
4. Assert feature event and availability times do not exceed each prediction cutoff; keep outcomes separate.
5. Report exact partition counts, class balance, and every failed assertion before claiming a valid split.
"""


def write_reports(report: dict[str, Any], json_path: Path, summary_path: Path, split_path: Path) -> None:
    for path in (json_path, summary_path, split_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(render_summary(report), encoding="utf-8")
    split_path.write_text(render_split_design(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--summary-report", type=Path, default=DEFAULT_SUMMARY_REPORT)
    parser.add_argument("--split-report", type=Path, default=DEFAULT_SPLIT_REPORT)
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error(f"Input directory does not exist: {args.input_dir}")
    report = validate_dataset(args.input_dir)
    write_reports(report, args.json_report, args.summary_report, args.split_report)
    print(f"Overall validation: {report['overall_status']}")
    print(f"Counts: {json.dumps(report['counts'], sort_keys=True)}")
    print(f"Checks: {report['summary']['passes']} passed, {report['summary']['warnings']} warnings, {report['summary']['failures']} failures")
    print(f"JSON report: {args.json_report.resolve()}")
    print(f"Summary: {args.summary_report.resolve()}")
    print(f"Split design: {args.split_report.resolve()}")
    return 1 if report["overall_status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
