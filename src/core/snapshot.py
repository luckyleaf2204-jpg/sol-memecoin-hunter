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

FILES = ("paper_bot.json", "sample_epoch.json", "truth_ledger.json", "trading.json", "holdout_lock.json",
         "research.db", "bwkw_shadow.db")
GENERATIONS = 3                       # rotating slots: the live generation is never overwritten
MANIFEST = "manifest.json"
PROBE = "probe.json"
NOT_DURABLE = "MẪU KHÔNG BỀN - sẽ mất khi restart"
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


def write_atomic(path, data: bytes) -> None:
    """Write a file so that a crash never leaves it half-written (temp file in the same dir + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def target_of(store) -> str:
    """Where snapshots go, safe to log / show: a directory path, or scheme://host/path of the HTTP store — never the
    token, user info or query string."""
    if store is None:
        return "-"
    if store.kind == "dir":
        return str(store.root)
    from urllib.parse import urlsplit
    u = urlsplit(store.url)
    return f"{u.scheme}://{u.hostname or ''}{f':{u.port}' if u.port else ''}{u.path}"


def durability_check(store, data_dir: Path) -> dict:
    """Start-up check: is there a durable store and can it be written AND read back?"""
    import secrets
    if store is None:
        return {"durable": False, "store": "NOT CONFIGURED", "target": "-", "warning": NOT_DURABLE,
                "reason": "SNAPSHOT_DIR / SNAPSHOT_URL not set"}
    out = {"durable": False, "store": store.kind, "target": target_of(store)}
    if store.kind == "dir":
        try:
            Path(store.root).resolve().relative_to(Path(data_dir).resolve())
            return {**out, "warning": NOT_DURABLE, "reason": "SNAPSHOT_DIR is inside DATA_DIR (same ephemeral disk)"}
        except ValueError:
            pass
    nonce = secrets.token_hex(8).encode()
    try:
        store.put(PROBE, nonce)
        ok = store.get(PROBE) == nonce
    except Exception as e:                                  # the message may carry a URL: report the type only
        return {**out, "warning": NOT_DURABLE, "reason": f"write test failed ({type(e).__name__})"}
    if not ok:
        return {**out, "warning": NOT_DURABLE, "reason": "write test failed (read-back differs)"}
    return {**out, "durable": True, "reason": "write + read-back OK"}


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


class SnapshotError(RuntimeError):
    pass


def _obj(slot: int, name: str) -> str:
    return f"gen-{slot}--{name}.gz"                  # flat object names (no "/"): any simple key-value store works


def _manifest(store) -> dict | None:
    raw = store.get(MANIFEST)
    return json.loads(raw) if raw is not None else None


def snapshot(data_dir: Path, store, commit: str = "", now: float | None = None) -> dict:
    """Write generation N+1 into its own slot (GENERATIONS slots, rotating), read every object back and verify its
    checksum, and only then switch the manifest to it. A failed write / verify leaves the previous generation as
    the restore point (the manifest still points to it)."""
    t0 = time.time()
    now = now or t0
    prev = _manifest(store)
    gen = (prev or {}).get("gen", 0) + 1
    slot = gen % GENERATIONS
    files = {}
    for name in FILES:
        p = Path(data_dir) / name
        if not p.exists():
            continue
        raw = _read(p)
        gz = gzip.compress(raw, 6)
        sha = hashlib.sha256(raw).hexdigest()
        store.put(_obj(slot, name), gz)
        back = store.get(_obj(slot, name))
        if back is None or hashlib.sha256(gzip.decompress(back)).hexdigest() != sha:
            raise SnapshotError(f"verify failed for {name}: manifest not switched (generation {gen} discarded)")
        files[name] = {"sha256": sha, "size": len(raw), "size_gz": len(gz), "object": _obj(slot, name)}
    man = {"gen": gen, "slot": slot, "ts": now, "commit": commit, "files": files,
           "bytes_raw": sum(f["size"] for f in files.values()), "bytes_gz": sum(f["size_gz"] for f in files.values()),
           "duration_s": round(time.time() - t0, 2), "target": target_of(store), "store": store.kind}
    store.put(MANIFEST, json.dumps(man).encode())   # the switch: one object write
    return man


def restore(data_dir: Path, store) -> dict:
    """ALL-OR-NOTHING restore of the manifest's generation: every file is downloaded and checksum-verified into a
    staging directory first; only if all are good are they moved into DATA_DIR. If any sample file already exists
    locally nothing is restored (no mixing of two states). Any failure raises SnapshotError and writes nothing."""
    man = _manifest(store)
    if man is None:
        return {"restored": [], "skipped": [], "snapshot_ts": None, "gen": None, "status": "NO SNAPSHOT", "at": time.time()}
    data_dir = Path(data_dir)
    present = [n for n in man.get("files", {}) if (data_dir / n).exists()]
    if present:
        return {"restored": [], "skipped": list(man["files"]), "snapshot_ts": man.get("ts"), "gen": man.get("gen"),
                "status": "LOCAL DATA PRESENT (not restored)", "at": time.time()}
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=data_dir))
    try:
        for name, meta in man["files"].items():
            blob = store.get(meta.get("object") or name + ".gz")
            if blob is None:
                raise SnapshotError(f"missing object for {name} (generation {man.get('gen')})")
            data = gzip.decompress(blob)
            if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                raise SnapshotError(f"checksum mismatch for {name} (generation {man.get('gen')})")
            (staging / name).write_bytes(data)
        for name in man["files"]:                     # all verified: move them in
            os.replace(staging / name, data_dir / name)
    finally:
        for f in staging.glob("*"):
            f.unlink(missing_ok=True)
        staging.rmdir()
    return {"restored": list(man["files"]), "skipped": [], "snapshot_ts": man.get("ts"), "gen": man.get("gen"),
            "commit": man.get("commit"), "status": "RESTORED", "at": time.time()}


def fmt_bytes(n: int | None) -> str:
    return "-" if n is None else f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.1f} kB"
