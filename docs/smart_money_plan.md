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
