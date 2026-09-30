"""Holder distribution: holder count, top 5/10/20/50 concentration, LP/curve exclusion.

Concentration is always computed WITHOUT the bonding curve / AMM pool / burn accounts,
otherwise every Pump.fun token would look 80% "concentrated" in its own curve.
"""
from __future__ import annotations

from core.models import HolderInfo, HolderStats
from solana_data.rpc import SolanaRpc

BURN_OWNERS = {"1nc1nerator11111111111111111111111111111111": "burn"}
# PumpSwap / pump.fun program-owned authorities seen holding pool vaults
KNOWN_PROGRAM_OWNERS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun program",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap program",
}


def compute_stats(owner_amounts: dict[str, float], supply: float, exclude: dict[str, str],
                  creator: str = "", holder_count: int | None = None, capped: bool = False,
                  source: str = "", top_n: int = 50, complete: bool = False) -> HolderStats:
    excluded = {**BURN_OWNERS, **KNOWN_PROGRAM_OWNERS, **{k: v for k, v in exclude.items() if k}}
    ranked = sorted(owner_amounts.items(), key=lambda kv: kv[1], reverse=True)
    excluded_pct = 0.0
    top: list[HolderInfo] = []
    kept: dict[str, float] = {}
    for owner, amt in ranked:
        pct = 100 * amt / supply if supply else 0.0
        if owner in excluded:
            excluded_pct += pct
            continue
        kept[owner] = amt
        if len(top) < top_n:
            tags = ["CREATOR"] if creator and owner == creator else []
            top.append(HolderInfo(owner=owner, amount=amt, pct=pct, tags=tags))

    def top_sum(n):
        return round(sum(h.pct for h in top[:n]), 2) if top else None

    creator_pct = next((h.pct for h in top if h.owner == creator), 0.0 if top else None)
    t10 = top_sum(10)
    valid, reason = True, ""
    if t10 is not None and t10 > 100.5:
        valid, reason = False, f"top10 = {t10:.1f}% of supply (> 100%): supply or balances inconsistent"
    return HolderStats(
        valid=valid, invalid_reason=reason,
        holder_count=holder_count, holder_count_capped=capped, top=top,
        top5_pct=top_sum(5), top10_pct=top_sum(10), top20_pct=top_sum(20), top50_pct=top_sum(50),
        max_single_pct=round(top[0].pct, 2) if top else None,
        excluded_pct=round(excluded_pct, 2), creator_pct=creator_pct, source=source,
        complete_list=complete, owner_amounts=kept,
    )


class HolderAnalyzer:
    def __init__(self, rpc: SolanaRpc, max_pages: int = 5):
        self.rpc = rpc
        self.max_pages = max_pages

    async def analyze(self, mint: str, supply: float | None, decimals: int,
                      exclude: dict[str, str], creator: str = "") -> HolderStats | None:
        if not supply:
            s = await self.rpc.token_supply(mint)
            if not s:
                return None
            supply, decimals = s
        if self.rpc.has_das:
            stats = await self._via_das(mint, supply, decimals, exclude, creator)
            if stats:
                return stats
        return await self._via_largest(mint, supply, exclude, creator)

    async def _via_das(self, mint, supply, decimals, exclude, creator) -> HolderStats | None:
        amounts: dict[str, float] = {}
        capped = False
        for page in range(1, self.max_pages + 1):
            r = await self.rpc.das_token_accounts(mint, page=page, limit=1000)
            if r is None:
                if page == 1:
                    return None
                part = self._finish(amounts, supply, exclude, creator, True)
                part.valid, part.invalid_reason = False, f"DAS page {page} missing (partial holder list)"
                return part
            accs = r.get("token_accounts") or []
            for a in accs:
                amt = float(a.get("amount") or 0) / 10 ** decimals
                if amt > 0 and a.get("owner"):
                    amounts[a["owner"]] = amounts.get(a["owner"], 0.0) + amt
            if len(accs) < 1000:
                break
            capped = page == self.max_pages
        return self._finish(amounts, supply, exclude, creator, capped)

    def _finish(self, amounts, supply, exclude, creator, capped):
        return compute_stats(amounts, supply, exclude, creator, holder_count=len(amounts),
                             capped=capped, source="helius_das", complete=not capped)

    async def _via_largest(self, mint, supply, exclude, creator) -> HolderStats | None:
        largest = await self.rpc.largest_accounts(mint)
        if not largest:
            return None
        owners = await self.rpc.account_owners([a for a, _ in largest])
        amounts: dict[str, float] = {}
        for addr, amt in largest:
            owner = owners.get(addr, addr)
            amounts[owner] = amounts.get(owner, 0.0) + amt
        return compute_stats(amounts, supply, exclude, creator, holder_count=None,
                             source="rpc_largest_accounts (top 20 only, no holder count)")
