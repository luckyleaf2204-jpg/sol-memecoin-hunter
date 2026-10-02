"""Review bundle (Part 3.4): one JSON / Markdown summary a reviewer can read on GitHub without server access —
sample report, the last 50 trades, gate statistics, snapshot / lease status. Paper data only; NO token, key, access
code or URL credential: `check_no_secrets` refuses to write a bundle that contains one."""
from __future__ import annotations

import json
import time

from core.secret_scan import check_no_secrets, check_obj  # noqa: F401  (re-exported)

TRADE_FIELDS = ("trade_id", "symbol", "engine", "sample_id", "epoch", "lifecycle", "setup_type", "location",
                "extension_5m_pct", "entry_ts", "exit_ts", "holding_s", "entry_price", "exit_reason", "mfe_pct",
                "mae_pct", "gross_move_pct", "net_pnl_pct", "net_pnl_usd", "real_cost_pct", "fixed_fee_pct", "haircut",
                "n_tx", "size_usd")
def build(journal: list[dict], report: dict, snapshot: dict | None = None, now: float | None = None,
          source: str = "server /api/review_bundle") -> dict:
    now = now or time.time()
    ep = report.get("epoch") or {}
    counted_epoch = ep.get("id")
    trades = [r for r in journal if not counted_epoch or r.get("epoch") == counted_epoch] or journal
    return {"generated_at": now, "paper_only": True, "source": source,
            "summary_line": report.get("summary_line"), "conclusion": report.get("conclusion"),
            "report": {k: v for k, v in report.items() if k not in ("summary_line",)},
            "last_trades": [{k: r.get(k) for k in TRADE_FIELDS} for r in trades[-50:]],
            "snapshot": _safe_snapshot(snapshot) if snapshot else {"status": "unknown (offline bundle)"}}


def _safe_snapshot(sn: dict) -> dict:
    """Durability status without WHERE the store is (the target URL is configuration, not review material)."""
    out = {k: v for k, v in sn.items() if k not in ("target",)}
    if "target" in sn:
        out["target"] = "configured" if sn["target"] not in (None, "-") else "-"
    return out


def _f(v, nd=2):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _ci(c):
    return "n/a" if not c else f"[{c[0]}, {c[1]}]"


def to_markdown(b: dict) -> str:
    r = b["report"]
    L = ["# Review bundle — SOL Memecoin Hunter (PAPER only)", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(b['generated_at']))}. No wallet, no signing, "
         "no real transaction. No credentials in this file.", "", f"Source: **{b.get('source', '-')}**", ""]
    c = b.get("conclusion") or {}
    L += [f"**Kết luận:** {c.get('text', '-')}", ""]
    for w in c.get("why") or []:
        L.append(f"- {w}")
    L += ["", f"`{b.get('summary_line') or '-'}`", ""]
    ep = r.get("epoch") or {}
    L += ["## Sample", "", f"- epoch: strategy `{ep.get('strategy_version')}`, fingerprint `{ep.get('fingerprint')}`, "
          f"commit `{ep.get('commit')}`, since {ep.get('started_at_utc')}",
          f"- status: **{r.get('sample_status')}**, n = {r.get('n')} (excluded legacy / no-quote: "
          f"{r.get('excluded_legacy_or_noquote')})",
          f"- gaps: {(r.get('gaps') or {}).get('count')} ({(r.get('gaps') or {}).get('minutes')} min, "
          f"{(r.get('gaps') or {}).get('excluded_trades')} trades excluded)", ""]
    for w in r.get("warnings") or []:
        L.append(f"- ⚠ {w}")
    L += ["", "## Net expectancy after cost", "",
          "| cost | n | expectancy % | 95 % CI | 95 % CI per token | win % | R:R | max DD $ | SL-gap -20 % exp. |",
          "|---|---|---|---|---|---|---|---|---|"]
    gap = ((r.get("sl_gap_scenario") or {}).get("by_cost")) or {}
    for k, m in (r.get("by_cost") or {}).items():
        L.append(f"| {k} | {m.get('n')} | {_f(m.get('expectancy_pct'), 3)} | {_ci(m.get('ci95_pct'))} | "
                 f"{_ci(m.get('ci95_cluster_pct'))} | {_f(m.get('win_rate_pct'), 1)} | {_f(m.get('rr_realised'), 2)} | "
                 f"{_f((m.get('max_drawdown') or {}).get('usd'))} | {_f((gap.get(k) or {}).get('expectancy_pct'), 3)} |")
    hc = r.get("haircut") or {}
    L += ["", "## Haircut exits (no Jupiter SELL quote)", "",
          f"- haircut trades: {hc.get('n_haircut_trades')} ({hc.get('share_pct')} %)"]
    for k, m in (hc.get("without_haircut_trades") or {}).items():
        L.append(f"- without them @{k}: n {m.get('n')}, expectancy {_f(m.get('expectancy_pct'), 3)} %, "
                 f"CI {_ci(m.get('ci95_pct'))}")
    co = r.get("costs") or {}
    L += ["", "## Fixed fees", "", f"- median fixed fee {co.get('fixed_fee_pct_median')} % of the trade, max "
          f"{co.get('fixed_fee_pct_max')} %, tx per trade (median) {co.get('n_tx_median')}, recommended min size "
          f"{co.get('recommended_min_size_usd')} $"]
    gb = r.get("gate_block_rates") or {}
    L += ["", "## Entry gate (unique tokens this epoch)", "",
          f"- at the gate: {gb.get('tokens_at_gate')}, passed once: {gb.get('passed_once')}, never passed: "
          f"{gb.get('never_passed')}", "", "| reason | tokens | rate |", "|---|---|---|"]
    for k, v in (gb.get("by_reason") or {}).items():
        L.append(f"| {k} | {v['tokens']} | {v['rate']} |")
    gf = r.get("gate_forward_returns") or {}
    L += ["", "## Forward returns: blocked vs entered vs matched baseline", ""]
    if gf.get("by_horizon"):
        L += ["| horizon | group | n | mean % | median % | win % |", "|---|---|---|---|---|---|"]
        for h, row in gf["by_horizon"].items():
            for g, s in row.items():
                L.append(f"| {h} | {g} | {s.get('n')} | {_f(s.get('mean_ret_pct'))} | {_f(s.get('median_ret_pct'))} "
                         f"| {_f(s.get('win_rate_pct'), 1)} |")
        L.append(f"\n{gf.get('note', '')}")
    else:
        L.append(f"- {gf.get('status', 'not available')}")
    sn = b.get("snapshot") or {}
    L += ["", "## Durability", "", "```", json.dumps(sn, indent=1, default=str), "```",
          "", "## Last 50 trades", "",
          "| id | symbol | location | setup | entry | exit reason | MFE % | MAE % | gross % | net % | cost % | haircut |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in b.get("last_trades") or []:
        L.append(f"| {(t.get('trade_id') or '')[:12]} | {t.get('symbol')} | {t.get('location')} | {t.get('setup_type')} | "
                 f"{t.get('entry_price')} | {t.get('exit_reason')} | {_f(t.get('mfe_pct'))} | {_f(t.get('mae_pct'))} | "
                 f"{_f(t.get('gross_move_pct'))} | {_f(t.get('net_pnl_pct'))} | {_f(t.get('real_cost_pct'))} | "
                 f"{'yes' if t.get('haircut') else ''} |")
    if not b.get("last_trades"):
        L.append("| - | no closed trade in this bundle | | | | | | | | | | |")
    text = "\n".join(L) + "\n"
    check_no_secrets(text)
    return text
