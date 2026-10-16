"""Strict Phase 6 feature/metadata/outcome column boundary.

This module defines an allowlist only; it does not build features or train a
model. Opaque identifiers and cutoff fields must be retained as metadata.
Numeric parsing, missing-value policy, and model fitting belong to a later,
explicit model-matrix preparation step.
"""
from __future__ import annotations

MODEL_FEATURE_ALLOWLIST = frozenset({
    "account_count", "transaction_count", "unique_directed_pairs",
    "repeated_directed_pair_count", "total_observed_amount",
    "mean_transaction_amount", "weak_component_count",
    "largest_weak_component_size", "source_account_count", "sink_account_count",
    "accounts_reachable_from_sources", "max_shortest_hops_from_source",
    "max_in_degree", "max_out_degree", "max_fan_in", "max_fan_out",
    "in_degree", "out_degree", "unique_counterparties", "fan_in", "fan_out",
    "weak_component_size", "shortest_hops_from_source", "amount_in", "amount_out",
})

METADATA_COLUMNS = frozenset({
    "scenario_id", "scenario_group_id", "account_id", "transaction_id",
    "complaint_id", "prediction_cutoff_at", "scenario_rule_version",
    "record_origin", "source_system", "source_record_ref",
})

FORBIDDEN_COLUMNS = frozenset({
    "scenario_label", "cashout_present", "cashout_event_id", "location_id",
    "occurred_at", "ground_truth_window_start", "ground_truth_window_end",
    "cashout_lead_hours", "transaction_hops", "label_status", "report_delay_hours",
    "source_fraud_label", "source_flagged_label", "isfraud", "isflaggedfraud",
    "seed", "random_seed", "configuration", "generation_manifest",
    "synthetic_case_region_id",
})


def classify_columns(columns: list[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Return (model_features, metadata), failing closed on all other fields."""
    if len(columns) != len(set(columns)):
        raise ValueError("duplicate columns are not allowed")
    normalized = {column: column.casefold() for column in columns}
    forbidden = [column for column, key in normalized.items() if key in FORBIDDEN_COLUMNS]
    if forbidden:
        raise ValueError(f"forbidden target/provenance columns: {sorted(forbidden)}")
    unknown = [column for column, key in normalized.items()
               if column not in METADATA_COLUMNS and column not in MODEL_FEATURE_ALLOWLIST]
    if unknown:
        raise ValueError(f"unclassified columns require explicit review: {sorted(unknown)}")
    features = [column for column in columns if column in MODEL_FEATURE_ALLOWLIST]
    metadata = [column for column in columns if column in METADATA_COLUMNS]
    return features, metadata
