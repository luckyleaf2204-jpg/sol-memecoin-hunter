"""Whale intelligence: wallets holding ≥ 1 % of supply (curve / pool / burn excluded).

Compares the two most recent holder snapshots at least 2 minutes apart:
  delta = Σ over wallets that were whales in EITHER snapshot of (balance_now − balance_before), in % of supply
  ACCUMULATION  delta ≥ +1 % of supply
  DISTRIBUTION  delta ≤ −1 % of supply
  NEUTRAL       otherwise
  UNKNOWN       fewer than two snapshots / no supply
Entries / exits = wallets crossing the 1 % threshold between the snapshots.
Works with the top-20 RPC fallback too (whales are always inside the top 20 of a concentrated token).
"""
from __future__ import annotations

from core.models import TokenState, WhaleIntel
from history.store import TokenHistory
from intel.metrics import MetricBuilder

WHALE_PCT = 1.0
FLOW_PCT = 1.0


def analyze_whales(st: TokenState, h: TokenHistory, M: MetricBuilder) -> WhaleIntel:
    wi = WhaleIntel()
    supply = st.info.total_supply
    hs = st.holders
    src = hs.source if hs else "Helius DAS"
    ts = hs.fetched_at if hs else None
    if hs and supply and h.holders:
        cur = h.holders[-1]
        pct = {o: 100 * a / supply for o, a in cur.amounts.items()}
        whales = {o for o, p in pct.items() if p >= WHALE_PCT}
        wi.whale_count, wi.whale_pct = len(whales), round(sum(pct[o] for o in whales), 2)
        wi.holder_count = cur.count
        prev = h.previous_holders(120)
        if prev:
            ppct = {o: 100 * a / supply for o, a in prev.amounts.items()}
            pwhales = {o for o, p in ppct.items() if p >= WHALE_PCT}
            union = whales | pwhales
            wi.delta_pct = round(sum(pct.get(o, 0.0) - ppct.get(o, 0.0) for o in union), 3)
            wi.window_min = (cur.ts - prev.ts) / 60
            wi.holder_count = cur.count
            wi.holder_increase = (cur.count - prev.count) if cur.count is not None and prev.count is not None else None
            wi.entries = sorted(whales - pwhales)
            wi.exits = sorted(pwhales - whales)
            wi.state = "ACCUMULATION" if wi.delta_pct >= FLOW_PCT else \
                "DISTRIBUTION" if wi.delta_pct <= -FLOW_PCT else "NEUTRAL"
    M.add("whale_state", wi.state if wi.state != "UNKNOWN" else None, "text", "whales", src, ts,
          derived=True, note="needs_holder_history")
    from intel.holders import holder_reason
    why = holder_reason(st) if not hs else "needs_holder_history"
    M.add("whale_count", wi.whale_count, "int", "whales", src, ts, derived=True, note=why)
    M.add("whale_pct", wi.whale_pct, "pct", "whales", src, ts, derived=True, note=why)
    M.add("whale_delta", wi.delta_pct, "pct", "whales", src, ts, derived=True, note="needs_holder_history")
    M.add("whale_entries", len(wi.entries) if wi.window_min is not None else None, "int", "whales", src, ts,
          derived=True, note="needs_holder_history")
    M.add("whale_exits", len(wi.exits) if wi.window_min is not None else None, "int", "whales", src, ts,
          derived=True, note="needs_holder_history")
    M.unknown("whale_cluster", "text", "whales", "not_implemented")
    return wi
