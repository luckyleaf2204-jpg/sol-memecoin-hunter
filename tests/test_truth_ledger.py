"""V1.3 final validation: OFFICIAL TRUTH P&L from executable Jupiter SELL quotes for exactly the tokens sold,
measured entry / exit latency, acceptance test, legacy P&L kept separate."""
import asyncio
import json
import time

import pytest

from test_v12 import MINT, opened
from test_v13 import TruthJupiter, snap, tracker
from trading import jupiter as J
from trading.truth_price import (ACCEPT_N, LEGACY_FAST_SL, NO_ROUTE, VALID, TruthLedger, apply_exit_quote,
                                 apply_requote, event_valid, ledger_stats, new_exit_event, trade_record)


def closed_tracker(exit_price=1.1, lag=1.0, slot=777, tokens=1000.0, status_ok=True, entry_quote=0.98):
    tr = tracker(entry=1.0, t0=0.0)
    tr.entry_quote_price = entry_quote
    tr.execution_impact_pct, tr.latency_model_pct = 0.5, 2.0
    tr.ctx = {"cost_usd": 1000.0, "setup": "NEW/new", "age_s": 120, "liquidity_usd": 8000, "opportunity": 72,
              "risk": 30, "entry_latency_measured_pct": 0.8}
    ev = new_exit_event(50.0, "stop_loss", tokens, 0.8, "liquidity_model_at_dexscreener_mark")
    s = snap(exit_price, 50.0 + lag, tokens=tokens)
    s.context_slot = slot
    if not status_ok:
        s.source_status, s.price_usd, s.sol_out = NO_ROUTE, None, None
    apply_exit_quote(ev, s, 150.0)
    apply_requote(ev, snap(exit_price * 0.99, 53.0 + lag, tokens=tokens))
    tr.exit_events.append(ev)
    tr.production_open = False
    return tr


def test_truth_pnl_math_entry_cost_vs_executable_proceeds():
    tr = closed_tracker(exit_price=1.1)
    r = trade_record(tr, {"pnl_pct": -20.0, "pnl_usd": -200.0, "source": "x"}, network_fee_usd=0.5)
    assert r["valid"] and r["label"] == "TRUTH_PNL"
    assert r["proceeds_usd"] == pytest.approx(1100, rel=1e-6)          # 1000 tokens x 1.1 executable
    assert r["truth_pnl_usd"] == pytest.approx(1100 - 1000 - 0.5, rel=1e-6) and r["truth_pnl_pct"] == pytest.approx(9.95, abs=0.01)
    assert r["legacy_pnl_pct"] == -20.0 and r["sign_differs_from_legacy"] is True       # kept apart, not mixed
    assert r["exit_truth_price"] == pytest.approx(1.1) and r["entry_truth_price"] == 1.0
    assert r["pnl_quote_to_quote_pct"] == pytest.approx(12.245, abs=0.01)             # vs the BUY quote itself
    c = r["cost"]
    assert c["exit_latency_measured_pct"] == pytest.approx(1.0) and c["entry_latency_measured_pct"] == 0.8
    assert c["entry_latency_sim_pct"] == 2.0 and c["network_fee_pct"] == pytest.approx(0.05)
    assert c["total_execution_cost_pct"] == pytest.approx(0.5 + 0.8 + 1.0 + 1.0 + 0.05, abs=0.01)  # sim latency NOT used


def test_invalid_exit_quotes_are_excluded_from_common_source():
    assert not trade_record(closed_tracker(status_ok=False), {}, 0.0)["valid"]
    assert "no contextSlot" in trade_record(closed_tracker(slot=None), {}, 0.0)["invalid_reasons"]
    late = trade_record(closed_tracker(lag=12.0), {}, 0.0)
    assert not late["valid"] and "lag" in late["invalid_reasons"][0]
    tr = closed_tracker()
    tr.exit_events[0]["quoted_tokens"] = 999.0                       # not the size sold
    assert event_valid(tr.exit_events[0]) == (False, "quote size != tokens sold")
    tr = closed_tracker()
    tr.exit_events[0]["lag_s"] = -0.5                                 # quote before the exit decision (look-ahead)
    assert not event_valid(tr.exit_events[0])[0]


def test_truth_mfe_mae_only_within_the_holding_window():
    tr = closed_tracker(exit_price=0.9)
    tr.add(snap(1.3, 20))                                            # inside
    tr.snapshots.append({**tr.snapshots[-1], "timestamp": 500.0, "price_usd": 3.0})   # after the exit: ignored
    r = trade_record(tr, {}, 0.0)
    assert r["mfe_truth"] == pytest.approx(30) and r["mae_truth"] == pytest.approx(-10)


def test_ledger_stats_acceptance_and_persistence(tmp_path):
    led = TruthLedger(tmp_path / "truth_ledger.json")
    for i in range(ACCEPT_N - 1):
        led.add(trade_record(closed_tracker(exit_price=1.2 if i % 3 == 0 else 0.9), {"pnl_pct": 1.0}, 0.0))
    for _ in range(100):
        s = snap(1.0, 1.0)
        s.context_slot = 5
        led.count_quote(s)
    a = led.acceptance()
    assert a["status"] == "NOT VALIDATED YET" and not a["checks"]["common_source_n>=30"] and a["legacy_fast_sl"] == LEGACY_FAST_SL
    led.add(trade_record(closed_tracker(), {}, 0.0))
    a = led.acceptance()
    assert a["status"] == "VALIDATED" and a["valid_quote_pct"] == 100.0 and a["context_slot_pct"] == 100.0
    st = ledger_stats(led.trades)
    assert st["n"] == ACCEPT_N and st["win_rate_pct"] == pytest.approx(100 * 11 / 30, abs=0.1)
    assert st["profit_factor"] == pytest.approx((10 * 200 + 100) / (19 * 100), rel=1e-3)   # wins 2100 / losses 1900
    assert st["expectancy_usd"] == pytest.approx((2100 - 1900) / 30, rel=1e-3)
    assert set(st) >= {"by_setup", "by_age", "by_liquidity", "by_opportunity", "by_risk_at_entry", "execution_cost"}
    assert st["by_liquidity"]["5-10k"]["n"] == 30 and st["by_age"]["1-5m"]["sample"] == "OK"
    again = TruthLedger(tmp_path / "truth_ledger.json")
    assert len(again.trades) == ACCEPT_N and again.quotes["VALID"] == 100     # survives a restart
    bad = snap(1.0, 1.0)
    bad.source_status = NO_ROUTE
    for _ in range(10):
        again.count_quote(bad)
    assert again.acceptance()["checks"]["sell_quote_valid>=95%"] is False


def test_bot_production_exit_gets_exact_size_truth_quote_requote_and_ledger_record(tmp_path):
    b, st, p = opened()
    b.truth_ledger.path = tmp_path / "truth_ledger.json"
    j = TruthJupiter(sell_price=p.entry_price * 0.9, slot=4242)
    b.jupiter = j
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.7                     # production (DexScreener) stop loss
    b.tick()
    asyncio.run(b.execute_sells())                              # HARD exit: filled on a Jupiter SELL quote
    tr = b.cs_shadow[MINT]
    ev = tr.exit_events[0]
    assert MINT not in b.book.positions and ev["status"] == "PENDING" and ev["tokens"] == pytest.approx(p.initial_tokens)
    b.intents["Other"] = {"ts": time.time()}                      # exit truth quotes are NOT blocked by BUY intents
    now = time.time() + 1
    asyncio.run(b.common_source_round(now))
    assert ev["status"] == VALID and ev["slot"] == 4242 and ev["quoted_tokens"] == pytest.approx(p.initial_tokens)
    assert j.sells[0] == int(p.initial_tokens * 1e6) and not b.truth_ledger.trades      # waits for the re-quote
    asyncio.run(b.common_source_round(now + 2.5))
    assert ev["requote_status"] == VALID and ev["exit_latency_pct"] == pytest.approx(0, abs=1e-6)
    rec = b.truth_ledger.trades[-1]
    fee = b.exec.network_fee(150.0)                               # network + priority fee of the exit tx
    assert rec["valid"] and rec["truth_pnl_pct"] == pytest.approx(
        100 * ((p.initial_tokens * p.entry_price * 0.9 - fee) / p.cost_usd - 1), abs=0.1)
    assert rec["legacy_pnl_pct"] < rec["truth_pnl_pct"] and rec["legacy_exit_source"] == "jupiter_sell_quote"   # step 1
    assert json.loads((tmp_path / "truth_ledger.json").read_text())["trades"][0]["trade_id"] == rec["trade_id"]
    panel = b.truth_panel()
    assert panel["status"]["common_source_n"] == 1 and panel["status"]["status"] == "NOT VALIDATED YET"
    assert panel["truth"]["n"] == 1 and "LEGACY" in panel["legacy"]["label"]


def test_partial_tp1_and_final_exit_are_both_quoted_at_their_sizes():
    b, st, p = opened()
    j = TruthJupiter(sell_price=p.entry_price * 1.35, slot=9)
    b.jupiter = j
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 1.35                    # TP1 (via Jupiter sell intent)
    b.tick()
    asyncio.run(b.execute_sells())
    tr = b.cs_shadow[MINT]
    assert len(tr.exit_events) == 1 and tr.exit_events[0]["reason"] == "take_profit_1"
    half = tr.exit_events[0]["tokens"]
    from core.models import LiquidityIntel
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()
    asyncio.run(b.execute_sells())                              # HARD exit: filled on a Jupiter SELL quote
    assert len(tr.exit_events) == 2 and tr.exit_events[1]["tokens"] == pytest.approx(p.initial_tokens - half)
    now = time.time() + 1
    asyncio.run(b.common_source_round(now))
    asyncio.run(b.common_source_round(now + 3))
    rec = b.truth_ledger.trades[-1]
    assert rec["valid"] and rec["exit_reasons"] == ["take_profit_1", "liquidity_collapse"]
    assert [e["quoted_tokens"] for e in rec["events"]] == pytest.approx([half, p.initial_tokens - half])


def test_entry_latency_measured_from_the_buy_requote_probe():
    b, st, p = opened()
    tr = b.cs_shadow[MINT]
    assert tr.ctx["entry_latency_measured_pct"] is None            # probe off in tests: never invented
    assert tr.ctx["cost_usd"] == pytest.approx(p.cost_usd) and tr.ctx["risk"] is not None


def test_failed_exit_quote_leaves_trade_out_of_common_source():
    b, st, p = opened()
    b.jupiter = TruthJupiter(status=J.NO_ROUTE)
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.7
    b.tick()
    asyncio.run(b.execute_sells())                              # HARD exit: filled on a Jupiter SELL quote
    asyncio.run(b.common_source_round(time.time() + 1))
    rec = b.truth_ledger.trades[-1]
    assert not rec["valid"] and rec["truth_pnl_pct"] is None and "exit quote NO_ROUTE" in rec["invalid_reasons"]
    assert b.truth_ledger.acceptance()["common_source_n"] == 0 and b.truth_ledger.quotes["INVALID"] >= 1
