"""One ACTIVE instance per snapshot store (Part 2.1 / 2.2).

Render's zero-downtime deploy starts the new instance, switches traffic to it, and only 60 s later sends SIGTERM to
the old one (render.com/docs/deploys) — so two instances run side by side for >= 60 s. Without a lock both would
trade and both would write snapshots. The lease is a small object `instance_lease.json` in the snapshot store:

  {"owner": <instance id>, "acquired_at", "renewed_at", "expires_at", "released": bool}

  * an instance that does not hold the lease does NOT restore, trade or snapshot: it waits (STANDBY) and polls
  * the holder renews every LEASE_RENEW_S; a lease not renewed for LEASE_TTL_S is free (crashed holder)
  * on SIGTERM the holder writes its last snapshot, THEN releases the lease; the standby instance takes it and
    only then restores — so the new instance starts from the old one's final state (data loss ~ 0 instead of up to
    one hourly snapshot)
  * a holder that finds the lease taken by someone else when renewing stops trading and snapshots (LEASE LOST)

LIMIT: a plain file / HTTP object store has no compare-and-swap. Two instances acquiring at the same moment are
separated by write -> wait ACQUIRE_SETTLE_S -> read back: the last writer wins and the other backs off. This is a
best-effort lock for the deploy hand-over (instances start ~ a minute apart), not a consensus protocol."""
from __future__ import annotations

import json
import os
import secrets
import socket
import time

LEASE = "instance_lease.json"
LEASE_TTL_S = 180.0
LEASE_RENEW_S = 60.0
LEASE_POLL_S = 10.0
ACQUIRE_SETTLE_S = 2.0


def instance_id() -> str:
    base = os.environ.get("RENDER_INSTANCE_ID") or socket.gethostname()
    return f"{base}-{os.getpid()}-{secrets.token_hex(3)}"


class Lease:
    def __init__(self, store, owner: str | None = None, ttl: float = LEASE_TTL_S, settle_s: float | None = None,
                 clock=time.time, sleep=time.sleep):
        self.store, self.owner, self.ttl, self.settle_s = store, owner or instance_id(), ttl, settle_s
        self.clock, self.sleep = clock, sleep
        self.status = "NOT ACQUIRED"
        self.last_ok: float | None = None             # last successful acquire / renew (self-fencing clock)

    def read(self) -> dict | None:
        raw = self.store.get(LEASE)
        if raw is None:
            return None
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) and "owner" in d and "expires_at" in d else None
        except ValueError:
            return None                                    # unreadable lease = free (it only guards, holds no data)

    def _held_by_other(self, d: dict | None, now: float) -> bool:
        return bool(d) and d["owner"] != self.owner and not d.get("released") and d["expires_at"] > now

    def _write(self, now: float, acquired_at: float, released: bool = False) -> None:
        self.store.put(LEASE, json.dumps({"owner": self.owner, "acquired_at": acquired_at, "renewed_at": now,
                                          "expires_at": now if released else now + self.ttl,
                                          "released": released}).encode())

    def acquire(self) -> bool:
        now = self.clock()
        d = self.read()
        if self._held_by_other(d, now):
            self.status = f"STANDBY: held by {d['owner']} until {d['expires_at']:.0f}"
            return False
        self._write(now, now)
        settle = ACQUIRE_SETTLE_S if self.settle_s is None else self.settle_s
        if settle:
            self.sleep(settle)                      # another instance writing now overwrites us: back off
        mine = (self.read() or {}).get("owner") == self.owner
        self.status = "HELD" if mine else "STANDBY: lost the acquire race"
        if mine:
            self.last_ok = self.clock()
        return mine

    def held(self) -> bool:
        d = self.read()
        return bool(d) and d["owner"] == self.owner and not d.get("released") and d["expires_at"] > self.clock()

    def renew(self) -> bool:
        """True = still ours (renewed). False ONLY when another instance holds it (or we released it): that is the
        one case to stop. A missing (404) or unreadable lease is rewritten as ours (nobody else holds a valid one);
        a store error RAISES — the caller treats it as transient and self-fences after the TTL (see app)."""
        d = self.read()
        if d is not None and (d["owner"] != self.owner or d.get("released")):
            self.status = f"LOST to {d['owner']}" if d["owner"] != self.owner else "LOST (released)"
            return False
        now = self.clock()
        self._write(now, (d or {}).get("acquired_at", now))
        self.status = "HELD" if d is not None else "HELD (lease object was missing / unreadable: rewritten)"
        self.last_ok = now
        return True

    def fenced(self, ttl: float | None = None) -> bool:
        """No successful renew for longer than the TTL: another instance may legitimately hold the lease now."""
        return self.last_ok is not None and self.clock() - self.last_ok > (ttl or self.ttl)

    def release(self) -> None:
        d = self.read()
        if d and d["owner"] == self.owner:
            self._write(self.clock(), d.get("acquired_at", self.clock()), released=True)
            self.status = "RELEASED"

    def as_dict(self) -> dict:
        return {"owner": self.owner, "status": self.status}
