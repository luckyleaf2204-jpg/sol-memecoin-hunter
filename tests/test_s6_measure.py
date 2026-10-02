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
