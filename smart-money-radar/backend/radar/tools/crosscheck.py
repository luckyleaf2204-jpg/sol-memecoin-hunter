"""Independent cross-check of decoded trades.

1. Independent indexer: GeckoTerminal `/pools/{pool}/trades` (last 24 h, 300 trades/pool) is
   matched by transaction signature; token amounts and SOL amounts are compared with ours.
   GeckoTerminal's SOL figure for a curve trade may include or exclude pump fees, so the SOL
   difference is reported next to our trading fee to explain it.
2. Data integrity across providers: the same transaction is fetched from a second RPC
   provider and the wallet's balance fields are compared byte for byte.
3. Every row has a Solscan link so a person can check it by hand.

    python -m radar crosscheck --n 25 --out docs/reports/crosscheck.md
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from ..context import create_ctx
from ..sources.geckoterminal import BASE, HEADERS


async def run(n: int = 25, out: str | None = None) -> dict:
    ctx = await create_ctx("crosscheck", run_migrations=True)
    try:
        rows = await ctx.pool.fetch("""SELECT t.*, k.bonding_curve, k.amm_pool FROM wallet_transactions t
            JOIN tokens k ON k.mint = t.mint
            WHERE t.source = 'rpc_tx' AND t.tx_type IN ('buy','sell') AND t.block_time > now() - interval '20 hours'
            ORDER BY (t.sol_amount >= 2) DESC, t.block_time DESC LIMIT 400""")
        by_pool: dict[str, list] = {}
        for r in rows:
            pool = r["bonding_curve"] if r["venue"] == "pump_curve" else r["amm_pool"]
            if pool:
                by_pool.setdefault(pool, []).append(dict(r))
        results: list[dict[str, Any]] = []
        for pool, trs in sorted(by_pool.items(), key=lambda x: -len(x[1])):
            if len(results) >= n:
                break
            try:
                data = await ctx.http.request_json("geckoterminal", "GET", f"{BASE}/pools/{pool}/trades",
                                                   headers=HEADERS)
            except Exception as e:
                results.append({"pool": pool, "error": str(e)[:120]})
                continue
            gt = {}
            for d in (data or {}).get("data") or []:
                a = d.get("attributes") or {}
                gt.setdefault(a.get("tx_hash"), []).append(a)
            for r in trs:
                if len(results) >= n:
                    break
                g = gt.get(r["signature"])
                if not g:
                    continue
                a = g[0]
                ours_tok = float(r["token_amount"])
                ours_sol = float(r["sol_amount"])
                fee = float(r["fee_trading_sol"])
                if a.get("kind") == "buy":
                    gt_sol, gt_tok = float(a["from_token_amount"]), float(a["to_token_amount"])
                else:
                    gt_sol, gt_tok = float(a["to_token_amount"]), float(a["from_token_amount"])
                results.append({
                    "signature": r["signature"], "solscan": f"https://solscan.io/tx/{r['signature']}",
                    "wallet": r["wallet"], "mint": r["mint"], "venue": r["venue"], "side": r["tx_type"],
                    "gt_side": a.get("kind"), "ours_sol": ours_sol, "gt_sol": gt_sol, "sol_diff": gt_sol - ours_sol,
                    "trading_fee": fee, "ours_tokens": ours_tok, "gt_tokens": gt_tok,
                    "token_diff_rel": (gt_tok - ours_tok) / ours_tok if ours_tok else None,
                    "gt_trader": a.get("tx_from_address"), "verification": r["verification_status"],
                    "residual_sol": float(r["reconciliation_residual_sol"]) if r["reconciliation_residual_sol"] is not None else None})
        # provider integrity on the matched signatures
        alt = [e for e in ctx.rpc.endpoints if e.name != ctx.rpc.endpoints[0].name]
        for res in results:
            if "signature" not in res or not alt:
                continue
            try:
                raw = await ctx.pool.fetchval("SELECT tx FROM raw_transactions WHERE signature = $1", res["signature"])
                other = await ctx.rpc._call_one(alt[-1], "getTransaction", [res["signature"], {
                    "encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
                if raw and other:
                    res["providers_identical"] = (raw["meta"]["preBalances"] == other["meta"]["preBalances"]
                                                  and raw["meta"]["postBalances"] == other["meta"]["postBalances"]
                                                  and raw["meta"]["postTokenBalances"] == other["meta"]["postTokenBalances"])
                    res["second_provider"] = alt[-1].name
            except Exception as e:
                res["providers_identical"] = None
                res["provider_error"] = type(e).__name__
        matched = [r for r in results if "signature" in r]
        summary = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "matched": len(matched),
            "side_agrees": sum(1 for r in matched if r["side"] == r["gt_side"]),
            "token_amount_exact": sum(1 for r in matched if r["token_diff_rel"] is not None and abs(r["token_diff_rel"]) < 1e-6),
            "sol_within_fee": sum(1 for r in matched if abs(r["sol_diff"]) <= r["trading_fee"] + 1e-6),
            "trader_agrees": sum(1 for r in matched if r["gt_trader"] in (r["wallet"],)),
            "providers_identical": sum(1 for r in matched if r.get("providers_identical")),
        }
        report = {"summary": summary, "rows": results}
        if out:
            with open(out, "w", encoding="utf-8") as f:
                if out.endswith(".json"):
                    json.dump(report, f, indent=1, default=str)
                else:
                    f.write(_markdown(report))
        print(json.dumps(summary, indent=1))
        return report
    finally:
        await ctx.close()


def _markdown(rep: dict) -> str:
    s = rep["summary"]
    lines = ["# Cross-check giao dịch on-chain", "",
             f"Tạo lúc: {s['generated_at']}", "",
             "Nguồn đối chiếu độc lập: GeckoTerminal `/pools/{pool}/trades` (khớp theo chữ ký giao dịch) "
             "và một nhà cung cấp RPC thứ hai (so sánh số dư trước/sau).", "",
             f"- Giao dịch khớp được: **{s['matched']}**",
             f"- Chiều mua/bán trùng khớp: {s['side_agrees']}/{s['matched']}",
             f"- Số lượng token trùng khớp tuyệt đối: {s['token_amount_exact']}/{s['matched']}",
             f"- Chênh lệch SOL nằm trong phạm vi phí giao dịch: {s['sol_within_fee']}/{s['matched']}",
             f"- Ví giao dịch trùng khớp: {s['trader_agrees']}/{s['matched']}",
             f"- Dữ liệu thô giống hệt giữa 2 RPC: {s['providers_identical']}/{s['matched']}", "",
             "| # | Giao dịch | Chiều | SOL (hệ thống) | SOL (GeckoTerminal) | Chênh lệch | Phí giao dịch | Token khớp | Xác minh |",
             "|---|---|---|---:|---:|---:|---:|---|---|"]
    for i, r in enumerate([r for r in rep["rows"] if "signature" in r], 1):
        lines.append(f"| {i} | [{r['signature'][:12]}…]({r['solscan']}) | {r['side']} | {r['ours_sol']:.6f} | "
                     f"{r['gt_sol']:.6f} | {r['sol_diff']:+.6f} | {r['trading_fee']:.6f} | "
                     f"{'✓' if r['token_diff_rel'] is not None and abs(r['token_diff_rel']) < 1e-6 else '✗'} | "
                     f"{r['verification']} |")
    return "\n".join(lines) + "\n"
