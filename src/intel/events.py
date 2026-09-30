"""Realtime event detection on validated snapshots. Each (token, event type) fires at most once
per COOLDOWN_S. Events computed only when their inputs exist — never from UNKNOWN values.

  VOLUME_SPIKE        vol5m ≥ 3 × vol5m 5 min ago and vol5m ≥ $5K                      positive
  BUY_PRESSURE_SPIKE  B/S 5m ≥ 2.0 with ≥ 20 txns, and B/S 5 min ago < 1.3               positive
  HOLDER_SPIKE        holder count +15 % (and ≥ +20 holders) vs the snapshot ~5 min earlier positive
  WHALE_ENTRY         wallet crossed ≥ 1 % of supply between holder snapshots             info
  WHALE_EXIT          wallet fell below 1 % between holder snapshots                      warning
  DEV_SELL            verified dev token balance dropped ≥ 5 % between two RPC checks     warning
  LIQUIDITY_ADD       AMM liquidity +25 % vs the previous point (≤ 2 min earlier)         info
  LIQUIDITY_REMOVE    AMM liquidity −25 % vs the previous point (≤ 2 min earlier)         warning
                      (AMM pools with ≥ $5K only: a bonding curve has no LP that can be removed —
                       its reserve falls when people sell, which the price-crash rule covers)
  BREAKOUT            lifecycle entered BREAKOUT                                           positive
  DISTRIBUTION        lifecycle entered DISTRIBUTION                                       warning
  RUG_WARNING         AMM liquidity −50 % within 5 min, or price −60 % in 5 min,
                      or DEV_SELL to zero with price −40 % in 5 min                        critical
  DATA_BREAK          DexScreener pair changed (graduation / migration): pair-specific history restarts   info
  (volume / buy-pressure / liquidity events only compare snapshots of the SAME pair)
  SMART_MONEY_ENTRY / SOCIAL_SPIKE / NARRATIVE_SPIKE — NOT AVAILABLE (no data source yet)
"""
from __future__ import annotations

from core.models import Event, TokenState
from history.store import TokenHistory

COOLDOWN_S = 600
MIN_AMM_LIQ = 5_000
NOT_AVAILABLE_EVENTS = ("SMART_MONEY_ENTRY", "SOCIAL_SPIKE", "NARRATIVE_SPIKE")
ALL_EVENTS = ("VOLUME_SPIKE", "BUY_PRESSURE_SPIKE", "HOLDER_SPIKE", "WHALE_ENTRY", "WHALE_EXIT", "DEV_SELL",
              "LIQUIDITY_ADD", "LIQUIDITY_REMOVE", "BREAKOUT", "DISTRIBUTION", "RUG_WARNING",
              "DATA_BREAK") + NOT_AVAILABLE_EVENTS


class EventDetector:
    def __init__(self, cooldown_s: float = COOLDOWN_S):
        self.cooldown = cooldown_s
        self._last: dict[tuple[str, str], float] = {}
        self._lifecycle: dict[str, str] = {}
        self._breaks_seen: dict[str, float] = {}

    def forget(self, mint: str) -> None:
        self._lifecycle.pop(mint, None)
        self._breaks_seen.pop(mint, None)
        for k in [k for k in self._last if k[0] == mint]:
            del self._last[k]

    def detect(self, st: TokenState, h: TokenHistory, now: float) -> list[Event]:
        out: list[Event] = []
        sym = st.info.symbol or st.mint[:6]

        def emit(etype, severity, source, **params):
            key = (st.mint, etype)
            if now - self._last.get(key, -1e18) < self.cooldown:
                return
            self._last[key] = now
            out.append(Event(now, st.mint, sym, etype, severity, params, source))

        cur = h.latest()
        pair = cur.pair if cur and cur.pair else None
        p5 = h.at(300, now, tol=75, pair=pair)                  # same pair (D5)
        p5_token = h.at(300, now, tol=75)                       # token-level (price)
        prev = h.points[-2] if len(h.points) >= 2 else None
        if prev and pair and prev.pair != pair:
            prev = None
        dex = "DexScreener snapshots"
        if h.breaks and h.breaks[-1][0] > self._breaks_seen.get(st.mint, 0):
            ts_b, old, new = h.breaks[-1]
            self._breaks_seen[st.mint] = ts_b
            emit("DATA_BREAK", "info", "DexScreener pair history", old=old[:8], new=new[:8])
        if cur and p5:
            if cur.vol_5m is not None and p5.vol_5m and cur.vol_5m >= 5_000 and cur.vol_5m >= 3 * p5.vol_5m:
                emit("VOLUME_SPIKE", "positive", dex, before=p5.vol_5m, now=cur.vol_5m)
            if cur.bs is not None and (cur.txns_5m or 0) >= 20 and cur.bs >= 2.0 and p5.bs is not None and p5.bs < 1.3:
                emit("BUY_PRESSURE_SPIKE", "positive", dex, before=round(p5.bs, 2), now=round(cur.bs, 2))
        if cur and p5_token and cur.price and p5_token.price and cur.price <= 0.4 * p5_token.price:
            emit("RUG_WARNING", "critical", dex, reason="price_crash",
                 change=round(100 * (cur.price / p5_token.price - 1), 1))
        amm = cur is not None and cur.liq_src == "dexscreener_amm"
        if amm and prev and cur.liq is not None and prev.liq and prev.liq >= MIN_AMM_LIQ and cur.ts - prev.ts <= 120                 and cur.liq_src == prev.liq_src:
            ch = 100 * (cur.liq / prev.liq - 1)
            if ch >= 25:
                emit("LIQUIDITY_ADD", "info", dex, change=round(ch, 1), now=cur.liq)
            elif ch <= -25:
                emit("LIQUIDITY_REMOVE", "warning", dex, change=round(ch, 1), now=cur.liq)
        liq_pts = [p for p in h.points if p.ts >= now - 300 and p.liq is not None and p.liq_src == "dexscreener_amm"
                   and (pair is None or p.pair == pair)]
        if amm and len(liq_pts) >= 2 and max(p.liq for p in liq_pts) >= MIN_AMM_LIQ and cur.liq is not None                 and cur.liq <= 0.5 * max(p.liq for p in liq_pts):
            emit("RUG_WARNING", "critical", dex, reason="liquidity_drop",
                 change=round(100 * (cur.liq / max(p.liq for p in liq_pts) - 1), 1))

        # holders / whales
        if len(h.holders) >= 2:
            hc = h.holders[-1]
            hp = h.holders_at(300, hc.ts, tol=150)
            if hp and hp is not hc and hc.count and hp.count:
                g = 100 * (hc.count / hp.count - 1)
                if g >= 15 and hc.count - hp.count >= 20:
                    emit("HOLDER_SPIKE", "positive", st.holders.source if st.holders else "holders",
                         before=hp.count, now=hc.count)
        wi = st.whale_intel
        if wi and wi.window_min is not None:
            if wi.entries:
                emit("WHALE_ENTRY", "info", "holder snapshots", wallets=len(wi.entries), first=wi.entries[0])
            if wi.exits:
                emit("WHALE_EXIT", "warning", "holder snapshots", wallets=len(wi.exits), first=wi.exits[0])

        # dev
        if len(h.dev_balances) >= 2:
            (t0, b0), (t1, b1) = h.dev_balances[-2], h.dev_balances[-1]
            if b0 > 0 and b1 <= 0.95 * b0:
                emit("DEV_SELL", "warning", "Solana RPC", before=b0, now=b1)
                if b1 <= 0 and cur and p5_token and cur.price and p5_token.price and cur.price <= 0.6 * p5_token.price:
                    emit("RUG_WARNING", "critical", "Solana RPC + DexScreener", reason="dev_exit",
                         change=round(100 * (cur.price / p5_token.price - 1), 1))

        # lifecycle transitions
        before = self._lifecycle.get(st.mint)
        if st.lifecycle != before:
            if st.lifecycle == "BREAKOUT":
                emit("BREAKOUT", "positive", "lifecycle")
            elif st.lifecycle == "DISTRIBUTION":
                emit("DISTRIBUTION", "warning", "lifecycle")
            self._lifecycle[st.mint] = st.lifecycle
        return out
