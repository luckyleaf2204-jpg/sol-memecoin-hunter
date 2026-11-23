"""Recognise pump.fun -> PumpSwap migration (graduation) transactions, also when Solana
truncated their logs (they are large: 20 of 21 checked on 23/11/2026 had no migration event)."""
from __future__ import annotations

from .logs import records_from_logs

MIGRATE_LINES = ("Instruction: Migrate", "Instruction: MigrateV2")


def is_migration_log(logs: list[str]) -> bool:
    """The instruction name is printed near the top of the logs, which are never truncated there."""
    return any(l.endswith(MIGRATE_LINES) for l in logs or [])


WSOL = "So11111111111111111111111111111111111111112"


def graduation_from_tx(tx: dict) -> dict | None:
    """Migration transaction -> {mint, pool, migration_sig}. Migration transactions are large and
    Solana truncates their logs (20 of 21 checked on 23/11/2026), so the migration event is
    usually missing: the transaction is recognised by its 'Instruction: Migrate' / 'MigrateV2' log
    line (near the top, never truncated) and the mint is the non-WSOL token with the largest
    balance in the transaction (the curve's tokens moved into the pool; 32 of 33 checked).
    The pool comes from the event when present, else from GeckoTerminal later."""
    meta = tx.get("meta") or {}
    logs = meta.get("logMessages") or []
    if not is_migration_log(logs):
        return None
    sig = tx["transaction"]["signatures"][0]
    rec = records_from_logs(sig, tx.get("slot") or 0, logs)
    if rec["migrations"]:
        m = rec["migrations"][0]
        return {"mint": m.mint, "pool": m.pool, "migration_sig": sig}
    bal: dict[str, float] = {}
    for b in meta.get("postTokenBalances") or []:
        if b.get("mint") and b["mint"] != WSOL:
            bal[b["mint"]] = bal.get(b["mint"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
    if not bal:
        return None
    return {"mint": max(bal, key=bal.get), "pool": None, "migration_sig": sig}


