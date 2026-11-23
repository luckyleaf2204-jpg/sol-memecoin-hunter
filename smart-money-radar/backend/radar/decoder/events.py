"""Binary layouts of pump.fun (bonding curve) and PumpSwap (AMM) Anchor events.

Layouts were checked against real mainnet transactions (see tests/fixtures/tx and
tests/unit/test_decoder.py). Only the stable prefix of each event is required; newer
trailing fields are parsed when present and ignored otherwise, so an IDL extension
does not break decoding.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..solana.base58 import b58encode
from ..solana.constants import (DISC_AMM_BUY, DISC_AMM_SELL, DISC_COMPLETE, DISC_CREATE,
                                DISC_MIGRATION, DISC_TRADE, anchor_event_disc)

DISC_CREATE_V2 = anchor_event_disc("CreateV2Event")


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.o = 0

    def _need(self, n: int) -> None:
        if self.o + n > len(self.d):
            raise EOFError(f"need {n} bytes at {self.o}, have {len(self.d)}")

    def remaining(self) -> int:
        return len(self.d) - self.o

    def pubkey(self) -> str:
        self._need(32)
        v = b58encode(self.d[self.o:self.o + 32])
        self.o += 32
        return v

    def u64(self) -> int:
        self._need(8)
        v = struct.unpack_from("<Q", self.d, self.o)[0]
        self.o += 8
        return v

    def i64(self) -> int:
        self._need(8)
        v = struct.unpack_from("<q", self.d, self.o)[0]
        self.o += 8
        return v

    def u8(self) -> int:
        self._need(1)
        v = self.d[self.o]
        self.o += 1
        return v

    def boolean(self) -> bool:
        return self.u8() != 0

    def string(self, max_len: int = 1000) -> str:
        self._need(4)
        n = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        if n > max_len:
            raise ValueError("string too long")
        self._need(n)
        v = self.d[self.o:self.o + n].decode("utf-8", errors="replace")
        self.o += n
        return v


@dataclass
class PumpTradeEvent:
    mint: str
    sol_amount: int           # lamports moved by the curve, before fees
    token_amount: int         # raw token units (6 decimals on pump.fun)
    is_buy: bool
    user: str
    timestamp: int
    virtual_sol_reserves: int  # after the trade
    virtual_token_reserves: int
    real_sol_reserves: int
    real_token_reserves: int
    fee_recipient: str
    fee_basis_points: int
    fee: int
    creator: str
    creator_fee_basis_points: int
    creator_fee: int
    ix_name: str | None = None
    venue: str = "pump_curve"
    # curves quoted in another token (USDC, ...; seen on 23/11/2026, sol fields are 0 there):
    quote_mint: str | None = None
    quote_amount: int | None = None            # raw quote units moved by the curve, before fees
    virtual_quote_reserves: int | None = None
    real_quote_reserves: int | None = None


@dataclass
class PumpCreateEvent:
    name: str
    symbol: str
    uri: str
    mint: str
    bonding_curve: str
    user: str
    creator: str | None
    timestamp: int | None
    token_program: str | None = None
    is_mayhem: bool | None = None


@dataclass
class PumpCompleteEvent:
    user: str
    mint: str
    bonding_curve: str
    timestamp: int


@dataclass
class PumpMigrationEvent:
    user: str
    mint: str
    mint_amount: int
    sol_amount: int
    pool_migration_fee: int
    bonding_curve: str
    timestamp: int
    pool: str


@dataclass
class AmmSwapEvent:
    side: str                 # 'buy' (BuyEvent: user gets base) | 'sell' (SellEvent: user gives base)
    timestamp: int
    base_amount: int          # base_amount_out (buy) / base_amount_in (sell)
    quote_amount: int         # quote_amount_in (buy) / quote_amount_out (sell), before fees
    user_quote_amount: int    # what the user actually paid (buy) / received (sell)
    pool_base_reserves: int
    pool_quote_reserves: int
    lp_fee: int
    protocol_fee: int
    coin_creator_fee: int
    pool: str
    user: str
    user_base_token_account: str
    user_quote_token_account: str
    coin_creator: str | None
    extra: dict = field(default_factory=dict)


def decode_pump_trade(p: bytes) -> PumpTradeEvent:
    r = Reader(p)
    ev = PumpTradeEvent(
        mint=r.pubkey(), sol_amount=r.u64(), token_amount=r.u64(), is_buy=r.boolean(), user=r.pubkey(),
        timestamp=r.i64(), virtual_sol_reserves=r.u64(), virtual_token_reserves=r.u64(),
        real_sol_reserves=r.u64(), real_token_reserves=r.u64(), fee_recipient=r.pubkey(),
        fee_basis_points=r.u64(), fee=r.u64(), creator=r.pubkey(), creator_fee_basis_points=r.u64(),
        creator_fee=r.u64())
    # trailing (2025+): track_volume bool, total_unclaimed_tokens u64, total_claimed_tokens u64,
    # current_sol_volume u64, last_update_timestamp i64, ix_name string, ...
    try:
        r.boolean(); r.u64(); r.u64(); r.u64(); r.i64()
        ev.ix_name = r.string(64)
    except (EOFError, ValueError):
        return ev
    # trailing (v2 instructions BuyV2/SellV2/buy_exact_quote_in): 37 bytes of flags / cashback
    # fields, then quote_mint, quote_amount, virtual_quote_reserves, real_quote_reserves.
    # Layout established on 23/11/2026 against real transactions: quote_amount equals the user's
    # quote-token balance change exactly for buy/sell (10 of 10) and before fees for
    # buy_exact_quote_in.
    try:
        if r.remaining() >= 37 + 32 + 24:
            for _ in range(37):
                r.u8()
            ev.quote_mint = r.pubkey()
            ev.quote_amount, ev.virtual_quote_reserves, ev.real_quote_reserves = r.u64(), r.u64(), r.u64()
    except (EOFError, ValueError):
        ev.quote_mint = None
    return ev


def decode_pump_create(p: bytes) -> PumpCreateEvent:
    r = Reader(p)
    name, symbol, uri = r.string(), r.string(), r.string()
    mint, curve, user = r.pubkey(), r.pubkey(), r.pubkey()
    creator = ts = None
    token_program = None
    mayhem = None
    try:
        creator = r.pubkey()
        ts = r.i64()
        r.u64(); r.u64(); r.u64(); r.u64()  # virtual/real reserves, total supply
        token_program = r.pubkey()
        mayhem = r.boolean()
    except (EOFError, ValueError):
        pass
    return PumpCreateEvent(name=name, symbol=symbol, uri=uri, mint=mint, bonding_curve=curve, user=user,
                           creator=creator, timestamp=ts, token_program=token_program, is_mayhem=mayhem)


def decode_pump_complete(p: bytes) -> PumpCompleteEvent:
    r = Reader(p)
    return PumpCompleteEvent(user=r.pubkey(), mint=r.pubkey(), bonding_curve=r.pubkey(), timestamp=r.i64())


def decode_pump_migration(p: bytes) -> PumpMigrationEvent:
    r = Reader(p)
    return PumpMigrationEvent(user=r.pubkey(), mint=r.pubkey(), mint_amount=r.u64(), sol_amount=r.u64(),
                              pool_migration_fee=r.u64(), bonding_curve=r.pubkey(), timestamp=r.i64(),
                              pool=r.pubkey())


def _decode_amm(p: bytes, side: str) -> AmmSwapEvent:
    r = Reader(p)
    ts = r.i64()
    base_amount = r.u64()      # base_amount_out | base_amount_in
    limit_quote = r.u64()      # max_quote_amount_in | min_quote_amount_out
    user_base_res = r.u64()
    user_quote_res = r.u64()
    pool_base = r.u64()
    pool_quote = r.u64()
    quote_amount = r.u64()     # quote_amount_in | quote_amount_out
    lp_bps = r.u64()
    lp_fee = r.u64()
    proto_bps = r.u64()
    proto_fee = r.u64()
    quote_with_lp = r.u64()    # quote_amount_in_with_lp_fee | quote_amount_out_without_lp_fee
    user_quote = r.u64()       # user_quote_amount_in | user_quote_amount_out
    pool = r.pubkey()
    user = r.pubkey()
    uba = r.pubkey()
    uqa = r.pubkey()
    pfr = r.pubkey()
    pfra = r.pubkey()
    coin_creator = c_bps = c_fee = None
    try:
        coin_creator = r.pubkey()
        c_bps = r.u64()
        c_fee = r.u64()
    except EOFError:
        pass
    return AmmSwapEvent(side=side, timestamp=ts, base_amount=base_amount, quote_amount=quote_amount,
                        user_quote_amount=user_quote, pool_base_reserves=pool_base,
                        pool_quote_reserves=pool_quote, lp_fee=lp_fee, protocol_fee=proto_fee,
                        coin_creator_fee=c_fee or 0, pool=pool, user=user, user_base_token_account=uba,
                        user_quote_token_account=uqa, coin_creator=coin_creator,
                        extra={"limit_quote": limit_quote, "lp_bps": lp_bps, "protocol_bps": proto_bps,
                               "creator_bps": c_bps, "quote_with_lp": quote_with_lp,
                               "user_base_reserves": user_base_res, "user_quote_reserves": user_quote_res,
                               "protocol_fee_recipient": pfr, "protocol_fee_recipient_ta": pfra})


def decode_event(data: bytes):
    """Decode an Anchor event body (8-byte discriminator + payload). Returns None if unknown."""
    if len(data) < 8:
        return None
    disc, payload = data[:8], data[8:]
    try:
        if disc == DISC_TRADE:
            return decode_pump_trade(payload)
        if disc in (DISC_CREATE, DISC_CREATE_V2):
            return decode_pump_create(payload)
        if disc == DISC_COMPLETE:
            return decode_pump_complete(payload)
        if disc == DISC_MIGRATION:
            return decode_pump_migration(payload)
        if disc == DISC_AMM_BUY:
            return _decode_amm(payload, "buy")
        if disc == DISC_AMM_SELL:
            return _decode_amm(payload, "sell")
    except (EOFError, ValueError, UnicodeDecodeError):
        return None
    return None
