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
    # V1.3 final: the official TRUTH P&L of the production trade (TruthLedger)
    exit_events: list = field(default_factory=list)   # one per production SELL (partial or final), quoted at its size
    ctx: dict = field(default_factory=dict)           # entry context: setup, age, liquidity, opportunity, risk, cost
    finalized: bool = False

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


# ====================================================================== OFFICIAL TRUTH P&L (V1.3 final validation)
# Every production SELL (partial or final) gets its own Jupiter SELL quote for EXACTLY the tokens sold, right after
# the exit decision. TRUTH P&L = proceeds of those executable quotes - recorded entry cost - network fees. The legacy
# book (DexScreener marks / liquidity model) stays as a REFERENCE and is never mixed in.
EXIT_TRUTH_MAX_LAG_S = 10.0      # an exit quote taken later than this after the exit decision is not common-source
LATENCY_REQUOTE_S = 2.0          # measured exit latency: re-quote the same size after a 2 s landing window
SIZE_TOL = 1e-6
LEGACY_FAST_SL = ("INVALID: legacy fast stop losses fired on stale DexScreener prints (V1.2 / V1.3 forensics); "
                  "not used to judge the Exit Engine")
ACCEPT_N, ACCEPT_VALID_PCT, ACCEPT_SLOT_PCT = 30, 95.0, 95.0


def new_exit_event(ts: float, reason: str, tokens: float, legacy_fill: float | None, legacy_source: str) -> dict:
    return {"ts": ts, "reason": reason, "tokens": tokens, "legacy_fill": legacy_fill, "legacy_source": legacy_source,
            "status": "PENDING", "quote_ts": None, "lag_s": None, "quoted_tokens": None, "sol_out": None,
            "price_usd": None, "impact_pct": None, "slot": None, "route": "", "requote_status": "PENDING",
            "requote_ts": None, "exit_latency_pct": None, "sol_price": None}


def apply_exit_quote(ev: dict, snap: TruthPriceSnapshot, sol_price: float | None) -> None:
    ev.update(status=snap.source_status, quote_ts=snap.timestamp, lag_s=round(snap.timestamp - ev["ts"], 2),
              quoted_tokens=snap.position_size, sol_out=snap.sol_out, price_usd=snap.price_usd,
              impact_pct=snap.price_impact_pct, slot=snap.context_slot, route=snap.route, sol_price=sol_price)
    if snap.source_status != VALID:
        ev["requote_status"] = "SKIPPED"


def apply_requote(ev: dict, snap: TruthPriceSnapshot) -> None:
    """Measured exit latency: executable SOL out LATENCY_REQUOTE_S later vs at the exit (positive = adverse)."""
    ev["requote_ts"] = snap.timestamp
    if snap.source_status == VALID and snap.sol_out and ev.get("sol_out"):
        ev["requote_status"] = VALID
        ev["exit_latency_pct"] = round(100 * (1 - snap.sol_out / ev["sol_out"]), 4)
    else:
        ev["requote_status"] = snap.source_status


def event_valid(ev: dict) -> tuple[bool, str]:
    if ev["status"] != VALID:
        return False, f"exit quote {ev['status']}"
    if ev.get("slot") is None:
        return False, "no contextSlot"
    if not ev.get("quoted_tokens") or abs(ev["quoted_tokens"] / ev["tokens"] - 1) > SIZE_TOL:
        return False, "quote size != tokens sold"
    if ev.get("lag_s") is None or ev["lag_s"] < 0 or ev["lag_s"] > EXIT_TRUTH_MAX_LAG_S:
        return False, f"exit quote lag {ev.get('lag_s')}s"
    if not ev.get("sol_price"):
        return False, "no SOL price"
    return True, ""


def trade_record(tr: "TruthTracker", legacy: dict, network_fee_usd: float) -> dict:
    """Official TRUTH P&L of one closed production trade + execution-cost decomposition + truth excursions.
    Entry = the recorded paper execution (Jupiter BUY quote x latency model); exit = executable SELL quotes."""
    c, evs = tr.ctx, tr.exit_events
    bad = [why for ok, why in (event_valid(e) for e in evs) if not ok]
    if tr.entry_quote_price is None:
        bad.append("no entry quote")
    if not evs:
        bad.append("no exit event")
    cost = c.get("cost_usd")
    if not cost:
        bad.append("no entry cost")
    proceeds = sum(e["sol_out"] * e["sol_price"] for e in evs if e.get("sol_out") and e.get("sol_price"))
    sold = sum(e["tokens"] for e in evs)
    fees = network_fee_usd * len(evs)
    valid = not bad
    exit_px = proceeds / sold if sold and proceeds else None
    pnl_usd = proceeds - cost - fees if valid else None
    pnl_pct = 100 * pnl_usd / cost if valid else None
    w = [e["tokens"] / sold for e in evs] if sold else []
    exit_imp = sum(wi * (e.get("impact_pct") or 0) for wi, e in zip(w, evs)) if valid else None
    lat = [(wi, e["exit_latency_pct"]) for wi, e in zip(w, evs) if e.get("exit_latency_pct") is not None]
    exit_lat = sum(wi * x for wi, x in lat) / sum(wi for wi, _ in lat) if lat else None
    entry_meas = c.get("entry_latency_measured_pct")
    q2q = pct(exit_px, tr.entry_quote_price) if valid else None
    measured = None
    if q2q is not None and entry_meas is not None and exit_lat is not None:
        measured = round(100 * ((1 + q2q / 100) * (1 - exit_lat / 100) / (1 + entry_meas / 100) - 1), 3)
    last = max(e["ts"] for e in evs) if evs else None
    marks = [s["price_usd"] for s in tr.snapshots
             if s["source_status"] == VALID and s.get("price_usd") and (last is None or s["timestamp"] <= last)]
    marks += [e["price_usd"] for e in evs if e["status"] == VALID and e.get("price_usd")]
    exc = [pct(px, tr.entry_fill) for px in marks]
    entry_lat = entry_meas if entry_meas is not None else tr.latency_model_pct
    parts = [x for x in (tr.execution_impact_pct, entry_lat, exit_imp, exit_lat) if x is not None]
    fee_pct = 100 * fees / cost if cost else None
    legacy_pct = legacy.get("pnl_pct")
    return {"trade_id": tr.trade_id, "mint": tr.mint, "symbol": tr.symbol, "label": "TRUTH_PNL",
            "valid": valid, "invalid_reasons": bad,
            "setup": c.get("setup"), "lifecycle": tr.lifecycle, "age_s_at_entry": c.get("age_s"),
            "liquidity_at_entry": c.get("liquidity_usd"), "opportunity_at_entry": c.get("opportunity"),
            "risk_at_entry": c.get("risk"), "setup_score": c.get("setup_score"),
            "entry_ts": tr.entry_ts, "exit_ts": last, "holding_s": round(last - tr.entry_ts, 1) if last else None,
            "exit_reason": evs[-1]["reason"] if evs else None, "exit_reasons": [e["reason"] for e in evs],
            "entry_quote_price": tr.entry_quote_price, "entry_truth_price": tr.entry_fill, "exit_truth_price": exit_px,
            "cost_usd": cost, "proceeds_usd": round(proceeds, 6), "network_fees_usd": round(fees, 6),
            "truth_pnl_usd": None if pnl_usd is None else round(pnl_usd, 4),
            "truth_pnl_pct": None if pnl_pct is None else round(pnl_pct, 3),
            "pnl_quote_to_quote_pct": q2q, "pnl_measured_latency_pct": measured,
            "mfe_truth": max(exc) if exc else None, "mae_truth": min(exc) if exc else None,
            "cost": {"entry_impact_pct": tr.execution_impact_pct, "entry_latency_sim_pct": tr.latency_model_pct,
                     "entry_latency_measured_pct": entry_meas, "exit_impact_pct": exit_imp,
                     "exit_latency_measured_pct": None if exit_lat is None else round(exit_lat, 4),
                     "network_fee_pct": None if fee_pct is None else round(fee_pct, 4),
                     "pool_fees": "inside Jupiter outAmount (not separable)",
                     "total_execution_cost_pct": round(sum(parts) + (fee_pct or 0), 4) if parts else None},
            "legacy_pnl_pct": legacy_pct, "legacy_pnl_usd": legacy.get("pnl_usd"),
            "legacy_exit_source": legacy.get("source"),
            "sign_differs_from_legacy": None if pnl_pct is None or legacy_pct is None else (pnl_pct > 0) != (legacy_pct > 0),
            "events": evs}


def _summ(vals: list) -> dict:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {"n": 0, "mean": None, "median": None}
    mid = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
    return {"n": len(v), "mean": round(sum(v) / len(v), 3), "median": round(mid, 3), "p10": v[int(0.1 * (len(v) - 1))],
            "p90": v[int(0.9 * (len(v) - 1))], "min": v[0], "max": v[-1]}


BUCKETS = {"age_s_at_entry": ((60, "<1m"), (300, "1-5m"), (900, "5-15m"), (3600, "15-60m"), (float("inf"), ">1h")),
           "liquidity_at_entry": ((5_000, "<5k"), (10_000, "5-10k"), (25_000, "10-25k"), (100_000, "25-100k"),
                                  (float("inf"), ">100k")),
           "opportunity_at_entry": ((50, "<50"), (60, "50-60"), (70, "60-70"), (80, "70-80"), (float("inf"), ">=80")),
           "risk_at_entry": ((20, "<20"), (35, "20-35"), (45, "35-45"), (55, "45-55"), (float("inf"), ">=55"))}


def bucket(key: str, v) -> str:
    if v is None:
        return "UNKNOWN"
    for edge, name in BUCKETS.get(key, ()):
        if v < edge:
            return name
    return str(v)


def ledger_stats(trades: list[dict]) -> dict:
    """Distribution, win rate, profit factor, expectancy, excursions, execution cost — common-source trades only."""
    t = [x for x in trades if x["valid"]]
    usd = [x["truth_pnl_usd"] for x in t]
    wins, losses = [u for u in usd if u > 0], [u for u in usd if u <= 0]
    cost = {k: _summ([x["cost"][k] for x in t]) for k in ("entry_impact_pct", "entry_latency_sim_pct",
                                                           "entry_latency_measured_pct", "exit_impact_pct",
                                                           "exit_latency_measured_pct", "network_fee_pct",
                                                           "total_execution_cost_pct")}
    out = {"n": len(t), "truth_pnl_pct": _summ([x["truth_pnl_pct"] for x in t]),
           "truth_pnl_usd_total": round(sum(usd), 4) if usd else 0.0,
           "win_rate_pct": round(100 * len(wins) / len(t), 1) if t else None,
           "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
           "expectancy_usd": round(sum(usd) / len(t), 4) if t else None,
           "pnl_quote_to_quote_pct": _summ([x["pnl_quote_to_quote_pct"] for x in t]),
           "pnl_measured_latency_pct": _summ([x["pnl_measured_latency_pct"] for x in t]),
           "mfe_truth": _summ([x["mfe_truth"] for x in t]), "mae_truth": _summ([x["mae_truth"] for x in t]),
           "holding_s": _summ([x["holding_s"] for x in t]), "execution_cost": cost,
           "legacy_pnl_pct_same_trades": _summ([x["legacy_pnl_pct"] for x in t]),
           "sign_differs_from_legacy": sum(1 for x in t if x["sign_differs_from_legacy"])}
    for key, name in (("setup", "by_setup"), ("age_s_at_entry", "by_age"), ("liquidity_at_entry", "by_liquidity"),
                      ("opportunity_at_entry", "by_opportunity"), ("risk_at_entry", "by_risk_at_entry")):
        groups: dict[str, list] = {}
        for x in t:
            g = x.get(key) if key == "setup" else bucket(key, x.get(key))
            groups.setdefault(str(g), []).append(x)
        out[name] = {g: {"n": len(v), "mean_pct": _summ([x["truth_pnl_pct"] for x in v])["mean"],
                         "median_pct": _summ([x["truth_pnl_pct"] for x in v])["median"],
                         "win_rate_pct": round(100 * sum(1 for x in v if x["truth_pnl_usd"] > 0) / len(v), 1),
                         "sample": "INSUFFICIENT" if len(v) < ACCEPT_N else "OK"} for g, v in groups.items()}
    return out


class TruthLedger:
    """Completed production trades valued on executable quotes (persisted next to the paper book)."""

    def __init__(self, path=None):
        self.path = path
        self.trades: list[dict] = []
        self.quotes = {"VALID": 0, "INVALID": 0, "slot": 0}
        if path is not None:
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                self.trades, self.quotes = d.get("trades", []), {**self.quotes, **d.get("quotes", {})}
            except (OSError, ValueError):
                pass

    def count_quote(self, snap: TruthPriceSnapshot) -> None:
        ok = snap.source_status == VALID
        self.quotes["VALID" if ok else "INVALID"] += 1
        self.quotes["slot"] += int(ok and snap.context_slot is not None)

    def add(self, rec: dict) -> None:
        self.trades.append(rec)
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        import json
        import os
        tmp = str(self.path) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"trades": self.trades[-5000:], "quotes": self.quotes}, f, default=str)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def acceptance(self) -> dict:
        q = self.quotes
        total = q["VALID"] + q["INVALID"]
        valid_pct = round(100 * q["VALID"] / total, 2) if total else None
        slot_pct = round(100 * q["slot"] / q["VALID"], 2) if q["VALID"] else None
        good = [t for t in self.trades if t["valid"]]
        checks = {"common_source_n>=30": len(good) >= ACCEPT_N,
                  "sell_quote_valid>=95%": valid_pct is not None and valid_pct >= ACCEPT_VALID_PCT,
                  "context_slot>=95%": slot_pct is not None and slot_pct >= ACCEPT_SLOT_PCT,
                  # truth P&L never reads a reference price: a sign can only come from executable quotes
                  "no_stale_reference_sign_errors": all(t["exit_truth_price"] is not None for t in good),
                  "pnl_from_execution_source_only": all(not t["invalid_reasons"] for t in good)}
        return {"status": "VALIDATED" if all(checks.values()) else "NOT VALIDATED YET", "checks": checks,
                "common_source_n": len(good), "trades_total": len(self.trades), "quotes_valid": q["VALID"],
                "quotes_invalid": q["INVALID"], "valid_quote_pct": valid_pct, "context_slot_pct": slot_pct,
                "legacy_fast_sl": LEGACY_FAST_SL}
