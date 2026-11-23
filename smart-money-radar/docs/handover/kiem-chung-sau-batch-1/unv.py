import asyncio, json, datetime as dt
from collections import defaultdict
from radar.context import create_ctx
from radar.tools.verify_pnl import wallet_delta_lamports
T0 = dt.datetime(2026,11,23,3,33,31,tzinfo=dt.timezone.utc)
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"; WSOL="So11111111111111111111111111111111111111112"
async def main():
    ctx = await create_ctx("inv", pool_size=2); p=ctx.pool
    pos = await p.fetch("""SELECT p.wallet, p.mint, p.realized_pnl_sol, p.signatures, p.flags FROM wallet_positions p JOIN wallets w ON w.address=p.wallet
        WHERE w.backfill_status IN ('complete','partial') AND w.backfill_to >= $1 AND p.status='closed' AND p.verification <> 'verified'""", T0)
    per = defaultdict(lambda: {"checked":0,"within_0.01":0,"usdc_moved":0,"other":0,"sum_sys":0.0,"sum_chain":0.0,"maxdiff":0.0})
    for r in pos:
        d = per[r["wallet"][:8]]
        if r["flags"] and any(f in ("transfer_in","transfer_out","opened_by_transfer") for f in r["flags"]): d["other"]+=1; continue
        sigs=list(dict.fromkeys(r["signatures"] or []))
        raws={x["signature"]:x["tx"] for x in await p.fetch("SELECT signature, tx FROM raw_transactions WHERE signature=ANY($1::text[])", sigs)}
        if len(raws)<len(sigs) or len(sigs)>=200: d["other"]+=1; continue
        tot=0; usdc=False; bad=False
        for s in sigs:
            lam, mints = wallet_delta_lamports(raws[s], r["wallet"])
            if USDC in mints: usdc=True
            if mints - {r["mint"], WSOL, USDC}: bad=True
            tot+=lam
        if usdc: d["usdc_moved"]+=1; continue
        if bad: d["other"]+=1; continue
        diff=float(r["realized_pnl_sol"])-tot/1e9
        d["checked"]+=1; d["within_0.01"]+= abs(diff)<=0.01; d["sum_sys"]+=float(r["realized_pnl_sol"]); d["sum_chain"]+=tot/1e9; d["maxdiff"]=max(d["maxdiff"],abs(diff))
    for w,d in sorted(per.items()):
        print(w, json.dumps({k:(round(v,4) if isinstance(v,float) else v) for k,v in d.items()}))
    await ctx.close()
asyncio.run(main())
