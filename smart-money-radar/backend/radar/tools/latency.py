"""Pipeline latency report, measured on the system's own data (no synthetic traffic).

  stream      block time -> row stored from program logs          (wallet_transactions rpc_logs)
  verifier    block time -> full transaction verified              (verified_at)
  kol alert   block time of the KOL buy -> alert row created        (alerts kol_buy)
  discovery   first stored trade of a wallet -> wallet became candidate (wallets.first_seen_at);
              by design a wallet needs >= 2 tokens bought >= min_buy before it qualifies, so this is
              mostly waiting for the wallet's own second trade, not processing delay
  wallet sync age of the last incremental sync of tracked wallets  (last_synced_at)
  backfill    wallets finished per hour in the window               (backfill_to)
  prices      age of the newest on-chain price bar, SOL/USD         (token_prices, api_sync_status)
  rpc         per-endpoint request count, errors, 429s, mean latency (api_sync_status rpc_*)

The stream endpoint name (e.g. "helius" or "solana_public") is printed so a report says which
provider was measured. Usage: python -m radar latency [--minutes 30] [--out file.json]
"""
from __future__ import annotations

import json

from ..context import create_ctx


def _q(xs: list[float]) -> dict:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    pick = lambda p: round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 2)  # noqa: E731
    return {"n": len(xs), "p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99), "max": round(xs[-1], 2)}


async def run(minutes: int = 30, out: str | None = None) -> dict:
    ctx = await create_ctx("latency", pool_size=2)
    try:
        p = ctx.pool
        win = f"{int(minutes)} minutes"
        r: dict = {"window_minutes": minutes}
        ws = await p.fetchrow("SELECT status, details, updated_at FROM api_sync_status WHERE source = 'solana_ws'")
        r["stream_endpoint"] = (ws["details"] or {}).get("endpoint") if ws else None
        r["stream_status"] = ws["status"] if ws else None
        r["stream_last_message_age_s"] = (ws["details"] or {}).get("last_message_age_s") if ws else None
        rows = await p.fetch(f"""SELECT extract(epoch FROM ingested_at - block_time) AS d FROM wallet_transactions
            WHERE source = 'rpc_logs' AND ingested_at > now() - interval '{win}'""")
        r["stream_delay_s"] = _q([float(x["d"]) for x in rows])
        # live trades only (block time inside the window); backfill re-verifies old trades and is
        # reported separately as throughput
        rows = await p.fetch(f"""SELECT extract(epoch FROM verified_at - block_time) AS d FROM wallet_transactions
            WHERE verified_at > now() - interval '{win}' AND block_time > now() - interval '{win}'
              AND verification_status = 'verified'""")
        r["verifier_delay_s"] = _q([float(x["d"]) for x in rows])
        r["verified_in_window_total"] = await p.fetchval(
            f"SELECT count(*) FROM wallet_transactions WHERE verified_at > now() - interval '{win}'")
        rows = await p.fetch(f"""SELECT extract(epoch FROM a.created_at - t.bt) AS d FROM alerts a
            JOIN LATERAL (SELECT min(block_time) AS bt FROM wallet_transactions w
                          WHERE w.wallet = split_part(a.dedupe_key, ':', 2) AND w.mint = split_part(a.dedupe_key, ':', 3)
                            AND w.tx_type = 'buy' AND w.block_time <= a.created_at) t ON true
            WHERE a.reason = 'kol_buy' AND a.created_at > now() - interval '{win}'""")
        r["kol_alert_delay_s"] = _q([float(x["d"]) for x in rows if x["d"] is not None])
        rows = await p.fetch(f"""SELECT extract(epoch FROM w.first_seen_at - t.first_ingest) AS d FROM wallets w
            JOIN LATERAL (SELECT min(ingested_at) AS first_ingest FROM wallet_transactions x WHERE x.wallet = w.address) t ON true
            WHERE w.first_seen_at > now() - interval '{win}' AND w.status IN ('candidate','tracked','smart')""")
        r["discovery_delay_s"] = _q([float(x["d"]) for x in rows if x["d"] is not None and x["d"] >= 0])
        rows = await p.fetch("""SELECT extract(epoch FROM now() - last_synced_at) AS d FROM wallets
            WHERE status IN ('tracked','smart') AND backfill_status IN ('complete','partial') AND last_synced_at IS NOT NULL""")
        r["wallet_sync_age_s"] = _q([float(x["d"]) for x in rows])
        r["backfill_finished_in_window"] = await p.fetchval(
            f"SELECT count(*) FROM wallets WHERE backfill_to > now() - interval '{win}' AND backfill_status IN ('complete','partial')")
        r["backfill_queue"] = await p.fetchval("SELECT count(*) FROM wallets WHERE backfill_status IN ('queued','running')")
        bar = await p.fetchval("SELECT extract(epoch FROM now() - max(bucket_start)) FROM token_prices WHERE source = 'onchain_trades'")
        r["price_newest_onchain_bar_age_s"] = round(float(bar), 1) if bar is not None else None
        jup = await p.fetchval("SELECT extract(epoch FROM now() - updated_at) FROM api_sync_status WHERE source = 'jupiter'")
        r["sol_usd_age_s"] = round(float(jup), 1) if jup is not None else None
        r["rpc"] = [{"source": x["source"], "ok": x["requests_ok"], "failed": x["requests_failed"],
                     "rate_limited": x["rate_limited"], "mean_latency_ms": round(float((x["details"] or {}).get("latency_ms") or 0), 1)}
                    for x in await p.fetch("SELECT * FROM api_sync_status WHERE source LIKE 'rpc_%' ORDER BY source")]
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(r, f, indent=1, default=str)
        return r
    finally:
        await ctx.close()
