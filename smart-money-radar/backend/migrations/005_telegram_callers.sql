-- Telegram Caller Tracker (additive module: new tables only, no change to existing tables).
-- Channels calling Solana / pump.fun tokens, their posts, the calls found in them, the real
-- prices observed after each call and the smart-money activity around it.

CREATE TABLE IF NOT EXISTS tg_channels (
    id             bigserial PRIMARY KEY,
    username       text NOT NULL UNIQUE,          -- public @username (lowercase) or 'id:<chat id>' for client/bot sources
    title          text,
    source         text NOT NULL DEFAULT 'web',   -- web (t.me/s preview) | client (Telethon, owner's account) | bot (channel admin bot)
    enabled        boolean NOT NULL DEFAULT true,
    note           text,
    added_at       timestamptz NOT NULL DEFAULT now(),
    last_msg_id    bigint,                        -- highest message id read
    oldest_msg_id  bigint,                        -- lowest message id read by the history backfill
    backfill_done  boolean NOT NULL DEFAULT false,
    last_poll_at   timestamptz,
    last_ok_at     timestamptz,
    last_error     text,
    error_count    int NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tg_messages (
    channel_id     bigint NOT NULL REFERENCES tg_channels(id) ON DELETE CASCADE,
    msg_id         bigint NOT NULL,
    posted_at      timestamptz NOT NULL,          -- time Telegram shows for the post
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    text           text NOT NULL,
    text_hash      text NOT NULL,                 -- sha256 of the normalised text: edits are detected by a hash change
    edited         boolean NOT NULL DEFAULT false,
    edit_count     int NOT NULL DEFAULT 0,
    previous_texts jsonb NOT NULL DEFAULT '[]'::jsonb,
    deleted_at     timestamptz,                   -- post no longer present when its range was read again
    views          text,
    source         text NOT NULL,
    PRIMARY KEY (channel_id, msg_id)
);
CREATE INDEX IF NOT EXISTS tg_messages_posted_idx ON tg_messages (posted_at DESC);

CREATE TABLE IF NOT EXISTS tg_calls (
    id               bigserial PRIMARY KEY,
    channel_id       bigint NOT NULL REFERENCES tg_channels(id) ON DELETE CASCADE,
    msg_id           bigint NOT NULL,
    mint             text NOT NULL,
    called_at        timestamptz NOT NULL,        -- post time on Telegram
    detected_at      timestamptz NOT NULL DEFAULT now(),
    live             boolean NOT NULL,            -- true: seen while it happened; false: found by the history backfill
    ticker           text,
    name             text,
    stated_mc_usd    numeric,                     -- market cap written in the post (if any)
    stated_price_usd numeric,
    is_repeat        boolean NOT NULL DEFAULT false,   -- the channel had called this mint before
    first_call_id    bigint,                           -- id of the channel's first call of this mint
    venue            text,                        -- pump_curve | pumpswap | raydium | meteora | other (at call time, if known)
    status           text NOT NULL DEFAULT 'tracking', -- tracking | done | unpriced | invalid
    invalid_reason   text,
    UNIQUE (channel_id, msg_id, mint)
);
CREATE INDEX IF NOT EXISTS tg_calls_mint_idx ON tg_calls (mint, called_at);
CREATE INDEX IF NOT EXISTS tg_calls_channel_idx ON tg_calls (channel_id, called_at DESC);
CREATE INDEX IF NOT EXISTS tg_calls_status_idx ON tg_calls (status, called_at);

-- Real prices at the call and after it. Every row keeps the time the price belongs to and its
-- source; a mark is never filled with a price observed at another time.
CREATE TABLE IF NOT EXISTS tg_call_marks (
    call_id        bigint NOT NULL REFERENCES tg_calls(id) ON DELETE CASCADE,
    offset_min     int NOT NULL,                  -- 0 (entry), 5, 15, 30, 60, 120, 360, 720, 1440
    due_at         timestamptz NOT NULL,
    observed_at    timestamptz,                   -- time of the price actually used
    price_usd      numeric,
    price_sol      numeric,
    mc_usd         numeric,
    liquidity_usd  numeric,
    source         text,                          -- stream | dexscreener | geckoterminal_1m
    lag_s          numeric,                       -- observed_at - due_at
    status         text NOT NULL DEFAULT 'pending', -- pending | ok | missing
    PRIMARY KEY (call_id, offset_min)
);
CREATE INDEX IF NOT EXISTS tg_call_marks_due_idx ON tg_call_marks (status, due_at);

CREATE TABLE IF NOT EXISTS tg_call_outcomes (
    call_id          bigint PRIMARY KEY REFERENCES tg_calls(id) ON DELETE CASCADE,
    entry_price_usd  numeric,
    entry_source     text,
    ath_price_usd    numeric,
    ath_at           timestamptz,
    ath_multiple     numeric,
    min_price_usd    numeric,
    max_drawdown     numeric,                     -- lowest price after the call / entry - 1
    roi_24h          numeric,
    x2_at            timestamptz,
    x3_at            timestamptz,
    x5_at            timestamptz,
    rugged           boolean NOT NULL DEFAULT false,
    rug_reason       text,
    pre_call_run     numeric,                     -- price at call / lowest price of the 60 min before - 1
    pre_call_6h_mult numeric,                     -- price at call / lowest price of the 6 h before
    candles          int,                         -- 1-minute candles used
    coverage         numeric,                     -- share of the 24 h covered by candles
    finalized_at     timestamptz NOT NULL DEFAULT now(),
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS tg_call_sm (
    call_id          bigint PRIMARY KEY REFERENCES tg_calls(id) ON DELETE CASCADE,
    wallets_known    int NOT NULL,                -- smart/tracked/watched wallets in the database at check time
    buyers_before    int NOT NULL DEFAULT 0,      -- distinct wallets buying in the 6 h before the call
    buyers_after     int NOT NULL DEFAULT 0,      -- ... in the 6 h after
    sellers_30m      int NOT NULL DEFAULT 0,      -- ... selling in the 30 min after
    sol_before       numeric NOT NULL DEFAULT 0,
    sol_after        numeric NOT NULL DEFAULT 0,
    sol_sold_30m     numeric NOT NULL DEFAULT 0,
    first_sm_buy_at  timestamptz,
    big_buy_before   boolean,                     -- a >= 5 SOL buy (any wallet) happened before the call
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at       timestamptz NOT NULL DEFAULT now()
);
