"""Backfill audit: re-count each 'complete'/'partial' wallet's signatures inside the backfill window
on a full-history endpoint and compare with what the backfill saw. A wallet whose history was
listed by an endpoint that drops old signatures (publicnode, measured 22/11/2026) is re-queued;
the resumed backfill re-uses stored transactions (checkpoint), so nothing is fetched twice.

Also re-queues wallets skipped as "high frequency" whose own pump.fun trade rate is low
(signature count inflated by spam airdrops, not by trading).

Usage: python -m radar backfill-audit [--apply]
"""
from __future__ import annotations

import json
import time

from ..context import create_ctx


async def run(apply: bool = False, out: str | None = None) -> dict:
    ctx = await create_ctx("backfill-audit", run_migrations=True, pool_size=2)
    report: dict = {"checked": 0, "short": [], "requeued_flooded": [], "errors": 0}
    try:
        s = await ctx.settings.get()
        cutoff = time.time() - int(s["backfill_days"]) * 86400
        max_sigs = int(s["backfill_max_signatures"])
        rows = await ctx.pool.fetch("""SELECT address, backfill_status, backfill_sigs_seen, backfill_truncated
            FROM wallets WHERE backfill_status IN ('complete','partial') ORDER BY address""")
        for r in rows:
            try:
                n, before = 0, None
                while n < max_sigs:
                    page = await ctx.rpc.get_signatures_for_address(r["address"], before=before, limit=1000)
                    inside = [x for x in page if (x.get("blockTime") or time.time()) >= cutoff]
                    n += len(inside)
                    if len(inside) < len(page) or len(page) < 1000:
                        break
                    before = page[-1]["signature"]
                n = min(n, max_sigs)
            except Exception:
                report["errors"] += 1
                continue
            report["checked"] += 1
            seen = int(r["backfill_sigs_seen"] or 0)
            # sync runs add new signatures to backfill_sigs_seen, so only a shortfall matters
            if n > seen + 5:
                report["short"].append({"wallet": r["address"], "status": r["backfill_status"],
                                        "signatures_recorded": seen, "signatures_on_chain": n})
        flooded = await ctx.pool.fetch("""SELECT w.address FROM wallets w
            WHERE w.status = 'observed' AND w.backfill_status = 'none'
              AND (w.discovery_detail->>'high_frequency')::boolean
              AND (SELECT count(*) / GREATEST(extract(epoch FROM max(block_time) - min(block_time)) / 86400, 1)
                   FROM wallet_transactions t WHERE t.wallet = w.address AND t.tx_type IN ('buy','sell')) < 50
              AND EXISTS (SELECT 1 FROM wallet_transactions t WHERE t.wallet = w.address)""")
        report["requeued_flooded"] = [r["address"] for r in flooded]
        if apply:
            short = [x["wallet"] for x in report["short"]]
            if short:
                await ctx.pool.execute("""UPDATE wallets SET backfill_status = 'queued', updated_at = now(),
                    discovery_detail = discovery_detail || '{"backfill_audit": "history was incomplete"}'::jsonb
                    WHERE address = ANY($1::text[])""", short)
            if report["requeued_flooded"]:
                await ctx.pool.execute("""UPDATE wallets SET status = 'candidate', backfill_status = 'queued', updated_at = now(),
                    discovery_detail = discovery_detail || '{"backfill_audit": "spam-flooded, not a bot"}'::jsonb
                    WHERE address = ANY($1::text[])""", report["requeued_flooded"])
        report["applied"] = apply
        report["short_count"] = len(report["short"])
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1)
        return report
    finally:
        await ctx.close()
