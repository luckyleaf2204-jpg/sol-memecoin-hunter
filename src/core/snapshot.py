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

FILES = ("paper_bot.json", "sample_epoch.json", "holdout_lock.json", "trading.json", "truth_ledger.json",
         "price_history.json", "research.db")   # priority order: the book and the epoch first, the big DB last
SLOW = ("research.db",)               # may be carried over from the previous generation when it does not fit in time
OPTIONAL = ("price_history.json",)    # restored with the rest, but never part of the "partial local data" check
GENERATIONS = 3                       # rotating slots: the live generation is never overwritten
MANIFEST = "manifest.json"
PROBE = "probe.json"
NOT_DURABLE = "MẪU KHÔNG BỀN - sẽ mất khi restart"
SNAPSHOT_FAIL_BLOCK_N = 3            # this many failed snapshots in a row -> no new entries until one succeeds
DURABILITY_RETRY_S = 60.0            # start-up probe failed on a store error: retry this often
SHUTDOWN_DEADLINE_S = 80.0           # at SIGTERM the WHOLE snapshot() gets this long, from its first line
SHUTDOWN_SLOW_TIMEOUT_S = 60.0       # at SIGTERM: research.db gets this long, else the previous copy is kept
HOURLY_SLOW_TIMEOUT_S = 60.0         # hourly: same — a slow research.db is carried, it does not fail the snapshot
PUBLISH_RESERVE_S = 10.0             # inside the deadline: the two manifest puts keep at least this long
SHUTDOWN_TOTAL_S = 105.0             # bot stop <= 10 + snapshot <= 80 + lease release <= 15; engine stop uses the slack
# (render.yaml maxShutdownDelaySeconds 120. 10 + 80 + 15 = 105 < 110 even when every store call is slow.
#  The publish reserve is carved out of the 80s, not added on top.)
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


def _dev(path) -> int | None:
    p = Path(path).resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return os.stat(p).st_dev
    except OSError:
        return None


def same_disk_as(root, data_dir) -> list[str]:
    """Which of the system disk, the temp dir and DATA_DIR share the snapshot directory's device (st_dev)."""
    dev = _dev(root)
    refs = (("the system disk", Path(Path(root).resolve().anchor)), ("the temp dir", Path(tempfile.gettempdir())),
            ("DATA_DIR", Path(data_dir)))
    return [name for name, ref in refs if dev is not None and _dev(ref) == dev]


def durability_check(store, data_dir: Path) -> dict:
    """Start-up check: is there a durable store and can it be written AND read back?"""
    import secrets
    if store is None:
        return {"durable": False, "store": "NOT CONFIGURED", "target": "-", "warning": NOT_DURABLE,
                "reason": "SNAPSHOT_DIR / SNAPSHOT_URL not set"}
    out = {"durable": False, "store": store.kind, "target": target_of(store), "transient": False}
    if store.kind == "dir":
        try:
            Path(store.root).resolve().relative_to(Path(data_dir).resolve())
            return {**out, "warning": NOT_DURABLE, "reason": "SNAPSHOT_DIR is inside DATA_DIR (same ephemeral disk)"}
        except ValueError:
            pass
        if os.environ.get("ALLOW_LOCAL_DIR", "0") != "1":
            same = same_disk_as(store.root, data_dir)
            if same:
                return {**out, "warning": NOT_DURABLE,
                        "reason": f"SNAPSHOT_DIR is on the same disk as {', '.join(same)} (st_dev): not a separate "
                                  "mount (ALLOW_LOCAL_DIR=1 to accept a local directory)"}
    nonce = secrets.token_hex(8).encode()
    try:
        store.put(PROBE, nonce)
        ok = store.get(PROBE) == nonce
    except Exception as e:                                  # the message may carry a URL: report the type only
        return {**out, "warning": NOT_DURABLE, "reason": f"write test failed ({type(e).__name__})",
                "transient": True}
    if not ok:
        return {**out, "warning": NOT_DURABLE, "reason": "write test failed (read-back differs)", "transient": True}
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


def _gen_manifest_name(slot: int) -> str:
    return f"manifest-gen-{slot}.json"


def _parse(raw: bytes, what: str) -> dict:
    try:
        man = json.loads(raw)
    except ValueError as e:
        raise SnapshotError(f"{what} is corrupt (not JSON)") from e
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict) or "gen" not in man:
        raise SnapshotError(f"{what} is corrupt (missing gen / files)")
    return man


def _manifest(store) -> dict | None:
    """The current manifest for writing the next generation (an unreadable one restarts the numbering)."""
    raw = store.get(MANIFEST)
    if raw is None:
        return None
    try:
        return _parse(raw, MANIFEST)
    except SnapshotError:
        return None


def _free_obj(slot: int, name: str, protected: set[str]) -> str:
    """The object name to write: the slot's usual name, unless a newer generation still references it (a carried
    research.db) — then an alternate name, so a live restore point is never overwritten."""
    base = _obj(slot, name)
    for cand in (base, base[:-3] + ".alt.gz", base[:-3] + ".alt2.gz"):
        if cand not in protected:
            return cand
    raise SnapshotError(f"no free object name for {name} in slot {slot}")


def _protected(store, slot: int) -> set[str]:
    """Objects referenced by the live manifest and by every generation manifest of the OTHER slots."""
    refs = set()
    raws = [store.get(MANIFEST)] + [store.get(_gen_manifest_name(s)) for s in range(GENERATIONS) if s != slot]
    for raw in raws:
        if raw is None:
            continue
        try:
            m = _parse(raw, "manifest")
        except SnapshotError:
            continue
        refs |= {meta.get("object") for meta in m["files"].values() if meta.get("object")}
    return refs


def _put_verified(store, slot: int, name: str, raw: bytes, gen: int, protected: set[str] = frozenset()) -> dict:
    gz = gzip.compress(raw, 6)
    sha = hashlib.sha256(raw).hexdigest()
    obj = _free_obj(slot, name, protected)
    store.put(obj, gz)
    back = store.get(obj)
    if back is None or hashlib.sha256(gzip.decompress(back)).hexdigest() != sha:
        raise SnapshotError(f"verify failed for {name}: manifest not switched (generation {gen} discarded)")
    return {"sha256": sha, "size": len(raw), "size_gz": len(gz), "object": obj}


CRITICAL = ("paper_bot.json", "sample_epoch.json", "holdout_lock.json", "trading.json")   # never carried over


def _timed(fn, timeout: float | None):
    """Run fn in a worker thread, at most `timeout` seconds (None = no limit). Raises TimeoutError."""
    if timeout is None:
        return fn()
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=max(timeout, 0.0))
    except cf.TimeoutError as e:
        raise TimeoutError from e
    finally:
        ex.shutdown(wait=False)


def snapshot(data_dir: Path, store, commit: str = "", now: float | None = None,
             slow_timeout_s: float | None = None, deadline_s: float | None = None) -> dict:
    """Write generation N+1 into its own slot (GENERATIONS slots, rotating), read every object back and verify its
    checksum, and only then switch the manifest to it. A failed write / verify leaves the previous generation as
    the restore point (the manifest still points to it).
    Files go in priority order (book, epoch, lock first; research.db last). With slow_timeout_s (shutdown), a SLOW
    file that is not written + verified in time keeps the previous generation's verified copy (`carried_from`), so
    the book is never lost waiting for the research DB. `steps` gives the seconds of every file.
    deadline_s (shutdown): ONE budget for this whole call, measured from the start — manifest reads, the protected-object
    reads, every file upload and the two final manifest writes. A CRITICAL file (book / epoch / lock / config) not
    written in time aborts the generation (SnapshotError: the previous verified generation stays the restore point,
    never a mix of an old book and a new epoch); any other file not written in time keeps its previous verified copy.
    When the deadline is at least PUBLISH_RESERVE_S, a SLOW file's cap is min(slow_timeout, remaining - reserve) so
    the two manifest puts still have that reserve. Shorter deadlines (unit tests) keep the previous 1s publish grace.
    The manifest is not switched when the budget runs out, so a late critical file cannot publish a mixed generation.
    With slow_timeout_s set (hourly and shutdown), a non-critical file that errors is carried the same way, so one
    slow research.db cannot fail the generation."""
    t0 = time.time()
    now = now or t0
    _log_research_db(data_dir)
    end = None if deadline_s is None else t0 + max(float(deadline_s), 0.0)

    def _run(fn, cap: float | None = None, grace: float = 0.0):
        """Run fn within the time still left in the deadline (and within cap, when given).
        grace: when the budget is already spent, still wait this long — only for the manifest switch of a
        generation whose files are already done, so a carried slow file does not throw away the book."""
        limit = None if end is None else end - time.time()
        if cap is not None:
            limit = cap if limit is None else min(limit, cap)
        if limit is not None and limit <= 0:
            if grace <= 0:
                raise TimeoutError
            limit = grace
        return _timed(fn, limit)

    try:
        prev = _run(lambda: _manifest(store) or {}) or {}
        gens = [prev.get("gen", 0)]

        def _older_gens():
            found = []
            for slot_i in range(GENERATIONS):         # an unreadable main manifest must not reuse a live slot
                raw = store.get(_gen_manifest_name(slot_i))
                if raw is None:
                    continue
                try:
                    found.append(_parse(raw, "generation manifest")["gen"])
                except SnapshotError:
                    pass
            return found

        gens.extend(_run(_older_gens))
        gen = max(gens) + 1
        slot = gen % GENERATIONS
        protected = _run(lambda: _protected(store, slot))
    except TimeoutError:
        raise SnapshotError(f"deadline: snapshot reads not finished in {deadline_s}s — generation discarded, "
                            f"the previous generation stays the restore point") from None
    files, steps = {}, {}
    for name in FILES:
        p = Path(data_dir) / name
        if not p.exists():
            continue
        ts = time.time()
        cap = None
        if name in SLOW and slow_timeout_s is not None:
            cap = float(slow_timeout_s)
            if end is not None and deadline_s is not None and float(deadline_s) >= PUBLISH_RESERVE_S:
                rem = end - time.time()
                cap = min(cap, max(0.0, rem - PUBLISH_RESERVE_S))   # leave the manifest puts their reserve
        try:
            files[name] = _run(lambda p=p, name=name: _put_verified(store, slot, name, _read(p), gen, protected),
                               cap)
        except TimeoutError:
            if name in CRITICAL:
                raise SnapshotError(f"deadline: {name} not written in {deadline_s}s — generation {gen} discarded, "
                                    f"generation {prev.get('gen')} stays the restore point") from None
            old = (prev.get("files") or {}).get(name)
            if old is not None:                             # the previous generation's verified copy
                files[name] = {**old, "carried_from": old.get("carried_from", prev.get("gen"))}
            steps[name + " (timeout, carried)" if old else name + " (timeout, omitted)"] = round(time.time() - ts, 2)
            continue
        except Exception:
            if name in CRITICAL or slow_timeout_s is None:
                raise
            old = (prev.get("files") or {}).get(name)      # do not fail the generation, and do not overwrite it
            if old is not None:
                files[name] = {**old, "carried_from": old.get("carried_from", prev.get("gen"))}
            steps[name + " (error, carried)" if old else name + " (error, omitted)"] = round(time.time() - ts, 2)
            continue
        steps[name] = round(time.time() - ts, 2)
    man = {"gen": gen, "slot": slot, "ts": now, "commit": commit, "files": files, "steps": steps,
           "bytes_raw": sum(f["size"] for f in files.values()), "bytes_gz": sum(f["size_gz"] for f in files.values()),
           "duration_s": round(time.time() - t0, 2), "target": target_of(store), "store": store.kind}
    payload = json.dumps(man).encode()

    def _publish():
        store.put(_gen_manifest_name(slot), payload)   # this generation's own manifest (fallback)
        store.put(MANIFEST, payload)                   # the switch: one object write

    # A deadline long enough to hold the reserve spends that reserve on the two puts (no extra second past
    # the deadline). A shorter deadline keeps the 1s grace so a carried slow file can still publish.
    publish_grace = 0.0 if (deadline_s is not None and float(deadline_s) >= PUBLISH_RESERVE_S) else 1.0
    try:
        _run(_publish, grace=publish_grace)
    except TimeoutError:
        raise SnapshotError(f"deadline: manifest not switched in {deadline_s}s — generation {gen} discarded, "
                            f"generation {prev.get('gen')} stays the restore point") from None
    return man


def _log_research_db(data_dir) -> None:
    p = Path(data_dir) / "research.db"
    if p.exists():
        print(f"[snapshot] research.db {p.stat().st_size} bytes", flush=True)
    else:
        print("[snapshot] research.db absent", flush=True)


def blocks_entries(exc: BaseException) -> bool:
    """A snapshot failure counts toward the no-new-orders rule only when a CRITICAL file (or the snapshot as a
    whole) failed. research.db and the other non-critical files must not block entries."""
    msg = str(exc)
    if any(name in msg for name in CRITICAL):
        return True
    if any(name in msg for name in FILES if name not in CRITICAL):
        return False
    return True


def _stage(store, man: dict, staging: Path) -> None:
    """Download + verify every file of one generation into `staging` (raises SnapshotError on any problem)."""
    for name, meta in man["files"].items():
        blob = store.get(meta.get("object") or name + ".gz")
        if blob is None:
            raise SnapshotError(f"missing object for {name} (generation {man.get('gen')})")
        try:
            data = gzip.decompress(blob)
        except (OSError, EOFError) as e:
            raise SnapshotError(f"unreadable object for {name} (generation {man.get('gen')})") from e
        if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
            raise SnapshotError(f"checksum mismatch for {name} (generation {man.get('gen')})")
        (staging / name).write_bytes(data)


def restore(data_dir: Path, store) -> dict:
    """ALL-OR-NOTHING restore. Raises SnapshotError (-> the server HALTS) when:
      * the manifest is corrupt,
      * the data directory holds only SOME of the snapshot's files (leftovers of a broken state),
      * no generation verifies.
    If all of the snapshot's files are already present the local data is newer: nothing is restored. If the current
    generation is corrupt the restore falls back to the newest older generation that verifies (and says so)."""
    raw = store.get(MANIFEST)
    if raw is None:
        return {"restored": [], "skipped": [], "snapshot_ts": None, "gen": None, "status": "NO SNAPSHOT", "at": time.time()}
    man = _parse(raw, MANIFEST)
    data_dir = Path(data_dir)
    names = [n for n in man["files"] if n not in OPTIONAL]
    present = [n for n in names if (data_dir / n).exists()]
    if present and len(present) == len(names):
        return {"restored": [], "skipped": names, "snapshot_ts": man.get("ts"), "gen": man.get("gen"),
                "status": "LOCAL DATA PRESENT (not restored)", "at": time.time()}
    if present:
        raise SnapshotError(f"partial local data: {', '.join(present)} present but "
                            f"{', '.join(n for n in names if n not in present)} missing — not mixing two states")
    candidates = [man]
    for slot in range(GENERATIONS):                    # older generations, newest first
        rawg = store.get(_gen_manifest_name(slot))
        if rawg is None:
            continue
        try:
            g = _parse(rawg, _gen_manifest_name(slot))
        except SnapshotError:
            continue
        if g["gen"] < man["gen"]:
            candidates.append(g)
    candidates = [candidates[0]] + sorted(candidates[1:], key=lambda g: -g["gen"])
    data_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for g in candidates:
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=data_dir))
        try:
            _stage(store, g, staging)
            for name in g["files"]:                     # all verified: move them in
                os.replace(staging / name, data_dir / name)
        except SnapshotError as e:
            errors.append(str(e))
            continue
        finally:
            for f in staging.glob("*"):
                f.unlink(missing_ok=True)
            staging.rmdir()
        fell_back = g is not candidates[0]
        return {"restored": list(g["files"]), "skipped": [], "snapshot_ts": g.get("ts"), "gen": g.get("gen"),
                "commit": g.get("commit"), "errors": errors, "fell_back_from": man["gen"] if fell_back else None,
                "status": f"RESTORED (fell back to generation {g['gen']}: generation {man['gen']} corrupt)"
                if fell_back else "RESTORED", "at": time.time()}
    raise SnapshotError("no generation verifies: " + " | ".join(errors))


def fmt_bytes(n: int | None) -> str:
    return "-" if n is None else f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.1f} kB"
