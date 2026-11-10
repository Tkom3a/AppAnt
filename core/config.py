from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


CORE_DIR = Path(__file__).resolve().parent


def _project_root() -> Path:
    for candidate in (CORE_DIR.parent, CORE_DIR, Path.cwd()):
        if (candidate / ".env").exists() or (candidate / "miners.json").exists():
            return candidate
    return CORE_DIR.parent


ROOT_DIR = _project_root()
load_dotenv(ROOT_DIR / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    core_host: str = "0.0.0.0"
    core_port: int = 8486
    api_token: str = "CHANGE_ME_LONG_SECRET"

    location_ip: str = "127.0.0.1"
    miner_timeout_seconds: float = 8.0

    antminer_user: str = "root"
    antminer_password: str = "root"
    vnish_password: str = "admin"

    poll_interval_seconds: int = 600
    reboot_cooldown_seconds: int = 600
    auto_reboot_enabled: bool = True
    hashrate_alert_ratio: float = 0.7
    hashrate_reboot_ratio: float = 0.3
    max_auto_reboots: int = 3
    auto_reboot_window_seconds: int = 7200

    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    miners_file: str = "miners.json"
    pools_file: str = "pools.json"

    def miners_path(self) -> Path:
        path = Path(self.miners_file)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    def pools_path(self) -> Path:
        path = Path(self.pools_file)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    def data_dir(self) -> Path:
        path = ROOT_DIR / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def extra_env(self) -> dict[str, str]:
        """Raw env for per-miner password overrides like MINER_ASIC1_PASSWORD."""
        from os import environ

        return dict(environ)


@lru_cache
def get_settings() -> Settings:
    return Settings()
