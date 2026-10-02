# Deploy checklist (Render, PAPER bot)

This bot is PAPER only: no wallet key, no signing, no real transaction. A deploy changes code, never money.
Deploys happen only after the owner has reviewed the diff (branch `s6-review`) and said so.

## What a Render deploy does (render.com/docs/deploys)

* Zero-downtime: the new instance starts, passes `/healthz`, receives the traffic; **60 s later** the old instance
  gets `SIGTERM`, then `SIGKILL` after the shutdown delay (default 30 s). Both run side by side for >= 60 s.
* A service with a persistent disk has no zero-downtime deploys (the free plan has no disk at all).
* Without a snapshot store the data directory dies with the container: **"MẪU KHÔNG BỀN - sẽ mất khi restart"**.

How the app handles the overlap (`src/core/lease.py`): the instance holding `instance_lease.json` in the snapshot
store is ACTIVE (restores, trades, snapshots); the other is STANDBY (`/healthz` 200, `"role": "STANDBY"`) and does
nothing. On `SIGTERM` the active instance writes its final snapshot, THEN releases the lease; the standby takes it,
restores that final snapshot and starts. Open positions are re-evaluated on the first tick at the current price;
the downtime is recorded as a GAP and trades overlapping it leave the main sample.

## 1. Set the environment variables — ALL in ONE save

Render restarts the service on every save of the Environment page. Two saves = two restarts = two hand-overs.
Dashboard -> service -> Environment -> add every variable below, then press **Save** once:

| Variable | Value | Notes |
|---|---|---|
| `SNAPSHOT_URL` | `https://<worker>.workers.dev/hunter` | dedicated store, GET/PUT only (see `docs/sample_plan.md` 1b) |
| `SNAPSHOT_TOKEN` | the store's bearer secret | secret value; never in a file, chat, issue or screenshot |
| (or `SNAPSHOT_DIR`) | a path OUTSIDE `data/` | only with a real persistent disk (paid plan; disables zero-downtime) |
| `SNAPSHOT_EVERY_S` | `3600` (default) | optional |

Already set and NOT to be touched by this checklist: `APP_ACCESS_CODE`, `HELIUS_API_KEY`, `TELEGRAM_*`.
Optional switches (default on): `LIFECYCLE_ENGINE`, `EXPERIMENTAL_MODE`, `LATENCY_PROBE`, `RESEARCH_LOG`, `MONEYFLOW`,
`RESEARCH_ONCHAIN` (`0` = off). Keep-alive pings use `RENDER_EXTERNAL_URL` (set by Render).

## 2. Check the store after that restart

1. `GET /healthz` (public): `ok: true`, `role: "ACTIVE"`, `snapshot.durable: true`, no `snapshot.warning`,
   `lease.status: "HELD"`, the expected `commit`, `params` and `sample_epoch.strategy_version`.
2. `GET /api/snapshot` (access code): `durable: true`, `store: "http"`, `target` without any token, `last_restore`
   (`NO SNAPSHOT` the very first time), `role`, `lease`.
3. Log: `[durability] DURABLE: http https://.../hunter (write + read-back OK)` and, after the first hour,
   `[snapshot] hourly OK ...`. A `[durability] MẪU KHÔNG BỀN ...` line means the store is not working: stop here.

## 3. One controlled restart to prove the restore

1. Wait for one `[snapshot] hourly OK` (or trigger a restart: Dashboard -> Manual Deploy -> "Restart service").
2. Note the paper book before: equity, open positions, `n` of the sample report.
3. Restart. In the log of the NEW instance expect, in this order:
   `[lease] STANDBY: held by ...` -> (old instance) `[snapshot] shutdown OK` + `[lease] released after the final
   snapshot` -> `[lease] acquired after STANDBY` -> `[snapshot] restore RESTORED: ...`.
4. `/api/snapshot`: `last_restore.status = RESTORED` from a snapshot taken seconds before the restart; the book
   matches step 2; `/healthz` `role: ACTIVE`.
5. If instead `/healthz` shows `ok: false` with `halted`: the bot stopped on purpose (corrupt or partial data,
   lost lease). Read the reason, fix the store, restart. Never delete the store's files to "make it start".

## 4. Record the sample start

* From `/healthz`: `sample_epoch.started_at_utc`, `strategy_version`, `fingerprint`, `commit`.
* Write them into `docs/sample_plan.md` ("Current epoch") in a docs-only commit with `[skip render]` (a docs commit
  does not close the replay holdout: its hash uses STRATEGY_VERSION + fingerprint, not the commit).

## Known limits

* The lease is best effort (no compare-and-swap in a plain object store). It covers the deploy hand-over; it is not
  a cluster lock. If the store is unreachable at start-up the instance stays STANDBY until it can read the lease.
* A crashed instance (no SIGTERM) keeps the lease until it expires (180 s), and its data since the last snapshot
  (up to `SNAPSHOT_EVERY_S`) is lost; that time is a GAP.
* The free plan has 750 instance hours / workspace / month; when they run out the service is suspended
  (`tools/free_hours.py`).
