"""Smart-money research: record pump.fun bonding-curve trades live (docs/smart_money_plan.md). Runs until the fixed
window ends (first valid trade + --days, amendment 6) or until stopped. Read-only listener.

* One instance only (data/smartmoney/recorder.lock holds the PID).
* At start, the time since the last heartbeat is written as a RECORDER_GAP with --reason.
* Writes recorder_events (start / connect / disconnect / stop / finished) and RPC_COMPLETENESS samples.
* Stopping the process is NOT a stopping rule: the analysis window stays first trade + 21 days.

usage: python tools/sm_record.py [--db data/smartmoney/trades.db] [--days 21] [--reason "..."]"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney.recorder import (SigWindow, Store, completeness_loop, pid_alive, record,  # noqa: E402
                                 startup_gap)


def acquire_lock(lock: Path) -> bool:
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and pid_alive(pid):
            return False
    lock.write_text(str(os.getpid()))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "smartmoney" / "trades.db"))
    ap.add_argument("--days", type=float, default=21.0)
    ap.add_argument("--reason", default="process down (cause unknown)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    lock = Path(a.db).parent / "recorder.lock"
    if not acquire_lock(lock):
        print(f"[sm] another recorder is running (pid {lock.read_text().strip()}): exit", flush=True)
        sys.exit(3)
    store = Store(Path(a.db))
    if store.meta("finished"):
        print("[sm] window already finished: nothing to record", flush=True)
        return
    g = startup_gap(store, reason=a.reason)
    if g:
        print(f"[sm] RECORDER_GAP logged: {(g[1] - g[0]) / 3600:.2f} h ({a.reason})", flush=True)
    store.db.execute("INSERT OR IGNORE INTO meta VALUES ('started_at', ?)", (str(time.time()),))
    store.event("start", f"pid {os.getpid()}; {a.reason}")
    stop = asyncio.Event()
    sigs = SigWindow()

    async def run():
        async def window_end():
            f = None                                   # first valid trade: fixed once known (a full scan, not per minute)
            while not stop.is_set():
                f = f or store.db.execute("SELECT MIN(ts) FROM trades").fetchone()[0]
                if f and time.time() - f >= a.days * 86400:
                    print(f"[sm] fixed window ({a.days} days from the first trade) ended: stopping", flush=True)
                    store.set_meta("finished", time.time())
                    store.event("finished")
                    stop.set()
                await asyncio.sleep(60)
        log = lambda m: print(m, flush=True)  # noqa: E731
        await asyncio.gather(record(store, stop=stop, log=log, sigs=sigs), window_end(),
                             completeness_loop(store, sigs, stop, log=log))
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        store.flush()
        store.event("stop")
        try:
            if lock.read_text().strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass
        print("[sm] stopped", flush=True)


if __name__ == "__main__":
    main()
