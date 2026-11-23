"""Paper copy-trading of a fixed list of wallets (research tool; no real orders, no Helius).

What it does, per watched wallet:
  * reads the wallet's transactions from the FREE public Solana RPC and keeps only swaps the wallet
    SIGNED itself (transactions sent by others - spam, airdrops, transfers in - are counted apart);
  * rebuilds positions (first buy from a zero balance -> sells) from the decoded pump.fun curve /
    PumpSwap events of the wallet's own transactions;
  * for every buy and sell, reads the market (bonding curve or pool) transactions that followed it
    and rebuilds the reserves after each trade, so the price a copier would have got 1, 3, 5 and 10
    seconds later can be computed with the constant-product formula, the venue's fee and the
    copier's own network fee - and the copy is marked impossible when the price had already run
    too far, the market had moved to another venue, liquidity was too thin or the wallet had
    already sold;
  * writes everything to JSON files (no database table, no schema change).
Modes:
  replay <out_dir> [--per-wallet N]   historical: the wallets' most recent closed positions
  live <out_dir> [--hours H]          poll the wallets, record detection time, simulate when closed
  report <out_dir>                    aggregate per wallet and delay -> report.json
Assumptions are in SIM (all reported with the results).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sys
import time
import urllib.request

from ..decoder.events import AmmSwapEvent, PumpTradeEvent
from ..decoder.logs import events_from_logs
from ..solana.constants import PUMP_AMM_PROGRAM, PUMP_PROGRAM, WSOL_MINT
from ..solana.pda import find_program_address, pubkey_bytes

RPC = "https://api.mainnet-beta.solana.com"
assert "helius" not in RPC
WALLETS = [  # fixed list approved on 23/11/2026 (priority order); no other wallet is ever added
    "BvtZeURZMcvvQVBr5k9jDT53E2xuFq2Da6d6RTDP7gM4",
    "7Q8rvHKi1brA49epwVyinrqX6rMKZxZqW1FoUgpXTpTs",
    "ArF3BG43u4xakKDGQmyzcaLuxzZcY1L6SC8K6Miy4maz",
    "3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5",
    "B8bvkQ7UqykUXUkVSdxngFtFBnonHf4sJTEX7s3gwwmS",
]
SIM = {
    "delays_s": [1, 3, 5, 10],
    "slot_s": 0.4,                 # Solana slot time used to turn a delay into a slot offset
    "copy_size_sol": 0.5,          # fixed size of every simulated copy buy
    "copier_tx_cost_sol": 0.000105,  # 5,000 lamports base fee + 100,000 lamports priority fee per tx
    "max_chase": 0.25,             # copy refused if our fill price is > 25 % above the wallet's price
    "max_impact": 0.10,            # copy refused if our 0.5 SOL moves the price more than 10 %
    "window_slots": 30,            # market transactions read after each wallet trade (~12 s)
    "max_sig_pages": 8,            # signature pages read between two wallet trades of a market
    "max_window_tx": 45,           # market transactions decoded per window
}
QUOTES = {WSOL_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}


class PublicRPC:
    def __init__(self, rps: float = 3.0):
        self.gap = 1.0 / rps
        self.last = 0.0
        self.calls = 0
        self.throttled = 0

    def call(self, method: str, params: list):
        for attempt in range(8):
            w = self.gap - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            req = urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         {"Content-Type": "application/json"})
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=40) as r:
                    d = json.load(r)
                if "error" in d:
                    if d["error"].get("code") in (429, -32429):
                        raise RuntimeError("429")
                    return None
                return d.get("result")
            except Exception as e:  # noqa: BLE001
                if "429" in str(e):
                    self.throttled += 1
                time.sleep(min(30, 2 ** attempt))
        return None

    def tx(self, sig: str):
        return self.call("getTransaction", [sig, {"maxSupportedTransactionVersion": 1, "encoding": "json",
                                                  "commitment": "confirmed"}])

    def sigs(self, addr: str, **opt):
        p = {"limit": 1000, "commitment": "confirmed"}
        p.update({k: v for k, v in opt.items() if v})
        return self.call("getSignaturesForAddress", [addr, p]) or []


def _wsol_delta(meta: dict, owner: str) -> float:
    d = 0.0
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == owner and b["mint"] == WSOL_MINT:
            d += float(b["uiTokenAmount"]["uiAmountString"] or 0)
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == owner and b["mint"] == WSOL_MINT:
            d -= float(b["uiTokenAmount"]["uiAmountString"] or 0)
    return d


def generic_trades(tx: dict, mint: str) -> list[dict]:
    """Other venues: the fee payer's own buy/sell of `mint`, priced from its balance changes
    (includes the venue fee and that trader's network fee: a conservative execution price)."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    payer = keys_of(tx)[0]
    pre = sum(float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in meta.get("preTokenBalances") or []
              if b.get("owner") == payer and b["mint"] == mint)
    post = sum(float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in meta.get("postTokenBalances") or []
               if b.get("owner") == payer and b["mint"] == mint)
    dt = post - pre
    ds = (meta["postBalances"][0] - meta["preBalances"][0]) / 1e9 + _wsol_delta(meta, payer)
    if dt > 0 and ds < -0.001:
        return [{"side": "buy", "price": -ds / dt, "sol": -ds}]
    if dt < 0 and ds > 0.0005:
        return [{"side": "sell", "price": ds / -dt, "sol": ds}]
    return []


def curve_pda(mint: str) -> str:
    return find_program_address([b"bonding-curve", pubkey_bytes(mint)], PUMP_PROGRAM)[0]


# ------------------------------------------------------------------ decoding
def keys_of(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = list(msg["accountKeys"])
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + la.get("writable", []) + la.get("readonly", [])


def signers_of(tx: dict) -> set[str]:
    msg = tx["transaction"]["message"]
    n = msg["header"]["numRequiredSignatures"]
    return set(msg["accountKeys"][:n])


def market_events(tx: dict, mint_hint: str | None = None) -> list[dict]:
    """Trade events of a transaction with the market state they leave behind.
    pump curve: reserves AFTER the trade are in the event. PumpSwap: the event carries the pool
    reserves BEFORE the swap; the state after is rebuilt from the amounts (checked by consistency)."""
    out = []
    evs, _ = events_from_logs((tx.get("meta") or {}).get("logMessages") or [])
    for i, (prog, ev) in enumerate(evs):
        if isinstance(ev, PumpTradeEvent):
            if ev.quote_mint and ev.quote_mint != WSOL_MINT:
                continue
            out.append({"venue": "curve", "market": curve_pda(ev.mint), "mint": ev.mint, "user": ev.user,
                        "side": "buy" if ev.is_buy else "sell", "sol": ev.sol_amount / 1e9, "tok": ev.token_amount / 1e6,
                        "x": ev.virtual_sol_reserves / 1e9, "y": ev.virtual_token_reserves / 1e6,
                        "real_tok": ev.real_token_reserves / 1e6,
                        "fee": (ev.fee_basis_points + ev.creator_fee_basis_points) / 1e4, "i": i})
        elif isinstance(ev, AmmSwapEvent):
            if not ev.pool_base_reserves or not ev.quote_amount:
                continue
            fee = (ev.lp_fee + ev.protocol_fee + ev.coin_creator_fee) / ev.quote_amount
            b0, q0 = ev.pool_base_reserves / 1e6, ev.pool_quote_reserves / 1e9
            # verified on chain 23/11/2026: the event reserves are BEFORE the swap (39/39 consecutive
            # pairs: base reserves of trade k+1 = base reserves of trade k -/+ its base amount);
            # the LP fee stays in the pool, protocol and creator fees leave it
            lp = ev.lp_fee / 1e9
            if ev.side == "buy":
                b1, q1 = b0 - ev.base_amount / 1e6, q0 + ev.quote_amount / 1e9 + lp
            else:
                b1, q1 = b0 + ev.base_amount / 1e6, q0 - ev.quote_amount / 1e9 + lp
            out.append({"venue": "pumpswap", "market": ev.pool, "mint": mint_hint, "user": ev.user, "side": ev.side,
                        "sol": ev.quote_amount / 1e9, "tok": ev.base_amount / 1e6, "x": q1, "y": b1, "x_pre": q0, "y_pre": b0,
                        "fee": fee, "i": i})
    return out


def buy_fill(x: float, y: float, sol_in: float, fee: float) -> tuple[float, float]:
    """tokens received and price impact for `sol_in` SOL into a constant-product market (x SOL, y tok)."""
    net = sol_in * (1 - fee)
    tok = y * net / (x + net)
    impact = ((x + net) / (y - tok)) / (x / y) - 1
    return tok, impact


def sell_fill(x: float, y: float, tok_in: float, fee: float) -> float:
    return x * tok_in / (y + tok_in) * (1 - fee)


# ------------------------------------------------------------------ wallet side
def wallet_trades(rpc: PublicRPC, wallet: str, max_sigs: int, cache: dict) -> tuple[list[dict], dict]:
    """The wallet's own swaps (signed by it) on pump curve / PumpSwap, oldest first, plus counters."""
    sigs = []
    before = None
    while len(sigs) < max_sigs:
        page = rpc.sigs(wallet, before=before)
        if not page:
            break
        sigs += page
        before = page[-1]["signature"]
        if len(page) < 1000:
            break
    sigs = sigs[:max_sigs]
    cnt = {"signatures": len(sigs), "failed": 0, "not_signed": 0, "signed_swaps": 0, "signed_other": 0,
           "other_venue_swaps": 0, "unavailable": 0}
    trades = []
    for s in sigs:
        if s.get("err") is not None:
            cnt["failed"] += 1
            continue
        tx = cache.get(s["signature"]) or rpc.tx(s["signature"])
        if not tx:
            cnt["unavailable"] += 1
            continue
        cache[s["signature"]] = tx
        if wallet not in signers_of(tx):
            cnt["not_signed"] += 1     # sent by someone else: spam, airdrop, transfer in, "on behalf"
            continue
        meta = tx["meta"]
        keys = keys_of(tx)
        tokd = {}
        for b in meta.get("postTokenBalances") or []:
            if b.get("owner") == wallet and b["mint"] not in QUOTES:
                tokd[b["mint"]] = tokd.get(b["mint"], 0) + float(b["uiTokenAmount"]["uiAmountString"] or 0)
        pre = {}
        for b in meta.get("preTokenBalances") or []:
            if b.get("owner") == wallet and b["mint"] not in QUOTES:
                pre[b["mint"]] = pre.get(b["mint"], 0) + float(b["uiTokenAmount"]["uiAmountString"] or 0)
        mints = set(tokd) | set(pre)
        mint = next(iter(mints)) if len(mints) == 1 else None
        evs = [e for e in market_events(tx, mint) if e["user"] == wallet]
        idx = keys.index(wallet)
        sol_delta = (meta["postBalances"][idx] - meta["preBalances"][idx]) / 1e9 + _wsol_delta(meta, wallet)
        if not evs:
            dtok = (tokd.get(mint, 0.0) - pre.get(mint, 0.0)) if mint else 0.0
            if mint and abs(dtok) > 0 and ((dtok > 0 and sol_delta < -0.001) or (dtok < 0 and sol_delta > 0.0005)):
                # swap on another venue (Meteora, Raydium, routed): priced from balance changes
                cnt["signed_swaps"] += 1
                cnt["other_venue_swaps"] += 1
                trades.append({"sig": s["signature"], "slot": tx["slot"], "bt": tx.get("blockTime"), "mint": mint,
                               "venue": "other", "market": mint, "side": "buy" if dtok > 0 else "sell", "sol": abs(sol_delta),
                               "tok": abs(dtok), "price": abs(sol_delta) / abs(dtok), "wallet_sol_delta": sol_delta,
                               "fee_paid": meta["fee"] / 1e9, "pre_tok": pre.get(mint, 0.0), "post_tok": tokd.get(mint, 0.0),
                               "state": {"price": abs(sol_delta) / abs(dtok)}})
            elif mints:
                cnt["signed_other"] += 1        # token transfer / multi-token route
            else:
                cnt["signed_other"] += 1        # SOL transfers, account management
            continue
        e = evs[-1]
        if e["mint"] is None:
            e["mint"] = mint
        if e["mint"] is None:
            cnt["other_venue_swaps"] += 1
            continue
        cnt["signed_swaps"] += 1
        trades.append({"sig": s["signature"], "slot": tx["slot"], "bt": tx.get("blockTime"), "mint": e["mint"],
                       "venue": e["venue"], "market": e["market"], "side": e["side"], "sol": e["sol"], "tok": e["tok"],
                       "price": e["sol"] / e["tok"] if e["tok"] else None, "wallet_sol_delta": sol_delta,
                       "fee_paid": meta["fee"] / 1e9, "pre_tok": pre.get(e["mint"], 0.0), "post_tok": tokd.get(e["mint"], 0.0),
                       "state": {"x": e["x"], "y": e["y"], "fee": e["fee"]}})
    trades.sort(key=lambda t: (t["slot"], t["sig"]))
    return trades, cnt


def positions_from(trades: list[dict]) -> list[dict]:
    pos, cur = [], {}
    for t in trades:
        m = t["mint"]
        if t["side"] == "buy" and m not in cur:
            if t["pre_tok"] > 1e-6:
                continue                       # bought before the window: not a clean position
            cur[m] = {"mint": m, "trades": [t]}
        elif m in cur:
            cur[m]["trades"].append(t)
            if t["side"] == "sell" and t["post_tok"] <= 1e-6:
                p = cur.pop(m)
                p["closed"] = True
                pos.append(p)
    for p in cur.values():
        p["closed"] = False
        pos.append(p)
    return pos


# ------------------------------------------------------------------ market side
def anchor_sig(rpc: PublicRPC, slot: int) -> str | None:
    """A signature of the first produced block at or after `slot` (used as the 'before' bound, so the
    market's signatures are read only inside the window, whatever the market's activity)."""
    for sl in range(slot, slot + 8):
        b = rpc.call("getBlock", [sl, {"transactionDetails": "signatures", "rewards": False,
                                       "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        if b and b.get("signatures"):
            return b["signatures"][0]
    return None


def market_window(rpc: PublicRPC, market: str, after_sig: str, after_slot: int, before_sig: str | None,
                  mint: str, cache: dict) -> dict:
    """Market states after `after_sig` for `window_slots` slots: [(slot, order, state)]."""
    sigs, pages, complete = [], 0, False
    before = anchor_sig(rpc, after_slot + SIM["window_slots"] + 1) or before_sig
    while pages < SIM["max_sig_pages"]:
        page = rpc.sigs(market, before=before, until=after_sig)
        pages += 1
        if not page:
            complete = True
            break
        sigs += page
        before = page[-1]["signature"]
        if len(page) < 1000:
            complete = True
            break
    win = [s for s in reversed(sigs) if s.get("err") is None and s["slot"] <= after_slot + SIM["window_slots"]]
    states, n = [], 0
    for order, s in enumerate(win[:SIM["max_window_tx"]]):
        tx = cache.get(s["signature"]) or rpc.tx(s["signature"])
        n += 1
        if not tx:
            continue
        if market == mint:
            for g in generic_trades(tx, mint):
                states.append({"slot": s["slot"], "order": order * 100, **g})
            continue
        for e in market_events(tx, mint):
            if e["market"] == market:
                states.append({"slot": s["slot"], "order": order * 100 + e["i"], "x": e["x"], "y": e["y"], "fee": e["fee"],
                               "x_pre": e.get("x_pre"), "y_pre": e.get("y_pre"), "real_tok": e.get("real_tok")})
    return {"complete": complete or bool(win and len(win) < len(sigs)), "sig_pages": pages, "window_tx": len(win),
            "decoded": n, "truncated": len(win) > SIM["max_window_tx"], "states": states}


def state_at(base: dict, window: dict, slot: int) -> dict:
    st = base
    for s in window["states"]:
        if s["slot"] <= slot:
            st = s
    return st


def simulate_position(p: dict, windows: dict) -> dict:
    """Copy the first buy with SIM['copy_size_sol']; copy each sell in the same proportion."""
    first = p["trades"][0]
    res = {"mint": p["mint"], "venue": first["venue"], "open_sig": first["sig"], "open_bt": first["bt"],
           "closed": p["closed"], "wallet": {}, "delays": {}}
    buys = [t for t in p["trades"] if t["side"] == "buy"]
    sells = [t for t in p["trades"] if t["side"] == "sell"]
    w_in = sum(t["sol"] for t in buys)
    res["wallet"] = {"buys": len(buys), "sells": len(sells), "sol_in": round(w_in, 6), "sol_out": round(sum(t["sol"] for t in sells), 6),
                     "net_sol_onchain": round(sum(t["wallet_sol_delta"] for t in p["trades"]), 6),
                     "hold_s": (sells[-1]["bt"] - first["bt"]) if sells else None,
                     "entry_price": first["price"], "close_sig": sells[-1]["sig"] if sells else None}
    if p["closed"] and w_in > 0:
        res["wallet"]["roi"] = round(res["wallet"]["net_sol_onchain"] / w_in, 4)
    held = 0.0
    frac_sold = []
    for t in p["trades"]:
        if t["side"] == "buy":
            held += t["tok"]
        else:
            frac_sold.append((t, min(1.0, t["tok"] / held) if held > 0 else 1.0))
            held = max(0.0, held - t["tok"])
    for d in SIM["delays_s"]:
        ds = math.ceil(d / SIM["slot_s"])
        r = {"copyable": True, "reason": None}
        w = windows.get(first["sig"])
        if w is None or (not w["states"] and not w["complete"]):
            r.update(copyable=False, reason="thiếu dữ liệu giá sau lệnh mua")
            res["delays"][str(d)] = r
            continue
        if sells and sells[0]["slot"] <= first["slot"] + ds and sells[0]["venue"] == first["venue"]:
            r.update(copyable=False, reason=f"ví đã bán trước khi lệnh copy kịp vào ({d} giây)")
            res["delays"][str(d)] = r
            continue
        if first["venue"] == "other":
            res["delays"][str(d)] = simulate_generic(first, frac_sold, windows, w, ds, p["closed"])
            continue
        st = state_at(first["state"], w, first["slot"] + ds)
        if first["venue"] == "curve" and st.get("real_tok") is not None and st["real_tok"] <= 0:
            r.update(copyable=False, reason="bonding curve đã đầy (token chuyển sàn)")
            res["delays"][str(d)] = r
            continue
        tok, impact = buy_fill(st["x"], st["y"], SIM["copy_size_sol"], st["fee"])
        our_price = SIM["copy_size_sol"] / tok if tok > 0 else math.inf
        chase = our_price / first["price"] - 1 if first["price"] else None
        r.update(entry_price=our_price, chase=round(chase, 4) if chase is not None else None, impact=round(impact, 4))
        if chase is not None and chase > SIM["max_chase"]:
            r.update(copyable=False, reason=f"giá đã tăng {chase:.0%} so với giá ví mua")
            res["delays"][str(d)] = r
            continue
        if impact > SIM["max_impact"]:
            r.update(copyable=False, reason=f"thanh khoản mỏng: lệnh 0,5 SOL đẩy giá {impact:.0%}")
            res["delays"][str(d)] = r
            continue
        out, ntx, exit_ok = 0.0, 1, True
        held_c = tok
        for t, frac in frac_sold:
            wx = windows.get(t["sig"])
            if t["venue"] != first["venue"]:
                exit_ok = False
                r["exit_note"] = "ví bán ở sàn khác (token đã chuyển sàn): thoát theo trạng thái gần nhất"
            if wx is None or (not wx["states"] and not wx["complete"]):
                exit_ok = False
                sx = t["state"]
            else:
                sx = state_at(t["state"], wx, t["slot"] + ds)
            q = held_c * frac if frac < 0.999 else held_c
            out += sell_fill(sx["x"], sx["y"], q, sx["fee"])
            held_c -= q
            ntx += 1
        cost = ntx * SIM["copier_tx_cost_sol"]
        r.update(sol_in=SIM["copy_size_sol"], sol_out=round(out, 6), tx_cost=round(cost, 6),
                 open_tokens_left=round(held_c, 3), exit_data_complete=exit_ok)
        if p["closed"]:
            r["pnl_sol"] = round(out - SIM["copy_size_sol"] - cost, 6)
            r["roi"] = round(r["pnl_sol"] / SIM["copy_size_sol"], 4)
        else:
            r["pnl_sol"] = None
            r["reason"] = "vị thế chưa đóng: chưa tính PnL"
        res["delays"][str(d)] = r
    return res


def _first_trade(window: dict | None, side: str, slot: int) -> dict | None:
    if not window:
        return None
    return next((s for s in window["states"] if s.get("side") == side and s["slot"] >= slot), None)


def simulate_generic(first: dict, frac_sold: list, windows: dict, w: dict, ds: int, closed: bool) -> dict:
    """Other venues: our fill = execution price of the first real buy (sell) of the token at or
    after the delay; when nobody traded in the window, the wallet's own price is used (flagged)."""
    r = {"copyable": True, "reason": None, "pricing": "giao dịch thật kế tiếp (ước tính, không có dữ liệu thanh khoản)"}
    b = _first_trade(w, "buy", first["slot"] + ds)
    entry = b["price"] if b else first["price"]
    r["entry_from_real_trade"] = b is not None
    chase = entry / first["price"] - 1 if first["price"] else None
    r.update(entry_price=entry, chase=round(chase, 4) if chase is not None else None, impact=None)
    if chase is not None and chase > SIM["max_chase"]:
        r.update(copyable=False, reason=f"giá đã tăng {chase:.0%} so với giá ví mua")
        return r
    tok = SIM["copy_size_sol"] / entry
    held_c, out, ntx, exit_real = tok, 0.0, 1, True
    for t, frac in frac_sold:
        x = _first_trade(windows.get(t["sig"]), "sell", t["slot"] + ds)
        px = x["price"] if x else t["price"]
        exit_real &= x is not None
        q = held_c * frac if frac < 0.999 else held_c
        out += q * px
        held_c -= q
        ntx += 1
    cost = ntx * SIM["copier_tx_cost_sol"]
    r.update(sol_in=SIM["copy_size_sol"], sol_out=round(out, 6), tx_cost=round(cost, 6), open_tokens_left=round(held_c, 3),
             exit_data_complete=exit_real)
    if closed:
        r["pnl_sol"] = round(out - SIM["copy_size_sol"] - cost, 6)
        r["roi"] = round(r["pnl_sol"] / SIM["copy_size_sol"], 4)
    else:
        r.update(pnl_sol=None, reason="vị thế chưa đóng: chưa tính PnL")
    return r


def check_pumpswap_reserves(windows: dict) -> dict:
    """Consistency of the PumpSwap 'reserves before the swap' reading: the pre-state of a trade should
    equal the post-state of the previous trade in the same pool."""
    ok = bad = 0
    for w in windows.values():
        prev = None
        for s in w["states"]:
            if s.get("x_pre") is None:
                prev = None
                continue
            if prev is not None:
                if abs(s["y_pre"] - prev["y"]) <= 1e-6 * max(1, prev["y"]) + 1e-6:
                    ok += 1
                else:
                    bad += 1
            prev = s
    return {"consecutive_pairs_matching": ok, "not_matching": bad}


# ------------------------------------------------------------------ modes
def run_wallet(rpc: PublicRPC, wallet: str, out_dir: str, max_sigs: int, per_wallet: int) -> dict:
    path = os.path.join(out_dir, f"{wallet}.json.gz")
    cache: dict = {}
    trades, cnt = wallet_trades(rpc, wallet, max_sigs, cache)
    pos = [p for p in positions_from(trades) if p["closed"]][-per_wallet:]
    windows = {}
    for p in pos:
        ts = p["trades"]
        for k, t in enumerate(ts):
            nxt = next((u for u in ts[k + 1:] if u["market"] == t["market"]), None)
            windows[t["sig"]] = market_window(rpc, t["market"], t["sig"], t["slot"], nxt["sig"] if nxt else None, t["mint"], cache)
    sims = [simulate_position(p, windows) for p in pos]
    data = {"wallet": wallet, "collected_at": int(time.time()), "sim": SIM, "counts": cnt, "trades": trades,
            "positions": [{"mint": p["mint"], "closed": p["closed"], "trades": [t["sig"] for t in p["trades"]]} for p in pos],
            "windows": windows, "simulations": sims, "reserve_check": check_pumpswap_reserves(windows),
            "rpc_calls": rpc.calls}
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
    return data


def replay(out_dir: str, per_wallet: int = 25, max_sigs: int = 1500) -> None:
    os.makedirs(out_dir, exist_ok=True)
    rpc = PublicRPC()
    for w in WALLETS:
        if os.path.exists(os.path.join(out_dir, f"{w}.json.gz")):
            continue
        d = run_wallet(rpc, w, out_dir, max_sigs, per_wallet)
        print(w[:8], "swaps", d["counts"]["signed_swaps"], "closed simulated", len(d["simulations"]),
              "reserve check", d["reserve_check"], "calls", rpc.calls, "429", rpc.throttled, flush=True)
    print("done", rpc.calls, flush=True)


def live(out_dir: str, hours: float) -> None:
    """Poll the 5 wallets (public RPC), log every new self-signed swap with its detection delay;
    every 10 minutes the closed positions of the session are simulated (results in live/)."""
    os.makedirs(out_dir, exist_ok=True)
    rpc = PublicRPC(rps=2.0)
    seen_path = os.path.join(out_dir, "live_seen.json")
    seen = json.load(open(seen_path)) if os.path.exists(seen_path) else {}
    log_path = os.path.join(out_dir, "live_detections.jsonl")
    end = time.time() + hours * 3600
    while time.time() < end:
        for w in WALLETS:
            page = rpc.sigs(w, until=seen.get(w))
            if page and w in seen:
                for s in reversed(page):
                    if s.get("err") is not None:
                        continue
                    tx = rpc.tx(s["signature"])
                    if not tx:
                        continue
                    signed = w in signers_of(tx)
                    evs = [e for e in market_events(tx) if e["user"] == w] if signed else []
                    with open(log_path, "a") as f:
                        f.write(json.dumps({"wallet": w, "sig": s["signature"], "slot": s["slot"], "block_time": s.get("blockTime"),
                                            "detected_at": round(time.time(), 2),
                                            "detect_delay_s": round(time.time() - (s.get("blockTime") or time.time()), 2),
                                            "signed": signed, "swap": [{k: e[k] for k in ("venue", "side", "sol", "tok", "mint")} for e in evs]}) + "\n")
            if page:
                seen[w] = page[0]["signature"]
            json.dump(seen, open(seen_path, "w"))
    print("live done", rpc.calls, flush=True)


def report(out_dir: str) -> dict:
    rep = {"sim": SIM, "wallets": {}}
    for w in WALLETS:
        p = os.path.join(out_dir, f"{w}.json.gz")
        if not os.path.exists(p):
            continue
        d = json.load(gzip.open(p))
        sims = d["simulations"]
        wr = {"counts": d["counts"], "positions_closed": len(sims), "reserve_check": d["reserve_check"]}
        wn = [s["wallet"]["net_sol_onchain"] for s in sims if s["closed"]]
        wr["wallet_original"] = {"positions": len(wn), "pnl_sol_onchain": round(sum(wn), 4), "wins": sum(1 for x in wn if x > 0),
                                 "win_rate": round(sum(1 for x in wn if x > 0) / len(wn), 3) if wn else None,
                                 "sol_in": round(sum(s["wallet"]["sol_in"] for s in sims), 3),
                                 "median_hold_s": sorted(s["wallet"]["hold_s"] or 0 for s in sims)[len(sims) // 2] if sims else None}
        wr["delays"] = {}
        for dl in SIM["delays_s"]:
            rs = [s["delays"][str(dl)] for s in sims]
            ok = [r for r in rs if r.get("copyable") and r.get("pnl_sol") is not None]
            pn = [r["pnl_sol"] for r in ok]
            cum, peak, dd = 0.0, 0.0, 0.0
            for x in pn:
                cum += x
                peak = max(peak, cum)
                dd = min(dd, cum - peak)
            reasons = {}
            for r in rs:
                if not r.get("copyable"):
                    k = r["reason"].split(":")[0] if r.get("reason") else "?"
                    k = "giá đã tăng quá ngưỡng" if k.startswith("giá đã tăng") else ("ví đã bán trước" if k.startswith("ví đã bán") else k)
                    reasons[k] = reasons.get(k, 0) + 1
            wr["delays"][str(dl)] = {"signals": len(rs), "copied": len(ok), "catch_rate": round(len(ok) / len(rs), 3) if rs else None,
                                     "pnl_sol": round(sum(pn), 4), "wins": sum(1 for x in pn if x > 0),
                                     "win_rate": round(sum(1 for x in pn if x > 0) / len(pn), 3) if pn else None,
                                     "avg_roi": round(sum(r["roi"] for r in ok) / len(ok), 4) if ok else None,
                                     "max_drawdown_sol": round(dd, 4), "invested_sol": round(len(ok) * SIM["copy_size_sol"], 2),
                                     "median_chase": sorted(r["chase"] for r in rs if r.get("chase") is not None)[len([r for r in rs if r.get("chase") is not None]) // 2]
                                     if any(r.get("chase") is not None for r in rs) else None,
                                     "not_copyable": reasons}
        rep["wallets"][w] = wr
    live_log = os.path.join(out_dir, "live_detections.jsonl")
    if os.path.exists(live_log):
        rows = [json.loads(x) for x in open(live_log)]
        sw = [r for r in rows if r["signed"] and r["swap"]]
        dl = sorted(r["detect_delay_s"] for r in sw)
        rep["live"] = {"transactions_seen": len(rows), "self_signed_swaps": len(sw), "not_signed": sum(1 for r in rows if not r["signed"]),
                       "detect_delay_s_median": dl[len(dl) // 2] if dl else None,
                       "detect_delay_s_p90": dl[int(len(dl) * 0.9)] if dl else None,
                       "by_wallet": {w: sum(1 for r in sw if r["wallet"] == w) for w in WALLETS}}
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    return rep


if __name__ == "__main__":
    mode, out = sys.argv[1], sys.argv[2]
    if mode == "replay":
        replay(out, int(sys.argv[3]) if len(sys.argv) > 3 else 25)
    elif mode == "live":
        live(out, float(sys.argv[3]) if len(sys.argv) > 3 else 6.0)
    elif mode == "report":
        print(json.dumps(report(out), indent=1, ensure_ascii=False))
