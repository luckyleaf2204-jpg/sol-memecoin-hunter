"""Canonical token identity validation — one contract address, one identity.

Why: a discovery feed can attach a name/symbol to the WRONG address. Real case (2026-09-30): a Pump.fun
curve quoted in Tesla xStock (TSLAx, XsDoVfqe…JHzoB) was shown as "$APEWIF" on the TSLAx address, while
APEWIF's real mint is FUgEk…aDtT. So the scanner never trusts a symbol/name from a feed on its own:

  canonical (looked up BY this CA)   helius      DAS getAsset -> on-chain metadata of this mint
                                     pumpfun     Pump.fun record whose `mint` == this CA
                                     dexscreener pair whose baseToken.address == this CA
  claim only                         pumpportal  create event decoded by a third party

  UNVERIFIED  no canonical source yet -> analysed as data, but never "Cơ hội"
  VERIFIED    >= 1 canonical source and every source agrees on the symbol
  CONFLICT    two sources give different symbols for the same CA -> Data Quality critical issue
              `identity_conflict` (INVALID: no score, no ranking, no Early TRUE, never "Cơ hội"),
              no Helius holder scan, no MC anchor

Symbols are compared after Unicode NFKC normalisation, trimming, a leading "$" removed and case folding.
Names are informative only (feeds truncate them), so they are not compared. The displayed symbol/name is
always the canonical one (priority helius > pumpfun > dexscreener).
"""
from __future__ import annotations

import unicodedata

from core.models import TokenIdentity, TokenState

CANONICAL = ("helius", "pumpfun", "dexscreener")
CLAIM_ONLY = ("pumpportal",)
SOURCE_LABEL = {"helius": "Helius getAsset (on-chain)", "pumpfun": "Pump.fun", "dexscreener": "DexScreener",
                "pumpportal": "PumpPortal"}


def norm_symbol(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip()
    if s.startswith("$"):
        s = s[1:].strip()
    return s.casefold()


def record_claim(ident: TokenIdentity, source: str, symbol: str, name: str = "") -> bool:
    """Store what `source` says this CA is. Empty symbols are ignored. Returns True if something changed."""
    if not (symbol or "").strip():
        return False
    val = (symbol.strip(), (name or "").strip())
    if ident.claims.get(source) == val:
        return False
    ident.claims[source] = val
    return True


def resolve_identity(ident: TokenIdentity) -> TokenIdentity:
    canon = [s for s in CANONICAL if s in ident.claims]
    if not canon:
        ident.status, ident.symbol, ident.name, ident.reason = "UNVERIFIED", "", "", "no_canonical_source"
        return ident
    distinct = {norm_symbol(sym) for sym, _ in ident.claims.values()}
    first = ident.claims[canon[0]]
    ident.symbol = first[0]
    ident.name = next((ident.claims[s][1] for s in canon if ident.claims[s][1]), "")
    if len(distinct) > 1:
        ident.status = "CONFLICT"
        ident.reason = "; ".join(f"{SOURCE_LABEL.get(s, s)}={v[0]}" for s, v in sorted(ident.claims.items()))
    else:
        ident.status, ident.reason = "VERIFIED", ""
    return ident


def claim_from_info(st: TokenState, sources: set[str], symbol: str, name: str) -> None:
    """Discovery / Pump.fun TokenInfo observation -> claim under its source."""
    for src in sources:
        if src in CANONICAL or src in CLAIM_ONLY:
            record_claim(st.identity, src, symbol, name)


def apply_identity(st: TokenState) -> TokenIdentity:
    """Resolve and make every UI/alert show the canonical symbol/name of THIS CA."""
    ident = resolve_identity(st.identity)
    if ident.symbol:
        st.info.symbol = ident.symbol
        if ident.name:
            st.info.name = ident.name
    return ident
