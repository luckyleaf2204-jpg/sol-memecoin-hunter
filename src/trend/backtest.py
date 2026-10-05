"""Rule T1 backtest exactly as pre-registered in docs/trend_plan.md (no parameter is fitted here).

bars: [(ts, open, high, low, close, volume_usd)] ascending, hourly. A signal is read on a CLOSED bar i and filled at
the open of bar i + 1. Returns per trade are net of every cost of the plan."""
from __future__ import annotations

import random
from dataclasses import dataclass

ENTRY_LOOKBACK = 168          # 7 days of hourly bars
EXIT_LOOKBACK = 72            # 3 days
VOL_WINDOW = 24
POOL_FEE = 0.003              # per side
SLIPPAGE = 0.005              # per side
NETWORK_FEE_SOL = 0.00011
SOL_USD = 150.0
SPLIT = 0.6
BOOT = 2000


@dataclass
class Costs:
    size_usd: float = 100.0
    priority_fee_sol: float = 0.005
    reserve_usd: float = 1_000_000.0

    def round_trip_pct(self) -> float:
        impact = self.size_usd / (self.reserve_usd / 2)
        fixed = 2 * (NETWORK_FEE_SOL + self.priority_fee_sol) * SOL_USD / self.size_usd
        return 100 * (2 * (POOL_FEE + SLIPPAGE + impact) + fixed)

    def net(self, entry: float, exit_: float) -> float:
        """Net % of one trade: proportional costs on both legs, fixed fees on the size."""
        impact = self.size_usd / (self.reserve_usd / 2)
        side = POOL_FEE + SLIPPAGE + impact
        gross_out = (exit_ / entry) * (1 - side) * (1 - side)
        fixed = 2 * (NETWORK_FEE_SOL + self.priority_fee_sol) * SOL_USD / self.size_usd
        return 100 * (gross_out - 1 - fixed)


def signals_t1(bars: list[tuple]) -> list[tuple[int, int, str]]:
    """Trades as (entry_bar_index, exit_bar_index, exit_kind); fills at the OPEN of those bars (exit_kind 'end' =
    closed at the last close: exit index = len(bars) - 1 and price = its close)."""
    trades, i, n = [], ENTRY_LOOKBACK, len(bars)
    in_pos, entry_i = False, None
    while i < n - 1:
        ts, o, h, lo, c, v = bars[i]
        if not in_pos:
            prev_high = max(b[2] for b in bars[i - ENTRY_LOOKBACK:i])
            vol24 = sum(b[5] for b in bars[i - VOL_WINDOW + 1:i + 1])
            prev = bars[i - ENTRY_LOOKBACK:i]
            avg24 = sum(b[5] for b in prev) / ENTRY_LOOKBACK * VOL_WINDOW
            if c > prev_high and vol24 > avg24:
                in_pos, entry_i = True, i + 1
        elif i >= EXIT_LOOKBACK:
            prev_low = min(b[3] for b in bars[i - EXIT_LOOKBACK:i])
            if c < prev_low and i + 1 > entry_i:
                trades.append((entry_i, i + 1, "channel"))
                in_pos = False
        i += 1
    if in_pos:
        trades.append((entry_i, n - 1, "end"))
    return trades


def trade_rows(symbol: str, bars: list[tuple], costs: Costs) -> list[dict]:
    rows = []
    for ei, xi, kind in signals_t1(bars):
        entry = bars[ei][1]
        exit_ = bars[xi][4] if kind == "end" else bars[xi][1]
        rows.append({"symbol": symbol, "entry_ts": bars[ei][0], "exit_ts": bars[xi][0], "entry": entry, "exit": exit_,
                     "hold_bars": xi - ei, "kind": kind, "gross_pct": 100 * (exit_ / entry - 1),
                     "net_pct": costs.net(entry, exit_)})
    return rows


def cluster_ci(rows: list[dict], key: str = "net_pct", iters: int = BOOT, seed: int = 7):
    groups = {}
    for r in rows:
        groups.setdefault(r["symbol"], []).append(r[key])
    g = list(groups.values())
    if len(g) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        pick = [rng.choice(g) for _ in g]
        n = sum(len(x) for x in pick)
        means.append(sum(sum(x) for x in pick) / n)
    means.sort()
    return round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters) - 1], 3)


def stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    nets = [r["net_pct"] for r in rows]
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    return {"n": n, "tokens": len({r["symbol"] for r in rows}), "mean_net_pct": round(sum(nets) / n, 3),
            "median_net_pct": round(sorted(nets)[n // 2], 3), "ci95_cluster": cluster_ci(rows),
            "mean_gross_pct": round(sum(r["gross_pct"] for r in rows) / n, 3),
            "win_rate_pct": round(100 * len(wins) / n, 1),
            "avg_win_pct": round(sum(wins) / len(wins), 3) if wins else None,
            "avg_loss_pct": round(sum(losses) / len(losses), 3) if losses else None,
            "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
            "mean_hold_h": round(sum(r["hold_bars"] for r in rows) / n, 1)}


def random_baseline(rows: list[dict], data: dict, costs_of: dict, period: tuple[int, int], observed: float,
                    iters: int = BOOT, seed: int = 11) -> dict:
    """Same number of trades per token and the same holding durations, entries at random bars of the period."""
    rng = random.Random(seed)
    by_tok = {}
    for r in rows:
        by_tok.setdefault(r["symbol"], []).append(r["hold_bars"])
    idx = {s: [i for i, b in enumerate(data[s]) if period[0] <= b[0] < period[1]] for s in by_tok}
    ok = {(s, h): ([i for i in idx[s] if i + h < len(data[s])] or idx[s][:1]) for s, hs in by_tok.items() for h in hs}
    means = []
    for _ in range(iters):
        nets = []
        for sym, holds in by_tok.items():
            bars = data[sym]
            for h in holds:
                i = rng.choice(ok[(sym, h)])
                j = min(i + h, len(bars) - 1)
                nets.append(costs_of[sym].net(bars[i][1], bars[j][1]))
        means.append(sum(nets) / len(nets))
    means.sort()
    return {"iters": iters, "mean_of_random_means": round(sum(means) / iters, 3),
            "p_random_ge_observed": round(sum(1 for m in means if m >= observed) / iters, 4)}


def run(data: dict, reserves: dict, size_usd: float = 100.0, priority_fee_sol: float = 0.005,
        baseline_iters: int = BOOT) -> dict:
    """data: symbol -> bars; reserves: symbol -> pool reserve USD. Full T1 test with split, baseline and verdict."""
    costs = {s: Costs(size_usd, priority_fee_sol, reserves[s]) for s in data}
    t0 = min(b[0][0] for b in data.values() if b)
    t1 = max(b[-1][0] for b in data.values() if b)
    cut = t0 + SPLIT * (t1 - t0)
    rows = [r for s, bars in data.items() for r in trade_rows(s, bars, costs[s])]
    ins = [r for r in rows if r["entry_ts"] < cut]
    hold = [r for r in rows if r["entry_ts"] >= cut]
    s_in, s_hold = stats(ins), stats(hold)
    base = random_baseline(hold, data, costs, (cut, t1 + 1), s_hold.get("mean_net_pct", 0.0), baseline_iters) \
        if hold else None
    bh = {}
    for s, bars in data.items():
        hb = [b for b in bars if b[0] >= cut]
        if len(hb) > 1:
            bh[s] = round(costs[s].net(hb[0][1], hb[-1][4]), 2)
    checks = {"n>=100": s_hold.get("n", 0) >= 100,
              "ci_lower>0": bool(s_hold.get("ci95_cluster")) and s_hold["ci95_cluster"][0] > 0,
              "random_p<0.05": base is not None and base["p_random_ge_observed"] < 0.05,
              "in_sample_mean>0": s_in.get("mean_net_pct", -1) > 0}
    return {"window": {"start": t0, "cut": cut, "end": t1}, "size_usd": size_usd, "priority_fee_sol": priority_fee_sol,
            "round_trip_cost_pct": {s: round(c.round_trip_pct(), 3) for s, c in costs.items()},
            "in_sample": s_in, "holdout": s_hold, "random_baseline_holdout": base, "buy_hold_holdout_net_pct": bh,
            "checks": checks, "verdict": "PASS" if all(checks.values()) else "REJECT", "trades": rows}
