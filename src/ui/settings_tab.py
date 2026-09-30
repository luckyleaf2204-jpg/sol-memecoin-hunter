"""CÀI ĐẶT / SETTINGS: language, filters, scanner timing, alerts, local API, API keys, source health."""
from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from core.config import APP_DIR, ApiKeys, Settings
from i18n import t
from ui import theme

FIELDS = [
    ("scanner", [
        ("scan_interval_sec", 5, 300, 1), ("max_age_hours", 0.1, 72, 0.5), ("max_tracked", 50, 5000, 50),
        ("deep_per_cycle", 1, 30, 1), ("tracking_min_mc", 0, 1e6, 1000), ("snapshot_min_mc", 0, 1e7, 1000),
    ]),
    ("filters", [
        ("min_mc", 0, 1e9, 5000), ("max_mc", 0, 1e10, 50000), ("min_liquidity", 0, 1e9, 5000),
        ("min_volume_5m", 0, 1e9, 5000), ("min_txns_5m", 0, 100000, 10), ("min_holders", 0, 1000000, 50),
        ("min_buy_sell_ratio", 0, 20, 0.05), ("max_top10_pct", 0, 100, 1),
    ]),
    ("alerts", [("alert_min_score", 0, 100, 1), ("alert_max_risk", 0, 100, 1), ("alert_cooldown_min", 1, 1440, 5)]),
    ("api", [("api_port", 1024, 65535, 1)]),
]
CHECKS = ("use_pumpportal_ws", "alerts_enabled", "alert_require_filters", "api_enabled")


class SettingsTab(QScrollArea):
    language_changed = Signal(str)

    def __init__(self, settings: Settings, health_fn):
        super().__init__()
        self.settings = settings
        self.health_fn = health_fn
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)

        lang_box = QGroupBox(t("settings.language"))
        lh = QHBoxLayout(lang_box)
        self.lang = QComboBox()
        self.lang.addItem("🇻🇳 Tiếng Việt", "vi")
        self.lang.addItem("🇺🇸 English", "en")
        self.lang.setCurrentIndex(0 if settings.language == "vi" else 1)
        self.lang.currentIndexChanged.connect(self._lang)
        lh.addWidget(self.lang)
        lh.addStretch()
        lay.addWidget(lang_box)

        self.inputs = {}
        for group, fields in FIELDS:
            box = QGroupBox(t(f"settings.group.{group}"))
            form = QFormLayout(box)
            for key, lo, hi, step in fields:
                w = QSpinBox() if Settings.field_type(key) is int else QDoubleSpinBox()
                if isinstance(w, QDoubleSpinBox):
                    w.setDecimals(2)
                w.setRange(lo, hi)
                w.setSingleStep(step)
                w.setValue(getattr(settings, key))
                form.addRow(t(f"settings.{key}"), w)
                self.inputs[key] = w
            lay.addWidget(box)
        box = QGroupBox(t("settings.group.options"))
        v = QVBoxLayout(box)
        for key in CHECKS:
            c = QCheckBox(t(f"settings.{key}"))
            c.setChecked(getattr(settings, key))
            v.addWidget(c)
            self.inputs[key] = c
        lay.addWidget(box)

        row = QHBoxLayout()
        save = QPushButton(t("btn.save"))
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        self.saved = QLabel("")
        row.addWidget(save)
        row.addWidget(self.saved, 1)
        lay.addLayout(row)

        keys = QGroupBox(t("settings.keys", path=str(APP_DIR / ".env")))
        kv = QVBoxLayout(keys)
        for name, ok in ApiKeys.from_env().status().items():
            kv.addWidget(QLabel(f"<span style='color:{theme.GREEN if ok else theme.MUTED}'>{'●' if ok else '○'}</span> "
                                f"{name}: {t('settings.key_set') if ok else t('settings.key_missing')}"))
        lay.addWidget(keys)
        hbox = QGroupBox(t("settings.health"))
        hv = QVBoxLayout(hbox)
        self.health_label = QLabel("")
        self.health_label.setWordWrap(True)
        hv.addWidget(self.health_label)
        lay.addWidget(hbox)
        lay.addStretch()

    def _lang(self) -> None:
        lang = self.lang.currentData()
        if lang != self.settings.language:
            self.settings.language = lang
            self.settings.save()
            self.language_changed.emit(lang)

    def save(self) -> None:
        for key, w in self.inputs.items():
            if isinstance(w, QCheckBox):
                setattr(self.settings, key, w.isChecked())
            else:
                setattr(self.settings, key, Settings.field_type(key)(w.value()))
        self.settings.save()
        self.saved.setText(t("settings.saved"))

    def refresh_health(self) -> None:
        lines = []
        for name, s in sorted(self.health_fn().items()):
            color = theme.GREEN if s.ok else theme.RED
            ago = f"{time.time() - s.last_ok:.0f}s" if s.last_ok else "—"
            err = f" — {s.last_error}" if s.last_error else ""
            last = (f" · {s.last_call} {s.last_endpoint} → HTTP {s.last_status if s.last_status is not None else '—'}"
                    f"{f' ({s.last_ms} ms)' if s.last_ms is not None else ''}") if s.last_endpoint else ""
            lines.append(f"<span style='color:{color}'>●</span> <b>{name}</b>: "
                         f"{t('settings.health_row', requests=s.requests, errors=s.errors, ago=ago)}"
                         f"<span style='color:{theme.MUTED}'>{last}{err}</span>")
        self.health_label.setText("<br>".join(lines) or t("settings.waiting"))
