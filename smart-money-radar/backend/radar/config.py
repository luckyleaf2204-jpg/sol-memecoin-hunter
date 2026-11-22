"""Process configuration loaded from environment variables.

Secrets (API keys, bot token, dashboard password) live ONLY here — they are never
stored in the database, never returned by the API and never written to logs
(see radar.logging_setup.redact).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if v is not None else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name, "")
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RpcEndpoint:
    name: str
    url: str
    ws_url: str
    rps: float
    # getTokenLargestAccounts etc. are blocked on public RPCs
    supports_heavy: bool


@dataclass(frozen=True)
class Config:
    database_url: str
    redis_url: str
    helius_api_key: str
    rpc_endpoints: tuple[RpcEndpoint, ...]
    birdeye_api_key: str
    solscan_api_key: str
    pumpportal_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    dashboard_password: str
    session_secret: str
    session_ttl_hours: int
    cors_origins: tuple[str, ...]
    log_level: str
    log_json: bool
    trading_enabled: bool = field(default=False)  # hard-wired: the app never trades

    @property
    def has_helius(self) -> bool:
        return bool(self.helius_api_key)

    def secret_values(self) -> list[str]:
        vals = [self.helius_api_key, self.birdeye_api_key, self.solscan_api_key,
                self.pumpportal_api_key, self.telegram_bot_token, self.dashboard_password,
                self.session_secret]
        return [v for v in vals if v and len(v) >= 6]


def _build_endpoints() -> tuple[RpcEndpoint, ...]:
    eps: list[RpcEndpoint] = []
    key = _env("HELIUS_API_KEY")
    if key:
        eps.append(RpcEndpoint(
            name="helius",
            url=f"https://mainnet.helius-rpc.com/?api-key={key}",
            ws_url=f"wss://mainnet.helius-rpc.com/?api-key={key}",
            rps=_env_float("HELIUS_RPS", 8.0),
            supports_heavy=True,
        ))
    extra = [u.strip() for u in _env("SOLANA_RPC_URLS").split(",") if u.strip()]
    for i, u in enumerate(extra):
        ws = u.replace("https://", "wss://").replace("http://", "ws://")
        eps.append(RpcEndpoint(name=f"custom{i + 1}", url=u, ws_url=ws,
                               rps=_env_float("SOLANA_RPC_RPS", 5.0), supports_heavy=True))
    if _env_bool("USE_PUBLIC_RPC", True):
        # Public limits (docs.solana.com/cluster/rpc-endpoints): 100 req / 10 s per IP,
        # 40 req / 10 s per method -> stay at 3.5 rps.
        eps.append(RpcEndpoint("solana_public", "https://api.mainnet-beta.solana.com",
                               "wss://api.mainnet-beta.solana.com",
                               _env_float("PUBLIC_RPC_RPS", 3.5), supports_heavy=False))
        eps.append(RpcEndpoint("publicnode", "https://solana-rpc.publicnode.com",
                               "wss://solana-rpc.publicnode.com",
                               _env_float("PUBLICNODE_RPS", 4.0), supports_heavy=False))
    return tuple(eps)


def load_config() -> Config:
    return Config(
        database_url=_env("DATABASE_URL", "postgresql://radar:radar@localhost:5432/radar"),
        redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
        helius_api_key=_env("HELIUS_API_KEY"),
        rpc_endpoints=_build_endpoints(),
        birdeye_api_key=_env("BIRDEYE_API_KEY"),
        solscan_api_key=_env("SOLSCAN_API_KEY"),
        pumpportal_api_key=_env("PUMPPORTAL_API_KEY"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        dashboard_password=_env("DASHBOARD_PASSWORD"),
        session_secret=_env("SESSION_SECRET"),
        session_ttl_hours=_env_int("SESSION_TTL_HOURS", 24 * 14),
        cors_origins=tuple(o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip()),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_json=_env_bool("LOG_JSON", True),
    )


_CONFIG: Config | None = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def reset_config_for_tests() -> None:
    global _CONFIG
    _CONFIG = None
