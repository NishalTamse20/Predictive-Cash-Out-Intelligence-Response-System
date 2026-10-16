
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "raw" / "paysim.csv"


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {CSV_PATH}")

    total_rows = 0
    fraud_by_type = {}
    missing_counts = {}
    duplicate_sample_count = 0
    amount_stats = []

    columns = [
        "step", "type", "amount", "nameOrig",
        "oldbalanceOrg", "newbalanceOrig", "nameDest",
        "oldbalanceDest", "newbalanceDest", "isFraud",
        "isFlaggedFraud",
    ]

    print("PCIRS - PaySim Data Quality Validation")

    for chunk in pd.read_csv(
        CSV_PATH,
        usecols=columns,
        chunksize=250_000,
    ):
        total_rows += len(chunk)

        # Missing values
        for col, count in chunk.isna().sum().items():
            missing_counts[col] = missing_counts.get(col, 0) + int(count)

        # Fraud labels by transaction type
        counts = chunk.loc[
            chunk["isFraud"] == 1, "type"
        ].value_counts()

        for kind, count in counts.items():
            fraud_by_type[kind] = fraud_by_type.get(kind, 0) + int(count)

        # Basic amount and time validation
        invalid_amount = (chunk["amount"] < 0).sum()
        invalid_step = (chunk["step"] < 0).sum()

        duplicate_sample_count += int(chunk.duplicated().sum())

        amount_stats.append({
            "rows": len(chunk),
            "amount_min": chunk["amount"].min(),
            "amount_max": chunk["amount"].max(),
            "amount_mean": chunk["amount"].mean(),
            "negative_amounts": int(invalid_amount),
            "negative_steps": int(invalid_step),
        })

    stats = pd.DataFrame(amount_stats)

    print(f"\nTotal rows scanned: {total_rows:,}")
    print("\nMissing values by column:")
    print(pd.Series(missing_counts).to_string())

    print("\nFraud-labeled rows by type:")
    print(pd.Series(fraud_by_type).to_string())

    print("\nAmount range across chunks:")
    print(f"Minimum amount: {stats['amount_min'].min():,.2f}")
    print(f"Maximum amount: {stats['amount_max'].max():,.2f}")

    weighted_mean = (
        (stats["amount_mean"] * stats["rows"]).sum()
        / stats["rows"].sum()
    )
    print(f"Mean amount: {weighted_mean:,.2f}")

    print("\nValidation checks:")
    print(f"Negative amounts: {stats['negative_amounts'].sum():,}")
    print(f"Negative steps: {stats['negative_steps'].sum():,}")
    print(
        "Duplicate rows found within individual chunks: "
        f"{duplicate_sample_count:,}"
    )

    print("\nValidation completed.")
    print("Note: duplicate counting here does not detect duplicates")
    print("that occur across different chunks.")


if __name__ == "__main__":
    main()
