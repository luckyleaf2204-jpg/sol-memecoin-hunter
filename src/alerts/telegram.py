"""Telegram Bot API alerts (official API: https://core.telegram.org/bots/api#sendmessage).

Needs TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_CHAT_ID in .env.
"""
from __future__ import annotations

from core.http import HttpClient

SOURCE = "telegram_bot"


class TelegramAlerter:
    def __init__(self, http: HttpClient, bot_token: str, chat_id: str):
        self.http = http
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        data = await self.http.post_json(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            source=SOURCE, retries=1)
        return bool(isinstance(data, dict) and data.get("ok"))
