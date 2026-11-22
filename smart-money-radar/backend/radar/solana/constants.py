"""On-chain program ids and constants.

All values below were checked against mainnet on 2026-11-22:
  * pump.fun Global account 4wTV1Ymi... (owner 6EF8...) decoded:
      initial_virtual_token_reserves = 1_073_000_000_000_000
      initial_virtual_sol_reserves   = 30_000_000_000
      initial_real_token_reserves    =   793_100_000_000_000
      token_total_supply             = 1_000_000_000_000_000 (6 decimals)
      fee_basis_points               = 95 (+ creator fee 30 bps seen in TradeEvent)
  * Event discriminators = sha256("event:<Name>")[:8] (Anchor convention).
The analytics read the live Global account at start-up when possible
(see radar.sources.pumpfun_chain) and only fall back to these numbers.
"""
from __future__ import annotations

import hashlib

LAMPORTS_PER_SOL = 1_000_000_000

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_FEE_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
PUMP_GLOBAL = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

PUMP_DECIMALS = 6
PUMP_TOTAL_SUPPLY_RAW = 1_000_000_000_000_000
PUMP_INITIAL_VIRTUAL_TOKEN = 1_073_000_000_000_000
PUMP_INITIAL_VIRTUAL_SOL = 30_000_000_000
PUMP_INITIAL_REAL_TOKEN = 793_100_000_000_000

# Jito tip accounts (https://docs.jito.wtf/lowlatencytxnsend/#gettipaccounts)
JITO_TIP_ACCOUNTS = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})

# Accounts that are never "wallets" (programs / sysvars / protocol vaults)
NON_WALLET_ACCOUNTS = frozenset({
    PUMP_PROGRAM, PUMP_AMM_PROGRAM, PUMP_FEE_PROGRAM, PUMP_GLOBAL, SYSTEM_PROGRAM,
    TOKEN_PROGRAM, TOKEN_2022_PROGRAM, ATA_PROGRAM, COMPUTE_BUDGET_PROGRAM, WSOL_MINT,
    "SysvarRent111111111111111111111111111111111", "SysvarC1ock11111111111111111111111111111111",
    "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # pump event authority
})


def anchor_event_disc(name: str) -> bytes:
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


def anchor_ix_disc(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


# Anchor emit_cpi! wraps events in a self-invocation whose data starts with this tag
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")

DISC_TRADE = anchor_event_disc("TradeEvent")            # bddb7fd34ee661ee
DISC_CREATE = anchor_event_disc("CreateEvent")          # 1b72a94ddeeb6376
DISC_COMPLETE = anchor_event_disc("CompleteEvent")      # 5f72619cd42e9808
DISC_MIGRATION = anchor_event_disc("CompletePumpAmmMigrationEvent")
DISC_AMM_BUY = anchor_event_disc("BuyEvent")            # 67f4521f2cf57777
DISC_AMM_SELL = anchor_event_disc("SellEvent")          # 3e2f370aa503dc2a
DISC_AMM_CREATE_POOL = anchor_event_disc("CreatePoolEvent")
