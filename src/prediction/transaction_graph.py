"""Small directed transaction-chain traversal for the local demo."""

from collections import defaultdict, deque


def find_trail(transactions: list[dict], start_account_id: str, max_hops: int = 4) -> list[dict]:
    """Return the earliest breadth-first trail from a start account (at most max_hops)."""
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for transaction in transactions:
        outgoing[transaction["sender_id"]].append(transaction)
    for edges in outgoing.values():
        edges.sort(key=lambda row: (row["event_time"], row["transaction_id"]))

    queue = deque([(start_account_id, [])])
    while queue:
        account_id, trail = queue.popleft()
        if trail and account_id not in outgoing:
            return trail
        if len(trail) >= max_hops:
            continue
        for transaction in outgoing.get(account_id, []):
            # Avoid cycles in a path; graph traversal is deterministic for a fixed input.
            visited = {start_account_id}
            visited.update(edge["receiver_id"] for edge in trail)
            receiver = transaction["receiver_id"]
            if receiver not in visited:
                queue.append((receiver, trail + [transaction]))
    if transactions and start_account_id not in outgoing:
        raise ValueError(f"No outgoing transaction found for account {start_account_id!r}.")
    return []


def validate_chain(trail: list[dict], start_account_id: str) -> None:
    """Raise ValueError if the trail is not a continuous 2–4-hop chain."""
    if not 2 <= len(trail) <= 4:
        raise ValueError(f"Expected a 2–4-hop trail; got {len(trail)} hop(s).")
    if trail[0]["sender_id"] != start_account_id:
        raise ValueError("Trail does not begin at the complaint's victim account.")
    for previous, current in zip(trail, trail[1:]):
        if previous["receiver_id"] != current["sender_id"]:
            raise ValueError("Transaction trail is discontinuous between adjacent hops.")
        if current["event_time"] <= previous["event_time"]:
            raise ValueError("Transaction times must increase strictly along the trail.")

