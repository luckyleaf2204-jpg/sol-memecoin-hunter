# Smart-money copy research (direction B) — PRE-REGISTERED test plan

Written BEFORE any wallet or swap data is collected. Research only: no bot, no order, no wallet of ours. Returns are
measured in SOL on swap prices; nothing below may be changed after the data is seen (a change = a new test).

## Question

Do wallets that made money on Solana memecoins in one period keep doing so in the NEXT period, by enough that
copying their buys (with a realistic delay and costs) beats copying random wallets of the same kind?

## Data

* Swaps (wallet = fee payer, token amount, SOL amount, time) of a sample of PumpSwap memecoins, from Helius
  parsed transaction history of each pool. Token sample: every PumpSwap pool created in the collection window that
  reached the sampling rule fixed in the data-source decision (prospective: all new pools recorded live, no
  survivorship; historical: a list fixed before download). Swaps below 0.05 SOL are ignored (dust / bots).
* Price of a swap = SOL amount / token amount. Time = block time.
* Window split in TIME: formation = first 50 %, test = last 50 %.

## Wallet selection (formation period only)

* Realized round trip per (wallet, token): SOL in for buys, SOL out for sells (FIFO on tokens); unsold tokens at the
  end of formation are valued at 0 (a rug is a loss, not "unrealized").
* Eligible: >= 5 distinct tokens traded, average buy >= 0.5 SOL, not a pool / program account.
* SELECTED: the top 20 eligible wallets by total realized profit in SOL.

## Copy rule (test period)

* Trigger: a selected wallet's FIRST buy of a token in the test period.
* Entry: the price of the first swap in that pool at least D = 3 s after the trigger (D = 10 s also reported, not
  decisive).
* Exit: the price of the first swap at least D after the wallet's first sell of that token; no sell within 24 h ->
  the last price before 24 h; pool with no swap after the trigger -> trade lost (-100 %).
* One copy per (wallet, token); the same token copied from two wallets counts twice (reported).

## Costs (per round trip, all charged)

* 1.25 % per side (0.25 % pool fee + 1.0 % impact / slippage) and 2 fixed fees of (0.00011 + 0.005) SOL on a position
  of 0.33 SOL (~$50): ~5.6 % per round trip.

## Metrics

* Net return per copied trade %, mean with a 95 % bootstrap CI resampling whole WALLETS; also per token.
* Random baseline: 2,000 draws of 20 wallets from the eligible-but-not-selected wallets, copied with the same rule
  and costs; p = share of draws whose mean >= the selected wallets' mean.

## Verdict (fixed now)

PASS only if, in the TEST period: n >= 100 copied trades, lower bound of the per-wallet 95 % CI > 0, and random
baseline p < 0.05. Otherwise REJECT: no copy bot, no parameter tuning on this data.
If PASS: paper copy-trading only, >= 2-4 weeks, before any live consideration.

## Amendment 1 — data source chosen (before ANY trade was recorded)

* Source: the pump.fun program's own TradeEvent / CompleteEvent logs, streamed live from the public Solana RPC
  (`logsSubscribe` mentions 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P). BONDING-CURVE trades only: the PumpSwap
  stream (~170 GB/day) is not recorded. Token universe = every token traded on the curve during the window
  (no survivorship). Swap price = sol_amount / token_amount of the TradeEvent.
* Window: 21 days of recording from the first recorded trade (or less if stopped; then the actual span is split
  50 / 50 the same way). Connection gaps are recorded; copy trades whose holding overlaps a gap > 5 min are excluded.
* Formation valuation: tokens a wallet still holds at the end of formation are valued at that token's last curve
  price at that time (a dead token's last price already contains its collapse); a token whose curve completed
  (CompleteEvent) is valued at its last curve price before completion.
* Copy exit when the token completes its curve before the wallet sells: the last curve price before completion
  (flagged "migrated"; an approximation — the AMM price is not recorded). Other exit rules unchanged.
* Trades below 0.001 SOL are not stored (dust); the 0.05 SOL rule for wallet selection is unchanged.

## Amendment 2 — precise accounting (written while recording, before ANY recorded trade was analysed or viewed)

* The 0.05 SOL rule: a token counts towards a wallet's ">= 5 distinct tokens" only if the wallet bought >= 0.05 SOL
  of it in one trade, and a copy TRIGGER is a buy of >= 0.05 SOL. Profit uses every stored trade (>= 0.001 SOL) of
  the wallet in that token: profit = SOL out + tokens still held x valuation price - SOL in.
* Average buy (eligibility) = mean SOL of the wallet's buys >= 0.05 SOL in the formation period.
* Prices for entry / exit / valuation = sol_amount / token_amount of a single recorded trade (any wallet).
* A copy whose exit point falls after the end of the window exits at the last price before the end ("end").
* Program / pool accounts cannot be told apart from wallets in TradeEvent data (the event's `user` is the signer):
  no extra filter.

## Amendment 3 — corrupt event + restart (2026-10-06, no recorded trade analysed or viewed beyond MIN/MAX ts)

* The first run stopped after ~26 minutes: one event decoded with ts = -2.9e17 became MIN(ts), so the "21 days since
  the first trade" stop fired at once. That one row was deleted; the decoder now drops events whose block time is
  before 2024-01-01 or more than a day ahead of the clock.
* The downtime between the last stored trade and the restart is written to `gaps` (same exclusion rule as any gap).
  The 21-day window still counts from the first valid trade. No analysis rule changed.

## Amendment 4 — gap rule covers the whole copy (2026-10-08, no recorded trade analysed or viewed)

* OLD (wrong): a copy was excluded only if a gap > 5 min overlapped [entry fill, exit fill]. Two holes: (1) a signal
  just before a gap was "entered" at the first trade AFTER the gap — possibly hours later — and counted; (2) a signal
  with no recorded trade afterwards was counted -100 % even when the reason was the recorder being down.
* NEW: a copy is excluded ("gap") when a RECORDER_GAP > 5 min overlaps ANY part of [signal, max(exit decision time,
  exit fill)] — signal -> entry, entry -> exit and every lookup in between. A signal with no trade afterwards counts
  -100 % only if no gap overlaps [signal, window end]; otherwise it is excluded. Excluded copies are counted and
  reported (status "gap"), never silently dropped.
* Direction C uses the same copy function, so this applies to C as well.

## Amendment 5 — migration outcome on PumpSwap, never the last curve price (2026-10-08, no data viewed)

* OLD (optimistic): when the curve completed while a copy was open, the copy exited at the LAST BONDING-CURVE PRICE
  before completion ("migrated"). That is the curve's top; PumpSwap trading after migration is not recorded, so the
  outcome was not a real executable price and biased results upward (toward PASS).
* NEW: price lookups (entry, exit, max-hold, window end) and the wallet's first sell use curve trades AND PumpSwap
  trades (`amm_trades`). A copy whose [signal, exit] contains the completion time is valued only if the token's
  PumpSwap price path (`amm_fetch`) and that wallet's PumpSwap activity (`amm_wallet_fetch`) are complete over
  [completion, exit]; otherwise it is "unresolved". ANY unresolved copy (selected wallets or baseline) makes the
  verdict BLOCKED_MIGRATION_DATA: the analysis is blocked until the PumpSwap data is fetched. Unresolved copies are
  never dropped (dropping them would select on an outcome — migration).
* Migration time = the CompleteEvent block time (`completes.ts`; receipt time `completes.recv_ms` from 2026-10-08).
* The PumpSwap data is fetched AFTER the window by a separate tool (amendment 5a). Formation-period valuation (selection only) is unchanged.

## Amendment 5a — the PumpSwap fetcher (2026-10-10, no recorded trade analysed or viewed)

Implemented as `src/smartmoney/pumpswap.py` + `tools/sm_pumpswap.py` (fetch refused before the window end). No
outcome rule changes; this only says how amendment 5's data is obtained and when it counts as complete.
* Mints: every curve completion in the window; range [completion, min(completion + 24 h, window end)] (a copy open
  at the completion has trigger <= completion and span <= trigger + 24 h). Wallet marks: every wallet with a
  >= 0.05 SOL curve buy of the mint up to the completion.
* Pool: GeckoTerminal, dex `pumpswap`, base = the mint, quote = WSOL, created within 10 min of the completion;
  none or more than one -> failed (no guessing).
* History: Helius `getTransactionsForAddress(pool)` in block-time order over the range, every page to the end.
  Swap = the pool's own vault change (quote WSOL / base token, lamports per raw token as on the curve); wallet = fee
  payer; failed transactions and liquidity changes are skipped; one row per transaction (unique, re-runs idempotent).
* Complete only if: every page read (transient errors retried, request errors not), block times inside the range
  and non-decreasing, the pool's creation transaction is the start of the history, its initial reserve price is
  within 5x of the last curve price (a unit / wrong-pool check — the first SWAP is not used: a migration-second
  snipe moved one real pool 38x), and the page cap (400 per mint) was not hit. Otherwise nothing is written for the
  mint except a log row: its copies stay unresolved and the verdict stays BLOCKED_MIGRATION_DATA.
* Limits: the vault delta includes the ~0.2 % LP fee; a relayer-paid swap is attributed to the relayer; trading on
  other pools / DEXes is not seen.

## Amendment 6 — fixed window and stopping rule (2026-10-08)

* The window is FIXED: first valid trade 2026-10-06 01:13:48 UTC + 21 days = **2026-10-27 01:13:48 UTC**; formation
  / test cut 2026-10-16 13:13:48 UTC. The words "or less if stopped" (amendment 1) are withdrawn.
* Recorder downtime, crashes or a stopped recorder are NOT a stopping rule: the gap is logged (RECORDER_GAP), the
  recorder is restarted, the window end does not move.
* No analysis of B or C before 2026-10-27 01:13:48 UTC, whatever the sample size or how results might look.
  `tools/sm_analyze.py` and `tools/sm_kol.py` refuse to run before that time. `tools/sm_status.py` shows operations
  only (gaps, completeness, storage) and never computes an outcome.

## Recorder instrumentation (2026-10-08, data quality only — no rule above depends on it)

* `trades.recv_ms` / `completes.recv_ms`: receipt time of each event from 2026-10-08 (NULL before; not back-filled).
* `gaps.reason`, `recorder_events`, heartbeat, automatic startup gap, watchdog + logon autostart.
* RPC_COMPLETENESS samples (`rpc_checks`); events found only by those samples go to `recovered_events` and are NOT
  used by the analysis.
