from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


Firmware = Literal["antminer", "vnish"]
MinerStatus = Literal[
    "unknown",
    "healthy",
    "low",
    "down",
    "rebooting",
    "cooldown",
    "sleeping",
]


class MinerConfig(BaseModel):
    id: str
    name: str
    firmware: Firmware
    port: int
    expected_ths: float = 120
    auto_reboot: bool = True
    # Optional absolute thresholds; if omitted, ratios from .env are used
    alert_ths: Optional[float] = None
    reboot_ths: Optional[float] = None
    # Номер воркера. Дописывается к кошельку из pools.json: кошелек.1
    worker: str = ""

    @field_validator("worker", mode="before")
    @classmethod
    def worker_as_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MinerSnapshot(BaseModel):
    id: str
    name: str
    firmware: Firmware
    port: int
    expected_ths: float
    auto_reboot: bool
    status: MinerStatus = "unknown"
    online: bool = False
    hashrate_ths: Optional[float] = None
    temperature_c: Optional[float] = None
    elapsed_seconds: Optional[int] = None
    last_ping_at: Optional[str] = None
    last_error: Optional[str] = None
    last_reboot_at: Optional[str] = None
    last_reboot_reason: Optional[str] = None
    cooldown_until: Optional[str] = None
    auto_reboots_in_window: int = 0


class EventItem(BaseModel):
    ts: str
    level: Literal["info", "warn", "crit"]
    miner_id: Optional[str] = None
    message: str


class PoolSlot(BaseModel):
    url: str = ""
    user: str = ""
    password: str = ""


class PoolPreset(BaseModel):
    id: str
    name: str
    pools: list[PoolSlot] = Field(default_factory=list)


class AppState(BaseModel):
    auto_reboot_enabled: bool = True
    expo_tokens: list[str] = Field(default_factory=list)
    snapshots: dict[str, MinerSnapshot] = Field(default_factory=dict)
    events: list[EventItem] = Field(default_factory=list)
    reboot_times: dict[str, list[str]] = Field(default_factory=dict)
    sleep_enabled: bool = False
    sleep_start: str = "23:00"
    sleep_end: str = "07:00"
    sleep_timezone: str = "Europe/Moscow"
    sleep_applied: dict[str, bool] = Field(default_factory=dict)
    pool_presets: list[PoolPreset] = Field(default_factory=list)


class RebootRequest(BaseModel):
    reason: str = "manual"


class DeviceRegister(BaseModel):
    token: str
    platform: str = "ios"


class SettingsPatch(BaseModel):
    auto_reboot_enabled: Optional[bool] = None
    sleep_enabled: Optional[bool] = None
    sleep_start: Optional[str] = None
    sleep_end: Optional[str] = None


class SleepCommand(BaseModel):
    sleep: bool
    miner_ids: list[str] = Field(default_factory=list)


class PoolsCommand(BaseModel):
    pools: list[PoolSlot]
    miner_ids: list[str] = Field(default_factory=list)
    keep_worker: bool = False


class PoolOrderBody(BaseModel):
    order: list[int]


class PoolApplyBody(BaseModel):
    miner_ids: list[str] = Field(default_factory=list)


class PresetCreate(BaseModel):
    name: str
    pools: list[PoolSlot]
