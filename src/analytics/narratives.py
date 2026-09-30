"""Narrative aggregates over tokens the scanner actually tracked (real Pump.fun / DexScreener data).

Per keyword tag: tokens tracked, tokens created in the last 30 min vs the 30 min before (trend),
summed 5m volume of validated tokens, and the tokens with most 5m volume. This is DATA, not a
narrative score — X/Telegram narrative strength is NOT AVAILABLE until a social source exists.
"""
from __future__ import annotations

import time

from core.models import INVALID, TokenState


def aggregate_narratives(states: list[TokenState], now: float | None = None) -> list[dict]:
    now = now or time.time()
    agg: dict[str, dict] = {}
    for st in states:
        for tag in st.narratives or []:
            a = agg.setdefault(tag, {"tag": tag, "tokens": 0, "new_30m": 0, "prev_30m": 0, "vol_5m": 0.0,
                                     "valid_tokens": 0, "top": []})
            a["tokens"] += 1
            created = st.info.created_at
            if created:
                if now - created <= 1800:
                    a["new_30m"] += 1
                elif now - created <= 3600:
                    a["prev_30m"] += 1
            if st.dq_status != INVALID and st.market and st.market.vol_5m:
                a["vol_5m"] += st.market.vol_5m
                a["valid_tokens"] += 1
                a["top"].append((st.market.vol_5m, st.info.symbol))
    out = []
    for a in agg.values():
        a["top"] = [s for _, s in sorted(a["top"], reverse=True)[:5]]
        a["launch_trend"] = (a["new_30m"] / a["prev_30m"]) if a["prev_30m"] else None
        out.append(a)
    return sorted(out, key=lambda x: -x["vol_5m"])
