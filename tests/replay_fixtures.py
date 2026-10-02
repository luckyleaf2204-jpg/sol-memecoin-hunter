"""Synthetic research.db for replay tests: ENTERED lifecycle trades (gate_events + forward_returns anchor 'entered'),
optional gate-blocked tokens, and baseline tokens (same age / liquidity bucket) whose price path produces a chosen
TP / SL first hit."""
import sqlite3

from research.dataset import SCHEMA, migrate

HZ = "1m"                       # short horizon: embargo 60 s < the 100 s spacing of trades
PRICE = {"tp30": 1.35, "sl15": 0.80, None: 1.0}
FR = "INSERT INTO forward_returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
GE = ("INSERT INTO gate_events (ca, ts, kind, reasons, extension_5m_pct, entry_location, age_s, liquidity_usd, price, "
      "lifecycle) VALUES (?,?,?,?,?,?,?,?,?,?)")
SNAP = "INSERT INTO token_snapshots (snapshot_id, ca, ts, age_sec, liq_usd, price_usd) VALUES (?,?,?,?,?,?)"


def _event(db, ca, ts, kind, anchor, hit, horizon, lifecycle):
    db.execute(FR, (ca, anchor, ts, 1.0, horizon, 60, 1.1, ts, 10.0, 35.0, -5.0, int(hit == "tp30"),
                    int(hit == "sl15"), hit, 10, "tracked"))
    db.execute(GE, (ca, ts, kind, "[]", 5.0, "PULLBACK", 200.0, 7000.0, 1.0, lifecycle))
    db.execute(SNAP, (f"s-{ca}", ca, ts, 200, 7000.0, 1.0))


def make_db(path, cand_hits, base_hits, t0=10_000.0, step=100.0, engine="lifecycle", horizon=HZ, base_liq=7000.0,
            engines=None, blocked_hits=()):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    migrate(db)
    for i, h in enumerate(cand_hits):
        e = engines[i] if engines else engine
        _event(db, f"C{i}", t0 + i * step, "entered", "entered", h, horizon, "NEW" if e == "lifecycle" else None)
    for i, h in enumerate(blocked_hits):
        _event(db, f"X{i}", t0 + i * step + 50, "blocked", "gate_blocked", h, horizon, "NEW")
    span = max(step * max(len(cand_hits), 1), 1.0)
    for j, h in enumerate(base_hits):
        ca = f"B{j}"
        ts = t0 - 200 + j * (span + 200) / max(len(base_hits), 1)
        db.execute(SNAP, (f"b{j}", ca, ts, 210, base_liq, 1.0))
        db.executemany("INSERT INTO price_path VALUES (?,?,?,?,?,?,?)",
                       [(ca, ts + 30, PRICE[h], None, None, None, "s"), (ca, ts + 60, PRICE[h], None, None, None, "s")])
    db.commit()
    db.close()
