"""Telegram Bot API (https://core.telegram.org/bots/api): sendMessage + getUpdates long polling.
The bot token comes from TELEGRAM_BOT_TOKEN only and is redacted from logs."""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
from typing import Any

from ..net.http import HttpClient, HttpError

log = logging.getLogger(__name__)
VN = dt.timezone(dt.timedelta(hours=7))


def _url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


async def send_message(http: HttpClient, token: str, chat_id: str, text: str) -> dict:
    return await http.request_json("telegram", "POST", _url(token, "sendMessage"), json_body={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def fmt_sol(x: float | None, d: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{d}f} SOL".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_num(x: float | None, d: int = 0) -> str:
    if x is None:
        return "n/a"
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_price(x: float | None) -> str:
    if not x:
        return "n/a"
    return f"{x:.4g} SOL".replace(".", ",")


def format_alert(sig: dict[str, Any], token: dict[str, Any], sol_usd: float | None, public_url: str | None) -> str:
    """Message layout of PHẦN IX. Numbers in Vietnamese format (1.234,56), time in GMT+7."""
    e = html.escape
    name = token.get("name") or "?"
    sym = token.get("symbol") or "?"
    mint = sig["mint"]
    mc_sol = sig.get("market_cap_sol")
    mc_usd = token.get("market_cap_usd") or ((mc_sol * sol_usd) if (mc_sol and sol_usd) else None)
    liq_sol = sig.get("liquidity_sol")
    liq_usd = token.get("liquidity_usd") or ((liq_sol * sol_usd) if (liq_sol and sol_usd) else None)
    lines = [f"🚨 <b>SMART MONEY ALERT — {e(sig['level'].replace('_', ' '))}</b>", "",
             f"Token: <b>{e(name)}</b>", f"Symbol: {e(sym)}", f"Mint Address: <code>{e(mint)}</code>",
             f"Market Cap: {fmt_sol(mc_sol, 1)}" + (f" (~${fmt_num(mc_usd)})" if mc_usd else ""),
             f"Liquidity: {fmt_sol(liq_sol, 1)}" + (f" (~${fmt_num(liq_usd)})" if liq_usd else ""),
             f"Số ví Smart Money mua: {sig['independent_buyers']} độc lập"
             + (f" ({sig['raw_buyers']} ví)" if sig["raw_buyers"] != sig["independent_buyers"] else ""),
             f"Tổng vốn mua: {fmt_sol(sig['total_buy_sol'])}",
             f"Net Smart Money Flow: {fmt_sol(sig['net_flow_sol'])}", "", "Ví mua:"]
    for i, b in enumerate(sig["buyers"][:8], 1):
        wr = b.get("win_rate")
        lines.append(f"{i}. <code>{e(b['wallet'])}</code> – {fmt_sol(b['position_sol'])} – "
                     f"WR {('n/a' if wr is None else fmt_num(100 * wr, 0) + '%')} – Score {fmt_num(b.get('score'), 0)}")
        if b.get("first_ts"):
            t = dt.datetime.fromtimestamp(int(b["first_ts"]), dt.timezone.utc).astimezone(VN).strftime("%H:%M:%S")
            lines.append(f"   vào {t} · giá {fmt_price(b.get('entry_price_sol'))} · MC {fmt_sol(b.get('entry_mc_sol'), 1)}"
                         + (" · cùng cụm ví khác" if b.get("cluster_id") not in (None, b["wallet"]) else ""))
    det = sig.get("detected_at") or dt.datetime.now(dt.timezone.utc)
    lines += ["", f"Thời gian phát hiện: {det.astimezone(VN).strftime('%d/%m/%Y %H:%M:%S')} (GMT+7)",
              f"Giá tại thời điểm phát hiện: {fmt_price(sig.get('price_at_detection_sol'))}"
              + (f" (tăng {fmt_num(sig['runup_multiple'], 2)}x từ entry SM đầu tiên)" if sig.get("runup_multiple") else ""),
              f"Smart Money Score (TB người mua): {fmt_num(sig.get('avg_buyer_score'), 1)}",
              f"Risk Level: {e(sig.get('risk_level') or 'UNKNOWN')}",
              f"Xác minh on-chain: {e(sig.get('verification') or '')}"]
    if sig.get("downgrade_reasons"):
        lines.append("Ghi chú: " + e("; ".join(sig["downgrade_reasons"])[:400]))
    lines += ["", f"Token Link: https://pump.fun/coin/{mint}", f"DexScreener: https://dexscreener.com/solana/{mint}"]
    if public_url:
        lines.append(f"Dashboard: {public_url.rstrip('/')}/tokens/{mint}")
    lines.append("⚠️ Chỉ là phân tích dữ liệu on-chain, không phải khuyến nghị đầu tư. Hệ thống không tự giao dịch.")
    return "\n".join(lines)


async def poll_commands(http: HttpClient, token: str, allowed_chat: str | None, handler) -> None:
    """Long-poll getUpdates. /start answers with the chat id (needed for setup)."""
    offset = None
    while True:
        try:
            params: dict[str, Any] = {"timeout": 25, "allowed_updates": '["message"]'}
            if offset is not None:
                params["offset"] = offset
            data = await http.request_json("telegram", "GET", _url(token, "getUpdates"), params=params)
            for u in (data or {}).get("result") or []:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id") or "")
                text = (msg.get("text") or "").strip()
                if not chat or not text.startswith("/"):
                    continue
                cmd = text.split()[0].split("@")[0].lower()
                if cmd == "/start":
                    reply = (f"Smart Money Radar bot.\nChat ID của bạn: <code>{chat}</code>\n"
                             "Điền vào TELEGRAM_CHAT_ID hoặc Settings → Telegram.")
                elif allowed_chat and chat == str(allowed_chat):
                    reply = await handler(cmd)
                else:
                    reply = "Chat này chưa được cấu hình để nhận dữ liệu."
                await send_message(http, token, chat, reply)
        except HttpError as e:
            if e.status in (401, 404):
                log.error("telegram token rejected; command polling stopped")
                return
            await asyncio.sleep(5)
        except Exception as e:
            log.warning("telegram polling error: %s", type(e).__name__)
            await asyncio.sleep(5)


def format_exit_alert(sig: dict[str, Any], token: dict[str, Any], prog: dict[str, Any], level: str,
                      public_url: str | None) -> str:
    e = html.escape
    pct = fmt_num(100 * prog["exited_share"], 0)
    lines = [f"⚠️ <b>SMART MONEY EXIT — {pct}% vị thế đã bán</b>", "",
             f"Token: <b>{e(token.get('name') or '?')}</b> ({e(token.get('symbol') or '?')})",
             f"Mint Address: <code>{e(sig['mint'])}</code>",
             f"Tín hiệu gốc: {e(sig['level'].replace('_', ' '))} lúc "
             f"{sig['detected_at'].astimezone(VN).strftime('%d/%m/%Y %H:%M')} (GMT+7)",
             f"Ví đã thoát hoàn toàn: {prog['fully_exited']}/{prog['holders_at_signal']}",
             f"SOL Smart Money đã bán sau tín hiệu: {fmt_sol(prog['sell_sol_after'])}",
             f"Giá hiện tại: {fmt_price(float(token['last_price_sol']) if token.get('last_price_sol') else None)}", ""]
    for w in sorted(prog["wallets"], key=lambda x: -x["exited_share"])[:8]:
        lines.append(f"• <code>{e(w['wallet'])}</code> – đã bán {fmt_num(100 * w['exited_share'], 0)}% – "
                     f"{fmt_sol(w['sell_sol_after'])}")
    lines += ["", f"DexScreener: https://dexscreener.com/solana/{sig['mint']}"]
    if public_url:
        lines.append(f"Dashboard: {public_url.rstrip('/')}/tokens/{sig['mint']}")
    lines.append("Chỉ là dữ liệu on-chain, không phải khuyến nghị. Hệ thống không tự giao dịch.")
    return "\n".join(lines)
