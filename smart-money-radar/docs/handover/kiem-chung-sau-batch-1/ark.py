import asyncio, json
from radar.context import create_ctx
def bal(tx, w, m, which):
    return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (tx.get("meta") or {}).get(which) or [] if b.get("owner")==w and b.get("mint")==m)
async def main():
    ctx = await create_ctx("inv", pool_size=2); p=ctx.pool
    W = await p.fetchval("SELECT address FROM wallets WHERE address LIKE 'ArkdCAPE%'")
    for mp in ("pGukrxxc","G68A8gnX"):
        M = await p.fetchval("SELECT mint FROM wallet_positions WHERE wallet=$1 AND mint LIKE $2", W, mp+"%")
        rows = await p.fetch("""SELECT t.signature, t.block_time, t.tx_type, t.token_amount, t.sol_amount, t.event_key, t.verification_status, t.verification_note, t.venue, r.tx
            FROM wallet_transactions t LEFT JOIN raw_transactions r USING (signature) WHERE t.wallet=$1 AND t.mint=$2 ORDER BY t.block_time, t.event_key""", W, M)
        print("==", mp)
        for x in rows:
            tx = x["tx"]
            d = (bal(tx,W,M,"postTokenBalances")-bal(tx,W,M,"preTokenBalances")) if tx else None
            print(x["signature"][:10], x["tx_type"], x["event_key"], x["venue"], "decoded", round(float(x["token_amount"] or 0),1), "onchain_delta", None if d is None else round(d,1), x["verification_status"], (x["verification_note"] or "")[:90])
    await ctx.close()
asyncio.run(main())
