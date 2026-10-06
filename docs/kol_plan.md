# KOL copy research (direction C) — PRE-REGISTERED test plan

Written 2026-10-06, BEFORE any recorded trade was viewed or analysed. Research only: no bot, no order, no wallet of
ours. Nothing below may be changed after the data is seen (a change = a new test). Code fixed now:
`src/smartmoney/kol.py`, `tools/sm_kol.py` (tests: `tests/test_smartmoney_kol.py`).

## Question

Public "KOL" wallets are what social copy-trading apps (Fomo, kolscan, GMGN) let people follow. Does copying their
pump.fun curve buys, with a realistic delay and costs, make money — and more than copying random active wallets?

## Wallet set (frozen)

* `docs/kol_wallets_20261006.json`: the KOL roster embedded in kolscan.io/leaderboard (timeframes 1 / 7 / 30 days),
  fetched 2026-10-06: **565 wallets**, sha256 `9bdf7dd311a0bad9a6e79cbc0247b09dd03943f5ca58eec8c90ee041293ec939`.
  The selection was made by kolscan before our window, so the whole window is out-of-sample: no formation period.

## Data

* The smart-money recorder database (docs/smart_money_plan.md, amendments 1-3): every pump.fun bonding-curve trade
  >= 0.001 SOL for 21 days from 2026-10-06, gaps logged. PumpSwap (after migration) is NOT recorded.

## Eligibility (KOLs and baseline alike)

* >= 3 distinct tokens bought with a single buy >= 0.05 SOL inside the window. KOLs below that are not copied
  (count reported).

## Copy rule, costs

* Identical to the smart-money plan (analysis.py `copies`, `net_pct`): trigger = a wallet's first buy >= 0.05 SOL of
  a token; entry = first swap >= 3 s later; exit = first swap >= 3 s after the wallet's first sell, or the last
  curve price before completion ("migrated"), or the last price before 24 h / window end; no swap after the trigger
  = -100 %; holdings overlapping a gap > 5 min are excluded. ~5.6 % round-trip cost on a 0.33 SOL position.

## Metrics

* Mean net return per copied trade, 95 % bootstrap CI resampling whole wallets (and by token, reported).
* Random baseline: a fixed-seed sample of up to 5,000 eligible non-KOL wallets; 2,000 draws of as many wallets as
  there are eligible KOLs, same rule and costs; p = share of draws whose mean >= the KOL mean.
* Report only (not decisive): delay 10 s; the KOLs' own return on the same tokens (their buy price -> their first
  sell price, same costs) — the gap to the copy return is what being a follower costs.

## Verdict (fixed now)

* PASS only if n >= 100 copied trades, lower bound of the per-wallet CI > 0 and random p < 0.05.
* n < 100: INCONCLUSIVE (expected risk: KOLs trade mostly after migration, which is not recorded). Otherwise REJECT.
* Any verdict other than PASS: no KOL copy bot, no tuning on this data. PASS: paper copy-trading only, >= 2-4 weeks.
