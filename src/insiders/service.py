"""Server side of the dashboard's Insiders page: serves the last scan (bundled result.json or the latest run) and
runs a new scan in a background thread with the server's HELIUS_API_KEY (never sent to the browser)."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from insiders import scan as S


def light(result: dict | None) -> dict | None:
    """The page needs per-token counts, not the 40 early wallets of every token (keeps the phone payload small)."""
    if not result:
        return result
    toks = [{**{k: v for k, v in t.items() if k != "buyers"}, "n_buyers": len(t.get("buyers") or []),
             "n_pre_call": sum(1 for b in t.get("buyers") or [] if b.get("pre_call"))} for t in result.get("tokens", [])]
    return {**result, "tokens": toks}


class InsiderService:
    def __init__(self, result_path: Path = S.RESULT_JSON, key: str | None = None):
        self.result_path = result_path
        self.key = key if key is not None else os.environ.get("HELIUS_API_KEY")
        self.result = None
        self.running = False
        self.started_at = None
        self.error = None
        self.log = deque(maxlen=30)
        self.lock = threading.Lock()
        try:
            self.result = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.result = None

    def status(self) -> dict:
        return {"running": self.running, "started_at": self.started_at, "error": self.error,
                "progress": list(self.log), "can_run": bool(self.key), "result": light(self.result)}

    def start(self, runner=None) -> bool:
        """Start a scan unless one is running. Returns False when it cannot start."""
        with self.lock:
            if self.running or not self.key:
                return False
            self.running, self.started_at, self.error = True, time.time(), None
            self.log.clear()
        threading.Thread(target=self._run, args=(runner,), daemon=True).start()
        return True

    def _run(self, runner) -> None:
        try:
            if runner is not None:
                res = runner(self.log.append)
            else:
                chain = S.Chain(self.key, S.default_cache())
                res = S.run(chain, S.load_calls(), progress=self.log.append)
            if self.result and self.result.get("deep") and not res.get("deep"):
                res["deep"] = {**self.result["deep"], "from_earlier_scan": True}   # deep trace runs offline
            self.result = res
            try:
                Path(self.result_path).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass                                         # read-only deploy: kept in memory
        except Exception as e:                               # type + message only (no URL / key)
            self.error = f"{type(e).__name__}: {str(e)[:160]}"
        finally:
            self.running = False
