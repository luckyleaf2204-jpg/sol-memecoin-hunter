"""Smart Money co-buy signal (PHẦN VII).

A signal for token T at time `now` looks at Smart Money buys of T inside
[now - window, now]. A buyer counts when:
  * its wallet score >= signal_min_wallet_score and it is not excluded,
  * its current position in T (sum of buys of the holding cycle) >= signal_min_position_sol.
Buyers are grouped by relationship cluster: the number of INDEPENDENT buyers is the number of
distinct clusters. Levels (configurable):
  WATCH        >= 2 independent buyers and >= 5 SOL total
  STRONG       >= 3 independent buyers and >= 10 SOL
  VERY_STRONG  >= 5 independent buyers and >= 20 SOL
Downgrades:
  * price already > signal_max_runup x the first Smart Money entry in the window -> no signal
  * STRONG / VERY_STRONG require every counted buy to be `verified` on-chain; otherwise the
    level computed from verified buys only is used (never below WATCH if WATCH holds)
  * HIGH token risk caps the level at WATCH (configurable)
  * `unverified` buys (third-party data not yet confirmed) are never counted
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEVELS = ["WATCH", "STRONG", "VERY_STRONG"]
LEVEL_RANK = {None: 0, "WATCH": 1, "STRONG": 2, "VERY_STRONG": 3}


@dataclass
class SmartBuy:
    wallet: str
    ts: int
    sol: float
    position_sol: float
    price_sol: float | None
    verification: str
    cluster_id: str
    score: float
    win_rate: float | None
    signature: str
    mc_sol: float | None = None


@dataclass
class SmartSell:
    wallet: str
    ts: int
    sol: float


@dataclass
class SignalResult:
    level: str | None
    raw_buyers: int
    independent_buyers: int
    total_buy_sol: float
    total_sell_sol: float
    net_flow_sol: float
    window_start: int | None
    window_end: int | None
    first_entry_price: float | None
    runup: float | None
    buyers: list[dict[str, Any]] = field(default_factory=list)
    downgrade_reasons: list[str] = field(default_factory=list)
    verification: str = "verified"
    avg_buyer_score: float | None = None


def _level_for(n_indep: int, total: float, s: dict[str, Any]) -> str | None:
    if n_indep >= s["signal_very_strong_min_wallets"] and total >= s["signal_very_strong_min_total_sol"]:
        return "VERY_STRONG"
    if n_indep >= s["signal_strong_min_wallets"] and total >= s["signal_strong_min_total_sol"]:
        return "STRONG"
    if n_indep >= s["signal_watch_min_wallets"] and total >= s["signal_watch_min_total_sol"]:
        return "WATCH"
    return None


def _aggregate(buys: list[SmartBuy]) -> tuple[dict[str, dict], int, float]:
    per_wallet: dict[str, dict] = {}
    for b in buys:
        d = per_wallet.setdefault(b.wallet, {"wallet": b.wallet, "buy_sol_window": 0.0, "position_sol": 0.0,
                                             "cluster_id": b.cluster_id, "score": b.score, "win_rate": b.win_rate,
                                             "first_ts": b.ts, "verification": "verified", "signatures": []})
        d["buy_sol_window"] += b.sol
        d["position_sol"] = max(d["position_sol"], b.position_sol)
        if b.ts <= d["first_ts"] or "entry_price_sol" not in d:
            # entry of this wallet inside the window: time, price and market cap of its first buy
            d["entry_price_sol"] = b.price_sol
            d["entry_mc_sol"] = b.mc_sol
        d["first_ts"] = min(d["first_ts"], b.ts)
        d["signatures"].append(b.signature)
        if b.verification != "verified":
            d["verification"] = b.verification
    clusters = {d["cluster_id"] for d in per_wallet.values()}
    total = sum(d["position_sol"] for d in per_wallet.values())
    return per_wallet, len(clusters), total


def evaluate(buys: list[SmartBuy], sells: list[SmartSell], now: int, settings: dict[str, Any],
             current_price: float | None, risk_level: str | None) -> SignalResult:
    s = settings
    window = int(float(s["signal_window_minutes"]) * 60)
    start = now - window
    excluded = set(s.get("excluded_wallets") or [])
    reasons: list[str] = []
    cand = [b for b in buys if start <= b.ts <= now and b.wallet not in excluded
            and b.score >= float(s["signal_min_wallet_score"]) and b.verification != "unverified"]
    per_wallet, _, _ = _aggregate(cand)
    counted = {w: d for w, d in per_wallet.items() if d["position_sol"] >= float(s["signal_min_position_sol"])}
    counted_buys = [b for b in cand if b.wallet in counted]
    _, n_indep, total = _aggregate(counted_buys) if counted_buys else ({}, 0, 0.0)
    sell_sol = sum(x.sol for x in sells if start <= x.ts <= now)
    first_price = None
    if counted_buys:
        first = min(counted_buys, key=lambda b: b.ts)
        first_price = first.price_sol
    runup = (current_price / first_price) if (current_price and first_price) else None

    level = _level_for(n_indep, total, s)
    if level and runup is not None and runup > float(s["signal_max_runup"]):
        reasons.append(f"price already {runup:.2f}x above the first Smart Money entry")
        level = None
    verification = "verified"
    if any(d["verification"] != "verified" for d in counted.values()):
        verification = "partially_verified"
    if level in ("STRONG", "VERY_STRONG") and s.get("signal_require_verified_for_strong", True):
        vb = [b for b in counted_buys if b.verification == "verified"]
        _, nv, tv = _aggregate(vb) if vb else ({}, 0, 0.0)
        lv = _level_for(nv, tv, s)
        if LEVEL_RANK[lv] < LEVEL_RANK[level]:
            reasons.append(f"{level} pending on-chain verification of some buys: using {lv or 'WATCH'}")
            level = lv or "WATCH"
    if level in ("STRONG", "VERY_STRONG") and risk_level == "HIGH" and s.get("signal_high_risk_caps_watch", True):
        reasons.append("HIGH token risk: capped at WATCH")
        level = "WATCH"
    n_raw = len(counted)
    if n_raw > n_indep:
        reasons.append(f"{n_raw} wallets but only {n_indep} independent clusters (related wallets merged)")
    buyers = sorted(counted.values(), key=lambda d: -d["position_sol"])
    scores = [d["score"] for d in buyers]
    return SignalResult(level=level, raw_buyers=n_raw, independent_buyers=n_indep, total_buy_sol=total,
                        total_sell_sol=sell_sol, net_flow_sol=total - sell_sol, window_start=start, window_end=now,
                        first_entry_price=first_price, runup=runup, buyers=buyers, downgrade_reasons=reasons,
                        verification=verification,
                        avg_buyer_score=(sum(scores) / len(scores)) if scores else None)


def flow_label(net_flow: float, gross: float) -> str:
    """Accumulating / distributing / balanced (|net| < 15 % of gross flow)."""
    if gross <= 0:
        return "BALANCED"
    if abs(net_flow) < 0.15 * gross:
        return "BALANCED"
    return "ACCUMULATING" if net_flow > 0 else "DISTRIBUTING"



def exit_progress(trades_by_wallet: dict[str, list[tuple[int, str, float, float]]], detected_at: int
                  ) -> dict[str, Any]:
    """How much of the signal buyers' holdings has been sold since the signal.

    trades_by_wallet: wallet -> [(ts, 'buy'|'sell', tokens, sol)] for the signal token.
    For each wallet: held = tokens bought - sold up to `detected_at`; exited = tokens sold after,
    capped at held. The overall share is weighted by the SOL each wallet had in the position.
    """
    per = []
    w_total = w_exit = 0.0
    sell_sol = 0.0
    for wallet, lst in trades_by_wallet.items():
        held = cost = 0.0
        sold_after = sol_after = 0.0
        for ts, side, tokens, sol in sorted(lst):
            if ts <= detected_at:
                if side == "buy":
                    held += tokens
                    cost += sol
                else:
                    if held > 0:
                        cost *= max(0.0, 1 - tokens / held)
                    held = max(0.0, held - tokens)
            elif side == "sell":
                sold_after += tokens
                sol_after += sol
        if held <= 0:
            continue
        frac = min(1.0, sold_after / held)
        per.append({"wallet": wallet, "exited_share": frac, "sell_sol_after": sol_after, "position_sol": cost})
        w_total += cost
        w_exit += cost * frac
        sell_sol += sol_after
    share = (w_exit / w_total) if w_total > 0 else 0.0
    return {"exited_share": share, "wallets": per, "sell_sol_after": sell_sol,
            "fully_exited": sum(1 for x in per if x["exited_share"] >= 0.95), "holders_at_signal": len(per)}
