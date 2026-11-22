"""Capture REAL mainnet transactions as offline test fixtures.

Listens to pump.fun + PumpSwap program logs for a while, picks a diverse set of
signatures (curve buys/sells incl. >= 2 SOL, token creations, PumpSwap buys/sells,
migrations, failed txs, multi-trade txs), downloads each with getTransaction
(jsonParsed, v1) and writes tests/fixtures/tx/<signature>.json plus an index.

    python -m scripts.capture_fixtures --seconds 90
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import websockets

from radar.config import load_config
from radar.decoder.events import AmmSwapEvent, PumpCompleteEvent, PumpCreateEvent, PumpMigrationEvent, PumpTradeEvent
from radar.decoder.logs import events_from_logs
from radar.solana.constants import PUMP_AMM_PROGRAM, PUMP_PROGRAM
from radar.solana.rpc import SolanaRpc

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tx"

WANT = {"curve_buy": 5, "curve_buy_2sol": 4, "curve_sell": 5, "curve_sell_2sol": 2, "create": 3,
        "amm_buy": 4, "amm_sell": 4, "migration": 1, "complete": 1, "failed": 3, "multi_trade": 2}


def classify(logs: list[str], err) -> list[str]:
    if err is not None:
        return ["failed"]
    evs, _ = events_from_logs(logs)
    cats = []
    trades = [e for _, e in evs if isinstance(e, (PumpTradeEvent, AmmSwapEvent))]
    if len(trades) >= 2:
        cats.append("multi_trade")
    for _, e in evs:
        if isinstance(e, PumpTradeEvent):
            big = e.sol_amount >= 2_000_000_000
            if e.is_buy:
                cats.append("curve_buy_2sol" if big else "curve_buy")
            else:
                cats.append("curve_sell_2sol" if big else "curve_sell")
        elif isinstance(e, AmmSwapEvent):
            cats.append("amm_buy" if e.side == "buy" else "amm_sell")
        elif isinstance(e, PumpCreateEvent):
            cats.append("create")
        elif isinstance(e, PumpMigrationEvent):
            cats.append("migration")
        elif isinstance(e, PumpCompleteEvent):
            cats.append("complete")
    return cats


async def listen(ws_url: str, seconds: float) -> dict[str, list[str]]:
    picked: dict[str, list[str]] = defaultdict(list)
    deadline = time.time() + seconds
    async with websockets.connect(ws_url, max_size=2 ** 23, open_timeout=20, ping_interval=20) as ws:
        for i, prog in enumerate((PUMP_PROGRAM, PUMP_AMM_PROGRAM)):
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": i + 1, "method": "logsSubscribe",
                                      "params": [{"mentions": [prog]}, {"commitment": "confirmed"}]}))
        while time.time() < deadline:
            if all(len(picked[k]) >= n for k, n in WANT.items()):
                break
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            except asyncio.TimeoutError:
                continue
            v = ((msg.get("params") or {}).get("result") or {}).get("value")
            if not v:
                continue
            for c in classify(v.get("logs") or [], v.get("err")):
                if len(picked[c]) < WANT.get(c, 0) and v["signature"] not in picked[c]:
                    picked[c].append(v["signature"])
    return picked


async def main(seconds: float) -> None:
    cfg = load_config()
    rpc = SolanaRpc(cfg.rpc_endpoints)
    ws_url = next(e.ws_url for e in cfg.rpc_endpoints if e.name in ("helius", "solana_public"))
    picked = await listen(ws_url, seconds)
    await asyncio.sleep(3)  # let 'confirmed' txs become available on every node
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for cat, sigs in picked.items():
        for sig in sigs:
            tx = None
            for _ in range(4):
                tx = await rpc.get_transaction(sig)
                if tx:
                    break
                await asyncio.sleep(2)
            if not tx:
                print("not available yet", sig)
                continue
            (OUT / f"{sig}.json").write_text(json.dumps(tx, indent=None))
            index.setdefault(sig, []).append(cat)
            print(cat, sig)
    idx_path = OUT.parent / "index.json"
    old = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    old.update(index)
    idx_path.write_text(json.dumps(old, indent=1, sort_keys=True))
    await rpc.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    asyncio.run(main(ap.parse_args().seconds))
