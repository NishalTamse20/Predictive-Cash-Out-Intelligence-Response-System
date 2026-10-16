"""Summarize PaySim balance-equation residuals by transaction type and label.

This is a diagnostic only. PaySim balance behavior is transaction-type and
simulation dependent; a residual is not evidence of corruption or fraud.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV_PATH = ROOT / "data" / "raw" / "paysim.csv"
DEFAULT_REPORT_PATH = ROOT / "reports" / "balance_mismatch_report.csv"
REQUIRED_COLUMNS = [
    "type", "isFraud", "amount", "oldbalanceOrg", "newbalanceOrig",
    "oldbalanceDest", "newbalanceDest",
]


def analyze(csv_path: Path, tolerance: float, chunksize: int) -> pd.DataFrame:
    """Read only required columns and aggregate counts without retaining rows."""
    # key -> [group rows, origin checked, origin invalid, origin mismatches,
    #         destination checked, destination invalid, destination mismatches]
    totals: dict[tuple[str, str], list[int]] = {}

    for chunk in pd.read_csv(
        csv_path, usecols=REQUIRED_COLUMNS, chunksize=chunksize
    ):
        for col in ("amount", "oldbalanceOrg", "newbalanceOrig",
                    "oldbalanceDest", "newbalanceDest", "isFraud"):
            chunk[col] = pd.to_numeric(chunk[col], errors="coerce")

        chunk["type"] = chunk["type"].fillna("<MISSING>").astype(str)
        # Keep labels distinct and explicit when a label is missing/invalid.
        fraud = chunk["isFraud"]
        chunk["_fraud_label"] = fraud.map(
            lambda value: str(int(value)) if pd.notna(value) and value in (0, 1)
            else "<MISSING_OR_INVALID>"
        )

        origin_valid = chunk[["amount", "oldbalanceOrg", "newbalanceOrig"]].notna().all(axis=1)
        destination_valid = chunk[["amount", "oldbalanceDest", "newbalanceDest"]].notna().all(axis=1)
        origin_residual = (
            chunk["oldbalanceOrg"] - chunk["amount"] - chunk["newbalanceOrig"]
        ).abs()
        destination_residual = (
            chunk["oldbalanceDest"] + chunk["amount"] - chunk["newbalanceDest"]
        ).abs()
        chunk["_origin_valid"] = origin_valid
        chunk["_origin_mismatch"] = origin_valid & (origin_residual > tolerance)
        chunk["_destination_valid"] = destination_valid
        chunk["_destination_mismatch"] = destination_valid & (destination_residual > tolerance)

        grouped = chunk.groupby(["type", "_fraud_label"], sort=True, dropna=False)
        for (kind, label), group in grouped:
            key = (str(kind), str(label))
            values = totals.setdefault(key, [0] * 7)
            values[0] += len(group)
            values[1] += int(group["_origin_valid"].sum())
            values[2] += int((~group["_origin_valid"]).sum())
            values[3] += int(group["_origin_mismatch"].sum())
            values[4] += int(group["_destination_valid"].sum())
            values[5] += int((~group["_destination_valid"]).sum())
            values[6] += int(group["_destination_mismatch"].sum())

    rows = []
    for (kind, label), counts in sorted(totals.items()):
        (group_rows, origin_checked, origin_invalid, origin_mismatches,
         destination_checked, destination_invalid, destination_mismatches) = counts
        rows.append({
            "type": kind,
            "isFraud": label,
            "rows_in_group": group_rows,
            "origin_rows_checked": origin_checked,
            "origin_invalid_rows": origin_invalid,
            "origin_mismatch_count": origin_mismatches,
            "origin_mismatch_percentage": (
                100 * origin_mismatches / origin_checked if origin_checked else 0.0
            ),
            "destination_rows_checked": destination_checked,
            "destination_invalid_rows": destination_invalid,
            "destination_mismatch_count": destination_mismatches,
            "destination_mismatch_percentage": (
                100 * destination_mismatches / destination_checked
                if destination_checked else 0.0
            ),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--tolerance", type=float, default=0.01,
                        help="Absolute balance residual threshold (default: 0.01).")
    parser.add_argument("--chunksize", type=int, default=250_000)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"Input CSV does not exist: {args.input}")
    if args.tolerance < 0:
        parser.error("--tolerance must be nonnegative")
    if args.chunksize < 1:
        parser.error("--chunksize must be positive")

    report = analyze(args.input, args.tolerance, args.chunksize)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.output, index=False, float_format="%.8f")
    print(f"Rows grouped: {int(report['rows_in_group'].sum()):,}")
    print(f"Tolerance: {args.tolerance:g}")
    print(f"Report: {args.output}")
    print(report.to_string(index=False))
    print("Diagnostic only: mismatches do not establish corruption or fraud.")


if __name__ == "__main__":
    main()
