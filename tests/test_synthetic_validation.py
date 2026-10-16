import csv
import tempfile
import unittest
from pathlib import Path

from scripts.generate_synthetic_scenarios import Config, generate_scenarios
from scripts.validate_synthetic_data import validate_dataset


ROOT = Path(__file__).resolve().parents[1]


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class SyntheticValidationTests(unittest.TestCase):
    def make_fixture(self, parent: Path) -> Path:
        directory = parent / "fixture"
        generate_scenarios(Config(seed=71, scenarios=40, fraud_ratio=0.5, output_dir=directory))
        return directory

    @staticmethod
    def checks(report: dict) -> dict[str, dict]:
        return {check["check_id"]: check for check in report["checks"]}

    def test_validation_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            self.assertEqual(validate_dataset(fixture), validate_dataset(fixture))

    def test_invalid_foreign_key_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            path = fixture / "transactions.csv"
            headers, rows = read_rows(path)
            rows[0]["source_account_id"] = "00000000-0000-0000-0000-000000000001"
            write_rows(path, headers, rows)
            checks = self.checks(validate_dataset(fixture))
            self.assertEqual(checks["foreign-key:transaction_source_account"]["status"], "fail")

    def test_duplicate_primary_id_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            path = fixture / "accounts.csv"
            headers, rows = read_rows(path)
            rows[1]["account_id"] = rows[0]["account_id"]
            write_rows(path, headers, rows)
            checks = self.checks(validate_dataset(fixture))
            self.assertEqual(checks["primary-key:accounts.csv:account_id"]["status"], "fail")

    def test_future_outcome_column_in_input_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            path = fixture / "prediction_scenarios.csv"
            headers, rows = read_rows(path)
            truth = read_rows(fixture / "ground_truth_cashouts.csv")[1]
            headers.append("ground_truth_location_id")
            for row in rows:
                row["ground_truth_location_id"] = truth[0]["location_id"]
            write_rows(path, headers, rows)
            checks = self.checks(validate_dataset(fixture))
            self.assertEqual(checks["direct-outcome-column-leakage"]["status"], "fail")

    def test_future_transaction_after_cutoff_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            path = fixture / "transactions.csv"
            headers, rows = read_rows(path)
            rows[0]["event_time"] = "2030-01-01T00:00:00Z"
            rows[0]["available_at"] = "2030-01-01T00:00:00Z"
            write_rows(path, headers, rows)
            checks = self.checks(validate_dataset(fixture))
            self.assertEqual(checks["cutoff-observability-and-chronology"]["status"], "fail")

    def test_invalid_complaint_chronology_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            path = fixture / "complaints.csv"
            headers, rows = read_rows(path)
            rows[0]["reported_at"] = "2024-12-31T00:00:00Z"
            write_rows(path, headers, rows)
            checks = self.checks(validate_dataset(fixture))
            self.assertEqual(checks["complaint-reporting-delay-rules"]["status"], "fail")
            self.assertEqual(checks["cutoff-observability-and-chronology"]["status"], "fail")

    def test_v2_seed_replay_leakage_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture = self.make_fixture(Path(temp))
            checks = self.checks(validate_dataset(fixture))
            finding = checks["indirect-home-zone-target-leakage"]
            self.assertEqual(finding["status"], "pass")
            self.assertIn("home_zone_field_present=False", finding["evidence"])
            self.assertEqual(checks["seed-order-label-reconstruction"]["status"], "fail")
            self.assertIn("40/40", checks["seed-order-label-reconstruction"]["evidence"])
            self.assertEqual(checks["cross-scenario-account-reuse"]["status"], "pass")
            self.assertEqual(checks["v2-mixed-class-split-groups"]["status"], "pass")
            self.assertEqual(checks["v2-noisy-region-signal"]["status"], "pass")
            self.assertIn("exact_type_count_pattern_majority_accuracy=", checks["fraud-nonfraud-feature-separability"]["evidence"])
            self.assertNotIn("deterministic_type_or_count_separation=True", checks["fraud-nonfraud-feature-separability"]["evidence"])

    def test_v3_seed_does_not_reconstruct_labels(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            fixture=Path(temp)/"v3"
            generate_scenarios(Config(seed=71,scenarios=40,fraud_ratio=0.5,output_dir=fixture,version="v3"))
            checks=self.checks(validate_dataset(fixture))
            self.assertEqual(checks["seed-order-label-reconstruction"]["status"],"pass")
            self.assertIn("OS entropy",checks["seed-order-label-reconstruction"]["evidence"])


if __name__ == "__main__":
    unittest.main()
