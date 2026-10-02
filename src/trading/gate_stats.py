"""Gate block rate by reason (G8): of the unique tokens that reached the entry gate with a TRADE decision in this
sample epoch, how many were blocked for each reason, and how many passed at least once.

book.gate_seen: mint -> {"t": last evaluation ts, "first": first ts, "passed": bool, "reasons": [reason keys]}.
A token blocked first and passed later counts in both its reason(s) and "passed": rates are per reason, not a
partition."""
from __future__ import annotations

KEEP = 5000                          # newest tokens kept in paper_bot.json


def reason_key(why: str) -> str:
    """'entry_location: history 120s < 300s' -> 'history<300s'; 'entry_location: EXTENDED' -> 'EXTENDED'."""
    s = why.split(":", 1)[-1].strip()
    if s.startswith("history"):
        return "history<300s"
    if s.startswith("extension_5m"):
        return "extension_5m"
    if "still falling" in s:
        return s.split()[0] + " new low<180s"
    return s


def record(seen: dict, mint: str, why: list[str], now: float) -> None:
    e = seen.get(mint)
    if e is None:
        e = seen[mint] = {"first": now, "t": now, "passed": False, "reasons": []}
    e["t"] = now
    if not why:
        e["passed"] = True
    for k in (reason_key(w) for w in why):
        if k not in e["reasons"]:
            e["reasons"].append(k)


def prune(seen: dict) -> dict:
    if len(seen) <= KEEP:
        return dict(seen)
    return dict(sorted(seen.items(), key=lambda kv: kv[1]["t"])[-KEEP:])


def block_rates(seen: dict, since: float | None) -> dict:
    rows = [e for e in seen.values() if e["t"] >= (since or 0.0)]
    n = len(rows)
    by = {}
    for e in rows:
        for k in e["reasons"]:
            by[k] = by.get(k, 0) + 1
    rate = lambda c: round(c / n, 4) if n else None   # noqa: E731
    return {"tokens_at_gate": n, "passed_once": sum(1 for e in rows if e["passed"]),
            "never_passed": sum(1 for e in rows if not e["passed"]),
            "blocked_any": sum(1 for e in rows if e["reasons"]),
            "by_reason": {k: {"tokens": c, "rate": rate(c)} for k, c in sorted(by.items(), key=lambda kv: -kv[1])},
            "unit": "unique tokens with a TRADE decision that reached the entry gate in this epoch; a token can "
                    "count under several reasons"}
