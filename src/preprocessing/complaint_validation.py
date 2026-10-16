"""Input validation and normalization for a local PCIRS complaint demo."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo

INDIA_TZ = ZoneInfo("Asia/Kolkata")
SAMPLE_CASE_INPUT = {
    "reported_amount_inr": 50000.00,
    "report_time": "2026-10-10T08:25",
    "starting_account_id": "SYN-VICTIM-001",
    "reporting_delay_minutes": 25,
}
_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def validate_complaint_input(payload: dict) -> dict:
    """Validate fields and return a canonical, timezone-aware demo case input."""
    required = ("reported_amount_inr", "report_time", "starting_account_id", "reporting_delay_minutes")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}.")

    try:
        amount = Decimal(str(payload["reported_amount_inr"]))
    except (InvalidOperation, ValueError):
        raise ValueError("Reported amount must be a valid number in INR.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("100000000"):
        raise ValueError("Reported amount must be greater than ₹0 and no more than ₹10 crore.")
    try:
        rounded_amount = amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("Reported amount can have at most two decimal places.") from None
    if amount != rounded_amount:
        raise ValueError("Reported amount can have at most two decimal places.")

    account_id = str(payload["starting_account_id"]).strip()
    if not _ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("Starting account must be 1–40 letters, digits, dots, underscores, or hyphens. Use a fictional ID.")

    try:
        report_time = datetime.fromisoformat(str(payload["report_time"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("Complaint/report time must be a valid date and time.") from None
    if report_time.tzinfo is None:
        report_time = report_time.replace(tzinfo=INDIA_TZ)
    else:
        report_time = report_time.astimezone(INDIA_TZ)

    delay_value = payload["reporting_delay_minutes"]
    if isinstance(delay_value, bool):
        raise ValueError("Reporting delay must be a whole number of minutes.")
    try:
        delay = int(delay_value)
    except (TypeError, ValueError):
        raise ValueError("Reporting delay must be a whole number of minutes.") from None
    if str(delay_value).strip() != str(delay):
        raise ValueError("Reporting delay must be a whole number of minutes.")
    if not 10 <= delay <= 10080:
        raise ValueError("Reporting delay must be between 10 minutes and 7 days so the demo trail precedes the report.")

    amount_value = float(amount)
    canonical_time = report_time.isoformat(timespec="seconds")
    identity = json.dumps(
        {"amount": format(amount, ".2f"), "report_time": canonical_time, "account": account_id, "delay": delay},
        sort_keys=True,
        separators=(",", ":"),
    )
    case_id = "PCIRS-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12].upper()
    return {
        "reported_amount_inr": amount_value,
        "report_time": report_time,
        "report_time_iso": canonical_time,
        "starting_account_id": account_id,
        "reporting_delay_minutes": delay,
        "incident_time": report_time - timedelta(minutes=delay),
        "case_id": case_id,
    }
