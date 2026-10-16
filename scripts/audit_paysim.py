
from pathlib import Path
import pandas as pd

# Locate the project and PaySim dataset automatically
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "paysim.csv"


def main():
    if not CSV_PATH.exists():
        print(f"ERROR: Dataset not found: {CSV_PATH}")
        return

    print("=" * 60)
    print("PCIRS - PaySim Fraud Pattern Audit")
    print("=" * 60)
    print(f"Dataset: {CSV_PATH}")

    total_rows = 0
    all_by_type = {}
    fraud_by_type = {}
    fraud_steps = {}
    account_activity = {}

    print("\nReading dataset in chunks. Please wait...")

    for chunk in pd.read_csv(
        CSV_PATH,
        usecols=[
            "step",
            "type",
            "nameOrig",
            "nameDest",
            "isFraud",
            "isFlaggedFraud",
        ],
        chunksize=250_000,
    ):
        total_rows += len(chunk)

        # Count all transactions by type
        for kind, count in chunk["type"].value_counts().items():
            all_by_type[kind] = all_by_type.get(kind, 0) + int(count)

        # Count fraud-labeled transactions by type
        fraud_chunk = chunk[chunk["isFraud"] == 1]

        for kind, count in fraud_chunk["type"].value_counts().items():
            fraud_by_type[kind] = (
                fraud_by_type.get(kind, 0) + int(count)
            )

        # Count fraud-labeled rows by simulation step
        for step, count in fraud_chunk["step"].value_counts().items():
            fraud_steps[step] = fraud_steps.get(step, 0) + int(count)

        # Count source and destination account appearances
        for column in ["nameOrig", "nameDest"]:
            counts = chunk[column].value_counts()

            for account, count in counts.items():
                account_activity[account] = (
                    account_activity.get(account, 0) + int(count)
                )

    print(f"\nTOTAL TRANSACTIONS: {total_rows:,}")

    # Fraud by transaction type
    summary = pd.DataFrame({
        "all_transactions": pd.Series(all_by_type),
        "fraud_labeled": pd.Series(fraud_by_type),
    }).fillna(0).astype("int64")

    summary["fraud_percent_within_type"] = (
        summary["fraud_labeled"]
        / summary["all_transactions"]
        * 100
    )

    print("\n" + "=" * 60)
    print("FRAUD BY TRANSACTION TYPE")
    print("=" * 60)
    print(
        summary.sort_values(
            "fraud_labeled", ascending=False
        ).to_string()
    )

    # Simulation steps with most fraud-labeled rows
    print("\n" + "=" * 60)
    print("TOP 10 SIMULATION STEPS BY FRAUD-LABELED ROW COUNT")
    print("=" * 60)

    steps_series = pd.Series(fraud_steps, name="fraud_rows")
    print(steps_series.sort_values(ascending=False).head(10).to_string())

    # Account activity summary
    print("\n" + "=" * 60)
    print("ACCOUNT CONNECTIVITY SUMMARY")
    print("=" * 60)

    repeated_accounts = sum(
        count > 1 for count in account_activity.values()
    )

    print(
        "Distinct account IDs appearing as source or destination: "
        f"{len(account_activity):,}"
    )
    print(f"Accounts appearing more than once: {repeated_accounts:,}")

    print("\nAudit completed successfully.")


if __name__ == "__main__":
    main()
