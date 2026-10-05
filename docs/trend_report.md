# Trend research — rule T1 result

Pre-registered plan: [docs/trend_plan.md](trend_plan.md). Research only (paper numbers, no order).

Universe built 2026-10-05T14:36 UTC. Window 2026-04-08 16:00 -> 2026-10-05 14:00, holdout from 2026-07-25 14:48.

## Verdict: **REJECT**

- ❌ n>=100
- ❌ ci_lower>0
- ❌ random_p<0.05
- ❌ in_sample_mean>0

## Results (primary: $100 size, priority fee 0.005 SOL)

| period | n | tokens | mean net % | 95 % CI per token | median % | mean gross % | win % | avg win | avg loss | PF | hold h |
|---|---|---|---|---|---|---|---|---|---|---|---|
| in_sample | 66 | 15 | -4.794 | (-7.522, -1.708) | -8.302 | -1.682 | 24.2 | 11.534 | -10.019 | 0.368 | 126.7 |
| holdout | 62 | 15 | 1.101 | (-2.441, 6.372) | -4.616 | 4.31 | 43.5 | 15.581 | -10.07 | 1.194 | 163.2 |

Random baseline (holdout, 2000 draws, same trades per token and holding times): mean of random means 4.107 %, p(random >= observed) = 0.9435

## Sensitivity (not decisive)

- $50, 0.005 SOL: holdout n 62, mean net -0.425 %, CI (-3.966, 4.846), verdict REJECT
- $100, 0.001 SOL: holdout n 62, mean net 2.301 %, CI (-1.241, 7.572), verdict REJECT

## Universe

| token | pool | dex | quote | reserve $ | round trip cost % | buy & hold holdout net % |
|---|---|---|---|---|---|---|
| BONK | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| WIF | EP2ib6dY… | raydium | SOL | 6,887,814 | 3.139 | 66.93 |
| JUP | C8Gr6AUu… | meteora | SOL | 2,298,428 | 3.15 | 75.78 |
| RAY | AVs9TA4n… | raydium | SOL | 5,554,878 | 3.14 | 216.6 |
| PYTH | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| JTO | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| POPCAT | FRhB8L7Y… | raydium | SOL | 4,127,265 | 3.143 | 23.18 |
| ORCA | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| BOME | DSUvc5qf… | raydium | SOL | 18,046,335 | 3.135 | 135.19 |
| MEW | 879F697i… | raydium | SOL | 10,623,572 | 3.137 | 46.91 |
| W | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| PENGU | FAqh648x… | orca | SOL | 4,217,169 | 3.142 | 57.68 |
| TRUMP | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| FARTCOIN | Bzc9NZfM… | raydium | SOL | 8,979,592 | 3.137 | 44.96 |
| GOAT | 9Tb2ohu5… | raydium | SOL | 1,766,353 | 3.156 | 41.69 |
| PNUT | 4AZRPNEf… | raydium | SOL | 3,703,382 | 3.144 | 32.32 |
| MOODENG | 22WrmyTj… | raydium | SOL | 3,043,593 | 3.146 | 23.82 |
| AI16Z | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| CHILLGUY | 93tjgwff… | raydium | SOL | 1,550,061 | 3.159 | -0.21 |
| RENDER | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| HNT | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| TNSR | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| DRIFT | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| KMNO | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| CLOUD | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| GRIFFAIN | CpsMssqi… | raydium | SOL | 2,334,184 | 3.15 | 117.58 |
| ZEREBRO | dropped: mint not found (404) | | | | | |
| WEN | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| SAMO | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| PUMP | HbjYfcWZ… | meteora | SOL | 2,005,981 | 3.153 | 252.54 |
| MOBILE | dropped: no SOL/USDC/USDT pool with reserve >= $1,000,000 older than 200 days | | | | | |
| ACT | B4PHSkL6… | raydium | SOL | 1,587,555 | 3.158 | 16.31 |

## Known limits

- 180 days only (public API): one market regime.
- Survivorship: today's well-known tokens and today's pool reserve -> results biased UPWARD.
- Price impact uses today's reserve; fills at the next hourly open (gaps included, intrabar path unknown).
