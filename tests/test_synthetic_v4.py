import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.generate_synthetic_scenarios_v4 import Config, INPUT_SCHEMAS, generate_v4, main, validate_v4_dataset


ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path):
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


class SyntheticV4Tests(unittest.TestCase):
    def generate(self, parent: Path, name: str, seed: int = 987654321987):
        return parent / name, generate_v4(Config(private_seed=seed, output_dir=parent / name, scenarios=24))

    def test_schema_references_cutoffs_and_labels(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output, result = self.generate(Path(temp), "v4")
            self.assertEqual(result["status"], "passed")
            report = validate_v4_dataset(output)
            self.assertEqual(report["status"], "passed")
            for name, expected in INPUT_SCHEMAS.items():
                header, data = rows(output / "prediction_inputs" / name)
                self.assertEqual(header, expected)
                pk = {"cases.csv": "case_id", "accounts.csv": "account_id", "transactions.csv": "transaction_id", "complaints.csv": "complaint_id", "candidate_locations.csv": "location_id", "prediction_scenarios.csv": "scenario_id"}.get(name)
                if pk:
                    self.assertEqual(len(data), len({row[pk] for row in data}))
            _, scenarios = rows(output / "prediction_inputs" / "prediction_scenarios.csv")
            _, labels = rows(output / "labels_private" / "ground_truth_cashouts.csv")
            _, universe = rows(output / "prediction_inputs" / "scenario_candidate_universe.csv")
            self.assertEqual(len(scenarios), 24)
            self.assertEqual(len(labels), 24)
            self.assertTrue(any(row["cashout_present"] == "true" for row in labels))
            self.assertTrue(any(row["cashout_present"] == "false" for row in labels))
            self.assertFalse((output / "splits_private" / "split_assignments.csv").exists())
            self.assertFalse((output / "splits_private").exists())
            self.assertIn("not generated in Phase 1", (output / "reports" / "split_report.md").read_text(encoding="utf-8"))
            scenario_by_id = {row["scenario_id"]: row for row in scenarios}
            for row in scenarios:
                self.assertEqual(row["origin_kind"], "fictional_synthetic_origin")
                self.assertLessEqual(row["origin_available_at"], row["prediction_cutoff_at"])
            for row in universe:
                self.assertLessEqual(row["available_at"], scenario_by_id[row["scenario_id"]]["prediction_cutoff_at"])
                self.assertGreater(float(row["origin_distance_km"]), 0)
                self.assertGreaterEqual(int(row["distance_rank"]), 1)

    def test_outcome_fields_are_separate_and_target_is_eligible(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output, _ = self.generate(Path(temp), "v4")
            _, scenarios = rows(output / "prediction_inputs" / "prediction_scenarios.csv")
            _, labels = rows(output / "labels_private" / "ground_truth_cashouts.csv")
            _, universe = rows(output / "prediction_inputs" / "scenario_candidate_universe.csv")
            universe_pairs = {(row["scenario_id"], row["location_id"]) for row in universe if row["eligible"] == "true"}
            scenario_by_id = {row["scenario_id"]: row for row in scenarios}
            for file in (output / "prediction_inputs").glob("*.csv"):
                header, _ = rows(file)
                self.assertFalse(set(header) & {"cashout_present", "occurred_at", "ground_truth_window_start", "ground_truth_window_end", "outcome_observation_end_at", "latent_outcome_propensity", "private_seed", "seed"})
            for label in labels:
                if label["cashout_present"] == "true":
                    s = scenario_by_id[label["scenario_id"]]
                    self.assertIn((label["scenario_id"], label["location_id"]), universe_pairs)
                    self.assertIn(label["location_id"], json.loads(s["candidate_location_ids"]))
                    self.assertGreater(label["occurred_at"], s["prediction_cutoff_at"])
                    self.assertLessEqual(label["occurred_at"], label["outcome_observation_end_at"])
                    self.assertLessEqual(label["ground_truth_window_end"], label["outcome_observation_end_at"])
                else:
                    self.assertEqual(label["location_id"], "")
                    self.assertEqual(label["occurred_at"], "")
                    self.assertEqual(label["ground_truth_window_start"], "")
                    self.assertEqual(label["ground_truth_window_end"], "")
                    self.assertGreater(label["outcome_observation_end_at"], scenario_by_id[label["scenario_id"]]["prediction_cutoff_at"])
            for label in labels:
                cutoff = datetime.fromisoformat(scenario_by_id[label["scenario_id"]]["prediction_cutoff_at"].replace("Z", "+00:00"))
                expected_end = (cutoff + timedelta(hours=72)).isoformat(timespec="seconds").replace("+00:00", "Z")
                self.assertEqual(label["outcome_observation_end_at"], expected_end)

    def test_same_private_seed_is_reproducible_and_never_persisted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            parent = Path(temp)
            a, _ = self.generate(parent, "a")
            b, _ = self.generate(parent, "b")
            seed_text = "987654321987"
            for relative in sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file()):
                left, right = (a / relative).read_bytes(), (b / relative).read_bytes()
                self.assertEqual(left, right, str(relative))
                self.assertNotIn(seed_text.encode(), left, str(relative))
            manifest = json.loads((a / "generation_manifest.json").read_text(encoding="utf-8"))
            self.assertFalse({key.lower() for key in manifest} & {"seed", "private_seed", "random_seed"})

    def test_private_seed_is_not_logged_by_cli(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            seed = "987654321987"
            output = Path(temp) / "cli-output"
            captured = io.StringIO()
            with redirect_stdout(captured):
                main(["--private-seed", seed, "--output-dir", str(output), "--scenarios", "8"])
            self.assertNotIn(seed, captured.getvalue())

    def test_existing_output_is_refused_without_overwrite(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output = Path(temp) / "occupied"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                generate_v4(Config(private_seed=17, output_dir=output, scenarios=8))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_publish_retries_transient_windows_access_denied(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            parent = Path(temp)
            output = parent / "v4"
            original_rename = Path.rename
            attempts = 0

            def fail_once_then_rename(staging: Path, destination: Path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    error = PermissionError(5, "Access is denied")
                    error.winerror = 5
                    raise error
                return original_rename(staging, destination)

            with patch.object(Path, "rename", new=fail_once_then_rename):
                generate_v4(Config(private_seed=23, output_dir=output, scenarios=8))

            self.assertEqual(attempts, 2)
            self.assertTrue(output.is_dir())

    def test_transaction_generation_does_not_depend_on_outcome_class(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            parent = Path(temp)
            first = generate_v4(Config(private_seed=411, output_dir=parent / "first", scenarios=80, cashout_present_ratio=0.25))
            second = generate_v4(Config(private_seed=411, output_dir=parent / "second", scenarios=80, cashout_present_ratio=0.75))
            first_transactions = Path(first["output_dir"]) / "prediction_inputs" / "transactions.csv"
            second_transactions = Path(second["output_dir"]) / "prediction_inputs" / "transactions.csv"
            self.assertEqual(first_transactions.read_bytes(), second_transactions.read_bytes())

    def test_positive_target_selection_responds_to_candidate_distance(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output = Path(temp) / "large"
            result = generate_v4(Config(private_seed=9137, output_dir=output, scenarios=800))
            _, labels = rows(output / "labels_private" / "ground_truth_cashouts.csv")
            _, universe = rows(output / "prediction_inputs" / "scenario_candidate_universe.csv")
            ranks = {(row["scenario_id"], row["location_id"]): int(row["distance_rank"]) for row in universe}
            counts = {}
            for row in universe:
                counts[row["scenario_id"]] = counts.get(row["scenario_id"], 0) + 1
            positive_target_ranks = [
                ranks[(label["scenario_id"], label["location_id"])]
                for label in labels
                if label["cashout_present"] == "true"
            ]
            random_baseline_mean_rank = sum((count + 1) / 2 for count in counts.values()) / len(counts)
            self.assertEqual(len(positive_target_ranks), result["positive_scenarios"])
            self.assertLess(sum(positive_target_ranks) / len(positive_target_ranks), random_baseline_mean_rank - 0.5)
            self.assertTrue(any(rank > 1 for rank in positive_target_ranks))

    def test_invalid_candidate_count_configuration_fails_clearly(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            for minimum, maximum in ((31, 31), (2, 31)):
                with self.subTest(minimum=minimum, maximum=maximum):
                    with self.assertRaisesRegex(ValueError, "candidate range must satisfy 2 <= min_candidates <= max_candidates <= 30"):
                        generate_v4(Config(private_seed=19, output_dir=Path(temp) / f"invalid-{minimum}-{maximum}", scenarios=8, min_candidates=minimum, max_candidates=maximum))

    def test_validator_fails_when_target_is_not_eligible(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output, _ = self.generate(Path(temp), "v4")
            labels_path = output / "labels_private" / "ground_truth_cashouts.csv"
            header, data = rows(labels_path)
            positive = next(row for row in data if row["cashout_present"] == "true")
            positive["location_id"] = "not-a-candidate"
            with labels_path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=header, lineterminator="\n")
                writer.writeheader()
                writer.writerows(data)
            with self.assertRaisesRegex(ValueError, "scenario-references-and-candidate-eligibility"):
                validate_v4_dataset(output)

    def test_validator_fails_when_transaction_is_unavailable_at_cutoff(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp:
            output, _ = self.generate(Path(temp), "v4")
            tx_path = output / "prediction_inputs" / "transactions.csv"
            header, data = rows(tx_path)
            data[0]["available_at"] = "2035-01-01T00:00:00Z"
            with tx_path.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=header, lineterminator="\n")
                writer.writeheader()
                writer.writerows(data)
            with self.assertRaisesRegex(ValueError, "cutoff-observability-and-target-chronology"):
                validate_v4_dataset(output)


if __name__ == "__main__":
    unittest.main()
