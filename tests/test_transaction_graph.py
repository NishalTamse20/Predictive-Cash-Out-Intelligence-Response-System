import csv
import json
import tempfile
import unittest
from pathlib import Path

import networkx as nx

from scripts.build_transaction_graph import build_graph, build_transaction_multidigraph, validate_graph_outputs
from scripts.feature_contract import classify_columns

ROOT = Path(__file__).resolve().parents[1]


def read_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


class TransactionGraphTests(unittest.TestCase):
    def fixture(self, parent: Path, seed=2026) -> Path:
        data = parent / "input"
        data.mkdir(parents=True)
        accounts = []
        transactions = []
        manifests = []
        for group_index in range(2):
            group = f"group-{group_index}"
            a, b, c = (f"acct-{group_index}-{letter}" for letter in "abc")
            accounts.extend({"account_id": account, "scenario_group_id": group,
                             "available_at": "2026-01-01T00:00:00Z"} for account in (a, b, c))
            for case_index in range(4):
                scenario = f"case-{group_index}-{case_index}"
                txids = [f"{scenario}-tx-1", f"{scenario}-tx-2"]
                for txid, source, destination, minute in zip(txids, (a, b), (b, c), (5, 10)):
                    transactions.append({"transaction_id": txid, "scenario_id": scenario,
                        "source_account_id": source, "destination_account_id": destination,
                        "transaction_type": "TRANSFER", "amount": "12.50",
                        "event_time": f"2026-01-01T00:{minute:02d}:00Z",
                        "available_at": f"2026-01-01T00:{minute+1:02d}:00Z",
                        "source_record_ref": f"ref-{txid}"})
                manifests.append({"scenario_id": scenario, "scenario_group_id": group,
                    "prediction_cutoff_at": "2026-01-01T01:00:00Z",
                    "observed_transaction_ids": json.dumps(txids, separators=(",", ":"))})
        write_rows(data / "accounts.csv", ["account_id", "scenario_group_id", "available_at"], accounts)
        write_rows(data / "transactions.csv", ["transaction_id", "scenario_id", "source_account_id",
            "destination_account_id", "transaction_type", "amount", "event_time", "available_at",
            "source_record_ref"], transactions)
        write_rows(data / "prediction_scenarios.csv", ["scenario_id", "scenario_group_id",
            "prediction_cutoff_at", "observed_transaction_ids"], manifests)
        return data

    def test_documented_input_schemas_build_successfully(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
            root = Path(td)
            report = build_graph(self.fixture(root), root / "graph", root / "report.json", root / "summary.md")
            self.assertEqual(report["status"], "passed")

    def test_unexpected_columns_are_rejected_per_input_file(self):
        cases = (("accounts.csv", "accounts.csv", "surprise_account_field"),
                 ("transactions.csv", "transactions.csv", "surprise_transaction_field"),
                 ("prediction_scenarios.csv", "prediction_scenarios.csv", "surprise_manifest_field"))
        for label, filename, unexpected in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
                root = Path(td)
                data = self.fixture(root)
                path = data / filename
                headers, rows = read_rows(path)
                headers.append(unexpected)
                for row in rows:
                    row[unexpected] = "unexpected"
                write_rows(path, headers, rows)
                with self.assertRaisesRegex(ValueError, rf"{filename} has unexpected columns:.*{unexpected}"):
                    build_graph(data, root / "graph", root / "report.json", root / "summary.md")

    def test_outcome_columns_are_rejected_at_raw_input_boundary(self):
        cases = (("accounts.csv", "ground_truth_location_id"),
                 ("transactions.csv", "source_fraud_label"),
                 ("prediction_scenarios.csv", "seed"),
                 ("prediction_scenarios.csv", "occurred_at"))
        for filename, leaked_column in cases:
            with self.subTest(filename=filename, column=leaked_column), tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
                root = Path(td)
                data = self.fixture(root)
                path = data / filename
                headers, rows = read_rows(path)
                headers.append(leaked_column)
                for row in rows:
                    row[leaked_column] = "must-not-enter"
                write_rows(path, headers, rows)
                with self.assertRaisesRegex(ValueError, rf"{filename} has unexpected columns:.*{leaked_column}"):
                    build_graph(data, root / "graph", root / "report.json", root / "summary.md")

    def test_networkx_multidigraph_preserves_direction_parallel_edges_and_event_data(self):
        edges = [
            {"transaction_id": "tx-1", "scenario_id": "case-1", "scenario_group_id": "group-1",
             "source_account_id": "A", "destination_account_id": "B", "transaction_type": "TRANSFER",
             "amount": "12.50", "event_time": "2026-01-01T00:05:00Z",
             "available_at": "2026-01-01T00:06:00Z", "prediction_cutoff_at": "2026-01-01T01:00:00Z"},
            {"transaction_id": "tx-2", "scenario_id": "case-1", "scenario_group_id": "group-1",
             "source_account_id": "A", "destination_account_id": "B", "transaction_type": "TRANSFER",
             "amount": "7.25", "event_time": "2026-01-01T00:10:00Z",
             "available_at": "2026-01-01T00:11:00Z", "prediction_cutoff_at": "2026-01-01T01:00:00Z"},
        ]
        graph = build_transaction_multidigraph(edges)
        self.assertIsInstance(graph, nx.MultiDiGraph)
        self.assertEqual(graph.number_of_edges(), 2)
        self.assertTrue(graph.has_edge("A", "B"))
        self.assertFalse(graph.has_edge("B", "A"))
        self.assertEqual(set(graph["A"]["B"]), {"tx-1", "tx-2"})
        self.assertEqual(graph["A"]["B"]["tx-1"]["amount"], 12.5)
        self.assertEqual(graph["A"]["B"]["tx-1"]["amount_text"], "12.50")
        self.assertEqual(graph["A"]["B"]["tx-1"]["event_time"], "2026-01-01T00:05:00Z")
        self.assertEqual(graph["A"]["B"]["tx-2"]["event_time"], "2026-01-01T00:10:00Z")
        self.assertEqual(graph["A"]["B"]["tx-1"]["available_at"], "2026-01-01T00:06:00Z")
        self.assertEqual(graph["A"]["B"]["tx-1"]["prediction_cutoff_at"], "2026-01-01T01:00:00Z")

    def test_direction_parallel_edges_references_and_degree_features(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
            root=Path(td); data=self.fixture(root); tx_path=data/"transactions.csv"
            tx_headers, txs=read_rows(tx_path)
            original=txs[0]
            duplicate=dict(original)
            duplicate["transaction_id"]="a2eec22e-6952-4d19-9343-cc53d26fcf30"
            duplicate["source_record_ref"]="opaque-extra-reference"
            txs.append(duplicate); write_rows(tx_path,tx_headers,txs)
            manifest_path=data/"prediction_scenarios.csv"
            mh,manifests=read_rows(manifest_path)
            m=next(x for x in manifests if x["scenario_id"]==original["scenario_id"])
            ids=json.loads(m["observed_transaction_ids"]); ids.append(duplicate["transaction_id"])
            m["observed_transaction_ids"]=json.dumps(ids,separators=(",",":")); m["observed_transaction_count"]=str(len(ids))
            mh.append("observed_transaction_count")
            write_rows(manifest_path,mh,manifests)
            out=root/"graph"; report=build_graph(data,out,root/"r.json",root/"r.md")
            _,edges=read_rows(out/"graph_edges.csv")
            same_pair=[e for e in edges if e["source_account_id"]==original["source_account_id"] and e["destination_account_id"]==original["destination_account_id"] and e["scenario_id"]==original["scenario_id"]]
            self.assertEqual(len(same_pair),2)
            self.assertEqual(same_pair[0]["source_account_id"],original["source_account_id"])
            self.assertNotEqual(same_pair[0]["transaction_id"],same_pair[1]["transaction_id"])
            _,features=read_rows(out/"account_graph_features.csv")
            node=next(r for r in features if r["scenario_id"]==original["scenario_id"] and r["account_id"]==original["source_account_id"])
            expected_out=sum(e["scenario_id"]==original["scenario_id"] and e["source_account_id"]==node["account_id"] for e in edges)
            expected_in=sum(e["scenario_id"]==original["scenario_id"] and e["destination_account_id"]==node["account_id"] for e in edges)
            self.assertEqual(int(node["out_degree"]),expected_out)
            self.assertEqual(int(node["in_degree"]),expected_in)
            unique_out = len({e["destination_account_id"] for e in edges if e["scenario_id"] == original["scenario_id"] and e["source_account_id"] == node["account_id"]})
            unique_in = len({e["source_account_id"] for e in edges if e["scenario_id"] == original["scenario_id"] and e["destination_account_id"] == node["account_id"]})
            self.assertEqual(int(node["fan_out"]), unique_out)
            self.assertEqual(int(node["fan_in"]), unique_in)
            self.assertEqual(report["validation"]["status"],"passed")

    def test_reproducible_features_group_metadata_and_no_outcomes_or_seed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
            root=Path(td); data=self.fixture(root); a=root/"graph_a"; b=root/"graph_b"
            build_graph(data,a,root/"a.json",root/"a.md"); build_graph(data,b,root/"b.json",root/"b.md")
            for name in ("graph_nodes.csv","graph_edges.csv","account_graph_features.csv","scenario_graph_features.csv","group_graph_features.csv"):
                self.assertEqual((a/name).read_bytes(),(b/name).read_bytes())
                headers,_=read_rows(a/name)
                forbidden={"scenario_label","cashout_present","location_id","occurred_at","random_seed","seed","configuration","source_fraud_label","source_flagged_label","scenario_rule_version","observed_transaction_ids","observed_account_ids","candidate_location_ids","synthetic_case_region_id"}
                self.assertFalse(set(headers)&forbidden)
                if name in ("account_graph_features.csv", "scenario_graph_features.csv"):
                    model_features, metadata = classify_columns(headers)
                    self.assertTrue(model_features)
                    self.assertIn("scenario_id", metadata)
            _,scenario_rows=read_rows(a/"scenario_graph_features.csv")
            _,group_rows=read_rows(a/"group_graph_features.csv")
            self.assertEqual(len(scenario_rows),8); self.assertEqual(len(group_rows),2)
            self.assertTrue(all(r["scenario_group_id"] for r in scenario_rows+group_rows))
            self.assertTrue(validate_graph_outputs(a)["status"]=="passed")

    def test_builder_rejects_transaction_beyond_manifest_cutoff(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data") as td:
            root=Path(td); data=self.fixture(root); p=data/"transactions.csv"
            headers,txs=read_rows(p); txs[0]["event_time"]="2035-01-01T00:00:00Z"; txs[0]["available_at"]="2035-01-01T00:00:00Z"; write_rows(p,headers,txs)
            with self.assertRaisesRegex(ValueError,"beyond prediction cutoff"):
                build_graph(data,root/"graph",root/"r.json",root/"r.md")


if __name__ == "__main__":
    unittest.main()
