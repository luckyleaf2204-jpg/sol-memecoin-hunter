"""Secondary tabs: NARRATIVE, CẢNH BÁO/ALERTS, WATCHLIST, LỊCH SỬ/HISTORY (backtest), NOT AVAILABLE pages,
and the LIVE EVENTS panel. All text via i18n."""
from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from alerts.report import event_text, liquidity_label, usd
from analytics.backtest import TARGETS, run_backtest
from analytics.narratives import aggregate_narratives
from core.models import Event, TokenState
from database.db import Database
from i18n import t
from ui import theme

SEV_COLOR = {"positive": theme.GREEN, "info": theme.TEXT, "warning": theme.ORANGE, "critical": theme.RED}


def _table(header_keys: list[str]) -> QTableWidget:
    tb = QTableWidget(0, len(header_keys))
    tb.setHorizontalHeaderLabels([t(k) if "." in k else k for k in header_keys])
    tb.verticalHeader().setVisible(False)
    tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tb.setSelectionBehavior(QAbstractItemView.SelectRows)
    tb.setAlternatingRowColors(True)
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    tb.horizontalHeader().setStretchLastSection(True)
    return tb


def _fill(tb: QTableWidget, rows: list[list], colors: dict[int, QColor] | None = None) -> None:
    tb.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            it = QTableWidgetItem("" if v is None else str(v))
            if colors and r in colors:
                it.setForeground(colors[r])
            tb.setItem(r, c, it)


class NotAvailableTab(QTextBrowser):
    def __init__(self, key: str):
        super().__init__()
        self.setHtml(f"<h2 style='color:{theme.MUTED}'>{t('tab.' + key)} — {t('common.not_available')}</h2>"
                     f"<p>{t('na.' + key)}</p><p style='color:{theme.MUTED}'>{t('na.rule')}</p>")


class NarrativeTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        note = QLabel(t("lbl.narrative_note"))
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.table = _table(["col.narrative", "col.tokens", "col.new_30m", "col.prev_30m", "col.launch_trend",
                             "col.valid_tokens", "col.vol5m_sum", "col.top_tokens", "col.narrative_score"])
        lay.addWidget(self.table)

    def update_states(self, states: list[TokenState]) -> None:
        rows = []
        for a in aggregate_narratives(states):
            trend = f"{a['launch_trend']:.2f}×" if a["launch_trend"] is not None else t("common.unknown")
            rows.append([t(f"narrative.{a['tag']}"), a["tokens"], a["new_30m"], a["prev_30m"], trend,
                         a["valid_tokens"], usd(a["vol_5m"]), ", ".join(a["top"]), t("common.not_available")])
        _fill(self.table, rows)


class EventsPanel(QWidget):
    selected = Signal(str)   # mint

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel(t("lbl.live_events"))
        title.setStyleSheet(f"font-weight:800;color:{theme.ACCENT}")
        head.addWidget(title)
        self.filter = QComboBox()
        self.filter.addItem(t("lbl.all_events"), "")
        for sev in ("positive", "warning", "critical"):
            self.filter.addItem(t(f"severity.{sev}"), sev)
        self.filter.currentIndexChanged.connect(self._render)
        head.addStretch()
        head.addWidget(self.filter)
        lay.addLayout(head)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: self.selected.emit(it.data(Qt.UserRole)))
        lay.addWidget(self.list)
        self.events: list[Event] = list(reversed(db.recent_events(200)))

    def add(self, events: list[Event]) -> None:
        self.events = (self.events + events)[-400:]
        self._render()

    def _render(self) -> None:
        sev = self.filter.currentData()
        self.list.clear()
        for ev in reversed(self.events):
            if sev and ev.severity != sev:
                continue
            name, detail = event_text(ev)
            it = QListWidgetItem(f"{time.strftime('%H:%M:%S', time.localtime(ev.ts))}  ${ev.symbol}  {name} — {detail}")
            it.setForeground(QColor(SEV_COLOR.get(ev.severity, theme.TEXT)))
            it.setData(Qt.UserRole, ev.mint)
            self.list.addItem(it)
            if self.list.count() >= 300:
                break


class AlertsTab(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Vertical)
        self.table = _table(["col.time", "col.token", "col.opp", "col.risk", "col.mc", "col.telegram", "col.mint"])
        self.table.itemSelectionChanged.connect(self._show)
        self.msg = QTextBrowser()
        split.addWidget(self.table)
        split.addWidget(self.msg)
        lay.addWidget(split)
        self._rows = []

    def refresh(self) -> None:
        self._rows = self.db.recent_alerts(300)
        _fill(self.table, [[time.strftime("%m-%d %H:%M:%S", time.localtime(r["ts"])), f"${r['symbol']}",
                            r["score"], r["risk"], usd(r["mc"]), t("lbl.sent") if r["sent_telegram"] else t("lbl.in_app"),
                            r["mint"]] for r in self._rows])

    def _show(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self._rows):
            self.msg.setHtml(self._rows[r]["message"].replace("\n", "<br>"))


class WatchlistTab(QWidget):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    selected = Signal(str)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.current: str | None = None
        self.states: dict[str, TokenState] = {}
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(t("lbl.paste_ca"))
        self.input.returnPressed.connect(self._add)
        add = QPushButton(t("btn.add"))
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        rm = QPushButton(t("btn.remove"))
        rm.clicked.connect(self._remove)
        row.addWidget(self.input, 1)
        row.addWidget(add)
        row.addWidget(rm)
        lay.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._pick)
        split.addWidget(self.list)
        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        self.plots = pg.GraphicsLayoutWidget()
        self.p = {}
        for r, key in enumerate(("mc", "liquidity", "vol_5m", "holders")):
            p = self.plots.addPlot(row=r, col=0, title=t(f"chart.{key}"))
            p.setAxisItems({"bottom": pg.DateAxisItem()})
            p.showGrid(x=True, y=True, alpha=0.15)
            self.p[key] = p
        split.addWidget(self.plots)
        split.setSizes([260, 900])
        lay.addWidget(split, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.reload_list()

    def _add(self) -> None:
        mint = self.input.text().strip()
        if 32 <= len(mint) <= 44:
            self.add_requested.emit(mint)
            self.input.clear()
            self.summary.setText(t("lbl.analysing", mint=mint))

    def _remove(self) -> None:
        it = self.list.currentItem()
        if it:
            self.remove_requested.emit(it.data(Qt.UserRole))
            self.db.remove_watch(it.data(Qt.UserRole))
            self.reload_list()

    def _pick(self, it, _prev=None) -> None:
        if it:
            self.current = it.data(Qt.UserRole)
            self.selected.emit(self.current)
            self.redraw()

    def reload_list(self) -> None:
        cur = self.current
        self.list.blockSignals(True)
        self.list.clear()
        for mint in self.db.watchlist():
            st = self.states.get(mint)
            it = QListWidgetItem(f"${st.info.symbol}  ·  {mint[:6]}…" if st and st.info.symbol else mint)
            it.setData(Qt.UserRole, mint)
            self.list.addItem(it)
            if mint == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def update_states(self, states: list[TokenState]) -> None:
        changed = False
        for s in states:
            if s.watch:
                changed |= s.mint not in self.states or not self.states[s.mint].info.symbol
                self.states[s.mint] = s
        if changed:
            self.reload_list()
        if self.isVisible():
            self.redraw()

    def redraw(self) -> None:
        if not self.current:
            return
        snaps = self.db.snapshots(self.current, since=time.time() - 7 * 86400)
        for key, col, color in (("mc", "mc", theme.ACCENT), ("liquidity", "liquidity", theme.PURPLE),
                                ("vol_5m", "vol_5m", theme.YELLOW), ("holders", "holders", theme.GREEN)):
            p = self.p[key]
            p.clear()
            pts = [(r["ts"], r[col]) for r in snaps if r[col] is not None and r["dq_status"] != "INVALID"]
            if pts:
                p.plot([a for a, _ in pts], [b for _, b in pts], pen=pg.mkPen(color, width=2))
        st = self.states.get(self.current)
        if st and st.market:
            m = st.market
            self.summary.setText(
                f"${st.info.symbol} · {t('col.dq')} {t('state.' + st.dq_status)} · MC {usd(m.market_cap)} · "
                f"{t('col.liq')} {liquidity_label(st)} · {t('col.vol5m')} {usd(m.vol_5m)} · "
                f"{t('col.opp')} {st.score.total if st.score else '—'} · {t('col.risk')} {st.risk.score if st.risk else '—'} · "
                f"{t('col.early')} {st.early.strength if st.early and st.early.strength is not None else '—'} · "
                f"{t('lbl.snapshots', n=len(snaps))}")


class HistoryTab(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.stats = QLabel("")
        self.column = QComboBox()
        self.column.addItem(t("score.opportunity"), "score")
        self.column.addItem(t("score.early_signal"), "early_signal")
        run = QPushButton(t("btn.run_backtest"))
        run.setObjectName("primary")
        run.clicked.connect(self.run)
        top.addWidget(self.stats, 1)
        top.addWidget(self.column)
        top.addWidget(run)
        lay.addLayout(top)
        expl = QLabel(t("lbl.backtest_rule"))
        expl.setWordWrap(True)
        expl.setObjectName("muted")
        lay.addWidget(expl)
        self.table = _table(["col.threshold", "col.window", "col.signals", "col.evaluated", *TARGETS.keys(),
                             "col.drop50", "col.median_gain"])
        lay.addWidget(self.table)
        self.refresh_stats()

    def refresh_stats(self) -> None:
        s = self.db.stats()
        first = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["first"])) if s["first"] else "—"
        self.stats.setText(t("lbl.db_stats", snapshots=f"{s['snapshots']:,}", tokens=f"{s['tokens']:,}",
                             events=f"{s['events']:,}", first=first))

    def run(self) -> None:
        self.refresh_stats()
        rows = [[r.threshold, r.window, r.signals, r.evaluated, *[f"{r.hits[k]:.0f}%" for k in TARGETS],
                 f"{r.drop50_pct:.0f}%", f"{r.median_max_gain_pct:.0f}%" if r.median_max_gain_pct is not None else "—"]
                for r in run_backtest(self.db.conn, column=self.column.currentData())]
        _fill(self.table, rows)
