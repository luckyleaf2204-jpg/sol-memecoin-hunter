"""Price history across a restart (G8). The scanner's in-memory price points are written to price_history.json
(right before every snapshot and at shutdown) and loaded back at start-up, so a quick restart does not restart the
300 s entry warm-up from zero.

Only a FRESH file is loaded: if the newest saved point is more than MAX_RESTORE_GAP_S older than now, the series would
have a hole the gate cannot see (history_s counts from the first point), so nothing is loaded and the warm-up starts
from scratch. Market points only (validated values, the same fields as history.store.Point); holders are not kept."""
from __future__ import annotations

import json
import time
from pathlib import Path

from history.store import Point

FILE = "price_history.json"
KEEP_S = 1800.0                 # per token: the last 30 min (entry_location looks back 600 s, the gate needs 300 s)
MAX_RESTORE_GAP_S = 120.0       # newest saved point older than this at start-up -> do not load (no hidden hole)
FIELDS = ("ts", "price", "mc", "liq", "liq_src", "vol_5m", "vol_1h", "buys_5m", "sells_5m", "buys_1h", "sells_1h",
          "pair")


def dump(store, path: Path, now: float | None = None) -> dict:
    now = now or time.time()
    out = {}
    for mint, h in list(store._h.items()):
        pts = [[getattr(p, f) for f in FIELDS] for p in list(h.points) if now - p.ts <= KEEP_S]
        if pts:
            out[mint] = pts
    newest = max((pts[-1][0] for pts in out.values()), default=None)
    from core.snapshot import write_atomic
    write_atomic(path, json.dumps({"saved_at": now, "newest_ts": newest, "fields": FIELDS, "tokens": out}).encode())
    return {"tokens": len(out), "points": sum(len(v) for v in out.values()), "newest_ts": newest}


def load(store, path: Path, now: float | None = None) -> dict:
    """Load into an EMPTY history store. Returns a status dict (never raises: a bad file means a normal warm-up)."""
    now = now or time.time()
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "NONE", "tokens": 0, "points": 0}
    except (OSError, ValueError):
        return {"status": "UNREADABLE (warm-up from scratch)", "tokens": 0, "points": 0}
    newest = d.get("newest_ts")
    gap = None if newest is None else round(now - newest, 1)
    if gap is None or gap > MAX_RESTORE_GAP_S or gap < 0 or tuple(d.get("fields") or ()) != FIELDS:
        return {"status": f"STALE (gap {gap}s > {MAX_RESTORE_GAP_S:.0f}s): warm-up from scratch", "tokens": 0,
                "points": 0, "gap_s": gap}
    n = 0
    for mint, pts in (d.get("tokens") or {}).items():
        h = store.get(mint)
        if h.points:                                  # live data already arrived: never mix it with old points
            continue
        for row in pts:
            h.points.append(Point(*row))
            n += 1
    return {"status": "LOADED", "tokens": len(d.get("tokens") or {}), "points": n, "gap_s": gap}
