"""Collect public wallet leaderboards (read-only research tool, no Helius, no paid API).

Sources (server-rendered public pages, fetched politely, ~1 request/second):
  - KOL Explorer  https://kolexplorer.com/            (KOL ranking + /kol/<slug> profile pages)
  - dEdge         https://app.dedge.pro/wallets       (+ /wallets/vetted|win-rate|active|bots, /w/<wallet>)
  - Solana Tracker 30D tables are rendered in a browser separately (pages saved as HTML) and parsed here.
Output: <out>/leaderboards.json with one record per (source, wallet), every figure as shown by the source.
Usage: python -m radar.tools.leaderboard_collect <out_dir> [st_html ...]
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
B58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"


def get(url: str, pause: float = 1.2) -> str:
    for attempt in range(4):
        time.sleep(pause)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                print("fetch failed", url, type(e).__name__, flush=True)
                return ""
            time.sleep(3 * (attempt + 1))
    return ""


def text(t: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    s = re.sub(r"<[^>]+>", "|", s)
    return re.sub(r"\s*\|[\s|]*", "|", _html.unescape(s))


def money(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"([+\-−]?)\$?([+\-−]?)([\d,.]+)\s*([KMB]?)", s.replace("$-", "-$"))
    if not m:
        return None
    v = float(m.group(3).replace(",", "")) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(4)]
    return -v if "-" in (m.group(1) + m.group(2)) or "−" in (m.group(1) + m.group(2)) else v


def hold_seconds(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(s|min|h|d)", s)
    return float(m.group(1)) * {"s": 1, "min": 60, "h": 3600, "d": 86400}[m.group(2)] if m else None


# ---------------------------------------------------------------- KOL Explorer
def kolexplorer() -> list[dict]:
    t = get("https://kolexplorer.com/", 0)
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        slug = re.search(r'href="/kol/([a-z0-9-]+)"', r)
        tds = [text(x).strip("|") for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if not slug or len(tds) < 14:
            continue
        out.append({"slug": slug.group(1), "rank": tds[0], "name": tds[1], "pnl_1d_usd": money(tds[3]),
                    "pnl_7d_usd": money(tds[4]), "unrealized_usd": money(tds[5]), "total_usd": money(tds[6]),
                    "pnl_30d_usd": money(tds[7]), "wr_7d": tds[8], "wr_30d": tds[9], "avg_position": tds[10],
                    "median_hold": tds[11], "last_trade": tds[13]})
    for k in out:
        p = get(f"https://kolexplorer.com/kol/{k['slug']}")
        s = text(p)
        addrs = []
        for a in re.findall(rf"\|({B58})\|Copy", s):
            if a not in addrs:
                addrs.append(a)
        k["wallets"] = addrs
        g = lambda pat: (re.search(pat, s).group(1) if re.search(pat, s) else None)  # noqa: E731
        k.update({"realized_90d": g(r"\|([+\-−]\$[\d.,]+[KMB]?)\|Realized · 90 days"),
                  "win_rate_90d": g(r"\|([\d.]+%)\|Win rate\|"), "median_entry_mc": g(r"\|(\$[\d.,]+[KMB]?)\|Median entry MC"),
                  "median_hold_profile": g(r"\|([\d.]+ ?(?:s|min|h|d))\|Median hold"),
                  "mean_hold": g(r"Median hold · avg ([\d.]+ ?(?:s|min|h|d))"), "open_positions": g(r"all ([\d,]+) open positions"),
                  "tokens_touched_90d": g(r"\|([\d,]+)\|Tokens touched"), "top5_concentration": g(r"\|([\d.]+%)\|Top 5 concentration"),
                  "consistency": g(r"\|(\d+ · [A-Za-z ]+)\|"), "style": g(r"Trading Style & Pace\|([A-Za-z \-]+)\|"),
                  "last_tx": g(r"\|([^|]+)\|Last TX"), "tracked_since": g(r"tracked since ([A-Za-z]+ \d{4})")})
        bands = re.findall(r"\|(< 1 h|1–6 h|6–24 h|1–3 d|3–7 d|> 7 d)\|([<\d.%]+)\|([\d%—]+)\|([+\-−$\d,.K—]+)", s)
        k["hold_bands"] = [{"band": b, "share": sh, "win": w, "avg_pnl": ap} for b, sh, w, ap in bands]
        print("kol", k["slug"], k["wallets"][:2], flush=True)
    return out


# ---------------------------------------------------------------- dEdge
def dedge() -> dict:
    lists = {}
    for view in ("", "/vetted", "/win-rate", "/active", "/bots"):
        t = get(f"https://app.dedge.pro/wallets{view}")
        lists[view or "/top"] = list(dict.fromkeys(re.findall(rf"app\.dedge\.pro/w/({B58})", t)))
    wallets = {}
    for view, ws in lists.items():
        for w in ws:
            wallets.setdefault(w, {"lists": []})["lists"].append(view)
    for w, d in wallets.items():
        s = text(get(f"https://app.dedge.pro/w/{w}"))
        g = lambda pat: (re.search(pat, s).group(1) if re.search(pat, s) else None)  # noqa: E731
        net = re.search(r"Net profit\|([+\-−]?)\|?([\d.,]+)\|SOL", s)
        pct = re.findall(r"\|([+\-−]?)\|?([\d.]+)\|%\|(p10|p25|median|p75)", s)
        d.update({"status": g(r"\|(ACTIVE|INACTIVE|DORMANT|[A-Z]+)\|(?:ELITE|STRONG|LOW SAMPLE|[A-Z ]+)\|Net profit"),
                  "tier": g(r"\|([A-Z ]+)\|Net profit"),
                  "net_sol_15d": (-1 if net and net.group(1) in "-−" and net.group(1) else 1) * float(net.group(2).replace(",", "")) if net else None,
                  "win_rate": g(r"\|([\d.]+%)\|Win rate"), "closed_trades": g(r"\|(\d+)\|Closed trades"),
                  "tokens": g(r"\|(\d+)\|Tokens\|"), "last_trade": g(r"\|([\dhdmw]+ ?\w*)\|Last trade"),
                  "return_spread": {p: (s1 + v) for s1, v, p in pct}, "copy_check": g(r"Copy check: (\w+)")})
        print("dedge", w[:8], d.get("net_sol_15d"), d.get("copy_check"), flush=True)
    return {"lists": lists, "wallets": wallets}


# ---------------------------------------------------------------- Solana Tracker (saved HTML)
def solanatracker(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        board = os.path.basename(p).split(".")[0].replace("st_", "")
        t = open(p, encoding="utf-8").read()
        for a, b in re.findall(rf'<a class="block[^"]*" href="/wallet/({B58})">(.*?)</a>', t, re.S):
            f = [x for x in text(b).split("|") if x.strip()]
            d = {"board": board, "period": "30D", "wallet": a, "rank": f[0] if f else None}
            for k in ("Realized", "ROI", "Volume", "Win Rate", "Trades", "Tokens"):
                if k in f:
                    d[k] = f[f.index(k) + 1]
            la = [x for x in f if x.startswith("Last active")]
            d["last_active"] = la[0][12:] if la else None
            out.append(d)
    return out


if __name__ == "__main__":
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    res = {"collected_at": int(time.time()), "solanatracker": solanatracker(sys.argv[2:])}
    res["dedge"] = dedge()
    res["kolexplorer"] = kolexplorer()
    with open(os.path.join(out_dir, "leaderboards.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)
    print("done", len(res["solanatracker"]), len(res["dedge"]["wallets"]), len(res["kolexplorer"]))
