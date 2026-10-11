"""What did the special wallets do in a time window? (read-only, public Solana RPC, no key)

    python tools/insider_window.py --from "2026-10-12 15:00" --to "2026-10-12 17:30" [--min-sol 1] [--json out.json]

Times are UTC. For each listed wallet (45 holders + personal successors) every transaction in the window is analysed
(same rules as the live watch): coins created, bought / received, sold, and SOL sent to new wallets. Output: one line
per coin with the wallets that bought it, SOL in, first buy time, name / symbol / market cap (DexScreener)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from insiders.vip import vip_list  # noqa: E402
from insiders.watch import STABLES, WSOL, Rpc, analyse  # noqa: E402


def utc(s: str) -> float:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp()


def sigs_in(rpc, wallet: str, t0: float, t1: float, cap: int = 300) -> list[str]:
    out, before = [], None
    while len(out) < cap:
        opts = {"limit": 100, **({"before": before} if before else {})}
        page = rpc("getSignaturesForAddress", [wallet, opts]) or []
        if not page:
            break
        for s in page:
            bt = s.get("blockTime") or 0
            if bt < t0:
                return out
            if bt <= t1 and s.get("err") is None:
                out.append(s["signature"])
        before = page[-1]["signature"]
        if len(page) < 100:
            break
    return out


def dex_meta(mints: list[str]) -> dict:
    meta = {}
    for i in range(0, len(mints), 30):
        try:
            req = urllib.request.Request("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[i:i + 30]),
                                         headers={"User-Agent": "Mozilla/5.0"})
            for p in json.loads(urllib.request.urlopen(req, timeout=20).read()) or []:
                b = p.get("baseToken") or {}
                if b.get("address") and b["address"] not in meta:
                    meta[b["address"]] = {"symbol": b.get("symbol"), "name": b.get("name"), "mcap": p.get("marketCap"),
                                          "created": (p.get("pairCreatedAt") or 0) / 1000 or None, "url": p.get("url")}
        except Exception:
            continue
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t0", required=True)
    ap.add_argument("--to", dest="t1", required=True)
    ap.add_argument("--min-sol", type=float, default=0.0)
    ap.add_argument("--json")
    a = ap.parse_args()
    t0, t1 = utc(a.t0), utc(a.t1)
    result = json.loads((Path(__file__).resolve().parents[1] / "src/insiders/result.json").read_text(encoding="utf-8"))
    wallets = vip_list(result)
    rpc = Rpc(gap_s=0.25)
    coins = defaultdict(lambda: {"buy": defaultdict(float), "sell": defaultdict(float), "first": None, "create": []})
    funds, active = [], 0
    for k, w in enumerate(wallets, 1):
        try:
            sigs = sigs_in(rpc, w, t0, t1)
        except RuntimeError as e:
            print(f"  ! {w[:8]} {e}", file=sys.stderr)
            continue
        active += bool(sigs)
        for s in sigs:
            try:
                tx = rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            except RuntimeError:
                continue
            for ev in analyse(tx or {}, w) if tx else []:
                m = ev.get("mint")
                if ev["kind"] == "FUND":
                    funds.append((ev["ts"], w, ev.get("to"), ev.get("sol")))
                if not m or m in STABLES or m == WSOL:
                    continue
                c = coins[m]
                if ev["kind"] in ("BUY", "RECEIVE", "CREATE"):
                    c["buy"][w] += ev.get("sol") or 0
                    c["first"] = min(c["first"] or ev["ts"], ev["ts"])
                    if ev["kind"] == "CREATE":
                        c["create"].append(w)
                elif ev["kind"] in ("SELL", "SEND"):
                    c["sell"][w] += ev.get("sol") or 0
        print(f"  {k}/{len(wallets)} {w[:8]} {len(sigs)} tx", file=sys.stderr)
    keep = {m: c for m, c in coins.items() if sum(c["buy"].values()) >= a.min_sol or c["create"]}
    meta = dex_meta(sorted(keep))
    rows = sorted(keep.items(), key=lambda x: (-len(x[1]["buy"]), -sum(x[1]["buy"].values())))
    fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%m-%d %H:%M") if t else "?"  # noqa: E731
    print(f"\nWindow {a.t0} -> {a.t1} UTC: {active}/{len(wallets)} wallets active, {len(keep)} coins")
    out = []
    for m, c in rows:
        info = meta.get(m, {})
        r = {"mint": m, "symbol": info.get("symbol"), "name": info.get("name"), "mcap": info.get("mcap"),
             "pair_created": fmt(info.get("created")), "first_buy": fmt(c["first"]), "n_wallets": len(c["buy"]),
             "sol_in": round(sum(c["buy"].values()), 2), "sol_out": round(sum(c["sell"].values()), 2),
             "created_by_listed": c["create"], "wallets": {w[:8]: round(v, 2) for w, v in c["buy"].items()}}
        out.append(r)
        print(f"- {r['symbol'] or '?'} ({r['name'] or ''}) {m}\n    {r['n_wallets']} wallets, in {r['sol_in']} SOL, "
              f"out {r['sol_out']} SOL, first buy {r['first_buy']}, pair {r['pair_created']}, "
              f"MC {r['mcap']}{', CREATED by listed wallet' if c['create'] else ''}\n    {r['wallets']}")
    big = [f for f in funds if (f[3] or 0) >= 0.5]
    print(f"\nSOL sent >= 0.5 to other wallets: {len(big)}")
    for ts, w, to, sol in sorted(big)[:30]:
        print(f"  {fmt(ts)} {w[:8]} -> {to} {sol} SOL")
    if a.json:
        Path(a.json).write_text(json.dumps({"coins": out, "funds": big}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
