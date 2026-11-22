-- Phase 2: per-wallet token flows aggregated from EVERY decoded trade (not only stored ones).
-- For a token whose creation was seen by the stream (and no stream gap since), bought - sold per
-- wallet is a complete estimate of trade-based holdings (token transfers are not included).
CREATE TABLE IF NOT EXISTS token_wallet_flows (
    mint        text NOT NULL,
    wallet      text NOT NULL,
    bought_raw  numeric(40,0) NOT NULL DEFAULT 0,
    sold_raw    numeric(40,0) NOT NULL DEFAULT 0,
    buy_sol     numeric(30,9) NOT NULL DEFAULT 0,
    sell_sol    numeric(30,9) NOT NULL DEFAULT 0,
    fees_sol    numeric(30,9) NOT NULL DEFAULT 0,
    n_buys      int NOT NULL DEFAULT 0,
    n_sells     int NOT NULL DEFAULT 0,
    first_ts    timestamptz,
    last_ts     timestamptz,
    first_slot  bigint,
    PRIMARY KEY (mint, wallet)
);
CREATE INDEX IF NOT EXISTS twf_last_idx ON token_wallet_flows (last_ts);

-- trading fees (protocol + creator + LP) generated per bar
ALTER TABLE token_prices ADD COLUMN IF NOT EXISTS fees_sol numeric(30,9) NOT NULL DEFAULT 0;

-- true while every trade of the token since its creation went through the stream
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS stream_from_creation boolean NOT NULL DEFAULT false;

-- wallet age / first funder probe (one getSignaturesForAddress, + one getTransaction for young wallets)
CREATE TABLE IF NOT EXISTS wallet_age (
    address            text PRIMARY KEY,
    first_activity_at  timestamptz,          -- exact when history_complete, else a lower bound
    signatures_seen    int NOT NULL,
    history_complete   boolean NOT NULL,
    funder             text,
    funder_signature   text,
    funder_lamports    bigint,
    checked_at         timestamptz NOT NULL DEFAULT now()
);
