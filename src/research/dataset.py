"""Research dataset recorder (Implementation Spec, Part 1) — READ-ONLY with respect to trading: it records, it never
decides. Nothing here changes a BUY / TRADE / VET / Risk / Early Signal decision.

Tables (SQLite, its own file, default data/research.db):
  token_discovery   one row per CA ever discovered (first seen, launch, initial MC/liq/price, outcome label, MFE/MAE)
  token_snapshots   one row per CA per observation: at discovery, +30s, +1m, +2m, +5m, +10m, +30m, +1h (while the
                    scanner still tracks it) and on every stage change / candidate / Jupiter quote. Full feature row:
                    market, holders, authorities, identity, VET, Risk, D1-D8, the 7 Early Signal groups, scores,
                    BLOCKED_BY (exact reasons), Jupiter quote status. Missing data stays NULL — never invented.
  price_path        compact price path (15 s while < 15 min old, 60 s to 1 h, 5 min after; plus follow-up quotes from
                    DexScreener at 30 m / 1 h / 6 h / 24 h after the scanner stopped tracking the CA). Forward returns,
                    MFE and MAE from ANY anchor time are computed from it without look-ahead.
  forward_returns   per CA x anchor (discovery | candidate) x horizon (30s .. 24h): return, MFE, MAE, TP+30 / SL-15 hit
                    and which came first.
  candidates        every Trade Candidate (decision TRADE + Early TRUE + VERIFIED + VET PASS), whatever happened next:
                    risk block, Jupiter quote status, bought or not, would_have_bought_if_quote_ok and
                    simulated_pnl_if_forced (simplified: entry + estimated cost, first of TP +30 % / SL -15 % /
                    1 h time stop, exit cost) — measures missed edge separately from execution.

D-columns (-1 unknown, 0 fail, 1 pass) — the Early Signal design rules as observable per snapshot:
  d1 direction OK (no volume/txn spike during a sell-off)   d2 buy-pressure signal fired
  d3 whale holder increase >= +10                            d4 not suppressed by risk (top10 > 35 %, rug flag, Risk > 60)
  d5 pair stable (no pair change / migration in 5 min)        d6 holder data valid
  d7 holders >= 50                                            d8 data not INVALID and holders not SUSPICIOUS
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

FORWARD_EVERY_S = 15
CANDIDATE_GAP_S = 300          # a candidate absent this long ends its episode (no row per flap of OPP 64/65)
SNAP_OFFSETS = (0, 30, 60, 120, 300, 600, 1800, 3600)
HORIZONS = (("30s", 30), ("1m", 60), ("2m", 120), ("5m", 300), ("10m", 600), ("30m", 1800), ("1h", 3600),
            ("6h", 21600), ("24h", 86400))
FOLLOWUP_S = (1800, 3600, 21600, 86400)       # off-scanner price checks (relative to first seen)
SIGNALS = ("volume_accel", "txn_accel", "buy_pressure", "mc_accel", "holder_accel", "liquidity_growth", "whale_accum")
KEEP_DAYS = 7
TP, SL, TIME_STOP_S = 0.30, -0.15, 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS token_discovery (
  ca TEXT PRIMARY KEY, symbol TEXT, first_seen_ts REAL, launch_ts REAL, source_first TEXT,
  initial_mc REAL, initial_liq REAL, initial_price REAL, initial_price_ts REAL,
  first_pre_early_ts REAL, first_early_watch_ts REAL, first_early_true_ts REAL, first_candidate_ts REAL,
  first_bought_ts REAL, early_complete_ts REAL, last_tracked_ts REAL,
  final_outcome_label TEXT, max_mc_24h REAL, max_favorable_excursion_pct REAL, max_adverse_excursion_pct REAL,
  done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS token_snapshots (
  snapshot_id TEXT PRIMARY KEY, ca TEXT NOT NULL, ts REAL NOT NULL, age_sec INTEGER, source TEXT, stage TEXT,
  reason TEXT, mc_usd REAL, price_usd REAL, liq_usd REAL, vol_5m REAL, vol_1h REAL, tx_5m INTEGER, buys_5m INTEGER,
  sells_5m INTEGER, buy_pressure_pct REAL, holders INTEGER, holders_growth_1m REAL, holders_growth_5m_pct REAL,
  top10_pct REAL, creator_pct REAL, authority_mint TEXT, authority_freeze TEXT, is_token2022 INTEGER,
  migrated INTEGER, pair_address TEXT, dex_id TEXT, identity_status TEXT, vet_status TEXT, vet_fail TEXT,
  vet_unknown TEXT, risk_score REAL, risk_reasons TEXT,
  d1 INTEGER, d2 INTEGER, d3 INTEGER, d4 INTEGER, d5 INTEGER, d6 INTEGER, d7 INTEGER, d8 INTEGER,
  s_volume_accel INTEGER, s_txn_accel INTEGER, s_buy_pressure INTEGER, s_mc_accel INTEGER, s_holder_accel INTEGER,
  s_liquidity_growth INTEGER, s_whale_accum INTEGER, early_signal TEXT, early_strength REAL, early_groups INTEGER,
  early_fired INTEGER, early_history_min REAL, early_suppressed TEXT, pre_early TEXT, early_watch_rank REAL,
  opportunity REAL, momentum REAL, confidence REAL, decision TEXT, blocked_by TEXT,
  jupiter_quote_ok INTEGER, jupiter_status TEXT, jupiter_slippage_bps INTEGER, jupiter_route_hops INTEGER,
  expected_price_impact_pct REAL, notes TEXT);
CREATE INDEX IF NOT EXISTS ix_snap_ca_ts ON token_snapshots(ca, ts);
CREATE INDEX IF NOT EXISTS ix_snap_ts ON token_snapshots(ts);
CREATE INDEX IF NOT EXISTS ix_snap_stage ON token_snapshots(stage);
CREATE TABLE IF NOT EXISTS price_path (ca TEXT NOT NULL, ts REAL NOT NULL, price REAL, mc REAL, liq REAL,
  vol_5m REAL, src TEXT);
CREATE INDEX IF NOT EXISTS ix_path_ca_ts ON price_path(ca, ts);
CREATE TABLE IF NOT EXISTS forward_returns (
  ca TEXT NOT NULL, anchor TEXT NOT NULL, anchor_ts REAL, anchor_price REAL, horizon TEXT NOT NULL, horizon_s INTEGER,
  price REAL, price_ts REAL, return_pct REAL, mfe_pct REAL, mae_pct REAL, hit_tp30 INTEGER, hit_sl15 INTEGER,
  first_hit TEXT, samples INTEGER, observed TEXT, PRIMARY KEY (ca, anchor, horizon));
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ca TEXT NOT NULL, symbol TEXT, ts REAL, discovery_ts REAL,
  time_from_discovery_to_candidate_sec INTEGER, price_at_candidate REAL, liq_at_candidate REAL, usd REAL,
  risk_allowed INTEGER, blocked_reason_at_candidate TEXT, quote_ts REAL, time_from_candidate_to_quote_sec REAL,
  quote_status TEXT, quote_detail TEXT, quote_impact_pct REAL, quote_route_hops INTEGER,
  would_have_bought_if_quote_ok INTEGER, bought INTEGER DEFAULT 0, est_cost_pct REAL,
  simulated_pnl_if_forced REAL, sim_exit TEXT, sim_done INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_cand_ca ON candidates(ca);
"""

AB_SNAP_COLS = {"engine": "TEXT", "old_decision": "TEXT", "new_decision": "TEXT", "blocked_by_old": "TEXT",
                "early_score": "REAL", "early_confidence": "REAL", "early_theta": "REAL", "early_gamma": "REAL",
                "age_bucket": "TEXT", "prior_risk": "REAL", "final_risk": "REAL"}
AB_CAND_COLS = {"engine": "TEXT", "old_decision": "TEXT", "new_decision": "TEXT", "old_candidate": "INTEGER",
                "new_candidate": "INTEGER", "blocked_by_old": "TEXT", "blocked_by_new": "TEXT", "early_score": "REAL",
                "early_confidence": "REAL", "early_theta": "REAL", "early_gamma": "REAL", "age_bucket": "TEXT",
                "age_sec": "REAL", "prior_risk": "REAL", "final_risk": "REAL", "simulated_fill": "INTEGER"}


def migrate(db: sqlite3.Connection) -> None:
    """Add A/B columns to databases created before experimental mode (ALTER TABLE ADD COLUMN, idempotent)."""
    for table, cols in (("token_snapshots", AB_SNAP_COLS), ("candidates", AB_CAND_COLS)):
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for c, typ in cols.items():
            if c not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
    db.commit()


def ab_fields(rec: dict | None) -> dict:
    rec = rec or {}
    es = rec.get("early_score") or {}
    return {"engine": rec.get("engine", "old"), "old_decision": rec.get("old_decision"),
            "new_decision": rec.get("decision") if rec.get("engine") == "experimental" else None,
            "blocked_by_old": json.dumps(rec.get("blocked_by_old") or []) if rec else None,
            "early_score": es.get("score"), "early_confidence": es.get("confidence"), "early_theta": es.get("theta"),
            "early_gamma": es.get("gamma"), "age_bucket": es.get("bucket"), "prior_risk": es.get("prior_risk"),
            "final_risk": es.get("final_risk")}


SNAP_COLS = ("snapshot_id", "ca", "ts", "age_sec", "source", "stage", "reason", "mc_usd", "price_usd", "liq_usd",
             "vol_5m", "vol_1h", "tx_5m", "buys_5m", "sells_5m", "buy_pressure_pct", "holders", "holders_growth_1m",
             "holders_growth_5m_pct", "top10_pct", "creator_pct", "authority_mint", "authority_freeze",
             "is_token2022", "migrated", "pair_address", "dex_id", "identity_status", "vet_status", "vet_fail",
             "vet_unknown", "risk_score", "risk_reasons", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
             *("s_" + s for s in SIGNALS), "early_signal", "early_strength", "early_groups", "early_fired",
             "early_history_min", "early_suppressed", "pre_early", "early_watch_rank", "opportunity", "momentum",
             "confidence", "decision", "blocked_by", "jupiter_quote_ok", "jupiter_status", "jupiter_slippage_bps",
             "jupiter_route_hops", "expected_price_impact_pct", "notes", *AB_SNAP_COLS)
from trading.decision import T22  # noqa: E402  (canonical Token-2022 program id)


def _tri(v) -> int:
    return -1 if v is None else (1 if v else 0)


def _price(st) -> float | None:
    m = st.market
    p = m.price_usd if m else None
    return p if p and p > 0 else None


def stage_of(st, rec: dict | None, candidate: bool, bought: bool, exited: bool) -> str:
    if bought:
        return "bought"
    if exited:
        return "exited"
    if candidate:
        return "candidate"
    if st.early is not None and st.early.is_early is True:
        return "early_signal"
    if rec and rec.get("decision") == "TRADE":
        return "blocked"
    if st.early_watch is not None and getattr(st.early_watch, "eligible", False):
        return "early_watch"
    if st.pre_early is not None and getattr(st.pre_early, "status", "NOT_ELIGIBLE") != "NOT_ELIGIBLE":
        return "pre_early"
    return "discovery"


def d_flags(st) -> dict:
    es = st.early
    hits = {h.key: h for h in (es.signals if es else [])}
    blocked = [str(h.raw.get("blocked", "")) for h in hits.values()]
    h = st.holders if st.holder_status == "ok" else None
    vt = [hits.get(k) for k in ("volume_accel", "txn_accel")]
    d1 = 0 if any(b.startswith("D1") for b in blocked) else (1 if any(x is not None and x.fired is not None for x in vt) else None)
    w = hits.get("whale_accum")
    d3 = 0 if any(b.startswith("D3") for b in blocked) else (1 if w is not None and w.fired is not None else None)
    recent_break = any(e.type == "DATA_BREAK" and time.time() - e.ts < 300 for e in st.recent_events)
    hv = None if st.holders is None else bool(st.holders.valid)
    suspicious = any(b.startswith("D8 holder growth SUSPICIOUS") for b in blocked)
    return {"d1": _tri(d1), "d2": _tri(hits["buy_pressure"].fired if "buy_pressure" in hits else None), "d3": _tri(d3),
            "d4": _tri(None if es is None or es.strength is None else not es.suppressed),
            "d5": _tri(not recent_break) if st.market else -1, "d6": _tri(hv),
            "d7": _tri(None if h is None or h.holder_count is None else h.holder_count >= 50),
            "d8": _tri(None if st.quality is None else (st.dq_status != "INVALID" and not suspicious))}


class DatasetRecorder:
    def __init__(self, path: str | Path, dex=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        migrate(self.db)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.dex = dex                                  # DexScreenerClient for off-scanner follow-ups
        self.t: dict[str, dict] = {}                    # in-memory per-CA tracking state
        self.cand_open: dict[str, int] = {}             # ca -> candidates.id of the latest candidate episode
        self._pending_snaps: list[tuple] = []
        self._pending_path: list[tuple] = []
        self._last_prune = 0.0
        self._last_fwd = 0.0
        self.cand_seen: dict[str, float] = {}           # ca -> last time it was a Trade Candidate
        self.counters = {"discovered": 0, "snapshots": 0, "path_points": 0, "forward_rows": 0, "candidates": 0,
                         "followup_calls": 0, "followup_points": 0}
        self._restore()

    # ------------------------------------------------------------------ in-memory state
    def _restore(self) -> None:
        """After a restart, keep follow-ups for CAs first seen < 25 h ago (anchors come back from the DB)."""
        since = time.time() - 25 * 3600
        for ca, fs, launch, ip, ipts, cts in self.db.execute(
                "SELECT ca, first_seen_ts, launch_ts, initial_price, initial_price_ts, first_candidate_ts "
                "FROM token_discovery WHERE first_seen_ts > ? AND done = 0", (since,)):
            self.t[ca] = {"first": fs, "launch": launch, "snap_i": len(SNAP_OFFSETS), "stage": None, "last_path": 0.0,
                          "anchors": {"discovery": (ipts, ip)} if ip else {}, "fwd_done": set(), "tracked": False,
                          "fu_done": set(), "last_seen": fs, "restored": True}
            if cts:
                row = self.db.execute("SELECT ts, price_at_candidate FROM candidates WHERE ca=? ORDER BY id DESC LIMIT 1",
                                      (ca,)).fetchone()
                if row and row[1]:
                    self.t[ca]["anchors"]["candidate"] = (row[0], row[1])
        for ca, anchor, h in self.db.execute("SELECT ca, anchor, horizon FROM forward_returns"):
            if ca in self.t:
                self.t[ca]["fwd_done"].add((anchor, h))

    # ------------------------------------------------------------------ recording (called every bot tick)
    def observe(self, states, decisions: dict, now: float, candidates: set[str], positions: set[str],
                exited: set[str] = frozenset()) -> None:
        seen = set()
        for st in states:
            ca = st.mint
            seen.add(ca)
            rec = decisions.get(ca)
            t = self.t.get(ca)
            if t is None:
                t = self._discover(st, now)
            t["tracked"], t["last_seen"] = True, now
            p = _price(st)
            if p is not None and "discovery" not in t["anchors"]:
                t["anchors"]["discovery"] = (now, p)
                self.db.execute("UPDATE token_discovery SET initial_price=?, initial_price_ts=?, "
                                "initial_mc=COALESCE(initial_mc, ?), initial_liq=COALESCE(initial_liq, ?) WHERE ca=?",
                                (p, now, st.market.market_cap, st.market.liquidity_usd, ca))
            stage = stage_of(st, rec, ca in candidates, ca in positions, ca in exited)
            self._first_times(ca, st, stage, t, now)
            due = t["snap_i"] < len(SNAP_OFFSETS) and now - t["first"] >= SNAP_OFFSETS[t["snap_i"]]
            if due:
                while t["snap_i"] < len(SNAP_OFFSETS) and now - t["first"] >= SNAP_OFFSETS[t["snap_i"]]:
                    t["snap_i"] += 1
                self._snap(st, rec, stage, "timeline", now)
            elif stage != t["stage"] and t["stage"] is not None:
                self._snap(st, rec, stage, "stage_change", now)
            t["stage"] = stage
            age = now - t["first"]
            step = 15 if age < 900 else 60 if age < 3600 else 300
            if p is not None and now - t["last_path"] >= step:
                t["last_path"] = now
                m = st.market
                self._pending_path.append((ca, now, p, m.market_cap, m.liquidity_usd, m.vol_5m, "scanner"))
        for ca in candidates:
            self.cand_seen[ca] = now
        for ca in [c for c in self.cand_open if now - self.cand_seen.get(c, 0) > CANDIDATE_GAP_S]:
            self.cand_open.pop(ca, None)
        for ca, t in self.t.items():
            if ca not in seen and t.get("tracked"):
                t["tracked"] = False
                self.db.execute("UPDATE token_discovery SET last_tracked_ts=? WHERE ca=?", (t["last_seen"], ca))
        self._flush()
        self._forward(now)
        if now - self._last_prune > 3600:
            self._prune(now)

    def _discover(self, st, now: float) -> dict:
        ca = st.mint
        launch = st.info.created_at
        first = min(now, st.info.discovered_at or now)
        src = ",".join(sorted(st.info.sources)) if st.info.sources else ""
        m = st.market
        self.db.execute("INSERT OR IGNORE INTO token_discovery (ca, symbol, first_seen_ts, launch_ts, source_first, "
                        "initial_mc, initial_liq) VALUES (?,?,?,?,?,?,?)",
                        (ca, st.info.symbol, first, launch, src,
                         (st.mc_track.initial_mc if st.mc_track else None) or (m.market_cap if m else None),
                         m.liquidity_usd if m else None))
        self.counters["discovered"] += 1
        t = self.t[ca] = {"first": first, "launch": launch, "snap_i": 0, "stage": None, "last_path": 0.0,
                          "anchors": {}, "fwd_done": set(), "tracked": True, "fu_done": set(), "last_seen": now}
        return t

    def _first_times(self, ca, st, stage, t, now) -> None:
        marks = t.setdefault("marks", set())
        col = {"pre_early": "first_pre_early_ts", "early_watch": "first_early_watch_ts",
               "early_signal": "first_early_true_ts", "candidate": "first_candidate_ts", "bought": "first_bought_ts"}
        order = ["pre_early", "early_watch", "early_signal", "candidate", "bought"]
        reached = order[:order.index(stage) + 1] if stage in order else []
        if st.early is not None and st.early.is_early is True and "early_signal" not in reached:
            reached.append("early_signal")
        for s in reached:
            if s in col and s not in marks:
                marks.add(s)
                self.db.execute(f"UPDATE token_discovery SET {col[s]}=COALESCE({col[s]}, ?) WHERE ca=?", (now, ca))
        if "early_complete" not in marks and st.early is not None and st.early.groups_computable >= 7:
            marks.add("early_complete")
            self.db.execute("UPDATE token_discovery SET early_complete_ts=COALESCE(early_complete_ts, ?) WHERE ca=?",
                            (now, ca))

    def _snap(self, st, rec, stage, reason, now, quote=None) -> None:
        self._pending_snaps.append(self.snapshot_row(st, rec, stage, reason, now, quote,
                                                     self.t.get(st.mint, {}).get("first")))

    @staticmethod
    def snapshot_row(st, rec, stage, reason, now, quote=None, first=None) -> tuple:
        m, es, ident = st.market, st.early, st.identity
        h = st.holders if st.holder_status == "ok" else None
        hi = st.holder_intel
        checks = (rec or {}).get("checks") or []
        vet_fail = [c["key"] for c in checks if c["result"] == "FAIL"]
        vet_unk = [c["key"] for c in checks if c["result"] == "UNKNOWN"]
        vet_status = None if rec is None else ("pass" if rec.get("vet_passed") else "fail" if vet_fail else "pending")
        hits = {x.key: x for x in (es.signals if es else [])}
        b, s = (m.buys_5m, m.sells_5m) if m else (None, None)
        bp = 100 * b / (b + s) if b is not None and s is not None and b + s > 0 else None
        launch = st.info.created_at
        age = now - (launch or first or now)
        rk = st.risk
        comp = (rec or {}).get("components") or {}
        q = quote or {}
        row = {
            "snapshot_id": uuid.uuid4().hex, "ca": st.mint, "ts": now, "age_sec": int(age), "source": "combined",
            "stage": stage, "reason": reason, "mc_usd": m.market_cap if m else None, "price_usd": _price(st),
            "liq_usd": m.liquidity_usd if m else None, "vol_5m": m.vol_5m if m else None,
            "vol_1h": m.vol_1h if m else None, "tx_5m": m.txns_5m if m else None, "buys_5m": b, "sells_5m": s,
            "buy_pressure_pct": bp, "holders": h.holder_count if h else None,
            "holders_growth_1m": hi.new_per_min if hi else None,
            "holders_growth_5m_pct": hi.growth_5m_pct if hi else None, "top10_pct": h.top10_pct if h else None,
            "creator_pct": (st.dev.current_pct if st.dev and st.dev.balance_verified else
                            (h.creator_pct if h else None)),
            "authority_mint": (("active" if ident.mint_authority else "revoked") if ident.helius_checked else None),
            "authority_freeze": (("active" if ident.freeze_authority else "revoked") if ident.helius_checked else None),
            "is_token2022": (int(ident.token_program == T22) if ident.helius_checked else None),
            "migrated": (None if st.info.complete is None and m is None else
                         int(bool(st.info.complete) or bool(m and not m.is_curve))),
            "pair_address": m.pair_address if m else None, "dex_id": m.dex_id if m else None,
            "identity_status": ident.status, "vet_status": vet_status, "vet_fail": ",".join(vet_fail) or None,
            "vet_unknown": ",".join(vet_unk) or None, "risk_score": rk.score if rk else None,
            "risk_reasons": json.dumps([f.key for f in rk.factors]) if rk else None,
            **d_flags(st),
            **{"s_" + k: _tri(hits[k].fired) if k in hits else -1 for k in SIGNALS},
            "early_signal": ("unknown" if es is None or es.strength is None else
                             "true" if es.is_early is True else "false"),
            "early_strength": es.strength if es else None, "early_groups": es.groups_computable if es else None,
            "early_fired": es.fired_count if es else None, "early_history_min": es.history_min if es else None,
            "early_suppressed": ",".join(es.suppressed) if es and es.suppressed else None,
            "pre_early": getattr(st.pre_early, "status", None),
            "early_watch_rank": getattr(st.early_watch, "rank_score", None),
            "opportunity": (rec or {}).get("opportunity"), "momentum": comp.get("momentum"),
            "confidence": (rec or {}).get("confidence"), "decision": (rec or {}).get("decision"),
            "blocked_by": json.dumps((rec or {}).get("blocked_by") or []) if rec else None,
            "jupiter_quote_ok": q.get("ok"), "jupiter_status": q.get("status"),
            "jupiter_slippage_bps": q.get("slippage_bps"), "jupiter_route_hops": q.get("hops"),
            "expected_price_impact_pct": q.get("impact_pct"), "notes": q.get("detail"), **ab_fields(rec)}
        return tuple(row[c] for c in SNAP_COLS)

    # ------------------------------------------------------------------ candidates / execution
    def candidate(self, st, rec: dict, now: float, usd: float | None, risk_allowed: bool, est_cost_pct: float) -> int:
        """A Trade Candidate episode (first time, or again after the previous episode ended)."""
        ca = st.mint
        self.cand_seen[ca] = now
        if ca in self.cand_open:
            return self.cand_open[ca]
        t = self.t.get(ca) or self._discover(st, now)
        p = _price(st)
        blocked = [b for b in (rec.get("blocked_by") or [])] + ["risk_engine: " + r for r in rec.get("risk_reasons") or []]
        cur = self.db.execute(
            "INSERT INTO candidates (ca, symbol, ts, discovery_ts, time_from_discovery_to_candidate_sec, "
            "price_at_candidate, liq_at_candidate, usd, risk_allowed, blocked_reason_at_candidate, est_cost_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ca, st.info.symbol, now, t["first"], int(now - t["first"]), p,
             st.market.liquidity_usd if st.market else None, usd, int(bool(risk_allowed)),
             json.dumps(blocked) if blocked else None, est_cost_pct))
        cid = cur.lastrowid
        ab = ab_fields(rec)
        es = rec.get("early_score") or {}
        exp = rec.get("engine") == "experimental"
        self.db.execute(
            "UPDATE candidates SET engine=?, old_decision=?, new_decision=?, old_candidate=?, new_candidate=?, "
            "blocked_by_old=?, blocked_by_new=?, early_score=?, early_confidence=?, early_theta=?, early_gamma=?, "
            "age_bucket=?, age_sec=?, prior_risk=?, final_risk=? WHERE id=?",
            (ab["engine"], ab["old_decision"], ab["new_decision"], int(bool(rec.get("old_candidate"))),
             int(exp and rec.get("decision") == "TRADE"), ab["blocked_by_old"],
             json.dumps(rec.get("blocked_by") or []) if exp else None, ab["early_score"], ab["early_confidence"],
             ab["early_theta"], ab["early_gamma"], ab["age_bucket"], es.get("age_s"), ab["prior_risk"],
             ab["final_risk"], cid))
        self.cand_open[ca] = cid
        if p is not None and "candidate" not in t["anchors"]:
            t["anchors"]["candidate"] = (now, p)
        self._snap(st, rec, "candidate", "candidate", now)
        self.counters["candidates"] += 1
        self._flush()
        return cid

    def candidate_ended(self, ca: str) -> None:
        self.cand_open.pop(ca, None)

    def quote(self, st, rec: dict | None, now: float, status: str, detail: str, quote: dict | None,
              bought: bool, slippage_bps: int | None) -> None:
        cid = self.cand_open.get(st.mint)
        hops = len(quote.get("routePlan") or []) if quote else None
        try:
            imp = 100 * float(quote.get("priceImpactPct")) if quote else None
        except (TypeError, ValueError):
            imp = None
        if cid is not None:
            row = self.db.execute("SELECT ts, quote_ts FROM candidates WHERE id=?", (cid,)).fetchone()
            ok = status == "OK"
            self.db.execute(
                "UPDATE candidates SET quote_ts=COALESCE(quote_ts, ?), time_from_candidate_to_quote_sec="
                "COALESCE(time_from_candidate_to_quote_sec, ?), quote_status=?, quote_detail=?, quote_impact_pct=?, "
                "quote_route_hops=?, would_have_bought_if_quote_ok=?, bought=MAX(bought, ?), "
                "simulated_fill=MAX(COALESCE(simulated_fill, 0), ?) WHERE id=?",
                (now, round(now - row[0], 2) if row else None, status, detail[:300], imp, hops,
                 int(not ok and bool(rec and rec.get("risk_allowed"))), int(bought),
                 int(bought and not ok), cid))
        self._snap(st, rec, "bought" if bought else "candidate", "quote", now,
                   {"ok": int(status == "OK"), "status": status, "slippage_bps": slippage_bps, "hops": hops,
                    "impact_pct": imp, "detail": detail[:300]})
        self._flush()

    # ------------------------------------------------------------------ forward returns
    def _path(self, ca: str, t0: float, t1: float) -> list[tuple[float, float, str]]:
        return self.db.execute("SELECT ts, price, src FROM price_path WHERE ca=? AND ts>=? AND ts<=? AND price>0 "
                               "ORDER BY ts", (ca, t0, t1)).fetchall()

    def _forward(self, now: float) -> None:
        if now - self._last_fwd < FORWARD_EVERY_S:
            return
        self._last_fwd = now
        rows = []
        for ca, t in list(self.t.items()):
            for anchor, (a_ts, a_p) in list(t["anchors"].items()):
                if not a_p:
                    continue
                for name, hs in HORIZONS:
                    if (anchor, name) in t["fwd_done"] or now < a_ts + hs:
                        continue
                    pts = self._path(ca, a_ts, a_ts + hs)
                    after = [x for x in self._path(ca, a_ts + hs, a_ts + hs * 1.5 + 60)][:1]
                    end_pt = after[0] if after and after[0][0] - (a_ts + hs) <= max(60, hs * 0.25) else None
                    if end_pt is None and pts and (a_ts + hs) - pts[-1][0] <= max(30, hs * 0.1):
                        end_pt = pts[-1]
                    waiting = end_pt is None and now < a_ts + hs * 1.5 + 120 and \
                        (t.get("tracked") or hs in FOLLOWUP_S)
                    if waiting:
                        continue                          # a follow-up / next sample may still arrive
                    t["fwd_done"].add((anchor, name))
                    window = pts + ([end_pt] if end_pt and end_pt not in pts else [])
                    rets = [p / a_p - 1 for _, p, _s in window]
                    first_hit = None
                    for r in rets:
                        if r >= TP:
                            first_hit = "tp30"
                            break
                        if r <= SL:
                            first_hit = "sl15"
                            break
                    rows.append((ca, anchor, a_ts, a_p, name, hs, end_pt[1] if end_pt else None,
                                 end_pt[0] if end_pt else None, 100 * (end_pt[1] / a_p - 1) if end_pt else None,
                                 100 * max(rets) if rets else None, 100 * min(rets) if rets else None,
                                 int(any(r >= TP for r in rets)) if rets else None,
                                 int(any(r <= SL for r in rets)) if rets else None, first_hit, len(window),
                                 "missing" if end_pt is None else
                                 ("followup" if any(x[2] != "scanner" for x in window) else "tracked")))
            all_done = len(t["fwd_done"]) >= len(HORIZONS) * len(t["anchors"])
            age = now - t["first"]
            if not t.get("tracked") and ((all_done and age > 86400 + 600) or age > 90000 + 3 * 3600):
                self._finalize(ca, t)
        if rows:
            self.db.executemany("INSERT OR REPLACE INTO forward_returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.counters["forward_rows"] += len(rows)
            self.db.commit()
        self._simulate(now)

    def _simulate(self, now: float) -> None:
        """simulated_pnl_if_forced for candidates whose 1 h window has passed (simplified exit, see module doc)."""
        for cid, ca, ts, p0, cost in self.db.execute(
                "SELECT id, ca, ts, price_at_candidate, est_cost_pct FROM candidates WHERE sim_done=0 AND ts < ?",
                (now - TIME_STOP_S - 120,)).fetchall():
            if not p0:
                self.db.execute("UPDATE candidates SET sim_done=1, sim_exit='no_price' WHERE id=?", (cid,))
                continue
            c = (cost or 0) / 100
            entry = p0 * (1 + c)
            exit_p, why = None, "no_data"
            for _t, p, _s in self._path(ca, ts, ts + TIME_STOP_S):
                r = p / entry - 1
                if r >= TP:
                    exit_p, why = p, "tp30"
                    break
                if r <= SL:
                    exit_p, why = p, "sl15"
                    break
                exit_p, why = p, "time_stop_1h"
            pnl = None if exit_p is None else 100 * (exit_p * (1 - c) / entry - 1)
            self.db.execute("UPDATE candidates SET sim_done=1, simulated_pnl_if_forced=?, sim_exit=? WHERE id=?",
                            (pnl, why, cid))
        self.db.commit()

    def _finalize(self, ca: str, t: dict) -> None:
        a = t["anchors"].get("discovery")
        if a:
            pts = self._path(ca, a[0], a[0] + 86400)
            mcs = self.db.execute("SELECT MAX(mc) FROM price_path WHERE ca=? AND ts<=?", (ca, a[0] + 86400)).fetchone()
            rets = [p / a[1] - 1 for _, p, _s in pts]
            mfe, mae = (100 * max(rets), 100 * min(rets)) if rets else (None, None)
            last = rets[-1] if rets else None
            label = ("unknown" if not rets else "runner" if mfe >= 100 else
                     "rug" if mae <= -90 and mfe < 30 else "dead" if last is not None and last <= -0.8 else "mediocre")
            self.db.execute("UPDATE token_discovery SET done=1, max_mc_24h=?, max_favorable_excursion_pct=?, "
                            "max_adverse_excursion_pct=?, final_outcome_label=? WHERE ca=?",
                            (mcs[0] if mcs else None, mfe, mae, label, ca))
        else:
            self.db.execute("UPDATE token_discovery SET done=1, final_outcome_label='unknown' WHERE ca=?", (ca,))
        self.t.pop(ca, None)

    # ------------------------------------------------------------------ off-scanner follow-ups (async)
    def followups_due(self, now: float, limit: int = 30) -> list[str]:
        out = []
        for ca, t in self.t.items():
            if t.get("tracked"):
                continue
            for off in FOLLOWUP_S:
                if off not in t["fu_done"] and t["first"] + off <= now <= t["first"] + off + 3 * 3600:
                    out.append(ca)
                    break
            if len(out) >= limit:
                break
        return out

    async def run_followups(self, now: float | None = None) -> int:
        """One DexScreener batch (<= 30 CAs) for CAs the scanner no longer tracks; returns points written."""
        now = now or time.time()
        if self.dex is None:
            return 0
        due = self.followups_due(now)
        if not due:
            return 0
        res = await self.dex.tokens(due)
        self.counters["followup_calls"] += 1
        n = 0
        for ca in due:
            t = self.t.get(ca)
            if t is None:
                continue
            for off in FOLLOWUP_S:
                if t["first"] + off <= now:
                    t["fu_done"].add(off)
            got = (res or {}).get(ca)
            m = got[0] if got else None
            if m is not None and m.price_usd and m.price_usd > 0:
                self._pending_path.append((ca, now, m.price_usd, m.market_cap, m.liquidity_usd, m.vol_5m, "followup"))
                n += 1
            elif res is not None:                     # DexScreener answered: no pair any more
                self._pending_path.append((ca, now, None, None, None, None, "followup_no_pair"))
        self.counters["followup_points"] += n
        self._flush()
        return n

    # ------------------------------------------------------------------ housekeeping
    def _flush(self) -> None:
        if self._pending_snaps:
            self.db.executemany(f"INSERT INTO token_snapshots ({','.join(SNAP_COLS)}) VALUES "
                                f"({','.join('?' * len(SNAP_COLS))})", self._pending_snaps)
            self.counters["snapshots"] += len(self._pending_snaps)
            self._pending_snaps = []
        if self._pending_path:
            self.db.executemany("INSERT INTO price_path VALUES (?,?,?,?,?,?,?)", self._pending_path)
            self.counters["path_points"] += len(self._pending_path)
            self._pending_path = []
        self.db.commit()

    def _prune(self, now: float) -> None:
        self._last_prune = now
        cut = now - KEEP_DAYS * 86400
        for tbl, col in (("token_snapshots", "ts"), ("price_path", "ts"), ("candidates", "ts")):
            self.db.execute(f"DELETE FROM {tbl} WHERE {col} < ?", (cut,))
        self.db.execute("DELETE FROM forward_returns WHERE anchor_ts < ?", (cut,))
        self.db.execute("DELETE FROM token_discovery WHERE first_seen_ts < ? AND done = 1", (cut,))
        self.db.commit()

    def summary(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]  # noqa: E731
        return {"path": str(self.path.name), "tokens": q("SELECT COUNT(*) FROM token_discovery"),
                "snapshots": q("SELECT COUNT(*) FROM token_snapshots"),
                "price_points": q("SELECT COUNT(*) FROM price_path"),
                "forward_returns": q("SELECT COUNT(*) FROM forward_returns"),
                "candidates": q("SELECT COUNT(*) FROM candidates"),
                "labelled": q("SELECT COUNT(*) FROM token_discovery WHERE done=1"),
                "in_memory": len(self.t), **{"session_" + k: v for k, v in self.counters.items()}}

    def close(self) -> None:
        self._flush()
        self.db.close()


EXPORT_TABLES = {"token_discovery": "first_seen_ts", "token_snapshots": "ts", "price_path": "ts",
                 "forward_returns": "anchor_ts", "candidates": "ts"}


def export_csv(path: str | Path, table: str, since: float = 0.0, limit: int = 200000):
    """Yields CSV lines (header first) of `table` rows newer than `since` (whitelisted table names only).
    Uses its own read-only connection (WAL: never blocks the recorder)."""
    import csv
    import io
    col = EXPORT_TABLES[table]
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    cur = db.execute(f"SELECT * FROM {table} WHERE {col} >= ? ORDER BY {col} LIMIT ?", (since, limit))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([d[0] for d in cur.description])
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        w.writerows(rows)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
    if buf.getvalue():
        yield buf.getvalue()
    db.close()
