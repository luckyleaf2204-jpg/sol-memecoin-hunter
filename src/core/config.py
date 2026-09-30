"""Configuration: API keys from .env, user settings from data/settings.json."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dotenv import load_dotenv


def _app_dir() -> Path:
    # When frozen by PyInstaller the .env / data folder live next to the .exe.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_DIR = _app_dir()
ENV_PATH = APP_DIR / ".env"
load_dotenv(ENV_PATH)

# DATA_DIR can be moved with an environment variable (e.g. on a server); default = <app>/data
DATA_DIR = Path(os.getenv("DATA_DIR", "").strip() or APP_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "hunter.db"


def env(name: str) -> str:
    return os.getenv(name, "").strip()


@dataclass
class ApiKeys:
    helius: str = ""
    solana_rpc_url: str = ""
    birdeye: str = ""
    x_bearer: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    helius_source: str = ""          # where the Helius key came from (path or "environment variable") — never the key

    @classmethod
    def from_env(cls) -> "ApiKeys":
        from dotenv import dotenv_values
        in_file = bool((dotenv_values(ENV_PATH).get("HELIUS_API_KEY") or "").strip()) if ENV_PATH.exists() else False
        helius = env("HELIUS_API_KEY")
        return cls(
            helius_source=(str(ENV_PATH) if in_file else "environment variable") if helius else "",
            helius=helius,
            solana_rpc_url=env("SOLANA_RPC_URL"),
            birdeye=env("BIRDEYE_API_KEY"),
            x_bearer=env("X_BEARER_TOKEN") or env("X_API_KEY"),
            telegram_bot_token=env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=env("TELEGRAM_CHAT_ID"),
            telegram_api_id=env("TELEGRAM_API_ID"),
            telegram_api_hash=env("TELEGRAM_API_HASH"),
        )

    def status(self) -> dict[str, bool]:
        return {
            "Helius (holders/top holders)": bool(self.helius),
            "Custom Solana RPC": bool(self.solana_rpc_url),
            "Birdeye (Phase 2)": bool(self.birdeye),
            "X API (Phase 2)": bool(self.x_bearer),
            "Telegram alert bot": bool(self.telegram_bot_token and self.telegram_chat_id),
            "Telegram user API (Phase 2)": bool(self.telegram_api_id and self.telegram_api_hash),
        }


@dataclass
class Settings:
    # Scanner
    scan_interval_sec: int = 20
    max_age_hours: float = 6.0
    max_tracked: int = 400
    deep_per_cycle: int = 15         # tokens per round that get on-chain holder/dev analysis
    deep_max_per_min: int = 45       # cap on holder/dev analyses per minute (Helius credit budget)
    tracking_min_mc: float = 5_000   # drop tokens below this after 15 min
    snapshot_min_mc: float = 10_000  # start saving snapshots above this MC
    use_pumpportal_ws: bool = True

    # Filters (a token failing a filter is NOT removed, just marked)
    min_mc: float = 50_000
    max_mc: float = 500_000
    min_liquidity: float = 30_000
    min_volume_5m: float = 25_000
    min_txns_5m: int = 0
    min_holders: int = 0
    min_buy_sell_ratio: float = 1.20
    max_top10_pct: float = 40.0

    # UI / local API
    language: str = "vi"             # "vi" | "en"
    api_enabled: bool = True         # read-only JSON API on 127.0.0.1
    api_port: int = 8765

    # Alerts
    alerts_enabled: bool = True
    alert_min_score: int = 70
    alert_max_risk: int = 60
    alert_cooldown_min: int = 60
    alert_require_filters: bool = True

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        s = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        for f in fields(cls):
            if f.name in data:
                try:
                    setattr(s, f.name, cls.field_type(f.name)(data[f.name]))
                except (TypeError, ValueError):
                    pass
        return s

    @classmethod
    def field_type(cls, name: str) -> type:
        """Declared type of a settings field (annotations are strings under `from __future__`)."""
        return {"int": int, "float": float, "bool": bool, "str": str}[cls.__dataclass_fields__[name].type]

    def __post_init__(self) -> None:
        for f in fields(self):
            setattr(self, f.name, self.field_type(f.name)(getattr(self, f.name)))

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
