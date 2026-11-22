"""Structured logging with secret redaction.

Every log record passes through `RedactingFilter`, which removes:
  * any configured secret value (API keys, bot token, password, session secret),
  * `api-key=` / `apikey=` / `token=` query parameters,
  * Telegram bot tokens inside api.telegram.org URLs.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time

_SECRETS: list[str] = []
_PATTERNS = [
    (re.compile(r"(api[-_]?key=)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(token=)[^&\s\"']+", re.I), r"\1***"),
    (re.compile(r"(bot)\d{6,}:[A-Za-z0-9_-]{20,}"), r"\1***"),
    (re.compile(r"(authorization:\s*bearer\s+)\S+", re.I), r"\1***"),
]


def register_secrets(values: list[str]) -> None:
    for v in values:
        if v and v not in _SECRETS:
            _SECRETS.append(v)


def redact(text: str) -> str:
    if not text:
        return text
    for s in _SECRETS:
        text = text.replace(s, "***")
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_text:
            out["exc"] = record.exc_text
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            out.update({k: v for k, v in extra.items() if k not in out})
        return redact(json.dumps(out, default=str, ensure_ascii=False))


def setup_logging(level: str = "INFO", json_logs: bool = True, secrets: list[str] | None = None) -> None:
    register_secrets(secrets or [])
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.addFilter(RedactingFilter())
    h.setFormatter(JsonFormatter() if json_logs else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
