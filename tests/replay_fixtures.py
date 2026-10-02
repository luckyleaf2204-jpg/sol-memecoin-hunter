"""Synthetic research.db for replay tests: lifecycle candidates with forward_returns + snapshots, and baseline tokens
(same age / liquidity bucket) whose price path produces a chosen TP / SL first hit."""
import sqlite3

from research.dataset import SCHEMA, migrate

HZ = "1m"                       # short horizon: embargo 60 s < the 100 s spacing of candidates
PRICE = {"tp30": 1.35, "sl15": 0.80, None: 1.0}


def make_db(path, cand_hits, base_hits, t0=10_000.0, step=100.0, engine="lifecycle", horizon=HZ, base_liq=7000.0,
            engines=None):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    migrate(db)                                           # engine column on candidates
    fr = "INSERT INTO forward_returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    snap = "INSERT INTO token_snapshots (snapshot_id, ca, ts, age_sec, liq_usd, price_usd) VALUES (?,?,?,?,?,?)"
    for i, h in enumerate(cand_hits):
        ca, ts = f"C{i}", t0 + i * step
        db.execute(fr, (ca, "candidate", ts, 1.0, horizon, 60, 1.1, ts, 10.0, 35.0, -5.0, int(h == "tp30"),
                        int(h == "sl15"), h, 10, "tracked"))
        db.execute("INSERT INTO candidates (ca, symbol, ts, bought, engine) VALUES (?,?,?,?,?)",
                   (ca, ca, ts, i % 2, (engines[i] if engines else engine)))
        db.execute(snap, (f"c{i}", ca, ts, 200, 7000.0, 1.0))
    span = max(step * max(len(cand_hits), 1), 1.0)
    for j, h in enumerate(base_hits):
        ca = f"B{j}"
        ts = t0 - 200 + j * (span + 200) / max(len(base_hits), 1)
        db.execute(snap, (f"b{j}", ca, ts, 210, base_liq, 1.0))
        db.executemany("INSERT INTO price_path VALUES (?,?,?,?,?,?,?)",
                       [(ca, ts + 30, PRICE[h], None, None, None, "s"), (ca, ts + 60, PRICE[h], None, None, None, "s")])
    db.commit()
    db.close()
