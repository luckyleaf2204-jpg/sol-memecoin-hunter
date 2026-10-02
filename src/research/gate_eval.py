"""Step C — is the no-chasing gate right? (read-only, on research.db)

Groups (first event per CA):
  BLOCKED   lifecycle TRADE stopped by the gate (extension_5m > 40 % or EXTENDED / MID_MOVE), with its reasons
  ENTERED   filled BUYs
  BASELINE  random OTHER tokens matched to each event: same age bucket and liquidity bucket, observed in a token
            snapshot taken at or before the event time (|dt| <= MATCH_WINDOW_S) — never chosen with later data
Outcome per event and horizon: forward return from the price path after the event (end price near t0 + h, MFE / MAE
inside the window, TP +30 % / SL -15 % first hit). Prices are DexScreener prints (stale / wrong prints included).
"""
from __future__ import annotations

import random
import sqlite3
import statistics

HORIZONS = (("10m", 600), ("30m", 1800), ("1h", 3600))
MATCH_WINDOW_S = 300.0
BASELINE_PER_EVENT = 3
AGE_EDGES = ((300, "<5m"), (900, "5-15m"), (3600, "15-60m"), (6 * 3600, "1-6h"), (float("inf"), ">6h"))
LIQ_EDGES = ((5_000, "<5k"), (10_000, "5-10k"), (25_000, "10-25k"), (100_000, "25-100k"), (float("inf"), ">100k"))


def _bucket(v, edges) -> str:
    if v is None:
        return "UNKNOWN"
    for e, name in edges:
        if v < e:
            return name
    return edges[-1][1]


def outcome(db: sqlite3.Connection, ca: str, t0: float, p0: float, h: float) -> dict | None:
    """Forward outcome from the price path strictly AFTER t0 (the event itself uses only data <= t0)."""
    if not p0:
        return None
    pts = db.execute("SELECT ts, price FROM price_path WHERE ca=? AND ts>? AND ts<=? AND price>0 ORDER BY ts",
                     (ca, t0, t0 + h + max(60.0, 0.25 * h))).fetchall()
    inside = [p for t, p in pts if t < t0 + h]
    after = [p for t, p in pts if t >= t0 + h]                    # same convention as research.dataset._forward
    end = after[0] if after else (inside[-1] if inside and t0 + h - max(t for t, _ in pts if t < t0 + h)
                                  <= max(30.0, 0.1 * h) else None)
    if end is None:
        return None
    window = inside + [end]
    rets = [p / p0 - 1 for p in window]
    first = None
    for r in rets:
        if r >= 0.30:
            first = "tp30"
            break
        if r <= -0.15:
            first = "sl15"
            break
    return {"ret": 100 * (end / p0 - 1), "mfe": 100 * max(rets), "mae": 100 * min(rets), "first_hit": first}


def _summary(outs: list[dict]) -> dict:
    o = [x for x in outs if x is not None]
    if not o:
        return {"n": 0}
    rets = [x["ret"] for x in o]
    tp = sum(1 for x in o if x["first_hit"] == "tp30")
    sl = sum(1 for x in o if x["first_hit"] == "sl15")
    return {"n": len(o), "mean_ret_pct": round(statistics.fmean(rets), 2), "median_ret_pct": round(statistics.median(rets), 2),
            "win_rate_pct": round(100 * sum(1 for r in rets if r > 0) / len(o), 1),
            "mean_mfe_pct": round(statistics.fmean(x["mfe"] for x in o), 2),
            "mean_mae_pct": round(statistics.fmean(x["mae"] for x in o), 2),
            "tp_share_reference": round(tp / (tp + sl), 3) if tp + sl else None}


def events(db: sqlite3.Connection, since: float | None = None) -> dict[str, list[dict]]:
    out = {"blocked": [], "entered": []}
    q = ("SELECT ca, MIN(ts), kind, reasons, extension_5m_pct, entry_location, age_s, liquidity_usd, price "
         "FROM gate_events WHERE ts >= ? GROUP BY ca, kind")
    for ca, ts, kind, reasons, ext, loc, age, liq, price in db.execute(q, (since or 0.0,)):
        if kind in out:
            out[kind].append({"ca": ca, "ts": ts, "reasons": reasons, "extension": ext, "location": loc, "age_s": age,
                              "liq": liq, "price": price})
    return out


def baseline_matches(db: sqlite3.Connection, ev: dict, exclude: set[str], rng: random.Random) -> list[tuple]:
    """Other tokens with the same age / liquidity bucket in a snapshot taken at or before the event (no look-ahead)."""
    rows = db.execute("SELECT ca, ts, age_sec, liq_usd, price_usd FROM token_snapshots WHERE ts<=? AND ts>=? "
                      "AND price_usd>0", (ev["ts"], ev["ts"] - MATCH_WINDOW_S)).fetchall()
    ab, lb = _bucket(ev["age_s"], AGE_EDGES), _bucket(ev["liq"], LIQ_EDGES)
    seen, pool = set(), []
    for ca, ts, age, liq, price in sorted(rows, key=lambda r: -r[1]):            # latest snapshot per token
        if ca in exclude or ca in seen:
            continue
        seen.add(ca)
        if _bucket(age, AGE_EDGES) == ab and _bucket(liq, LIQ_EDGES) == lb:
            pool.append((ca, ts, price))
    return rng.sample(pool, min(BASELINE_PER_EVENT, len(pool)))


def evaluate(db_path: str, seed: int = 7, since: float | None = None) -> dict:
    """since: only gate events at or after it (the sample epoch start)."""
    db = sqlite3.connect(db_path)
    try:
        ev = events(db, since)
        rng = random.Random(seed)
        exclude = {e["ca"] for g in ev.values() for e in g}
        out = {"n_blocked": len(ev["blocked"]), "n_entered": len(ev["entered"])}
        tot = out["n_blocked"] + out["n_entered"]
        out["block_rate_pct"] = round(100 * out["n_blocked"] / tot, 1) if tot else None
        reasons: dict[str, int] = {}
        for e in ev["blocked"]:
            for r in (e["reasons"] or "[]").strip("[]").replace('"', "").split(", "):
                if r:
                    key = "extension_5m > 40%" if "extension" in r else r
                    reasons[key] = reasons.get(key, 0) + 1
        out["block_reasons"] = reasons
        matches = {k: [(e, baseline_matches(db, e, exclude, rng)) for e in g] for k, g in ev.items()}
        out["by_horizon"] = {}
        for name, h in HORIZONS:
            row = {}
            for k, g in ev.items():
                row[k] = _summary([outcome(db, e["ca"], e["ts"], e["price"], h) for e in g])
                row[f"baseline_for_{k}"] = _summary([outcome(db, ca, ts, p, h) for e, ms in matches[k] for ca, ts, p in ms])
            out["by_horizon"][name] = row
        out["note"] = ("forward returns on DexScreener prints; baseline = same age / liquidity bucket, chosen from "
                       "snapshots at or before each event; INSUFFICIENT below 30 events per group")
        out["sample"] = {k: "INSUFFICIENT (<30)" if len(g) < 30 else "OK" for k, g in ev.items()}
        return out
    finally:
        db.close()
