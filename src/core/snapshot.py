"""Durable paper-sample data on an ephemeral host (Render free plan: no persistent disk; a deploy, a restart or a
spin-down wipes DATA_DIR).

Every SNAPSHOT_EVERY_S (default 1 h) and on a graceful shutdown (Render sends SIGTERM before a deploy / restart) the
sample files are gzip-copied to a durable store; on start-up, files missing from DATA_DIR are restored from the
latest snapshot BEFORE the bot and the research recorder open them. Existing local files are never overwritten.

Stores (environment, configured by the owner — nothing is created here):
  SNAPSHOT_DIR=/path                       a mounted persistent disk / any durable directory
  SNAPSHOT_URL=https://host/prefix         HTTP store: PUT / GET {url}/{name} (S3-compatible gateway, WebDAV, ...)
  SNAPSHOT_TOKEN=...                       optional bearer token for the HTTP store (never logged)
Without one of them: NOT CONFIGURED — reported on /healthz and in the sample report (the sample restarts with the
container).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

FILES = ("paper_bot.json", "sample_epoch.json", "truth_ledger.json", "trading.json", "research.db")
MANIFEST = "manifest.json"
SNAPSHOT_EVERY_S = float(os.environ.get("SNAPSHOT_EVERY_S", "3600") or 3600)


class LocalStore:
    kind = "dir"

    def __init__(self, root):
        self.root = Path(root)

    def put(self, name: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.root / name)

    def get(self, name: str) -> bytes | None:
        p = self.root / name
        return p.read_bytes() if p.exists() else None


class HttpStore:
    kind = "http"

    def __init__(self, url: str, token: str = "", timeout: float = 60.0):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def put(self, name: str, data: bytes) -> None:
        import httpx
        r = httpx.put(f"{self.url}/{name}", content=data, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()

    def get(self, name: str) -> bytes | None:
        import httpx
        r = httpx.get(f"{self.url}/{name}", headers=self._headers(), timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content


def store_from_env():
    d = os.environ.get("SNAPSHOT_DIR", "").strip()
    if d:
        return LocalStore(d)
    u = os.environ.get("SNAPSHOT_URL", "").strip()
    if u:
        return HttpStore(u, os.environ.get("SNAPSHOT_TOKEN", "").strip())
    return None


def _read(path: Path) -> bytes:
    """A consistent copy: SQLite through its online backup API (WAL safe), other files as they are."""
    if path.suffix == ".db":
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "copy.db"
            src = sqlite3.connect(path)
            out = sqlite3.connect(dst)
            try:
                src.backup(out)
            finally:
                out.close()
                src.close()
            return dst.read_bytes()
    return path.read_bytes()


def snapshot(data_dir: Path, store, commit: str = "", now: float | None = None) -> dict:
    now = now or time.time()
    files = {}
    for name in FILES:
        p = Path(data_dir) / name
        if not p.exists():
            continue
        raw = _read(p)
        store.put(name + ".gz", gzip.compress(raw, 6))
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    man = {"ts": now, "commit": commit, "files": files}
    store.put(MANIFEST, json.dumps(man).encode())
    return man


def restore(data_dir: Path, store) -> dict:
    """Restore the files that are MISSING locally from the latest snapshot (checksum verified)."""
    raw = store.get(MANIFEST)
    if raw is None:
        return {"restored": [], "skipped": [], "snapshot_ts": None, "status": "NO SNAPSHOT"}
    man = json.loads(raw)
    restored, skipped = [], []
    for name, meta in man.get("files", {}).items():
        dst = Path(data_dir) / name
        if dst.exists():
            skipped.append(name)                       # never overwrite live local data
            continue
        blob = store.get(name + ".gz")
        if blob is None:
            skipped.append(name)
            continue
        data = gzip.decompress(blob)
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise ValueError(f"snapshot checksum mismatch for {name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".restore")
        tmp.write_bytes(data)
        tmp.replace(dst)
        restored.append(name)
    return {"restored": restored, "skipped": skipped, "snapshot_ts": man.get("ts"), "commit": man.get("commit"),
            "status": "RESTORED" if restored else "NOTHING TO RESTORE"}
