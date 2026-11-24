"""Smart-money activity around a call, from the trades already in the database (no extra API).

Wallet set: wallets with status smart / tracked / candidate plus confirmed watched (KOL) wallets.
The number of known wallets is stored with every check: the correlation is only as good as the set.
"""
from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc

WALLET_SET_SQL = """
    SELECT address, status AS role FROM wallets WHERE status IN ('smart','tracked','candidate') AND NOT is_excluded
    UNION SELECT address, 'kol' FROM watched_wallets WHERE confirmed"""


async def check(conn, call_id: int, mint: str, called_at: dt.datetime) -> dict:
    known = await conn.fetchval(f"SELECT count(*) FROM ({WALLET_SET_SQL}) s")
    rows = await conn.fetch(f"""
        WITH sm AS ({WALLET_SET_SQL})
        SELECT t.wallet, sm.role, t.tx_type, t.sol_amount, t.block_time, t.signature
        FROM wallet_transactions t JOIN sm ON sm.address = t.wallet
        WHERE t.mint = $1 AND t.tx_type IN ('buy','sell')
          AND t.block_time BETWEEN $2::timestamptz - interval '6 hours' AND $2::timestamptz + interval '6 hours'
        ORDER BY t.block_time""", mint, called_at)
    big = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM wallet_transactions WHERE mint = $1 AND tx_type = 'buy'
        AND sol_amount >= 5 AND block_time BETWEEN $2::timestamptz - interval '6 hours' AND $2::timestamptz)""", mint, called_at)
    before = [r for r in rows if r["tx_type"] == "buy" and r["block_time"] < called_at]
    after = [r for r in rows if r["tx_type"] == "buy" and r["block_time"] >= called_at]
    sold = [r for r in rows if r["tx_type"] == "sell" and called_at <= r["block_time"] <= called_at + dt.timedelta(minutes=30)]
    res = {"wallets_known": int(known or 0), "buyers_before": len({r["wallet"] for r in before}),
           "buyers_after": len({r["wallet"] for r in after}), "sellers_30m": len({r["wallet"] for r in sold}),
           "sol_before": float(sum(r["sol_amount"] for r in before)), "sol_after": float(sum(r["sol_amount"] for r in after)),
           "sol_sold_30m": float(sum(r["sol_amount"] for r in sold)),
           "first_sm_buy_at": before[0]["block_time"] if before else (after[0]["block_time"] if after else None),
           "big_buy_before": bool(big),
           "detail": {"trades": [{"wallet": r["wallet"], "role": r["role"], "side": r["tx_type"], "sol": float(r["sol_amount"]),
                                  "at": r["block_time"].isoformat(), "signature": r["signature"],
                                  "min_from_call": round((r["block_time"] - called_at).total_seconds() / 60, 1)} for r in rows][:200]}}
    await conn.execute("""INSERT INTO tg_call_sm(call_id, wallets_known, buyers_before, buyers_after, sellers_30m, sol_before,
            sol_after, sol_sold_30m, first_sm_buy_at, big_buy_before, detail, checked_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
        ON CONFLICT (call_id) DO UPDATE SET wallets_known = EXCLUDED.wallets_known, buyers_before = EXCLUDED.buyers_before,
            buyers_after = EXCLUDED.buyers_after, sellers_30m = EXCLUDED.sellers_30m, sol_before = EXCLUDED.sol_before,
            sol_after = EXCLUDED.sol_after, sol_sold_30m = EXCLUDED.sol_sold_30m, first_sm_buy_at = EXCLUDED.first_sm_buy_at,
            big_buy_before = EXCLUDED.big_buy_before, detail = EXCLUDED.detail, checked_at = now()""",
        call_id, res["wallets_known"], res["buyers_before"], res["buyers_after"], res["sellers_30m"], res["sol_before"],
        res["sol_after"], res["sol_sold_30m"], res["first_sm_buy_at"], res["big_buy_before"], res["detail"])
    return res
