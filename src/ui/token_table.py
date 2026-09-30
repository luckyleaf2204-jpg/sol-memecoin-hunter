"""Reusable sortable token table (model + view). Headers are i18n keys ("col.*")."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from alerts.report import age_str, filter_text, usd
from core.models import INVALID, PARTIAL, VALID, TokenState
from i18n import t
from ui import theme

SORT_ROLE = Qt.UserRole + 1
DQ_COLORS = {VALID: theme.GREEN, PARTIAL: theme.YELLOW, INVALID: theme.RED}


@dataclass
class Col:
    key: str                                     # i18n key under "col."
    value: Callable[[TokenState], Any]
    fmt: Callable[[Any], str] = lambda v: "" if v is None else str(v)
    color: Callable[[Any], QColor] | None = None
    special: str = ""                            # "dq" | "filters"


def _f2(v):
    return "" if v is None else f"{v:.2f}"


def _x(v):
    return "" if v is None else f"{v:.2f}×"


def _int(v):
    return "" if v is None else f"{v:,}"


def _pct(v, d=1):
    return "" if v is None else f"{v:.{d}f}%"


def _usd(v):
    return "" if v is None else usd(v)


def _signed(v):
    return "" if v is None else f"{v:+.1f}%"


def _state(v):
    return "" if not v or v == "UNKNOWN" else t(f"state.{v}")


def _green_red(v):
    return QColor(theme.GREEN if v and v > 0 else theme.RED if v and v < 0 else theme.TEXT)


m = lambda st: st.market  # noqa: E731


def dev_pct(s):
    return s.dev.current_pct if s.dev and s.dev.balance_verified else None


def sub(key):
    return lambda s: s.subscores[key].score if key in s.subscores else None


C = {
    "token": Col("token", lambda s: f"${s.info.symbol or s.mint[:6]}"),
    "dq": Col("dq", lambda s: s.quality.score if s.quality else None, special="dq"),
    "age": Col("age", lambda s: s.age_minutes, age_str),
    "lifecycle": Col("lifecycle", lambda s: s.lifecycle, lambda v: t(f"lifecycle.{v}")),
    "mc": Col("mc", lambda s: m(s).market_cap if m(s) else None, _usd),
    "liq": Col("liq", lambda s: m(s).liquidity_usd if m(s) else None, _usd),
    "vol5m": Col("vol5m", lambda s: m(s).vol_5m if m(s) else None, _usd),
    "vol1h": Col("vol1h", lambda s: m(s).vol_1h if m(s) else None, _usd),
    "bs": Col("bs", lambda s: m(s).buy_sell_ratio_5m if m(s) else None, _f2,
              lambda v: QColor(theme.GREEN if v >= 1.2 else theme.RED if v < 0.8 else theme.TEXT)),
    "txns": Col("txns", lambda s: m(s).txns_5m if m(s) else None, _int),
    "holders": Col("holders", lambda s: s.holders.holder_count if s.holders else None, _int),
    "top10": Col("top10", lambda s: s.holders.top10_pct if s.holders else None, _pct),
    "dev": Col("dev", dev_pct, lambda v: _pct(v, 2)),
    "curve": Col("curve", lambda s: 101 if s.info.complete else s.info.curve_progress,
                 lambda v: "" if v is None else ("GRAD" if v > 100 else f"{v:.0f}%")),
    "opp": Col("opp", lambda s: s.score.total if s.score else None, _int, theme.score_color),
    "risk": Col("risk", lambda s: s.risk.score if s.risk else None, _int, theme.risk_color),
    "early": Col("early", lambda s: s.early.strength if s.early else None,
                 lambda v: "" if v is None else f"{v}", theme.score_color),
    "is_early": Col("is_early", lambda s: (1 if s.early.is_early else 0) if s.early and s.early.is_early is not None else None,
                    lambda v: "" if v is None else ("⚡" if v else "·"), lambda v: QColor(theme.ACCENT if v else theme.MUTED)),
    "fired": Col("fired", lambda s: s.early.fired_count if s.early else None, _int),
    "history": Col("history", lambda s: s.early.history_min if s.early else None, lambda v: "" if v is None else f"{v:.0f}m"),
    "filters": Col("filters", lambda s: len(s.filter_fails), special="filters"),
    "vol_accel": Col("vol_accel", lambda s: m(s).vol_accel if m(s) else None, _x,
                     lambda v: QColor(theme.GREEN if v >= 2 else theme.TEXT)),
    "txn_accel": Col("txn_accel", lambda s: m(s).txn_accel if m(s) else None, _x),
    "pc5": Col("pc5", lambda s: m(s).price_change_5m if m(s) else None, _signed, _green_red),
    "pc1h": Col("pc1h", lambda s: m(s).price_change_1h if m(s) else None, _signed, _green_red),
    "holder_growth": Col("holder_growth", lambda s: s.holder_growth_pct, _signed, _green_red),
    "momentum": Col("momentum", sub("momentum"), _int, theme.score_color),
    "holder_s": Col("holder_s", sub("holder"), _int, theme.score_color),
    "liq_s": Col("liq_s", sub("liquidity"), _int, theme.score_color),
    "liq_state": Col("liq_state", lambda s: s.liquidity_intel.state if s.liquidity_intel else None, _state),
    "whale_state": Col("whale_state", lambda s: s.whale_intel.state if s.whale_intel else None, _state),
    "whales": Col("whales", lambda s: s.whale_intel.whale_count if s.whale_intel else None, _int),
    "whale_pct": Col("whale_pct", lambda s: s.whale_intel.whale_pct if s.whale_intel else None, _pct),
    "whale_delta": Col("whale_delta", lambda s: s.whale_intel.delta_pct if s.whale_intel else None,
                       lambda v: "" if v is None else f"{v:+.2f}%", _green_red),
    "creator": Col("creator", lambda s: s.info.creator, lambda v: f"{v[:6]}…{v[-4:]}" if v else ""),
    "dev_status": Col("dev_status", lambda s: s.dev.status if s.dev and s.dev.balance_verified else "UNKNOWN",
                      lambda v: t(f"devstatus.{v}"),
                      lambda v: QColor(theme.RED if v in ("SOLD ALL", "MAJOR SELL") else
                                       theme.MUTED if v == "UNKNOWN" else theme.TEXT)),
    "sold": Col("sold", lambda s: s.dev.sold_pct if s.dev and s.dev.balance_verified else None, lambda v: _pct(v, 0)),
    "sol_bal": Col("sol_bal", lambda s: s.dev.sol_balance if s.dev else None, _f2),
    "prev_tokens": Col("prev_tokens", lambda s: s.dev.prev_tokens_count if s.dev and s.dev.history_verified else None, _int),
    "graduated": Col("graduated", lambda s: s.dev.prev_graduated if s.dev and s.dev.history_verified else None, _int),
    "funding": Col("funding", lambda s: s.dev.funding_wallet if s.dev else None, lambda v: f"{v[:6]}…{v[-4:]}" if v else ""),
    "dev_s": Col("dev_s", sub("dev"), _int, theme.score_color),
    "x": Col("x", lambda s: s.info.twitter or None, lambda v: "✓" if v else ""),
    "tg": Col("tg", lambda s: s.info.telegram or None, lambda v: "✓" if v else ""),
    "web": Col("web", lambda s: s.info.website or None, lambda v: "✓" if v else ""),
    "narrative": Col("narrative", lambda s: ", ".join(t(f"narrative.{x}") for x in s.narratives)),
}

NEW_COLS = [C[k] for k in ("token", "dq", "age", "lifecycle", "mc", "liq", "vol5m", "vol1h", "bs", "txns", "holders",
                           "top10", "dev", "curve", "opp", "early", "risk", "filters")]
TOP_COLS = [C[k] for k in ("token", "dq", "opp", "risk", "early", "momentum", "holder_s", "liq_s", "lifecycle",
                           "mc", "liq", "vol5m", "bs", "holders", "age")]
EARLY_COLS = [C[k] for k in ("token", "dq", "is_early", "early", "fired", "history", "lifecycle", "vol_accel",
                             "txn_accel", "pc5", "holder_growth", "liq_state", "opp", "risk")]
DEV_COLS = [C[k] for k in ("token", "creator", "dev", "dev_status", "sold", "sol_bal", "prev_tokens", "graduated",
                           "funding", "dev_s", "risk")]
WHALE_COLS = [C[k] for k in ("token", "dq", "whale_state", "whales", "whale_pct", "whale_delta", "top10", "holders",
                             "holder_growth", "risk")]
SOCIAL_COLS = [C[k] for k in ("token", "x", "tg", "web", "narrative", "opp", "risk")]


class TokenTableModel(QAbstractTableModel):
    def __init__(self, cols: list[Col]):
        super().__init__()
        self.cols = cols
        self.rows: list[TokenState] = []

    def set_rows(self, rows: list[TokenState]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return t(f"col.{self.cols[section].key}")
        return None

    def data(self, index, role=Qt.DisplayRole):
        st, col = self.rows[index.row()], self.cols[index.column()]
        try:
            v = col.value(st)
        except (AttributeError, TypeError, ZeroDivisionError, KeyError):
            v = None
        if col.special == "dq":
            q = st.quality
            if role == Qt.DisplayRole:
                return f"{t('state.' + q.status)} {q.score}" if q else "—"
            if role == Qt.ForegroundRole:
                return QColor(DQ_COLORS.get(q.status, theme.MUTED)) if q else QColor(theme.MUTED)
            if role == Qt.ToolTipRole and q:
                from alerts.report import issue_text
                return "\n".join(f"[{i.severity}] {issue_text(i)}" for i in q.issues)
            if role == SORT_ROLE:
                return ({VALID: 2, PARTIAL: 1}.get(q.status, 0) * 1000 + q.score) if q else -1
        if col.special == "filters":
            if role == Qt.DisplayRole:
                return t("lbl.pass") if v == 0 else t("lbl.fails", n=v)
            if role == Qt.ForegroundRole:
                return QColor(theme.ACCENT if v == 0 else theme.MUTED)
            if role == Qt.ToolTipRole:
                return "\n".join(filter_text(k) for k in st.filter_fails)
        if role == Qt.DisplayRole:
            if v is None and col.key == "opp" and st.quality and st.quality.status == INVALID:
                return "—"
            return col.fmt(v) if v is not None else ""
        if role == SORT_ROLE:
            if v is None:
                return float("-inf")
            return v if not isinstance(v, str) else v.lower()
        if role == Qt.ForegroundRole:
            if st.quality and st.quality.status == INVALID:
                return QColor(theme.MUTED)
            if col.color and v is not None:
                return col.color(v)
        if role == Qt.BackgroundRole and st.risk and st.risk.score > 80:
            return QColor(60, 20, 24)
        if role == Qt.ToolTipRole and index.column() == 0:
            return f"{st.info.name}\n{st.mint}"
        if role == Qt.TextAlignmentRole and index.column() > 0:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class _Proxy(QSortFilterProxyModel):
    def lessThan(self, left, right):
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class TokenTable(QTableView):
    selected = Signal(object)  # TokenState

    def __init__(self, cols: list[Col], sort_col: int = 0, desc: bool = True):
        super().__init__()
        self.model_ = TokenTableModel(cols)
        self.proxy = _Proxy()
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.sortByColumn(sort_col, Qt.DescendingOrder if desc else Qt.AscendingOrder)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(24)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.clicked.connect(self._emit)
        self.activated.connect(self._emit)
        self._selected_mint: str | None = None
        self._sized = False

    def _emit(self, proxy_index):
        row = self.proxy.mapToSource(proxy_index).row()
        if 0 <= row < len(self.model_.rows):
            st = self.model_.rows[row]
            self._selected_mint = st.mint
            self.selected.emit(st)

    def set_rows(self, rows: list[TokenState]) -> None:
        self.model_.set_rows(rows)
        if rows and not self._sized:
            self.resizeColumnsToContents()
            self._sized = True
        if self._selected_mint:
            for r, st in enumerate(self.model_.rows):
                if st.mint == self._selected_mint:
                    self.selectRow(self.proxy.mapFromSource(self.model_.index(r, 0)).row())
                    break
