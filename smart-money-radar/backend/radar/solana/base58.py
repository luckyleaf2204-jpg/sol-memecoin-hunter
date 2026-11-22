"""Minimal base58 (Bitcoin alphabet) used by Solana addresses and instruction data."""
from __future__ import annotations

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        try:
            n = n * 58 + _INDEX[c]
        except KeyError as e:
            raise ValueError(f"invalid base58 character {c!r}") from e
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = []
    while n:
        n, r = divmod(n, 58)
        out.append(ALPHABET[r])
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + "".join(reversed(out))


def is_valid_address(s: str) -> bool:
    if not isinstance(s, str) or not (32 <= len(s) <= 44):
        return False
    try:
        return len(b58decode(s)) == 32
    except ValueError:
        return False


def is_valid_signature(s: str) -> bool:
    if not isinstance(s, str) or not (64 <= len(s) <= 90):
        return False
    try:
        return len(b58decode(s)) == 64
    except ValueError:
        return False
