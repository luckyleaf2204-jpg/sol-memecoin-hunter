"""V1.3 TRUTH PRICE LAYER: executable Jupiter SELL quote for the exact position size as the shadow P&L truth,
DexScreener / curve comparison, migration epochs, truth exit shadow (TP / SL / trailing / mirrored risk), truth MFE /
MAE, fast-SL research, no look-ahead, production untouched."""
import asyncio
import time

import pytest

from core.models import LiquidityIntel, TokenInfo
from test_bot_v2 import FakeJupiter, bot, good
from test_v12 import MINT, opened
from trading import jupiter as J
from trading.truth_price import (CONSISTENT, DS_MISMATCH, ERROR, MIGRATION_PRICE_DISCONTINUITY, MISMATCH, NO_ROUTE,
                                 STALE, STALE_DS, TRUTH_UNAVAILABLE, TRUTH_VALID, UNKNOWN, VALID, TruthPriceSnapshot,
                                 TruthTracker, compare_sources, confidence, curve_spot, is_fresh, map_reason,
                                 snapshot_from_quote)

SOL = 150.0
POOL = "PoolAAAA"


def sell_result(tokens, price, impact=0.01, slot=777, mint=MINT, pool=POOL, in_amount=None, decimals=6):
    sol_out = tokens * price / SOL
    q = {"inputMint": mint, "outputMint": J.WSOL, "inAmount": str(in_amount or int(tokens * 10 ** decimals)),
         "outAmount": str(int(sol_out * 1e9)), "priceImpactPct": str(impact),
         "routePlan": [{"swapInfo": {"label": "Pump.fun", "ammKey": pool}}]}
    if slot is not None:
        q["contextSlot"] = slot
    return J.QuoteResult(J.OK, quote=q, http=200, attempts=1)


def snap(price, ts, tokens=1000.0, **ctx):
    s = snapshot_from_quote(sell_result(tokens, price), mint=MINT, tokens=tokens, sol_price=SOL, t_req=ts - 0.2, t_resp=ts)
    for k, v in ctx.items():
        setattr(s, k, v)
    return compare_sources(s)


def tracker(entry=1.0, t0=1000.0, pair=POOL):
    return TruthTracker("1:" + MINT, MINT, "TKN", "NEW", entry, t0, 1000.0, sl_pct=15, tp1_pct=30, tp1_frac=0.5,
                        tp2_pct=80, trailing_pct=20, max_hold_s=3600, pair=pair, dex="pumpfun")


# ---------------------------------------------------------------- snapshot / statuses
def test_valid_jupiter_sell_snapshot_and_context_slot():
    s = snapshot_from_quote(sell_result(1000, 0.002, impact=0.02, slot=4242), mint=MINT, tokens=1000, sol_price=SOL,
                            t_req=10.0, t_resp=10.3)
    assert s.source_status == VALID and s.source == "jupiter_sell_quote"
    assert s.price_usd == pytest.approx(0.002) and s.sol_out == pytest.approx(1000 * 0.002 / SOL, rel=1e-6)
    assert s.price_impact_pct == pytest.approx(2.0) and s.context_slot == 4242 and s.route == "Pump.fun"
    assert s.route_pool == POOL and s.latency_ms == pytest.approx(300)
    assert snapshot_from_quote(sell_result(1000, 0.002, slot=None), mint=MINT, tokens=1000, sol_price=SOL,
                               t_req=0, t_resp=0).context_slot is None          # never guessed


def test_no_route_quote_error_and_mismatch_are_never_substituted():
    nr = snapshot_from_quote(J.QuoteResult(J.NO_ROUTE, http=400, code="COULD_NOT_FIND_ANY_ROUTE"), mint=MINT,
                             tokens=10, sol_price=SOL, t_req=0, t_resp=1)
    er = snapshot_from_quote(J.QuoteResult(J.TIMEOUT, detail="timeout"), mint=MINT, tokens=10, sol_price=SOL,
                             t_req=0, t_resp=1)
    mm = snapshot_from_quote(J.QuoteResult(J.INVALID, http=200, detail="inAmount 1 != 2"), mint=MINT, tokens=10,
                             sol_price=SOL, t_req=0, t_resp=1)
    wrong = snapshot_from_quote(sell_result(10, 1.0, mint="OtherMint"), mint=MINT, tokens=10, sol_price=SOL, t_req=0, t_resp=1)
    nosol = snapshot_from_quote(sell_result(10, 1.0), mint=MINT, tokens=10, sol_price=None, t_req=0, t_resp=1)
    assert (nr.source_status, er.source_status, mm.source_status, wrong.source_status, nosol.source_status) == \
        (NO_ROUTE, ERROR, MISMATCH, MISMATCH, UNKNOWN)
    assert all(x.price_usd is None for x in (nr, er, mm, wrong, nosol)) and "COULD_NOT_FIND" in nr.reason
    assert compare_sources(nr).ds_class == TRUTH_UNAVAILABLE and confidence(nr, False) == 0


def test_unavailable_sell_quote_goes_stale_and_truth_unknown():
    s = snap(0.9, 100.0)
    assert is_fresh(s, 120.0) and not is_fresh(s, 131.0) and not is_fresh(s, 99.0)     # 30 s limit, no future
    tr = tracker(t0=90.0)
    tr.add(s)
    sm = tr.summary(now=200.0)
    assert sm["exit_reason_truth"] == STALE                          # last executable quote too old


def test_quote_size_dependency_uses_the_position_size():
    """Executable price is size dependent: the same token at 10x size with more impact is a different truth."""
    small = snapshot_from_quote(sell_result(1_000, 0.002, impact=0.01), mint=MINT, tokens=1_000, sol_price=SOL, t_req=0, t_resp=0)
    big = snapshot_from_quote(sell_result(10_000, 0.0018, impact=0.10), mint=MINT, tokens=10_000, sol_price=SOL, t_req=0, t_resp=0)
    assert small.position_size == 1_000 and big.position_size == 10_000 and big.price_usd < small.price_usd
    assert big.mid_price == pytest.approx(0.0018 / 0.9) and small.mid_price == pytest.approx(0.002 / 0.99)


# ---------------------------------------------------------------- DexScreener / curve comparison
def test_fresh_ds_consistent_stale_ds_and_mismatch():
    assert snap(1.0, 10, ds_price=1.02, ds_age_s=1.0).ds_class == CONSISTENT     # vs mid 1/0.99
    assert snap(1.0, 10, ds_price=1.30, ds_age_s=12.0).ds_class == STALE_DS
    m = snap(1.0, 10, ds_price=1.55, ds_age_s=0.6)
    assert m.ds_class == DS_MISMATCH and m.ds_vs_truth_pct == pytest.approx(53.45, abs=0.05)   # not hidden
    assert snap(1.0, 10).ds_class == TRUTH_VALID                                # no DS print: truth only


def test_curve_price_only_while_on_a_fresh_sol_curve():
    now = 1000.0
    info = TokenInfo(mint=MINT, complete=False, virtual_sol_reserves=40.0, real_token_reserves=500_000_000,
                     quote_mint="11111111111111111111111111111111", pump_updated_at=now - 3)
    p, st, _ = curve_spot(info, SOL, now)
    assert st == VALID and p == pytest.approx(40.0 / (500_000_000 + 279_900_000) * SOL)
    info.pump_updated_at = now - 120
    assert curve_spot(info, SOL, now)[1] == STALE
    info.pump_updated_at, info.complete = now, True
    assert curve_spot(info, SOL, now)[:2] == (None, UNKNOWN)                    # after migration: not the market
    info.complete, info.quote_mint = False, "USDCxxxx"
    assert curve_spot(info, SOL, now)[0] is None
    s = snap(1.0, 10, ds_price=1.0, ds_age_s=1, curve_price=1.2)
    compare_sources(s)
    assert s.curve_vs_truth_pct == pytest.approx(18.8, abs=0.1) and s.ds_vs_curve_pct == pytest.approx(-16.667, abs=0.01)


# ---------------------------------------------------------------- migration epochs
def test_pair_migration_creates_a_new_epoch_and_flags_discontinuity():
    tr = tracker(t0=0.0, pair="CurvePair")
    tr.add(snap(1.0, 5, pair_address="CurvePair", pair_identity="pumpfun:CurvePair", ds_price=1.0, ds_age_s=1))
    ev = tr.add(snap(1.4, 10, pair_address="AmmPool", pair_identity="pumpswap:AmmPool", ds_price=1.5, ds_age_s=1))
    assert ev["flag"] == MIGRATION_PRICE_DISCONTINUITY and ev["old_pair"] == "CurvePair" and ev["new_pair"] == "AmmPool"
    assert ev["price_before"] == 1.0 and ev["price_after"] == 1.5 and ev["jump_pct"] == pytest.approx(50)
    assert tr.snapshots[-1]["epoch"] == 1 and tr.summary()["crosses_migration_unverified"] is True
    small = tracker(t0=0.0, pair="A")
    small.add(snap(1.0, 1, pair_address="A", pair_identity="pumpfun:A", ds_price=1.0))
    assert small.add(snap(1.0, 2, pair_address="B", pair_identity="pumpswap:B", ds_price=1.03))["flag"] == "PAIR_CHANGE"


def test_pre_and_post_migration_confidence():
    s = snap(1.0, 10, pair_address=POOL, ds_price=1.0, ds_age_s=1, migration_state="CURVE")
    assert confidence(s, pair_changed=False) == 100
    assert confidence(s, pair_changed=True) == 90                     # migration component lost
    s.migration_state = "MIGRATING"
    assert confidence(s, pair_changed=False) == 90


# ---------------------------------------------------------------- truth exit shadow / MFE / MAE / P&L
def test_initial_valuation_and_truth_pnl():
    tr = tracker(entry=1.0, t0=0.0)
    tr.add(snap(0.95, 3))
    sm = tr.summary(now=4)
    assert sm["label"] == "TRUTH_PNL_CANDIDATE" and tr.snapshots[-1]["truth_pnl_pct"] == pytest.approx(-5)
    assert sm["pnl_pct"] is None and sm["exit_reason_truth"] == "NONE"   # still open: no realized truth P&L


def test_truth_sl_tp_trailing_mfe_mae():
    sl = tracker(t0=0.0)
    sl.add(snap(0.9, 5))
    sl.add(snap(0.84, 10))
    s = sl.summary()
    assert s["exit_reason"] == "stop_loss" and s["exit_reason_truth"] == "SL" and s["pnl_pct"] == pytest.approx(-16)
    assert s["mae_pct"] == pytest.approx(-16) and s["time_to_mae_s"] == 10 and s["mfe_pct"] == pytest.approx(-10)
    tp = tracker(t0=0.0)
    for t, p in ((5, 1.31), (20, 1.6), (40, 1.27)):
        tp.add(snap(p, t))
    s = tp.summary()
    assert s["exit_reason_truth"] == "TRAILING" and s["pnl_pct"] == pytest.approx(100 * (0.5 * 1.31 + 0.5 * 1.27 - 1))
    assert s["mfe_pct"] == pytest.approx(60) and s["time_to_mfe_s"] == 20
    tp2 = tracker(t0=0.0)
    tp2.add(snap(1.85, 5))
    assert tp2.summary()["exit_reason_truth"] == "TP" and tp2.summary()["pnl_pct"] == pytest.approx(85)


def test_old_vs_truth_shadow_and_fast_sl_classification():
    """Production SL at 8 s on a DexScreener mark, while the executable quote says -4 % -> not an executable loss."""
    tr = tracker(t0=0.0)
    tr.add(snap(0.96, 5, ds_price=0.80, ds_age_s=12))
    tr.on_production_exit("stop_loss", 0.80, -20.0, 8.0, "liquidity_model_at_dexscreener_mark", {"age_ms": 12000})
    s = tr.summary()
    assert s["exit_reason_old"] == "SL" and s["old_fast_sl_class"] == "STALE_PRICE_ARTIFACT"
    assert s["pnl_common_source"] == pytest.approx(-4) and s["fast_sl"]["5s"]["sl_hit"] is False
    real = tracker(t0=0.0)
    real.add(snap(0.80, 5))
    real.on_production_exit("stop_loss", 0.80, -20.0, 6.0, "x", {"age_ms": 1000})
    assert real.summary()["old_fast_sl_class"] == "REAL_EXECUTABLE_LOSS"
    blind = tracker(t0=0.0)
    blind.on_production_exit("stop_loss", 0.80, -20.0, 6.0, "x", None)
    assert blind.summary()["old_fast_sl_class"] == "UNVERIFIED"


def test_non_price_production_exit_is_mirrored_on_next_executable_quote():
    tr = tracker(t0=0.0)
    tr.add(snap(1.0, 5))
    tr.on_production_exit("liquidity_collapse", 0.7, -30.0, 6.0, "liquidity_model_at_dexscreener_mark", None)
    tr.add(snap(0.88, 7))
    s = tr.summary()
    assert s["exit_reason"] == "mirror:liquidity_collapse" and s["exit_reason_truth"] == "LIQUIDITY"
    assert s["pnl_pct"] == pytest.approx(-12) and map_reason("risk_spike") == "RISK"


def test_no_lookahead_snapshots_before_entry_or_out_of_order_are_rejected():
    tr = tracker(t0=100.0)
    tr.add(snap(0.5, 99.0))                          # before the entry
    tr.add(snap(1.0, 110.0))
    tr.add(snap(0.5, 105.0))                         # back-filled
    assert tr.rejected == 2 and len(tr.snapshots) == 1 and tr.mae == pytest.approx(0)
    tr.add(snap(0.7, 140.0))
    assert tr.fast_sl()["10s"]["truth_pnl_pct"] == pytest.approx(0)      # value at entry+10 s ignores the later -30 %
    assert tr.fast_sl()["5s"] is None                                    # nothing known at entry+5 s


# ---------------------------------------------------------------- bot integration
class TruthJupiter:
    """BUY quotes like FakeJupiter; SELL quotes at a scripted executable price (or a scripted failure)."""
    def __init__(self, sell_price=None, status=J.OK, slot=99):
        self.buy, self.sell_price, self.status, self.slot, self.sells = FakeJupiter(), sell_price, status, slot, []

    async def quote(self, *a):
        return await self.buy.quote(*a)

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps):
        if input_mint == J.WSOL:
            return J.QuoteResult(J.OK, quote=await self.buy.quote(input_mint, output_mint, amount_raw, slippage_bps), http=200)
        self.sells.append(amount_raw)
        if self.status != J.OK:
            return J.QuoteResult(self.status, http=400, code="COULD_NOT_FIND_ANY_ROUTE")
        return sell_result(amount_raw / 1e6, self.sell_price, slot=self.slot, mint=input_mint)


def test_bot_truth_round_records_snapshot_db_rows_and_panel():
    b, st, p = opened()
    rows = {}
    b.recorder = type("R", (), {"__getattr__": lambda self, n: (lambda *a: rows.setdefault(n, []).append(a))})()
    b.jupiter = TruthJupiter(sell_price=p.entry_price * 0.9)
    n = asyncio.run(b.common_source_round(time.time() + 1))
    assert n == 1 and b.jupiter.sells == [int(p.initial_tokens * 1e6)]    # exact position size
    tr = b.cs_shadow[MINT]
    x = tr.snapshots[-1]
    assert x["source_status"] == VALID and x["context_slot"] == 99 and x["truth_pnl_pct"] == pytest.approx(-10, abs=0.01)
    assert x["ds_price"] == st.market.price_usd and x["pair_address"] == st.market.pair_address
    assert {"truth_snapshot", "truth_trade", "price_provenance"} <= set(rows)
    panel = b.truth_panel(time.time() + 2)
    assert panel["open"][0]["truth"] == "VALID" and panel["open"][0]["truth_pnl_pct"] == pytest.approx(-10, abs=0.01)
    assert panel["open"][0]["ds_mark_post_entry"] is False                # the pre-entry print is not shown as a mark
    late = b.truth_panel(time.time() + 60)
    assert late["open"][0]["truth"] == "TRUTH UNKNOWN" and late["open"][0]["truth_pnl_pct"] is None   # no fake P&L


def test_bot_no_route_keeps_truth_unknown():
    b, st, p = opened()
    b.jupiter = TruthJupiter(status=J.NO_ROUTE)
    asyncio.run(b.common_source_round(time.time() + 1))
    tr = b.cs_shadow[MINT]
    assert tr.snapshots[-1]["source_status"] == NO_ROUTE and b.truth_panel()["open"][0]["truth"] == "TRUTH UNKNOWN"
    assert tr.summary()["exit_reason_truth"] == NO_ROUTE


def test_bot_exit_shadow_records_old_and_truth():
    b, st, p = opened()
    rows = {}
    b.recorder = type("R", (), {"__getattr__": lambda self, n: (lambda *a: rows.setdefault(n, []).append(a))})()
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()                                                             # production liquidity_collapse
    asyncio.run(b.execute_sells())                              # HARD exit: filled on a Jupiter SELL quote
    b.jupiter = TruthJupiter(sell_price=p.entry_price * 0.95)
    asyncio.run(b.common_source_round(time.time() + 1))
    ex = rows["truth_exit"][-1][0]
    assert ex["exit_reason_old"] == "LIQUIDITY" and ex["exit_reason_truth"] == "LIQUIDITY"
    assert ex["truth_pnl"] == pytest.approx(-5, abs=0.01) and ex["old_pnl"] is not None


def test_truth_layer_never_changes_production():
    """Same inputs with and without the truth layer -> identical book (decisions do not read truth data)."""
    def run(with_truth):
        b, st, p = opened()
        if with_truth:
            b.jupiter = TruthJupiter(sell_price=p.entry_price * 0.5)   # terrible executable price
            asyncio.run(b.common_source_round(time.time() + 1))
        st.stamps["market"].updated_at = time.time() + 2
        st.market.price_usd = p.entry_price * 1.1
        b.tick()
        return MINT in b.book.positions, round(p.last_price, 12), p.stop_price, len(b.book.executions)
    assert run(True) == run(False)


def test_truth_budget_cap():
    from trading import bot as B
    b, st, p = opened()
    b.jupiter = TruthJupiter(sell_price=p.entry_price)
    now = time.time()
    b.truth_quotes.extend([now] * B.TRUTH_QUOTES_PER_MIN)
    assert asyncio.run(b.common_source_round(now + 1)) == 0 and b.truth_skipped_budget == 1


def test_research_tables_are_written(tmp_path):
    import sqlite3
    from research.dataset import DatasetRecorder
    b, st, p = opened()
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    b.jupiter = TruthJupiter(sell_price=p.entry_price * 0.8)
    asyncio.run(b.common_source_round(time.time() + 1))
    tr = b.cs_shadow[MINT]
    tr.add(snap(p.entry_price * 0.8, time.time() + 3, tokens=tr.size_now(), pair_address="NewPool",
                pair_identity="pumpswap:NewPool", ds_price=p.entry_price))
    b._rec("truth_epoch", tr.pair_changes[-1])
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()
    asyncio.run(b.execute_sells())                              # HARD exit: filled on a Jupiter SELL quote
    db = sqlite3.connect(tmp_path / "r.db")
    n = {t: db.execute(f"select count(*) from {t}").fetchone()[0] for t in
         ("truth_price_snapshots", "price_source_comparison", "migration_price_epochs", "trade_price_truth",
          "truth_exit_shadow")}
    assert all(v >= 1 for v in n.values()), n
    row = db.execute("select truth_status, context_slot, jupiter_sell_price, ds_age from truth_price_snapshots").fetchone()
    assert row[0] == VALID and row[1] == 99 and row[2] == pytest.approx(p.entry_price * 0.8, rel=1e-6)
    assert db.execute("select label from trade_price_truth").fetchone()[0] == "TRUTH_PNL_CANDIDATE"


def test_truth_keeps_quoting_while_production_holds_and_values_its_exit_timing():
    """Live V1.3 run: the truth shadow closed first and stopped quoting -> pnl_common_source known for 4/16 trades."""
    tr = tracker(t0=0.0)
    tr.add(snap(0.84, 5))                                      # truth SL
    assert tr.exit.closed_at is not None and tr.due(30.0)      # production still open: keep quoting
    tr.add(snap(0.97, 30))
    tr.on_production_exit("stop_loss", 0.62, -38.0, 31.0, "liquidity_model_at_dexscreener_mark", {"age_ms": 15000})
    assert not tr.due(60.0)                                    # both closed
    s = tr.summary()
    assert s["pnl_common_source"] == pytest.approx(-3) and s["pnl_pct"] == pytest.approx(-16)
    assert s["mae_pct"] == pytest.approx(-16) and s["exit_reason_old"] == "SL"


def test_quotes_the_production_size_while_it_holds_and_never_zero():
    """Live V1.3 run B: after the truth shadow closed, size 0 was quoted (Jupiter INVALID) -> coverage gaps."""
    b, st, p = opened()
    j = TruthJupiter(sell_price=p.entry_price * 0.8)              # truth shadow: stop loss at once
    b.jupiter = j
    asyncio.run(b.common_source_round(time.time() + 1))
    tr = b.cs_shadow[MINT]
    assert tr.exit.closed_at is not None and tr.size_now() == 0 and MINT in b.book.positions
    j.sell_price = p.entry_price * 0.95
    asyncio.run(b.common_source_round(time.time() + 7))
    assert j.sells[-1] == int(p.tokens * 1e6) and tr.snapshots[-1]["source_status"] == VALID
    assert all(x > 0 for x in j.sells)
