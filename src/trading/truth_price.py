"""TRUTH PRICE LAYER (V1.3) — research / SHADOW only. Nothing here changes a BUY, an exit or the production book.

Question it answers: "when the bot says +10 % or -20 %, could the position actually have exited at that price?"

Three prices, never merged, never silently substituted:
  A. mark_price               DexScreener priceUsd — discovery / chart / context. NOT authoritative for P&L
                              (V1.2: fetch-time age P50 10 s, sometimes 0.65x-3.3x away from on-chain executable).
  B. entry_fill_price         Jupiter BUY quote x (1 + simulated latency slip) — the paper fill (unchanged).
  C. executable_exit_price    Jupiter SELL quote for the EXACT position size (token -> SOL) x SOL price. Size, route and
                              liquidity dependent: it is what THIS position could have been sold for, not "the" price.
Optional: curve_price          Pump.fun classic bonding-curve spot from reserves (virtual SOL / virtual tokens), only
                              while the token is on the curve, the quote asset is SOL and the reserves are fresh.

executable_pnl_pct = (executable_exit_price - entry_fill_price) / entry_fill_price x 100   -> TRUTH_PNL_CANDIDATE
(no fee is invented: pool fees are inside Jupiter's outAmount; network / priority fees are not modelled here).

No look-ahead: a snapshot is only accepted at or after the entry and in time order; production code never reads a
tracker. Fast-SL / MFE / MAE at offset t use only snapshots taken at or before entry + t.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from trading.price_provenance import WSOL, CommonSourceExit, amm_keys

VALID, STALE, NO_ROUTE, ERROR, MISMATCH, UNKNOWN = "VALID", "STALE", "NO_ROUTE", "ERROR", "MISMATCH", "UNKNOWN"
CONSISTENT, STALE_DS, DS_MISMATCH, TRUTH_VALID, TRUTH_UNAVAILABLE = (
    "CONSISTENT", "STALE_DS", "DS_MISMATCH", "TRUTH_VALID", "TRUTH_UNAVAILABLE")
MIGRATION_PRICE_DISCONTINUITY = "MIGRATION_PRICE_DISCONTINUITY"
TRUTH_PNL_LABEL = "TRUTH_PNL_CANDIDATE"          # not "REAL P&L" until validated (>= 30 completed trades)

TRUTH_STALE_S = 30.0         # an executable quote older than this no longer values the position
DS_STALE_S = 5.0             # a DexScreener print at least this old (fetch-time lower bound) is stale
AGREE_TOL = 0.05             # |source / truth mid - 1| below this = agreement
CURVE_MAX_AGE_S = 30.0       # Pump.fun reserves older than this -> curve price UNKNOWN
CLASSIC_VIRTUAL_TOKEN_OFFSET = 279_900_000      # classic curve: virtual tokens = real tokens + 279.9 M
SOL_QUOTES = ("", "11111111111111111111111111111111", WSOL)
FAST_SL_OFFSETS = (5, 10, 15, 30)
JUMP_FLAG_PCT = 10.0         # |price jump| across a pair change above this -> MIGRATION_PRICE_DISCONTINUITY

STATUS_OF = {"OK": VALID, "NO_ROUTE": NO_ROUTE, "INVALID": MISMATCH, "RATE_LIMITED": ERROR, "TIMEOUT": ERROR,
             "API_ERROR": ERROR, "COOLDOWN": ERROR}


def pct(a: float | None, b: float | None) -> float | None:
    return None if not a or not b else round(100 * (a / b - 1), 3)


@dataclass
class TruthPriceSnapshot:
    timestamp: float
    token_ca: str
    position_size: float                      # tokens quoted (UI units)
    source: str = "jupiter_sell_quote"
    price_usd: float | None = None            # executable_exit_price
    sol_out: float | None = None
    price_impact_pct: float | None = None
    route: str = ""
    route_pool: str = ""
    context_slot: int | None = None
    quote_age_ms: float | None = 0.0          # age when recorded (re-evaluated by is_fresh)
    latency_ms: float | None = None
    source_status: str = UNKNOWN
    confidence: int | None = None
    reason: str = ""
    # context observed at the SAME moment (never later)
    trade_id: str = ""
    lifecycle: str | None = None
    pair_address: str = ""
    pair_identity: str = ""
    migration_state: str = "UNKNOWN"
    entry_fill_price: float | None = None
    ds_price: float | None = None
    ds_age_s: float | None = None
    ds_vs_truth_pct: float | None = None
    ds_class: str = TRUTH_UNAVAILABLE
    curve_price: float | None = None
    curve_status: str = UNKNOWN
    curve_vs_truth_pct: float | None = None
    ds_vs_curve_pct: float | None = None
    truth_pnl_pct: float | None = None        # executable_pnl_pct
    old_pnl_pct: float | None = None          # production (DexScreener-mark) unrealized P&L at the same moment
    epoch: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def mid_price(self) -> float | None:
        """Executable price grossed up by Jupiter's own price impact — comparable with a mid / spot print."""
        if self.price_usd is None:
            return None
        imp = (self.price_impact_pct or 0.0) / 100
        return self.price_usd / (1 - imp) if imp < 1 else None


def snapshot_from_quote(result, *, mint: str, tokens: float, sol_price: float | None, t_req: float,
                        t_resp: float) -> TruthPriceSnapshot:
    """QuoteResult (trading.jupiter) of a SELL quote token -> SOL for `tokens` -> snapshot. Never guesses."""
    s = TruthPriceSnapshot(timestamp=t_resp, token_ca=mint, position_size=tokens, latency_ms=1000 * (t_resp - t_req))
    status = getattr(result, "status", None)
    s.source_status = STATUS_OF.get(status, UNKNOWN)
    if s.source_status != VALID:
        s.reason = (result.label() if hasattr(result, "label") else str(status))[:160]
        return s
    q = result.quote or {}
    if q.get("inputMint") != mint or q.get("outputMint") != WSOL:
        s.source_status, s.reason = MISMATCH, "quote is not token -> SOL for this CA"
        return s
    try:
        sol_out = int(q["outAmount"]) / 1e9
    except (KeyError, TypeError, ValueError):
        s.source_status, s.reason = ERROR, "unparseable outAmount"
        return s
    keys = amm_keys(q)
    labels = [((r.get("swapInfo") or {}).get("label") or "?") for r in q.get("routePlan") or []]
    s.sol_out, s.route, s.route_pool = sol_out, " > ".join(labels), keys[0] if keys else ""
    try:
        s.price_impact_pct = round(100 * float(q.get("priceImpactPct")), 4)
    except (TypeError, ValueError):
        s.price_impact_pct = None
    try:
        s.context_slot = int(q["contextSlot"])
    except (KeyError, TypeError, ValueError):
        s.context_slot = None
    if not sol_price:
        s.source_status, s.reason = UNKNOWN, "no SOL price: USD value not computed"
        return s
    if not tokens or sol_out <= 0:
        s.source_status, s.reason = NO_ROUTE, "zero output"
        return s
    s.price_usd = sol_out * sol_price / tokens
    return s


def is_fresh(snap: dict | TruthPriceSnapshot, now: float) -> bool:
    ts = snap["timestamp"] if isinstance(snap, dict) else snap.timestamp
    st = snap["source_status"] if isinstance(snap, dict) else snap.source_status
    return st == VALID and 0 <= now - ts <= TRUTH_STALE_S


def curve_spot(info, sol_price: float | None, now: float) -> tuple[float | None, str, str]:
    """Pump.fun CLASSIC curve spot (USD) from reserves, or (None, status, why). Not a market after migration."""
    if info is None or sol_price is None:
        return None, UNKNOWN, "no token info / SOL price"
    if info.complete:
        return None, UNKNOWN, "migrated: curve is not the market"
    if (info.quote_mint or "") not in SOL_QUOTES:
        return None, UNKNOWN, "curve quote asset is not SOL"
    if info.mayhem_state:
        return None, UNKNOWN, "mayhem curve: reserves model not verified"
    vs, rt = info.virtual_sol_reserves, info.real_token_reserves
    if not vs or rt is None:
        return None, UNKNOWN, "reserves unknown"
    if info.pump_updated_at is None or now - info.pump_updated_at > CURVE_MAX_AGE_S:
        return None, STALE, f"reserves {now - (info.pump_updated_at or 0):.0f}s old"
    return vs / (rt + CLASSIC_VIRTUAL_TOKEN_OFFSET) * sol_price, VALID, ""


def compare_sources(s: TruthPriceSnapshot) -> TruthPriceSnapshot:
    """DexScreener and curve vs the executable truth (mid = executable grossed up by Jupiter impact). Mismatches are
    recorded as they are — no source is chosen because it gives a nicer P&L."""
    mid = s.mid_price if s.source_status == VALID else None
    if mid is None:
        s.ds_class = TRUTH_UNAVAILABLE
    elif s.ds_price is None:
        s.ds_class = TRUTH_VALID
    else:
        s.ds_vs_truth_pct = pct(s.ds_price, mid)
        if abs(s.ds_price / mid - 1) <= AGREE_TOL:
            s.ds_class = CONSISTENT
        elif s.ds_age_s is not None and s.ds_age_s >= DS_STALE_S:
            s.ds_class = STALE_DS
        else:
            s.ds_class = DS_MISMATCH
    if s.curve_price is not None:
        s.curve_vs_truth_pct = pct(s.curve_price, mid) if mid else None
        s.ds_vs_curve_pct = pct(s.ds_price, s.curve_price)
    return s


def confidence(s: TruthPriceSnapshot, pair_changed: bool) -> int:
    """PRICE TRUTH CONFIDENCE 0-100 — data quality only (research). Never used by BUY / risk / exits.
    Weighted over the components that are applicable (curve agreement only when a curve price exists)."""
    if s.source_status != VALID:
        return 0
    parts = [(20, (s.quote_age_ms or 0) <= 5000 and (s.latency_ms or 0) <= 2000),
             (20, True),                                                   # Jupiter success
             (15, bool(s.route) and bool(s.route_pool)),                   # route validity
             (10, s.context_slot is not None),
             (10, bool(s.pair_address) and s.route_pool == s.pair_address),   # route pool == market pair
             (10, not pair_changed and s.migration_state != "MIGRATING"),
             (15, s.ds_class == CONSISTENT)]
    if s.curve_price is not None and s.curve_vs_truth_pct is not None:
        parts.append((5, abs(s.curve_vs_truth_pct) <= 100 * AGREE_TOL))
    tot = sum(w for w, _ in parts)
    return round(100 * sum(w for w, ok in parts if ok) / tot)


def map_reason(reason: str | None) -> str:
    """Exit reason -> TP / SL / TRAILING / RISK / LIQUIDITY / NO_ROUTE / STALE / NONE (raw reason kept separately)."""
    r = (reason or "").replace("mirror:", "")
    if r in ("stop_loss", "break_even_stop"):
        return "SL"
    if r in ("take_profit_1", "take_profit_2"):
        return "TP"
    if r == "trailing_stop":
        return "TRAILING"
    if r == "liquidity_collapse":
        return "LIQUIDITY"
    if r in ("risk_spike", "whale_dump", "holder_anomaly", "identity_conflict", "momentum_deterioration",
             "volume_collapse"):
        return "RISK"
    if r in (NO_ROUTE, STALE):
        return r
    return "NONE"


@dataclass
class TruthTracker:
    """One paper trade: executable SELL-quote snapshots for the shadow position's own size, the TRUTH exit shadow
    (production price rules applied to executable prices from the entry fill), truth MFE / MAE, price epochs."""
    trade_id: str
    mint: str
    symbol: str
    lifecycle: str | None
    entry_fill: float
    entry_ts: float
    tokens: float
    sl_pct: float
    tp1_pct: float
    tp1_frac: float
    tp2_pct: float
    trailing_pct: float
    max_hold_s: float
    pair: str = ""
    dex: str = ""
    entry_quote_price: float | None = None
    execution_impact_pct: float | None = None
    latency_model_pct: float | None = None
    snapshots: list = field(default_factory=list)
    status_counts: dict = field(default_factory=dict)
    epochs: list = field(default_factory=list)
    pair_changes: list = field(default_factory=list)
    last_quote: float = 0.0
    rejected: int = 0
    mfe: float | None = None
    mae: float | None = None
    t_mfe: float | None = None
    t_mae: float | None = None
    old_exit: dict | None = None
    pending_mirror: tuple | None = None
    production_open: bool = True              # keep quoting while production holds: values its exit timing too

    def __post_init__(self):
        self.exit = CommonSourceExit(self.entry_fill, self.entry_ts, self.sl_pct, self.tp1_pct, self.tp1_frac,
                                     self.tp2_pct, self.trailing_pct, self.max_hold_s)
        self.epochs.append({"epoch": 0, "pair": self.pair, "dex": self.dex, "start": self.entry_ts})

    # ------------------------------------------------------------------ sizing / cadence
    def size_now(self) -> float:
        return self.tokens * (1 - self.exit.realized_frac)

    def due(self, now: float) -> bool:
        if self.exit.closed_at is not None and self.pending_mirror is None and not self.production_open:
            return False
        if now - self.entry_ts > 1800:
            return False
        every = 5.0 if now - self.entry_ts <= 60 else 15.0          # fast-SL research needs 5 s resolution early
        return now - self.last_quote >= every

    # ------------------------------------------------------------------ ingest
    def add(self, s: TruthPriceSnapshot) -> dict | None:
        """Accept a snapshot taken at/after the entry and after the previous one (no look-ahead / back-fill).
        Returns the pair-change event when the market pair changed (new price epoch)."""
        if s.timestamp < self.entry_ts or (self.snapshots and s.timestamp < self.snapshots[-1]["timestamp"]):
            self.rejected += 1
            return None
        event = None
        cur = self.epochs[-1]
        if s.pair_address and cur["pair"] and s.pair_address != cur["pair"]:
            before = next((x["ds_price"] for x in reversed(self.snapshots) if x.get("ds_price")), None)
            jump = pct(s.ds_price, before)
            event = {"trade_id": self.trade_id, "token_ca": self.mint, "timestamp": s.timestamp,
                     "epoch": cur["epoch"] + 1, "old_pair": cur["pair"], "new_pair": s.pair_address,
                     "old_dex": cur["dex"], "new_dex": s.pair_identity.split(":")[0],
                     "price_before": before, "price_after": s.ds_price, "jump_pct": jump,
                     "flag": MIGRATION_PRICE_DISCONTINUITY if jump is not None and abs(jump) >= JUMP_FLAG_PCT
                     else "PAIR_CHANGE"}
            self.pair_changes.append(event)
            self.epochs.append({"epoch": cur["epoch"] + 1, "pair": s.pair_address,
                                "dex": s.pair_identity.split(":")[0], "start": s.timestamp})
        elif s.pair_address and not cur["pair"]:
            cur["pair"], cur["dex"] = s.pair_address, s.pair_identity.split(":")[0]
        s.epoch = self.epochs[-1]["epoch"]
        s.trade_id, s.entry_fill_price = self.trade_id, self.entry_fill
        s.confidence = confidence(s, bool(self.pair_changes))
        self.status_counts[s.source_status] = self.status_counts.get(s.source_status, 0) + 1
        if s.source_status == VALID and s.price_usd:
            s.truth_pnl_pct = pct(s.price_usd, self.entry_fill)
            t = s.timestamp - self.entry_ts
            if self.mfe is None or s.truth_pnl_pct > self.mfe:     # over the life of production AND truth shadow
                self.mfe, self.t_mfe = s.truth_pnl_pct, round(t, 1)
            if self.mae is None or s.truth_pnl_pct < self.mae:
                self.mae, self.t_mae = s.truth_pnl_pct, round(t, 1)
            if self.exit.closed_at is None:
                if self.pending_mirror is not None:
                    self.exit.force_close(s.price_usd, s.timestamp, "mirror:" + self.pending_mirror[1])
                else:
                    self.exit.on_price(s.price_usd, s.timestamp)
            if self.exit.closed_at is not None:
                self.pending_mirror = None
        self.snapshots.append(s.as_dict())
        del self.snapshots[:-400]
        return event

    def on_production_exit(self, reason: str, price: float | None, pnl_pct: float | None, ts: float,
                           fill_source: str, ds_mark: dict | None) -> None:
        self.production_open = False
        self.old_exit = {"reason": reason, "price": price, "pnl_pct": pnl_pct, "ts": ts, "fill_source": fill_source,
                         "held_s": round(ts - self.entry_ts, 1), "ds_mark": ds_mark}
        if self.exit.closed_at is None and reason not in ("stop_loss", "break_even_stop", "take_profit_1",
                                                          "take_profit_2", "trailing_stop", "max_hold",
                                                          "max_hold_time"):
            self.pending_mirror = (ts, reason)          # valued on the next executable quote

    # ------------------------------------------------------------------ research views (no look-ahead)
    def valid_at(self, t: float, window_s: float = TRUTH_STALE_S) -> dict | None:
        """Latest VALID snapshot taken at or before t and not older than window_s."""
        for x in reversed(self.snapshots):
            if x["timestamp"] <= t:
                if x["source_status"] == VALID and t - x["timestamp"] <= window_s:
                    return x
                if x["source_status"] == VALID:
                    return None
        return None

    def fast_sl(self) -> dict:
        out = {}
        for off in FAST_SL_OFFSETS:
            x = self.valid_at(self.entry_ts + off, window_s=max(5.0, off))
            out[f"{off}s"] = None if x is None else {"truth_pnl_pct": x["truth_pnl_pct"],
                                                    "sl_hit": x["truth_pnl_pct"] <= -self.sl_pct}
        return out

    def classify_old_exit(self) -> str | None:
        """Production SL within 30 s -> real executable loss / stale-price artifact / migration artifact /
        liquidity-route failure (UNVERIFIED when no executable quote exists around it)."""
        o = self.old_exit
        if not o or o["reason"] != "stop_loss" or o["held_s"] > 30:
            return None
        if any(c["timestamp"] <= o["ts"] for c in self.pair_changes):
            return "MIGRATION_ARTIFACT"
        x = self.valid_at(o["ts"], window_s=15)
        if x is None:
            around = [s for s in self.snapshots if abs(s["timestamp"] - o["ts"]) <= 15]
            if around and all(s["source_status"] in (NO_ROUTE, ERROR) for s in around):
                return "LIQUIDITY_ROUTE_FAILURE"
            return "UNVERIFIED"
        if x["truth_pnl_pct"] <= -self.sl_pct:
            return "REAL_EXECUTABLE_LOSS"
        ds = o.get("ds_mark") or {}
        if (ds.get("age_ms") or 0) >= 1000 * DS_STALE_S or x.get("ds_class") in (STALE_DS, DS_MISMATCH):
            return "STALE_PRICE_ARTIFACT"
        return "NOT_EXECUTABLE_LOSS"

    def common_source_pnl(self) -> float | None:
        """Entry fill vs the executable quote at the PRODUCTION exit time (same exit timing, executable price)."""
        if not self.old_exit:
            return None
        x = self.valid_at(self.old_exit["ts"], window_s=15)
        return None if x is None else x["truth_pnl_pct"]

    def summary(self, now: float | None = None) -> dict:
        ex = self.exit.summary()
        last = self.snapshots[-1] if self.snapshots else None
        last_valid = next((x for x in reversed(self.snapshots) if x["source_status"] == VALID), None)
        if ex["exit_reason"]:
            truth_reason = map_reason(ex["exit_reason"])
        elif last_valid is None:
            truth_reason = NO_ROUTE if last and last["source_status"] == NO_ROUTE else ("STALE" if last else "NONE")
        elif now is not None and now - last_valid["timestamp"] > TRUTH_STALE_S:
            truth_reason = STALE
        else:
            truth_reason = "NONE"
        n = len(self.snapshots)
        return {"trade_id": self.trade_id, "label": TRUTH_PNL_LABEL, "lifecycle": self.lifecycle,
                "entry_fill_price": self.entry_fill, "pnl_pct": ex["pnl_pct"], "exit_reason": ex["exit_reason"],
                "exit_reason_truth": truth_reason, "truth_price": ex["exit_price"],
                "closed_after_s": ex["closed_after_s"], "mfe_pct": self.mfe, "mae_pct": self.mae,
                "time_to_mfe_s": self.t_mfe, "time_to_mae_s": self.t_mae, "snapshots": n,
                "status_counts": dict(self.status_counts),
                "coverage_valid_pct": round(100 * self.status_counts.get(VALID, 0) / n, 1) if n else None,
                "last_valid": last_valid, "epochs": len(self.epochs), "pair_changes": self.pair_changes,
                "crosses_migration_unverified": bool(self.pair_changes), "fast_sl": self.fast_sl(),
                "old_exit": self.old_exit, "exit_reason_old": map_reason(self.old_exit["reason"]) if self.old_exit else None,
                "old_fast_sl_class": self.classify_old_exit(), "pnl_common_source": self.common_source_pnl(),
                "rejected_snapshots": self.rejected, "events": ex["events"]}
