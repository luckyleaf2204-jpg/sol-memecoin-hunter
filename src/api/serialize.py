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
from intel.mc_track import compact_path, mc_scenario, validated_mc
from validation.identity import SOURCE_LABEL


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
def usd_short(v) -> str:
    """$8.4K / $25K / $1.3M — for MC journeys and scenario levels."""
    if v is None:
        return t("common.unknown")
    v = float(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            x = v / div
            txt = f"{x:.1f}" if x < 10 else f"{x:.0f}"
            return f"${txt.rstrip('0').rstrip('.') if '.' in txt else txt}{suf}"
    return f"${v:,.0f}"


def _group_reason(st: TokenState, r: str) -> str:
    if r.startswith("rug:"):
        key = r.split(":", 1)[1]
        f = next((f for f in (st.risk.factors if st.risk else []) if f.key == key), None)
        return risk_text(f)[0] if f else key
    if r == "early_unknown" and st.early:
        return t("web.gr.early_unknown", groups=st.early.groups_computable, minutes=round(st.early.history_min, 1))
    if r == "top10" and st.holders and st.holders.top10_pct is not None:
        return t("web.gr.top10", pct=f"{st.holders.top10_pct:.1f}")
    if r == "risk_high" and st.risk:
        return t("web.gr.risk_high", risk=st.risk.score)
    return t(f"web.gr.{r}")


def _pair_status(st: TokenState) -> tuple[str, str]:
    m, i = st.market, st.info
    if any(e.type == "DATA_BREAK" and time.time() - e.ts < 1800 for e in st.recent_events[-10:]):
        return "migrated", t("web.pair.migrated")
    if m and m.is_curve or (i.complete is False):
        prog = f" {i.curve_progress:.0f}%" if i.curve_progress is not None else ""
        return "curve", t("web.pair.curve") + prog
    if i.complete or (m and m.dex_id and not m.is_curve):
        return "graduated", t("web.pair.graduated", dex=(m.dex_id if m and m.dex_id else "AMM"))
    return "unknown", t("common.unknown")


def profile(st: TokenState) -> dict:
    """Coin profile: MC journey, reference scenario, dev / on-chain / social summaries. Real data only."""
    m, d, hi, tr = st.market, st.dev, st.holder_intel, st.mc_track
    mc = validated_mc(st)
    gain = tr.gain_x(mc) if tr else None
    sc = mc_scenario(st)
    dev_ok, hist_ok = bool(d and d.balance_verified), bool(d and d.history_verified)
    trend = st.trend or {}
    pair_key, pair_label = _pair_status(st)
    return {
        "first_seen": tr.first_seen if tr else st.info.discovered_at,
        "age_at_discovery_min": round((st.info.discovered_at - st.info.created_at) / 60, 1)
        if st.info.created_at and st.info.discovered_at >= st.info.created_at else None,
        "initial_mc": tr.initial_mc if tr else None,
        "initial_ts": tr.initial_ts if tr else None,
        "initial_label": usd_short(tr.initial_mc) if tr and tr.initial_mc else t("web.nodata"),
        "initial_source": tr.initial_source if tr else "",
        "gain_x": round(gain, 2) if gain is not None else None,
        "gain_pct": round(100 * (gain - 1), 1) if gain is not None else None,
        "ath_mc": tr.ath_mc if tr else None,
        "mc_path": [("⇄ " if tag == "migrate" else "") + usd_short(x) for x, tag in compact_path(tr, mc)],
        "migrations": [{"ts": x["ts"], "before": usd_short(x["mc_before"]) if x.get("mc_before") else None,
                        "after": usd_short(x["mc_after"])} for x in (tr.migrations if tr else [])],
        "scenario": None if sc is None else {
            "kind": sc["kind"],
            "basis": {"label": usd_short(sc["basis"]["mc"]), "source": sc["basis"]["source"], "ts": sc["basis"]["ts"]},
            "levels": [{"label": usd_short(x["mc"]), "multiple": x["multiple"], "reached": x["reached_before"]}
                       for x in sc["levels"]],
            "refs": [{"label": t(f"web.ref.{r['key']}"), "value": usd_short(r["mc"]), "multiple": r["multiple"],
                      "source": r["source"]} for r in sc["refs"]],
        },
        "dev": {
            "known": dev_ok or hist_ok,
            "wallet": st.info.creator or None,
            "holding_pct": d.current_pct if dev_ok else None,
            "sold_pct": d.sold_pct if dev_ok else None,
            "status": t(f"devstatus.{d.status}") if dev_ok else None,
            "initial_buy": st.info.dev_initial_buy,
            "prev_tokens": d.prev_tokens_count if hist_ok else None,
            "graduated": d.prev_graduated if hist_ok else None,
            "dead": d.prev_dead if hist_ok else None,
            "best_ath": usd_short(d.prev_best_ath) if hist_ok and d.prev_best_ath else None,
            "funding": d.funding_wallet if d and d.funding_wallet else None,
            "funding_sol": d.funding_sol if d and d.funding_wallet else None,
            "related": None,                        # wallet clustering NOT AVAILABLE -> never guessed
            "updated": d.fetched_at if d else None,
        },
        "onchain": {
            "holders": st.holders.holder_count if st.holders else None,
            "holders_chg_15m": hi.abs_growth_15m if hi else None,
            "holders_chg_5m": hi.abs_growth_5m if hi else None,
            "whale": t(f"state.{st.whale_intel.state}") if st.whale_intel and st.whale_intel.state != "UNKNOWN" else None,
            "buy_share": trend.get("buy_share"),
            "buy_pp_5m": trend.get("buy_pp_5m"),
            "mc_chg_5m": trend.get("mc_chg_5m_pct"),
            "vol_chg_5m": trend.get("vol_chg_5m_pct"),
            "txns_5m": m.txns_5m if m else None,
            "pair": pair_key, "pair_label": pair_label,
            "holder_status": st.holder_status or None,
        },
        "social": {
            "links": {k: getattr(st.info, k) for k in ("twitter", "telegram", "website") if getattr(st.info, k)},
            "category": [t(f"narrative.{n}") for n in st.narratives],
            "activity": None,                       # X / Telegram activity NOT AVAILABLE (no scraping)
        },
        "updated": {k: v.updated_at for k, v in st.stamps.items() if k in ("market", "holders", "dev", "curve")},
    }


def pre_early_card(st: TokenState, full: bool = False) -> dict | None:
    pe = st.pre_early
    if pe is None or pe.status == "NOT_ELIGIBLE":
        return None
    out = {"status": pe.status, "label": t(f"web.pre.status.{pe.status}"), "fired": pe.fired,
           "computable": pe.computable, "total": pe.total, "age_min": pe.age_min,
           "data": t("web.pre.data_ok" if pe.computable >= 3 else "web.pre.data_missing",
                     n=pe.computable, total=pe.total),
           "blocked_by": [t(f"web.pre.block.{b}") for b in pe.blocked_by],
           "reasons": [t(f"web.pre.sig.{s.key}") + ": " + s.value for s in pe.signals if s.fired]}
    if full:
        out["signals"] = [{"label": t(f"web.pre.sig.{s.key}"),
                           "state": "fired" if s.fired else ("off" if s.fired is False else "unknown"),
                           "value": s.value or t("common.unknown"),
                           "rule": t(f"web.pre.need.{s.key}") if s.fired is None else s.note} for s in pe.signals]
    return out


def early_watch_card(st: TokenState) -> dict | None:
    w = st.early_watch
    if w is None or not w.eligible:
        return None
    return {"rank": w.rank, "confidence": w.confidence, "components": w.components, "age_min": w.age_min,
            "excluded_by": [t(f"web.ew.ex.{x}") for x in w.excluded_by],
            "missing": [t(f"web.ew.miss.{x}") for x in w.missing],
            "data": t("web.ew.data", n=6 - len(w.missing), total=6)}


def card(st: TokenState) -> dict:
    """Compact coin card. Numbers are kept raw for client-side sort/filter; labels are localized."""
    m, e, h, wi = st.market, st.early, st.holders, st.whale_intel
    trend, hi, d = st.trend or {}, st.holder_intel, st.dev
    hist_ok = bool(d and d.history_verified)
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
        "identity": st.identity.status,
        "identity_label": t(f"web.id.{st.identity.status}"),
        "identity_claims": [{"source": SOURCE_LABEL.get(k, k), "symbol": v[0], "name": v[1]}
                            for k, v in sorted(st.identity.claims.items())],
        "token_program": st.identity.token_program or None,
        "pre_early": pre_early_card(st),
        "early_watch": early_watch_card(st),
        "momentum": st.subscores["momentum"].score if "momentum" in st.subscores else None,
        "buy_share": (st.trend or {}).get("buy_share"),
        "fired": e.fired_count if e and e.strength is not None else None,
        "group": st.group or None,
        "group_reasons": [_group_reason(st, r) for r in st.group_reasons],
        "hot": [t(f"web.hot.{r}") for r in st.priority_reasons],
        "first_seen": st.mc_track.first_seen if st.mc_track else st.info.discovered_at,
        "updated_at": st.stamps["market"].updated_at if "market" in st.stamps else None,
        # sort keys (raw, None = unknown -> always sorted last)
        "mc_rise": trend.get("mc_chg_5m_pct") if trend.get("mc_chg_5m_pct") is not None else (m.price_change_5m if m else None),
        "vol_rise": m.vol_accel if m and m.vol_accel is not None else None,
        "buy_pp": trend.get("buy_pp_5m"),
        "holder_rise": hi.abs_growth_15m if hi else None,
        "dev_hist": (d.prev_graduated if hist_ok else None),
        "profile": profile(st),
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
        "server_time": now,
        "links": st.links,
        "scores": [{"label": lab, "value": val, "note": note} for lab, val, note in subscore_rows(st)],
        "why": [{"points": p, "label": lab, "value": val, "source": src} for p, lab, val, src in why_items(st)],
        "early": early,
        "pre_early": pre_early_card(st, full=True),
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
