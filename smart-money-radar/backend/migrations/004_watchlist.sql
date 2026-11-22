-- Watched wallets (KOL / callers and their side wallets). Every buy of a watched wallet is
-- alerted at once, before any co-buy signal. Labels come from the operator (e.g. copied from
-- a public KOL tracker); suggested side wallets start unconfirmed and are not alerted.
CREATE TABLE IF NOT EXISTS watched_wallets (
    address     text PRIMARY KEY,
    label       text NOT NULL,
    x_handle    text,
    kind        text NOT NULL DEFAULT 'kol',      -- kol | side_wallet | custom
    parent      text,                              -- side wallet: the watched wallet that funded it
    source      text NOT NULL DEFAULT 'manual',    -- manual | funded_by_watched
    note        text,
    confirmed   boolean NOT NULL DEFAULT true,
    added_at    timestamptz NOT NULL DEFAULT now(),
    evidence    jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS watched_wallets_parent_idx ON watched_wallets (parent);
