import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from core.config import Settings  # noqa: E402
from core.models import DevReport, HolderStats, TokenInfo, TokenState  # noqa: E402
from dex.dexscreener import parse_pair  # noqa: E402
from history.store import TokenHistory  # noqa: E402
from i18n import set_language  # noqa: E402
from scanner.pipeline import evaluate, ingest_market  # noqa: E402

SOL_USD = 117.5
set_language("en")


@pytest.fixture(autouse=True)
def _offline_unless_live(request, monkeypatch):
    """Offline tests must be hermetic: no real API key -> no real network call, even under `--live`
    (where HELIUS_API_KEY is exported for the live tests only)."""
    if "live" not in request.keywords:
        monkeypatch.delenv("HELIUS_API_KEY", raising=False)
        monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    yield


@pytest.fixture(autouse=True)
def _fast_lease(monkeypatch):
    """The lease's acquire settle wait (2 s on the server) is 0 in tests; test_lease drives it explicitly."""
    import core.lease
    monkeypatch.setattr(core.lease, "ACQUIRE_SETTLE_S", 0.0)
    yield


@pytest.fixture(autouse=True)
def _reset_language():
    """create_app() switches the global UI language to Vietnamese; never let that leak into the next test."""
    set_language("en")
    yield
    set_language("en")


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run tests that call real APIs")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live API test (use --live)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def dex_pair(mint="Mint111", *, price="0.0002", mc=200_000, fdv=200_000, liq=40_000, dex="pumpswap",
             pair="PAIR111", m5=(300, 150), h1=(1200, 900), vol=(40_000, 120_000), pc5=8.0, drop=()):
    """A raw DexScreener /tokens/v1 pair dict. `drop` removes keys to simulate missing fields."""
    p = {
        "chainId": "solana", "dexId": dex, "pairAddress": pair,
        "baseToken": {"address": mint, "symbol": "TKN"}, "quoteToken": {"symbol": "SOL"},
        "priceUsd": price, "marketCap": mc, "fdv": fdv,
        "txns": {"m5": {"buys": m5[0], "sells": m5[1]}, "h1": {"buys": h1[0], "sells": h1[1]}},
        "volume": {"m5": vol[0], "h1": vol[1], "h6": vol[1] * 3, "h24": vol[1] * 6},
        "priceChange": {"m5": pc5, "h1": 30.0, "h6": 50.0, "h24": 80.0},
        "pairCreatedAt": int((time.time() - 3600) * 1000),
    }
    if liq is not None:
        p["liquidity"] = {"usd": liq}
    for k in drop:
        p.pop(k, None)
    return p


def default_info(mint="Mint111", age_s=3600, **kw) -> TokenInfo:
    base = dict(mint=mint, name="Good Cat", symbol="GCAT", creator="Dev111", created_at=time.time() - age_s,
                twitter="https://x.com/g", telegram="https://t.me/g", website="https://g.io", complete=True,
                total_supply=1e9)
    base.update(kw)
    return TokenInfo(**base)


def good_holders() -> HolderStats:
    return HolderStats(holder_count=1200, top5_pct=8, top10_pct=12, top20_pct=18, top50_pct=30, max_single_pct=2.5,
                       source="helius_das", complete_list=True)


def good_dev() -> DevReport:
    return DevReport(creator="Dev111", balance_verified=True, balance_source="Solana RPC", current_pct=1.5,
                     current_tokens=15e6, status="HOLD", sold_pct=0.0, history_verified=True, prev_tokens_count=3,
                     prev_graduated=1, prev_dead=1)


def build_state(pair: dict, *, info: TokenInfo | None = None, holders=True, dev=True, settings: Settings | None = None,
                history: TokenHistory | None = None, now: float | None = None) -> TokenState:
    """Run ONE raw pair through the real pipeline: ingest (validation + history) -> evaluate."""
    now = now or time.time()
    st = TokenState(info=info or default_info(pair["baseToken"]["address"]))
    if holders:
        st.holders = good_holders()
    if dev:
        st.dev = good_dev()
    h = history if history is not None else TokenHistory()
    ingest_market(st, parse_pair(pair), {}, SOL_USD, h, now)
    evaluate(st, settings or Settings(), h, now, SOL_USD)
    return st


def build_series(pairs_by_ago: list[tuple[float, dict]], *, info: TokenInfo | None = None, holders=True, dev=True,
                 now: float | None = None, settings: Settings | None = None) -> tuple[TokenState, TokenHistory]:
    """Feed several observations (seconds_ago, raw_pair) oldest first through the pipeline."""
    now = now or time.time()
    first = pairs_by_ago[0][1]
    st = TokenState(info=info or default_info(first["baseToken"]["address"]))
    if holders:
        st.holders = good_holders()
    if dev:
        st.dev = good_dev()
    h = TokenHistory()
    s = settings or Settings()
    for ago, pair in sorted(pairs_by_ago, key=lambda x: -x[0]):
        ts = now - ago
        ingest_market(st, parse_pair(pair), {}, SOL_USD, h, ts)
        evaluate(st, s, h, ts, SOL_USD)
    return st, h


@pytest.fixture
def good_state():
    return build_state(dex_pair())


@pytest.fixture
def bad_state():
    info = default_info("Mint222", symbol="RUG", creator="Dev222", twitter="", telegram="", website="",
                        dev_initial_buy=150_000_000)
    st = TokenState(info=info)
    st.holders = HolderStats(holder_count=30, top5_pct=45, top10_pct=58, top20_pct=70, max_single_pct=20,
                             source="helius_das")
    st.dev = DevReport(creator="Dev222", balance_verified=True, balance_source="Solana RPC", current_pct=12,
                       status="MAJOR SELL", sold_pct=60, history_verified=True, prev_tokens_count=25,
                       prev_graduated=0, prev_dead=25)
    h = TokenHistory()
    now = time.time()
    ingest_market(st, parse_pair(dex_pair("Mint222", price="0.0003", mc=300_000, fdv=300_000, liq=9_000,
                                          m5=(500, 900), h1=(2000, 2000), vol=(1_200_000, 1_300_000), pc5=150.0)),
                  {}, SOL_USD, h, now)
    evaluate(st, Settings(), h, now, SOL_USD)
    return st
