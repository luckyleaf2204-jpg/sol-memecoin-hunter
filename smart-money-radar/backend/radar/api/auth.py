"""Dashboard access control: one password (DASHBOARD_PASSWORD) -> signed session cookie.

* The cookie is an HMAC-SHA256 signed token (SESSION_SECRET), HttpOnly, SameSite=Lax,
  Secure when served over HTTPS. No session state is stored server-side.
* Login attempts are rate limited per client IP (Redis): 10 failures / 15 min.
* If DASHBOARD_PASSWORD is empty the API refuses to start unless ALLOW_NO_AUTH=1 (dev only).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE = "smr_session"


def _secret() -> bytes:
    from ..config import get_config
    s = get_config().session_secret
    if not s:
        s = os.environ.setdefault("_RUNTIME_SESSION_SECRET", secrets.token_hex(32))
    return s.encode()


def make_token(ttl_hours: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + ttl_hours * 3600,
                                                   "n": secrets.token_hex(8)}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        return False
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode()))["exp"] > time.time()
    except Exception:
        return False


def password_ok(given: str) -> bool:
    from ..config import get_config
    want = get_config().dashboard_password
    return bool(want) and hmac.compare_digest(given.encode(), want.encode())


def auth_disabled() -> bool:
    from ..config import get_config
    return not get_config().dashboard_password and os.environ.get("ALLOW_NO_AUTH") == "1"


async def require_auth(request: Request) -> None:
    if auth_disabled():
        return
    if not check_token(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="authentication required")
