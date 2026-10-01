"""LIFECYCLE classification (Lifecycle-Aware Hunter V1). Never from age alone, never guessed.

  NEW             still on the Pump.fun bonding curve (Pump.fun record not complete AND the market source is the
                  curve pair), curve progress below the pre-migration level
  PRE_MIGRATION   still on the curve with RELIABLE progress >= cfg.premigration_progress_min (progress = 1 - remaining
                  real token reserves / initial, reported by Pump.fun, fresh)
  POST_MIGRATION  graduation confirmed (Pump.fun complete) AND an AMM pair (PumpSwap / Raydium / ...) is the market
  UNKNOWN         sources disagree (e.g. Pump.fun says curve but the market is an AMM pair, or complete but the
                  market is still the curve), or the state cannot be established -> no BUY

Confidence: HIGH (two sources agree, fresh) · MEDIUM (one authoritative source, the other silent) · LOW (stale /
partial) · UNKNOWN. Setup engines run only on HIGH / MEDIUM. Tokens that never had a Pump.fun curve (native AMM
launches) are POST_MIGRATION-like only with LOW confidence (no migration to anchor a second wave) -> not traded.
LifecycleTracker keeps the transition timestamps (discovery, NEW start, PRE start, migration, POST start); a
migration closes the NEW / PRE setups and starts a fresh POST state — pre-migration prices are never used as
post-migration K-lines.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

NEW, PRE_MIGRATION, POST_MIGRATION, UNKNOWN = "NEW", "PRE_MIGRATION", "POST_MIGRATION", "UNKNOWN"
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
CURVE_FRESH_S = 180            # Pump.fun record must be this fresh for HIGH confidence
AMM_DEXES = {"pumpswap", "raydium", "meteora", "orca", "raydium-clmm", "raydium-cp", "meteoradbc", "fluxbeam"}


@dataclass
class LifecycleInfo:
    lifecycle: str = UNKNOWN
    confidence: str = UNKNOWN
    source: str = ""
    timestamp: float = 0.0
    migration_status: str = "unknown"      # curve | near_migration | migrated | conflict | unknown
    migration_progress: float | None = None
    pair_type: str = ""                    # curve | amm | ""
    pair_address: str = ""
    market_source: str = ""
    reasons: list = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.lifecycle != UNKNOWN and self.confidence in (HIGH, MEDIUM)

    def as_dict(self) -> dict:
        return asdict(self) | {"active": self.active}


def classify(st, cfg, now: float | None = None) -> LifecycleInfo:
    now = now or time.time()
    m, info = st.market, st.info
    li = LifecycleInfo(timestamp=now)
    pump_known = info.pump_updated_at is not None or info.complete is not None
    pump_fresh = info.pump_updated_at is not None and now - info.pump_updated_at <= CURVE_FRESH_S
    li.migration_progress = info.curve_progress
    if m is not None:
        li.pair_address = m.pair_address or ""
        li.market_source = m.dex_id or ""
        li.pair_type = "curve" if m.is_curve else ("amm" if (m.dex_id or "").lower() in AMM_DEXES or m.pair_address else "")
    srcs = []
    if pump_known:
        srcs.append("pump.fun" + ("" if pump_fresh else " (stale)"))
    if m is not None:
        srcs.append(f"dexscreener:{m.dex_id or '?'}")
    li.source = " + ".join(srcs)

    if info.complete is True:
        if li.pair_type == "amm":
            li.lifecycle, li.migration_status = POST_MIGRATION, "migrated"
            li.confidence = HIGH
            li.reasons.append("Pump.fun complete + AMM pair active")
        elif li.pair_type == "curve":
            li.lifecycle, li.migration_status, li.confidence = UNKNOWN, "conflict", UNKNOWN
            li.reasons.append("conflict: Pump.fun complete but the market is still the curve pair")
        else:
            li.lifecycle, li.migration_status, li.confidence = POST_MIGRATION, "migrated", MEDIUM if m is None else LOW
            li.reasons.append("Pump.fun complete, AMM pair not visible yet")
        return li
    if info.complete is False:
        if li.pair_type == "amm":
            li.lifecycle, li.migration_status, li.confidence = UNKNOWN, "conflict", UNKNOWN
            li.reasons.append("conflict: Pump.fun curve not complete but the market is an AMM pair")
            return li
        p = info.curve_progress
        on_curve = li.pair_type == "curve"
        conf = HIGH if (on_curve and pump_fresh) else MEDIUM if (pump_fresh or on_curve) else LOW
        if p is not None and p >= cfg.premigration_progress_min:
            li.lifecycle, li.migration_status = PRE_MIGRATION, "near_migration"
            li.reasons.append(f"curve progress {p:.1f}% >= {cfg.premigration_progress_min:.0f}%")
            if not pump_fresh:
                conf = LOW                                   # progress must be reliable for PRE
                li.reasons.append("progress not fresh")
        else:
            li.lifecycle, li.migration_status = NEW, "curve"
            li.reasons.append("on bonding curve" + (f", progress {p:.1f}%" if p is not None else ", progress unknown"))
        li.confidence = conf
        return li
    # no Pump.fun record
    if li.pair_type == "curve":
        li.lifecycle, li.migration_status, li.confidence = NEW, "curve", MEDIUM
        li.reasons.append("curve pair on DexScreener, no Pump.fun record")
    elif li.pair_type == "amm":
        li.lifecycle, li.migration_status, li.confidence = POST_MIGRATION, "unknown", LOW
        li.reasons.append("AMM pair without bonding-curve history (no migration anchor)")
    else:
        li.reasons.append("no market / curve data")
    return li


@dataclass
class Transitions:
    discovery_ts: float | None = None
    new_start_ts: float | None = None
    premigration_start_ts: float | None = None
    migration_ts: float | None = None
    postmigration_start_ts: float | None = None
    post_pair: str = ""
    last: str = UNKNOWN
    history: list = field(default_factory=list)


class LifecycleTracker:
    """Per-CA lifecycle transitions. NEW -> PRE -> POST is kept, history is never reset; a migration closes the
    NEW / PRE setups (the caller is told via `migrated_now`) and pins the post-migration pair."""

    def __init__(self):
        self.t: dict[str, Transitions] = {}

    def update(self, st, li: LifecycleInfo, now: float) -> tuple[Transitions, bool]:
        tr = self.t.get(st.mint)
        if tr is None:
            tr = self.t[st.mint] = Transitions(discovery_ts=st.info.discovered_at or now)
        migrated_now = False
        if li.lifecycle == NEW and tr.new_start_ts is None:
            tr.new_start_ts = now
        elif li.lifecycle == PRE_MIGRATION and tr.premigration_start_ts is None:
            tr.premigration_start_ts = now
        elif li.lifecycle == POST_MIGRATION and tr.postmigration_start_ts is None:
            tr.postmigration_start_ts = now
            m = st.market
            # migration time: the AMM pair's creation when reported, else first time we saw it
            tr.migration_ts = (m.pair_created_at if m and m.pair_created_at else now)
            tr.post_pair = li.pair_address
            migrated_now = tr.last in (NEW, PRE_MIGRATION)
        if li.lifecycle != tr.last:
            tr.history.append((now, li.lifecycle))
            del tr.history[:-20]
            tr.last = li.lifecycle
        if len(self.t) > 30000:
            for k in list(self.t)[:5000]:
                self.t.pop(k, None)
        return tr, migrated_now
