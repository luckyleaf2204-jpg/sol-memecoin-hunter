"""Display helpers + text/Telegram reports. All words come from i18n; numbers are formatted here.

Anything unverified shows UNKNOWN, anything not implemented NOT AVAILABLE — never 0 or a placeholder.
DATA (metrics with source/time/age/confidence) and INTERPRETATION (scores/flags) are kept apart.
"""
from __future__ import annotations

import html
import time

from core.models import INVALID, Event, Issue, Metric, RiskFactor, TokenState
from i18n import has, t

SECTIONS = ("overview", "market", "momentum", "holders", "dev", "smart_money", "whales", "cluster", "liquidity",
            "social", "narrative", "security")
SUBSCORE_ORDER = ("opportunity", "risk", "data_quality", "momentum", "onchain", "holder", "dev", "smart_money",
                  "whale", "liquidity", "social", "narrative", "security", "early_signal")


def usd(v) -> str:
    if v is None:
        return t("common.unknown")
    v = float(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v/div:.2f}{suf}"
    return f"${v:,.2f}" if abs(v) >= 1 else f"${v:.8f}".rstrip("0")


def pct(v, digits=1) -> str:
    return t("common.unknown") if v is None else f"{v:.{digits}f}%"


def num(v) -> str:
    return t("common.unknown") if v is None else f"{v:,.0f}"


def age_str(minutes) -> str:
    if minutes is None:
        return t("common.unknown")
    if minutes < 60:
        return f"{minutes:.0f}m"
    if minutes < 48 * 60:
        return f"{minutes/60:.1f}h"
    return f"{minutes/1440:.0f}d"


def secs(s: float | None) -> str:
    if s is None:
        return "—"
    return f"{s:.0f}s" if s < 120 else f"{s/60:.0f}m"


def fmt(value, kind: str) -> str:
    if value is None:
        return t("common.unknown")
    if kind == "usd":
        return usd(value)
    if kind == "pct":
        return f"{value:+.1f}%" if isinstance(value, float) and value < 0 else f"{value:.1f}%"
    if kind == "mult":
        return f"{value:.2f}×"
    if kind == "ratio":
        return f"{value:.2f}"
    if kind == "int":
        return f"{value:,.0f}"
    if kind == "sol":
        return f"{value:,.2f} SOL"
    if kind == "min":
        return age_str(value)
    if kind == "hours":
        return f"{value:.1f}h"
    if kind == "bool":
        return t("common.yes") if value else t("common.no")
    if kind == "text":
        s = str(value)
        if s.replace("_", "").replace(" ", "").isupper():
            for prefix in ("state", "lifecycle", "devstatus"):
                if has(f"{prefix}.{s}"):
                    return t(f"{prefix}.{s}")
        return s
    return str(value)


def metric_value(m: Metric) -> str:
    if m.value is None:
        if m.note == "not_implemented":
            return t("common.not_available")
        return t("common.unknown")
    return fmt(m.value, m.kind)


def metric_row(m: Metric, now: float | None = None) -> tuple[str, str, str, str, str, str]:
    """(label, value, source, updated HH:MM:SS, age, confidence) — the per-metric DATA QUALITY view."""
    now = now or time.time()
    note = f" — {t('note.' + m.note)}" if m.note and (m.value is None or m.note in ("both_sides", "since_tracking",
                                                                                   "calc_cp")) else ""
    return (t(f"metric.{m.key}"), metric_value(m) + note, m.source or "—",
            time.strftime("%H:%M:%S", time.localtime(m.ts)) if m.ts and m.value is not None else "—",
            secs(now - m.ts) if m.ts and m.value is not None else "—",
            f"{m.confidence:.2f}" if m.confidence is not None else "—")


def dev_label(st: TokenState) -> str:
    d = st.dev
    if not d or not d.balance_verified:
        return f"{t('lbl.dev')}: {t('common.unknown')}"
    return f"{t('lbl.dev')}: {pct(d.current_pct, 2)} {t('devstatus.' + d.status)} ({t('common.verified')})"


def liquidity_label(st: TokenState) -> str:
    m = st.market
    if not m or m.liquidity_usd is None:
        return t("common.unknown")
    if m.liquidity_source == "pumpfun_curve":
        return f"{usd(m.liquidity_usd)} ({t('lbl.curve_reserve')})"
    return usd(m.liquidity_usd)


def issue_text(i: Issue) -> str:
    return t(f"issue.{i.key}", **i.params)


def risk_text(f: RiskFactor) -> tuple[str, str]:
    return t(f"risk.{f.key}"), t(f"risk.{f.key}.detail", **f.params)


def event_text(e: Event) -> tuple[str, str]:
    p = dict(e.params)
    for k in ("before", "now"):
        if k in p and e.type in ("VOLUME_SPIKE", "LIQUIDITY_ADD", "LIQUIDITY_REMOVE"):
            p[k] = usd(p[k])
    if "reason" in p:
        p["reason"] = t(f"rugreason.{p['reason']}")
    return t(f"event.{e.type}"), t(f"event.{e.type}.detail", **p)


def filter_text(key: str) -> str:
    return t(f"filter.{key}")


def why_items(st: TokenState, limit: int = 12) -> list[tuple[str, str, str, str]]:
    """Opportunity contributions: ('+18.2', label, value, source), largest first."""
    if not st.score:
        return []
    return [(f"+{pts:.1f}", t(f"factor.{key}"), value, src)
            for key, pts, value, src in st.score.contributions[:limit] if pts > 0]


def subscore_rows(st: TokenState) -> list[tuple[str, str, str]]:
    """(label, value, note) for the 14 score dimensions."""
    rows = []
    for k in SUBSCORE_ORDER:
        if k == "opportunity":
            v = st.score.total if st.score else None
            note = "" if st.score else t("note.invalid_not_scored" if st.dq_status == INVALID else "note.no_data")
        elif k == "risk":
            v, note = (st.risk.score, t(f"state.{st.risk.level}")) if st.risk else (None, "")
        elif k == "data_quality":
            v, note = (st.quality.score, t(f"state.{st.quality.status}")) if st.quality else (None, "")
        else:
            sub = st.subscores.get(k)
            v = sub.score if sub else None
            note = t(f"note.{sub.note}") if sub and sub.score is None and sub.note else \
                (f"{t('lbl.coverage')} {sub.coverage_pct}%" if sub and sub.factors else "")
        if v is None and k in ("smart_money", "social", "narrative", "security"):
            shown = t("common.not_available")
        else:
            shown = str(v) if v is not None else t("common.unknown")
        rows.append((t(f"score.{k}"), shown, note))
    return rows


def text_report(st: TokenState) -> str:
    now = time.time()
    L = [f"{st.info.name} (${st.info.symbol})  {st.mint}",
         f"{t('score.opportunity')}: {st.score.total if st.score else t('common.unknown')} | "
         f"{t('score.risk')}: {st.risk.score if st.risk else '—'} | "
         f"{t('score.data_quality')}: {st.quality.status if st.quality else '—'} {st.quality.score if st.quality else ''} | "
         f"{t('col.lifecycle')}: {t('lifecycle.' + st.lifecycle)}",
         ""]
    if st.quality:
        L += [f"  ! {issue_text(i)}" for i in st.quality.issues if i.severity == "critical"]
    L.append(f"=== {t('lbl.data')} ===")
    for sec in SECTIONS:
        ms = [m for m in st.metrics if m.section == sec]
        if not ms:
            continue
        L.append(f"[{t(f'tab.d_{sec}')}]")
        for m in ms:
            lab, val, src, upd, age, conf = metric_row(m, now)
            L.append(f"  {lab:34s} {val:28s} {src:30s} {upd:>8s} {age:>5s} conf {conf}")
    L += ["", f"=== {t('lbl.interpretation')} ==="]
    L += [f"  {lab:24s} {val:>10s}  {note}" for lab, val, note in subscore_rows(st)]
    L.append(f"{t('lbl.why')}:")
    L += [f"  {p:>6s} {lab}: {val}   [{src}]" for p, lab, val, src in why_items(st)] or [f"  {t('common.none')}"]
    L.append(f"{t('lbl.risk_flags')}:")
    L += [f"  ⚠ +{f.points} {risk_text(f)[0]}: {risk_text(f)[1]}   [{f.source}]" for f in st.risk.factors] \
        if st.risk and st.risk.factors else [f"  {t('common.none')}"]
    if st.early:
        e = st.early
        L.append(f"{t('score.early_signal')}: {e.strength if e.strength is not None else t('common.unknown')} "
                 f"| EARLY = {e.is_early} | {t('lbl.transition')} = {e.transition} | "
                 f"{t('lbl.history')} {e.history_min:.0f}m")
        for x in e.signals:
            state = "✓" if x.fired else ("·" if x.fired is False else "—")
            L.append(f"  {state} {t('signal.' + x.key)}: {x.value or t('note.' + (x.note or 'no_data'))}")
    L.append(t("app.disclaimer"))
    return "\n".join(L)


def telegram_alert(st: TokenState, lang: str | None = None) -> str:
    """Compact HTML for the Telegram Bot API (sent only for VALID, ranked tokens)."""
    from i18n import set_language, get_language
    prev = get_language()
    if lang:
        set_language(lang)
    try:
        i, m, h, sc, rk, q = st.info, st.market, st.holders, st.score, st.risk, st.quality
        e = html.escape
        icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "EXTREME": "🔴"}.get(rk.level if rk else "", "⚪")
        lines = [f"🔥 <b>{e(t('alert.title'))}</b>", f"<b>${e(i.symbol)}</b> — {e(i.name)}", "",
                 f"<b>{e(t('lbl.data'))}</b>"]
        if m:
            bs = m.buy_sell_ratio_5m
            lines += [f"MC: {usd(m.market_cap)}", f"{e(t('metric.liquidity'))}: {e(liquidity_label(st))}",
                      f"{e(t('metric.vol_5m'))}: {usd(m.vol_5m)}",
                      f"{e(t('metric.buy_sell_5m'))}: {f'{bs:.2f}' if bs is not None else e(t('common.unknown'))}"]
        lines.append(f"{e(t('col.age'))}: {age_str(st.age_minutes)} · {e(t('lifecycle.' + st.lifecycle))}")
        lines += [f"{e(t('metric.holders'))}: {num(h.holder_count)} · Top10: {pct(h.top10_pct)}" if h
                  else f"{e(t('metric.holders'))}: {e(t('common.unknown'))}",
                  e(dev_label(st)),
                  f"{e(t('score.smart_money'))}: {e(t('common.not_available'))}",
                  f"{e(t('score.social'))}: {e(t('common.not_available'))}", "",
                  f"<b>{e(t('lbl.interpretation'))}</b>"]
        if sc:
            lines.append(f"{e(t('score.opportunity'))}: <b>{sc.total}/100</b> ({e(t('lbl.coverage'))} {sc.coverage_pct}%)")
            lines += [f"{p} {e(lab)}: {e(val)}" for p, lab, val, _ in why_items(st, 5)]
        if st.early and st.early.strength is not None:
            lines.append(f"{e(t('score.early_signal'))}: {st.early.strength}" + (" ⚡" if st.early.is_early else ""))
        if rk:
            lines.append(f"{e(t('score.risk'))}: {icon} <b>{rk.score} {e(t('state.' + rk.level))}</b>")
            lines += [f"⚠ {e(risk_text(f)[0])}: {e(risk_text(f)[1])}" for f in rk.factors[:4]]
        if q:
            lines.append(f"{e(t('score.data_quality'))}: {e(t('state.' + q.status))} {q.score}/100")
        lines += ["", f"<code>{i.mint}</code>",
                  f'<a href="{st.links["pumpfun"]}">Pump.fun</a> | <a href="{st.links["dexscreener"]}">DexScreener</a>'
                  f' | <a href="{st.links["solscan"]}">Solscan</a>', f"<i>{e(t('app.disclaimer'))}</i>"]
        return "\n".join(lines)
    finally:
        set_language(prev)
