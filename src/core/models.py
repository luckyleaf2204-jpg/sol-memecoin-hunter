"""Plain data models shared by every module. DATA lives here; INTERPRETATION lives in scoring/risk/intel.

Rules:
  * a value that was not reported or failed validation is None — never 0
  * None is displayed as UNKNOWN / NOT AVAILABLE and is never scored as good or bad
  * user-facing text is NOT stored here: explanations carry an i18n key + params
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

VALID, PARTIAL, INVALID = "VALID", "PARTIAL", "INVALID"
MIN_PACE_WINDOW_MIN = 10   # below this, "acceleration vs average pace" is not measurable
UNKNOWN_STATE = "UNKNOWN"


@dataclass
class SourceStamp:
    source: str
    updated_at: float

    def age(self, now: float | None = None) -> float:
        return (now or time.time()) - self.updated_at


@dataclass
class TokenInfo:
    mint: str
    name: str = ""
    symbol: str = ""
    creator: str = ""
    created_at: float | None = None        # unix seconds
    image: str = ""
    twitter: str = ""
    telegram: str = ""
    website: str = ""
    bonding_curve: str = ""
    pool: str = ""
    complete: bool | None = None           # True = graduated from bonding curve
    decimals: int = 6
    total_supply: float | None = None      # UI units
    real_sol_reserves: float | None = None     # SOL in bonding curve, as reported by Pump.fun
    virtual_sol_reserves: float | None = None
    real_token_reserves: float | None = None
    curve_progress: float | None = None    # 0-100
    pump_usd_mc: float | None = None
    ath_usd_mc: float | None = None        # Pump.fun-reported all-time-high market cap (USD)
    sol_price: float | None = None
    pump_updated_at: float | None = None   # when Pump.fun data above was fetched
    dev_initial_buy: float | None = None   # tokens bought by creator in create tx (PumpPortal)
    dev_initial_sol: float | None = None
    discovery_mc_sol: float | None = None  # PumpPortal create event: market cap in QUOTE units (SOL for classic curves)
    quote_mint: str = ""                   # Pump.fun record: curve quote mint ("" = unknown)
    sources: set[str] = field(default_factory=set)
    discovered_at: float = field(default_factory=time.time)

    def merge(self, other: "TokenInfo") -> None:
        """Fill/refresh fields from another observation without erasing known data."""
        for k, v in other.__dict__.items():
            if k in ("mint", "discovered_at"):
                continue
            if k == "sources":
                self.sources |= v
            elif v not in (None, "", set()):
                setattr(self, k, v)


@dataclass
class MarketData:
    price_usd: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    liquidity_usd: float | None = None
    liquidity_source: str = ""             # "dexscreener_amm" | "pumpfun_curve" | ""
    vol_5m: float | None = None
    vol_1h: float | None = None
    vol_6h: float | None = None
    vol_24h: float | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    buys_1h: int | None = None
    sells_1h: int | None = None
    price_change_5m: float | None = None
    price_change_1h: float | None = None
    price_change_6h: float | None = None
    price_change_24h: float | None = None
    dex_id: str = ""
    pair_address: str = ""
    quote_symbol: str = ""
    quote_address: str = ""
    base_symbol: str = ""                  # DexScreener baseToken (address == this CA) — identity check
    base_name: str = ""
    pair_created_at: float | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def is_curve(self) -> bool:
        return self.dex_id == "pumpfun"

    @property
    def txns_5m(self) -> int | None:
        if self.buys_5m is None or self.sells_5m is None:
            return None
        return self.buys_5m + self.sells_5m

    @property
    def buy_sell_ratio_5m(self) -> float | None:
        if self.buys_5m is None or not self.sells_5m:
            return None  # undefined without sells — never invent a ratio
        return self.buys_5m / self.sells_5m

    @property
    def mc_liq_ratio(self) -> float | None:
        if self.market_cap and self.liquidity_usd:
            return self.market_cap / self.liquidity_usd
        return None

    @property
    def pace_window_min(self) -> float | None:
        """Minutes actually covered by DexScreener's "h1" window: the pair's age, capped at 60.
        For a 4-minute-old pair, h1 volume == 5m volume; dividing by 12 slices would fake a 12× spike."""
        if self.pair_created_at is None:
            return None
        return max(0.0, min(60.0, (self.updated_at - self.pair_created_at) / 60))

    def _accel(self, now: float | None, window_total: float | None) -> float | None:
        w = self.pace_window_min
        if now is None or not window_total or w is None or w < MIN_PACE_WINDOW_MIN:
            return None
        return now / (window_total / (w / 5))

    @property
    def vol_accel(self) -> float | None:
        """5m volume vs the average 5m slice of the pair's last hour (or its life if younger); 1.0 = steady."""
        return self._accel(self.vol_5m, self.vol_1h)

    @property
    def txn_accel(self) -> float | None:
        if self.buys_1h is None or self.sells_1h is None:
            return None
        return self._accel(self.txns_5m, self.buys_1h + self.sells_1h)

    @property
    def buy_accel(self) -> float | None:
        return self._accel(self.buys_5m, self.buys_1h)


@dataclass
class HolderInfo:
    owner: str
    amount: float
    pct: float
    tags: list[str] = field(default_factory=list)


@dataclass
class HolderStats:
    holder_count: int | None = None
    holder_count_capped: bool = False
    top: list[HolderInfo] = field(default_factory=list)   # excludes LP/curve accounts
    top5_pct: float | None = None
    top10_pct: float | None = None
    top20_pct: float | None = None
    top50_pct: float | None = None
    max_single_pct: float | None = None
    excluded_pct: float = 0.0                              # held by curve/pool/burn
    creator_pct: float | None = None
    source: str = ""
    complete_list: bool = False                            # True = every holder seen (DAS), not just top 20
    valid: bool = True                                     # D6: False = data inconsistent, must not be used
    invalid_reason: str = ""
    owner_amounts: dict[str, float] = field(default_factory=dict, repr=False)  # non-excluded owners
    fetched_at: float = field(default_factory=time.time)


@dataclass
class DevReport:
    creator: str
    balance_verified: bool = False         # True only if the RPC actually answered the balance query
    balance_source: str = ""
    sol_balance: float | None = None
    current_tokens: float | None = None
    current_pct: float | None = None
    initial_buy: float | None = None
    sold_pct: float | None = None
    status: str = "UNKNOWN"
    history_verified: bool = False
    prev_tokens_count: int | None = None
    prev_graduated: int | None = None
    prev_dead: int | None = None           # prior tokens whose ATH MC < $10K
    prev_best_ath: float | None = None
    median_launch_gap_h: float | None = None
    prev_tokens: list[dict] = field(default_factory=list)
    funding_wallet: str | None = None
    funding_sol: float | None = None
    funding_note: str = ""
    notes: list[str] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)


@dataclass
class Issue:
    severity: str        # "critical" -> INVALID, "warning" -> lowers quality
    field: str
    key: str             # i18n key under "issue."
    params: dict = field(default_factory=dict)


@dataclass
class DataQuality:
    score: int
    status: str          # VALID | PARTIAL | INVALID
    issues: list[Issue] = field(default_factory=list)


@dataclass
class Metric:
    """One observed or computed value with full provenance."""
    key: str                     # i18n key under "metric."
    value: Any                   # None = UNKNOWN
    kind: str                    # usd | pct | mult | ratio | int | sec | min | text | bool | sol
    section: str                 # overview | market | momentum | holders | dev | whales | liquidity | ...
    source: str = ""
    ts: float | None = None
    confidence: float | None = None   # 0-1; None when value is None
    derived: bool = False             # computed by us (from snapshots / formulas) vs reported by an API
    note: str = ""                    # i18n key under "note." explaining UNKNOWN / derivation

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass
class Factor:
    key: str                     # i18n key under "factor."
    points: float
    max_points: float
    available: bool = True
    value: str = ""              # language-neutral formatted observation ("2.4×", "$52K")
    source: str = ""
    note: str = ""               # i18n key under "note." when unavailable


@dataclass
class SubScore:
    key: str                     # i18n key under "score."
    score: int | None            # None = NOT AVAILABLE / UNKNOWN (never 0-by-default)
    factors: list[Factor] = field(default_factory=list)
    note: str = ""               # i18n key when score is None

    @property
    def coverage_pct(self) -> int:
        full = sum(f.max_points for f in self.factors)
        avail = sum(f.max_points for f in self.factors if f.available)
        return round(100 * avail / full) if full else 0


@dataclass
class OpportunityResult:
    total: int
    coverage_pct: int                                  # share of Opportunity weights with data
    parts: list[tuple[str, float, int | None]]         # (subscore key, weight, subscore or None)
    contributions: list[tuple[str, float, str, str]]   # (factor key, points toward total, value, source)


@dataclass
class RiskFactor:
    key: str                     # i18n key under "risk."
    points: int
    category: str                # data | liquidity | holders | dev | manipulation | rug | age | social
    params: dict = field(default_factory=dict)
    source: str = ""


@dataclass
class RiskResult:
    score: int
    level: str
    factors: list[RiskFactor] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)          # i18n keys under "missing."
    categories: dict[str, int] = field(default_factory=dict)  # category -> 0-100


@dataclass
class SignalHit:
    key: str                     # i18n key under "signal."
    fired: bool | None           # None = not computable (insufficient history / no data)
    strength: float | None       # 0-1
    weight: float
    value: str = ""
    source: str = ""
    note: str = ""
    raw: dict = field(default_factory=dict)      # diagnostics only: the exact inputs (never used for scoring)


@dataclass
class EarlySignal:
    strength: int | None         # 0-100, None = UNKNOWN (insufficient history)
    is_early: bool | None
    transition: bool | None      # low-activity baseline -> current activity
    fired_count: int = 0
    coverage_pct: int = 0
    history_min: float = 0.0
    signals: list[SignalHit] = field(default_factory=list)
    note: str = ""
    suppressed: list[str] = field(default_factory=list)   # D4: reasons EARLY was held back by risk
    groups_computable: int = 0                            # how many of the 7 signal groups had valid data


@dataclass
class LiquidityIntel:
    state: str = UNKNOWN_STATE   # GROWING | STABLE | FALLING | SHOCK | UNKNOWN
    change_5m_pct: float | None = None
    change_15m_pct: float | None = None
    max_drop_5m_pct: float | None = None
    slippage_1k_pct: float | None = None
    slippage_5k_pct: float | None = None
    exit_liquidity_usd: float | None = None
    pool_age_min: float | None = None
    vol_liq_ratio: float | None = None


@dataclass
class HolderIntel:
    growth_5m_pct: float | None = None
    growth_15m_pct: float | None = None
    prev_growth_5m_pct: float | None = None
    accel: float | None = None                 # growth last 5m minus growth previous 5m (pct points)
    count_now: int | None = None
    abs_growth_5m: int | None = None           # holders added vs ~5 min earlier (absolute)
    abs_growth_15m: int | None = None
    new_holders: int | None = None
    lost_holders: int | None = None
    new_per_min: float | None = None
    churn_pct: float | None = None
    early_retention_pct: float | None = None
    early_snapshot_age_min: float | None = None
    dust_share_new_pct: float | None = None
    whale_count: int | None = None
    organic: str = UNKNOWN_STATE               # ORGANIC | SUSPICIOUS | UNKNOWN
    flags: list[str] = field(default_factory=list)   # i18n keys under "holderflag."


@dataclass
class WhaleIntel:
    state: str = UNKNOWN_STATE   # ACCUMULATION | DISTRIBUTION | NEUTRAL | UNKNOWN
    whale_count: int | None = None
    whale_pct: float | None = None
    delta_pct: float | None = None             # change of whale-held supply between holder snapshots
    window_min: float | None = None
    holder_count: int | None = None
    holder_increase: int | None = None         # absolute holder change over the same window (D3)
    entries: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)


@dataclass
class TokenIdentity:
    """Canonical identity of ONE contract address.

    claims: source -> (symbol, name). CA-keyed sources (looked up BY this address) are canonical:
      helius (on-chain metadata via DAS getAsset), pumpfun (Pump.fun record of this mint),
      dexscreener (pair whose baseToken.address == this CA).
    Discovery feeds (pumpportal) only CLAIM a symbol/name; they are never trusted on their own.
    status: UNVERIFIED (no canonical source yet) | VERIFIED | CONFLICT (sources disagree on the symbol)."""
    claims: dict[str, tuple[str, str]] = field(default_factory=dict)
    status: str = "UNVERIFIED"
    symbol: str = ""
    name: str = ""
    reason: str = ""
    helius_checked: bool = False
    token_program: str = ""
    extensions: list[str] = field(default_factory=list)
    mint_authority: str = ""               # "" = revoked (only meaningful when helius_checked)
    freeze_authority: str = ""


@dataclass
class McTrack:
    """Market-cap journey as WE observed it.

    `initial_mc` is the ANCHOR: the MC at discovery, taken from the discovery source when it reports one
    (PumpPortal create event / Pump.fun list), otherwise the first VALIDATED DexScreener MC. It is written
    once and never overwritten — not by later MC, not by a new pair after graduation/migration, not by a
    restart or re-discovery (SQLite COALESCE + in-memory stash)."""
    first_seen: float                      # when the scanner discovered the token
    initial_mc: float | None = None
    initial_ts: float | None = None
    initial_source: str = ""
    ath_mc: float | None = None            # highest validated MC we observed
    ath_ts: float | None = None
    # (ts, mc, tag) milestones: tag "initial" | "" (>= 30 % move) | "migrate" (first MC on a new pair)
    path: list[tuple] = field(default_factory=list)
    last_pair: str = ""                    # DexScreener pair of the last observation
    last_mc: float | None = None
    migrations: list[dict] = field(default_factory=list)   # {ts, from, to, mc_before, mc_after}
    pending: dict | None = None            # PumpPortal MC waiting for "quote is SOL" confirmation (not persisted)
    dirty: bool = False                    # needs saving

    def gain_x(self, current: float | None) -> float | None:
        if current is None or not self.initial_mc:
            return None
        return current / self.initial_mc


@dataclass
class Event:
    ts: float
    mint: str
    symbol: str
    type: str                    # VOLUME_SPIKE | BUY_PRESSURE_SPIKE | ... (i18n under "event.")
    severity: str                # positive | info | warning | critical
    params: dict = field(default_factory=dict)
    source: str = ""


@dataclass
class TokenState:
    info: TokenInfo
    market: MarketData | None = None
    holders: HolderStats | None = None
    dev: DevReport | None = None
    quality: DataQuality | None = None
    score: OpportunityResult | None = None     # Opportunity; None = not rankable (INVALID data)
    risk: RiskResult | None = None
    early: EarlySignal | None = None
    lifecycle: str = UNKNOWN_STATE
    subscores: dict[str, SubScore] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    liquidity_intel: LiquidityIntel | None = None
    holder_intel: HolderIntel | None = None
    whale_intel: WhaleIntel | None = None
    narratives: list[str] = field(default_factory=list)
    recent_events: list[Event] = field(default_factory=list)
    market_issues: list[Issue] = field(default_factory=list)
    stamps: dict[str, SourceStamp] = field(default_factory=dict)
    filter_fails: list[str] = field(default_factory=list)
    holder_status: str = ""           # no_key | pending | ok | failed  (why holder data is / isn't there)
    holder_error: str = ""            # last Helius/RPC error for this token (no secrets)
    watch: bool = False
    snapshotted: bool = False
    last_deep: float = 0.0
    mc_track: McTrack | None = None
    identity: TokenIdentity = field(default_factory=TokenIdentity)
    pre_early: Any = None                                 # intel.pre_early.PreEarly (1–3 min old tokens)
    trend: dict = field(default_factory=dict)            # display/priority deltas (never used for scoring)
    group: str = ""                                       # opportunity | watch | nodata | excluded | quiet
    group_reasons: list[str] = field(default_factory=list)
    priority_reasons: list[str] = field(default_factory=list)
    refreshed: dict[str, float] = field(default_factory=dict)   # tier -> last fetch time (market/holders/dev)

    @property
    def mint(self) -> str:
        return self.info.mint

    @property
    def age_minutes(self) -> float | None:
        created = self.info.created_at or (self.market.pair_created_at if self.market else None)
        return (time.time() - created) / 60 if created else None

    @property
    def dq_status(self) -> str:
        return self.quality.status if self.quality else INVALID

    @property
    def holder_growth_pct(self) -> float | None:
        return self.holder_intel.growth_15m_pct if self.holder_intel else None

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)

    @property
    def links(self) -> dict[str, str]:
        m = self.mint
        return {
            "pumpfun": f"https://pump.fun/coin/{m}",
            "dexscreener": f"https://dexscreener.com/solana/{m}",
            "solscan": f"https://solscan.io/token/{m}",
        }
