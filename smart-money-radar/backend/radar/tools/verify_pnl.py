"""Independent check of PnL / ROI / Win Rate / Smart Money Score.

1. PnL per position, WITHOUT the event decoder: for every closed, fully verified position of a
   wallet whose history was read, sum the wallet's own SOL balance change over the position's
   transactions, from the stored raw transactions (pre/post balances of the wallet and of every
   token account it owns, so rent of a token account created and closed nets out; network fees,
   tips and trading fees are included because they leave the wallet). That is the SOL the wallet
   really made or lost. Compare with the system's realized PnL.
   Positions are skipped (not counted as matches) when a transaction is missing from the raw
   store, when a transaction also moves another token of the wallet (two positions in one tx),
   when the position has transfers in/out, or when its signature list was capped.
2. Win Rate / ROI / realized PnL recomputed from the stored positions vs wallet_performance.
3. Smart Money Score recomputed with score_wallet() from the stored performance row vs the
   stored score (formula unchanged; checks the stored numbers are reproducible).

Usage: python -m radar verify-pnl [--out report.json]
"""
from __future__ import annotations

import json
import statistics

from ..context import create_ctx


def wallet_delta_lamports(tx: dict, wallet: str) -> tuple[int, set[str]]:
    """Wallet SOL change incl. its token accounts; also the set of mints whose balance moved."""
    msg = tx["transaction"]["message"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg["accountKeys"]]
    meta = tx["meta"]
    pre, post = meta["preBalances"], meta["postBalances"]
    owned = {wallet}
    moved: dict[str, int] = {}
    for which in ("preTokenBalances", "postTokenBalances"):
        for b in meta.get(which) or []:
            if b.get("owner") == wallet:
                owned.add(keys[b["accountIndex"]])
                amt = int((b.get("uiTokenAmount") or {}).get("amount") or 0)
                moved[b["mint"]] = moved.get(b["mint"], 0) + (amt if which == "postTokenBalances" else -amt)
    d = sum(post[i] - pre[i] for i, k in enumerate(keys) if k in owned and i < len(pre) and i < len(post))
    return d, {m for m, v in moved.items() if v != 0}


async def run(out: str | None = None) -> dict:
    from ..analytics.performance import Performance
    from ..analytics.scoring import score_wallet
    ctx = await create_ctx("verify-pnl", pool_size=2)
    rep: dict = {}
    try:
        p = ctx.pool
        wsol = "So11111111111111111111111111111111111111112"
        pos = await p.fetch("""SELECT p.wallet, p.mint, p.cycle_no, p.realized_pnl_sol, p.buy_sol, p.signatures, p.flags, p.n_buys, p.n_sells
            FROM wallet_positions p JOIN wallets w ON w.address = p.wallet
            WHERE (w.backfill_status IN ('complete','partial') OR w.last_synced_at IS NOT NULL)
              AND p.status = 'closed' AND p.verification = 'verified'""")
        checked, skipped, diffs, rel = 0, {"missing_raw_tx": 0, "other_token_in_same_tx": 0, "transfers": 0, "capped_signatures": 0}, [], []
        worst = []
        for r in pos:
            if r["flags"] and any(f in ("transfer_in", "transfer_out", "opened_by_transfer") for f in r["flags"]):
                skipped["transfers"] += 1
                continue
            sigs = list(dict.fromkeys(r["signatures"] or []))
            if len(sigs) >= 200 or len(sigs) < r["n_buys"] + r["n_sells"] - 0:
                if len(sigs) >= 200:
                    skipped["capped_signatures"] += 1
                    continue
            raws = {x["signature"]: x["tx"] for x in await p.fetch(
                "SELECT signature, tx FROM raw_transactions WHERE signature = ANY($1::text[])", sigs)}
            if len(raws) < len(sigs):
                skipped["missing_raw_tx"] += 1
                continue
            total, bad = 0, False
            for s in sigs:
                d, mints = wallet_delta_lamports(raws[s], r["wallet"])
                if mints - {r["mint"], wsol}:
                    bad = True
                    break
                total += d
            if bad:
                skipped["other_token_in_same_tx"] += 1
                continue
            onchain = total / 1e9
            sys_pnl = float(r["realized_pnl_sol"])
            diff = sys_pnl - onchain
            checked += 1
            diffs.append(diff)
            rel.append(abs(diff) / max(float(r["buy_sol"]), 1e-9))
            worst.append((abs(diff), r["wallet"], r["mint"], sys_pnl, onchain))
        worst.sort(reverse=True)
        ad = sorted(abs(x) for x in diffs)
        rep["pnl_positions_checked"] = checked
        rep["pnl_positions_skipped"] = skipped
        if checked:
            rep["pnl_abs_diff_sol"] = {"median": round(statistics.median(ad), 6), "p90": round(ad[int(0.9 * (len(ad) - 1))], 6),
                                       "max": round(ad[-1], 6)}
            rep["pnl_within_0.001_sol"] = round(sum(1 for x in ad if x <= 0.001) / checked, 4)
            rep["pnl_within_0.01_sol"] = round(sum(1 for x in ad if x <= 0.01) / checked, 4)
            rep["pnl_within_1pct_of_entry"] = round(sum(1 for x in rel if x <= 0.01) / checked, 4)
            rep["pnl_sum_system_vs_onchain"] = [round(sum(diffs) + 0, 4), "system minus on-chain, SOL"]
            rep["pnl_worst"] = [{"wallet": w, "mint": m, "system_pnl": round(a, 6), "onchain_delta": round(b, 6)} for _, w, m, a, b in worst[:8]]

        # 2) WR / ROI / PnL from stored positions vs wallet_performance (30 d, qualifying)
        perf = await p.fetch("""SELECT wp.wallet, wp.win_rate, wp.roi, wp.realized_pnl_sol, wp.closed_qualifying,
                (SELECT count(*) FROM wallet_positions x WHERE x.wallet = wp.wallet AND x.qualifying AND x.status IN ('closed','written_off')
                   AND COALESCE(x.first_buy_at, x.opened_at) >= now() - interval '30 days') AS closed_q,
                (SELECT count(*) FROM wallet_positions x WHERE x.wallet = wp.wallet AND x.qualifying AND x.status IN ('closed','written_off')
                   AND COALESCE(x.first_buy_at, x.opened_at) >= now() - interval '30 days' AND x.realized_pnl_sol > 0) AS wins_q,
                (SELECT COALESCE(sum(realized_pnl_sol), 0) FROM wallet_positions x WHERE x.wallet = wp.wallet AND x.qualifying
                   AND x.status IN ('closed','written_off') AND COALESCE(x.first_buy_at, x.opened_at) >= now() - interval '30 days') AS pnl_q
            FROM wallet_performance wp JOIN wallets w ON w.address = wp.wallet
            WHERE wp.period_days = 30 AND (w.backfill_status IN ('complete','partial') OR w.last_synced_at IS NOT NULL)""")
        mism = []
        for r in perf:
            wr = (r["wins_q"] / r["closed_q"]) if r["closed_q"] else None
            ok = (r["closed_q"] == r["closed_qualifying"]
                  and ((wr is None and r["win_rate"] is None) or (wr is not None and r["win_rate"] is not None and abs(wr - float(r["win_rate"])) < 1e-4))  # win_rate stored with 5 decimals
                  and abs(float(r["pnl_q"]) - float(r["realized_pnl_sol"])) < 1e-6)
            if not ok:
                mism.append({"wallet": r["wallet"], "closed_stored": r["closed_qualifying"], "closed_recount": r["closed_q"],
                             "wr_stored": r["win_rate"], "wr_recount": wr, "pnl_stored": r["realized_pnl_sol"], "pnl_recount": r["pnl_q"]})
        rep["performance_rows_checked"] = len(perf)
        rep["performance_mismatches"] = len(mism)
        rep["performance_mismatch_examples"] = mism[:5]

        # 3) score reproducibility
        s = await ctx.settings.get()
        rows = await p.fetch("""SELECT sc.wallet, sc.score, sc.components, wp.* FROM smart_money_scores sc
            JOIN wallet_performance wp USING (wallet, period_days) JOIN wallets w ON w.address = sc.wallet
            WHERE sc.period_days = 30 AND (w.backfill_status IN ('complete','partial') OR w.last_synced_at IS NOT NULL)""")
        sd = []
        fields = set(Performance.__dataclass_fields__)
        for r in rows:
            perf_obj = Performance(period_days=30)
            for k in fields:
                if k in r.keys() and k != "period_days":
                    v = r[k]
                    setattr(perf_obj, k, float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, bool)) else v)
            perf_obj.metrics = dict(r["metrics"] or {})
            if perf_obj.last_active_at is not None and hasattr(perf_obj.last_active_at, "timestamp"):
                perf_obj.last_active_at = int(perf_obj.last_active_at.timestamp())
            cl = (r["components"] or {}).get("stability", {}).get("cluster_size") or 1
            res = score_wallet(perf_obj, s, cluster_size=int(cl), history_read=True)
            sd.append(abs(res.score - float(r["score"])))
        rep["scores_recomputed"] = len(sd)
        rep["score_abs_diff"] = {"max": round(max(sd), 4), "within_0.01": sum(1 for x in sd if x <= 0.01)} if sd else None
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1, default=str)
        return rep
    finally:
        await ctx.close()
