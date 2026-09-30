"""Capture raw API responses for a token and the validation verdict, for auditing.

  python tools/capture_samples.py <MINT> [<MINT> ...]
Writes docs/samples/<SYMBOL>_<status>.json containing the raw DexScreener pair(s), the raw
Pump.fun coin record, and the cleaned market data + data-quality issues.
"""
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core.config import DATA_DIR, Settings  # noqa: E402
from core.http import HttpClient  # noqa: E402
from database.db import Database  # noqa: E402
from pumpfun.client import HEADERS  # noqa: E402
from scanner.engine import ScannerEngine  # noqa: E402

WSOL = "So11111111111111111111111111111111111111112"
KEEP_PUMP = ["mint", "name", "symbol", "creator", "created_timestamp", "complete", "program", "mayhem_state",
             "quote_mint", "quote_decimals", "market_cap", "usd_market_cap", "virtual_sol_reserves",
             "virtual_quote_reserves", "real_sol_reserves", "real_quote_reserves", "real_token_reserves",
             "total_supply", "pool_address", "pump_swap_pool", "canonical_pool_liquidity_usd"]


async def capture(mint: str, http: HttpClient, engine: ScannerEngine) -> None:
    raw_pairs = await http.get_json(f"https://api.dexscreener.com/tokens/v1/solana/{mint},{WSOL}", source="dex")
    raw_coin = await http.get_json(f"https://frontend-api-v3.pump.fun/coins-v2/{mint}", source="pump", headers=HEADERS)
    pairs = [p for p in raw_pairs or [] if (p.get("baseToken") or {}).get("address") == mint]
    sol = next((float(p["priceUsd"]) for p in raw_pairs or []
                if (p.get("baseToken") or {}).get("address") == WSOL and p.get("priceUsd")), None)
    st = await engine.analyze_one(mint)   # the exact production pipeline
    q = st.quality
    out = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mint": mint,
        "raw_dexscreener_pairs": pairs,
        "raw_pumpfun_coin": {k: raw_coin.get(k) for k in KEEP_PUMP} if isinstance(raw_coin, dict) else raw_coin,
        "sol_usd": sol,
        "validated_market": asdict(st.market) if st.market else None,
        "data_quality": {"status": q.status, "score": q.score, "issues": [asdict(i) for i in q.issues]},
        "opportunity_score": st.score.total if st.score else None,
        "risk_score": st.risk.score if st.risk else None,
    }
    name = f"{(st.info.symbol or mint[:6])}_{q.status}.json".replace("/", "_")
    path = os.path.join(ROOT, "docs", "samples", name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"{mint}: {q.status} {q.score} -> {path}")


async def main(mints):
    http = HttpClient()
    engine = ScannerEngine(Settings(), Database(os.path.join(DATA_DIR, "samples_scratch.db")), on_log=lambda m: None)
    try:
        for m in mints:
            await capture(m, http, engine)
            await asyncio.sleep(2)
    finally:
        await http.aclose()
        await engine.http.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
