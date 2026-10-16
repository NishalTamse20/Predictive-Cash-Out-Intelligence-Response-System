"""Process-local case status for the local demonstration only."""

STATUSES = ("New", "Under Investigation", "Reviewed")
_CASE_STATUSES: dict[str, str] = {}


def get_case_status(case_id: str) -> str:
    return _CASE_STATUSES.get(case_id, "New")


def register_case(case_id: str) -> str:
    return _CASE_STATUSES.setdefault(case_id, "New")


def update_case_status(case_id: str, status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")
    if case_id not in _CASE_STATUSES:
        raise KeyError(f"Unknown local demo case: {case_id}")
    _CASE_STATUSES[case_id] = status
    return status


def get_case_status_counts() -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for status in _CASE_STATUSES.values():
        if status in counts: counts[status] += 1
    return counts
