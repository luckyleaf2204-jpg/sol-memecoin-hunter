"""SQLite storage: tokens, time-series snapshots, events, alerts, watchlist.

Snapshots store what the pipeline saw AT THAT MOMENT (validated market fields, holder data,
every sub-score, early-signal strength, lifecycle, data quality) so later analysis of
volume / holder / liquidity acceleration and backtests never use future information.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from core.models import Event, McTrack, TokenState

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, creator TEXT,
    created_at REAL, first_seen REAL, sources TEXT,
    twitter TEXT, telegram TEXT, website TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, mint TEXT NOT NULL,
    price REAL, mc REAL, fdv REAL, liquidity REAL,
    vol_5m REAL, vol_1h REAL, buys_5m INTEGER, sells_5m INTEGER, txns_5m INTEGER,
    holders INTEGER, top10_pct REAL, dev_pct REAL, creator TEXT,
    curve_progress REAL, complete INTEGER,
    score INTEGER, risk INTEGER, breakdown TEXT, top_holders TEXT,
    dq INTEGER, dq_status TEXT, liquidity_source TEXT,
    early_signal INTEGER, is_early INTEGER, lifecycle TEXT, subscores TEXT,
    liq_state TEXT, whale_state TEXT, holder_quality TEXT, pc_5m REAL
);
CREATE INDEX IF NOT EXISTS ix_snap_mint_ts ON snapshots(mint, ts);
CREATE INDEX IF NOT EXISTS ix_snap_score ON snapshots(score);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mint TEXT, symbol TEXT,
    type TEXT, severity TEXT, params TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_mint ON events(mint, ts);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mint TEXT, symbol TEXT,
    score INTEGER, risk INTEGER, mc REAL, message TEXT, sent_telegram INTEGER
);
CREATE INDEX IF NOT EXISTS ix_alert_mint ON alerts(mint, ts);
CREATE TABLE IF NOT EXISTS watchlist (mint TEXT PRIMARY KEY, added_at REAL, note TEXT);
CREATE TABLE IF NOT EXISTS mc_track (
    mint TEXT PRIMARY KEY, first_seen REAL, initial_mc REAL, initial_ts REAL, initial_source TEXT,
    ath_mc REAL, ath_ts REAL, path TEXT, last_pair TEXT, migrations TEXT
);
"""

MIGRATIONS = (("dq", "INTEGER"), ("dq_status", "TEXT"), ("liquidity_source", "TEXT"),
              ("early_signal", "INTEGER"), ("is_early", "INTEGER"), ("lifecycle", "TEXT"), ("subscores", "TEXT"),
              ("liq_state", "TEXT"), ("whale_state", "TEXT"), ("holder_quality", "TEXT"), ("pc_5m", "REAL"))
SNAP_COLS = ("ts", "mint", "price", "mc", "fdv", "liquidity", "vol_5m", "vol_1h", "buys_5m", "sells_5m", "txns_5m",
             "holders", "top10_pct", "dev_pct", "creator", "curve_progress", "complete", "score", "risk",
             "breakdown", "top_holders", "dq", "dq_status", "liquidity_source", "early_signal", "is_early",
             "lifecycle", "subscores", "liq_state", "whale_state", "holder_quality", "pc_5m")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after V1. Rows without dq_status = legacy V1 scoring, excluded from backtests."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(snapshots)")}
        for col, typ in MIGRATIONS:
            if col not in cols:
                self.conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {typ}")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(mc_track)")}
        for col in ("last_pair", "migrations"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE mc_track ADD COLUMN {col} TEXT")

    def _exec(self, sql: str, args=()) -> None:
        with self._lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def _query(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    # --- tokens / snapshots ---
    def upsert_token(self, st: TokenState) -> None:
        i = st.info
        self._exec(
            """INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(mint) DO UPDATE SET name=excluded.name, symbol=excluded.symbol,
               creator=COALESCE(NULLIF(excluded.creator,''), tokens.creator),
               created_at=COALESCE(excluded.created_at, tokens.created_at), sources=excluded.sources,
               twitter=excluded.twitter, telegram=excluded.telegram, website=excluded.website""",
            (i.mint, i.name, i.symbol, i.creator, i.created_at, i.discovered_at,
             ",".join(sorted(i.sources)), i.twitter, i.telegram, i.website))

    def insert_snapshots(self, states: list[TokenState], ts: float | None = None) -> None:
        ts = ts or time.time()
        rows = []
        for st in states:
            m, h, d, sc, rk, q, e = st.market, st.holders, st.dev, st.score, st.risk, st.quality, st.early
            if not m:
                continue
            subs = json.dumps({k: v.score for k, v in st.subscores.items()}) if st.subscores else None
            breakdown = json.dumps([[c[0], c[1]] for c in sc.contributions[:12]]) if sc else None
            top = json.dumps([[x.owner, round(x.pct, 3)] for x in h.top[:20]]) if h else None
            rows.append((ts, st.mint, m.price_usd, m.market_cap, m.fdv, m.liquidity_usd,
                         m.vol_5m, m.vol_1h, m.buys_5m, m.sells_5m, m.txns_5m,
                         h.holder_count if h else None, h.top10_pct if h else None,
                         d.current_pct if d and d.balance_verified else None, st.info.creator,
                         st.info.curve_progress, int(bool(st.info.complete)),
                         sc.total if sc else None, rk.score if rk else None, breakdown, top,
                         q.score if q else None, q.status if q else None, m.liquidity_source or None,
                         e.strength if e else None, (int(e.is_early) if e and e.is_early is not None else None),
                         st.lifecycle, subs,
                         st.liquidity_intel.state if st.liquidity_intel else None,
                         st.whale_intel.state if st.whale_intel else None,
                         st.holder_intel.organic if st.holder_intel else None,
                         m.price_change_5m))
        if not rows:
            return
        with self._lock:
            self.conn.executemany(
                f"INSERT INTO snapshots ({','.join(SNAP_COLS)}) VALUES ({','.join('?' * len(SNAP_COLS))})", rows)
            self.conn.commit()

    def snapshots(self, mint: str, since: float = 0) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM snapshots WHERE mint=? AND ts>=? ORDER BY ts", (mint, since))

    def stats(self) -> dict:
        r = self._query("SELECT COUNT(*) n, COUNT(DISTINCT mint) m, MIN(ts) a, MAX(ts) b FROM snapshots")[0]
        e = self._query("SELECT COUNT(*) n FROM events")[0]
        return {"snapshots": r["n"], "tokens": r["m"], "first": r["a"], "last": r["b"], "events": e["n"]}

    # --- events ---
    def insert_events(self, events: list[Event]) -> None:
        if not events:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT INTO events (ts,mint,symbol,type,severity,params,source) VALUES (?,?,?,?,?,?,?)",
                [(e.ts, e.mint, e.symbol, e.type, e.severity, json.dumps(e.params, default=str), e.source)
                 for e in events])
            self.conn.commit()

    def recent_events(self, limit: int = 300, mint: str | None = None) -> list[Event]:
        if mint:
            rows = self._query("SELECT * FROM events WHERE mint=? ORDER BY ts DESC LIMIT ?", (mint, limit))
        else:
            rows = self._query("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [Event(r["ts"], r["mint"], r["symbol"], r["type"], r["severity"], json.loads(r["params"] or "{}"),
                      r["source"]) for r in rows]

    # --- alerts ---
    def last_alert_ts(self, mint: str) -> float | None:
        r = self._query("SELECT MAX(ts) t FROM alerts WHERE mint=?", (mint,))
        return r[0]["t"] if r else None

    def insert_alert(self, st: TokenState, message: str, sent: bool) -> None:
        self._exec("INSERT INTO alerts (ts,mint,symbol,score,risk,mc,message,sent_telegram) VALUES (?,?,?,?,?,?,?,?)",
                   (time.time(), st.mint, st.info.symbol, st.score.total if st.score else None,
                    st.risk.score if st.risk else None, st.market.market_cap if st.market else None,
                    message, int(sent)))

    def recent_alerts(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))

    # --- MC journey (initial MC at discovery is written once and never overwritten) ---
    def save_mc_tracks(self, items: list[tuple[str, McTrack]]) -> None:
        rows = [(m, t.first_seen, t.initial_mc, t.initial_ts, t.initial_source, t.ath_mc, t.ath_ts,
                 json.dumps([[round(p[0], 1), p[1], p[2] if len(p) > 2 else ""] for p in t.path]),
                 t.last_pair, json.dumps(t.migrations)) for m, t in items]
        with self._lock:
            self.conn.executemany(
                """INSERT INTO mc_track (mint, first_seen, initial_mc, initial_ts, initial_source, ath_mc, ath_ts,
                                         path, last_pair, migrations) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mint) DO UPDATE SET
                   initial_mc=COALESCE(mc_track.initial_mc, excluded.initial_mc),
                   initial_ts=COALESCE(mc_track.initial_ts, excluded.initial_ts),
                   initial_source=CASE WHEN mc_track.initial_mc IS NULL THEN excluded.initial_source
                                       ELSE mc_track.initial_source END,
                   first_seen=MIN(mc_track.first_seen, excluded.first_seen),
                   ath_mc=excluded.ath_mc, ath_ts=excluded.ath_ts, path=excluded.path,
                   last_pair=excluded.last_pair, migrations=excluded.migrations""", rows)
            self.conn.commit()

    def load_mc_tracks(self, since: float = 0) -> dict[str, McTrack]:
        out = {}
        for r in self._query("SELECT * FROM mc_track WHERE first_seen >= ?", (since,)):
            try:
                path = [(float(x[0]), float(x[1]), str(x[2]) if len(x) > 2 else "") for x in json.loads(r["path"] or "[]")]
                migrations = json.loads(r["migrations"] or "[]")
            except (ValueError, TypeError, IndexError):
                path, migrations = [], []
            out[r["mint"]] = McTrack(first_seen=r["first_seen"], initial_mc=r["initial_mc"],
                                     initial_ts=r["initial_ts"], initial_source=r["initial_source"] or "",
                                     ath_mc=r["ath_mc"], ath_ts=r["ath_ts"], path=path,
                                     last_pair=r["last_pair"] or "", migrations=migrations)
        return out

    # --- watchlist ---
    def watchlist(self) -> list[str]:
        return [r["mint"] for r in self._query("SELECT mint FROM watchlist ORDER BY added_at")]

    def add_watch(self, mint: str, note: str = "") -> None:
        self._exec("INSERT OR IGNORE INTO watchlist VALUES (?,?,?)", (mint, time.time(), note))

    def remove_watch(self, mint: str) -> None:
        self._exec("DELETE FROM watchlist WHERE mint=?", (mint,))

    def close(self) -> None:
        with self._lock:
            self.conn.close()
