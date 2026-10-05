# Trend research (direction A) — PRE-REGISTERED test plan

Written and committed BEFORE any price data is downloaded. Nothing below may be changed after the data is seen;
a changed rule is a NEW test with a new name, run on data that did not choose it.

Research only: no bot, no order, no wallet. Paper numbers.

## Question

Does a plain trend-following rule on established, liquid Solana tokens make money AFTER realistic costs, out of
sample, better than random entries held for the same time?

## Data

* Source: GeckoTerminal public API, hourly OHLCV in USD of the token's pool. The public API serves only the last
  180 days: that is the whole window (a known limit: one market regime).
* Universe: the fixed list `src/trend/universe.py::CANDIDATES` (well-known Solana tokens, chosen by name before any
  return was looked at; stables, SOL and liquid-staking tokens excluded). Each mint is checked against the API's
  symbol (mismatch -> dropped). Pool per token: its largest pool quoted in SOL or USDC/USDT with reserve >= $1M
  today and created >= 200 days ago. Tokens without such a pool are dropped (listed in the report).
* Survivorship bias: the list is today's well-known tokens and the liquidity filter uses today's reserve. Both
  favour survivors and make results look BETTER than reality. A pass therefore needs caution; a fail is strong.

## Rule T1 (the only rule; no parameter search)

* Bars: 1 hour. A signal is computed on a CLOSED bar and filled at the NEXT bar's open (no look-ahead).
* Entry (flat): close > highest high of the previous 168 bars (7 days) AND volume of the last 24 bars > the average
  24-bar volume of the previous 168 bars.
* Exit (long): close < lowest low of the previous 72 bars (3 days). An open trade at the end of the data is closed at
  the last close (marked "end of data").
* One position per token at a time, long only.

## Costs (per round trip, all charged)

* Pool fee 0.30 % per side.
* Price impact per side = size / (pool reserve / 2) (today's reserve; constant-product approximation).
* Latency / execution slippage 0.50 % per side.
* Fixed fee per transaction: (0.00011 + 0.005 priority) SOL at $150 = $0.7665; 2 transactions.
* Primary position size $100. Also reported (not decisive): $50, and priority fee 0.001 SOL.
* The fill at the next bar's open already contains the gap between the signal and the fill.

## Split

* In-sample: the first 60 % of the time window; holdout: the last 40 %. A trade belongs to the period of its entry.
  No parameter is fitted on either part (T1 is fixed), so the in-sample part is a consistency check only.

## Metrics

* Per trade: net return % after all costs at the primary size.
* Mean net return per trade with a 95 % bootstrap CI, resampling whole TOKENS (trades of a token are correlated).
* Win rate, average win / loss, profit factor, number of trades, number of tokens.
* Random baseline: 2,000 draws of the same number of trades per token, same holding durations, entries at random
  bars of the same period, same costs -> p = share of draws whose mean net >= the observed mean.
* Context only: buy-and-hold of each token over the holdout.

## Verdict (decided here, before the data)

PASS only if ALL hold on the HOLDOUT at the primary size:
1. n >= 100 trades,
2. lower bound of the 95 % per-token CI of the mean net return > 0,
3. random-baseline p < 0.05,
and the in-sample mean net return is > 0.

Otherwise REJECT: T1 is not traded (paper or live) and its parameters are NOT tuned on this data.
If PASS: paper trading only, >= 2-4 weeks on durable infrastructure, before any live consideration.

## Amendment 1 (data plumbing, before ANY price or return was seen)

The first universe build stopped on an unknown mint (HTTP 404) and dropped tokens wrongly: the API returns
'$WIF' for WIF, and pools where the token is the QUOTE side ("SOL / BONK") were ignored. Fixed: 404 -> token
dropped; a leading '$' is ignored in the symbol check; both pool orientations are accepted and bars are requested
for the token's own mint (`token=<mint>`). Rule T1, costs, split and verdict are unchanged.
