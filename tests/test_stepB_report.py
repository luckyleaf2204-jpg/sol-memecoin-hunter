"""Step B — decision metric: net expectancy after 5 / 7 / 10 % round-trip cost with a 95 % bootstrap CI, win rate,
realised R:R, max drawdown, stop-gap -20 % scenario; TP/(TP+SL) reference only."""
import pytest

from test_bot_v2 import bot, good
from trading.sample_epoch import SampleEpoch
from trading.sample_report import bootstrap_ci, report
from trading.serialize import bot_status


def epoch():
    e = SampleEpoch()
    e.start("fp", "c", now=1000.0)
    return e


def row(e, gross, reason="trailing_stop", ts=2000.0, cost=100.0, epoch_id=None, noquote=False):
    return {"epoch": e.id if epoch_id is None else epoch_id, "entry_ts": ts, "exit_ts": ts + 60, "gross_move_pct": gross,
            "net_pnl_pct": gross - 6.0, "exit_reason": reason, "cost_usd": cost, "noquote": noquote}


def test_net_expectancy_per_cost_level():
    e = epoch()
    rows = [row(e, 10, ts=2000), row(e, -10, ts=2100), row(e, 20, ts=2200)]
    r = report(rows, e)
    m5 = r["by_cost"]["5%"]
    assert r["n"] == 3 and m5["expectancy_pct"] == pytest.approx((5 - 15 + 15) / 3, abs=1e-3)
    assert m5["win_rate_pct"] == pytest.approx(66.7) and m5["rr_realised"] == pytest.approx(10 / 15, abs=1e-3)
    assert m5["max_drawdown"]["usd"] == pytest.approx(15.0) and m5["max_drawdown"]["pct_of_start"] == pytest.approx(1.5)
    assert r["by_cost"]["10%"]["expectancy_pct"] == pytest.approx((0 - 20 + 10) / 3, abs=1e-3)
    assert r["by_cost"]["7%"]["total_usd"] == pytest.approx(3 - 17 + 13)
    lo, hi = m5["ci95_pct"]
    assert lo <= m5["expectancy_pct"] <= hi
    assert r["modelled_cost"]["expectancy_pct"] == pytest.approx((4 - 16 + 14) / 3, abs=1e-3)
    assert "net expectancy" in r["primary_metric"]


def test_bootstrap_ci_deterministic_and_none_for_tiny_samples():
    v = [1.0, -2.0, 3.0, 0.5, -1.0, 2.0]
    assert bootstrap_ci(v) == bootstrap_ci(v) and bootstrap_ci([1.0]) is None


def test_stop_gap_scenario():
    e = epoch()
    rows = [row(e, -12, "stop_loss"), row(e, -35, "stop_loss"), row(e, 30, "take_profit_2")]
    r = report(rows, e)
    g = r["sl_gap_scenario"]
    assert g["stop_exits"] == 2
    assert g["by_cost"]["5%"]["expectancy_pct"] == pytest.approx((-25 - 40 + 25) / 3, abs=1e-3)   # -12 -> -20; -35 stays
    assert r["by_cost"]["5%"]["expectancy_pct"] == pytest.approx((-17 - 40 + 25) / 3, abs=1e-3)


def test_legacy_and_noquote_are_excluded_and_tp_share_is_reference_only():
    e = epoch()
    rows = [row(e, 10, "take_profit_2"), row(e, -10, "stop_loss"),
            row(e, 50, epoch_id=""), row(e, 50, ts=500.0), row(e, 50, noquote=True), row(e, 50, epoch_id="old:x:1")]
    r = report(rows, e)
    assert r["n"] == 2 and r["excluded_legacy_or_noquote"] == 4
    assert r["reference_only"]["tp_share"] == 0.5 and "volatility" in r["reference_only"]["note"]


def test_dashboard_payload_has_the_sample_report():
    b = bot([good()])
    d = bot_status(b)
    assert d["sample_report"]["n"] == 0 and set(d["sample_report"]["by_cost"]) == {"5%", "7%", "10%"}


def test_replay_marks_tp_share_as_reference(tmp_path):
    from test_step4_measure import _db
    from research.replay import replay
    _db(tmp_path / "r.db", ["tp30"], ["sl15"])
    assert "REFERENCE ONLY" in replay(str(tmp_path / "r.db"))["metric_note"]
