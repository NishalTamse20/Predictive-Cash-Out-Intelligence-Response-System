"""Build one reproducible, explicitly synthetic PCIRS complaint scenario."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import random
from datetime import timedelta

import pandas as pd

from src.preprocessing.complaint_validation import SAMPLE_CASE_INPUT, validate_complaint_input

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAYSIM_PATH = PROJECT_ROOT / "data" / "raw" / "paysim.csv"
SEED = 20261010


@lru_cache(maxsize=4)
def load_paysim_transfer_patterns() -> tuple[dict, ...]:
    """Read a bounded sample of PaySim transfer patterns without changing the CSV."""
    if not PAYSIM_PATH.is_file():
        raise FileNotFoundError(f"Required PaySim file is missing: {PAYSIM_PATH}")
    sample = pd.read_csv(PAYSIM_PATH, usecols=["step", "type", "amount"], nrows=100_000)
    transfers = sample.loc[sample["type"].eq("TRANSFER"), ["step", "type", "amount"]]
    if transfers.empty:
        raise ValueError("The first 100,000 PaySim rows contain no TRANSFER examples to sample.")
    return tuple(
        {"step": int(row.step), "type": str(row.type), "amount": round(float(row.amount), 2)}
        for row in transfers.itertuples(index=False)
    )


def make_demo_scenario(seed: int = SEED, *, complaint_input: dict | None = None) -> dict:
    """Create a deterministic 3-hop trail whose amounts equal the complaint amount."""
    normalized = validate_complaint_input(complaint_input or SAMPLE_CASE_INPUT)
    effective_seed = seed ^ int(normalized["case_id"][-8:], 16)
    rng = random.Random(effective_seed)
    patterns = load_paysim_transfer_patterns()
    hop_count = 3
    incident = normalized["incident_time"]
    report_time = normalized["report_time"]
    delay = normalized["reporting_delay_minutes"]
    case_id = normalized["case_id"]
    account_ids = [normalized["starting_account_id"]] + [f"SYN-MULE-{case_id[-6:]}-{i:02d}" for i in range(1, hop_count + 1)]
    transfers = []
    initial_amount = normalized["reported_amount_inr"]
    event_minutes = sorted({max(1, round(delay * fraction)) for fraction in (0.2, 0.5, 0.8)})

    for index in range(hop_count):
        # PaySim supplies a transaction-type/step reference pattern only. The
        # user-reported principal is held unchanged at every generated hop.
        pattern = rng.choice(patterns)
        event_time = incident + timedelta(minutes=event_minutes[index])
        transfers.append({
            "transaction_id": f"SYN-TX-{case_id[-6:]}-{index + 1:03d}",
            "case_id": case_id,
            "step": index + 1,
            "sender_id": account_ids[index],
            "receiver_id": account_ids[index + 1],
            "amount_inr": initial_amount,
            "transaction_type": "TRANSFER",
            "event_time": event_time.isoformat(timespec="seconds"),
            "paysim_pattern_reference": {"type": pattern["type"], "step": pattern["step"]},
            "amount_rule": "reported principal held constant across all synthetic hops",
            "data_origin": "synthetic_chain_with_paysim_type_step_reference",
            "is_synthetic": True,
        })

    # The label has an independent seed and is attached only after prediction.
    truth_rng = random.Random(effective_seed ^ 0x5A17)
    truth_time = report_time + timedelta(minutes=45)
    scenario = {
        "schema_version": "interactive-demo-v1",
        "seed": effective_seed,
        "case": {
            "case_id": case_id,
            "incident_time": incident.isoformat(timespec="seconds"),
            "prediction_cutoff_at": report_time.isoformat(timespec="seconds"),
            "reported_loss_amount_inr": initial_amount,
            "reporting_delay_minutes": delay,
            "is_synthetic": True,
        },
        "complaint": {
            "complaint_id": f"SYN-COMP-{case_id[-6:]}",
            "case_id": case_id,
            "reported_at": report_time.isoformat(timespec="seconds"),
            "incident_time_estimate": incident.isoformat(timespec="seconds"),
            "reporting_delay_minutes": delay,
            "reported_loss_amount_inr": initial_amount,
            "victim_account_id": account_ids[0],
            "location_hint": {"latitude": 18.5204, "longitude": 73.8567, "origin": "synthetic_demo_coordinate"},
            "is_synthetic": True,
        },
        "transactions": transfers,
        "accounts": [
            {"account_id": account_id, "account_role": "victim" if i == 0 else "mule", "is_synthetic": True}
            for i, account_id in enumerate(account_ids)
        ],
        "synthetic_ground_truth": {
            "cashout_event_id": f"SYN-CO-{case_id[-6:]}",
            "case_id": case_id,
            "account_id": account_ids[-1],
            "cashout_time": truth_time.isoformat(timespec="seconds"),
            "amount_inr": initial_amount,
            "target_candidate_id": f"SYN-ATM-{truth_rng.randrange(1, 6):03d}",
            "ground_truth_origin": "synthetic_simulation",
            "is_synthetic_ground_truth": True,
        },
        "provenance": {
            "transaction_pattern_source": "data/raw/paysim.csv; first 100,000 rows; TRANSFER row type/step references",
            "reported_amount_rule": "user-entered reported amount is copied unchanged to each synthetic hop",
            "account_ids_and_chain": "synthetic",
            "complaint_and_reporting_delay": "synthetic",
            "cashout_time_and_location_label": "synthetic_simulation; not observed activity",
        },
    }
    return scenario


def save_demo_scenario(path: Path | None = None) -> Path:
    """Write reproducible demo JSON for inspection; never writes into raw data."""
    import json

    destination = path or PROJECT_ROOT / "data" / "generated" / "demo_case.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(make_demo_scenario(), indent=2), encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(save_demo_scenario())
