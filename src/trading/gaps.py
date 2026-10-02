"""GAPS in the paper sample: periods > GAP_MIN_S when the bot could not watch its positions — the process slept /
restarted (Render free plan spin-down, deploy, crash) or the market feed failed. A trade whose holding period
overlaps a gap is excluded from the main sample (its exits were not managed).

State lives in the paper book (persisted and snapshotted with it): book.gaps and book.heartbeat.
"""
from __future__ import annotations

GAP_MIN_S = 300.0


class GapTracker:
    def __init__(self, book):
        self.book = book
        self.feed_down_since: float | None = None

    def _add(self, start: float, end: float, cause: str) -> None:
        self.book.gaps.append({"start": start, "end": end, "minutes": round((end - start) / 60, 1), "cause": cause})
        del self.book.gaps[:-1000]

    def on_start(self, now: float) -> None:
        """Process (re)start: the last persisted heartbeat to now is a gap if longer than GAP_MIN_S."""
        hb = self.book.heartbeat
        if hb and now - hb > GAP_MIN_S:
            self._add(hb, now, "restart / sleep")
        self.book.heartbeat = now

    def beat(self, now: float, feeds_ok: bool) -> None:
        hb = self.book.heartbeat
        if hb and now - hb > GAP_MIN_S:
            self._add(hb, now, "loop paused")
        if not feeds_ok:
            self.feed_down_since = self.feed_down_since or now
        elif self.feed_down_since is not None:
            if now - self.feed_down_since > GAP_MIN_S:
                self._add(self.feed_down_since, now, "feed down")
            self.feed_down_since = None
        self.book.heartbeat = now

    def all_gaps(self, now: float) -> list[dict]:
        out = list(self.book.gaps)
        if self.feed_down_since is not None and now - self.feed_down_since > GAP_MIN_S:
            out.append({"start": self.feed_down_since, "end": now, "minutes": round((now - self.feed_down_since) / 60, 1),
                        "cause": "feed down (ongoing)"})
        return out


def overlaps(row: dict, gaps: list[dict], now: float) -> bool:
    start, end = row.get("entry_ts") or 0.0, row.get("exit_ts") or now
    return any(start <= g["end"] and end >= g["start"] for g in gaps)
