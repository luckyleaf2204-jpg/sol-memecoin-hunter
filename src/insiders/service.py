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
    out = {**result, "tokens": toks}
    if result.get("pnl"):
        out["pnl"] = {k: v for k, v in result["pnl"].items() if k != "rows"}
    return out


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
                "progress": list(self.log), "can_run": bool(self.key), "result": light(self.result),
                "watch": self.watcher.status() if getattr(self, "watcher", None) else None}

    def start_watch(self, state_path: Path) -> bool:
        """Start the 5-minute deployer watch in a daemon thread (public RPC; no key needed)."""
        if getattr(self, "watcher", None) or not self.result:
            return False
        from insiders.watch import Watcher, coin_meta, telegram_sender
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        levels = tuple(x for x in (os.environ.get("INSIDER_ALERT_LEVELS") or "hold,vip,vipstrong").split(",") if x)
        from insiders.paper import jupiter_quote
        self.watcher = Watcher(self.result, state_path, notify=telegram_sender(tok, chat) if tok and chat else None,
                               notify_levels=levels,
                               paper_quote=jupiter_quote if (os.environ.get("INSIDER_PAPER") or "1") != "0" else None,
                               meta=coin_meta)
        self.watch_stop = threading.Event()
        # INSIDER_MODE=vip (default): only the special wallets (45 holders + successors + the wallets they fund),
        # streamed live, plus their balances; =broad: the earlier 1 500-wallet polling as well
        self.watcher.mode = (os.environ.get("INSIDER_MODE") or "vip").lower()
        if self.watcher.mode == "broad":
            threading.Thread(target=self.watcher.run_forever, args=(self.watch_stop,), daemon=True).start()
        if (os.environ.get("INSIDER_VIP") or "1") != "0":     # real-time stream of the special wallets
            from insiders.portfolio import Portfolio
            from insiders.vip import VipStream
            from insiders.watch import Rpc
            self.watcher.vip = VipStream(self.watcher, self.result)
            self.watcher.vip.start(self.watch_stop)
            self.watcher.portfolio = Portfolio(self.watcher.vip.wallets, Rpc(gap_s=0.25))
            threading.Thread(target=self.watcher.portfolio.run_forever, args=(self.watch_stop,), daemon=True).start()
        return True

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
            if self.result and self.result.get("pnl") and not res.get("pnl"):
                res["pnl"] = {**self.result["pnl"], "from_earlier_scan": True}
            self.result = res
            try:
                Path(self.result_path).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass                                         # read-only deploy: kept in memory
        except Exception as e:                               # type + message only (no URL / key)
            self.error = f"{type(e).__name__}: {str(e)[:160]}"
        finally:
            self.running = False
