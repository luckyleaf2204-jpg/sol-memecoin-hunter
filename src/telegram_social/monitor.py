"""Telegram social monitor — PHASE 2 (interface only).

Legal route: the official Telegram MTProto API via Telethon with YOUR OWN account
(TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org), reading only channels/groups
you have joined. Planned metrics: messages/5m, unique senders, member count, CA mention count,
duplicate-message ratio (spam/shill detector) vs unique-sender ratio (organic discussion).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TelegramActivity:
    available: bool = False
    note: str = "Phase 2 — not implemented"


class TelegramMonitor:
    async def activity(self, mint: str, symbol: str) -> TelegramActivity:
        return TelegramActivity()
