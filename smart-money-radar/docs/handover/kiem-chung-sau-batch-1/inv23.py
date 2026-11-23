import asyncio, json, datetime as dt
from collections import Counter
from radar.context import create_ctx
T0 = dt.datetime(2026,11,23,3,33,31,tzinfo=dt.timezone.utc)
def bal(tx, w, m, which):
    return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (tx.get("meta") or {}).get(which) or [] if b.get("owner")==w and b.get("mint")==m)
async def main():
    ctx = await create_ctx("inv", pool_size=2); p=ctx.pool
    dust = float((await ctx.settings.get()).get("dust_fraction") or 0.01)
    wallets = [r["address"] for r in await p.fetch("SELECT address FROM wallets WHERE backfill_status IN ('complete','partial') AND backfill_to >= $1", T0)]
    print("WALLETS", len(wallets))
    # ---- part 1: the flagged positions, order-independent on-chain check
    pos = await p.fetch("""SELECT p.* FROM wallet_positions p WHERE p.wallet = ANY($1::text[]) AND p.status='closed' AND p.verification='verified'""", wallets)
    flagged=[]
    for r in pos:
        if r["flags"] and any(f in ("transfer_in","transfer_out","opened_by_transfer") for f in r["flags"]): continue
        sigs=list(dict.fromkeys(r["signatures"] or []))
        if len(sigs)>=200: continue
        raws={x["signature"]:x["tx"] for x in await p.fetch("SELECT signature, tx FROM raw_transactions WHERE signature=ANY($1::text[])", sigs)}
        if len(raws)<len(sigs): continue
        last=max(raws.values(), key=lambda t:(t.get("slot") or 0))
        left_slot=bal(last,r["wallet"],r["mint"],"postTokenBalances")
        if left_slot <= dust*max(float(r["tokens_bought"] or 0),1e-12): continue
        net=sum(bal(t,r["wallet"],r["mint"],"postTokenBalances")-bal(t,r["wallet"],r["mint"],"preTokenBalances") for t in raws.values())
        pre_first=min(bal(t,r["wallet"],r["mint"],"preTokenBalances") for t in raws.values() if t.get("slot")==min(x.get("slot") for x in raws.values()))
        bought,sold=float(r["tokens_bought"]),float(r["tokens_sold"])
        same_slot = Counter(t.get("slot") for t in raws.values())
        extra = max(sold-bought,0.0)
        over = float(r["sell_sol"])*extra/sold if sold else 0
        flagged.append({"wallet":r["wallet"],"mint":r["mint"],"bought":round(bought,1),"sold":round(sold,1),"left_by_max_slot":round(left_slot,1),
            "net_onchain_position":round(net,1),"held_before_first_buy":round(pre_first,1),"txs_sharing_a_slot":sum(1 for v in same_slot.values() if v>1),
            "sell_sol":float(r["sell_sol"]),"realized_pnl":float(r["realized_pnl_sol"]),"pnl_from_tokens_without_cost_sol":round(over,4)})
    print("FLAGGED", json.dumps(flagged))
    # ---- all positions of the 23 wallets selling more than they bought (history starts after the first buy)
    agg = await p.fetchrow("""SELECT count(*) n, count(*) FILTER (WHERE tokens_sold > tokens_bought*1.01 AND COALESCE(tokens_in,0)=0) over_n,
        COALESCE(sum(CASE WHEN tokens_sold > tokens_bought*1.01 AND COALESCE(tokens_in,0)=0 THEN sell_sol*(tokens_sold-tokens_bought)/tokens_sold END),0) over_sol,
        COALESCE(sum(realized_pnl_sol),0) pnl FROM wallet_positions WHERE wallet = ANY($1::text[]) AND status IN ('closed','written_off')""", wallets)
    print("OVERSELL", dict(agg))
    # ---- part 2: per wallet verified / reasons / completeness / PnL share of non-verified positions
    for w in wallets:
        wr = await p.fetchrow("SELECT backfill_status, backfill_sigs_seen, backfill_truncated, discovery_detail->'backfill_check' bc, discovery_detail->>'behavior' beh FROM wallets WHERE address=$1", w)
        notes = await p.fetch("""SELECT regexp_replace(left(coalesce(verification_note,''),60),'[0-9]+','N','g') note, count(*) n FROM wallet_transactions
            WHERE wallet=$1 AND source='rpc_tx' AND tx_type IN ('buy','sell') AND verification_status<>'verified' GROUP BY 1 ORDER BY 2 DESC LIMIT 3""", w)
        venues = await p.fetch("""SELECT venue, count(*) n, count(*) FILTER (WHERE verification_status='verified') v FROM wallet_transactions
            WHERE wallet=$1 AND source='rpc_tx' AND tx_type IN ('buy','sell') GROUP BY 1 ORDER BY 2 DESC LIMIT 4""", w)
        pp = await p.fetchrow("""SELECT count(*) FILTER (WHERE status IN ('closed','written_off')) closed,
            count(*) FILTER (WHERE status IN ('closed','written_off') AND verification='verified') closed_v,
            COALESCE(sum(realized_pnl_sol) FILTER (WHERE status IN ('closed','written_off')),0) pnl,
            COALESCE(sum(realized_pnl_sol) FILTER (WHERE status IN ('closed','written_off') AND verification<>'verified'),0) pnl_unv,
            count(*) FILTER (WHERE status IN ('closed','written_off') AND tokens_sold > tokens_bought*1.01 AND COALESCE(tokens_in,0)=0) oversold
            FROM wallet_positions WHERE wallet=$1""", w)
        sc = await p.fetchrow("SELECT score, confidence, qualifies FROM smart_money_scores WHERE wallet=$1 AND period_days=30", w)
        bc = wr["bc"] or {}
        print("W", json.dumps({"w":w,"st":wr["backfill_status"],"sigs":wr["backfill_sigs_seen"],"trunc":wr["backfill_truncated"],
            "trades":bc.get("trades"),"vshare":bc.get("verified_share"),"notes":[(x["note"],x["n"]) for x in notes],
            "venues":[(x["venue"],x["n"],x["v"]) for x in venues],"closed":pp["closed"],"closed_verified":pp["closed_v"],
            "pnl":round(float(pp["pnl"]),3),"pnl_unverified_positions":round(float(pp["pnl_unv"]),3),"oversold_positions":pp["oversold"],
            "score":float(sc["score"]) if sc else None,"confidence":sc["confidence"] if sc else None,"qualifies":sc["qualifies"] if sc else None,
            "behavior":(json.loads(wr["beh"]) if wr["beh"] and wr["beh"].startswith("{") else wr["beh"])}, default=str))
    await ctx.close()
asyncio.run(main())
