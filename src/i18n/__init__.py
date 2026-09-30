"""Translations. All user-facing text comes from vi.json / en.json — never hard-coded in components.

  t("tab.new_coins")                 -> "COIN MỚI" / "NEW COINS"
  t("risk.top10_high.detail", value=58.2)
Missing key -> English -> the key itself (tests assert every used key exists in both files).
"""
from __future__ import annotations

import json
import os
import sys

LANGS = ("vi", "en")
_current = "vi"
_cache: dict[str, dict[str, str]] = {}


def _dir() -> str:
    base = getattr(sys, "_MEIPASS", None)   # PyInstaller one-file bundle
    return os.path.join(base, "i18n") if base else os.path.dirname(os.path.abspath(__file__))


def load(lang: str) -> dict[str, str]:
    if lang not in _cache:
        with open(os.path.join(_dir(), f"{lang}.json"), encoding="utf-8") as f:
            _cache[lang] = json.load(f)
    return _cache[lang]


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGS else "vi"


def get_language() -> str:
    return _current


def has(key: str, lang: str | None = None) -> bool:
    return key in load(lang or _current)


def t(key: str, lang: str | None = None, **params) -> str:
    text = load(lang or _current).get(key) or load("en").get(key) or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, ValueError, IndexError, TypeError):
            return text
    return text


def t_or(key: str, fallback: str, lang: str | None = None, **params) -> str:
    return t(key, lang, **params) if has(key, lang) or has(key, "en") else fallback
