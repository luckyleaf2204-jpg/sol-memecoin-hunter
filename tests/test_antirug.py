"""Anti-rug research: features are point-in-time, missing data is UNKNOWN (never False), the shadow score never
changes a decision, labels use only later prices, forensics / latency are persisted, the report runs."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from core.models import HolderIntel, RiskFactor, RiskResult
from research.antirug import BINARY, features, shadow_score
from research.dataset import DatasetRecorder
from test_bot_v2 import bot
from test_experimental import hot
from test_jupiter_exec import ScriptedJupiter
from trading import jupiter as J

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from antirug_report import label  # noqa: E402


def test_missing_data_is_unknown_not_false():
    st = hot()                                         # no holder data
    f = features(st)
    assert f["holders_ok"] is False and f["top10_pct"] is None and f["bundle_slot_concentration"] is None
    assert BINARY["holders:top10>35%"](f) is None       # unknown, not False
    assert BINARY["holders:data_missing"](f) is True
    assert f["lp_events"] is None and f["creator_wallet_age"] is None


def test_features_reflect_the_state_at_that_moment():
    st = hot(holders=120)
    st.holders.top10_pct, st.holders.max_single_pct = 62.0, 18.0
    st.holder_intel = HolderIntel(growth_5m_pct=-2.0)
    st.market.price_change_5m = 80.0
    st.risk = RiskResult(20, "LOW", [RiskFactor("dev_snipe", 10, "dev"), RiskFactor("sudden_spike", 10, "manipulation")])
    f = features(st)
    assert BINARY["holders:top10>50%"](f) and BINARY["holders:top1>10%"](f) and f["price_up_holders_flat"]
    assert BINARY["riskfactor:dev_snipe"](f) and f["dev_snipe"] is True
    sc, why = shadow_score(f)
    assert 0 <= sc <= 100 and any("top10 > 50%" in w for w in why) and f["shadow_antirug_score"] == sc


def test_shadow_score_bounds_and_missing_data_weight():
    lo, _ = shadow_score({"holders_ok": True, "dev_verified": True})
    hi, _ = shadow_score({"holders_ok": False, "dev_verified": False, "top10_pct": 90, "top1_pct": 40, "dev_pct": 30,
                          "dev_status": "SOLD ALL", "dev_snipe": True, "suspicious_holders": True,
                          "amm_equivalent_usd": 5000, "liq_change_5m_pct": -50, "price_up_holders_flat": True,
                          "buy_share_5m": 0.2, "tx_5m": 100, "risk_factors": ["sudden_spike", "wash_pattern"]})
    assert lo == 0 and hi == 100


def test_label_has_no_lookahead():
    path = [(0, 1.0), (10, 0.1), (100, 1.0), (700, 1.0)]
    assert label(path, 10, 0.1)[0] == "NON_RUG"         # anchored at the bottom: the crash is in the past
    assert label(path, 0, 1.0)[0] == "RUG" and label(path, 0, 1.0)[1] == 10
    assert label([(0, 1.0), (60, 0.9)], 0, 1.0)[0] == "UNKNOWN"   # < 10 min observed, no crash


def test_research_logging_never_changes_decisions(tmp_path):
    def world():
        out = []
        for i, age in enumerate((40, 150, 900)):
            st = hot(age_s=age, holders=60 if i else None)
            st.info.mint = f"Ar{i}" + "1" * 41
            out.append(st)
        return out
    a, b = bot(world(), ScriptedJupiter([J.OK])), bot(world(), ScriptedJupiter([J.OK]))
    for x in (a, b):
        x.cfg.experimental = True
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    for k in range(3):
        for x in (a, b):
            x.tick(t0 + 5 * k)
            asyncio.run(x.execute_intents(t0 + 5 * k))
    strip = lambda d: {m: (r["decision"], r["opportunity"], r.get("blocked_by")) for m, r in d.items()}  # noqa: E731
    assert strip(a.decisions) == strip(b.decisions) and set(a.book.positions) == set(b.book.positions)
    rows = b.recorder.db.execute("SELECT antirug, shadow_antirug_score FROM token_snapshots").fetchall()
    assert rows and all(json.loads(r[0])["shadow_antirug_score"] == r[1] for r in rows if r[0])


def test_forensics_and_latency_persisted_and_report_runs(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep

    async def no_sleep(*_a, **_k):
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental, b.cfg.latency_probe = True, True
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    for k in (5, 10, 15, 30, 60, 65):
        b.tick(t0 + k)
    db = b.recorder.db
    assert db.execute("SELECT COUNT(*) FROM buy_forensics").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM latency_samples").fetchone()[0] == 1
    lat = json.loads(db.execute("SELECT data FROM latency_samples").fetchone()[0])
    for k in ("age_s", "liquidity_usd", "is_curve", "route", "impact_pct", "quote_size_usd", "hour_utc", "latency_s"):
        assert k in lat, k
    b.recorder.close()
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "antirug_report.py"), "--db", str(tmp_path / "r.db"),
                          "--out", str(tmp_path / "rep.json")], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr[-800:]
    rep = json.loads((tmp_path / "rep.json").read_text(encoding="utf-8"))
    for k in ("dataset", "CANDIDATE", "BUY", "POP_2M", "POP_5M", "missing_data", "data_refresh", "latency",
              "liquidity_ab", "recommendation", "candidate_table"):
        assert k in rep, k
    assert rep["dataset"]["buys_with_forensics"] == 1 and rep["CANDIDATE"]["shadow"]["sample"] == "INSUFFICIENT SAMPLE"
    assert rep["recommendation"].startswith("NO PRODUCTION CHANGE")
