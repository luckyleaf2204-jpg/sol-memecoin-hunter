"""Part 3 — is it profitable? Per-token (cluster) bootstrap CI, an explicit "not enough data" conclusion, the entry
location in every journal row, blocked vs entered vs baseline forward returns per epoch, the review bundle."""
import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from replay_fixtures import make_db
from test_durable_sample import epoch, row
from test_v12 import opened
from trading import review_bundle as RB
from trading.sample_report import NOT_ENOUGH, cluster_bootstrap_ci, conclusion, report, summary_line

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 3.1 cluster CI
def test_cluster_ci_is_wider_when_trades_of_a_token_are_correlated():
    rows, nets = [], []
    for t in range(10):                                   # 10 tokens x 10 trades, same result inside a token
        for _ in range(10):
            rows.append({"mint": f"T{t}"})
            nets.append(8.0 if t % 2 else -6.0)
    from trading.sample_report import bootstrap_ci
    plain, cluster = bootstrap_ci(nets), cluster_bootstrap_ci(rows, nets)
    assert cluster[1] - cluster[0] > 2 * (plain[1] - plain[0])
    assert cluster_bootstrap_ci([{"mint": "A"}] * 5, [1.0] * 5) is None            # one token: no CI


def test_report_has_cluster_ci_and_token_count():
    e = epoch()
    r = report([row(e, 12.0 if i % 2 else -2.0, ts=2000 + i) for i in range(40)], e)
    m = r["by_cost"]["5%"]
    assert m["ci95_cluster_pct"] is not None and m["n_tokens"] >= 1
    assert {"win_rate_pct", "rr_realised", "max_drawdown"} <= set(m)
    assert "by_cost" in r["sl_gap_scenario"] and "without_haircut_trades" in r["haircut"]


# ---------------------------------------------------------------- 3.2 the conclusion
def _bc(ci, cc, n=250):
    return {k: {"n": n, "ci95_pct": ci, "ci95_cluster_pct": cc} for k in ("5%", "7%", "10%")}


@pytest.mark.parametrize("n, ci, cc, text, prof", [
    (150, [1.0, 3.0], [0.5, 4.0], NOT_ENOUGH, None),                  # n < 200
    (250, [-1.0, 3.0], [-2.0, 4.0], NOT_ENOUGH, None),                # CI includes 0
    (250, [1.0, 3.0], [-0.5, 4.0], NOT_ENOUGH, None),                 # only the per-token CI includes 0
    (250, [1.0, 3.0], [0.5, 4.0], "Có lãi", True),
    (250, [-3.0, -1.0], [-4.0, -0.5], "THUA LỖ", False),
])
def test_conclusion(n, ci, cc, text, prof):
    c = conclusion(n, _bc(ci, cc, n))
    assert c["text"].startswith(text) and c["profitable"] is prof


def test_summary_line_says_not_enough_data():
    e = epoch()
    line = summary_line(report([row(e, 12.0 if i % 2 else -2.0, ts=2000 + i) for i in range(40)], e))
    assert line.startswith("SAMPLE PRELIMINARY n=40 · " + NOT_ENOUGH)


# ---------------------------------------------------------------- 3.3 every trade row
def test_journal_row_has_location_cost_engine_sample_epoch():
    b, st, p = opened()
    b.decisions.setdefault(st.mint, {}).update({"entry_location": "PULLBACK", "entry_extension": 12.5})
    b._tag_position(st.mint, {"lifecycle": "NEW", "setup_type": "NEW"})
    st.stamps["market"].updated_at = 2e9
    st.market.price_usd = p.entry_price * 0.8
    b.jupiter = None
    b.tick()
    j = b.book.journal[-1]
    for k in ("entry_price", "mfe_pct", "mae_pct", "exit_reason", "real_cost_pct", "fees_usd", "engine", "sample_id",
              "epoch", "setup_type", "location", "extension_5m_pct"):
        assert k in j, k
    assert j["location"] == "PULLBACK" and j["extension_5m_pct"] == 12.5


# ---------------------------------------------------------------- 3.1 blocked vs entered vs baseline, this epoch
def test_gate_forward_returns_only_this_epoch(tmp_path):
    from research.gate_eval import evaluate
    p = tmp_path / "r.db"
    make_db(p, ["tp30"] * 3, ["sl15"] * 6, blocked_hits=["sl15"] * 2)
    db = sqlite3.connect(p)
    db.execute("INSERT INTO gate_events (ca, ts, kind, reasons, age_s, liquidity_usd, price, lifecycle) "
               "VALUES ('OLD', 10, 'blocked', '[]', 100, 7000, 1, 'NEW')")
    db.commit()
    db.close()
    assert evaluate(str(p))["n_blocked"] == 3 and evaluate(str(p), since=5_000)["n_blocked"] == 2
    b, st, _ = opened()

    class Rec:
        path = p
    b.recorder = Rec()
    b.sample_epoch.started_at = 5_000.0
    res = asyncio.run(b.refresh_gate_eval())
    assert res["n_entered"] == 3 and "by_horizon" in res
    assert b.sample_report()["gate_forward_returns"]["n_blocked"] == 2


# ---------------------------------------------------------------- 3.4 the review bundle
def test_bundle_markdown_has_everything_and_no_secret(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_TOKEN", "sekret-token-value-123")
    e = epoch()
    journal = [row(e, 12.0 if i % 2 else -2.0, ts=2000 + i) for i in range(60)]
    rep = report(journal, e)
    rep["summary_line"] = summary_line(rep)
    md = RB.to_markdown(RB.build(journal, rep, {"store": "http", "target": "https://h/x"}))
    for s in ("Kết luận", NOT_ENOUGH, "per token", "Haircut", "Fixed fees", "Entry gate", "blocked vs entered",
              "Last 50 trades", "Durability"):
        assert s in md, s
    assert md.count("\n| ") >= 50
    with pytest.raises(ValueError):
        RB.to_markdown(RB.build(journal, rep, {"leak": "sekret-token-value-123"}))
    with pytest.raises(ValueError):
        RB.check_no_secrets("https://x/?api-key=abc")


def test_review_bundle_endpoint_needs_the_code():
    import web.app as webapp
    b, _, _ = opened()
    app = webapp.create_app(engine=b.engine, start_scanner=False, access_code="c0de", bot=b)
    with TestClient(app) as c:
        assert c.get("/api/review_bundle").status_code in (401, 403)
        d = c.get("/api/review_bundle", headers={"X-Access-Code": "c0de"}).json()
        assert d["paper_only"] and d["conclusion"]["text"] == NOT_ENOUGH and "snapshot" in d
        assert "c0de" not in json.dumps(d)


def test_export_tool_offline(tmp_path):
    out = tmp_path / "b.md"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "export_review_bundle.py"), "--data", str(tmp_path),
                        "--out", str(out)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "NONE found" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- B10: wider credential check
@pytest.mark.parametrize("text", [
    "https://h/x?token=abcd1234efgh", "key=abcdef123456", "secret: hunter2hunter2", "client_secret=xyzxyzxyz",
    "...&sig=0a1b2c3d4e5f", "X-Amz-Signature=deadbeefcafe", "X-Amz-Credential=AKIAEXAMPLE/2026",
    "SNAPSHOT_TOKEN=s3cr3t-v4lue", "snapshot_url: https://worker.example/hunter", "Authorization: Bearer abcdefghijk",
    "Bearer abcdefghijklmnop", "api-key=0123456789abcdef",
    "5" + "K" * 87,                                                      # a base58 private key shape
])
def test_credential_like_strings_are_refused(text):
    with pytest.raises(ValueError):
        RB.check_no_secrets(f"some text {text} more")


@pytest.mark.parametrize("text", [
    "api-key=REDACTED", "token=***", "key=<your key>", "secret=REDACTED", "SNAPSHOT_TOKEN=xxxx",
    "Bearer REDACTED", "So11111111111111111111111111111111111111112",   # a mint (44 base58) is fine
    "unique tokens with a TRADE decision", "keys: 5",
])
def test_redacted_or_harmless_strings_pass(text):
    RB.check_no_secrets(f"some text {text} more")


def test_env_values_are_refused_and_the_store_target_is_not_in_the_bundle(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_URL", "https://worker.example.dev/hunter")
    e = epoch()
    rep = report([], e)
    b = RB.build([], rep, {"store": "http", "target": "https://worker.example.dev/hunter", "durable": True})
    assert b["snapshot"]["target"] == "configured"
    RB.to_markdown(b)                                                    # passes: the URL is not in it
    with pytest.raises(ValueError, match="SNAPSHOT_URL"):
        RB.check_no_secrets("store at https://worker.example.dev/hunter")


def test_endpoint_refuses_a_bundle_with_a_credential(monkeypatch):
    import web.app as webapp
    b, _, _ = opened()
    b.book.journal.append({"trade_id": "t", "symbol": "token=abcd1234efgh"})       # something leaked into data
    app = webapp.create_app(engine=b.engine, start_scanner=False, access_code="c0de", bot=b)
    with TestClient(app) as c:
        r = c.get("/api/review_bundle", headers={"X-Access-Code": "c0de"})
        assert r.status_code == 500 and "refused" in r.json()["error"] and "abcd1234efgh" not in r.text


# ---------------------------------------------------------------- C6: JSON pairs, user:pass URLs, long hex
@pytest.mark.parametrize("text", [
    '{"api_key": "abc123xyz"}', '{"token": "eyJhbGciOiJIUzI1NiJ9abc123"}', '{"secret": "hunter22"}',
    '{"SNAPSHOT_TOKEN": "t0ken-value"}', '{"password": "pa55word"}', '{"Authorization": "Bearer abcdefghij"}',
    "https://user:pa55@store.example/hunter", "postgres://admin:s3cret@db:5432/x",
    "sha " + "a1b2c3d4" * 8,                                            # 64 hex chars
])
def test_json_pairs_userpass_urls_and_long_hex_are_refused(text):
    with pytest.raises(ValueError):
        RB.check_no_secrets(text)


@pytest.mark.parametrize("text", [
    '{"api_key": "REDACTED"}', '{"token": "***"}', '{"SNAPSHOT_TOKEN": "<set in Render>"}',
    "KEY:USDT", "pair KEY:SOL", "commit 3f2a1b4c5d6e7f8091a2b3c4d5e6f708192a3b4c",   # a 40-char git SHA
    "https://store.example/hunter", '{"tokens": 5}', "sig: n/a",
])
def test_json_and_text_false_positives_pass(text):
    RB.check_no_secrets(text)


def test_check_obj_walks_nested_json():
    RB.check_obj({"a": [{"b": {"api_key": "REDACTED", "tokens": 3, "token": None}}]})
    with pytest.raises(ValueError, match="a.b.client_secret"):
        RB.check_obj({"a": {"b": {"client_secret": "zzz999"}}})
    with pytest.raises(ValueError):
        RB.check_obj([{"Authorization": "Basic dXNlcjpwYXNz"}])


def test_endpoint_refuses_a_json_secret_value(monkeypatch):
    import web.app as webapp
    b, _, _ = opened()
    b.book.journal.append({"trade_id": "t", "symbol": "x"})
    app = webapp.create_app(engine=b.engine, start_scanner=False, access_code="c0de", bot=b)
    import trading.review_bundle as RBm
    orig = RBm._safe_snapshot
    monkeypatch.setattr(RBm, "_safe_snapshot", lambda sn: {**orig(sn), "password": "pa55word99"})
    with TestClient(app) as c:
        r = c.get("/api/review_bundle", headers={"X-Access-Code": "c0de"})
        assert r.status_code == 500 and "pa55word99" not in r.text
