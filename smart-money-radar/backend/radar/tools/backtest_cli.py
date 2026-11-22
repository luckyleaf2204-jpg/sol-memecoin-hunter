"""Run the walk-forward backtest on stored on-chain data and save the result."""
from __future__ import annotations

import datetime as dt
import json

from ..analytics.backtest import BtTrade, run_backtest
from ..analytics.price_hits import bars_from_rows
from ..context import create_ctx

GRID = [{"signal_watch_min_wallets": w, "signal_max_runup": r}
        for w in (2, 3) for r in (1.5, 2.0, 3.0)]


async def run(out: str | None = None) -> dict:
    ctx = await create_ctx("backtest", run_migrations=True)
    try:
        s = await ctx.settings.get()
        rows = await ctx.pool.fetch("""SELECT t.* FROM wallet_transactions t JOIN wallets w ON w.address = t.wallet
            -- every wallet whose history was read, WHATEVER its current status: filtering on today's
            -- status (decided with later data) would be survivorship / look-ahead bias
            WHERE w.backfill_status IN ('complete','partial') AND NOT w.is_excluded
              AND t.tx_type IN ('buy','sell','transfer_out') ORDER BY t.block_time""")
        trades = [BtTrade(r["wallet"], r["mint"], int(r["block_time"].timestamp()), int(r["slot"]), r["tx_type"],
                          float(r["sol_amount"]), float(r["token_amount"]),
                          float(r["fee_trading_sol"]) + float(r["fee_network_sol"]) + float(r["tip_sol"])
                          + float(r["other_cost_sol"]),
                          float(r["effective_price_sol"]) if r["effective_price_sol"] else None,
                          float(r["liquidity_sol"]) if r["liquidity_sol"] is not None else None,
                          r["verification_status"], r["signature"], r["event_key"], r["venue"] or "pump_curve")
                  for r in rows]
        mints = sorted({t.mint for t in trades})
        bars = {}
        for m in mints:
            br = await ctx.pool.fetch("""SELECT * FROM token_prices WHERE mint = $1 AND source IN
                ('onchain_trades','geckoterminal') ORDER BY bucket_start""", m)
            seen, merged = set(), []
            for r in br:  # on-chain first
                if r["source"] == "onchain_trades":
                    merged.append(dict(r))
                    seen.add(r["bucket_start"])
            merged += [dict(r) for r in br if r["source"] == "geckoterminal" and r["bucket_start"] not in seen]
            bars[m] = bars_from_rows(merged)
        res = run_backtest(trades, bars, s, grid=GRID)
        meta = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "wallets": len({t.wallet for t in trades}),
                "trades": len(trades), "tokens": len(mints), "tokens_with_bars": sum(1 for m in mints if bars[m]),
                "period": [trades[0].ts if trades else None, trades[-1].ts if trades else None],
                "settings": {k: s[k] for k in s if k.startswith(("signal_", "backtest_", "peak_"))}}
        await ctx.pool.execute("INSERT INTO backtest_runs(params, results) VALUES ($1, $2)",
                               {"grid": GRID, "meta": meta},
                               {"selected_params": res["selected_params"], "selected": res["selected"],
                                "grid": res["grid"], "rows": res["rows"][:2000]})
        report = {"meta": meta, **res}
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1, default=str)
        print(json.dumps({"meta": meta, "selected_params": res["selected_params"],
                          "test": (res["selected"] or {}).get("test")}, indent=1, default=str))
        return report
    finally:
        await ctx.close()
