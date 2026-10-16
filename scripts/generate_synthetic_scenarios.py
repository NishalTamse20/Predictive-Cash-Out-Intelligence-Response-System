"""Generate versioned, fictional PCIRS scenarios (v2 by default).

The generator never reads PaySim. Outcomes are synthetic and are not evidence.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, math, random, uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "synthetic" / "v2"
BASE = datetime(2025, 1, 1, 8, tzinfo=timezone.utc)

@dataclass(frozen=True)
class Config:
    seed: int = 42
    scenarios: int = 100
    fraud_ratio: float = .5
    min_hops: int = 1
    max_hops: int = 4
    report_delay_min_hours: int = 1
    report_delay_max_hours: int = 24
    gap_min_minutes: int = 5
    gap_max_minutes: int = 60
    cashout_lead_min_hours: int = 1
    cashout_lead_max_hours: int = 24
    output_dir: Path | None = None
    version: str = "v2"
    grid_size: int = 5
    group_size: int = 4
    candidate_count: int = 12
    region_bias: float = .35

def _rng(seed: int, name: str) -> random.Random:
    material = hashlib.sha256(f"pcirs-v2:{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(material, "big"))

def _id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))

def _time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _json(x: object) -> str:
    return json.dumps(x, separators=(",", ":"), sort_keys=True)

def _write(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader(); w.writerows(rows)

def validate_config(c: Config) -> None:
    if c.scenarios < 4 or c.group_size < 2 or c.scenarios % c.group_size:
        raise ValueError("scenarios must be >=4 and divisible by group_size")
    if not 0 < c.fraud_ratio < 1: raise ValueError("fraud_ratio must be between 0 and 1")
    if not 1 <= c.min_hops <= c.max_hops <= 4: raise ValueError("hops must satisfy 1 <= min <= max <= 4")
    if not 3 <= c.grid_size <= 25: raise ValueError("grid_size must be from 3 to 25")
    if not 2 <= c.candidate_count <= c.grid_size**2: raise ValueError("candidate_count must be between 2 and grid_size squared")
    if not 0 <= c.region_bias < 1: raise ValueError("region_bias must be in [0,1)")
    if c.report_delay_min_hours < 1 or c.report_delay_max_hours < c.report_delay_min_hours: raise ValueError("invalid report delay range")
    if c.gap_min_minutes < 1 or c.gap_max_minutes < c.gap_min_minutes: raise ValueError("invalid event gap range")
    if c.cashout_lead_min_hours < 1 or c.cashout_lead_max_hours < c.cashout_lead_min_hours: raise ValueError("invalid cash-out lead range")

def generate_scenarios(config: Config) -> dict[str, int]:
    validate_config(config)
    out = Path(config.output_dir) if config.output_dir is not None else ROOT / "data" / "synthetic" / config.version
    out.mkdir(parents=True, exist_ok=True)
    if config.version not in {"v2", "v3"}:
        raise ValueError("version must be v2 or v3")
    labels_rng = _rng(config.seed, "labels") if config.version == "v2" else random.SystemRandom()
    behavior, geography, ids, ordering = (_rng(config.seed, n) for n in ("behavior", "geography", "identifiers", "row-order"))
    nloc = config.grid_size**2
    location_rows = []
    for z in range(nloc):
        row, col = divmod(z, config.grid_size)
        # Region is a fictional grid band, not a sourced administrative geography.
        location_rows.append({"location_id": _id(ids), "zone_id": z, "region_id": f"R{row // max(1, config.grid_size // 2)}", "location_kind": "synthetic_candidate", "latitude": f"{(row-(config.grid_size-1)/2)/111.32:.6f}", "longitude": f"{(col-(config.grid_size-1)/2)/111.32:.6f}", "geocoding_precision": "fictional_grid_cell", "source_name": "pcirs_synthetic_grid", "source_observed_at": "2024-12-01T00:00:00Z", "available_at": "2024-12-01T00:00:00Z", "record_origin": "synthetic"})
    loc_by_zone = {int(r["zone_id"]): r for r in location_rows}
    account_by_key: dict[str, dict] = {}
    txs=[]; complaints=[]; outcomes=[]; manifests=[]
    group_count = config.scenarios // config.group_size
    fraud_per_group = min(config.group_size-1, max(1, round(config.group_size*config.fraud_ratio)))
    scenario_specs=[]
    for g in range(group_count):
        labels=[True]*fraud_per_group+[False]*(config.group_size-fraud_per_group); labels_rng.shuffle(labels)
        scenario_specs.extend((g, label) for label in labels)
    # Shuffle scenario specs with an independent stream; IDs/row order do not encode labels.
    ordering.shuffle(scenario_specs)
    for i,(g,is_fraud) in enumerate(scenario_specs):
        sid,cid=_id(ids),_id(ids); group_id=f"GROUP-{g:04d}"
        start=BASE+timedelta(days=3*i); case_region=f"R{geography.randrange(max(1,(config.grid_size+1)//2))}"
        hub_key=f"hub-{g}"
        def acct(key: str, role: str) -> dict:
            if key not in account_by_key:
                account_by_key[key]={"account_id":_id(ids),"source_account_ref":_id(ids),"source_system":"pcirs_synthetic_generator","record_origin":"synthetic","synthetic_role":role,"scenario_group_id":group_id,"created_at":_time(start),"available_at":_time(start)}
            return account_by_key[key]
        hub=acct(hub_key,"group_intermediate")
        hops=behavior.randint(config.min_hops,config.max_hops)
        # V2 preserves its historical unused-account behavior so the published
        # seed replay remains auditable. V3 avoids cataloguing unused vertices.
        private_count = hops if config.version == "v3" else hops + 1
        unique=[acct(f"case-{i}-{j}","scenario_account") for j in range(private_count)]
        path=[unique[0],hub,*unique[1:]] if hops>1 else [unique[0],hub]
        # For one-hop cases path is source->hub. For longer cases, retain exactly hops edges.
        path=path[:hops+1]
        txids=[]; observed=set(); t=start
        fraud_types=("TRANSFER","TRANSFER","PAYMENT","CASH_OUT")
        routine_types=("PAYMENT","TRANSFER","PAYMENT","CASH_OUT")
        for j in range(hops):
            if j: t += timedelta(minutes=behavior.randint(config.gap_min_minutes,config.gap_max_minutes))
            typ=behavior.choice(fraud_types if is_fraud else routine_types)
            amount=behavior.triangular(10,10000,4000 if is_fraud else 3500)
            tid=_id(ids); txids.append(tid); observed.update((path[j]["account_id"],path[j+1]["account_id"]))
            txs.append({"transaction_id":tid,"source_system":"pcirs_synthetic_generator","source_record_ref":_id(ids),"scenario_id":sid,"scenario_group_id":group_id,"source_account_id":path[j]["account_id"],"destination_account_id":path[j+1]["account_id"],"transaction_type":typ,"amount":f"{amount:.2f}","currency":"","event_time":_time(t),"simulation_step":"","available_at":_time(t+timedelta(minutes=1)),"origin_old_balance":"","origin_new_balance":"","destination_old_balance":"","destination_new_balance":"","source_fraud_label":"","source_flagged_label":"","record_origin":"synthetic"})
        delay=behavior.randint(config.report_delay_min_hours,config.report_delay_max_hours); cutoff=t+timedelta(hours=delay)
        complaints.append({"complaint_id":cid,"case_id":"","scenario_id":sid,"scenario_group_id":group_id,"incident_start_at":_time(t),"reported_at":_time(cutoff),"report_delay_seconds":delay*3600,"available_at":_time(cutoff),"record_origin":"synthetic","status":"received","data_limitations":_json(["fictional scenario","no real complaint or account data"]),"created_at":_time(cutoff)})
        region_zones=[z for z,r in loc_by_zone.items() if r["region_id"]==case_region]
        target_zone=(geography.choice(region_zones) if geography.random()<config.region_bias else geography.randrange(nloc))
        candidate_zones={target_zone}
        while len(candidate_zones)<config.candidate_count: candidate_zones.add(geography.randrange(nloc))
        candidates=[loc_by_zone[z]["location_id"] for z in candidate_zones]; ordering.shuffle(candidates)
        if is_fraud:
            lead=behavior.randint(config.cashout_lead_min_hours,config.cashout_lead_max_hours); occurred=cutoff+timedelta(hours=lead); target=loc_by_zone[target_zone]["location_id"]
            truth={"cashout_event_id":_id(ids),"scenario_id":sid,"complaint_id":cid,"scenario_group_id":group_id,"scenario_label":"fraud","cashout_present":"true","location_id":target,"occurred_at":_time(occurred),"ground_truth_window_start":_time(occurred),"ground_truth_window_end":_time(occurred+timedelta(minutes=60)),"cashout_lead_hours":lead,"report_delay_hours":delay,"transaction_hops":hops,"label_status":"known","record_origin":"synthetic"}
        else:
            truth={"cashout_event_id":"","scenario_id":sid,"complaint_id":cid,"scenario_group_id":group_id,"scenario_label":"non_fraud","cashout_present":"false","location_id":"","occurred_at":"","ground_truth_window_start":"","ground_truth_window_end":"","cashout_lead_hours":"","report_delay_hours":delay,"transaction_hops":hops,"label_status":"no_event","record_origin":"synthetic"}
        outcomes.append(truth)
        manifests.append({"scenario_id":sid,"complaint_id":cid,"scenario_group_id":group_id,"synthetic_case_region_id":case_region,"prediction_cutoff_at":_time(cutoff),"observed_transaction_ids":_json(txids),"observed_account_ids":_json(sorted(observed)),"candidate_location_ids":_json(candidates),"observed_transaction_count":len(txids),"candidate_location_count":len(candidates),"record_origin":"synthetic","scenario_rule_version":f"pcirs-synthetic-{config.version}","configuration":_json({"fraud_ratio":config.fraud_ratio,"group_size":config.group_size,"regional_bias_probability":config.region_bias,"report_delay_hours":[config.report_delay_min_hours,config.report_delay_max_hours]})})
    account_rows=list(account_by_key.values())
    tables={"accounts.csv":(account_rows,["account_id","source_account_ref","source_system","record_origin","synthetic_role","scenario_group_id","created_at","available_at"]),"transactions.csv":(txs,["transaction_id","source_system","source_record_ref","scenario_id","scenario_group_id","source_account_id","destination_account_id","transaction_type","amount","currency","event_time","simulation_step","available_at","origin_old_balance","origin_new_balance","destination_old_balance","destination_new_balance","source_fraud_label","source_flagged_label","record_origin"]),"complaints.csv":(complaints,["complaint_id","case_id","scenario_id","scenario_group_id","incident_start_at","reported_at","report_delay_seconds","available_at","record_origin","status","data_limitations","created_at"]),"candidate_locations.csv":(location_rows,["location_id","zone_id","region_id","location_kind","latitude","longitude","geocoding_precision","source_name","source_observed_at","available_at","record_origin"]),"ground_truth_cashouts.csv":(outcomes,["cashout_event_id","scenario_id","complaint_id","scenario_group_id","scenario_label","cashout_present","location_id","occurred_at","ground_truth_window_start","ground_truth_window_end","cashout_lead_hours","report_delay_hours","transaction_hops","label_status","record_origin"]),"prediction_scenarios.csv":(manifests,["scenario_id","complaint_id","scenario_group_id","synthetic_case_region_id","prediction_cutoff_at","observed_transaction_ids","observed_account_ids","candidate_location_ids","observed_transaction_count","candidate_location_count","record_origin","scenario_rule_version","configuration"])}
    for rows,_ in tables.values(): ordering.shuffle(rows)
    for name,(rows,cols) in tables.items(): _write(out/name,rows,cols)
    manifest={"dataset_version":config.version,"seed":config.seed,"configuration":{k:(str(v) if isinstance(v,Path) else v) for k,v in config.__dict__.items() if k not in {"output_dir","version"}},"model_input_files":["accounts.csv","transactions.csv","complaints.csv","candidate_locations.csv","prediction_scenarios.csv"],"outcome_only_files":["ground_truth_cashouts.csv"],"warning":"Seed and generation configuration are metadata, not model inputs. V3 class assignment uses OS entropy and is not reproducible from the public seed; preserve this exact versioned artifact. Do not join outcome-only labels into predictors." if config.version == "v3" else "V2 labels are reproducible from the disclosed seed and generation procedure. Do not use v2 for blind predictive evaluation; keep the manifest and outcomes out of model features."}
    if config.version == "v3":
        manifest["label_assignment_method"] = "balanced within groups; shuffled with system entropy; entropy not stored"
    (out/"generation_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {"scenarios":len(manifests),"fraud_scenarios":sum(x["scenario_label"]=="fraud" for x in outcomes),"accounts":len(account_rows),"transactions":len(txs),"candidate_locations":len(location_rows),"groups":group_count}

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--version",choices=["v2","v3"],default="v2"); p.add_argument("--seed",type=int,default=42); p.add_argument("--scenarios",type=int,default=100); p.add_argument("--fraud-ratio",type=float,default=.5); p.add_argument("--output-dir",type=Path); p.add_argument("--group-size",type=int,default=4); p.add_argument("--candidate-count",type=int,default=12); p.add_argument("--region-bias",type=float,default=.35); p.add_argument("--grid-size",type=int,default=5); a=p.parse_args(argv); out=a.output_dir or ROOT/"data"/"synthetic"/a.version; return Config(seed=a.seed,scenarios=a.scenarios,fraud_ratio=a.fraud_ratio,output_dir=out,group_size=a.group_size,candidate_count=a.candidate_count,region_bias=a.region_bias,grid_size=a.grid_size,version=a.version)

if __name__=="__main__":
    c=parse_args(); print(generate_scenarios(c)); print(f"Generated fictional {c.version} data at {c.output_dir.resolve()}")
