"""Program Derived Address derivation (find_program_address) in pure Python.

A PDA must NOT be a valid ed25519 point, so we need an on-curve check:
decompress y, compute x^2 = (y^2 - 1) / (d*y^2 + 1) mod p and test for a square root.
"""
from __future__ import annotations

import hashlib

from .base58 import b58decode, b58encode

_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _is_on_curve(b: bytes) -> bool:
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    if x2 == 0:
        return True
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    return (x * x - x2) % _P == 0


def create_program_address(seeds: list[bytes], program_id: str) -> str | None:
    h = hashlib.sha256()
    for s in seeds:
        if len(s) > 32:
            raise ValueError("seed too long")
        h.update(s)
    h.update(b58decode(program_id))
    h.update(b"ProgramDerivedAddress")
    out = h.digest()
    if _is_on_curve(out):
        return None
    return b58encode(out)


def find_program_address(seeds: list[bytes], program_id: str) -> tuple[str, int]:
    for bump in range(255, -1, -1):
        addr = create_program_address(seeds + [bytes([bump])], program_id)
        if addr is not None:
            return addr, bump
    raise ValueError("unable to find a viable program address")


def pubkey_bytes(addr: str) -> bytes:
    b = b58decode(addr)
    if len(b) != 32:
        raise ValueError("not a 32-byte public key")
    return b
