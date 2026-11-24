"""Readers of Telegram posts. Every reader returns posts as dicts:
  {"msg_id": int, "posted_at": datetime(UTC), "text": str, "links": [str], "edited": bool, "views": str|None}

web     https://t.me/s/<username> - Telegram's own public web preview of PUBLIC channels. No account,
        no key. ~20 posts per page; older pages with ?before=<msg_id> (history backfill).
client  Telethon with the owner's own Telegram account (api_id / api_hash from my.telegram.org and a
        one-time login). Only channels the account can already read. Optional: needs `telethon`.
bot     Bot API getUpdates: only channels where the operator's bot is an administrator.
No reader joins channels, bypasses access rules or reads private chats it was not invited to.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html as _html
import logging
import os
import re

import httpx

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class SourceError(Exception):
    pass


def clean_username(raw: str) -> str:
    u = (raw or "").strip()
    u = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/(s/)?", "", u)
    u = u.lstrip("@").split("/")[0].split("?")[0]
    if not USERNAME_RE.match(u):
        raise ValueError(f"invalid Telegram channel username: {raw!r}")
    return u.lower()


# ------------------------------------------------------------------ web preview (t.me/s)
_POST_RE = re.compile(r'<div class="tgme_widget_message_wrap[^"]*">(.*?)(?=<div class="tgme_widget_message_wrap|<div class="tgme_widget_message_centered|\Z)', re.S)


def _strip(h: str) -> str:
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    return _html.unescape(h).strip()


def parse_web_page(page: str, username: str) -> dict:
    """Posts of one t.me/s page, oldest first, plus the channel title."""
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    posts = []
    for block in _POST_RE.findall(page):
        m = re.search(rf'data-post="{re.escape(username)}/(\d+)"', block, re.I)
        if not m:
            continue
        msg_id = int(m.group(1))
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        if not tm:
            continue
        posted = dt.datetime.fromisoformat(tm.group(1).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        text = _strip(tx.group(1)) if tx else ""
        links = re.findall(r'href="(https?://[^"]+)"', tx.group(1)) if tx else []
        links += re.findall(r'<a class="tgme_widget_message_link_preview" href="([^"]+)"', block)
        views = re.search(r'<span class="tgme_widget_message_views">([^<]+)</span>', block)
        meta = re.search(r'<span class="tgme_widget_message_meta">(.*?)</span>\s*</div>', block, re.S)
        edited = bool(re.search(r">\s*edited\s*<", block))
        posts.append({"msg_id": msg_id, "posted_at": posted, "text": text, "links": links, "edited": edited,
                      "views": views.group(1).strip() if views else None, "meta": bool(meta)})
    posts.sort(key=lambda p: p["msg_id"])
    return {"title": _html.unescape(title.group(1)) if title else None, "posts": posts,
            "web_preview": "tgme_channel_info" in page or bool(posts)}


class WebSource:
    """Polite reader of t.me/s pages: 1 request at a time, pause between requests, backoff on errors."""

    def __init__(self, pause_s: float = 2.0, timeout: float = 20.0):
        self.pause_s = pause_s
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def page(self, username: str, before: int | None = None) -> dict:
        url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
        async with self._lock:
            last = None
            for attempt in range(4):
                try:
                    r = await self._client.get(url)
                    await asyncio.sleep(self.pause_s)
                    if r.status_code == 429:
                        last = SourceError("rate limited by t.me")
                        await asyncio.sleep(min(120, 10 * 2 ** attempt))
                        continue
                    if r.status_code >= 400:
                        raise SourceError(f"t.me HTTP {r.status_code}")
                    if "/s/" not in str(r.url):          # redirected to the channel card: preview disabled / not a channel
                        raise SourceError("web preview not available (private channel, group or preview disabled)")
                    return parse_web_page(r.text, username)
                except (httpx.TransportError, httpx.TimeoutException) as e:
                    last = SourceError(f"{type(e).__name__}: {e}")
                    await asyncio.sleep(min(60, 3 * 2 ** attempt))
            raise last or SourceError("t.me unreachable")


# ------------------------------------------------------------------ Telethon (optional)
class ClientSource:
    """Read channels with the owner's account. Enabled only when TELEGRAM_API_ID/HASH are set and a
    session file exists (created once with `python -m radar tg-login`)."""

    def __init__(self, session_path: str):
        self.session_path = session_path
        self.client = None

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("TELEGRAM_API_ID") and os.environ.get("TELEGRAM_API_HASH"))

    async def start(self) -> None:
        try:
            from telethon import TelegramClient  # type: ignore
        except ImportError as e:
            raise SourceError("telethon is not installed") from e
        self.client = TelegramClient(self.session_path, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"],
                                     flood_sleep_threshold=60)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise SourceError("Telegram client not logged in: run `python -m radar tg-login` once")

    async def page(self, username: str, before: int | None = None, limit: int = 50) -> dict:
        if self.client is None:
            await self.start()
        ent = await self.client.get_entity(username)
        posts = []
        async for m in self.client.iter_messages(ent, limit=limit, offset_id=before or 0):
            text = m.message or ""
            links = []
            for e in m.entities or []:
                url = getattr(e, "url", None)
                if url:
                    links.append(url)
            posts.append({"msg_id": m.id, "posted_at": m.date.astimezone(dt.timezone.utc), "text": text, "links": links,
                          "edited": m.edit_date is not None, "views": str(m.views) if m.views is not None else None})
        posts.sort(key=lambda p: p["msg_id"])
        return {"title": getattr(ent, "title", None), "posts": posts, "web_preview": True}

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()


async def login(session_path: str) -> None:
    """Interactive one-time login of the owner's account (phone number + code typed by the owner)."""
    from telethon import TelegramClient  # type: ignore
    client = TelegramClient(session_path, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
    await client.start()
    me = await client.get_me()
    print("logged in as", getattr(me, "username", None) or me.id, "- session saved to", session_path)
    await client.disconnect()
