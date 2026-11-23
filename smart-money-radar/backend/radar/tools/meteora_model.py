"""Meteora DAMM v2 (cp-amm) and Dynamic Bonding Curve swap events -> pool state (research tool).

Both programs emit `EvtSwap2` through a self-CPI (anchor emit_cpi): the inner instruction data is
  e445a52e51cb9a1d | sha256("event:EvtSwap2")[:8] | payload
The swap result carries the post-swap sqrt price (Q64.64, raw quote per raw base). Within a price
range both venues behave like a concentrated-liquidity curve with liquidity L:
  quote in  = L * (s1 - s0)          base out = L * (1/s0 - 1/s1)
so each trade gives its pre-swap sqrt price s0 and the local liquidity L; a copy is priced on the
exact state (s, L) at the copy's slot. Layouts are checked against the wallet's balance changes
(see validate_*) before any result is used.
"""
from __future__ import annotations

import hashlib
import struct

from ..solana.base58 import b58decode

DAMM = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"
DBC = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
EMIT_CPI = bytes.fromhex("e445a52e51cb9a1d")
EVT_SWAP2 = hashlib.sha256(b"event:EvtSwap2").digest()[:8]
Q64 = 2.0 ** 64


def _u64(b, o):
    return struct.unpack_from("<Q", b, o)[0], o + 8


def _u128(b, o):
    lo, hi = struct.unpack_from("<QQ", b, o)
    return (hi << 64) | lo, o + 16


def _pk(b, o):
    from ..solana.base58 import b58encode
    return b58encode(b[o:o + 32]), o + 32


def decode_damm_swap2(p: bytes) -> dict:
    o = 0
    pool, o = _pk(p, o)
    direction, cfm, ref = p[o], p[o + 1], p[o + 2]
    o += 3
    a0, o = _u64(p, o)
    a1, o = _u64(p, o)
    mode = p[o]
    o += 1
    incl_in, o = _u64(p, o)
    excl_in, o = _u64(p, o)
    left, o = _u64(p, o)
    out, o = _u64(p, o)
    nsp, o = _u128(p, o)
    tfee, o = _u64(p, o)
    pfee, o = _u64(p, o)
    partner, o = _u64(p, o)
    rfee, o = _u64(p, o)
    tf_in, o = _u64(p, o)
    tf_out, o = _u64(p, o)
    ex_tf_out, o = _u64(p, o)
    ts, o = _u64(p, o)
    ra, o = _u64(p, o)
    rb, o = _u64(p, o)
    return {"venue": "damm", "pool": pool, "direction": direction, "collect_fee_mode": cfm, "incl_in": incl_in,
            "excl_in": excl_in, "out": out, "next_sqrt": nsp, "trading_fee": tfee, "protocol_fee": pfee,
            "partner_fee": partner, "referral_fee": rfee, "ts": ts, "reserve_a": ra, "reserve_b": rb, "mode": mode,
            "amount_0": a0, "amount_1": a1}


def decode_dbc_swap2(p: bytes) -> dict:
    o = 0
    pool, o = _pk(p, o)
    config, o = _pk(p, o)
    direction, ref = p[o], p[o + 1]
    o += 2
    a0, o = _u64(p, o)
    a1, o = _u64(p, o)
    mode = p[o]
    o += 1
    incl_in, o = _u64(p, o)
    excl_in, o = _u64(p, o)
    left, o = _u64(p, o)
    out, o = _u64(p, o)
    nsp, o = _u128(p, o)
    tfee, o = _u64(p, o)
    pfee, o = _u64(p, o)
    rfee, o = _u64(p, o)
    qres, o = _u64(p, o)
    thr, o = _u64(p, o)
    ts, o = _u64(p, o)
    return {"venue": "dbc", "pool": pool, "config": config, "direction": direction, "incl_in": incl_in, "excl_in": excl_in,
            "out": out, "next_sqrt": nsp, "trading_fee": tfee, "protocol_fee": pfee, "referral_fee": rfee,
            "quote_reserve": qres, "migration_threshold": thr, "ts": ts, "mode": mode}


def swap_events(tx: dict, keys: list[str]) -> list[dict]:
    """All Meteora EvtSwap2 events of a transaction, in execution order."""
    out = []
    for g in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ins in g["instructions"]:
            pid = keys[ins["programIdIndex"]]
            if pid not in (DAMM, DBC):
                continue
            raw = b58decode(ins["data"])
            if raw[:8] != EMIT_CPI or raw[8:16] != EVT_SWAP2:
                continue
            try:
                e = decode_damm_swap2(raw[16:]) if pid == DAMM else decode_dbc_swap2(raw[16:])
            except struct.error:
                continue
            e["order"] = (g["index"], len(out))
            out.append(e)
    return out


def curve_state(e: dict, quote_is_b: bool, fee_on_output: bool) -> dict | None:
    """Pre-swap sqrt price s0 and local liquidity L of one trade, from its amounts and post sqrt price.
    Amounts entering/leaving the curve: input = excluded_fee_input; output = out (+ fees when the fee
    is taken from the output). Sqrt prices are real numbers (raw quote per raw base)."""
    s1 = e["next_sqrt"] / Q64
    if s1 <= 0:
        return None
    fee = e["trading_fee"] + e.get("protocol_fee", 0) + e.get("partner_fee", 0) + e.get("referral_fee", 0)
    cin = e["excl_in"]
    cout = e["out"] + (fee if fee_on_output else 0)
    if cin <= 0 or cout <= 0:
        return None
    buy_base = (e["direction"] == 1) if quote_is_b else (e["direction"] == 0)
    if buy_base:            # quote in, base out: price up. q = L(s1-s0), b = L(s1-s0)/(s0 s1)
        q, b = cin, cout
        s0 = q / (b * s1)
        if s0 >= s1:
            return None
        L = q / (s1 - s0)
    else:                   # base in, quote out: price down. b = L(1/s1 - 1/s0) = L(s0-s1)/(s0 s1), q = L(s0-s1)
        b, q = cin, cout
        s0 = q / (b * s1)
        if s0 <= s1:
            return None
        L = q / (s0 - s1)
    return {"s0": s0, "s1": s1, "L": L, "buy": buy_base, "fee_rate": fee / max(1, e["incl_in"] if not fee_on_output else e["out"] + fee)}


def buy_quote(s: float, L: float, quote_in: float) -> tuple[float, float]:
    """base out and new sqrt price for `quote_in` raw quote entering the curve."""
    s_new = s + quote_in / L
    return L * (1 / s - 1 / s_new), s_new


def sell_base(s: float, L: float, base_in: float) -> tuple[float, float]:
    """quote out and new sqrt price for `base_in` raw base entering the curve."""
    inv_new = 1 / s + base_in / L
    s_new = 1 / inv_new
    return L * (s - s_new), s_new
