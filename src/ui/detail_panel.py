"""Token detail — 15 tabs. Every metric row shows VALUE · SOURCE · UPDATED · AGE · CONFIDENCE."""
from __future__ import annotations

import html
import time

import pyqtgraph as pg
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from alerts.report import (event_text, filter_text, issue_text, metric_row, pct, risk_text, subscore_rows, usd,
                           why_items)
from core.models import INVALID, PARTIAL, VALID, TokenState
from database.db import Database
from i18n import t
from ui import theme

DQ_COLOR = {VALID: theme.GREEN, PARTIAL: theme.YELLOW, INVALID: theme.RED}
SEV_COLOR = {"positive": theme.GREEN, "info": theme.TEXT, "warning": theme.ORANGE, "critical": theme.RED}
TABS = ("overview", "market", "momentum", "holders", "dev", "smart_money", "whales", "cluster", "liquidity",
        "social", "narrative", "security", "risk", "events", "history")
e = html.escape


def _badge(text: str, color: str) -> str:
    return f"<span style='background:{color};color:#0f1117;font-weight:800'>&nbsp;{e(text)}&nbsp;</span>"


def metrics_table(st: TokenState, section: str) -> str:
    now = time.time()
    rows = [metric_row(m, now) for m in st.metrics if m.section == section]
    if not rows:
        return f"<p style='color:{theme.MUTED}'>{e(t('common.not_available'))}</p>"
    head = "".join(f"<td>{e(t('col.' + c))}</td>" for c in ("metric", "value", "source", "updated", "data_age", "confidence"))
    body = []
    for lab, val, src, upd, age, conf in rows:
        muted = val.startswith(t("common.unknown")) or val.startswith(t("common.not_available"))
        style = f" style='color:{theme.MUTED}'" if muted else ""
        body.append(f"<tr{style}><td><b>{e(lab)}</b></td><td>{e(val)}</td><td>{e(src)}</td><td>{e(upd)}</td>"
                    f"<td>{e(age)}</td><td>{e(conf)}</td></tr>")
    return (f"<table cellspacing=0 cellpadding=2 width='100%'><tr style='color:{theme.MUTED}'>{head}</tr>"
            + "".join(body) + "</table>")


def overview_html(st: TokenState) -> str:
    q, rk = st.quality, st.risk
    out = []
    if rk and rk.score > 80:
        out.append(f"<div style='background:{theme.RED};color:white;padding:6px;font-weight:700'>"
                   f"⚠ {e(t('lbl.extreme_risk', score=rk.score))}</div>")
    if q:
        out.append(f"<p>{_badge(t('state.' + q.status), DQ_COLOR[q.status])} {e(t('score.data_quality'))} <b>{q.score}</b>/100 · "
                   f"{e(t('col.lifecycle'))}: <b>{e(t('lifecycle.' + st.lifecycle))}</b></p>")
        crit = [i for i in q.issues if i.severity == "critical"]
        if crit:
            out.append("<ul>" + "".join(f"<li style='color:{theme.RED}'>{e(issue_text(i))}</li>" for i in crit) + "</ul>")
    # all score dimensions
    out.append(f"<h3 style='margin:4px 0'>{e(t('lbl.scores'))}</h3><table cellspacing=0 cellpadding=1 width='100%'>")
    for lab, val, note in subscore_rows(st):
        out.append(f"<tr><td>{e(lab)}</td><td align=right><b>{e(val)}</b></td>"
                   f"<td style='color:{theme.MUTED}'>&nbsp;{e(note)}</td></tr>")
    out.append("</table>")
    # WHY
    if st.score:
        out.append(f"<h3 style='margin:6px 0;color:{theme.score_hex(st.score.total)}'>"
                   f"{e(t('lbl.why'))} — {e(t('score.opportunity'))} {st.score.total}/100 "
                   f"<span style='color:{theme.MUTED};font-size:9pt'>({e(t('lbl.coverage'))} {st.score.coverage_pct}%)</span></h3>")
        out.append("<table cellspacing=0 cellpadding=1 width='100%'>")
        for p, lab, val, src in why_items(st):
            out.append(f"<tr><td style='color:{theme.GREEN}'><b>{e(p)}</b></td><td><b>{e(lab)}</b></td><td>{e(val)}</td>"
                       f"<td style='color:{theme.MUTED}'>{e(src)}</td></tr>")
        out.append("</table>")
    elif q and q.status == INVALID:
        out.append(f"<h3 style='color:{theme.RED}'>{e(t('lbl.not_scored_invalid'))}</h3>")
    # EARLY
    es = st.early
    if es:
        head = (f"{t('common.unknown')} — {t('note.' + es.note)} ({es.groups_computable}/7)" if es.strength is None
                else f"{es.strength}/100 ({es.groups_computable}/7)")
        flag = " ⚡ EARLY SIGNAL" if es.is_early else ""
        out.append(f"<h3 style='margin:6px 0;color:{theme.ACCENT if es.is_early else theme.TEXT}'>"
                   f"{e(t('score.early_signal'))}: {e(head)}{flag}</h3>")
        out.append(f"<p style='color:{theme.MUTED}'>{e(t('lbl.early_rule'))} · {e(t('lbl.transition'))}: "
                   f"{e(t('common.yes') if es.transition else t('common.no') if es.transition is False else t('common.unknown'))}"
                   f" · {e(t('lbl.history'))}: {es.history_min:.0f}m</p>")
        if es.suppressed:
            out.append(f"<p style='color:{theme.ORANGE}'><b>{e(t('lbl.suppressed'))}:</b> {e('; '.join(es.suppressed))}</p>")
        out.append("<table cellspacing=0 cellpadding=1 width='100%'>")
        for x in es.signals:
            mark, col = ("✓", theme.GREEN) if x.fired else (("·", theme.TEXT) if x.fired is False else ("—", theme.MUTED))
            val = x.value if x.fired is not None else (x.raw.get("missing") or t("note." + (x.note or "no_data")))
            if x.raw.get("blocked"):
                val += f"  [{x.raw['blocked']}]"
            out.append(f"<tr style='color:{col}'><td><b>{mark} {e(t('signal.' + x.key))}</b></td><td>{e(val)}</td>"
                       f"<td style='color:{theme.MUTED}'>{e(x.source)}</td></tr>")
        out.append("</table>")
    # RISK FLAGS
    if rk:
        out.append(f"<h3 style='margin:6px 0;color:{theme.risk_hex(rk.score)}'>{e(t('lbl.risk_flags'))} — "
                   f"{rk.score}/100 {e(t('state.' + rk.level))}</h3><table cellspacing=0 cellpadding=1 width='100%'>")
        for f in rk.factors:
            name, detail = risk_text(f)
            out.append(f"<tr><td style='color:{theme.ORANGE}'><b>⚠ {e(name)}</b></td><td>{e(detail)}</td>"
                       f"<td style='color:{theme.MUTED}'>{e(f.source)}</td></tr>")
        out.append("</table>")
    out.append(f"<p><b>{e(t('lbl.filters'))}:</b> "
               f"{e(', '.join(filter_text(k) for k in st.filter_fails) if st.filter_fails else t('lbl.all_passed'))}</p>")
    out.append(f"<p style='color:{theme.YELLOW}'><i>{e(t('app.disclaimer'))}</i></p>")
    return "".join(out)


def holders_html(st: TokenState) -> str:
    out = [metrics_table(st, "holders")]
    h = st.holders
    if h and h.top:
        out.append(f"<h3>{e(t('lbl.top_holders'))}</h3><table cellspacing=0 cellpadding=1 width='100%'>")
        for i, x in enumerate(h.top[:50]):
            color = theme.ORANGE if "CREATOR" in x.tags else theme.YELLOW if x.pct > 5 else theme.TEXT
            out.append(f"<tr style='color:{color}'><td>{i+1}</td><td>{e(x.owner)}</td><td align=right>{x.amount:,.0f}</td>"
                       f"<td align=right>{x.pct:.2f}%</td><td>{e(', '.join(x.tags))}</td></tr>")
        out.append("</table>")
    hi = st.holder_intel
    if hi and hi.flags:
        out.append("<p>" + "<br>".join(f"⚠ {e(t('holderflag.' + f))}" for f in hi.flags) + "</p>")
    return "".join(out)


def risk_html(st: TokenState) -> str:
    rk = st.risk
    if not rk:
        return ""
    out = [f"<h3>{e(t('score.risk'))} {rk.score}/100 {e(t('state.' + rk.level))}</h3>",
           "<table cellspacing=0 cellpadding=2>"]
    for c, v in rk.categories.items():
        out.append(f"<tr><td>{e(t('riskcat.' + c))}</td><td align=right style='color:{theme.risk_hex(v)}'><b>{v}</b></td></tr>")
    out.append("</table><table cellspacing=0 cellpadding=2 width='100%'>")
    for f in rk.factors:
        name, detail = risk_text(f)
        out.append(f"<tr><td style='color:{theme.ORANGE}'>+{f.points}</td><td><b>{e(name)}</b></td><td>{e(detail)}</td>"
                   f"<td>{e(t('riskcat.' + f.category))}</td><td style='color:{theme.MUTED}'>{e(f.source)}</td></tr>")
    out.append("</table>")
    out.append(f"<p style='color:{theme.MUTED}'>{e(t('lbl.not_measurable'))}: "
               f"{e(', '.join(t('missing.' + k) for k in rk.missing))}</p>")
    return "".join(out)


def events_html(events) -> str:
    if not events:
        return f"<p style='color:{theme.MUTED}'>{e(t('common.none'))}</p>"
    rows = []
    for ev in sorted(events, key=lambda x: -x.ts):
        name, detail = event_text(ev)
        rows.append(f"<tr style='color:{SEV_COLOR.get(ev.severity, theme.TEXT)}'><td>{time.strftime('%H:%M:%S', time.localtime(ev.ts))}"
                    f"</td><td><b>{e(name)}</b></td><td>{e(detail)}</td><td style='color:{theme.MUTED}'>{e(ev.source)}</td></tr>")
    return "<table cellspacing=0 cellpadding=2 width='100%'>" + "".join(rows) + "</table>"


class HistoryChart(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        lay = QVBoxLayout(self)
        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        self.plots = pg.GraphicsLayoutWidget()
        lay.addWidget(self.plots)
        self.p = {}
        for row, key in enumerate(("mc", "liquidity", "vol_5m", "scores")):
            p = self.plots.addPlot(row=row, col=0, title=t(f"chart.{key}"))
            p.setAxisItems({"bottom": pg.DateAxisItem()})
            p.showGrid(x=True, y=True, alpha=0.15)
            self.p[key] = p
        self.empty = QLabel("")
        self.empty.setObjectName("muted")
        lay.addWidget(self.empty)

    def load(self, mint: str) -> None:
        snaps = self.db.snapshots(mint, since=time.time() - 7 * 86400)
        self.empty.setText("" if snaps else t("lbl.no_snapshots"))
        for key, col, color in (("mc", "mc", theme.ACCENT), ("liquidity", "liquidity", theme.PURPLE),
                                ("vol_5m", "vol_5m", theme.YELLOW)):
            p = self.p[key]
            p.clear()
            pts = [(r["ts"], r[col]) for r in snaps if r[col] is not None and r["dq_status"] != "INVALID"]
            if pts:
                p.plot([a for a, _ in pts], [b for _, b in pts], pen=pg.mkPen(color, width=2))
        p = self.p["scores"]
        p.clear()
        p.addLegend()
        for col, color, name in (("score", theme.GREEN, t("score.opportunity")), ("risk", theme.RED, t("score.risk")),
                                 ("early_signal", theme.ACCENT, t("score.early_signal"))):
            pts = [(r["ts"], r[col]) for r in snaps if r[col] is not None]
            if pts:
                p.plot([a for a, _ in pts], [b for _, b in pts], pen=pg.mkPen(color, width=1.5), name=name)


class DetailPanel(QWidget):
    watch_requested = Signal(str)
    refresh_requested = Signal(str)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.state: TokenState | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        self.header = QLabel(t("lbl.select_token"))
        self.header.setObjectName("title")
        self.header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.header.setWordWrap(True)
        lay.addWidget(self.header)
        btns = QHBoxLayout()
        for key in ("pumpfun", "dexscreener", "solscan"):
            b = QPushButton(t(f"btn.open_{key}"))
            b.clicked.connect(lambda _=False, k=key: self.state and QDesktopServices.openUrl(QUrl(self.state.links[k])))
            btns.addWidget(b)
        lay.addLayout(btns)
        btns2 = QHBoxLayout()
        self.watch_btn = QPushButton(t("btn.add_watch"))
        self.watch_btn.setObjectName("primary")
        self.watch_btn.clicked.connect(lambda: self.state and self.watch_requested.emit(self.state.mint))
        copy_btn = QPushButton(t("btn.copy_ca"))
        copy_btn.clicked.connect(lambda: self.state and QGuiApplication.clipboard().setText(self.state.mint))
        refresh = QPushButton(t("btn.deep_refresh"))
        refresh.clicked.connect(lambda: self.state and self.refresh_requested.emit(self.state.mint))
        for b in (self.watch_btn, copy_btn, refresh):
            btns2.addWidget(b)
        lay.addLayout(btns2)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.views: dict[str, QTextBrowser] = {}
        for key in TABS:
            if key == "history":
                self.chart = HistoryChart(db)
                self.tabs.addTab(self.chart, t(f"tab.d_{key}"))
            else:
                v = QTextBrowser()
                self.views[key] = v
                self.tabs.addTab(v, t(f"tab.d_{key}"))
        self.tabs.currentChanged.connect(lambda _: self._render_current())
        lay.addWidget(self.tabs, 1)

    def set_state(self, st: TokenState | None) -> None:
        self.state = st
        if not st:
            return
        sc = st.score.total if st.score else "—"
        dq = f"{t('state.' + st.quality.status)} {st.quality.score}" if st.quality else "—"
        es = st.early.strength if st.early and st.early.strength is not None else "—"
        self.header.setText(f"${st.info.symbol or '?'} · {t('col.opp')} {sc} · {t('col.risk')} "
                            f"{st.risk.score if st.risk else '—'} · {t('col.early')} {es} · {t('col.dq')} {dq}")
        self.watch_btn.setText(t("btn.in_watch") if st.watch else t("btn.add_watch"))
        self._render_current()

    def _render_current(self) -> None:
        st = self.state
        if not st:
            return
        key = TABS[self.tabs.currentIndex()]
        if key == "history":
            self.chart.load(st.mint)
            return
        v = self.views[key]
        if key == "overview":
            html_ = overview_html(st)
        elif key == "holders":
            html_ = holders_html(st)
        elif key == "risk":
            html_ = risk_html(st)
        elif key == "events":
            html_ = events_html(self.db.recent_events(100, st.mint) or st.recent_events)
        else:
            html_ = metrics_table(st, key)
            if key == "market":
                html_ += f"<p style='color:{theme.MUTED}'>{e(st.mint)}</p>"
        pos = v.verticalScrollBar().value()
        v.setHtml(html_)
        v.verticalScrollBar().setValue(pos)
