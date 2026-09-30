"""Narrative tagging — transparent keyword rules on the token's own name/symbol.

This is DATA (a label), not a score. Narrative strength / trend / acceleration on X needs a social
source (Phase 3) and is NOT AVAILABLE. The NARRATIVE tab aggregates tags over tokens the scanner
actually tracked (counts / volume from Pump.fun + DexScreener), which is real data.
"""
from __future__ import annotations

import re

NARRATIVES: dict[str, list[str]] = {
    "AI_AGENT": ["agent", "agentic", "autonomous", "agi"],
    "AI": ["ai", "gpt", "llm", "neural", "intelligen", "robot", "bot", "claude", "grok"],
    "DEPIN": ["depin", "node", "compute", "gpu", "network"],
    "RWA": ["rwa", "gold", "estate", "treasury", "stock", "bond"],
    "PRIVACY": ["privacy", "private", "zk", "anon", "stealth"],
    "GAMING": ["game", "gaming", "play", "quest", "arcade", "pixel"],
    "SOLANA": ["sol", "solana", "jup", "bonk", "saga"],
    "POLITICAL": ["trump", "biden", "maga", "president", "vote", "election", "kamala"],
    "CELEBRITY": ["kanye", "drake", "taylor", "musk", "elon", "celebrity", "mrbeast"],
    "NEWS": ["news", "breaking", "cnn", "fox", "report"],
    "ANIMAL": ["cat", "dog", "doge", "inu", "frog", "pepe", "shib", "bear", "bull", "monkey", "ape", "penguin",
               "hamster", "fish", "bird", "wif"],
    "COMMUNITY": ["community", "cto", "people", "dao", "fam", "army"],
    "TRENDING": ["viral", "trend", "tiktok", "meme of", "2026"],
}
ALL_TAGS = tuple(NARRATIVES) + ("MEME",)


def classify(*texts: str) -> list[str]:
    blob = " ".join(t.lower() for t in texts if t)
    words = set(re.findall(r"[a-z0-9]+", blob))
    tags = [n for n, kws in NARRATIVES.items()
            if any((k in words) if len(k) <= 3 else (k in blob) for k in kws)]
    return tags or ["MEME"]
