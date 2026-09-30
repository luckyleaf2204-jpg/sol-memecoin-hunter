"""Dev (creator) wallet analysis.

Chain covered in Phase 1:
  creator -> funding wallet (first incoming SOL transfer) -> SOL balance
  -> initial token buy (from PumpPortal create event) -> current balance -> sold %
  -> previous tokens created (Pump.fun) and how they did (graduated / dead / best ATH).
Transfers to other wallets and multi-wallet clusters are Phase 2 (smart_money/clustering).
"""
from __future__ import annotations

import time

from core.models import DevReport, HolderStats, TokenInfo
from pumpfun.client import PumpFunClient
from solana_data.rpc import SolanaRpc

DEAD_ATH_USD = 10_000
CACHE_TTL = 600


def classify_status(initial: float | None, current: float | None) -> tuple[str, float | None]:
    if current is None:
        return "UNKNOWN", None
    if initial and initial > 0:
        sold = max(0.0, min(100.0, 100 * (initial - current) / initial))
        if current <= 0:
            return "SOLD ALL", 100.0
        if sold >= 50:
            return "MAJOR SELL", sold
        if sold >= 5:
            return "PARTIAL SELL", sold
        return "HOLD", sold
    return ("ZERO BALANCE" if current <= 0 else "HOLDING"), None


MIN_FUNDING_SOL = 0.01
FUNDING_TX_SCAN = 15


def find_incoming_sol(tx: dict, wallet: str) -> tuple[str, float] | None:
    """Detect SOL funding INTO `wallet`: explicit system transfer first, else balance deltas
    (wallet gained ≥ MIN_FUNDING_SOL → funder = account that lost the most SOL)."""
    meta = tx.get("meta") or {}
    msg = (tx.get("transaction") or {}).get("message") or {}
    ixs = list(msg.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        ixs.extend(inner.get("instructions") or [])
    for ix in ixs:
        parsed = ix.get("parsed")
        if ix.get("program") == "system" and isinstance(parsed, dict) and parsed.get("type") == "transfer":
            info = parsed.get("info") or {}
            sol = int(info.get("lamports") or 0) / 1e9
            if info.get("destination") == wallet and info.get("source") != wallet and sol >= MIN_FUNDING_SOL:
                return info["source"], sol
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys") or []]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if wallet not in keys or len(pre) != len(keys) or len(post) != len(keys):
        return None
    i = keys.index(wallet)
    gained = (post[i] - pre[i]) / 1e9
    if gained < MIN_FUNDING_SOL:
        return None
    deltas = [(post[j] - pre[j], keys[j]) for j in range(len(keys)) if j != i]
    lost, funder = min(deltas, default=(0, None))
    return (funder, gained) if funder and lost < 0 else None


class DevAnalyzer:
    def __init__(self, rpc: SolanaRpc, pump: PumpFunClient):
        self.rpc = rpc
        self.pump = pump
        self._history: dict[str, tuple[float, list[TokenInfo] | None]] = {}
        self._funding: dict[str, tuple[str | None, float | None, str]] = {}

    async def analyze(self, info: TokenInfo, holders: HolderStats | None = None) -> DevReport | None:
        creator = info.creator
        if not creator:
            return None
        rep = DevReport(creator=creator, initial_buy=info.dev_initial_buy)

        # Balance counts as verified ONLY if the RPC actually answered; otherwise status stays UNKNOWN.
        rep.current_tokens = await self.rpc.owner_token_balance(creator, info.mint)
        if rep.current_tokens is not None and info.total_supply:
            rep.balance_verified = True
            rep.balance_source = f"Solana RPC getTokenAccountsByOwner @ {time.strftime('%H:%M:%S')}"
            rep.current_pct = 100 * rep.current_tokens / info.total_supply
            rep.status, rep.sold_pct = classify_status(info.dev_initial_buy, rep.current_tokens)
        else:
            rep.current_tokens = None
            rep.notes.append("Dev token balance could not be verified on-chain (RPC error or supply unknown).")
        if rep.balance_verified and info.dev_initial_buy is None:
            rep.notes.append("Initial buy unknown (token not seen live at creation) — sold % not computable.")
        rep.sol_balance = await self.rpc.sol_balance(creator)

        prev = await self._creator_history(creator)
        if prev is None:
            rep.notes.append("Creator history unavailable (Pump.fun API error).")
        else:
            others = [t for t in prev if t.mint != info.mint]
            rep.history_verified = True
            rep.prev_tokens_count = len(others)
            rep.prev_graduated = sum(1 for t in others if t.complete)
            rep.prev_dead = sum(1 for t in others if (t.ath_usd_mc or 0) < DEAD_ATH_USD)
            aths = [t.ath_usd_mc for t in others if t.ath_usd_mc]
            rep.prev_best_ath = max(aths) if aths else None
            rep.prev_tokens = [{"mint": t.mint, "symbol": t.symbol, "created_at": t.created_at,
                                "complete": t.complete, "ath_usd_mc": t.ath_usd_mc,
                                "usd_mc": t.pump_usd_mc} for t in others[:20]]
            if len(prev) >= 50:
                rep.notes.append("Creator has 50+ tokens (only latest 50 checked).")

        rep.funding_wallet, rep.funding_sol, rep.funding_note = await self._funding_source(creator)
        return rep

    async def _creator_history(self, creator: str) -> list[TokenInfo] | None:
        cached = self._history.get(creator)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]
        coins = await self.pump.creator_coins(creator, limit=50)
        if coins is not None:
            self._history[creator] = (time.time(), coins)
        return coins

    async def _funding_source(self, creator: str) -> tuple[str | None, float | None, str]:
        if creator in self._funding:
            return self._funding[creator]
        sigs = await self.rpc.signatures(creator, limit=1000)
        if sigs is None:
            return None, None, "lookup failed (RPC)"
        if not sigs:
            result = (None, None, "no transactions")
        elif len(sigs) >= 1000:
            result = (None, None, "wallet has 1000+ txs — first funding not traced (Phase 2)")
        else:
            result = (None, None, f"no SOL funding found in oldest {FUNDING_TX_SCAN} txs")
            for sig in reversed(sigs[-FUNDING_TX_SCAN:]):  # oldest first
                if sig.get("err"):
                    continue
                tx = await self.rpc.transaction(sig["signature"])
                hit = find_incoming_sol(tx, creator) if tx else None
                if hit:
                    result = (hit[0], hit[1], "first incoming SOL funding")
                    break
        self._funding[creator] = result
        return result
