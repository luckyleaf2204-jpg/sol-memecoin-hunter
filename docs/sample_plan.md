# Paper-trading sample plan

Paper trading only. Nothing here sends a transaction.

## 1. Sample epoch (which trades count)

* The server prints `commit · params · strategy · sample epoch since` at start-up and exposes the same on
  `GET /healthz` (`commit`, `params`, `sample_epoch`).
* Epoch history (parameter fingerprint `02e5bdcc03`, strategy `s4-148af2c`, all descendants of `148af2c`):
  2026-10-02T05:31:15Z (commit `77d6d56`) -> 05:39:52Z (`7ce1845`) -> **2026-10-02T05:43:59Z (`cfde5ef`, current)**.
  Only trades opened at or after the current start count.
* **The Render data directory is NOT persistent**: every deploy wipes `sample_epoch.json`, `paper_bot.json`
  (book + trade journal) and `research.db`, so every deploy restarts the sample from zero. While counting:
  do not deploy (commits that must not deploy carry `[skip render]`), or attach a persistent disk to the service.
* Trades opened earlier, in another epoch or without an epoch tag are **LEGACY** and excluded from every report.
* A new epoch starts automatically when the parameter fingerprint (`TradingConfig.sample_id()`) or
  `trading/sample_epoch.STRATEGY_VERSION` changes. Deploys that change neither (reports, docs) keep the epoch
  (as long as the server's data directory survives the deploy).

## 2. No parameter change while counting

Strategy parameters (gate 40 %, haircut 30 %, SL, TP, sizing, costs) are frozen during a sample.
`tests/test_stepA_version.py` pins the fingerprints (`78322bacfd` default, `02e5bdcc03` server): a parameter change
fails CI until it is made deliberately (new epoch, `STRATEGY_VERSION` bumped, pins updated).

## 3. Decision metric

Primary: **net expectancy per trade after a fixed round-trip cost of 5 %, 7 % and 10 %**, with a 95 % bootstrap
confidence interval (`trading/sample_report.py`). Alongside: win rate, realised R:R, max drawdown, the modelled-cost
expectancy, a **stop-gap scenario** (every stop-loss exit at -20 % or worse) and the **haircut split** (exits that
had no Jupiter SELL quote, with and without them).
TP/(TP+SL) is reference only: it mostly measures volatility.

## 4. Sample size

| n (trades in the epoch, per sample_id) | Use |
|---|---|
| < 30 | INSUFFICIENT — no conclusion at all |
| 30 – 199 | preliminary check only (does the CI clearly exclude 0?) |
| >= 200 per arm | needed to compare exit variants (A/B: wide stop, runner, time stop) |

The sample report prints these warnings itself (`sample_status`, `warnings`) and also warns when a 95 % CI
includes 0. Gate and replay reports mark groups below 30 as INSUFFICIENT.

## 5. Replay / walk-forward protocol (tools/replay_tp_sl.py)

* No look-ahead: selection uses only data with ts <= the decision time. The `bought` flag comes from the same
  candidate episode (<= 300 s after the decision), never from a later episode; the baseline window of each part
  ends at that part's last decision; forward outcomes are the only thing taken after the decision.
* Walk-forward: candidates in time order, first 60 % = in-sample, last 40 % = holdout.
  All test parameters (horizon, levels, filters, split, seed) are fixed on the in-sample part and hashed
  (`frozen_params.hash`). The holdout is evaluated once; run with `--holdout-log` so that a second evaluation of the
  same holdout with different parameters is flagged ("HOLDOUT ALREADY USED") — it is then no longer out-of-sample.
* Exit A/B variants follow the same rule: choose the variant on in-sample trades, confirm once on the holdout.

## 6. Reports

* Dashboard: SAMPLE REPORT panel (needs the access code).
* From the server's data files: `python tools/sample_report.py --book data/paper_bot.json --epoch data/sample_epoch.json`
* Gate: `python tools/gate_report.py --db data/research.db`
* Replay: `python tools/replay_tp_sl.py --db data/research.db --horizon 30m --holdout-log data/holdout_log.json`
