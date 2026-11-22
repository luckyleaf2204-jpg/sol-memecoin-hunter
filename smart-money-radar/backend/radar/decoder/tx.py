"""Full transaction decoder (getTransaction, encoding=jsonParsed, maxSupportedTransactionVersion=1).

For every pump.fun / PumpSwap trade event it produces a wallet-level TradeRecord with the
SOL economics separated:

  sol_amount       SOL swapped by the curve/pool, before fees  (Actual Entry Cost for buys)
  fee_trading_sol  pump protocol + creator fee | PumpSwap LP + protocol + creator fee
  fee_network_sol  transaction fee incl. priority fee — only when the wallet paid it
  tip_sol          SOL sent to Jito tip accounts outside the swap
  other_cost_sol   other SOL leaving the wallet outside the swap (bot/router fees,
                   non-refundable account creation such as a token launch)
  rent_sol         lamports locked (+) / released (-) in the wallet's OWN token accounts.
                   Rent is refundable when the account is closed, so it is NOT a trading cost.

Reconciliation: the wallet's observed "SOL wealth" change
    ΔW = Δlamports(wallet) + Σ Δlamports(token accounts owned by the wallet)   (includes WSOL)
is compared with what the events imply:
    expected = Σ(−sol−fee for buys, +sol−fee for sells) − network fee − tips − other costs + inflows
If |ΔW − expected| ≤ 10 000 lamports and the token balance change matches the events,
the record is `verified`; otherwise `partially_verified` with the residual stored.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from ..solana.base58 import b58decode
from ..solana.constants import (EVENT_IX_TAG, JITO_TIP_ACCOUNTS, PUMP_AMM_PROGRAM, PUMP_DECIMALS,
                                PUMP_PROGRAM, SYSTEM_PROGRAM, TOKEN_2022_PROGRAM, TOKEN_PROGRAM,
                                WSOL_MINT)
from .events import (AmmSwapEvent, PumpCompleteEvent, PumpCreateEvent, PumpMigrationEvent,
                     PumpTradeEvent, decode_event)
from .logs import amm_trade_record, events_from_logs, trade_record_from_curve_event
from .models import LAMPORTS, PARTIAL, VERIFIED, DecodedTx, SolTransfer, TokenTransfer, TradeRecord

SWAP_PROGRAMS = {PUMP_PROGRAM, PUMP_AMM_PROGRAM}
TOKEN_PROGRAMS = {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}
RESIDUAL_TOLERANCE_LAMPORTS = 10_000

_signers_cache: dict[str, set[str]] = {}

PoolResolver = Callable[[str], "tuple[str, str] | None"]


def _account_keys(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = msg.get("accountKeys") or []
    out = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    # jsonParsed already lists loaded addresses inside accountKeys (source='lookupTable')
    if keys and not isinstance(keys[0], dict):
        out += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    return out


def _program_id(ix: dict, keys: list[str]) -> str | None:
    if "programId" in ix:
        return ix["programId"]
    idx = ix.get("programIdIndex")
    return keys[idx] if isinstance(idx, int) and idx < len(keys) else None


def _walk_instructions(tx: dict, keys: list[str]):
    """Yield (ix, program_id, ancestors) in execution order."""
    msg = tx["transaction"]["message"]
    inner = {g["index"]: g["instructions"] for g in ((tx.get("meta") or {}).get("innerInstructions") or [])}
    for i, ix in enumerate(msg.get("instructions") or []):
        pid = _program_id(ix, keys)
        yield ix, pid, []
        stack: list[str | None] = [pid]
        for iix in inner.get(i, []):
            ipid = _program_id(iix, keys)
            h = iix.get("stackHeight")
            if isinstance(h, int) and h >= 2:
                stack = stack[:h - 1]
                ancestors = [p for p in stack if p]
                yield iix, ipid, ancestors
                stack.append(ipid)
            else:  # legacy tx without stackHeight: assume direct child of the top-level program
                yield iix, ipid, [pid] if pid else []


class _TokenBook:
    """Per token-account pre/post balances from meta.pre/postTokenBalances."""

    def __init__(self, meta: dict, keys: list[str]):
        self.acc: dict[str, dict[str, Any]] = {}
        for which in ("preTokenBalances", "postTokenBalances"):
            for b in meta.get(which) or []:
                idx = b.get("accountIndex")
                if not isinstance(idx, int) or idx >= len(keys):
                    continue
                a = self.acc.setdefault(keys[idx], {"mint": b.get("mint"), "owner": b.get("owner"),
                                                    "decimals": None, "pre": 0, "post": 0})
                ui = b.get("uiTokenAmount") or {}
                a["decimals"] = ui.get("decimals", a["decimals"])
                a["owner"] = a["owner"] or b.get("owner")
                a["pre" if which == "preTokenBalances" else "post"] = int(ui.get("amount") or 0)

    def owned_by(self, owner: str) -> list[str]:
        return [k for k, v in self.acc.items() if v["owner"] == owner]

    def delta(self, owner: str, mint: str) -> int:
        return sum(v["post"] - v["pre"] for v in self.acc.values() if v["owner"] == owner and v["mint"] == mint)

    def decimals_of(self, mint: str) -> int | None:
        for v in self.acc.values():
            if v["mint"] == mint and v["decimals"] is not None:
                return int(v["decimals"])
        return None


def decode_transaction(tx: dict, signature: str | None = None,
                       pool_resolver: PoolResolver | None = None) -> DecodedTx:
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    sig = signature or (tx["transaction"].get("signatures") or [None])[0]
    fee_payer = keys[0] if keys else None
    dec = DecodedTx(signature=sig, slot=int(tx.get("slot") or 0), block_time=tx.get("blockTime"),
                    fee_payer=fee_payer, fee_lamports=int(meta.get("fee") or 0),
                    failed=meta.get("err") is not None, error=meta.get("err"))
    if dec.failed:
        dec.notes.append("transaction failed on-chain: no balance change is attributed to trades")
        return dec

    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    lam_delta = {k: (post[i] - pre[i]) for i, k in enumerate(keys) if i < len(pre) and i < len(post)}
    book = _TokenBook(meta, keys)

    cpi_events: list[tuple[str, Any]] = []
    raw_sol: list[tuple[str, str, int, bool, str]] = []   # (src, dst, lamports, under_swap, kind)
    raw_tok: list[dict] = []
    created_owner: dict[str, str] = {}      # new account -> owning program
    temp_token_owner: dict[str, str] = {}   # token account -> wallet (from initialize/close ixs)
    for ix, pid, ancestors in _walk_instructions(tx, keys):
        under_swap = any(a in SWAP_PROGRAMS for a in ancestors)
        parsed = ix.get("parsed")
        if pid in SWAP_PROGRAMS and not isinstance(parsed, dict):
            data = ix.get("data")
            if isinstance(data, str) and ancestors and ancestors[-1] == pid:
                try:
                    raw = b58decode(data)
                except ValueError:
                    continue
                if raw[:8] == EVENT_IX_TAG:
                    ev = decode_event(raw[8:])
                    if ev is not None:
                        cpi_events.append((pid, ev))
            continue
        if not isinstance(parsed, dict):
            continue
        typ = parsed.get("type")
        info = parsed.get("info") or {}
        if pid == SYSTEM_PROGRAM and typ in ("transfer", "transferWithSeed", "createAccount",
                                              "createAccountWithSeed"):
            src = info.get("source")
            dst = info.get("destination") or info.get("newAccount")
            lamports = int(info.get("lamports") or 0)
            if typ.startswith("createAccount") and dst:
                created_owner[dst] = info.get("owner") or ""
            if src and dst and lamports:
                raw_sol.append((src, dst, lamports, under_swap, typ))
        elif pid in TOKEN_PROGRAMS and typ in ("initializeAccount", "initializeAccount2",
                                                "initializeAccount3", "closeAccount"):
            acct, owner = info.get("account"), info.get("owner")
            if acct and owner:
                temp_token_owner[acct] = owner
        elif pid in TOKEN_PROGRAMS and typ in ("transfer", "transferChecked"):
            amt = info.get("amount")
            if amt is None:
                amt = (info.get("tokenAmount") or {}).get("amount")
            raw_tok.append({"source": info.get("source"), "destination": info.get("destination"),
                            "mint": info.get("mint"), "amount": int(amt or 0), "under_swap": under_swap})

    events = cpi_events
    if not events:
        # Fallback for transactions without emit_cpi events. Only here can truncated logs
        # hide events, so the flag matters only on this path.
        events, dec.logs_truncated = events_from_logs(meta.get("logMessages") or [])
        if events:
            dec.notes.append("events taken from program logs (no emit_cpi events found)")

    # ---- events -> records -------------------------------------------------------------
    ordinal = 0
    nonsol: list[TradeRecord] = []
    for pid, ev in events:
        if isinstance(ev, PumpTradeEvent):
            rec = trade_record_from_curve_event(sig, dec.slot, ordinal, ev, source="rpc_tx")
            d = book.decimals_of(ev.mint)
            if d is not None and d != PUMP_DECIMALS:
                rec.decimals = d
                rec.token_amount = ev.token_amount / 10 ** d
            if ev.virtual_sol_reserves == 0 and ev.sol_amount == 0:
                # curve quoted in another token (multi-hop): not a SOL trade by itself
                nonsol.append(rec)
            else:
                dec.trades.append(rec)
            ordinal += 1
        elif isinstance(ev, AmmSwapEvent):
            mints = _pool_mints(ev, book, keys, pool_resolver)
            if mints is None:
                dec.notes.append(f"PumpSwap pool {ev.pool}: mints unknown, trade skipped")
                ordinal += 1
                continue
            dec.amm_pools[ev.pool] = mints
            base_dec = book.decimals_of(mints[0] if mints[1] == WSOL_MINT else mints[1]) or PUMP_DECIMALS
            rec = amm_trade_record(sig, dec.slot, ordinal, ev, mints[0], mints[1], base_decimals=base_dec,
                                   source="rpc_tx")
            ordinal += 1
            if rec is None:
                dec.notes.append(f"PumpSwap pool {ev.pool} is not quoted in SOL: skipped")
                continue
            dec.trades.append(rec)
        elif isinstance(ev, PumpCreateEvent):
            dec.creates.append(ev)
        elif isinstance(ev, PumpCompleteEvent):
            dec.completes.append(ev)
        elif isinstance(ev, PumpMigrationEvent):
            dec.migrations.append(ev)

    for (src, dst, lamports, under_swap, _kind) in raw_sol:
        dec.sol_transfers.append(SolTransfer(src, dst, lamports, under_swap))
    if nonsol:
        _collapse_multi_hop(dec, nonsol, book, lam_delta)

    if dec.block_time is None and dec.trades:
        dec.block_time = dec.trades[0].block_time
    signers = _signers(tx, keys)
    for t in dec.trades:
        if dec.block_time:
            t.block_time = int(dec.block_time)
        t.fee_payer = fee_payer
        t.extra["user_is_signer"] = t.wallet in signers
        if t.wallet not in signers:
            _attribute_router_trade(t, signers, book)

    _signers_cache[dec.signature] = signers
    try:
        _reconcile(dec, lam_delta, book, raw_sol, created_owner, temp_token_owner)
    finally:
        _signers_cache.pop(dec.signature, None)
    for t in dec.trades:
        if t.venue == "pump_curve_nonsol":   # valued from the wallet balance, never fully verified
            t.verification_status = PARTIAL
            t.verification_note = ("non-SOL quoted curve (e.g. USDC) bought/sold through a router: SOL amount "
                                   "= the wallet's own SOL balance change in this transaction")
    _token_transfers(dec, book, raw_tok)
    return dec


def _wallet_sol_value(a: TradeRecord, dec: DecodedTx, book: _TokenBook, lam_delta: dict[str, int]) -> float | None:
    """SOL the wallet itself paid (buy) / received (sell) in this tx, when the ONLY token it
    gained or lost is `a.mint` (the quote token, e.g. USDC, only passed through a router).
    ΔW includes its token accounts, so rent of a newly created account nets out."""
    w = a.wallet
    others = {v["mint"] for v in book.acc.values() if v["owner"] == w and v["mint"] not in (a.mint, WSOL_MINT)}
    if any(abs(book.delta(w, m)) > 1 for m in others):
        return None
    d_w = lam_delta.get(w, 0) + sum(lam_delta.get(k, 0) for k in book.owned_by(w))
    if w == dec.fee_payer:
        d_w += dec.fee_lamports
    sol = (-d_w if a.tx_type == "buy" else d_w) / LAMPORTS
    return sol if sol > 0 else None


def _collapse_multi_hop(dec: DecodedTx, nonsol: list[TradeRecord], book: _TokenBook,
                        lam_delta: dict[str, int] | None = None) -> None:
    """Token A traded against token B on a B-quoted curve, and B traded against SOL in the
    same tx while the wallet's B balance did not change (B only passed through): record ONE
    trade of A whose SOL amount is the SOL leg. Without such a leg (B bought elsewhere, e.g. a
    router swapped SOL -> USDC on another DEX), A is valued by the wallet's own SOL balance
    change when A is the only token it gained/lost; otherwise A is dropped (no SOL value)."""
    for a in nonsol:
        legs = [t for t in dec.trades if t.wallet == a.wallet and t.mint != a.mint
                and abs(book.delta(a.wallet, t.mint)) <= 1
                and ((a.tx_type == "sell" and t.tx_type == "sell") or (a.tx_type == "buy" and t.tx_type == "buy"))]
        if len(legs) != 1:
            sol = _wallet_sol_value(a, dec, book, lam_delta or {}) if not legs else None
            if sol is None:
                dec.notes.append(f"{a.mint}: traded on a non-SOL quoted curve; no single SOL leg found, not counted")
                continue
            a.sol_amount = sol
            a.effective_price_sol = (sol / a.token_amount) if a.token_amount else None
            a.price_sol = a.effective_price_sol
            a.market_cap_sol = (a.price_sol * 1e9) if a.price_sol else None
            a.liquidity_sol = None
            a.venue = "pump_curve_nonsol"
            dec.trades.append(a)
            dec.notes.append(f"{a.mint}: non-SOL quoted curve, valued by the wallet's SOL balance change")
            continue
        leg = legs[0]
        dec.trades.remove(leg)
        a.sol_amount = leg.sol_amount
        a.fee_trading_sol = leg.fee_trading_sol
        a.effective_price_sol = (a.sol_amount / a.token_amount) if a.token_amount else None
        a.price_sol = a.effective_price_sol
        a.market_cap_sol = (a.price_sol * 1e9) if a.price_sol else None
        a.liquidity_sol = None
        a.venue = "pump_curve_multihop"
        a.extra["hop_mint"] = leg.mint
        a.extra["hop_event_key"] = leg.event_key
        dec.trades.append(a)
    dec.trades.sort(key=lambda t: int(t.event_key.split(":")[1]))


def _attribute_router_trade(t: TradeRecord, signers: set[str], book: _TokenBook) -> None:
    """The event user is a router / PDA that did not sign. If exactly one signer's token balance
    moved by the event amount in the trade's direction, that signer is the real trader."""
    want = t.token_amount_raw if t.tx_type == "buy" else -t.token_amount_raw
    if want == 0:
        return
    cands = []
    for sgn in signers:
        got = book.delta(sgn, t.mint)
        if got != 0 and (got > 0) == (want > 0) and abs(got - want) <= max(1, abs(want) // 100):
            cands.append(sgn)
    if len(cands) == 1:
        t.extra["router_account"] = t.wallet
        t.wallet = cands[0]
        t.extra["user_is_signer"] = True
        t.extra["attributed_to_signer"] = True


def _signers(tx: dict, keys: list[str]) -> set[str]:
    msg = tx["transaction"]["message"]
    raw = msg.get("accountKeys") or []
    if raw and isinstance(raw[0], dict):
        return {k["pubkey"] for k in raw if k.get("signer")}
    n = (msg.get("header") or {}).get("numRequiredSignatures", 1)
    return set(keys[:n])


def _pool_mints(ev: AmmSwapEvent, book: _TokenBook, keys: list[str],
                resolver: PoolResolver | None) -> tuple[str, str] | None:
    b = book.acc.get(ev.user_base_token_account, {}).get("mint")
    q = book.acc.get(ev.user_quote_token_account, {}).get("mint")
    if b and q:
        return b, q
    # The user's WSOL account is often created and closed inside the tx (no balance entry):
    # use the pool's own two token accounts instead.
    pool_accts = {k: v for k, v in book.acc.items() if v["owner"] == ev.pool}
    pool_mints = {v["mint"] for v in pool_accts.values()}
    if len(pool_mints) == 2:
        if b and b in pool_mints:
            return b, next(m for m in pool_mints if m != b)
        if q and q in pool_mints:
            return next(m for m in pool_mints if m != q), q
        for v in pool_accts.values():  # base = the account whose balance equals the base reserves
            if ev.pool_base_reserves in (v["pre"], v["post"]):
                return v["mint"], next(m for m in pool_mints if m != v["mint"])
    if resolver is not None:
        return resolver(ev.pool)
    return None


def _reconcile(dec: DecodedTx, lam_delta: dict[str, int], book: _TokenBook,
               raw_sol: list[tuple[str, str, int, bool, str]], created_owner: dict[str, str],
               temp_token_owner: dict[str, str]) -> None:
    by_wallet: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in dec.trades:
        by_wallet[t.wallet].append(t)
    # accounts created in this tx that do not belong to the swap programs (e.g. a new mint)
    created_other = {a for a, owner in created_owner.items() if owner not in SWAP_PROGRAMS}
    for wallet, recs in by_wallet.items():
        owned = set(book.owned_by(wallet)) | {a for a, o in temp_token_owner.items() if o == wallet}
        d_w = lam_delta.get(wallet, 0) + sum(lam_delta.get(a, 0) for a in owned)
        wsol_delta = sum(v["post"] - v["pre"] for k, v in book.acc.items()
                         if k in owned and v["mint"] == WSOL_MINT)
        rent = sum(lam_delta.get(a, 0) for a in owned) - wsol_delta
        fee_net = dec.fee_lamports if wallet == dec.fee_payer else 0
        tips = other_out = other_in = swap_sys_out = 0
        for (src, dst, lamports, under_swap, kind) in raw_sol:
            if src == wallet and dst != wallet and dst not in owned:
                if dst in JITO_TIP_ACCOUNTS and not under_swap:
                    tips += lamports
                elif not under_swap or kind.startswith("createAccount") or dst in created_other:
                    # outside the swap, or funding an account created in this tx (token launch)
                    other_out += lamports
                else:
                    swap_sys_out += lamports
            elif dst == wallet and src != wallet and src not in owned and not under_swap:
                other_in += lamports
        token_ok = True
        notes = []
        for mint in {r.mint for r in recs}:
            want = sum((r.token_amount_raw if r.tx_type == "buy" else -r.token_amount_raw)
                       for r in recs if r.mint == mint)
            got = book.delta(wallet, mint)
            if mint == WSOL_MINT:
                continue
            if abs(got - want) > 1:
                token_ok = False
                notes.append(f"token delta {got} != events {want} for {mint}")
        # SOL the wallet sent inside pump.fun beyond what its buy events account for (rent
        # top-up of protocol accounts such as the creator vault). Only when the token balance
        # matches the events: otherwise the extra SOL may be an undecoded trade, not a cost.
        implied = sum(round((r.sol_amount + r.fee_trading_sol) * LAMPORTS) for r in recs
                      if r.tx_type == "buy" and r.venue == "pump_curve")
        extra_in_swap = (swap_sys_out - implied) if (implied and token_ok) else 0
        if extra_in_swap > 0:
            other_out += extra_in_swap
        expected = -fee_net - tips - other_out + other_in
        for r in recs:
            s = round(r.sol_amount * LAMPORTS)
            f = round(r.fee_trading_sol * LAMPORTS)
            expected += (-s - f) if r.tx_type == "buy" else (s - f)
        residual = d_w - expected


        sol_ok = abs(residual) <= RESIDUAL_TOLERANCE_LAMPORTS
        if not sol_ok:
            traded = {r.mint for r in recs} | {WSOL_MINT}
            other_assets = sorted({v["mint"] for k, v in book.acc.items()
                                   if k in owned and v["mint"] not in traded and v["post"] != v["pre"]})
            if other_assets:
                notes.append("wallet paid/received another asset in the same tx ("
                             + ", ".join(m[:8] for m in other_assets)
                             + "); SOL amount taken from the curve/pool event")
            if wallet not in _signers_cache.get(dec.signature, {wallet}):
                notes.append("event user did not sign: router/PDA account, economic owner is the fee payer")
        first = recs[0]
        first.fee_network_sol = fee_net / LAMPORTS
        first.tip_sol = tips / LAMPORTS
        first.other_cost_sol = (other_out - other_in) / LAMPORTS
        first.rent_sol = rent / LAMPORTS
        first.wallet_sol_delta = d_w / LAMPORTS
        first.reconciliation_residual_sol = residual / LAMPORTS
        status = VERIFIED if (sol_ok and token_ok and not dec.logs_truncated) else PARTIAL
        if not sol_ok:
            notes.append(f"SOL residual {residual} lamports")
        if dec.logs_truncated:
            notes.append("program logs truncated")
        if extra_in_swap > 0:
            notes.append(f"{extra_in_swap} lamports paid inside pump.fun beyond the event (account rent top-up)")
        note = "; ".join(notes) if notes else "balances reconciled with on-chain events"
        if len(recs) > 1:
            note += f"; {len(recs)} trades by this wallet in one tx (costs on {first.event_key})"
        if any(r.extra.get("attributed_to_signer") for r in recs):
            note += "; executed through a router account, attributed to the signing wallet"
        for r in recs:
            r.source = "rpc_tx"
            r.verification_status = status
            r.verification_note = note
            if r is not first:
                r.wallet_sol_delta = None
                r.reconciliation_residual_sol = None


def _token_transfers(dec: DecodedTx, book: _TokenBook, raw_tok: list[dict]) -> None:
    traded = {(t.wallet, t.mint) for t in dec.trades}
    n = 0
    for tr in raw_tok:
        if tr["under_swap"] or not tr["amount"]:
            continue
        src = book.acc.get(tr["source"] or "")
        dst = book.acc.get(tr["destination"] or "")
        mint = tr["mint"] or (src or {}).get("mint") or (dst or {}).get("mint")
        if not mint or mint == WSOL_MINT:
            continue
        a = (src or {}).get("owner")
        b = (dst or {}).get("owner")
        if a == b:
            continue
        if (a, mint) in traded or (b, mint) in traded:
            continue  # part of a routed swap
        decimals = book.decimals_of(mint) or PUMP_DECIMALS
        ui = tr["amount"] / 10 ** decimals
        bt = int(dec.block_time or 0)
        if a:
            dec.token_transfers.append(TokenTransfer(dec.signature, f"xfer:{n}:out", a, b, mint, dec.slot, bt,
                                                     "transfer_out", ui, tr["amount"], decimals))
        if b:
            dec.token_transfers.append(TokenTransfer(dec.signature, f"xfer:{n}:in", b, a, mint, dec.slot, bt,
                                                     "transfer_in", ui, tr["amount"], decimals))
        n += 1
