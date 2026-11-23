import asyncio, json, sys, time
from radar.context import create_ctx
WALLETS = sys.argv[1].split(",")
CAP = int(sys.argv[2])
async def main():
    ctx = await create_ctx("phase1-probe", pool_size=1)
    try:
        t0 = await ctx.rpc.helius_credits_today(ctx.rpc.tag)
        out = {}
        for w in WALLETS:
            used = await ctx.rpc.helius_credits_today(ctx.rpc.tag) - t0
            if used + 10 > CAP:
                out["_stopped"] = f"cap {CAP} reached at {used}"; break
            page, tok = await ctx.rpc.get_transactions_for_address(w, limit=1000, full=False, sort="desc")
            bts = [x.get("blockTime") or 0 for x in page if x.get("blockTime")]
            span = (max(bts) - min(bts)) / 86400 if len(bts) > 1 else 0
            out[w] = {"sigs_page": len(page), "more": bool(tok), "newest": max(bts) if bts else None,
                      "oldest": min(bts) if bts else None, "span_days": round(span, 2),
                      "tx_per_day": round(len(page) / max(span, 1 / 24), 1) if bts else 0,
                      "errors": sum(1 for x in page if x.get("err"))}
        out["_credits_used"] = await ctx.rpc.helius_credits_today(ctx.rpc.tag) - t0
        json.dump(out, open("/app/winner_data/pumpswap/probe_result.json", "w"), indent=1)
        print(json.dumps(out, indent=1))
    finally:
        await ctx.close()
asyncio.run(main())
