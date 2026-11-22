"""Wallet age and first funder for a handful of holders (top holders of tokens under analysis).

One getSignaturesForAddress(limit=1000): fewer than 1000 signatures means the whole history is
visible, so the oldest signature is the wallet's first activity (exact age). For such young
wallets the oldest transaction is fetched to find who funded it (first inbound SOL transfer).
Wallets with >= 1000 signatures are old/busy: only a lower bound of the age is known.
Results are cached in `wallet_age` for 24 h.
"""
from __future__ import annotations

import datetime as dt
import logging

from ..context import Ctx
from ..decoder.tx import decode_transaction

log = logging.getLogger(__name__)
UTC = dt.timezone.utc


async def probe_wallets(ctx: Ctx, wallets: list[str], max_new: int = 10) -> dict[str, dict]:
    if not wallets:
        return {}
    rows = {r["address"]: dict(r) for r in await ctx.pool.fetch(
        "SELECT * FROM wallet_age WHERE address = ANY($1::text[]) AND checked_at > now() - interval '24 hours'", wallets)}
    todo = [w for w in wallets if w not in rows][:max_new]
    for w in todo:
        try:
            sigs = await ctx.rpc.get_signatures_for_address(w, limit=1000)
        except Exception as e:
            log.debug("age probe %s: %s", w, e)
            continue
        complete = len(sigs) < 1000
        oldest = sigs[-1] if sigs else None
        first = dt.datetime.fromtimestamp(oldest["blockTime"], UTC) if oldest and oldest.get("blockTime") else None
        funder = fsig = None
        flam = None
        if complete and oldest:
            # the earliest successful tx usually is the funding transfer
            for cand in reversed(sigs[-5:]):
                if cand.get("err") is not None:
                    continue
                tx = await ctx.rpc.get_transaction(cand["signature"])
                if not tx:
                    continue
                d = decode_transaction(tx, cand["signature"])
                ins = [t for t in d.sol_transfers if t.destination == w and t.source != w and not t.under_swap]
                if ins:
                    best = max(ins, key=lambda t: t.lamports)
                    funder, fsig, flam = best.source, cand["signature"], best.lamports
                    break
        row = {"address": w, "first_activity_at": first, "signatures_seen": len(sigs), "history_complete": complete,
               "funder": funder, "funder_signature": fsig, "funder_lamports": flam}
        await ctx.pool.execute("""INSERT INTO wallet_age(address, first_activity_at, signatures_seen, history_complete,
                funder, funder_signature, funder_lamports, checked_at) VALUES ($1,$2,$3,$4,$5,$6,$7,now())
            ON CONFLICT (address) DO UPDATE SET first_activity_at = EXCLUDED.first_activity_at,
                signatures_seen = EXCLUDED.signatures_seen, history_complete = EXCLUDED.history_complete,
                funder = EXCLUDED.funder, funder_signature = EXCLUDED.funder_signature,
                funder_lamports = EXCLUDED.funder_lamports, checked_at = now()""",
                               w, first, len(sigs), complete, funder, fsig, flam)
        rows[w] = row
    return rows


def is_fresh(row: dict | None, hours: float) -> bool | None:
    """True = wallet's first ever activity is younger than `hours`; None = unknown."""
    if not row or row.get("first_activity_at") is None:
        return None
    if not row.get("history_complete"):
        return False
    age_h = (dt.datetime.now(UTC) - row["first_activity_at"]).total_seconds() / 3600
    return age_h < hours
