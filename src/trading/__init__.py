"""SOL Trading Bot — PAPER TRADING ONLY.

Pipeline (each stage is a module with RUN / READY / BLOCKED / ERROR status):
    SCAN -> VET -> SCORE -> SIZE -> RISK -> EXECUTE (paper) -> POSITION -> EXIT -> P&L

The bot only READS what the scanner published (TokenState); it never changes scanner results, Early Signal
or D1–D8. There is no private key, no wallet, no transaction signing and no network call that moves funds:
`execution.PaperExecutor` models routes, price impact, slippage, fees, failures and latency on real,
validated market data. Modes CONFIRM / AUTO exist only as names and are refused by `config.TradingConfig`.
"""
