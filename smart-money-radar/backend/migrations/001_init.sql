-- Smart Money Radar — initial schema
-- Amounts: SOL in numeric(30,9) (lamport precision), tokens in UI units numeric(40,6) + raw.
-- Every on-chain row carries its transaction signature so it can be checked on an explorer.

CREATE TABLE IF NOT EXISTS wallets (
    address              text PRIMARY KEY,
    status               text NOT NULL DEFAULT 'observed'
                         CHECK (status IN ('observed','candidate','tracked','smart','excluded')),
    first_seen_at        timestamptz NOT NULL DEFAULT now(),
    last_active_at       timestamptz,
    discovery_source     text,
    discovery_detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_excluded          boolean NOT NULL DEFAULT false,
    exclude_reason       text,
    note                 text,
    cluster_id           text,
    cluster_size         int NOT NULL DEFAULT 1,
    funding_source       text,
    funding_signature    text,
    funding_amount_sol   numeric(30,9),
    funding_checked_at   timestamptz,
    backfill_status      text NOT NULL DEFAULT 'none'
                         CHECK (backfill_status IN ('none','queued','running','complete','partial','failed')),
    backfill_from        timestamptz,
    backfill_to          timestamptz,
    backfill_sigs_seen   int NOT NULL DEFAULT 0,
    backfill_sigs_fetched int NOT NULL DEFAULT 0,
    backfill_truncated   boolean NOT NULL DEFAULT false,
    backfill_error       text,
    last_synced_signature text,
    last_synced_at       timestamptz,
    is_token_creator     boolean NOT NULL DEFAULT false,
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wallets_status_idx ON wallets (status);
CREATE INDEX IF NOT EXISTS wallets_cluster_idx ON wallets (cluster_id);

CREATE TABLE IF NOT EXISTS tokens (
    mint                 text PRIMARY KEY,
    name                 text,
    symbol               text,
    uri                  text,
    decimals             int NOT NULL DEFAULT 6,
    token_program        text,
    total_supply_raw     numeric(40,0),
    creator              text,
    created_at           timestamptz,
    created_signature    text,
    created_slot         bigint,
    bonding_curve        text,
    is_mayhem            boolean,
    curve_complete       boolean NOT NULL DEFAULT false,
    curve_completed_at   timestamptz,
    migrated_at          timestamptz,
    migration_signature  text,
    amm_pool             text,
    mint_authority       text,
    freeze_authority     text,
    authorities_checked_at timestamptz,
    last_trade_at        timestamptz,
    last_price_sol       numeric(40,18),
    last_mc_sol          numeric(30,9),
    last_liquidity_sol   numeric(30,9),
    curve_progress       numeric(8,5),
    metadata_source      text,
    first_seen_at        timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tokens_creator_idx ON tokens (creator);
CREATE INDEX IF NOT EXISTS tokens_last_trade_idx ON tokens (last_trade_at DESC);
CREATE INDEX IF NOT EXISTS tokens_pool_idx ON tokens (amm_pool);

-- One row per wallet-level economic event inside a transaction.
-- Idempotency key: (signature, event_key). event_key = 'trade:<n>' (n-th trade event in
-- execution order) or 'xfer:<mint>:<n>' for token transfers.
CREATE TABLE IF NOT EXISTS wallet_transactions (
    signature            text NOT NULL,
    event_key            text NOT NULL,
    wallet               text NOT NULL,
    mint                 text NOT NULL,
    slot                 bigint NOT NULL,
    block_time           timestamptz NOT NULL,
    tx_type              text NOT NULL CHECK (tx_type IN ('buy','sell','transfer_in','transfer_out')),
    venue                text,                        -- pump_curve | pumpswap | transfer
    sol_amount           numeric(30,9) NOT NULL DEFAULT 0,   -- SOL swapped, before fees
    token_amount         numeric(40,6) NOT NULL DEFAULT 0,
    token_amount_raw     numeric(40,0),
    fee_trading_sol      numeric(30,9) NOT NULL DEFAULT 0,   -- protocol + creator + LP fees
    fee_network_sol      numeric(30,9) NOT NULL DEFAULT 0,   -- base + priority fee (if wallet paid)
    tip_sol              numeric(30,9) NOT NULL DEFAULT 0,   -- Jito tips
    other_cost_sol       numeric(30,9) NOT NULL DEFAULT 0,   -- bot/router fees, non-refundable account creation
    rent_sol             numeric(30,9) NOT NULL DEFAULT 0,   -- token-account rent paid(+)/refunded(-), not a cost
    wallet_sol_delta     numeric(30,9),                       -- observed SOL+WSOL change of the wallet
    reconciliation_residual_sol numeric(30,9),
    price_sol            numeric(40,18),                      -- spot price after the trade (reserves)
    effective_price_sol  numeric(40,18),                      -- sol_amount / token_amount
    market_cap_sol       numeric(30,9),
    sol_usd              numeric(18,6),
    liquidity_sol        numeric(30,9),                       -- curve real SOL / pool quote reserves
    counterparty         text,
    fee_payer            text,
    source               text NOT NULL,       -- rpc_tx | rpc_logs | helius_enhanced | pumpportal
    verification_status  text NOT NULL CHECK (verification_status IN ('verified','partially_verified','unverified')),
    verification_note    text,
    ingested_at          timestamptz NOT NULL DEFAULT now(),
    verified_at          timestamptz,
    PRIMARY KEY (signature, event_key)
);
CREATE INDEX IF NOT EXISTS wtx_wallet_time_idx ON wallet_transactions (wallet, block_time);
CREATE INDEX IF NOT EXISTS wtx_mint_time_idx ON wallet_transactions (mint, block_time);
CREATE INDEX IF NOT EXISTS wtx_time_idx ON wallet_transactions (block_time);
CREATE INDEX IF NOT EXISTS wtx_unverified_idx ON wallet_transactions (verification_status)
    WHERE verification_status <> 'verified';

-- Failed transactions we saw for tracked wallets (never counted as trades; kept for audit)
CREATE TABLE IF NOT EXISTS failed_transactions (
    signature   text PRIMARY KEY,
    wallet      text,
    slot        bigint,
    block_time  timestamptz,
    error       jsonb,
    seen_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_transactions (
    signature   text PRIMARY KEY,
    slot        bigint,
    block_time  timestamptz,
    tx          jsonb NOT NULL,
    fetched_from text,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_positions (
    wallet               text NOT NULL,
    mint                 text NOT NULL,
    cycle_no             int NOT NULL,
    status               text NOT NULL CHECK (status IN ('open','closed','written_off')),
    opened_at            timestamptz NOT NULL,
    closed_at            timestamptz,
    first_buy_at         timestamptz,
    last_buy_at          timestamptz,
    first_sell_at        timestamptz,
    n_buys               int NOT NULL DEFAULT 0,
    n_sells              int NOT NULL DEFAULT 0,
    buy_sol              numeric(30,9) NOT NULL DEFAULT 0,  -- Actual Entry Cost
    sell_sol             numeric(30,9) NOT NULL DEFAULT 0,
    fees_sol             numeric(30,9) NOT NULL DEFAULT 0,
    tokens_bought        numeric(40,6) NOT NULL DEFAULT 0,
    tokens_sold          numeric(40,6) NOT NULL DEFAULT 0,
    tokens_in            numeric(40,6) NOT NULL DEFAULT 0,
    tokens_out           numeric(40,6) NOT NULL DEFAULT 0,
    remaining_tokens     numeric(40,6) NOT NULL DEFAULT 0,
    cost_basis_remaining numeric(30,9) NOT NULL DEFAULT 0,
    realized_pnl_sol     numeric(30,9) NOT NULL DEFAULT 0,
    unrealized_pnl_sol   numeric(30,9),
    roi                  numeric(18,6),
    avg_entry_price_sol  numeric(40,18),
    avg_exit_price_sol   numeric(40,18),
    entry_mc_sol         numeric(30,9),
    exit_mc_sol          numeric(30,9),
    current_price_sol    numeric(40,18),
    peak_price_sol       numeric(40,18),
    peak_multiple        numeric(18,6),
    peak_at              timestamptz,
    peak_validated       boolean,
    hit_2x boolean, hit_3x boolean, hit_5x boolean, hit_10x boolean,
    realized_multiple    numeric(18,6),
    price_roi            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- token price change at 5m..7d after entry
    holding_seconds      bigint,
    qualifying           boolean NOT NULL DEFAULT false,       -- buy_sol >= min buy
    verification         text NOT NULL DEFAULT 'unverified',
    flags                jsonb NOT NULL DEFAULT '[]'::jsonb,
    signatures           jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (wallet, mint, cycle_no)
);
CREATE INDEX IF NOT EXISTS wpos_wallet_idx ON wallet_positions (wallet, opened_at DESC);
CREATE INDEX IF NOT EXISTS wpos_mint_idx ON wallet_positions (mint);

CREATE TABLE IF NOT EXISTS wallet_performance (
    wallet               text NOT NULL,
    period_days          int NOT NULL,
    computed_at          timestamptz NOT NULL DEFAULT now(),
    positions_total      int NOT NULL DEFAULT 0,
    positions_qualifying int NOT NULL DEFAULT 0,
    closed_qualifying    int NOT NULL DEFAULT 0,
    open_qualifying      int NOT NULL DEFAULT 0,
    wins int NOT NULL DEFAULT 0,
    losses int NOT NULL DEFAULT 0,
    win_rate             numeric(8,5),
    avg_buy_sol          numeric(30,9),
    median_buy_sol       numeric(30,9),
    largest_buy_sol      numeric(30,9),
    total_buy_sol        numeric(30,9),
    avg_position_sol     numeric(30,9),
    realized_pnl_sol     numeric(30,9),
    unrealized_pnl_sol   numeric(30,9),
    total_pnl_sol        numeric(30,9),
    roi                  numeric(18,6),
    profit_factor        numeric(18,6),
    avg_win_sol          numeric(30,9),
    avg_loss_sol         numeric(30,9),
    max_drawdown_sol     numeric(30,9),
    largest_win_sol      numeric(30,9),
    largest_loss_sol     numeric(30,9),
    hit_rate_2x numeric(8,5), hit_rate_3x numeric(8,5), hit_rate_5x numeric(8,5), hit_rate_10x numeric(8,5),
    avg_realized_roi     numeric(18,6),
    median_realized_roi  numeric(18,6),
    avg_mfe              numeric(18,6),
    avg_holding_seconds  bigint,
    pnl_ex_best_sol      numeric(30,9),
    top_win_share        numeric(8,5),
    verified_share       numeric(8,5),
    coverage_complete    boolean NOT NULL DEFAULT false,
    last_active_at       timestamptz,
    metrics              jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (wallet, period_days)
);

CREATE TABLE IF NOT EXISTS token_prices (
    mint          text NOT NULL,
    bucket_start  timestamptz NOT NULL,
    source        text NOT NULL,              -- onchain_trades | geckoterminal | dexscreener | jupiter
    open_sol      numeric(40,18),
    high_sol      numeric(40,18),
    low_sol       numeric(40,18),
    close_sol     numeric(40,18),
    high_confirmed_sol numeric(40,18),        -- high from trades >= min size (onchain) only
    volume_sol    numeric(30,9) NOT NULL DEFAULT 0,
    volume_usd    numeric(24,2),
    buy_sol       numeric(30,9) NOT NULL DEFAULT 0,
    sell_sol      numeric(30,9) NOT NULL DEFAULT 0,
    trades        int NOT NULL DEFAULT 0,
    liquidity_sol numeric(30,9),
    market_cap_sol numeric(30,9),
    PRIMARY KEY (mint, bucket_start, source)
);
CREATE INDEX IF NOT EXISTS token_prices_time_idx ON token_prices (bucket_start);

CREATE TABLE IF NOT EXISTS sol_usd_prices (
    ts     timestamptz NOT NULL,
    source text NOT NULL,
    price  numeric(18,6) NOT NULL,
    PRIMARY KEY (ts, source)
);

CREATE TABLE IF NOT EXISTS token_holders (
    mint          text NOT NULL,
    snapshot_at   timestamptz NOT NULL,
    rank          int NOT NULL,
    owner         text,
    token_account text NOT NULL,
    amount        numeric(40,6) NOT NULL,
    pct_supply    numeric(10,6),
    is_curve_or_pool boolean NOT NULL DEFAULT false,
    is_creator    boolean NOT NULL DEFAULT false,
    source        text NOT NULL,             -- rpc_largest_accounts | helius_das | trade_estimate
    PRIMARY KEY (mint, snapshot_at, token_account)
);
CREATE INDEX IF NOT EXISTS token_holders_latest_idx ON token_holders (mint, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS token_risk_reports (
    mint         text NOT NULL,
    computed_at  timestamptz NOT NULL DEFAULT now(),
    risk_level   text NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH','UNKNOWN')),
    risk_points  int NOT NULL,
    checks       jsonb NOT NULL,
    PRIMARY KEY (mint, computed_at)
);

CREATE TABLE IF NOT EXISTS smart_money_scores (
    wallet        text NOT NULL,
    period_days   int NOT NULL,
    computed_at   timestamptz NOT NULL DEFAULT now(),
    score         numeric(6,2) NOT NULL,
    category      text NOT NULL,      -- EXCELLENT | STRONG | WATCHLIST | NOT_PRIORITY
    confidence    text NOT NULL,      -- HIGH | MEDIUM | LOW
    qualifies     boolean NOT NULL,   -- passes mandatory criteria
    official_rank int,                -- rank in official Top list (NULL when not eligible)
    components    jsonb NOT NULL,
    reasons       jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (wallet, period_days)
);
CREATE INDEX IF NOT EXISTS sms_rank_idx ON smart_money_scores (period_days, official_rank);

CREATE TABLE IF NOT EXISTS top_wallet_snapshots (
    period_days int NOT NULL,
    computed_at timestamptz NOT NULL,
    rank        int NOT NULL,
    wallet      text NOT NULL,
    score       numeric(6,2) NOT NULL,
    PRIMARY KEY (period_days, computed_at, rank)
);

CREATE TABLE IF NOT EXISTS smart_money_signals (
    id                bigserial PRIMARY KEY,
    mint              text NOT NULL,
    level             text NOT NULL CHECK (level IN ('WATCH','STRONG','VERY_STRONG')),
    detected_at       timestamptz NOT NULL DEFAULT now(),
    window_start      timestamptz NOT NULL,
    window_end        timestamptz NOT NULL,
    raw_buyers        int NOT NULL,
    independent_buyers int NOT NULL,
    total_buy_sol     numeric(30,9) NOT NULL,
    total_sell_sol    numeric(30,9) NOT NULL DEFAULT 0,
    net_flow_sol      numeric(30,9) NOT NULL,
    first_entry_price_sol numeric(40,18),
    price_at_detection_sol numeric(40,18),
    runup_multiple    numeric(18,6),
    market_cap_sol    numeric(30,9),
    liquidity_sol     numeric(30,9),
    risk_level        text,
    avg_buyer_score   numeric(6,2),
    verification      text NOT NULL,
    buyers            jsonb NOT NULL,
    downgrade_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    status            text NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS signals_mint_idx ON smart_money_signals (mint, detected_at DESC);
CREATE INDEX IF NOT EXISTS signals_time_idx ON smart_money_signals (detected_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id           bigserial PRIMARY KEY,
    signal_id    bigint REFERENCES smart_money_signals(id) ON DELETE SET NULL,
    mint         text NOT NULL,
    level        text NOT NULL,
    reason       text NOT NULL,          -- new | upgrade | important_change
    dedupe_key   text NOT NULL,
    channel      text NOT NULL,          -- telegram | dashboard
    status       text NOT NULL,          -- sent | failed | suppressed | dashboard_only
    message      text NOT NULL,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    sent_at      timestamptz,
    seen         boolean NOT NULL DEFAULT false,
    seen_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS alerts_dedupe_idx ON alerts (dedupe_key, channel);
CREATE INDEX IF NOT EXISTS alerts_time_idx ON alerts (created_at DESC);

CREATE TABLE IF NOT EXISTS wallet_relationships (
    wallet_a      text NOT NULL,
    wallet_b      text NOT NULL,
    relation_type text NOT NULL,   -- common_funder | direct_sol_transfer | direct_token_transfer | same_creator | sync_buys | funded_by
    strength      numeric(6,4) NOT NULL,
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (wallet_a, wallet_b, relation_type),
    CHECK (wallet_a < wallet_b)
);
CREATE INDEX IF NOT EXISTS wrel_b_idx ON wallet_relationships (wallet_b);

CREATE TABLE IF NOT EXISTS api_sync_status (
    source         text NOT NULL,
    job            text NOT NULL,
    status         text NOT NULL,
    last_success_at timestamptz,
    last_error_at  timestamptz,
    last_error     text,
    requests_ok    bigint NOT NULL DEFAULT 0,
    requests_failed bigint NOT NULL DEFAULT 0,
    rate_limited   bigint NOT NULL DEFAULT 0,
    details        jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, job)
);

CREATE TABLE IF NOT EXISTS system_settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker     text PRIMARY KEY,
    last_beat  timestamptz NOT NULL,
    started_at timestamptz NOT NULL,
    info       jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    params      jsonb NOT NULL,
    results     jsonb NOT NULL
);

-- TimescaleDB is optional: hypertables only when the extension is available.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable('token_prices', 'bucket_start', if_not_exists => TRUE,
                                  migrate_data => TRUE, chunk_time_interval => INTERVAL '1 day');
    END IF;
END$$;
