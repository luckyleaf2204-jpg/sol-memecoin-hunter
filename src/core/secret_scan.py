"""Credential-like content detector for anything the app hands out for review (core: generic, no trading logic).
check_no_secrets(text) scans text; check_obj(obj) scans JSON key / value pairs. Redacted values pass."""
from __future__ import annotations

import os
import re

SECRET_ENV = ("HELIUS_API_KEY", "SNAPSHOT_TOKEN", "SNAPSHOT_URL", "APP_ACCESS_CODE", "TELEGRAM_BOT_TOKEN",
              "REVIEW_ACCESS_CODE", "SOLANA_RPC_URL")
_REDACTED = r"(?!REDACTED|redacted|\*{3,}|<[^>]*>|x{3,}|X{3,})"
_VAL = r"[^\s&'\"<>,;)]"
_SEP = r"['\"]?\s*[=:]\s*['\"]?"            # key=value, key: value, "key": "value"
SECRET_RES = (
    # names that are always a credential: any real value (REDACTED, ***, <placeholder>, xxx are fine)
    re.compile(r"(?i)\b(api[-_]?key|access[-_]?code|client[-_]?secret|secret|private[-_]?key|password|passwd|"
               r"x-amz-signature|x-amz-credential|x-amz-security-token|snapshot_url|snapshot_token|authorization)"
               + _SEP + _REDACTED + _VAL + r"{3,}"),
    # generic names (token / key / sig): only a long value with a digit ("KEY:USDT" is a pair label, not a key)
    re.compile(r"(?i)\b(token|key|sig|signature)" + _SEP + _REDACTED + r"(?=" + _VAL + r"*\d)" + _VAL + r"{8,}"),
    re.compile(r"(?i)\bbearer\s+" + _REDACTED + r"[a-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)x-access-code"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),        # URL with user:password@host
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{60,}\b"),                        # base58 run longer than a mint (44)
    re.compile(r"(?i)\b[0-9a-f]{48,}\b"),                                 # long hex (longer than a git SHA-1)
)
SECRET_KEYS = {"apikey", "token", "secret", "clientsecret", "snapshottoken", "snapshoturl", "password", "passwd",
               "authorization", "accesscode", "privatekey", "bearer", "xaccesscode"}
_REDACTED_VALUE = re.compile(r"^(REDACTED|redacted|\*{3,}|<[^>]*>|x{3,}|X{3,}|)$")


def check_obj(obj, path: str = "") -> None:
    """JSON-level check: a value under a credential-like key must be empty or redacted."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            norm = re.sub(r"[^a-z]", "", str(k).lower())
            if norm in SECRET_KEYS and isinstance(v, str) and not _REDACTED_VALUE.match(v.strip()):
                raise ValueError(f"review bundle refused: a value under '{path}{k}'")
            check_obj(v, f"{path}{k}.")
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            check_obj(x, path)


def check_no_secrets(text: str) -> None:
    for rx in SECRET_RES:
        if rx.search(text):
            raise ValueError("review bundle refused: it contains a credential-like string")
    for k in SECRET_ENV:
        v = os.environ.get(k, "")
        if len(v) >= 6 and v in text:
            raise ValueError(f"review bundle refused: it contains the value of {k}")
