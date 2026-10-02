"""SAMPLE EPOCH: which paper trades count in the current sample (no parameter or strategy change while counting).

An epoch starts the first time the bot runs with a given (STRATEGY_VERSION, parameter fingerprint). A restart or a
deploy that changes neither keeps the epoch (e.g. reporting-only commits); a parameter change or a strategy-code
change starts a new one. Trades opened before the epoch start, under another epoch, or with no epoch tag are LEGACY
and are excluded from the sample report.

STRATEGY_VERSION is bumped by hand whenever code that changes entries, exits, sizing, risk or costs changes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STRATEGY_VERSION = "s13-fill-error-haircut"   # C5: a HARD exit whose fill raises SELL_FILL_ERROR_MAX (3) times is filled at the haircut; fill errors back off
# (previous: s12-stale-healthy-clock, never deployed)
MIN_SAMPLE_COMMIT = "148af2c"            # the server must run this commit or a descendant
LEGACY = "LEGACY"


class SampleEpoch:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.strategy_version = STRATEGY_VERSION
        self.fingerprint: str | None = None
        self.commit: str | None = None
        self.started_at: float | None = None

    @classmethod
    def load(cls, path: Path) -> "SampleEpoch":
        """Read a stored epoch (reporting: no new epoch is started)."""
        e = cls(None)
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        e.strategy_version, e.fingerprint, e.commit, e.started_at = (d.get("strategy_version"), d.get("fingerprint"),
                                                                      d.get("commit"), d.get("started_at"))
        return e

    @property
    def id(self) -> str:
        return f"{self.strategy_version}:{self.fingerprint}:{int(self.started_at or 0)}"

    def start(self, fingerprint: str, commit: str, now: float | None = None) -> bool:
        """Keep the stored epoch if strategy version and parameters are unchanged, else start a new one now.
        Returns True when a new epoch started."""
        now = now or time.time()
        old = None
        if self.path is not None:
            try:
                old = json.loads(Path(self.path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = None
        self.fingerprint, self.commit = fingerprint, commit
        if old and old.get("strategy_version") == self.strategy_version and old.get("fingerprint") == fingerprint:
            self.started_at = old["started_at"]
            new = False
        else:
            self.started_at = now
            new = True
        self.save()
        return new

    def save(self) -> None:
        if self.path is None:
            return
        from core.snapshot import write_atomic
        write_atomic(self.path, json.dumps(self.as_dict()).encode("utf-8"))

    def as_dict(self) -> dict:
        return {"id": self.id, "strategy_version": self.strategy_version, "fingerprint": self.fingerprint,
                "commit": self.commit, "started_at": self.started_at,
                "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_at))
                if self.started_at else None}

    def counts(self, row: dict) -> bool:
        """A journal row belongs to the current sample (not LEGACY)."""
        return (self.started_at is not None and row.get("epoch") == self.id
                and (row.get("entry_ts") or 0) >= self.started_at and not row.get("noquote"))
