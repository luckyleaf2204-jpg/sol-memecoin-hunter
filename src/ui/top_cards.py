"""CƠ HỘI NỔI BẬT / TOP OPPORTUNITIES strip — VALID data only, ranked by Opportunity."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from alerts.report import dev_label, liquidity_label, usd
from core.models import TokenState
from i18n import t
from scoring.ranking import rank_opportunities
from ui import theme

N_CARDS = 4
ROWS = ("mc", "liquidity", "vol_5m", "buy_sell", "holder_growth", "dev", "early", "lifecycle", "smart_money",
        "social", "opportunity", "risk", "data_quality")


class Card(QFrame):
    clicked = Signal(object)

    def __init__(self, rank: int):
        super().__init__()
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(250)
        self.state: TokenState | None = None
        self.rank = rank
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        top = QHBoxLayout()
        self.title = QLabel(f"#{rank} —")
        self.title.setStyleSheet("font-size:13pt;font-weight:800")
        self.score = QLabel("")
        self.score.setStyleSheet("font-size:13pt;font-weight:800")
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.score)
        lay.addLayout(top)
        grid = QGridLayout()
        grid.setVerticalSpacing(0)
        self.vals: dict[str, QLabel] = {}
        for i, k in enumerate(ROWS):
            name = QLabel(t(f"card.{k}"))
            name.setObjectName("muted")
            val = QLabel("—")
            val.setAlignment(Qt.AlignRight)
            grid.addWidget(name, i, 0)
            grid.addWidget(val, i, 1)
            self.vals[k] = val
        lay.addLayout(grid)

    def mousePressEvent(self, e):
        if self.state:
            self.clicked.emit(self.state)

    def _set(self, key: str, text: str, color: str | None = None) -> None:
        self.vals[key].setText(text)
        self.vals[key].setStyleSheet(f"color:{color};font-weight:700" if color else "")

    def set_state(self, st: TokenState | None) -> None:
        self.state = st
        if not st:
            self.title.setText(f"#{self.rank} —")
            self.score.setText("")
            for v in self.vals.values():
                v.setText("—")
            return
        m, i = st.market, st.info
        unk = t("common.unknown")
        self.title.setText(f"#{self.rank} ${i.symbol[:12]}")
        sc = st.score.total
        self.score.setText(str(sc))
        self.score.setStyleSheet(f"font-size:13pt;font-weight:800;color:{theme.score_hex(sc)}")
        self._set("mc", usd(m.market_cap))
        liq = usd(m.liquidity_usd) if m.liquidity_usd is not None else t("common.unknown")
        self._set("liquidity", liq + (" ⓒ" if m.liquidity_source == "pumpfun_curve" else ""))
        self.vals["liquidity"].setToolTip(liquidity_label(st))
        self._set("vol_5m", usd(m.vol_5m))
        bs = m.buy_sell_ratio_5m
        self._set("buy_sell", f"{bs:.2f}" if bs is not None else unk)
        g = st.holder_growth_pct
        self._set("holder_growth", f"{g:+.1f}%" if g is not None else unk)
        d = st.dev
        self._set("dev", f"{d.current_pct:.1f}% {t('devstatus.' + d.status)} ✓" if d and d.balance_verified
                  else t("common.unknown"))
        self.vals["dev"].setToolTip(dev_label(st))
        e = st.early
        self._set("early", (f"{e.strength}" + (" ⚡" if e.is_early else "")) if e and e.strength is not None else unk,
                  theme.ACCENT if e and e.is_early else None)
        self._set("lifecycle", t(f"lifecycle.{st.lifecycle}"))
        self._set("smart_money", t("common.not_available"), theme.MUTED)
        self._set("social", t("common.not_available"), theme.MUTED)
        self._set("opportunity", str(sc), theme.score_hex(sc))
        self._set("risk", f"{st.risk.score} {t('state.' + st.risk.level)}", theme.risk_hex(st.risk.score))
        self._set("data_quality", f"{st.quality.score} {t('state.' + st.quality.status)}", theme.GREEN)


class TopCards(QWidget):
    selected = Signal(object)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.cards = [Card(i + 1) for i in range(N_CARDS)]
        for c in self.cards:
            c.clicked.connect(self.selected.emit)
            lay.addWidget(c)

    def set_states(self, states: list[TokenState]) -> None:
        ranked = rank_opportunities(states)
        for i, c in enumerate(self.cards):
            c.set_state(ranked[i] if i < len(ranked) else None)
