import unittest

from scripts.feature_contract import classify_columns


class FeatureContractTests(unittest.TestCase):
    def test_only_allowlisted_numeric_features_enter_matrix(self):
        features, metadata = classify_columns([
            "scenario_id", "scenario_group_id", "prediction_cutoff_at",
            "transaction_count", "mean_transaction_amount",
        ])
        self.assertEqual(features, ["transaction_count", "mean_transaction_amount"])
        self.assertEqual(metadata, ["scenario_id", "scenario_group_id", "prediction_cutoff_at"])

    def test_graph_amount_features_are_allowlisted(self):
        features, metadata = classify_columns(["amount_in", "amount_out", "account_id", "scenario_id"])
        self.assertEqual(features, ["amount_in", "amount_out"])
        self.assertEqual(metadata, ["account_id", "scenario_id"])

    def test_ground_truth_source_labels_and_seed_are_rejected(self):
        for name in ("cashout_present", "ground_truth_window_start", "source_fraud_label", "seed"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                classify_columns(["transaction_count", name])

    def test_identifiers_unknown_fields_and_duplicate_columns_fail_closed(self):
        features, metadata = classify_columns(["account_id"])
        self.assertEqual(features, [])
        self.assertEqual(metadata, ["account_id"])
        for columns in (["synthetic_case_region_id"], ["scenario_id", "new_field"],
                        ["transaction_count", "transaction_count"]):
            with self.subTest(columns=columns), self.assertRaises(ValueError):
                classify_columns(columns)


if __name__ == "__main__":
    unittest.main()
