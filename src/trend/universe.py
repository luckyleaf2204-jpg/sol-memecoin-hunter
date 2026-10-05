"""Fixed candidate list for the trend research (docs/trend_plan.md). Chosen by name BEFORE any return was seen:
well-known Solana tokens (DeFi, infrastructure, memecoins that are still traded and ones that crashed). Stables,
SOL itself and liquid-staking tokens are excluded. Every mint is verified against the API's symbol before use."""
from __future__ import annotations

CANDIDATES = (
    ("BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
    ("WIF", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),
    ("JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),
    ("RAY", "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"),
    ("PYTH", "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"),
    ("JTO", "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"),
    ("POPCAT", "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"),
    ("ORCA", "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE"),
    ("BOME", "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82"),
    ("MEW", "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5"),
    ("W", "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ"),
    ("PENGU", "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv"),
    ("TRUMP", "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN"),
    ("FARTCOIN", "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"),
    ("GOAT", "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump"),
    ("PNUT", "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump"),
    ("MOODENG", "ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzPJBY"),
    ("AI16Z", "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC"),
    ("CHILLGUY", "Df6yfrKC8kZE3KNkrHERKzAetSxbrWeniQfyJY4Jpump"),
    ("RENDER", "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof"),
    ("HNT", "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux"),
    ("TNSR", "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6"),
    ("DRIFT", "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7"),
    ("KMNO", "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS"),
    ("CLOUD", "CLoUDKc4Ane7HeQcPpE3YHnznRxhMimJ4MyaUqyHFzAu"),
    ("GRIFFAIN", "KENJSUYLASHUMfHyy5o4Hp2FdNqZg1AsUPhfH2kYvEP"),
    ("ZEREBRO", "8x5VqbHA8D7NkD52uNuS5nnt3PwA3pLD34ymskeSo2Wn"),
    ("WEN", "WENWENvqqNya429ubCdR81ZmD69brwQaaBYY6p3LCpk"),
    ("SAMO", "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"),
    ("PUMP", "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"),
    ("MOBILE", "mb1eu7TzEc71KxDpsmsKoucSSuuoGLv1drys1oP2jh6"),
    ("ACT", "GJAFwWjJ3vnTsrQVabjBVK2TYB1YtRCQXRDfDgUnpump"),
)
QUOTES = {"So11111111111111111111111111111111111111112": "SOL",
          "EPjFWDdbu5EPrmBbNjfQkwnshWPQq9e5Bu2YTPNEnC3u": "USDC",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY8iyB7gxLfmJbM": "USDT"}
MIN_RESERVE_USD = 1_000_000.0
MIN_POOL_AGE_DAYS = 200
