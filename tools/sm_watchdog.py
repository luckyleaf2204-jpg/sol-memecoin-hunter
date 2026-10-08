"""Watchdog for the smart-money recorder: every 60 s, if the recorder is not running (or its heartbeat is older than
5 min), start it again; the recorder itself logs the downtime as a RECORDER_GAP. Exits when the fixed window has
finished. One watchdog at a time. Started at logon by the Startup-folder entry (tools/sm_autostart.cmd).

usage: pythonw tools/sm_watchdog.py   (or python, to see its log on the console)"""
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney.recorder import pid_alive  # noqa: E402

DATA = ROOT / "data" / "smartmoney"
DB = DATA / "trades.db"
STALE_S = 300.0
EVERY_S = 60.0


def log(msg: str) -> None:
    with open(DATA / "watchdog.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def state() -> tuple[int | None, float | None, bool]:
    pid = None
    try:
        pid = int((DATA / "recorder.lock").read_text().strip() or 0) or None
    except (OSError, ValueError):
        pass
    hb, finished = None, False
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        meta = dict(db.execute("SELECT k, v FROM meta"))
        db.close()
        hb = float(meta["heartbeat"]) if "heartbeat" in meta else None
        finished = "finished" in meta
    except (sqlite3.Error, ValueError):
        pass
    return pid, hb, finished


def decide(pid_ok: bool, hb: float | None, finished: bool, now: float) -> str:
    """'exit' | 'ok' | 'restart_stale' | 'start'"""
    if finished:
        return "exit"
    if pid_ok:
        return "restart_stale" if hb is not None and now - hb > STALE_S else "ok"
    return "start"


def start(reason: str) -> None:
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    out = open(DATA / "recorder.log", "a", encoding="utf-8")       # append: earlier logs are never overwritten
    err = open(DATA / "recorder.err", "a", encoding="utf-8")
    exe = sys.executable.replace("pythonw.exe", "python.exe")
    subprocess.Popen([exe, str(ROOT / "tools" / "sm_record.py"), "--days", "21", "--reason", reason],
                     cwd=str(ROOT), stdout=out, stderr=err, creationflags=flags, close_fds=True)
    log(f"started recorder ({reason})")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    lock = DATA / "watchdog.lock"
    try:
        other = int(lock.read_text().strip() or 0)
    except (OSError, ValueError):
        other = 0
    if other and other != os.getpid() and pid_alive(other):
        return
    lock.write_text(str(os.getpid()))
    log(f"watchdog up (pid {os.getpid()})")
    while True:
        pid, hb, finished = state()
        d = decide(pid_alive(pid), hb, finished, time.time())
        if d == "exit":
            log("window finished: watchdog exits")
            break
        if d == "restart_stale":
            log(f"recorder pid {pid} alive but heartbeat {time.time() - hb:.0f}s old: killing it")
            subprocess.run(["taskkill", "/PID", str(pid), "/F"] if os.name == "nt" else ["kill", "-9", str(pid)],
                           capture_output=True)
            time.sleep(3)
            start("watchdog: heartbeat stale (process hung)")
        elif d == "start":
            start("watchdog: recorder not running (reboot / crash / killed)")
        time.sleep(EVERY_S)


if __name__ == "__main__":
    main()
