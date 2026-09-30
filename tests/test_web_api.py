"""Web server / PWA: access control (fail-closed), no secrets in responses, no trading, PWA assets."""
import json
import struct

import pytest
from fastapi.testclient import TestClient

from conftest import build_state, dex_pair
from core.config import ApiKeys, Settings
from database.db import Database
from scanner.engine import ScannerEngine
from web.app import STATIC, create_app

CODE = "test-code-123"
SECRET_HELIUS = "HELIUS-SECRET-7c0e1a2b-9f11-4d7e-8a31-000000000000"
SECRET_TG = "123456:TELEGRAM-SECRET-TOKEN-abcdefghijklmnop"
GOOD = "GoodMint1111111111111111111111111111111111"
BAD = "BadMint22222222222222222222222222222222222"


@pytest.fixture
def env(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "w.db"),
                        keys=ApiKeys(helius=SECRET_HELIUS, telegram_bot_token=SECRET_TG, telegram_chat_id="1"),
                        on_log=lambda m: None)
    good = build_state(dex_pair(mint=GOOD))
    bad = build_state(dex_pair(mint=BAD, liq=7.1e-07))
    good.info.name = '<img src=x onerror=alert(1)>'       # hostile token name must stay plain data
    eng.tracked = {GOOD: good, BAD: bad}
    eng.published = [good, bad]
    eng.helius_state = {"state": "CONNECTED", "endpoint": "https://mainnet.helius-rpc.com/", "call": "getTokenAccounts",
                        "status": 200, "ms": 120}
    app = create_app(engine=eng, start_scanner=False, access_code=CODE)
    with TestClient(app) as client:
        yield client, eng, app


H = {"X-Access-Code": CODE}


def test_healthz_public_and_minimal(env):
    c, _, _ = env
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_api_requires_code(env):
    c, _, _ = env
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Access-Code": "wrong"}).status_code == 401
    assert c.get("/api/status", headers=H).status_code == 200


def test_fail_closed_without_configured_code(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "x.db"), keys=ApiKeys(), on_log=lambda m: None)
    with TestClient(create_app(engine=eng, start_scanner=False, access_code="")) as c:
        r = c.get("/api/status", headers={"X-Access-Code": ""})
        assert r.status_code == 503 and r.json()["error"] == "access_code_not_configured"


def test_bruteforce_limited(env):
    c, _, _ = env
    for _ in range(10):
        c.get("/api/auth", headers={"X-Access-Code": "nope"})
    assert c.get("/api/auth", headers=H).status_code == 429


def test_no_secret_in_any_response(env):
    c, _, _ = env
    urls = ["/healthz", "/api/auth", "/api/status", "/api/narratives", "/api/events", f"/api/token/{GOOD}",
            f"/api/token/{BAD}", f"/api/token/{GOOD}/history", f"/api/watch?mints={GOOD}", "/", "/sw.js",
            "/manifest.webmanifest", "/i18n/vi.json", "/static/app.js"] + [f"/api/list/{k}" for k in
                                                                            ("top", "new", "early", "whales", "dev", "social")]
    for u in urls:
        body = c.get(u, headers=H).text
        assert SECRET_HELIUS not in body, u
        assert SECRET_TG not in body, u
        assert "api-key=" not in body, u


def test_no_trading_or_wallet_endpoints(env):
    _, _, app = env
    paths = " ".join(getattr(r, "path", "") for r in app.routes).lower()
    for word in ("buy", "sell", "swap", "trade", "sign", "wallet", "transfer", "private", "seed", "withdraw", "order"):
        assert word not in paths, word


def test_top_only_valid_and_card_fields(env):
    c, _, _ = env
    items = c.get("/api/list/top", headers=H).json()["items"]
    assert [x["mint"] for x in items] == [GOOD]
    card = items[0]
    assert card["dq"] == "VALID" and card["dq_label"] and "early_groups" in card
    new = c.get("/api/list/new", headers=H).json()["items"]
    bad = next(x for x in new if x["mint"] == BAD)
    assert bad["dq"] == "INVALID" and bad["opp"] is None


def test_token_view_localized(env):
    c, _, _ = env
    v = c.get(f"/api/token/{GOOD}", headers=H).json()
    assert v["card"]["name"] == '<img src=x onerror=alert(1)>'   # raw data; the client escapes it
    titles = {s["key"]: s["title"] for s in v["sections"]}
    assert titles["market"] == "Thị trường" and titles["holders"] == "Holder"
    assert v["early"] is not None and "groups" in v["early"]
    assert v["links"]["solscan"].endswith(GOOD)
    assert c.get("/api/token/xyz", headers=H).status_code == 400
    assert c.get("/api/token/" + "1" * 40, headers=H).status_code == 404


def test_watch_sync_validates(env, monkeypatch):
    c, eng, _ = env
    added = []

    async def fake_add(m):
        added.append(m)
    monkeypatch.setattr(eng, "add_watch", fake_add)
    new = "NewMint3333333333333333333333333333333333"
    r = c.post("/api/watch", headers=H, json={"mints": [new, "bad!!", GOOD]}).json()
    assert new in r["accepted"] and "bad!!" in r["rejected"]
    assert c.post("/api/watch", headers=H, json={"x": 1}).status_code == 400
    items = c.get(f"/api/watch?mints={GOOD},{new}", headers=H).json()["items"]
    assert items[0]["mint"] == GOOD and items[1] == {"mint": new, "pending": True}


def test_refresh_rate_limited(env, monkeypatch):
    c, eng, _ = env
    calls = []

    async def fake_analyze(m):
        calls.append(m)
        return eng.tracked[GOOD]
    monkeypatch.setattr(eng, "analyze_one", fake_analyze)
    assert c.post(f"/api/token/{GOOD}/refresh", headers=H).status_code == 200
    assert c.post(f"/api/token/{GOOD}/refresh", headers=H).status_code == 429
    assert calls == [GOOD]


def test_security_headers(env):
    c, _, _ = env
    r = c.get("/", headers=H)
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/api/status", headers=H).headers["cache-control"] == "no-store"


def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_pwa_assets(env):
    c, _, _ = env
    html = c.get("/").text
    for needle in ('rel="manifest"', 'rel="apple-touch-icon"', "apple-mobile-web-app-capable", "viewport-fit=cover"):
        assert needle in html, needle
    assert "<script>" not in html and "style=" not in html          # CSP: no inline script/style
    m = c.get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    man = json.loads(m.text)
    assert man["display"] == "standalone" and man["start_url"].startswith("/")
    sizes = {i["sizes"] for i in man["icons"]}
    assert {"192x192", "512x512"} <= sizes
    sw = c.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript") and '"/api/"' in sw.text
    assert _png_size(STATIC / "apple-touch-icon.png") == (180, 180)
    assert _png_size(STATIC / "icons" / "icon-192.png") == (192, 192)
    assert _png_size(STATIC / "icons" / "icon-512.png") == (512, 512)
    assert c.get("/apple-touch-icon.png").status_code == 200


def test_frontend_escapes_and_has_no_secrets():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function esc(" in js and "${esc(c.name)}" in js and "${esc(c.symbol)}" in js
    for word in ("HELIUS_API_KEY", "helius-rpc.com", "api-key", "privateKey", "seed"):
        assert word not in js, word


def test_every_key_used_by_the_pwa_exists():
    import re
    from i18n import load
    js = (STATIC / "app.js").read_text(encoding="utf-8") + (STATIC / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'''t\(\s*["']([a-z_]+\.[A-Za-z0-9_.]+)["']''', js)) | set(re.findall(r'data-i18n="([^"]+)"', js))
    keys = {k for k in keys if not k.endswith(".")}           # prefixes built at runtime -> enumerated below
    kinds = ("top", "new", "early", "whales", "dev", "social")
    keys |= {f"web.title.{k}" for k in kinds + ("watch", "status")} | {f"web.empty.{k}" for k in kinds + ("watch",)}
    keys |= {f"web.sort.{k}" for k in ("opp", "early", "risk_low", "mc", "vol5m", "age", "holders", "confirm", "newest",
                                       "mc_low", "mc_rise", "vol_rise", "buy", "holder_rise", "liq", "dev_hist")}
    groups = ("opportunity", "watch", "nodata", "excluded")
    keys |= {f"web.{p}.{g}" for p in ("group", "group_rule", "group_empty") for g in groups} | {"web.title.home"}
    keys |= {f"web.rf.{k}" for k in ("discovery", "market", "holders", "dev", "persist")}
    keys |= {f"web.filter.{k}" for k in ("valid", "lowRisk", "pass")}
    for lang in ("vi", "en"):
        missing = sorted(k for k in keys if k not in load(lang))
        assert not missing, (lang, missing)


def test_static_assets_always_revalidated(env):
    c, _, _ = env
    for u in ("/static/app.js", "/static/styles.css", "/i18n/vi.json", "/sw.js", "/"):
        assert c.get(u).headers.get("cache-control") == "no-cache", u
