"""Read-only inspection of the PaySim CSV used by PCIRS.

Run from the project root with:
    python -m src.data_inspection.inspect_paysim
"""
from pathlib import Path
import sys

try:
    import pandas as pd
except ImportError as exc:
    raise SystemExit("pandas is required. Install it in the active environment with: python -m pip install pandas") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_ROOT / "data" / "raw" / "paysim.csv"
EXPECTED_COLUMNS = {
    "step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig",
    "nameDest", "oldbalanceDest", "newbalanceDest", "isFraud", "isFlaggedFraud",
}


def inspect_dataset(path: Path = DATASET_PATH) -> None:
    """Print dataset structure and descriptive summaries without changing the CSV."""
    if not path.is_file():
        raise FileNotFoundError(f"PaySim CSV was not found: {path}")

    dataframe = pd.read_csv(path)
    actual_columns = set(dataframe.columns)
    missing_expected = sorted(EXPECTED_COLUMNS - actual_columns)
    unexpected = sorted(actual_columns - EXPECTED_COLUMNS)

    print("PAYSIM DATASET INSPECTION (read-only)")
    print(f"File: {path}")
    print(f"Shape: {dataframe.shape[0]:,} rows x {dataframe.shape[1]:,} columns")
    print("\nColumn names:")
    print("\n".join(f"  {column}" for column in dataframe.columns))
    if missing_expected:
        print("\nExpected PaySim columns not found:")
        print("  " + ", ".join(missing_expected))
    if unexpected:
        print("\nAdditional/unrecognized columns:")
        print("  " + ", ".join(map(str, unexpected)))

    print("\nFirst five rows:")
    print(dataframe.head().to_string(index=False))
    print("\nData types:")
    print(dataframe.dtypes.to_string())
    print("\nMissing values by column:")
    print(dataframe.isna().sum().to_string())
    print(f"\nDuplicate rows: {int(dataframe.duplicated().sum()):,}")

    print("\nTransaction-type distribution:")
    if "type" in dataframe:
        print(dataframe["type"].value_counts(dropna=False).to_string())
    else:
        print("Unavailable: column 'type' is missing.")

    print("\nFraud-label distributions:")
    found_label = False
    for column in ("isFraud", "isFlaggedFraud"):
        if column in dataframe:
            found_label = True
            print(f"{column}:")
            print(dataframe[column].value_counts(dropna=False).to_string())
    if not found_label:
        print("Unavailable: neither 'isFraud' nor 'isFlaggedFraud' is present.")

    print("\nDescriptive statistics for numerical columns:")
    numeric = dataframe.select_dtypes(include="number")
    print(numeric.describe().to_string() if not numeric.empty else "No numeric columns found.")

    print("\nTime-step range:")
    time_column = next((name for name in ("step", "time_step", "timestamp") if name in dataframe), None)
    if time_column is None:
        print("Unavailable: no recognized time column (step, time_step, timestamp).")
    elif dataframe[time_column].notna().any():
        print(f"{time_column}: {dataframe[time_column].min()} to {dataframe[time_column].max()}")
    else:
        print(f"Column '{time_column}' has no non-missing values.")

    print("\nUnique sender and receiver IDs:")
    for label, candidates in (
        ("Sender", ("nameOrig", "sender_id", "origin", "sender")),
        ("Receiver", ("nameDest", "receiver_id", "destination", "receiver")),
    ):
        column = next((name for name in candidates if name in dataframe), None)
        if column is None:
            print(f"{label}: unavailable; no recognized ID column.")
        else:
            print(f"{label} ({column}): {dataframe[column].nunique(dropna=True):,}")


def main() -> int:
    try:
        inspect_dataset()
    except (FileNotFoundError, pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
