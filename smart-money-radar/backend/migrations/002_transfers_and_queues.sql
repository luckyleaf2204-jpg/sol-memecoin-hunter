-- SOL transfers involving tracked wallets (funding source / relationship evidence)
CREATE TABLE IF NOT EXISTS sol_transfers (
    signature   text NOT NULL,
    source      text NOT NULL,
    destination text NOT NULL,
    lamports    bigint NOT NULL,
    block_time  timestamptz,
    slot        bigint,
    PRIMARY KEY (signature, source, destination, lamports)
);
CREATE INDEX IF NOT EXISTS sol_transfers_src_idx ON sol_transfers (source, block_time);
CREATE INDEX IF NOT EXISTS sol_transfers_dst_idx ON sol_transfers (destination, block_time);

-- PumpSwap pool -> mints (resolved from pool accounts or migration events)
CREATE TABLE IF NOT EXISTS amm_pools (
    pool        text PRIMARY KEY,
    base_mint   text NOT NULL,
    quote_mint  text NOT NULL,
    source      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS amm_pools_base_idx ON amm_pools (base_mint);

-- whether each trade's event user signed the transaction (filled by the verifier)
ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS user_is_signer boolean;

-- GeckoTerminal backfill bookkeeping per token
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS price_backfill_until timestamptz;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS price_backfill_status text;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS market_cap_usd numeric(24,2);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS liquidity_usd numeric(24,2);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS volume_24h_usd numeric(24,2);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS holder_count int;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS dex_updated_at timestamptz;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS dex_url text;
