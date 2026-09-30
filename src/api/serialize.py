"""TokenState -> JSON for the local desktop API and the web/PWA server.

summary() / detail(): raw data (unchanged, used by the desktop API).
card() / view():      display-ready data for the iPhone PWA — every label/sentence comes from i18n
                      (same helpers as the desktop UI), so both frontends say exactly the same thing.
Nothing here ever contains an API key: TokenState holds no secrets.
"""
from __future__ import annotations

import time
from dataclasses import asdict

from alerts.report import (SECTIONS, age_str, dev_label, event_text, filter_text, issue_text, liquidity_label,
                           metric_row, pct, risk_text, subscore_rows, usd, why_items)
from core.models import TokenState
from i18n import t


def summary(st: TokenState) -> dict:
    m = st.market
    return {
        "mint": st.mint, "symbol": st.info.symbol, "name": st.info.name,
        "age_min": round(st.age_minutes, 1) if st.age_minutes is not None else None,
        "data_quality": {"status": st.dq_status, "score": st.quality.score if st.quality else None},
        "opportunity": st.score.total if st.score else None,
        "risk": st.risk.score if st.risk else None,
        "early_signal": st.early.strength if st.early else None,
        "is_early": st.early.is_early if st.early else None,
        "lifecycle": st.lifecycle,
        "mc": m.market_cap if m else None, "liquidity": m.liquidity_usd if m else None,
        "liquidity_source": m.liquidity_source if m else None,
        "vol_5m": m.vol_5m if m else None, "buy_sell_5m": m.buy_sell_ratio_5m if m else None,
        "holders": st.holders.holder_count if st.holders else None,
        "filters_failed": st.filter_fails,
    }


def detail(st: TokenState, now: float | None = None) -> dict:
    now = now or time.time()
    d = summary(st)
    d["metrics"] = [{"key": m.key, "section": m.section, "value": m.value, "kind": m.kind, "source": m.source,
                     "timestamp": m.ts, "age_s": round(now - m.ts, 1) if m.ts and m.value is not None else None,
                     "confidence": m.confidence, "derived": m.derived, "note": m.note} for m in st.metrics]
    d["subscores"] = {k: {"score": v.score, "coverage_pct": v.coverage_pct, "note": v.note,
                          "factors": [asdict(f) for f in v.factors]} for k, v in st.subscores.items()}
    d["opportunity_contributions"] = [{"factor": k, "points": p, "value": v, "source": s}
                                      for k, p, v, s in (st.score.contributions if st.score else [])]
    d["risk"] = asdict(st.risk) if st.risk else None
    d["early"] = asdict(st.early) if st.early else None
    d["data_quality_issues"] = [asdict(i) for i in st.quality.issues] if st.quality else []
    d["events"] = [asdict(e) for e in st.recent_events]
    d["stamps"] = {k: {"source": v.source, "updated_at": v.updated_at, "age_s": round(now - v.updated_at, 1)}
                   for k, v in st.stamps.items()}
    return d


# ---------------------------------------------------------------- display-ready (PWA)
def card(st: TokenState) -> dict:
    """Compact coin card. Numbers are kept raw for client-side sort/filter; labels are localized."""
    m, e, h, wi = st.market, st.early, st.holders, st.whale_intel
    return {
        "mint": st.mint, "symbol": st.info.symbol or st.mint[:6], "name": st.info.name,
        "age_min": round(st.age_minutes, 1) if st.age_minutes is not None else None, "age": age_str(st.age_minutes),
        "dq": st.dq_status, "dq_score": st.quality.score if st.quality else None,
        "dq_label": t(f"state.{st.dq_status}"),
        "opp": st.score.total if st.score else None,
        "risk": st.risk.score if st.risk else None,
        "risk_level": t(f"state.{st.risk.level}") if st.risk else None,
        "early": e.strength if e else None, "is_early": bool(e and e.is_early),
        "early_groups": e.groups_computable if e else 0, "early_suppressed": bool(e and e.suppressed),
        "lifecycle": st.lifecycle, "lifecycle_label": t(f"lifecycle.{st.lifecycle}"),
        "mc": m.market_cap if m else None, "mc_label": usd(m.market_cap) if m else t("common.unknown"),
        "liq": m.liquidity_usd if m else None,
        "liq_label": (usd(m.liquidity_usd) + (" ⓒ" if m.liquidity_source == "pumpfun_curve" else ""))
        if m and m.liquidity_usd is not None else t("common.unknown"),
        "liq_full": liquidity_label(st),
        "vol5m": m.vol_5m if m else None, "vol5m_label": usd(m.vol_5m) if m else t("common.unknown"),
        "bs": m.buy_sell_ratio_5m if m else None,
        "pc5": m.price_change_5m if m else None, "pc1h": m.price_change_1h if m else None,
        "holders": h.holder_count if h else None, "top10": h.top10_pct if h else None,
        "whale_state": t(f"state.{wi.state}") if wi and wi.state != "UNKNOWN" else None,
        "dev_label": dev_label(st).split(": ", 1)[-1],
        "dev_status": st.dev.status if st.dev and st.dev.balance_verified else None,
        "dev_pct": st.dev.current_pct if st.dev and st.dev.balance_verified else None,
        "creator": st.info.creator or None,
        "prev_tokens": st.dev.prev_tokens_count if st.dev and st.dev.history_verified else None,
        "graduated": st.dev.prev_graduated if st.dev and st.dev.history_verified else None,
        "links_social": {k: getattr(st.info, k) for k in ("twitter", "telegram", "website") if getattr(st.info, k)},
        "narratives": [t(f"narrative.{n}") for n in st.narratives],
        "filters_passed": not st.filter_fails,
        "watch": st.watch,
    }


def view(st: TokenState, events=None, now: float | None = None) -> dict:
    """Full token page, localized, grouped the same way as the desktop detail tabs."""
    now = now or time.time()
    q, rk, es = st.quality, st.risk, st.early
    sections = []
    for sec in SECTIONS:
        rows = [dict(zip(("label", "value", "source", "updated", "age", "confidence"), metric_row(m, now)))
                for m in st.metrics if m.section == sec]
        if rows:
            sections.append({"key": sec, "title": t(f"tab.d_{sec}"), "rows": rows})
    holders_top = [{"owner": x.owner, "amount": x.amount, "pct": round(x.pct, 3), "tags": x.tags}
                   for x in (st.holders.top[:50] if st.holders else [])]
    early = None
    if es:
        early = {
            "strength": es.strength, "is_early": es.is_early, "transition": es.transition,
            "groups": es.groups_computable, "history_min": es.history_min,
            "note": t(f"note.{es.note}") if es.note else "", "suppressed": es.suppressed,
            "signals": [{"label": t(f"signal.{x.key}"),
                         "state": "fired" if x.fired else ("off" if x.fired is False else "unknown"),
                         "value": x.value if x.fired is not None else (x.raw.get("missing") or t(f"note.{x.note or 'no_data'}")),
                         "blocked": x.raw.get("blocked", ""), "source": x.source} for x in es.signals],
        }
    return {
        "card": card(st),
        "links": st.links,
        "scores": [{"label": lab, "value": val, "note": note} for lab, val, note in subscore_rows(st)],
        "why": [{"points": p, "label": lab, "value": val, "source": src} for p, lab, val, src in why_items(st)],
        "early": early,
        "risk": {"score": rk.score, "level": t(f"state.{rk.level}"),
                 "categories": [{"label": t(f"riskcat.{c}"), "value": v} for c, v in rk.categories.items()],
                 "flags": [{"label": risk_text(f)[0], "detail": risk_text(f)[1], "points": f.points,
                            "category": t(f"riskcat.{f.category}"), "source": f.source} for f in rk.factors],
                 "not_measurable": [t(f"missing.{k}") for k in rk.missing]} if rk else None,
        "data_quality": {"status": q.status, "label": t(f"state.{q.status}"), "score": q.score,
                         "issues": [{"severity": i.severity, "text": issue_text(i)} for i in q.issues]} if q else None,
        "filters": [filter_text(k) for k in st.filter_fails],
        "sections": sections,
        "holders_top": holders_top,
        "events": [{"ts": e.ts, "type": t(f"event.{e.type}"), "detail": event_text(e)[1], "severity": e.severity,
                    "source": e.source} for e in sorted(events if events is not None else st.recent_events,
                                                      key=lambda x: -x.ts)][:50],
        "disclaimer": t("app.disclaimer"),
    }
