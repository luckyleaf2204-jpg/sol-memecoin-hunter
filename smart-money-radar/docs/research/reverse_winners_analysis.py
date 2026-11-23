"""Token-first reverse analysis on stored stream trades (no RPC calls)."""
import asyncio, json, statistics
from collections import defaultdict
from radar.context import create_ctx

async def main():
    ctx = await create_ctx("research", pool_size=2); p = ctx.pool
    rows = await p.fetch("""SELECT t.wallet, t.mint, t.signature, t.slot, extract(epoch FROM t.block_time)::bigint bt, t.tx_type,
            t.sol_amount::float sol, t.token_amount::float tok, COALESCE(t.fee_trading_sol,0)::float fee, t.market_cap_sol::float mc,
            t.user_is_signer, t.venue, k.created_slot, extract(epoch FROM k.created_at)::bigint created, k.creator
        FROM wallet_transactions t LEFT JOIN tokens k ON k.mint = t.mint
        WHERE t.tx_type IN ('buy','sell') AND t.block_time > now() - interval '30 hours'""")
    print("rows", len(rows))
    # --- token peaks (from trades seen) and winners
    tok = defaultdict(lambda: {"first_mc": None, "first_t": None, "peak_mc": 0.0, "peak_t": None, "last_mc": None, "created": None, "creator": None, "cslot": None})
    for r in sorted(rows, key=lambda r: r["bt"]):
        if r["mc"] is None: continue
        k = tok[r["mint"]]
        if k["first_mc"] is None: k["first_mc"], k["first_t"] = r["mc"], r["bt"]
        if r["mc"] > k["peak_mc"]: k["peak_mc"], k["peak_t"] = r["mc"], r["bt"]
        k["last_mc"] = r["mc"]; k["created"] = r["created"]; k["creator"] = r["creator"]; k["cslot"] = r["created_slot"]
    winners = {m for m, k in tok.items() if k["created"] and k["first_t"] and k["first_t"] - k["created"] < 600
               and k["first_mc"] and k["peak_mc"] >= 400 and k["peak_mc"] / k["first_mc"] >= 5}
    print("tokens with mc", len(tok), "winners (seen from <10 min after creation, peak >= 400 SOL MC, >= 5x)", len(winners))
    # --- per wallet per mint positions from stream rows (token-balanced only)
    by = defaultdict(list)
    for r in rows: by[(r["wallet"], r["mint"])].append(r)
    wallet = defaultdict(lambda: {"pos": [], "trades": 0, "first": None, "last": None, "sniper": 0, "bundle": 0, "creator": 0})
    slot_buyers = defaultdict(set)
    for r in rows:
        if r["tx_type"] == "buy": slot_buyers[(r["mint"], r["slot"])].add(r["wallet"])
    for (w, m), rs in by.items():
        rs.sort(key=lambda r: (r["bt"], r["slot"]))
        W = wallet[w]; W["trades"] += len(rs)
        W["first"] = min(W["first"] or rs[0]["bt"], rs[0]["bt"]); W["last"] = max(W["last"] or 0, rs[-1]["bt"])
        buys = [r for r in rs if r["tx_type"] == "buy"]; sells = [r for r in rs if r["tx_type"] == "sell"]
        if tok[m]["creator"] == w: W["creator"] += 1
        if buys and buys[0]["created_slot"] and buys[0]["slot"] - buys[0]["created_slot"] <= 2: W["sniper"] += 1
        if buys and len(slot_buyers[(m, buys[0]["slot"])]) >= 3 and buys[0]["created_slot"] and buys[0]["slot"] - buys[0]["created_slot"] <= 5: W["bundle"] += 1
        bt_tok = sum(r["tok"] for r in buys); st_tok = sum(r["tok"] for r in sells)
        if not buys or bt_tok <= 0: continue
        closed = st_tok >= 0.9 * bt_tok and st_tok <= 1.1 * bt_tok
        cost = sum(r["sol"] for r in buys); proceeds = sum(r["sol"] for r in sells); fees = sum(r["fee"] for r in rs)
        entry_mc = buys[0]["mc"]; k = tok[m]
        W["pos"].append({"mint": m, "closed": closed, "cost": cost, "pnl": proceeds - cost - fees if closed else None,
                         "hold": (sells[-1]["bt"] - buys[0]["bt"]) if sells else None, "entry_mc": entry_mc,
                         "peak_mc": k["peak_mc"], "winner": m in winners, "age_at_entry": (buys[0]["bt"] - k["created"]) if k["created"] else None,
                         "buy_sig": buys[0]["signature"], "sell_sig": sells[-1]["signature"] if sells else None})
    # --- candidate filter
    out = []
    funnel = defaultdict(int)
    for w, W in wallet.items():
        funnel["all"] += 1
        span_d = max((W["last"] - W["first"]) / 86400, 1 / 24)
        closed = [x for x in W["pos"] if x["closed"] and x["cost"] >= 0.5]
        if len(closed) < 6: continue
        funnel[">=6 closed positions >=0.5 SOL (token-balanced)"] += 1
        tpd = W["trades"] / span_d
        holds = [x["hold"] for x in closed if x["hold"] is not None]
        mhold = statistics.median(holds) if holds else 0
        if tpd > 150 or mhold < 30: continue
        funnel["+ not bot-like (<150 trades/day seen, median hold >=30s)"] += 1
        if W["sniper"] / max(len(W["pos"]), 1) > 0.3 or W["bundle"] / max(len(W["pos"]), 1) > 0.3 or W["creator"]: continue
        funnel["+ not sniper/bundler/creator (<=30% entries in first 2-5 slots, never creator)"] += 1
        pnls = [x["pnl"] for x in closed]; wins = [x for x in pnls if x > 0]
        tot = sum(pnls); gw = sum(wins); gl = -sum(x for x in pnls if x < 0)
        wr = len(wins) / len(pnls); pf = gw / gl if gl > 0 else 99
        top = max(pnls)
        if tot <= 0 or wr < 0.5 or pf < 1.5: continue
        funnel["+ profitable after pump fees: PnL>0, WR>=50%, PF>=1.5"] += 1
        if top > 0.6 * tot: continue
        funnel["+ not single-win dependent (best trade <=60% of PnL)"] += 1
        early_win = [x for x in closed if x["winner"] and x["entry_mc"] and x["peak_mc"] and x["entry_mc"] <= 0.3 * x["peak_mc"] and x["pnl"] > 0]
        out.append({"wallet": w, "closed": len(closed), "pnl": round(tot, 3), "wr": round(wr, 2), "pf": round(pf, 2), "trades_per_day": round(tpd, 1),
                    "median_hold_s": int(mhold), "median_buy_sol": round(statistics.median(x["cost"] for x in closed), 2),
                    "early_profitable_on_winners": len(early_win), "best_share": round(top / tot, 2),
                    "evidence": [{"mint": x["mint"], "cost": round(x["cost"], 3), "pnl": round(x["pnl"], 3), "entry_mc": round(x["entry_mc"] or 0, 1),
                                  "peak_mc": round(x["peak_mc"], 1), "buy": x["buy_sig"], "sell": x["sell_sig"]} for x in sorted(closed, key=lambda x: -x["pnl"])[:3]]})
    for k, v in funnel.items(): print("FUNNEL", k, v)
    out.sort(key=lambda o: (-o["early_profitable_on_winners"], -o["pnl"]))
    known = {r["address"]: (r["status"], r["discovery_source"], r["backfill_status"]) for r in await p.fetch("SELECT address, status, discovery_source, backfill_status FROM wallets WHERE address = ANY($1::text[])", [o["wallet"] for o in out])}
    for o in out: o["in_db"] = known.get(o["wallet"])
    print("CANDIDATES", len(out), "with >=1 early profitable winner:", sum(1 for o in out if o["early_profitable_on_winners"] >= 1), ">=2:", sum(1 for o in out if o["early_profitable_on_winners"] >= 2))
    json.dump({"funnel": funnel, "candidates": out, "winners": len(winners)}, open("/app/reverse_out.json", "w"), default=str)
    await ctx.close()
asyncio.run(main())
