"""Main window: TOP OPPORTUNITIES strip, 12 tabs, 15-tab token detail, LIVE EVENTS, status bar.
Every visible string comes from i18n. The worker lives outside the window so the UI can be rebuilt
when the language changes without stopping the scanner."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from core.config import Settings
from core.models import TokenState
from database.db import Database
from i18n import t
from scoring.ranking import rank_early, rank_opportunities
from ui.detail_panel import DetailPanel
from ui.settings_tab import SettingsTab
from ui.tabs import AlertsTab, EventsPanel, HistoryTab, NarrativeTab, NotAvailableTab, WatchlistTab
from ui.token_table import DEV_COLS, EARLY_COLS, NEW_COLS, SOCIAL_COLS, TOP_COLS, WHALE_COLS, TokenTable
from ui.top_cards import TopCards
from ui.worker import EngineWorker


class MainWindow(QMainWindow):
    language_changed = Signal(str)

    def __init__(self, settings: Settings, db: Database, worker: EngineWorker):
        super().__init__()
        self.settings, self.db, self.worker = settings, db, worker
        self.states: dict[str, TokenState] = {}
        self.setWindowTitle(t("app.window_title"))
        self.resize(1680, 1000)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        head = QHBoxLayout()
        title = QLabel(t("app.top_title"))
        title.setObjectName("title")
        head.addWidget(title)
        sub = QLabel(t("app.top_subtitle"))
        sub.setObjectName("muted")
        head.addWidget(sub)
        head.addStretch()
        lay.addLayout(head)
        self.cards = TopCards()
        self.cards.selected.connect(self.show_token)
        lay.addWidget(self.cards)

        split = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        new_wrap = QWidget()
        nl = QVBoxLayout(new_wrap)
        nl.setContentsMargins(0, 4, 0, 0)
        opts = QHBoxLayout()
        self.only_pass = QCheckBox(t("lbl.only_pass"))
        self.hide_invalid = QCheckBox(t("lbl.hide_invalid"))
        for c in (self.only_pass, self.hide_invalid):
            c.toggled.connect(self.render)
            opts.addWidget(c)
        opts.addStretch()
        nl.addLayout(opts)
        self.new_table = TokenTable(NEW_COLS, sort_col=2, desc=False)
        nl.addWidget(self.new_table)
        self.top_table = TokenTable(TOP_COLS, sort_col=2)
        self.early_table = TokenTable(EARLY_COLS, sort_col=3)
        self.dev_table = TokenTable(DEV_COLS, sort_col=len(DEV_COLS) - 1, desc=False)
        self.whale_table = TokenTable(WHALE_COLS, sort_col=5)
        self.social_table = TokenTable(SOCIAL_COLS, sort_col=5)
        self.narrative = NarrativeTab()
        self.alerts = AlertsTab(db)
        self.watchlist = WatchlistTab(db)
        self.history = HistoryTab(db)
        self.settings_tab = SettingsTab(settings, worker.health)
        self.settings_tab.language_changed.connect(self.language_changed.emit)

        social_wrap = QWidget()
        sl = QVBoxLayout(social_wrap)
        sl.setContentsMargins(0, 4, 0, 0)
        sn = QLabel(t("na.social"))
        sn.setWordWrap(True)
        sn.setObjectName("muted")
        sl.addWidget(sn)
        sl.addWidget(self.social_table)

        for widget, key in ((new_wrap, "new_coins"), (self.top_table, "top"), (self.early_table, "early"),
                            (NotAvailableTab("smart_money"), "smart_money"), (self.whale_table, "whales"),
                            (self.dev_table, "dev"), (social_wrap, "social"), (self.narrative, "narrative"),
                            (self.alerts, "alerts"), (self.watchlist, "watchlist"), (self.history, "history"),
                            (self.settings_tab, "settings")):
            self.tabs.addTab(widget, t(f"tab.{key}"))
        self.tabs.currentChanged.connect(self._tab_changed)
        split.addWidget(self.tabs)
        self.detail = DetailPanel(db)
        split.addWidget(self.detail)
        split.setSizes([1080, 600])

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(split)
        bottom = QSplitter(Qt.Horizontal)
        self.events_panel = EventsPanel(db)
        self.events_panel.selected.connect(lambda mint: self.states.get(mint) and self.show_token(self.states[mint]))
        bottom.addWidget(self.events_panel)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(500)
        bottom.addWidget(self.logbox)
        bottom.setSizes([1100, 580])
        vsplit.addWidget(bottom)
        vsplit.setSizes([780, 170])
        lay.addWidget(vsplit, 1)

        for tb in (self.new_table, self.top_table, self.early_table, self.dev_table, self.whale_table, self.social_table):
            tb.selected.connect(self.show_token)
        self.detail.watch_requested.connect(worker.add_watch)
        self.detail.refresh_requested.connect(self._deep_refresh)
        self.watchlist.add_requested.connect(worker.add_watch)
        self.watchlist.remove_requested.connect(worker.remove_watch)
        self.watchlist.selected.connect(lambda mint: self.states.get(mint) and self.show_token(self.states[mint]))
        self._links = [(worker.updated, self.on_update), (worker.events, self.events_panel.add),
                       (worker.log, self.logbox.appendPlainText), (worker.analyzed, self.on_analyzed),
                       (worker.failed, self._on_failed)]
        for sig, slot in self._links:
            sig.connect(slot)

        self.status = QLabel(t("lbl.starting"))
        self.statusBar().addWidget(self.status, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._status_tick)
        self.timer.start(3000)
        if worker.last_states:
            self.on_update(worker.last_states)
        self.events_panel._render()

    def detach(self) -> None:
        """Disconnect from the shared worker (used when the window is rebuilt for a language switch)."""
        for sig, slot in self._links:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.timer.stop()

    def _on_failed(self, msg: str) -> None:
        self.logbox.appendPlainText(f"error: {msg}")

    # ---------------------------------------------------------------- data flow
    def on_update(self, states: list[TokenState]) -> None:
        self.states = {s.mint: s for s in states}
        self.render()
        self.watchlist.update_states(states)
        if self.tabs.currentWidget() is self.alerts:
            self.alerts.refresh()
        cur = self.detail.state
        if cur and cur.mint in self.states:
            self.show_token(self.states[cur.mint])

    def on_analyzed(self, st: TokenState) -> None:
        self.states[st.mint] = st
        self.show_token(st)
        self.watchlist.update_states([st])
        self.watchlist.reload_list()

    def render(self) -> None:
        all_states = list(self.states.values())
        with_market = [s for s in all_states if s.market]
        new_rows = [s for s in with_market if (not self.only_pass.isChecked() or not s.filter_fails)
                    and (not self.hide_invalid.isChecked() or s.dq_status != "INVALID")]
        self.new_table.set_rows(new_rows)
        top = rank_opportunities(all_states)
        self.top_table.set_rows(top)
        early = rank_early(all_states)
        self.early_table.set_rows(early)
        self.dev_table.set_rows([s for s in all_states if s.dev])
        self.whale_table.set_rows([s for s in all_states if s.holders])
        self.social_table.set_rows([s for s in with_market if s.info.twitter or s.info.telegram or s.info.website])
        if self.tabs.currentWidget() is self.narrative:
            self.narrative.update_states(all_states)
        self.cards.set_states(all_states)
        n_early = sum(1 for s in early if s.early.is_early)
        self.tabs.setTabText(0, f"{t('tab.new_coins')} ({len(new_rows)})")
        self.tabs.setTabText(1, f"{t('tab.top')} ({len(top)})")
        self.tabs.setTabText(2, f"{t('tab.early')} ({n_early}⚡)")

    def show_token(self, st: TokenState) -> None:
        self.detail.set_state(st)

    def _deep_refresh(self, mint: str) -> None:
        self.logbox.appendPlainText(t("lbl.deep_refresh", mint=mint))
        self.worker.analyze(mint)

    def _tab_changed(self, _i: int) -> None:
        w = self.tabs.currentWidget()
        if w is self.alerts:
            self.alerts.refresh()
        elif w is self.history:
            self.history.refresh_stats()
        elif w is self.watchlist:
            self.watchlist.redraw()
        elif w is self.narrative:
            self.narrative.update_states(list(self.states.values()))
        elif w is self.settings_tab:
            self.settings_tab.refresh_health()

    def _status_tick(self) -> None:
        h = self.worker.health()
        parts = [f"{'🟢' if s.ok else '🔴'} {n}" for n, s in sorted(h.items())]
        dq = {k: sum(1 for s in self.states.values() if s.dq_status == k) for k in ("VALID", "PARTIAL", "INVALID")}
        hel = self.worker.helius_state()
        hel_icon = {"CONNECTED": "🟢", "FAILED": "🔴", "NO_KEY": "⚪"}.get(hel, "🟡")
        self.status.setText(t("lbl.status", tracked=len(self.states), valid=dq["VALID"], partial=dq["PARTIAL"],
                              invalid=dq["INVALID"]) + f"   |   {hel_icon} HELIUS = {hel}   |   " + "   ".join(parts))
        if self.tabs.currentWidget() is self.settings_tab:
            self.settings_tab.refresh_health()

    def closeEvent(self, e) -> None:
        if getattr(self, "_rebuilding", False):
            return super().closeEvent(e)
        self.worker.stop()
        self.worker.wait(8000)
        super().closeEvent(e)
