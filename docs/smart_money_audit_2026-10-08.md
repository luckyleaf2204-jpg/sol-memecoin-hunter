# Direction B — recording / methodology hardening, 2026-10-08

No recorded trade was analysed or viewed for this work. Read only: MIN / MAX block time, row counts, file sizes.
Plan changes are amendments 4-6 in `docs/smart_money_plan.md` (and amendment 1 in `docs/kol_plan.md`).

## 1. Gap rule (amendment 4)

| | old `copies()` | new `copies_with_status()` |
|---|---|---|
| gap check | gap > 5 min overlapping [entry fill, exit fill] | gap > 5 min overlapping [signal, max(exit decision, exit fill)] |
| signal before a gap | entered at the first trade after the gap, counted | excluded, status `gap` |
| no trade after the signal | -100 % always | -100 % only if no gap overlaps [signal, window end]; else `gap` |
| excluded copies | dropped silently | returned with a status and counted |

Synthetic demonstration (old function from HEAD 7a30870, same data as the new tests): a copy across a 1 h gap
counted +91.9 %; a token silent during a 30 000 s gap counted -100 %; a migrated token counted +189.4 % at the
curve top. New function: `gap`, `gap`, `unresolved`.

## 2. PumpSwap / migration outcome (amendment 5) — analysis BLOCKED until fetched

* Still on the curve: the recorded TradeEvent price `sol_amount / token_amount` (unchanged).
* Old after migration: `_last_price_before(mint, completion)` = the last curve price = the curve's top. Not an
  executable post-migration price; PumpSwap is not recorded. Optimistic, toward PASS.
* Migration time: CompleteEvent block time (`completes.ts`, 1 s resolution); receipt `completes.recv_ms` from now on.
* New: every lookup merges curve trades and PumpSwap trades (`amm_trades`); the wallet's first sell is looked up on
  both; a copy whose [signal, exit] contains the completion needs complete PumpSwap price data for the token
  (`amm_fetch`) AND complete PumpSwap activity of that wallet for the token (`amm_wallet_fetch`) over
  [completion, exit], otherwise it is `unresolved` and the verdict is `BLOCKED_MIGRATION_DATA`.

**Fetcher design (as first planned; BUILT 2026-10-10 with the changes in plan amendment 5a and section 7):**
1. List the (wallet, mint) copies whose span contains a completion, for the selected wallets AND the baseline
   pools — computed from the curve data with `copies_with_status` (status `unresolved`), no outcome needed.
2. Per mint: find its PumpSwap pool (GeckoTerminal `/networks/solana/tokens/{mint}/pools`, dex `pumpswap`), then the
   pool's swaps from the completion time to the latest exit needed: Helius parsed history of the POOL address
   (`/v0/addresses/{pool}/transactions?type=SWAP`, paged back to the completion) -> `amm_trades` rows (ts, mint,
   wallet = fee payer, side, SOL, tokens) + one `amm_fetch` row (from_ts, to_ts) only when paging reached the
   completion without an error. Because the pool history contains every wallet, the same fetch fills
   `amm_wallet_fetch` for every copied wallet of that mint.
3. Cost bound: 100 Helius credits per page of 100 swaps; measure the number of unresolved mints first and stop with
   BLOCKED (not a partial result) if the budget does not cover all of them.
4. Tests to add with it: pool lookup (pumpswap only), paging stops at the completion, fee-payer = wallet, a page
   error leaves `amm_fetch` unwritten (copy stays unresolved), idempotent re-run.

Formation-period valuation (wallet SELECTION, not the outcome) still uses the last curve price for completed tokens:
unchanged (it decides who is copied, not how a copy is scored).

## 3. Timestamps

| item | source | resolution | used for |
|---|---|---|---|
| chain time | TradeEvent / CompleteEvent `timestamp` (= block time) | 1 s | every rule (signal, entry, exit, window, gaps) |
| receipt time | `trades.recv_ms`, `completes.recv_ms` (from 2026-10-08 ~09:1x UTC) | ms | measurement only |
| signal | the wallet's first >= 0.05 SOL buy: its chain time | 1 s | |
| entry | first recorded trade with chain time >= signal + 3 s | 1 s | |
| exit | first trade with chain time >= wallet sell + 3 s / last price <= cap | 1 s | |
| migration | CompleteEvent chain time | 1 s | |

"3 s latency" is 3 s of CHAIN time with 1 s resolution (effective 2-4 s), not 3 s after our receipt. Before
2026-10-08 receipt times were not stored and are not reconstructed. From now on `tools/sm_status.py` reports
receipt minus block time (median / p90 / max) as a limitation measure; no rule uses it.

## 4. RPC completeness

Mechanism (`completeness_check`, every 5 min): take a signature the stream delivered ~60 s ago; ask
`getSignaturesForAddress(pump program, before=it, limit=1000)` -> a contiguous slot range (~3 s); drop its lowest
and highest slot (possibly partial); expected = the signatures left; received = those the stream delivered.
Missing ones (up to 20 per sample) are fetched with `getTransaction` and their events go to `recovered_events`
(NOT used by the analysis). Metric: `RPC_COMPLETENESS = sum received / sum expected` over successful samples,
`UNKNOWN` without samples. Also reported: expected, received, missing, recovered tx / events, failed samples,
reconnects (`recorder_events`), gaps.
Limits: a 3 s sample every 5 min (~1 % of the time), failed transactions included in both counts, completeness of the
TradeEvent decoding inside delivered transactions is not measured, samples during a gap are not taken.

## 5. Recorder reliability

* The 33.2 h outage (2026-10-06 22:55 -> 2026-10-08 08:08 UTC) came from a PC reboot; nothing restarted the recorder.
* Now: heartbeat every flush; at start the downtime since the heartbeat is written as a RECORDER_GAP with a reason;
  stream reconnects and silences > 30 s are gaps; single-instance lock; `tools/sm_watchdog.py` restarts a dead or
  hung recorder every 60 s and is started at logon from the Startup folder; logs are appended, never overwritten.
* Limits: it runs only while Windows is on and the user is logged on (no admin service); sleep / hibernation still
  makes gaps (logged); the existing two gaps keep reason NULL (logged before reasons existed) and are not edited.

## 6. Storage (2026-10-08 09:03 UTC)

268 MB, 3 301 475 trades, ~81 B per trade, ~318 MB per recorded day (~46 trades/s). Split (estimate — this SQLite
build has no `dbstat`): `trades` + its two indexes ~88 %, `names` (mints + wallets) ~11 %, rest < 1 %. New columns add
~2-8 B per row; `rpc_checks` / `recorder_events` / `recovered_events` stay in the kB-MB range. Projection to
2026-10-27 01:13 UTC: ~6.2 GB; free on D: ~16 GB -> ~11 GB left (margin ~2.9x the remaining growth). No compression or
retention is applied; none is needed for the window.

## 7. Follow-up 2026-10-10: recorder crash fixed, PumpSwap fetcher built

**Recorder crash (2026-10-08 09:12 -> 2026-10-09 01:25 UTC).** The completeness sampler ran `completeness_check`
in `asyncio.to_thread`; it wrote through the Store's SQLite connection from the worker thread (`ProgrammingError`,
outside the try) and also iterated the live `SigWindow` deque there (`deque mutated during iteration`, seen once).
Either ended `asyncio.gather`; the watchdog restarted the recorder each time (~160 restarts, each gap < 5 min).
Fix (commit 3311bc3): connection ownership — the anchor and a snapshot of received signatures are taken on the
event-loop thread, only HTTP runs in the worker (`completeness_probe`, no Store), rows are written back on the
loop thread (`save_check`); a sampler failure is logged (`sampler_error` + an rpc_checks error row) and never ends
the recorder. RPC_COMPLETENESS is UNKNOWN until 12 successful samples. Regression tests reproduce the old call
(ProgrammingError) and show the recorder keeps storing trades through sampler failures.
Evidence after the fix (recorder pid 2028 from 2026-10-09 01:25:26 UTC to 2026-10-10 08:14 UTC, ~30.8 h): 0
restarts, 1 stream reconnect (23 s gap), 369 completeness samples, 0 failed, 0 sampler errors;
RPC_COMPLETENESS 1.0 (338 195 expected = received). Caveat: both sides are the same public RPC; failed transactions
are included; decoding completeness inside delivered transactions is not measured.

**PumpSwap fetcher** (plan amendment 5a). Live check on an EXTERNAL token (not from the recorded data), pool
3TXrbe…, first 30 min after its creation: 41 pages, 3 731 swaps, a re-run gave the same 3 731 distinct rows;
per-minute last swap price vs GeckoTerminal minute close: 30 minutes, ratio min 0.996 / median 1.000 / max 1.006.
The pool's initial reserve price was 0.000326 lamports per raw token; the first swap (2 476 SOL, same second) paid
38x that — why the consistency check uses the creation reserves, not the first swap.
Cost: that busy pool needed ~82 pages per hour; a 24 h range of such a pool exceeds the 400-page cap (it would stay
unresolved), and at ~100 credits per page the whole fetch may not fit a free Helius plan. Run `--plan` after the
window to count mints, then decide the budget; until every needed mint is complete the verdict stays BLOCKED.
