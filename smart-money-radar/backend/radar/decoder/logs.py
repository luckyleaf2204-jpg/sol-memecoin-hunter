"""Decode pump.fun / PumpSwap events from program logs (logsSubscribe stream).

The pump.fun program writes each event both as a `Program data:` log line and as an
emit_cpi self-invocation. Logs arrive in real time through `logsSubscribe`, so the stream
can decode every curve trade without fetching the transaction. Records produced here are
`partially_verified`: decoded from on-chain program output, but the wallet's balance
change has not been reconciled yet — the verifier does that with getTransaction.
"""
from __future__ import annotations

import base64
import binascii
import re

from ..solana.constants import PUMP_AMM_PROGRAM, PUMP_DECIMALS, PUMP_PROGRAM, PUMP_TOTAL_SUPPLY_RAW
from .events import AmmSwapEvent, PumpTradeEvent, decode_event
from .models import LAMPORTS, PARTIAL, TradeRecord

_INVOKE = re.compile(r"^Program (\w+) invoke \[(\d+)\]")
_DONE = re.compile(r"^Program (\w+) (success|failed)")

EVENT_PROGRAMS = {PUMP_PROGRAM, PUMP_AMM_PROGRAM}


def events_from_logs(logs: list[str]) -> tuple[list, bool]:
    """Return ([(program_id, event), ...] in execution order, truncated_flag)."""
    stack: list[str] = []
    out = []
    truncated = False
    for line in logs or []:
        m = _INVOKE.match(line)
        if m:
            stack.append(m.group(1))
            continue
        m = _DONE.match(line)
        if m:
            if stack and stack[-1] == m.group(1):
                stack.pop()
            continue
        if line.startswith("Log truncated"):
            truncated = True
            continue
        if line.startswith("Program data: ") and stack and stack[-1] in EVENT_PROGRAMS:
            try:
                raw = base64.b64decode(line[14:], validate=False)
            except (binascii.Error, ValueError):
                continue
            ev = decode_event(raw)
            if ev is not None:
                out.append((stack[-1], ev))
    return out, truncated


def curve_prices(ev: PumpTradeEvent, decimals: int = PUMP_DECIMALS) -> dict:
    """Spot price after the trade from the curve's virtual reserves (SOL per token)."""
    spot = None
    if ev.virtual_token_reserves > 0:
        spot = (ev.virtual_sol_reserves / LAMPORTS) / (ev.virtual_token_reserves / 10 ** decimals)
    eff = None
    if ev.token_amount > 0:
        eff = (ev.sol_amount / LAMPORTS) / (ev.token_amount / 10 ** decimals)
    mc = spot * (PUMP_TOTAL_SUPPLY_RAW / 10 ** decimals) if spot is not None else None
    return {"spot": spot, "effective": eff, "mc": mc, "liquidity": ev.real_sol_reserves / LAMPORTS}


def trade_record_from_curve_event(signature: str, slot: int, ordinal: int, ev: PumpTradeEvent,
                                  source: str = "rpc_logs") -> TradeRecord:
    p = curve_prices(ev)
    return TradeRecord(
        signature=signature, event_key=f"trade:{ordinal}", wallet=ev.user, mint=ev.mint, slot=slot,
        block_time=ev.timestamp, tx_type="buy" if ev.is_buy else "sell", venue="pump_curve",
        sol_amount=ev.sol_amount / LAMPORTS, token_amount=ev.token_amount / 10 ** PUMP_DECIMALS,
        token_amount_raw=ev.token_amount, decimals=PUMP_DECIMALS,
        fee_trading_sol=(ev.fee + ev.creator_fee) / LAMPORTS,
        price_sol=p["spot"], effective_price_sol=p["effective"], market_cap_sol=p["mc"],
        liquidity_sol=p["liquidity"], source=source, verification_status=PARTIAL,
        verification_note="decoded from program logs; balance reconciliation pending",
        extra={"creator": ev.creator, "real_token_reserves": ev.real_token_reserves,
               "virtual_sol_reserves": ev.virtual_sol_reserves, "ix_name": ev.ix_name,
               "fee_bps": ev.fee_basis_points + ev.creator_fee_basis_points})


def amm_trade_record(signature: str, slot: int, ordinal: int, ev: AmmSwapEvent, base_mint: str,
                     quote_mint: str, base_decimals: int = PUMP_DECIMALS, supply_ui: float | None = None,
                     source: str = "rpc_logs") -> TradeRecord | None:
    """Map a PumpSwap event to a token buy/sell priced in SOL. Returns None for non-SOL pools."""
    from ..solana.constants import WSOL_MINT

    fees = ev.lp_fee + ev.protocol_fee + ev.coin_creator_fee
    if quote_mint == WSOL_MINT:
        mint = base_mint
        tok_raw = ev.base_amount
        tx_type = "buy" if ev.side == "buy" else "sell"
        if tx_type == "buy":
            # Two buy flavours exist on mainnet: exact-base-out (user pays quote_in + fees) and
            # exact-quote-in (fees deducted from the input). In both, the larger of the two
            # quote fields is what the user paid and (paid - fees) is what entered the pool.
            sol = max(ev.quote_amount, ev.user_quote_amount) - fees
        else:
            sol = ev.quote_amount  # gross out; the user receives quote_amount - fees
        fee_sol = fees / LAMPORTS
        spot = ((ev.pool_quote_reserves / LAMPORTS) / (ev.pool_base_reserves / 10 ** base_decimals)
                if ev.pool_base_reserves else None)
        liq = ev.pool_quote_reserves / LAMPORTS
        note = None
    elif base_mint == WSOL_MINT:
        # reversed pool: base is SOL. SellEvent = user sells SOL for the token (a token buy)
        mint = quote_mint
        tx_type = "buy" if ev.side == "sell" else "sell"
        sol = ev.base_amount
        tok_raw = ev.user_quote_amount if tx_type == "buy" else ev.quote_amount
        fee_sol = 0.0
        spot = ((ev.pool_base_reserves / LAMPORTS) / (ev.pool_quote_reserves / 10 ** base_decimals)
                if ev.pool_quote_reserves else None)
        liq = ev.pool_base_reserves / LAMPORTS
        note = "reversed pool (base=SOL); fees charged in token units, not counted in SOL"
    else:
        return None
    tok = tok_raw / 10 ** base_decimals
    eff = (sol / LAMPORTS) / tok if tok > 0 else None
    mc = spot * supply_ui if (spot is not None and supply_ui) else (
        spot * (PUMP_TOTAL_SUPPLY_RAW / 10 ** PUMP_DECIMALS) if spot is not None else None)
    return TradeRecord(
        signature=signature, event_key=f"trade:{ordinal}", wallet=ev.user, mint=mint, slot=slot,
        block_time=ev.timestamp, tx_type=tx_type, venue="pumpswap", sol_amount=sol / LAMPORTS,
        token_amount=tok, token_amount_raw=tok_raw, decimals=base_decimals, fee_trading_sol=fee_sol,
        price_sol=spot, effective_price_sol=eff, market_cap_sol=mc, liquidity_sol=liq, source=source,
        verification_status=PARTIAL,
        verification_note=note or "decoded from program logs; balance reconciliation pending",
        extra={"pool": ev.pool, "user_quote_amount": ev.user_quote_amount})


def records_from_logs(signature: str, slot: int, logs: list[str], pool_resolver=None) -> dict:
    """Stream-side decoding. Uses the SAME ordinal rule as radar.decoder.tx (every curve or
    AMM trade event increments the ordinal) so the verifier later upserts the same keys."""
    from .events import PumpCompleteEvent, PumpCreateEvent, PumpMigrationEvent

    evs, truncated = events_from_logs(logs)
    out = {"trades": [], "creates": [], "completes": [], "migrations": [], "unresolved_pools": [],
           "truncated": truncated}
    ordinal = 0
    for _prog, ev in evs:
        if isinstance(ev, PumpTradeEvent):
            out["trades"].append(trade_record_from_curve_event(signature, slot, ordinal, ev))
            ordinal += 1
        elif isinstance(ev, AmmSwapEvent):
            mints = pool_resolver(ev.pool) if pool_resolver else None
            if mints:
                rec = amm_trade_record(signature, slot, ordinal, ev, mints[0], mints[1])
                if rec:
                    out["trades"].append(rec)
            else:
                out["unresolved_pools"].append(ev.pool)
            ordinal += 1
        elif isinstance(ev, PumpCreateEvent):
            out["creates"].append(ev)
        elif isinstance(ev, PumpCompleteEvent):
            out["completes"].append(ev)
        elif isinstance(ev, PumpMigrationEvent):
            out["migrations"].append(ev)
    return out
