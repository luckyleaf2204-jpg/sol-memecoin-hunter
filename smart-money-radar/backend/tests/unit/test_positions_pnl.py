"""Position classification, Actual Entry Cost, PnL and Win Rate.

Inputs here are hand-built event sequences that exercise the accounting rules; they are test
vectors, never shown in the product.
"""
import pytest

from radar.analytics.performance import compute_performance, max_drawdown
from radar.analytics.positions import Event, build_positions, mark_to_market

W, M = "Wallet1111111111111111111111111111111111111", "Mint11111111111111111111111111111111111111"
T0 = 1_790_000_000


def ev(i, typ, sol=0.0, tok=0.0, fees=0.0, t=None, ver="verified", sig=None, liq=None):
    return Event(signature=sig or f"sig{i}", event_key="trade:0", slot=1000 + i, block_time=t or T0 + i * 60,
                 tx_type=typ, sol_amount=sol, token_amount=tok, fees_sol=fees, verification_status=ver,
                 liquidity_sol=liq)


def test_multiple_buys_are_one_position_and_entry_cost_is_their_sum():
    ps, orph = build_positions(W, M, [ev(1, "buy", 1.5, 1000, 0.02), ev(2, "buy", 1.0, 500, 0.01),
                                      ev(3, "sell", 3.0, 1500, 0.04)])
    assert len(ps) == 1 and not orph
    p = ps[0]
    assert p.n_buys == 2 and p.n_sells == 1
    assert p.buy_sol == pytest.approx(2.5)          # Actual Entry Cost
    assert p.status == "closed"
    # realised = proceeds - cost - all fees
    assert p.realized_pnl_sol == pytest.approx(3.0 - 2.5 - 0.07)
    assert p.qualifying(2.0) and p.is_win


def test_partial_sells_use_average_cost_and_keep_position_open():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2.0, 1000), ev(2, "sell", 1.5, 500)])
    p = ps[0]
    assert p.status == "open"
    assert p.remaining_tokens == pytest.approx(500)
    assert p.cost_basis_remaining == pytest.approx(1.0)
    assert p.realized_gross == pytest.approx(1.5 - 1.0)
    mark_to_market(p, price_sol=0.004, now=T0 + 600)
    assert p.unrealized_pnl_sol == pytest.approx(500 * 0.004 - 1.0)
    assert p.is_win is None                          # open positions are not wins/losses


def test_rebuy_after_full_exit_starts_a_new_independent_position():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2, 100), ev(2, "sell", 1, 100), ev(3, "buy", 3, 50),
                                   ev(4, "sell", 6, 50)])
    assert [p.cycle_no for p in ps] == [1, 2]
    assert ps[0].realized_pnl_sol < 0 and ps[1].realized_pnl_sol > 0


def test_dust_left_behind_still_closes_the_position():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2, 1_000_000), ev(2, "sell", 2.5, 999_999.5)])
    assert ps[0].status == "closed"


def test_transfer_in_is_not_a_buy_and_has_no_entry_cost():
    ps, _ = build_positions(W, M, [ev(1, "transfer_in", 0, 1000), ev(2, "sell", 1.0, 1000)])
    p = ps[0]
    assert p.n_buys == 0 and p.buy_sol == 0
    assert "opened_by_transfer" in p.flags
    assert not p.qualifying(2.0)                     # never counted as a Smart Money position


def test_transfer_out_moves_cost_instead_of_realising_a_loss():
    ps, _ = build_positions(W, M, [ev(1, "buy", 4.0, 1000), ev(2, "transfer_out", 0, 500), ev(3, "sell", 3.0, 500)])
    p = ps[0]
    assert p.status == "closed"
    assert p.transferred_out_cost == pytest.approx(2.0)
    assert p.realized_pnl_sol == pytest.approx(3.0 - 2.0)   # only the sold half is realised
    assert "transfer_out" in p.flags


def test_all_tokens_transferred_out_leaves_no_entry_cost_and_no_roi():
    # 14 equal transfers leave 8.9e-16 SOL of float residue in the entry cost; before the guard
    # ROI became -fees/residue = -1e11 and overflowed wallet_positions.roi (seen live 22/11/2026)
    evs = [ev(0, "buy", 2.951111108, 1e7, 0.0001)] + [ev(i + 1, "transfer_out", 0, 1e7 / 14) for i in range(14)]
    p = build_positions(W, M, evs)[0][0]
    assert p.status == "closed"
    assert p.net_entry_cost == 0.0
    assert p.roi is None and p.realized_multiple is None


def test_sell_without_known_buy_is_an_orphan_not_a_profit():
    ps, orph = build_positions(W, M, [ev(1, "sell", 5.0, 1000)])
    assert ps == [] and len(orph) == 1


def test_duplicate_events_are_ignored_idempotently():
    e = ev(1, "buy", 2, 100)
    ps, _ = build_positions(W, M, [e, e, ev(2, "sell", 3, 100), ev(2, "sell", 3, 100)])
    assert ps[0].buy_sol == 2 and ps[0].sell_sol == 3


def test_abandoned_worthless_bag_is_written_off_as_a_loss():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2.0, 1000)])
    p = ps[0]
    mark_to_market(p, price_sol=0.00001, now=T0 + 100 * 3600)   # worth 0.01 SOL, idle 100 h
    assert p.status == "written_off"
    assert p.realized_pnl_sol == pytest.approx(-2.0)
    assert p.is_win is False


def test_unpriceable_token_is_written_off_after_idle_period():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2.0, 1000)])
    mark_to_market(ps[0], price_sol=None, now=T0 + 80 * 3600)
    assert ps[0].status == "written_off" and "written_off_unpriceable" in ps[0].flags


def test_recently_bought_open_bag_is_not_written_off():
    ps, _ = build_positions(W, M, [ev(1, "buy", 2.0, 1000)])
    mark_to_market(ps[0], price_sol=None, now=T0 + 3600)
    assert ps[0].status == "open"


def _closed(pnl_list, size=2.5, start=T0):
    ps = []
    for i, pnl in enumerate(pnl_list):
        sell = size + pnl
        p, _ = build_positions(W, f"M{i}", [ev(i * 2, "buy", size, 100, t=start + i * 100),
                                             ev(i * 2 + 1, "sell", sell, 100, t=start + i * 100 + 50)])
        ps += p
    return ps


def test_win_rate_counts_closed_qualifying_positions_only():
    ps = _closed([1, 1, -0.5, 2])
    small, _ = build_positions(W, "Msmall", [ev(90, "buy", 0.5, 10, t=T0 + 900), ev(91, "sell", 5, 10, t=T0 + 950)])
    open_p, _ = build_positions(W, "Mopen", [ev(92, "buy", 3, 10, t=T0 + 990)])
    perf = compute_performance(ps + small + open_p, 30, T0 + 2000, 2.0, True)
    assert perf.positions_qualifying == 5            # 0.5 SOL position excluded
    assert perf.closed_qualifying == 4 and perf.open_qualifying == 1
    assert perf.win_rate == pytest.approx(3 / 4)
    assert perf.realized_pnl_sol == pytest.approx(3.5)
    assert perf.roi == pytest.approx(3.5 / 10.0)
    assert perf.profit_factor == pytest.approx(4 / 0.5)
    assert perf.largest_win_sol == pytest.approx(2) and perf.largest_loss_sol == pytest.approx(-0.5)


def test_median_buy_resists_one_huge_buy():
    sizes = [2.2, 2.5, 3.0, 2.4, 200.0]
    ps = []
    for i, s in enumerate(sizes):
        p, _ = build_positions(W, f"X{i}", [ev(i * 2, "buy", s, 10, t=T0 + i), ev(i * 2 + 1, "sell", s * 1.1, 10, t=T0 + i + 1)])
        ps += p
    perf = compute_performance(ps, 30, T0 + 100, 2.0, True)
    assert perf.median_buy_sol == pytest.approx(2.5)
    assert perf.avg_buy_sol == pytest.approx(sum(sizes) / 5)
    assert perf.largest_buy_sol == 200.0
    assert perf.metrics["median_to_mean"] < 0.1


def test_period_filter_excludes_old_positions():
    old = _closed([5], start=T0 - 40 * 86400)
    new = _closed([1], start=T0)
    perf = compute_performance(old + new, 30, T0 + 1000, 2.0, True)
    assert perf.closed_qualifying == 1 and perf.realized_pnl_sol == pytest.approx(1)


def test_max_drawdown_and_single_win_dependence():
    assert max_drawdown([1, 2, -4, 1, -1]) == pytest.approx(4)
    perf = compute_performance(_closed([10, -0.3, -0.4, -0.2, -0.1]), 30, T0 + 2000, 2.0, True)
    assert perf.top_win_share == pytest.approx(1.0)
    assert perf.pnl_ex_best_sol < 0


def test_verified_share_reflects_partial_trades():
    p1, _ = build_positions(W, "A", [ev(1, "buy", 2, 10), ev(2, "sell", 3, 10)])
    p2, _ = build_positions(W, "B", [ev(3, "buy", 2, 10, ver="partially_verified"), ev(4, "sell", 3, 10)])
    perf = compute_performance(p1 + p2, 30, T0 + 1000, 2.0, True)
    assert perf.verified_share == pytest.approx(0.5)
