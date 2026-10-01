"""MONEY FLOW research engine (SHADOW — never a BUY gate). Question it serves: is a rising meme coin carried by real
money from many INDEPENDENT buyers, or by a few related wallets?

Data (no stream with wallet-level trades is available: PumpPortal's subscribeTokenTrade needs a funded API key, so it
is NOT AVAILABLE here). Sources actually used, via the Helius RPC under its own credit budget:
  * getSignaturesForAddress(mint): every transaction's slot + blockTime  -> full tx-rate (all trades counted)
  * getTransaction(jsonParsed) for a SAMPLE of the newest unparsed txs   -> wallet, side, SOL, token pre/post balance
  * wallet funding (first incoming SOL of wallets with a short history, cached per wallet)  -> funder / activation time
Every feature is computed from data with blockTime <= decision time and carries its coverage (parsed / total txs in
the window). Nothing is extrapolated; missing = None (UNKNOWN), and UNKNOWN never counts as PASS.

MONEY_FLOW_SCORE 0-100 components: buyer_growth, independent_buyers, sol_inflow, inflow_acceleration,
buy_distribution, synchronization (penalty), cluster_penalty, creator_related_penalty, repeat_buyer_penalty.
INDEPENDENT_BUYER_SCORE 0-100 and CLUSTER_RISK 0-100 come with it. INDEPENDENCE = UNKNOWN unless funding data exists.
"""
from __future__ import annotations

import os
import time
from collections import Counter, deque

from research.onchain import parse_tx, pool_owners, trades
from trading.setup_common import clamp, ramp

WINDOW_S = 60.0                  # "last minute" vs "previous minute"
MIN_SAMPLE = 5                   # parsed trades needed for buyer-level components


def _sample(rec: dict | None, t: float, lo: float, hi: float) -> list:
    if not rec:
        return []
    return [x for x in rec.get("txs", []) if x.get("t") is not None and lo < x["t"] <= hi and x["t"] <= t]


def window_trades(rec: dict, t: float, pools: set) -> dict:
    """Buyers / sellers with SOL amounts in [t-60, t] and [t-120, t-60] (sampled txs) + full tx counts."""
    out = {}
    for name, lo, hi in (("now", t - WINDOW_S, t), ("prev", t - 2 * WINDOW_S, t - WINDOW_S)):
        buys, sells = [], []
        for x in _sample(rec, t, lo, hi):
            b, s = trades(x, pools)
            sol = abs(x.get("sol") or 0.0)
            for o in b:
                buys.append((o, sol if x.get("who") == o else None, x.get("slot"), x.get("t")))
            for o, amt in s.items():
                sells.append((o, sol if x.get("who") == o else None, amt, (x.get("pre_bal") or {}).get(o), x.get("t")))
        sigs = [bt for _, bt in rec.get("sigs", []) if bt is not None and lo < bt <= hi and bt <= t]
        out[name] = {"buys": buys, "sells": sells, "tx_total": len(sigs),
                     "parsed": len(_sample(rec, t, lo, hi))}
    return out


def money_flow_features(rec: dict | None, t: float, creator: str | None = None, funders: dict | None = None,
                        creator_funder: str | None = None) -> dict:
    """Point-in-time money-flow features (only data with blockTime <= t)."""
    f = {k: None for k in ("buyer_count", "unique_buyer_count", "new_buyer_count", "repeat_buyer_count",
                           "repeat_buyer_ratio", "buyer_acceleration", "sol_inflow", "sol_outflow", "net_sol_inflow",
                           "sol_inflow_acceleration", "avg_buy_sol", "median_buy_sol", "buy_concentration_top3",
                           "synchronized_buy_share", "creator_related_buyers", "funding_cluster_max",
                           "funded_buyers_known", "independent_buyers", "tx_rate_now", "tx_rate_prev",
                           "tx_acceleration", "coverage")}
    f["status"] = "UNKNOWN (not collected)" if not rec else rec.get("status", "ok")
    if not rec or rec.get("status") != "ok":
        return f
    pools = pool_owners(rec.get("txs", []))
    w = window_trades(rec, t, pools)
    now, prev = w["now"], w["prev"]
    f["tx_rate_now"], f["tx_rate_prev"] = now["tx_total"], prev["tx_total"]
    if prev["tx_total"]:
        f["tx_acceleration"] = round(now["tx_total"] / prev["tx_total"], 3)
    f["coverage"] = round(now["parsed"] / now["tx_total"], 3) if now["tx_total"] else None
    if now["parsed"] < MIN_SAMPLE:
        f["status"] = f"UNKNOWN (only {now['parsed']} parsed trades in the last minute)"
        return f
    f["status"] = "ok"
    earlier = {o for x in _sample(rec, t, 0, t - WINDOW_S) for o in trades(x, pools)[0]}
    buyers = [b[0] for b in now["buys"]]
    ub = set(buyers)
    f["buyer_count"], f["unique_buyer_count"] = len(buyers), len(ub)
    f["new_buyer_count"] = len(ub - earlier)
    f["repeat_buyer_count"] = len(ub & earlier) + sum(c - 1 for c in Counter(buyers).values() if c > 1)
    f["repeat_buyer_ratio"] = round(f["repeat_buyer_count"] / len(buyers), 3) if buyers else None
    pu = len({b[0] for b in prev["buys"]})
    if pu:
        f["buyer_acceleration"] = round(len(ub) / pu, 3)
    sol_in = [b[1] for b in now["buys"] if b[1] is not None]
    sol_out = [s[1] for s in now["sells"] if s[1] is not None]
    f["sol_inflow"], f["sol_outflow"] = round(sum(sol_in), 4), round(sum(sol_out), 4)
    f["net_sol_inflow"] = round(sum(sol_in) - sum(sol_out), 4)
    prev_in = sum(b[1] for b in prev["buys"] if b[1] is not None) - sum(s[1] for s in prev["sells"] if s[1] is not None)
    if prev["parsed"] >= MIN_SAMPLE:
        f["sol_inflow_acceleration"] = round(f["net_sol_inflow"] - prev_in, 4)
    if sol_in:
        f["avg_buy_sol"] = round(sum(sol_in) / len(sol_in), 4)
        f["median_buy_sol"] = round(sorted(sol_in)[len(sol_in) // 2], 4)
        per = Counter()
        for o, s, *_ in now["buys"]:
            per[o] += s or 0
        tot = sum(per.values())
        f["buy_concentration_top3"] = round(sum(v for _, v in per.most_common(3)) / tot, 3) if tot else None
    slots = Counter()
    for o, _, slot, _ in now["buys"]:
        slots[slot] += 1
    f["synchronized_buy_share"] = round(sum(c for c in slots.values() if c >= 3) / len(buyers), 3) if buyers else None
    # funding relationships (only for wallets whose funding was actually looked up)
    funders = funders or {}
    known = {o: funders[o] for o in ub if o in funders and funders[o].get("status") == "ok"}
    f["funded_buyers_known"] = len(known)
    rel = 0
    if creator:
        rel += sum(1 for o in ub if o == creator)
    by_funder = Counter(v.get("funder") for v in known.values() if v.get("funder"))
    if creator:
        rel += sum(1 for v in known.values() if v.get("funder") in (creator, creator_funder))
    f["creator_related_buyers"] = rel if (known or creator) else None
    f["funding_cluster_max"] = max(by_funder.values()) if by_funder else (0 if known else None)
    if known and len(known) >= max(3, len(ub) // 2):
        clustered = sum(c for c in by_funder.values() if c >= 2)
        f["independent_buyers"] = len(ub) - clustered - rel
    return f


def score_money_flow(f: dict) -> dict:
    """MONEY_FLOW_SCORE, INDEPENDENT_BUYER_SCORE, CLUSTER_RISK (0-100, None = UNKNOWN) with components."""
    comp = {k: None for k in ("buyer_growth", "independent_buyers", "sol_inflow", "inflow_acceleration",
                              "buy_distribution", "synchronization", "cluster_penalty", "creator_related_penalty",
                              "repeat_buyer_penalty")}
    out = {"money_flow_score": None, "independent_buyer_score": None, "cluster_risk": None, "components": comp,
           "independence": "UNKNOWN", "status": f.get("status")}
    if f.get("status") != "ok":
        return out
    if f.get("buyer_acceleration") is not None:
        comp["buyer_growth"] = ramp(f["buyer_acceleration"], 0.8, 2.0)
    if f.get("independent_buyers") is not None:
        comp["independent_buyers"] = ramp(f["independent_buyers"], 2, 12)
        out["independence"] = "MEASURED"
    if f.get("net_sol_inflow") is not None:
        comp["sol_inflow"] = ramp(f["net_sol_inflow"], 0, 10)
    if f.get("sol_inflow_acceleration") is not None:
        comp["inflow_acceleration"] = ramp(f["sol_inflow_acceleration"], -2, 5)
    if f.get("buy_concentration_top3") is not None:
        comp["buy_distribution"] = 1 - ramp(f["buy_concentration_top3"], 0.4, 0.9)
    if f.get("synchronized_buy_share") is not None:
        comp["synchronization"] = 1 - ramp(f["synchronized_buy_share"], 0.1, 0.6)
    if f.get("funding_cluster_max") is not None:
        comp["cluster_penalty"] = 1 - ramp(f["funding_cluster_max"], 1, 5)
    if f.get("creator_related_buyers") is not None:
        comp["creator_related_penalty"] = 1 - ramp(f["creator_related_buyers"], 0, 3)
    if f.get("repeat_buyer_ratio") is not None:
        comp["repeat_buyer_penalty"] = 1 - ramp(f["repeat_buyer_ratio"], 0.2, 0.7)
    w = {"buyer_growth": 0.15, "independent_buyers": 0.15, "sol_inflow": 0.15, "inflow_acceleration": 0.10,
         "buy_distribution": 0.10, "synchronization": 0.10, "cluster_penalty": 0.10, "creator_related_penalty": 0.10,
         "repeat_buyer_penalty": 0.05}
    known = {k: v for k, v in comp.items() if v is not None}
    if len(known) >= 4:
        out["money_flow_score"] = round(100 * sum(w[k] * v for k, v in known.items()) / sum(w[k] for k in known), 1)
    if comp["independent_buyers"] is not None:
        out["independent_buyer_score"] = round(100 * comp["independent_buyers"] * (comp["creator_related_penalty"] or 1.0), 1)
    risk = [1 - v for k, v in comp.items() if k in ("synchronization", "cluster_penalty", "creator_related_penalty")
            and v is not None]
    if risk:
        out["cluster_risk"] = round(100 * max(risk), 1)
    out["coverage"] = f.get("coverage")
    return out


class MoneyFlowCollector:
    """Budgeted Helius collector: for the few tokens that matter (held > candidate > high setup / money flow),
    refresh mint signatures and parse a sample of the newest unparsed txs; look up funding of a few new buyers.
    Default budget MONEYFLOW_CREDITS_PER_MIN=30 (~1.8K credits/hour), always behind the Helius pacing governor."""

    def __init__(self, rpc, credits_per_min: int | None = None, parse_per_refresh: int = 8, refresh_s: float = 30.0,
                 funding_per_min: int = 6):
        self.rpc = rpc
        self.credits_per_min = int(credits_per_min if credits_per_min is not None
                                   else os.environ.get("MONEYFLOW_CREDITS_PER_MIN", 30))
        self.parse_per_refresh, self.refresh_s, self.funding_per_min = parse_per_refresh, refresh_s, funding_per_min
        self.recs: dict[str, dict] = {}
        self.funders: dict[str, dict] = {}
        self.spent: deque = deque()
        self.n = {"refreshes": 0, "txs_parsed": 0, "funding_lookups": 0, "skipped_budget": 0, "failures": 0}

    def _left(self, now: float) -> int:
        while self.spent and now - self.spent[0][0] >= 60:
            self.spent.popleft()
        return self.credits_per_min - sum(c for _, c in self.spent)

    def _governor_ok(self, now: float) -> bool:
        try:
            st = self.rpc.credits.state()
            allowed = (st.get("daily_budget") or 0) * min(1.0, (now % 86400) / 86400 + 0.05)
            return not st.get("quota_exhausted") and not (allowed and st.get("used", 0) > 0.8 * allowed)
        except (AttributeError, TypeError):
            return True

    async def refresh(self, mint: str, creator: str | None, now: float) -> None:
        if self._left(now) < 2 or not self._governor_ok(now):
            self.n["skipped_budget"] += 1
            return
        rec = self.recs.setdefault(mint, {"status": "ok", "txs": [], "sigs": [], "parsed": set(), "creator": creator,
                                          "last": 0.0})
        if now - rec["last"] < self.refresh_s:
            return
        rec["last"] = now
        sigs = await self.rpc.signatures(mint, limit=100)
        self.spent.append((now, 1))
        self.n["refreshes"] += 1
        if sigs is None:
            self.n["failures"] += 1
            rec["status"] = "UNKNOWN (signatures unavailable)" if not rec["txs"] else rec["status"]
            return
        ok = [s for s in sigs if not s.get("err")]
        known = {(s["slot"], s.get("blockTime")) for s in ok}
        rec["sigs"] = sorted(set(map(tuple, rec["sigs"])) | known)[-2000:]
        todo = [s for s in ok if s["signature"] not in rec["parsed"]][: self.parse_per_refresh]
        for s in todo:
            if self._left(now) < 1:
                break
            tx = await self.rpc.transaction(s["signature"])
            self.spent.append((now, 1))
            rec["parsed"].add(s["signature"])
            p = parse_tx(tx, mint) if tx else None
            if p:
                rec["txs"].append(p)
                self.n["txs_parsed"] += 1
        rec["txs"] = rec["txs"][-600:]

    async def lookup_funding(self, wallets: list[str], now: float) -> None:
        done = 0
        for wlt in wallets:
            if wlt in self.funders or done >= self.funding_per_min or self._left(now) < 2 or not self._governor_ok(now):
                continue
            done += 1
            self.n["funding_lookups"] += 1
            sigs = await self.rpc.signatures(wlt, limit=25)
            self.spent.append((now, 1))
            if sigs is None:
                self.funders[wlt] = {"status": "UNKNOWN (rpc)"}
                continue
            ok = [s for s in sigs if not s.get("err")]
            if not ok:
                self.funders[wlt] = {"status": "UNKNOWN (no txs)"}
                continue
            first = min(ok, key=lambda s: s.get("blockTime") or 0)
            info = {"status": "ok", "complete": len(sigs) < 25, "first_seen": first.get("blockTime"), "funder": None}
            if len(sigs) < 25:
                tx = await self.rpc.transaction(first["signature"])
                self.spent.append((now, 1))
                info["funder"] = _incoming_funder(tx, wlt) if tx else None
            else:
                info["status"] = "UNKNOWN (wallet history > 25 txs; first funding not traced)"
            self.funders[wlt] = info
            if len(self.funders) > 50_000:
                for k in list(self.funders)[:10_000]:
                    self.funders.pop(k, None)

    def stats(self) -> dict:
        return dict(self.n) | {"tracked_tokens": len(self.recs), "funders_cached": len(self.funders),
                               "credits_per_min_limit": self.credits_per_min}


def _incoming_funder(tx: dict, wallet: str) -> str | None:
    for ins in ((tx or {}).get("transaction") or {}).get("message", {}).get("instructions") or []:
        p = ins.get("parsed") if isinstance(ins, dict) else None
        if isinstance(p, dict) and p.get("type") == "transfer" and ins.get("program") == "system":
            info = p.get("info") or {}
            if info.get("destination") == wallet and info.get("source"):
                return info["source"]
    return None
