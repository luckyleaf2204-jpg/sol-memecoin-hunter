"""Smart-money recorder status — operations only, NO outcome / return / P&L is computed or shown (safe to run during
the window; it does not peek at results). Gaps (RECORDER_GAP), reconnects, RPC_COMPLETENESS, receipt latency,
storage projection.

usage: python tools/sm_status.py [--db data/smartmoney/trades.db] [--days 21]"""
import argparse
import os
import shutil
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney.recorder import completeness_summary, estimate_storage, pid_alive  # noqa: E402


def f(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "smartmoney" / "trades.db"))
    ap.add_argument("--days", type=float, default=21.0)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = Path(a.db)
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    meta = dict(db.execute("SELECT k, v FROM meta"))
    t0, t1, n = db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM trades").fetchone()
    end = t0 + int(a.days * 86400)
    now = time.time()
    try:
        pid = int((p.parent / "recorder.lock").read_text().strip() or 0)
    except (OSError, ValueError):
        pid = 0
    hb = float(meta["heartbeat"]) if "heartbeat" in meta else None
    print(f"window: {f(t0)} -> {f(end)} (fixed); analysis allowed after {f(end)}; now {f(now)}")
    print(f"recorder: pid {pid or '-'} alive={pid_alive(pid)} heartbeat age "
          f"{'-' if hb is None else f'{now - hb:.0f}s'} finished={'finished' in meta}")
    cols = {r[1] for r in db.execute("PRAGMA table_info(gaps)")}
    q = "SELECT start, end, reason FROM gaps ORDER BY start" if "reason" in cols else "SELECT start, end, NULL FROM gaps"
    gaps = db.execute(q).fetchall()
    tot = sum(e - s for s, e, _ in gaps)
    print(f"RECORDER_GAP: {len(gaps)} gaps, {tot / 3600:.1f} h total")
    for s, e, r in gaps:
        print(f"  {f(s)} -> {f(e)}  {(e - s) / 3600:6.2f} h  {r or '(logged before 2026-10-08, reason not recorded)'}")
    ev = dict(db.execute("SELECT kind, COUNT(*) FROM recorder_events GROUP BY kind").fetchall()) \
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='recorder_events'").fetchone() else {}
    print(f"recorder events: {ev or 'none yet'} (reconnects = connect - start)")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='rpc_checks'").fetchone():
        print(f"RPC_COMPLETENESS: {completeness_summary(db)}")
    else:
        print("RPC_COMPLETENESS: UNKNOWN (no samples yet)")
    tcols = {r[1] for r in db.execute("PRAGMA table_info(trades)")}
    if "recv_ms" in tcols:
        lat = [r[0] / 1000 - r[1] for r in db.execute(
            "SELECT recv_ms, ts FROM trades WHERE recv_ms IS NOT NULL ORDER BY rowid DESC LIMIT 20000")]
        if lat:
            lat.sort()
            print(f"receipt - block time (s, block time has 1 s resolution): n {len(lat)} median "
                  f"{statistics.median(lat):.2f} p90 {lat[int(0.9 * (len(lat) - 1))]:.2f} max {lat[-1]:.2f}")
        else:
            print("receipt latency: no rows with recv_ms yet")
    size = sum(os.path.getsize(str(p) + s) for s in ("", "-wal") if os.path.exists(str(p) + s))
    recorded = max(1.0, (t1 - t0) - tot)
    st = estimate_storage(size, recorded, max(0.0, end - now), shutil.disk_usage(p.parent).free)
    print(f"storage: {size / 1e6:.0f} MB, {n:,} trades, {size / max(1, n):.0f} B/trade, {st}")


if __name__ == "__main__":
    main()
