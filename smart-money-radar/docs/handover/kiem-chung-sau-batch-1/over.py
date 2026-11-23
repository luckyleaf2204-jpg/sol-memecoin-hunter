import asyncio, json
from radar.context import create_ctx
async def main():
    ctx = await create_ctx("inv", pool_size=1); p=ctx.pool
    cases=[c.split(":") for c in open("/app/cases.txt").read().split()]
    tot=0; per={}
    for w,m,net in cases:
        r=await p.fetchrow("SELECT tokens_bought, tokens_sold, sell_sol, buy_sol, realized_pnl_sol FROM wallet_positions WHERE wallet=$1 AND mint=$2 AND status='closed' AND verification='verified' ORDER BY abs(tokens_sold-tokens_bought) DESC LIMIT 1", w, m)
        extra=-float(net); sold=float(r["tokens_sold"])
        over=float(r["sell_sol"])*min(extra,sold)/sold if sold else 0
        tot+=over; per[w[:8]]=per.get(w[:8],0)+over
        print(w[:8], m[:8], "bought", round(float(r["tokens_bought"])), "sold", round(sold), "pre-window tokens sold", round(extra), "sell_sol", round(float(r["sell_sol"]),3), "pnl", round(float(r["realized_pnl_sol"]),3), "proceeds_without_cost", round(over,3))
    print("TOTAL proceeds without cost basis (SOL):", round(tot,3), {k:round(v,3) for k,v in per.items()})
    await ctx.close()
asyncio.run(main())
