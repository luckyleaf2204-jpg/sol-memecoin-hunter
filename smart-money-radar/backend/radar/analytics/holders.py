"""Holder distribution and trading-flow quality from per-wallet flows (radar.services.stream).

Estimate: holdings(wallet) = tokens bought - tokens sold on the curve / PumpSwap. It is complete
only when the stream saw the token from its creation without a gap (`complete=True`); token
transfers between wallets are NOT included — the UI labels it "ước tính".

Heuristics here (unnatural volume) are flags for a human, not proven rules; they add at most one
risk point and are reported with their numbers.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class Flow:
    wallet: str
    bought: float          # UI tokens
    sold: float
    buy_sol: float
    sell_sol: float
    fees_sol: float
    n_buys: int
    n_sells: int

    @property
    def holding(self) -> float:
        return max(0.0, self.bought - self.sold)


def estimate_holders(flows: Iterable[Flow], supply_ui: float, exclude: set[str] | None = None,
                     top: int = 20) -> list[dict[str, Any]]:
    exclude = exclude or set()
    rows = [f for f in flows if f.wallet not in exclude and f.holding > 0]
    rows.sort(key=lambda f: -f.holding)
    return [{"wallet": f.wallet, "tokens": f.holding, "pct": 100 * f.holding / supply_ui if supply_ui else None,
             "buy_sol": f.buy_sol, "sell_sol": f.sell_sol} for f in rows[:top]]


def linked_groups(holders: list[dict[str, Any]], funders: dict[str, str | None],
                  exchange_fanout: int = 20, funder_children: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Holders that share the same first funder (an exchange-like funder is ignored)."""
    by_f: dict[str, list[dict]] = defaultdict(list)
    for h in holders:
        f = funders.get(h["wallet"])
        if f and f != h["wallet"]:
            if funder_children and funder_children.get(f, 0) >= exchange_fanout:
                continue
            by_f[f].append(h)
    groups = []
    for f, hs in by_f.items():
        if len(hs) >= 2:
            groups.append({"funder": f, "wallets": [h["wallet"] for h in hs],
                           "pct": sum(h["pct"] or 0 for h in hs)})
    # a funder that is itself a top holder links its children to it
    holder_set = {h["wallet"]: h for h in holders}
    for f, hs in by_f.items():
        if f in holder_set and len(hs) == 1:
            groups.append({"funder": f, "wallets": [f, hs[0]["wallet"]],
                           "pct": (holder_set[f]["pct"] or 0) + (hs[0]["pct"] or 0)})
    groups.sort(key=lambda g: -g["pct"])
    return groups


def flow_metrics(flows: list[Flow]) -> dict[str, Any]:
    """Quality of the trading flow: real participants vs churn."""
    traders = [f for f in flows if f.n_buys + f.n_sells > 0]
    vol = sum(f.buy_sol + f.sell_sol for f in traders)
    buyers = [f for f in traders if f.n_buys > 0]
    if not traders or vol <= 0:
        return {"traders": len(traders), "volume_sol": vol}
    # round trippers: sold >= 90 % of what they bought
    rt = [f for f in buyers if f.bought > 0 and f.sold >= 0.9 * f.bought]
    rt_vol = sum(f.buy_sol + f.sell_sol for f in rt)
    avg_sizes = [f.buy_sol / f.n_buys for f in buyers if f.n_buys]
    med = statistics.median(avg_sizes) if avg_sizes else None
    uniform = (sum(1 for x in avg_sizes if med and abs(x - med) <= 0.03 * med) / len(avg_sizes)) if avg_sizes else None
    net = [f.buy_sol - f.sell_sol for f in buyers]
    return {"traders": len(traders), "buyers": len(buyers), "volume_sol": vol,
            "volume_per_trader_sol": vol / len(traders),
            "round_trip_wallet_share": len(rt) / len(buyers) if buyers else None,
            "round_trip_volume_share": rt_vol / vol,
            "median_buy_size_sol": med, "size_uniformity": uniform,
            "median_net_position_sol": statistics.median(net) if net else None,
            "fees_sol": sum(f.fees_sol for f in traders)}


def unnatural_volume(m: dict[str, Any], mc_sol: float | None) -> tuple[bool, list[str]]:
    """Large volume carried by small, uniform, round-tripping positions (bot volume)."""
    reasons = []
    if not m.get("buyers") or not mc_sol:
        return False, reasons
    vol_mc = m["volume_sol"] / mc_sol
    signs = []
    if (m.get("round_trip_volume_share") or 0) >= 0.6:
        signs.append(f"{m['round_trip_volume_share']:.0%} of volume from wallets that already sold everything")
    if (m.get("size_uniformity") or 0) >= 0.5 and m["buyers"] >= 10:
        signs.append(f"{m['size_uniformity']:.0%} of buyers use the same buy size")
    if m.get("median_net_position_sol") is not None and \
            abs(m["median_net_position_sol"]) < 0.05 * (m["volume_per_trader_sol"] or 1):
        signs.append("typical wallet keeps almost no position after trading")
    reasons = [f"volume = {vol_mc:.1f}x market cap"] + signs
    # large volume relative to the market cap AND at least two churn signs
    return (vol_mc >= 2 and len(signs) >= 2), reasons
