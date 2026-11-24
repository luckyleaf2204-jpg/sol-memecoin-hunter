"""Find token calls in a Telegram post: contract addresses, ticker, name, stated price / market cap.

A contract address (CA) is a base58 string of 32-44 characters that decodes to 32 bytes. Addresses
that are obviously not tokens (programs, system accounts, wallets written as 'wallet:') are dropped;
whether a CA really is a token mint is checked later on chain / with DexScreener (status 'invalid').
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from ..solana.base58 import b58decode

B58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
CA_RE = re.compile(rf"(?<![1-9A-HJ-NP-Za-km-z/]){B58}(?![1-9A-HJ-NP-Za-km-z])")
# links carrying a mint: pump.fun/coin/<mint>, dexscreener.com/solana/<pair or mint>, birdeye, gmgn, photon...
LINK_RE = re.compile(rf"(?:pump\.fun/(?:coin/)?|dexscreener\.com/solana/|birdeye\.so/token/|gmgn\.ai/sol/token/(?:[A-Za-z0-9_]+_)?|"
                     rf"photon-sol\.tinyastro\.io/en/lp/|axiom\.trade/meme/|jup\.ag/swap/SOL-|solscan\.io/token/)({B58})")
TICKER_RE = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{1,14})\b")
MC_RE = re.compile(r"(?:\bmc|\bmcap|market\s*cap|\bfdv)\s*[:=\-–]?\s*\$?\s*([\d.,]+)\s*([kmb])?\b", re.I)
PRICE_RE = re.compile(r"\bprice\s*[:=\-–]?\s*\$\s*([\d.,]+(?:e-?\d+)?)", re.I)
NOT_TOKENS = {
    "11111111111111111111111111111111", "So11111111111111111111111111111111111111112",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", t).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def _is_pubkey(s: str) -> bool:
    try:
        return len(b58decode(s)) == 32
    except Exception:  # noqa: BLE001
        return False


def _num(v: str, suffix: str | None) -> float | None:
    v = v.replace(",", "")
    if v.count(".") > 1:
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x * {"k": 1e3, "m": 1e6, "b": 1e9}.get((suffix or "").lower(), 1)


def extract(text: str, links: list[str] | None = None) -> dict:
    """{'mints': [...in order of appearance], 'ticker', 'name', 'stated_mc_usd', 'stated_price_usd'}"""
    t = normalise(text)
    found: list[str] = []
    for src in [t] + list(links or []):
        for m in LINK_RE.finditer(src):
            found.append(m.group(1))
        for m in CA_RE.finditer(src):
            found.append(m.group(0))
    mints = []
    for c in found:
        if c in NOT_TOKENS or c in mints or not _is_pubkey(c):
            continue
        mints.append(c)
    tk = TICKER_RE.search(t)
    mc = MC_RE.search(t)
    pr = PRICE_RE.search(t)
    name = None
    first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    if first_line and len(first_line) <= 60 and not CA_RE.search(first_line):
        name = normalise(first_line)
    return {"mints": mints, "ticker": tk.group(1).upper() if tk else None, "name": name,
            "stated_mc_usd": _num(mc.group(1), mc.group(2)) if mc else None,
            "stated_price_usd": _num(pr.group(1), None) if pr else None}
