"""GUI entry point. Language switch rebuilds the window instantly; the scanner keeps running."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.config import DB_PATH, Settings
from database.db import Database
from i18n import set_language
from ui.main_window import MainWindow
from ui.theme import STYLESHEET
from ui.worker import EngineWorker


class App:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setApplicationName("SOL Memecoin Hunter")
        self.qt.setStyleSheet(STYLESHEET)
        self.settings = Settings.load()
        set_language(self.settings.language)
        self.db = Database(DB_PATH)
        self.worker = EngineWorker(self.settings, self.db)
        self.window: MainWindow | None = None
        self.build()
        self.worker.start()

    def build(self) -> None:
        old = self.window
        geo = old.geometry() if old else None
        self.window = MainWindow(self.settings, self.db, self.worker)
        self.window.language_changed.connect(self.switch_language)
        if geo:
            self.window.setGeometry(geo)
        self.window.show()
        if old:
            old._rebuilding = True
            old.detach()
            old.close()
            old.deleteLater()

    def switch_language(self, lang: str) -> None:
        set_language(lang)
        self.build()
        self.window.tabs.setCurrentWidget(self.window.settings_tab)

    def exec(self) -> int:
        return self.qt.exec()


def run_gui() -> None:
    sys.exit(App().exec())
