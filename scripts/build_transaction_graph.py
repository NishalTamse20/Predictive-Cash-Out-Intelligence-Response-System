"""Build cutoff-safe, fictional transaction graph tables from PCIRS v2.

Uses a NetworkX MultiDiGraph so direction and repeated transaction edges are
preserved. Ground-truth labels and generation manifests are never read as graph
inputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "synthetic" / "v2"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "transaction_graph_v2"
DEFAULT_REPORT = ROOT / "reports" / "transaction_graph_v2_validation_report.json"
DEFAULT_SUMMARY = ROOT / "reports" / "transaction_graph_v2_summary.md"
FORBIDDEN_COLUMNS = {
    "scenario_label", "cashout_present", "cashout_event_id", "cashout_lead_hours",
    "location_id", "ground_truth_location_id", "occurred_at", "ground_truth_window_start",
    "ground_truth_window_end", "transaction_hops", "isfraud", "isflaggedfraud",
    "source_fraud_label", "source_flagged_label", "random_seed", "seed", "configuration",
    "ground_truth_location_id", "target_location_id", "target_cashout_event_id",
    "future_cashout_at", "future_cashout_location_id", "label_status", "report_delay_hours",
    "synthetic_case_region_id", "candidate_location_ids", "outcome", "outcome_label",
}

# Accepted input headers. Optional fields are provenance or source attributes
# that the graph builder deliberately ignores; outcome-bearing fields are never
# accepted, even when their values happen to be blank.
INPUT_SCHEMAS = {
    "accounts.csv": {
        "required": {"account_id", "scenario_group_id", "available_at"},
        "optional": {"source_account_ref", "source_system", "record_origin", "synthetic_role", "created_at"},
    },
    "transactions.csv": {
        "required": {"transaction_id", "scenario_id", "event_time", "available_at", "source_account_id", "destination_account_id", "amount"},
        "optional": {"transaction_type", "scenario_group_id", "source_system", "source_record_ref", "currency", "simulation_step", "origin_old_balance", "origin_new_balance", "destination_old_balance", "destination_new_balance", "record_origin"},
    },
    "prediction_scenarios.csv": {
        "required": {"scenario_id", "scenario_group_id", "prediction_cutoff_at", "observed_transaction_ids"},
        "optional": {"complaint_id", "observed_account_ids", "observed_transaction_count", "candidate_location_count", "record_origin", "scenario_rule_version"},
    },
}


def validate_input_headers(filename: str, headers: list[str]) -> None:
    """Require exactly the documented schema for a graph input CSV."""
    schema = INPUT_SCHEMAS[filename]
    if len(headers) != len(set(headers)):
        duplicates = sorted({name for name in headers if headers.count(name) > 1})
        raise ValueError(f"{filename} has duplicate columns: {duplicates}")
    required = schema["required"]
    allowed = required | schema["optional"]
    missing = sorted(required - set(headers))
    unexpected = sorted(name for name in headers if name not in allowed)
    forbidden = sorted(name for name in headers if name.casefold() in {c.casefold() for c in FORBIDDEN_COLUMNS})
    if missing:
        raise ValueError(f"{filename} is missing required columns: {missing}")
    if unexpected:
        # Include the specific leakage classification in the same actionable error.
        detail = f"; forbidden outcome/provenance columns: {forbidden}" if forbidden else ""
        raise ValueError(f"{filename} has unexpected columns: {unexpected}{detail}")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def parse_utc(value: str, context: str) -> datetime:
    if not value or not value.endswith("Z"):
        raise ValueError(f"{context}: expected UTC timestamp ending Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{context}: expected UTC timestamp")
    return parsed


def build_transaction_multidigraph(edges: list[dict[str, Any]]) -> nx.MultiDiGraph:
    """Create a directed graph with one keyed edge per transaction record."""
    graph = nx.MultiDiGraph()
    transaction_ids: set[str] = set()
    for edge in edges:
        transaction_id = str(edge.get("transaction_id", ""))
        if not transaction_id or transaction_id in transaction_ids:
            raise ValueError(f"missing or duplicate transaction_id: {transaction_id!r}")
        transaction_ids.add(transaction_id)
        amount = float(edge["amount"])
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(f"transaction {transaction_id} has invalid nonnegative amount")
        graph.add_edge(
            edge["source_account_id"],
            edge["destination_account_id"],
            key=transaction_id,
            transaction_id=transaction_id,
            scenario_id=edge["scenario_id"],
            scenario_group_id=edge["scenario_group_id"],
            transaction_type=edge.get("transaction_type", ""),
            amount=amount,
            amount_text=str(edge["amount"]),
            event_time=edge["event_time"],
            available_at=edge["available_at"],
            prediction_cutoff_at=edge["prediction_cutoff_at"],
        )
    return graph


def validate_graph_outputs(output_dir: Path) -> dict[str, Any]:
    """Validate graph outputs without accessing the source outcomes or manifest."""
    errors: list[str] = []
    loaded = {}
    for name in ("graph_nodes.csv", "graph_edges.csv", "account_graph_features.csv", "scenario_graph_features.csv", "group_graph_features.csv"):
        try:
            headers, rows = read_csv(output_dir / name)
        except (OSError, csv.Error, UnicodeError) as exc:
            errors.append(f"{name}:unreadable:{type(exc).__name__}")
            headers, rows = [], []
        loaded[name] = (headers, rows)
        bad = sorted(set(headers) & FORBIDDEN_COLUMNS)
        if bad:
            errors.append(f"{name}:forbidden_columns={bad}")
    node_headers, nodes = loaded["graph_nodes.csv"]
    edge_headers, edges = loaded["graph_edges.csv"]
    account_headers, account_features = loaded["account_graph_features.csv"]
    scenario_headers, scenario_features = loaded["scenario_graph_features.csv"]
    group_headers, group_features = loaded["group_graph_features.csv"]
    node_ids = {r.get("account_id", "") for r in nodes}
    if len(node_ids) != len(nodes):
        errors.append("graph_nodes.csv:duplicate_account_id")
    node_group = {r.get("account_id", ""): r.get("scenario_group_id", "") for r in nodes}
    txids = [r.get("transaction_id", "") for r in edges]
    if len(txids) != len(set(txids)):
        errors.append("graph_edges.csv:duplicate_transaction_id")
    for row in edges:
        if row.get("source_account_id") not in node_ids or row.get("destination_account_id") not in node_ids:
            errors.append(f"graph_edges.csv:{row.get('transaction_id')}:unresolved_account_reference")
        elif any(node_group.get(row.get(k, "")) != row.get("scenario_group_id") for k in ("source_account_id", "destination_account_id")):
            errors.append(f"graph_edges.csv:{row.get('transaction_id')}:account_group_mismatch")
        try:
            if parse_utc(row["event_time"], "edge event") > parse_utc(row["prediction_cutoff_at"], "edge cutoff"):
                errors.append(f"graph_edges.csv:{row.get('transaction_id')}:event_after_cutoff")
            if parse_utc(row["available_at"], "edge availability") > parse_utc(row["prediction_cutoff_at"], "edge cutoff"):
                errors.append(f"graph_edges.csv:{row.get('transaction_id')}:availability_after_cutoff")
        except (KeyError, ValueError) as exc:
            errors.append(f"graph_edges.csv:{row.get('transaction_id')}:invalid_time:{exc}")
    edge_txids = set(txids)
    if not edge_txids and account_features:
        errors.append("account_graph_features.csv:has account rows with no graph edges")
    edge_scenarios = {r.get("scenario_id", "") for r in edges}
    scenario_ids = {r.get("scenario_id", "") for r in scenario_features}
    if edge_scenarios - scenario_ids:
        errors.append("scenario_graph_features.csv:missing scenario summary for edge scenario")
    scenario_group = {r.get("scenario_id", ""): r.get("scenario_group_id", "") for r in scenario_features}
    edge_counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        sid = edge.get("scenario_id", "")
        edge_counts[sid] += 1
        if scenario_group.get(sid) != edge.get("scenario_group_id"):
            errors.append(f"graph_edges.csv:{edge.get('transaction_id')}:scenario_group_mismatch")
    for row in scenario_features:
        try:
            recorded_edges = int(row.get("transaction_count", "-1"))
        except ValueError:
            recorded_edges = -1
        if recorded_edges != edge_counts.get(row.get("scenario_id", ""), 0):
            errors.append(f"scenario_graph_features.csv:{row.get('scenario_id')}:edge_count_mismatch")
    account_keys = [(r.get("scenario_id", ""), r.get("account_id", "")) for r in account_features]
    if len(account_keys) != len(set(account_keys)):
        errors.append("account_graph_features.csv:duplicate_scenario_account_key")
    for row in account_features:
        if row.get("account_id") not in node_ids:
            errors.append(f"account_graph_features.csv:{row.get('account_id')}:unknown_account")
        if scenario_group.get(row.get("scenario_id", "")) != row.get("scenario_group_id"):
            errors.append(f"account_graph_features.csv:{row.get('scenario_id')}:scenario_group_mismatch")
    for name, rows in (("graph_edges.csv", edges), ("account_graph_features.csv", account_features), ("scenario_graph_features.csv", scenario_features), ("group_graph_features.csv", group_features)):
        for row in rows:
            if not row.get("scenario_group_id"):
                errors.append(f"{name}:missing_scenario_group_id")
                break
    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "counts": {"nodes": len(nodes), "edges": len(edges), "account_feature_rows": len(account_features), "scenario_feature_rows": len(scenario_features), "group_feature_rows": len(group_features)},
        "feature_columns": {"account": account_headers, "scenario": scenario_headers, "group": group_headers},
        "checks": ["cutoff timestamps", "edge-to-account and group references", "unique transaction edge IDs", "feature-to-graph count/reference integrity", "required scenario/group IDs", "forbidden labels/seeds/outcomes absent"],
    }


def build_graph(input_dir: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT, summary_path: Path = DEFAULT_SUMMARY) -> dict[str, Any]:
    """Build a cutoff-observed directed multigraph and its initial features."""
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    # Read only these three allowlisted tables. ground_truth_cashouts.csv and
    # generation_manifest.json are intentionally never opened.
    account_headers, account_rows = read_csv(input_dir / "accounts.csv")
    tx_headers, tx_rows = read_csv(input_dir / "transactions.csv")
    manifest_headers, manifests = read_csv(input_dir / "prediction_scenarios.csv")
    validate_input_headers("accounts.csv", account_headers)
    validate_input_headers("transactions.csv", tx_headers)
    validate_input_headers("prediction_scenarios.csv", manifest_headers)
    account_by_id = {r.get("account_id", ""): r for r in account_rows if r.get("account_id")}
    if len(account_by_id) != len(account_rows):
        raise ValueError("accounts.csv has missing or duplicate account_id values")
    manifests_by_id = {r.get("scenario_id", ""): r for r in manifests if r.get("scenario_id")}
    if len(manifests_by_id) != len(manifests):
        raise ValueError("prediction_scenarios.csv has missing or duplicate scenario_id values")
    tx_by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    tx_by_id: dict[str, dict[str, str]] = {}
    for row in tx_rows:
        txid = row.get("transaction_id", "")
        if not txid or txid in tx_by_id:
            raise ValueError(f"transactions.csv has missing or duplicate transaction_id: {txid!r}")
        if row.get("scenario_id") not in manifests_by_id:
            raise ValueError(f"transaction {txid} references unknown scenario")
        tx_by_id[txid] = row
        tx_by_scenario[row["scenario_id"]].append(row)

    # One row per transaction preserves parallel edges without aggregation.
    edge_rows: list[dict[str, Any]] = []
    account_scenarios: dict[str, set[str]] = defaultdict(set)
    scenario_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups_by_account: dict[str, set[str]] = defaultdict(set)
    for sid, manifest in manifests_by_id.items():
        group = manifest.get("scenario_group_id", "")
        if not group:
            raise ValueError(f"scenario {sid} has no scenario_group_id")
        cutoff = parse_utc(manifest.get("prediction_cutoff_at", ""), f"scenario {sid} cutoff")
        try:
            observed_ids = json.loads(manifest.get("observed_transaction_ids", "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"scenario {sid} has invalid observed_transaction_ids") from exc
        if not isinstance(observed_ids, list) or len(observed_ids) != len(set(observed_ids)):
            raise ValueError(f"scenario {sid} observed_transaction_ids must be a unique list")
        observed_set = set(observed_ids)
        rows_for_scenario = tx_by_scenario.get(sid, [])
        source_set = {r["transaction_id"] for r in rows_for_scenario}
        if observed_set != source_set:
            raise ValueError(f"scenario {sid} manifest transaction references do not match transaction rows")
        for tx in rows_for_scenario:
            txid = tx["transaction_id"]
            event = parse_utc(tx.get("event_time", ""), f"transaction {txid} event_time")
            available = parse_utc(tx.get("available_at", ""), f"transaction {txid} available_at")
            if event > cutoff or available > cutoff:
                raise ValueError(f"manifest lists transaction {txid} beyond prediction cutoff")
            if available < event:
                raise ValueError(f"transaction {txid} is available before its event")
            src, dst = tx.get("source_account_id", ""), tx.get("destination_account_id", "")
            if src not in account_by_id or dst not in account_by_id:
                raise ValueError(f"transaction {txid} has unresolved account references")
            for account_id in (src, dst):
                acct = account_by_id[account_id]
                if acct.get("scenario_group_id") != group:
                    raise ValueError(f"account {account_id} group disagrees with scenario {sid}")
                if parse_utc(acct.get("available_at", ""), f"account {account_id} available_at") > cutoff:
                    raise ValueError(f"account {account_id} unavailable at scenario {sid} cutoff")
                account_scenarios[account_id].add(sid)
                groups_by_account[account_id].add(group)
            try:
                amount = float(tx.get("amount", ""))
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(f"transaction {txid} has invalid nonnegative amount") from exc
            edge = {"transaction_id": txid, "scenario_id": sid, "scenario_group_id": group,
                    "source_account_id": src, "destination_account_id": dst,
                    "transaction_type": tx.get("transaction_type", ""), "amount": tx["amount"],
                    "event_time": tx["event_time"], "available_at": tx["available_at"],
                    "prediction_cutoff_at": manifest["prediction_cutoff_at"]}
            edge_rows.append(edge)
            scenario_edges[sid].append(edge)
    cross_group = {a: sorted(gs) for a, gs in groups_by_account.items() if len(gs) > 1}
    if cross_group:
        raise ValueError(f"account identifiers span declared groups: {list(cross_group)[:5]}")

    # Include only accounts observed on an eligible edge; isolated catalog accounts
    # do not contribute to the transaction graph.
    observed_accounts = set(account_scenarios)
    node_rows = [{"account_id": a, "scenario_group_id": next(iter(groups_by_account[a])),
                  "scenario_ids": json.dumps(sorted(account_scenarios[a]), separators=(",", ":"))}
                 for a in sorted(observed_accounts)]
    account_feature_rows: list[dict[str, Any]] = []
    scenario_feature_rows: list[dict[str, Any]] = []
    group_scenarios: dict[str, list[str]] = defaultdict(list)
    scenario_graphs = {
        sid: build_transaction_multidigraph(scenario_edges.get(sid, []))
        for sid in manifests_by_id
    }
    group_graphs: dict[str, nx.MultiDiGraph] = defaultdict(nx.MultiDiGraph)
    combined_graph = nx.MultiDiGraph()
    for sid, graph in scenario_graphs.items():
        for node in graph.nodes:
            group_graphs[manifests_by_id[sid]["scenario_group_id"]].add_node(node)
            combined_graph.add_node(node)
        for source, destination, transaction_id, attrs in graph.edges(keys=True, data=True):
            group = manifests_by_id[sid]["scenario_group_id"]
            group_graphs[group].add_edge(source, destination, key=f"{sid}:{transaction_id}", **attrs)
            combined_graph.add_edge(source, destination, key=f"{sid}:{transaction_id}", **attrs)

    for sid, manifest in sorted(manifests_by_id.items()):
        group = manifest["scenario_group_id"]
        group_scenarios[group].append(sid)
        edges = scenario_edges.get(sid, [])
        graph = scenario_graphs[sid]
        nodes = set(graph.nodes)
        in_count = dict(graph.in_degree())
        out_count = dict(graph.out_degree())
        in_amount: defaultdict[str, float] = defaultdict(float)
        out_amount: defaultdict[str, float] = defaultdict(float)
        counterparties: dict[str, set[str]] = defaultdict(set)
        event_pairs: Counter[tuple[str, str]] = Counter()
        for src, dst, _key, attributes in graph.edges(keys=True, data=True):
            amount = attributes["amount"]
            out_amount[src] += amount
            in_amount[dst] += amount
            counterparties[src].add(dst)
            counterparties[dst].add(src)
            event_pairs[(src, dst)] += 1
        components = list(nx.weakly_connected_components(graph))
        component_size_by_node = {node: len(component) for component in components for node in component}
        sources = {account for account, degree in graph.in_degree() if degree == 0}
        distances = nx.multi_source_dijkstra_path_length(graph, sources, weight=None) if sources else {}
        sinks = {account for account, degree in graph.out_degree() if degree == 0}
        max_hops = max(distances.values(), default=0)
        for account_id in sorted(nodes):
            row = {"scenario_id": sid, "scenario_group_id": group, "account_id": account_id,
                   "in_degree": in_count[account_id], "out_degree": out_count[account_id],
                   "transaction_count": in_count[account_id] + out_count[account_id],
                   "total_observed_amount": f"{in_amount[account_id] + out_amount[account_id]:.2f}",
                   "amount_in": f"{in_amount[account_id]:.2f}", "amount_out": f"{out_amount[account_id]:.2f}",
                   "unique_counterparties": len(counterparties[account_id]),
                   "fan_in": len(set(graph.predecessors(account_id))),
                   "fan_out": len(set(graph.successors(account_id))),
                   "weak_component_size": component_size_by_node[account_id],
                   "shortest_hops_from_source": distances.get(account_id, "")}
            account_feature_rows.append(row)
        total_amount = sum(float(e["amount"]) for e in edges)
        unique_pairs = len(event_pairs)
        scenario_feature_rows.append({"scenario_id": sid, "scenario_group_id": group,
            "prediction_cutoff_at": manifest["prediction_cutoff_at"], "account_count": len(nodes),
            "transaction_count": len(edges), "unique_directed_pairs": unique_pairs,
            "repeated_directed_pair_count": sum(n > 1 for n in event_pairs.values()),
            "total_observed_amount": f"{total_amount:.2f}",
            "mean_transaction_amount": f"{total_amount/len(edges):.2f}" if edges else "",
            "weak_component_count": len(components), "largest_weak_component_size": max(map(len,components), default=0),
            "source_account_count": len(sources), "sink_account_count": len(sinks),
            "accounts_reachable_from_sources": len(distances), "max_shortest_hops_from_source": max_hops,
            "max_in_degree": max((in_count[a] for a in nodes), default=0),
            "max_out_degree": max((out_count[a] for a in nodes), default=0),
            "max_fan_in": max((len(set(graph.predecessors(a))) for a in nodes), default=0),
            "max_fan_out": max((len(set(graph.successors(a))) for a in nodes), default=0)})

    # Group-level connectivity summary used for split planning, never a model feature.
    group_rows=[]
    for group, sids in sorted(group_scenarios.items()):
        group_graph = group_graphs[group]
        group_nodes = set(group_graph.nodes)
        group_edges = group_graph.number_of_edges()
        comps = list(nx.weakly_connected_components(group_graph))
        group_rows.append({"scenario_group_id":group,"scenario_count":len(sids),"account_count":len(group_nodes),"transaction_count":group_edges,"weak_component_count":len(comps),"largest_weak_component_size":max(map(len,comps),default=0)})

    output_dir.mkdir(parents=True,exist_ok=True)
    write_csv(output_dir/"graph_nodes.csv",["account_id","scenario_group_id","scenario_ids"],node_rows)
    write_csv(output_dir/"graph_edges.csv",["transaction_id","scenario_id","scenario_group_id","source_account_id","destination_account_id","transaction_type","amount","event_time","available_at","prediction_cutoff_at"],edge_rows)
    account_cols=["scenario_id","scenario_group_id","account_id","in_degree","out_degree","transaction_count","total_observed_amount","amount_in","amount_out","unique_counterparties","fan_in","fan_out","weak_component_size","shortest_hops_from_source"]
    scenario_cols=["scenario_id","scenario_group_id","prediction_cutoff_at","account_count","transaction_count","unique_directed_pairs","repeated_directed_pair_count","total_observed_amount","mean_transaction_amount","weak_component_count","largest_weak_component_size","source_account_count","sink_account_count","accounts_reachable_from_sources","max_shortest_hops_from_source","max_in_degree","max_out_degree","max_fan_in","max_fan_out"]
    write_csv(output_dir/"account_graph_features.csv",account_cols,account_feature_rows)
    write_csv(output_dir/"scenario_graph_features.csv",scenario_cols,scenario_feature_rows)
    write_csv(output_dir/"group_graph_features.csv",["scenario_group_id","scenario_count","account_count","transaction_count","weak_component_count","largest_weak_component_size"],group_rows)

    global_comps = list(nx.weakly_connected_components(combined_graph))
    report={"input_version":"v2","input_directory":str(input_dir),"output_directory":str(output_dir),"status":"pending_validation",
            "counts":{"source_account_rows":len(account_rows),"unreferenced_account_rows":len(set(account_by_id)-observed_accounts),"nodes":len(node_rows),"edges":len(edge_rows),"scenario_feature_rows":len(scenario_feature_rows),"account_feature_rows":len(account_feature_rows),"groups":len(group_rows),"connected_components":len(global_comps),"largest_component_nodes":max(map(len,global_comps),default=0),"parallel_edge_pairs":sum(int(r["repeated_directed_pair_count"]) for r in scenario_feature_rows)},
            "feature_dimensions":{"scenario_graph_features":{"rows":len(scenario_feature_rows),"columns":len(scenario_cols)},"account_graph_features":{"rows":len(account_feature_rows),"columns":len(account_cols)},"group_graph_features":{"rows":len(group_rows),"columns":6}},
            "complexity":"Per-scenario degree aggregation and weak components are O(V+E); shortest-hop distances use NetworkX multi-source Dijkstra with unit edge costs. No betweenness centrality is computed.",
            "semantics":"Directed multi-edge transaction observations. Only transaction IDs listed in the scenario manifest and available by that scenario cutoff are included. Ground-truth outcomes and generation metadata were not read.",
            "limitations":["Synthetic v2 labels are exactly reconstructible from its published seed/configuration and generator; do not use v2 for blind model evaluation.","Graph edges are simulated events, not verified financial flows or evidence of criminal association.","Account-level rows and scenario/group IDs are join/split metadata, not model features."]}
    validation=validate_graph_outputs(output_dir)
    report["validation"]=validation
    report["status"]="passed" if validation["status"]=="passed" else "failed"
    report_path.parent.mkdir(parents=True,exist_ok=True); report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    summary_path.parent.mkdir(parents=True,exist_ok=True); summary_path.write_text(render_summary(report),encoding="utf-8")
    return report


def render_summary(report: dict[str, Any]) -> str:
    c=report["counts"]; dims=report["feature_dimensions"]; v=report["validation"]
    return f"""# PCIRS v2 Transaction Graph Construction Summary

**Validation:** `{report['status']}` ({len(v['errors'])} errors)  
**Input:** `{report['input_directory']}`  
**Output:** `{report['output_directory']}`

## Graph and feature counts

- Account nodes: {c['nodes']}
- Source account rows omitted because they have no observed transaction edge at a cutoff: {c['unreferenced_account_rows']} of {c['source_account_rows']}
- Directed transaction edges: {c['edges']} (one row per transaction; parallel observations retained)
- Account-connected components: {c['connected_components']}; largest component: {c['largest_component_nodes']} nodes
- Scenario groups: {c['groups']}
- Scenario feature table: {dims['scenario_graph_features']['rows']} rows × {dims['scenario_graph_features']['columns']} columns
- Account feature table: {dims['account_graph_features']['rows']} rows × {dims['account_graph_features']['columns']} columns
- Group connectivity table: {dims['group_graph_features']['rows']} rows × {dims['group_graph_features']['columns']} columns

## Features and cutoff handling

The pipeline uses only `accounts.csv`, `transactions.csv`, and the prediction-time fields needed from `prediction_scenarios.csv`. Each edge must appear in that scenario's observed transaction IDs and satisfy event and availability time ≤ prediction cutoff. Edges retain transaction ID, scenario/group IDs, direction endpoints, type, amount, event time, availability time, and cutoff. No ground-truth outcome or generation manifest is read.

Features include directed in/out degree, event counts and amounts, unique counterparties, fan-in/fan-out, weak component size, and shortest directed hop distance from source nodes. Scenario summaries include component and source/sink/path-depth measures. Graph rows' scenario/group/account IDs are linkage and split metadata; exclude them from numeric model matrices. Source label-shaped columns are omitted entirely.

Complexity: {report['complexity']}

## Validation

- Status: `{v['status']}`
- Checks: {', '.join(v['checks'])}
- Errors: {v['errors'] or 'none'}

## Limitations

{chr(10).join('- '+x for x in report['limitations'])}

This milestone constructs and validates graph artifacts only. It does not train a model or demonstrate predictive performance. V2 label replay is a documented blind-evaluation risk; the preserved v2 graph is for pipeline development only.
"""


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--input-dir",type=Path,default=DEFAULT_INPUT); p.add_argument("--output-dir",type=Path,default=DEFAULT_OUTPUT); p.add_argument("--report",type=Path,default=DEFAULT_REPORT); p.add_argument("--summary",type=Path,default=DEFAULT_SUMMARY); a=p.parse_args(argv)
    report=build_graph(a.input_dir,a.output_dir,a.report,a.summary)
    print(f"Graph status: {report['status']}; nodes={report['counts']['nodes']}; edges={report['counts']['edges']}; scenarios={report['feature_dimensions']['scenario_graph_features']['rows']}; groups={report['counts']['groups']}")
    print(f"Validation errors: {report['validation']['errors']}")
    print(f"Summary: {a.summary.resolve()}")
    return 0 if report["status"]=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())
