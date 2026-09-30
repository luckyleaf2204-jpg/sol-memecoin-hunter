"""Controlled Early-Signal scenarios run through the REAL pipeline (validation -> history -> intel -> scores).

Each scenario: 20 min of quiet baseline, a snapshot 10 min ago, one 5 min ago, and "now".
Values are (10m ago, 5m ago, now). Holder snapshots are added at the same moments.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from conftest import SOL_USD, dex_pair, default_info, good_dev
from core.config import Settings
from core.models import HolderStats, TokenState
from dex.dexscreener import parse_pair
from history.store import HolderSnap, TokenHistory
from scanner.pipeline import evaluate, ingest_market


@dataclass
class Scenario:
    name: str
    description: str
    vol5: tuple            # 5m volume at (-10m, -5m, now)
    buys: tuple
    sells: tuple
    mc: tuple
    liq: tuple
    holders: tuple         # holder counts at (-10m, -5m, now)
    top10: tuple = (12.0, 12.0, 12.0)
    smart_money_wallets: int = 0     # requested input; module NOT AVAILABLE -> cannot be represented
    notes: list = field(default_factory=list)


SCENARIOS = [
    Scenario("S1", "Volume +400%, holders +150%, B/S 2.5, liquidity +30%, transactions +300%, 3 smart-money wallets",
             vol5=(2_000, 2_000, 10_000), buys=(40, 40, 229), sells=(40, 40, 91), mc=(60_000, 60_000, 78_000),
             liq=(40_000, 40_000, 52_000), holders=(96, 100, 250), smart_money_wallets=3),
    Scenario("S2", "Price +100% but volume, holders and liquidity flat",
             vol5=(2_000, 2_000, 2_000), buys=(40, 40, 40), sells=(40, 40, 40), mc=(60_000, 60_000, 120_000),
             liq=(40_000, 40_000, 40_000), holders=(100, 100, 100)),
    Scenario("S3", "Volume +500% but liquidity -50% and top-holder concentration rising",
             vol5=(2_000, 2_000, 12_000), buys=(40, 40, 200), sells=(40, 40, 100), mc=(60_000, 60_000, 78_000),
             liq=(40_000, 40_000, 20_000), holders=(100, 100, 100), top10=(20.0, 20.0, 55.0)),
    Scenario("S4", "Volume +300%, holders +100%, liquidity +50%, buy/sell +2.0, many new wallets, no major risk",
             vol5=(2_000, 2_000, 8_000), buys=(60, 60, 240), sells=(60, 60, 80), mc=(60_000, 60_000, 75_000),
             liq=(40_000, 40_000, 60_000), holders=(98, 100, 200)),
]


def _pair(sc: Scenario, i: int):
    mc = sc.mc[i]
    return dex_pair(mint=sc.name, vol=(sc.vol5[i], 40_000), m5=(sc.buys[i], sc.sells[i]), mc=mc, fdv=mc,
                    price=f"{mc / 1e9:.12f}", liq=sc.liq[i], pc5=0.0)


def _owners(n: int, top10_pct: float) -> dict:
    """n wallets whose top 10 hold `top10_pct` % of a 1B supply."""
    top = {f"w{i}": top10_pct / 100 * 1e9 / 10 for i in range(min(10, n))}
    rest = {f"w{i}": 1_000_000.0 for i in range(10, n)}
    return top | rest


def run(sc: Scenario, now: float | None = None) -> TokenState:
    now = now or time.time()
    st = TokenState(info=default_info(sc.name, age_s=3 * 3600, symbol=sc.name))
    st.dev = good_dev()
    h = TokenHistory()
    s = Settings()
    # quiet baseline 30 -> 12 min ago (same values as the -10m snapshot)
    for ago in range(1800, 660, -120):
        ingest_market(st, parse_pair(_pair(sc, 0)), {}, SOL_USD, h, now - ago)
    for i, ago in enumerate((600, 300, 0)):
        ts = now - ago
        h.add_holders(HolderSnap(ts, sc.holders[i], _owners(sc.holders[i], sc.top10[i]), True))
        st.holders = HolderStats(holder_count=sc.holders[i], top10_pct=sc.top10[i], top5_pct=sc.top10[i] * 0.6,
                                 top20_pct=sc.top10[i] + 5, max_single_pct=sc.top10[i] / 10, source="helius_das",
                                 complete_list=True, owner_amounts=_owners(sc.holders[i], sc.top10[i]), fetched_at=ts)
        ingest_market(st, parse_pair(_pair(sc, i)), {}, SOL_USD, h, ts)
        evaluate(st, s, h, ts, SOL_USD)
    if sc.smart_money_wallets:
        sc.notes.append(f"{sc.smart_money_wallets} smart-money wallets requested: SMART MONEY module NOT AVAILABLE "
                        "-> cannot be represented; weight 0, excluded from the denominator")
    return st


def contribution_table(st: TokenState) -> list[tuple]:
    e = st.early
    denom = sum(x.weight for x in e.signals if x.fired is not None)
    rows = []
    for x in e.signals:
        if x.fired is None:
            rows.append((x.key, "UNKNOWN", x.weight, None, 0.0, x.raw.get("missing") or x.note))
        else:
            pts = round(100 * x.weight * (x.strength or 0) / denom, 1) if denom else 0.0
            rows.append((x.key, "FIRED" if x.fired else "not fired", x.weight, x.strength, pts if x.fired else 0.0,
                         x.value))
    return rows
