"""Deep copy-trading check of ONE wallet (research; public RPC only, no orders, files only).

Differences from paper_copy.replay:
  * every venue the wallet uses is priced on the EXACT market state at the copy's slot:
      pump.fun curve (virtual reserves), PumpSwap (vault + measured virtual quote reserve),
      Meteora DAMM v2 and Dynamic Bonding Curve (sqrt price + local liquidity from EvtSwap2);
    a trade on any other venue is reported as "not priceable" - never priced from the next trade;
  * the copier pays what the wallet itself paid per transaction (network + priority fee + Jito tip);
  * delays 1, 3, 5, 8, 10 s; slippage tolerance 25 % (and the 10 % sensitivity is reported);
  * all closed positions of the read window are kept (wins, losses, not copyable).
Usage: python -m radar.tools.deep_copy <wallet> <out_dir> [min_positions] [max_sigs]
"""
from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import sys
import time

from . import meteora_model as mm
from .paper_copy import (JITO_ACCOUNTS, PublicRPC, QUOTES, WSOL_MINT, _wsol_delta, anchor_sig, buy_fill, keys_of,
                         market_events, pool_v, ps_state_at, sell_fill, signers_of, state_at)

DELAYS = [1, 3, 5, 8, 10]
SLOT_S = 0.4
SIZE = 0.5
MAX_CHASE = 0.25
MAX_IMPACT = 0.10
WINDOW_SLOTS = 30
MAX_WINDOW_TX = 30


# ------------------------------------------------------------------ wallet trades (all venues)
def wallet_trade(tx: dict, wallet: str) -> tuple[str, dict | None]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return "failed", None
    if wallet not in signers_of(tx):
        return "not_signed", None
    keys = keys_of(tx)
    pre, post = {}, {}
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet and b["mint"] not in QUOTES:
            pre[b["mint"]] = pre.get(b["mint"], 0) + int(b["uiTokenAmount"]["amount"])
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet and b["mint"] not in QUOTES:
            post[b["mint"]] = post.get(b["mint"], 0) + int(b["uiTokenAmount"]["amount"])
    mints = {m for m in set(pre) | set(post) if post.get(m, 0) != pre.get(m, 0)}
    if len(mints) != 1:
        return ("signed_other" if not mints else "multi_token"), None
    mint = next(iter(mints))
    idx = keys.index(wallet)
    sol_delta = (meta["postBalances"][idx] - meta["preBalances"][idx]) / 1e9 + _wsol_delta(meta, wallet)
    tip = sum(max(0, meta["postBalances"][i] - meta["preBalances"][i]) for i, k in enumerate(keys[:len(meta["postBalances"])])
              if k in JITO_ACCOUNTS) / 1e9
    dtok = post.get(mint, 0) - pre.get(mint, 0)
    base = {"slot": tx["slot"], "bt": tx.get("blockTime"), "mint": mint, "side": "buy" if dtok > 0 else "sell",
            "wallet_sol_delta": sol_delta, "fee_paid": meta["fee"] / 1e9, "tip": tip,
            "pre_raw": pre.get(mint, 0), "post_raw": post.get(mint, 0), "tok_raw": abs(dtok)}
    # pump.fun curve / PumpSwap
    pe = [e for e in market_events(tx, mint) if e["user"] == wallet]
    if pe:
        e = pe[-1]
        st = {"x": e["x"], "y": e["y"], "fee": e["fee"], "V": e.get("V"), "side": e["side"], "x_pre": e.get("x_pre"),
              "y_pre": e.get("y_pre"), "real_tok": e.get("real_tok")}
        return "swap", {**base, "venue": e["venue"], "market": e["market"], "price": e["sol"] / e["tok"] if e["tok"] else None,
                        "sol": e["sol"], "state": st}
    me = mm.swap_events(tx, keys)
    if me:
        e = me[-1]
        cs = mm.curve_state(e, True, not (e.get("collect_fee_mode") == 1 and e["direction"] == 1))
        if cs is None:
            return "unpriceable", {**base, "venue": e["venue"], "market": e["pool"]}
        sol = abs(sol_delta)
        return "swap", {**base, "venue": e["venue"], "market": e["pool"], "price": sol / (abs(dtok) / 1e6) if dtok else None,
                        "sol": sol, "state": {"s": cs["s1"], "L": cs["L"], "fee": cs["fee_rate"], "cfm": e.get("collect_fee_mode")}}
    if abs(sol_delta) > 0.001:
        return "unpriceable", {**base, "venue": "other", "market": None}
    return "signed_other", None


def collect(rpc: PublicRPC, wallet: str, min_positions: int, max_sigs: int, cache: dict) -> dict:
    sigs, before = [], None
    trades, counts, unpriced = [], {}, []
    while len(sigs) < max_sigs:
        page = rpc.sigs(wallet, before=before)
        if not page:
            break
        before = page[-1]["signature"]
        sigs += page
        for s in page:
            if s.get("err") is not None:
                counts["failed"] = counts.get("failed", 0) + 1
                continue
            tx = cache.get(s["signature"]) or rpc.tx(s["signature"])
            if not tx:
                counts["unavailable"] = counts.get("unavailable", 0) + 1
                continue
            kind, t = wallet_trade(tx, wallet)
            counts[kind] = counts.get(kind, 0) + 1
            if t is not None:
                t["sig"] = s["signature"]
                (trades if kind == "swap" else unpriced).append(t)
        closed = len([p for p in positions(trades + unpriced) if p["closed"]])
        print("  sigs", len(sigs), "swaps", len(trades), "unpriceable", len(unpriced), "closed positions", closed, "calls", rpc.calls, flush=True)
        if closed >= min_positions + 5 or len(page) < 1000:
            break
    return {"signatures": len(sigs), "first_bt": min((s.get("blockTime") or 9e18) for s in sigs) if sigs else None,
            "last_bt": max((s.get("blockTime") or 0) for s in sigs) if sigs else None, "counts": counts,
            "trades": trades, "unpriceable": unpriced}


def positions(trades: list[dict]) -> list[dict]:
    pos, cur = [], {}
    for t in sorted(trades, key=lambda t: (t["slot"], t["sig"])):
        m = t["mint"]
        if t["side"] == "buy" and m not in cur:
            if t["pre_raw"] > 0:
                continue
            cur[m] = {"mint": m, "trades": [t]}
        elif m in cur:
            cur[m]["trades"].append(t)
            if t["side"] == "sell" and t["post_raw"] == 0:
                p = cur.pop(m)
                p["closed"] = True
                pos.append(p)
    for p in cur.values():
        p["closed"] = False
        pos.append(p)
    return pos


# ------------------------------------------------------------------ market windows
def window(rpc: PublicRPC, t: dict, cache: dict) -> dict:
    market = t["market"]
    before = anchor_sig(rpc, t["slot"] + WINDOW_SLOTS + 1)
    page = rpc.sigs(market, before=before, until=t["sig"]) if before else []
    win = [s for s in reversed(page or []) if s.get("err") is None and s["slot"] <= t["slot"] + WINDOW_SLOTS]
    states = []
    for order, s in enumerate(win[:MAX_WINDOW_TX]):
        tx = cache.get(s["signature"]) or rpc.tx(s["signature"])
        if not tx:
            continue
        if t["venue"] in ("damm", "dbc"):
            for e in mm.swap_events(tx, keys_of(tx)):
                if e["pool"] != market:
                    continue
                cs = mm.curve_state(e, True, not (e.get("collect_fee_mode") == 1 and e["direction"] == 1))
                states.append({"slot": s["slot"], "s": e["next_sqrt"] / mm.Q64, "s_pre": cs["s0"] if cs else None,
                               "L": cs["L"] if cs else None, "fee": cs["fee_rate"] if cs else None,
                               "side": "buy" if e["direction"] == 1 else "sell"})
        else:
            for e in market_events(tx, t["mint"]):
                if e["market"] == market:
                    states.append({"slot": s["slot"], "x": e["x"], "y": e["y"], "fee": e["fee"], "x_pre": e.get("x_pre"),
                                   "y_pre": e.get("y_pre"), "real_tok": e.get("real_tok"), "side": e["side"], "V": e.get("V")})
    return {"anchor": before is not None, "window_tx": len(win), "decoded": min(len(win), MAX_WINDOW_TX),
            "truncated": len(win) > MAX_WINDOW_TX, "states": states}


def met_state_at(t: dict, w: dict, slot: int) -> dict | None:
    """Meteora pool at the end of `slot`: pre-swap state of the first later trade, else post of last."""
    st = {"s": t["state"]["s"], "L": t["state"]["L"], "fee": t["state"]["fee"]}
    for s in w["states"]:
        if s["slot"] <= slot:
            st = {"s": s["s"], "L": s["L"] or st["L"], "fee": s["fee"] or st["fee"]}
        elif s.get("s_pre") is not None and s.get("L"):
            return {"s": s["s_pre"], "L": s["L"], "fee": s["fee"]}
    return st


# ------------------------------------------------------------------ fills
def fill_buy(t: dict, w: dict, slot: int, sol: float) -> dict:
    v = t["venue"]
    if v in ("damm", "dbc"):
        st = met_state_at(t, w, slot)
        if not st or not st["L"]:
            return {"ok": False, "why": "không đo được thanh khoản pool"}
        q = sol * 1e9
        if t["state"].get("cfm") == 1:            # fee on the SOL input
            q *= (1 - st["fee"])
            base, s_new = mm.buy_quote(st["s"], st["L"], q)
        else:                                      # fee taken from the tokens out
            base, s_new = mm.buy_quote(st["s"], st["L"], q)
            base *= (1 - st["fee"])
        tok = base / 1e6
        return {"ok": True, "tok": tok, "price": sol / tok, "impact": (s_new / st["s"]) ** 2 - 1, "spot": st["s"] ** 2 * 1e-3}
    if v == "pumpswap":
        V = pool_v([t["state"]], w["states"])
        if V is None:
            return {"ok": False, "why": "không đo được thanh khoản pool"}
        st = ps_state_at(t["state"], w, slot)
        tok, imp = buy_fill(st["x"] + V, st["y"], sol / (1 + st["fee"]), 0.0)
        return {"ok": True, "tok": tok, "price": sol / tok, "impact": imp}
    st = state_at(t["state"], w, slot)
    if st.get("real_tok") is not None and st["real_tok"] <= 0:
        return {"ok": False, "why": "bonding curve đã đầy"}
    tok, imp = buy_fill(st["x"], st["y"], sol, st["fee"])
    return {"ok": True, "tok": tok, "price": sol / tok, "impact": imp}


def fill_sell(t: dict, w: dict | None, slot: int, tok: float) -> float | None:
    v = t["venue"]
    w = w or {"states": []}
    if v in ("damm", "dbc"):
        st = met_state_at(t, w, slot)
        if not st or not st["L"]:
            return None
        q, _ = mm.sell_base(st["s"], st["L"], tok * 1e6)
        return q / 1e9 * (1 - st["fee"])
    if v == "pumpswap":
        V = pool_v([t["state"]], w["states"]) or 0.0
        st = ps_state_at(t["state"], w, slot)
        return sell_fill(st["x"] + V, st["y"], tok, st["fee"])
    st = state_at(t["state"], w, slot)
    return sell_fill(st["x"], st["y"], tok, st["fee"])


def simulate(p: dict, wins: dict, delay: float, max_chase: float = MAX_CHASE) -> dict:
    first = p["trades"][0]
    ds = math.ceil(delay / SLOT_S)
    sells = [t for t in p["trades"] if t["side"] == "sell"]
    r = {"delay": delay, "copied": False}
    if sells and sells[0]["slot"] <= first["slot"] + ds:
        return {**r, "why": "ví gốc bán trước khi lệnh copy vào"}
    w = wins.get(first["sig"])
    if not w or not w["anchor"]:
        return {**r, "why": "thiếu dữ liệu giá sau lệnh mua"}
    f = fill_buy(first, w, first["slot"] + ds, SIZE)
    if not f["ok"]:
        return {**r, "why": f["why"]}
    chase = f["price"] / first["price"] - 1
    r.update(entry_price=f["price"], chase=round(chase, 4), impact=round(f["impact"], 4))
    if chase > max_chase:
        return {**r, "why": f"giá đã tăng {chase:.0%} (> ngưỡng trượt giá {max_chase:.0%}): lệnh không khớp"}
    if f["impact"] > MAX_IMPACT:
        return {**r, "why": f"thanh khoản mỏng: lệnh 0,5 SOL đẩy giá {f['impact']:.0%}"}
    held, out = f["tok"], 0.0
    held_w = 0.0
    cost = first["fee_paid"] + first["tip"]
    exit_ok = True
    for t in p["trades"]:
        if t["side"] == "buy":
            held_w += t["tok_raw"]
            continue
        frac = min(1.0, t["tok_raw"] / held_w) if held_w else 1.0
        held_w = max(0, held_w - t["tok_raw"])
        q = held if (frac >= 0.999 or t["post_raw"] == 0) else held * frac
        wx = wins.get(t["sig"])
        if not wx or not wx["anchor"]:
            exit_ok = False
        o = fill_sell(t, wx, t["slot"] + ds, q)
        if o is None:
            return {**r, "why": "không định giá được lệnh bán"}
        out += o
        held -= q
        cost += t["fee_paid"] + t["tip"]
    pnl = out - SIZE - cost
    return {**r, "copied": True, "sol_out": round(out, 6), "cost": round(cost, 6), "pnl": round(pnl, 6),
            "roi": round(pnl / SIZE, 4), "exit_data_complete": exit_ok}


def stats(rows: list[dict], n_signals: int) -> dict:
    ok = [r for r in rows if r["copied"]]
    pn = [r["pnl"] for r in ok]
    cum = peak = dd = 0.0
    for x in pn:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    srt = sorted(pn, reverse=True)
    tot = sum(pn)
    why = {}
    for r in rows:
        if not r["copied"]:
            k = r["why"].split(" (")[0].split(":")[0] if r.get("why") else "?"
            k = "giá tăng quá ngưỡng" if k.startswith("giá đã tăng") else k
            why[k] = why.get(k, 0) + 1
    out = {"signals": n_signals, "copied": len(ok), "success_rate": round(len(ok) / n_signals, 3) if n_signals else None,
           "pnl_sol": round(tot, 4), "invested_sol": round(len(ok) * SIZE, 2), "roi_on_invested": round(tot / (len(ok) * SIZE), 4) if ok else None,
           "win_rate": round(sum(1 for x in pn if x > 0) / len(pn), 3) if pn else None,
           "avg_roi": round(statistics.mean(r["roi"] for r in ok), 4) if ok else None,
           "median_roi": round(statistics.median(r["roi"] for r in ok), 4) if ok else None,
           "max_drawdown_sol": round(dd, 4), "worst_trade_sol": round(min(pn), 4) if pn else None,
           "pnl_without_top1": round(tot - sum(srt[:1]), 4), "pnl_without_top3": round(tot - sum(srt[:3]), 4),
           "pnl_without_top5": round(tot - sum(srt[:5]), 4),
           "top3_share": round(sum(srt[:3]) / tot, 3) if tot > 0 else None,
           "median_chase": round(statistics.median(r["chase"] for r in rows if r.get("chase") is not None), 4)
           if any(r.get("chase") is not None for r in rows) else None,
           "median_impact": round(statistics.median(r["impact"] for r in ok), 4) if ok else None,
           "not_copied": why}
    # bootstrap 95 % interval of total PnL (resampling copied trades, fixed seed)
    if len(pn) >= 10:
        import random
        rnd = random.Random(7)
        boots = sorted(sum(rnd.choice(pn) for _ in pn) for _ in range(2000))
        out["pnl_ci95"] = [round(boots[50], 3), round(boots[1949], 3)]
        out["prob_pnl_le_0"] = round(sum(1 for b in boots if b <= 0) / len(boots), 3)
    return out


def main(wallet: str, out_dir: str, min_positions: int = 100, max_sigs: int = 4000) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"deep_{wallet}.json.gz")
    rpc = PublicRPC()
    cache: dict = {}
    if os.path.exists(path):
        data = json.load(gzip.open(path))
    else:
        data = collect(rpc, wallet, min_positions, max_sigs, cache)
        data["windows"] = {}
    allpos = positions(data["trades"] + data["unpriceable"])
    closed = [p for p in allpos if p["closed"]]
    priced = [p for p in closed if all(t.get("state") for t in p["trades"])]
    wins = data["windows"]
    for i, p in enumerate(priced):
        for t in p["trades"]:
            if t["sig"] not in wins:
                wins[t["sig"]] = window(rpc, t, cache)
        if i % 10 == 0:
            print("  windows", i, "/", len(priced), "calls", rpc.calls, flush=True)
            with gzip.open(path, "wt") as f:
                json.dump(data, f)
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
    res = {"wallet": wallet, "generated_at": int(time.time()), "rpc_calls": rpc.calls,
           "assumptions": {"delays_s": DELAYS, "slot_s": SLOT_S, "size_sol": SIZE, "max_chase": MAX_CHASE, "max_impact": MAX_IMPACT,
                           "copier_costs": "same network fee + priority fee + Jito tip as the wallet's own transaction"},
           "window": {"first": data["first_bt"], "last": data["last_bt"], "signatures": data["signatures"], "counts": data["counts"]},
           "positions": {"closed": len(closed), "priced": len(priced), "not_priceable": len(closed) - len(priced),
                         "open_at_end": len(allpos) - len(closed)}, "by_delay": {}, "by_venue": {}, "sensitivity_chase_10pct": {}}
    wallet_rows = []
    sims = {d: [] for d in DELAYS}
    for p in priced:
        ws = sum(t["wallet_sol_delta"] for t in p["trades"])
        win_in = -sum(t["wallet_sol_delta"] for t in p["trades"] if t["side"] == "buy")
        wallet_rows.append({"mint": p["mint"], "venue": p["trades"][0]["venue"], "open_sig": p["trades"][0]["sig"],
                            "close_sig": p["trades"][-1]["sig"], "open_bt": p["trades"][0]["bt"], "hold_s": p["trades"][-1]["bt"] - p["trades"][0]["bt"],
                            "buys": sum(1 for t in p["trades"] if t["side"] == "buy"), "sells": sum(1 for t in p["trades"] if t["side"] == "sell"),
                            "wallet_in": round(win_in, 6), "wallet_pnl": round(ws, 6),
                            "venues": sorted({t["venue"] for t in p["trades"]})})
        for d in DELAYS:
            sims[d].append(simulate(p, wins, d))
    wp = [r["wallet_pnl"] for r in wallet_rows]
    res["wallet_original"] = {"positions": len(wp), "pnl_sol": round(sum(wp), 4), "win_rate": round(sum(1 for x in wp if x > 0) / len(wp), 3) if wp else None,
                              "sol_in": round(sum(r["wallet_in"] for r in wallet_rows), 3),
                              "median_hold_s": statistics.median(r["hold_s"] for r in wallet_rows) if wallet_rows else None}
    unp = [p for p in closed if p not in priced]
    res["unpriceable_positions"] = [{"mint": p["mint"], "venue": p["trades"][0]["venue"], "open_sig": p["trades"][0]["sig"],
                                     "wallet_pnl": round(sum(t["wallet_sol_delta"] for t in p["trades"]), 6)} for p in unp]
    for d in DELAYS:
        res["by_delay"][str(d)] = stats(sims[d], len(priced))
        res["sensitivity_chase_10pct"][str(d)] = stats([simulate(p, wins, d, 0.10) for p in priced], len(priced))
        for v in sorted({r["venue"] for r in wallet_rows}):
            idx = [i for i, r in enumerate(wallet_rows) if r["venue"] == v]
            res["by_venue"].setdefault(v, {"positions": len(idx), "wallet_pnl": round(sum(wallet_rows[i]["wallet_pnl"] for i in idx), 4)})
            res["by_venue"][v][str(d)] = stats([sims[d][i] for i in idx], len(idx))
    # model check on the windows: predicted vs actual pre-swap sqrt price chain (Meteora) / base reserves (PumpSwap)
    chk = {"meteora_pairs": 0, "meteora_match": 0}
    for w in wins.values():
        st = [s for s in w["states"] if "s" in s]
        for a, b in zip(st, st[1:]):
            if b.get("s_pre"):
                chk["meteora_pairs"] += 1
                chk["meteora_match"] += abs(b["s_pre"] / a["s"] - 1) < 1e-5
    res["model_check"] = chk
    res["positions_detail"] = [{**wallet_rows[i], **{f"d{d}": sims[d][i] for d in DELAYS}} for i in range(len(priced))]
    with open(os.path.join(out_dir, f"deep_{wallet}_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: res[k] for k in ("window", "positions", "wallet_original", "by_delay", "model_check")}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 100, int(sys.argv[4]) if len(sys.argv) > 4 else 4000)
