import json, gzip, os, bisect, statistics, sys
from collections import defaultdict
W = sys.argv[1]  # winner_data dir
P = os.path.join(W, "pumpswap")
sol = sorted((c[0], c[4]) for c in json.load(open(os.path.join(P, "sol_usd_hourly.json")))["data"]["attributes"]["ohlcv_list"])
ts = [a for a, _ in sol]
def su(bt): return sol[max(0, bisect.bisect_right(ts, bt) - 1)][1]
quotes = {}
for l in open(os.path.join(P, "pool_quotes.txt")):
    s = l.split()
    if len(s) > 2: quotes[s[0]] = s[2]
quotes.setdefault("VAYMVikuYQpWz5sXCeCsQDeho2QcGqDSNq9rhCspump", "So11111111111111111111111111111111111111112")
quotes.setdefault("BAKsQrynzwadyACDMsg3J5R8a4CTvj1u7kEYyjq7pump", "So11111111111111111111111111111111111111112")
WSOL = "So11111111111111111111111111111111111111112"; USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PROTO = {"BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s", "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"}
known_bot = {}
for fn in ("verification.json", "verification_held.json"):
    for w, r in json.load(open(os.path.join(W, fn))).items():
        if any("bot" in x for x in r.get("reasons", [])): known_bot[w] = fn
LO, HI, MIN = 100_000, 300_000, 0.5
tokens, excluded_tokens = {}, {}
for f in sorted(os.listdir(os.path.join(P, "trades"))):
    d = json.load(gzip.open(os.path.join(P, "trades", f))); m = d["mint"]; q = quotes.get(m)
    if q not in (WSOL, USDC):
        excluded_tokens[m] = f"pool quote {q} (not SOL/USDC): cannot price in SOL/USD"; continue
    tr = []
    for x in d["trades"]:
        if x["tok"] <= 0 or x["sol"] <= 0: continue
        if q == USDC:   # decoder assumed 9 decimals; USDC has 6 -> amount x1000 is USDC
            usd = x["sol"] * 1000; sol_eq = usd / su(x["bt"])
        else:
            sol_eq = x["sol"]; usd = sol_eq * su(x["bt"])
        mc = usd / x["tok"] * 1e9
        tr.append({**x, "sol_eq": sol_eq, "mc": mc, "px_sol": sol_eq / x["tok"]})
    curve = None
    cp = os.path.join(W, "trades", f)
    if os.path.exists(cp):
        c = json.load(gzip.open(cp)); curve = c
    tokens[m] = {"trades": tr, "zone": d["zone"], "curve": curve, "quote": "USDC" if q == USDC else "SOL"}
# per token per wallet
stats = {}
funnel = {}
for m, t in tokens.items():
    byw = defaultdict(list)
    for x in t["trades"]: byw[x["wallet"]].append(x)
    cv = t["curve"]; creator = (cv or {}).get("meta", {}).get("creator"); cslot = (cv or {}).get("meta", {}).get("created_slot")
    snipers = set()
    if cv and cslot:
        for x in cv["trades"]:
            if x["tx_type"] == "buy" and x["slot"] <= cslot + 2: snipers.add(x["wallet"])
    curve_wallets = {x["wallet"] for x in (cv or {}).get("trades", [])}
    zb = {}
    for w, xs in byw.items():
        buys = [x for x in xs if x["tx_type"] == "buy" and LO <= x["mc"] <= HI and x["sol_eq"] >= MIN]
        if buys: zb[w] = buys
    fl = defaultdict(list); keep = {}
    for w, buys in zb.items():
        xs = sorted(byw[w], key=lambda x: (x["slot"], x["bt"]))
        slots = defaultdict(set)
        for x in xs: slots[x["slot"]].add(x["tx_type"])
        same_slot = sum(1 for s in slots.values() if len(s) == 2)
        rt, last = 0, None
        for x in xs:
            if last == "buy" and x["tx_type"] == "sell": rt += 1
            last = x["tx_type"]
        bsol = sum(x["sol_eq"] for x in xs if x["tx_type"] == "buy"); ssol = sum(x["sol_eq"] for x in xs if x["tx_type"] == "sell")
        btok = sum(x["tok"] for x in xs if x["tx_type"] == "buy"); stok = sum(x["tok"] for x in xs if x["tx_type"] == "sell")
        gaps = [b["bt"] - a["bt"] for a, b in zip(xs, xs[1:])]
        why = None
        if w in PROTO or w == t["zone"]["pool"]: why = "protocol"
        elif creator and w == creator: why = "dev"
        elif w in snipers: why = "sniper_bundler"
        elif w in known_bot: why = "known_bot"
        elif len(xs) >= 20 or (len(xs) >= 6 and statistics.median(gaps) < 5): why = "bot_hf"
        elif same_slot >= 1: why = "wash_or_mev_same_slot"
        elif rt >= 3 and stok >= 0.9 * btok and abs(bsol - ssol) < 0.03 * (bsol + ssol): why = "wash_roundtrip"
        if why: fl[why].append(w); continue
        fb = min(buys, key=lambda x: x["bt"])
        keep[w] = {"first_buy_sig": fb["signature"], "first_buy_bt": fb["bt"], "first_buy_sol": round(fb["sol_eq"], 3),
                   "first_buy_mc_usd": round(fb["mc"]), "first_buy_px_sol": fb["px_sol"],
                   "zone_buy_sol": round(sum(x["sol_eq"] for x in buys), 3), "n_trades": len(xs),
                   "buy_sol": round(bsol, 3), "sell_sol": round(ssol, 3), "tok_bought": btok, "tok_sold": stok,
                   "sold_share": round(stok / btok, 3) if btok else None,
                   "sell_sigs": [x["signature"] for x in xs if x["tx_type"] == "sell"][:3],
                   "in_curve_phase": w in curve_wallets}
    funnel[m] = {"quote": t["quote"], "trades": len(t["trades"]), "zone_buyers": len(zb),
                 "filtered": {k: len(v) for k, v in fl.items()}, "kept": len(keep),
                 "creator_known": bool(creator), "curve_data": bool(cv and cv.get("trades"))}
    stats[m] = {"kept": keep, "filtered": fl}
# cross-token
apps = defaultdict(list)
for m, s in stats.items():
    for w, k in s["kept"].items(): apps[w].append((m, k))
allzone = set().union(*[set(s["kept"]) | {w for v in s["filtered"].values() for w in v} for s in stats.values()])
kept_all = set(apps)
# cross-token HF filter: wallets kept in many tokens but filtered as bot elsewhere
botish_elsewhere = {w for s in stats.values() for k, v in s["filtered"].items() if k != "protocol" for w in v}
rep = {w: a for w, a in apps.items() if len(a) >= 2}
rep_clean = {w: a for w, a in rep.items() if w not in botish_elsewhere}
out = {"excluded_tokens": excluded_tokens, "funnel": funnel,
       "totals": {"zone_buyers_unique": len(allzone), "zone_buyer_token_pairs": sum(f["zone_buyers"] for f in funnel.values()),
                  "kept_unique": len(kept_all), "repeated_ge2": len(rep), "repeated_ge2_clean": len(rep_clean),
                  "filtered_pairs": {k: sum(f["filtered"].get(k, 0) for f in funnel.values()) for k in
                                     ("protocol", "dev", "sniper_bundler", "known_bot", "bot_hf", "wash_or_mev_same_slot", "wash_roundtrip")}},
       "repeated": {w: {"tokens": len(a), "bot_flag_on_other_token": w in botish_elsewhere,
                        "positions": [{"mint": m, **k} for m, k in a]} for w, a in sorted(rep.items(), key=lambda kv: -len(kv[1]))}}
json.dump(out, open(os.path.join(P, "phase1_result.json"), "w"), indent=1, default=str)
print(json.dumps({k: out[k] for k in ("excluded_tokens", "funnel", "totals")}, indent=1))
for w, r in list(out["repeated"].items())[:60]:
    print(w, r["tokens"], "botelse" if r["bot_flag_on_other_token"] else "", [(p["mint"][:6], p["first_buy_sol"], round(p["first_buy_mc_usd"]/1000), p["n_trades"], p["sold_share"]) for p in r["positions"]])
