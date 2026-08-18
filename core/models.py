from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Firmware = Literal["antminer", "vnish"]
MinerStatus = Literal[
    "unknown",
    "healthy",
    "low",
    "down",
    "rebooting",
    "cooldown",
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


class AppState(BaseModel):
    auto_reboot_enabled: bool = True
    expo_tokens: list[str] = Field(default_factory=list)
    snapshots: dict[str, MinerSnapshot] = Field(default_factory=dict)
    events: list[EventItem] = Field(default_factory=list)
    reboot_times: dict[str, list[str]] = Field(default_factory=dict)


class RebootRequest(BaseModel):
    reason: str = "manual"


class DeviceRegister(BaseModel):
    token: str
    platform: str = "ios"


class SettingsPatch(BaseModel):
    auto_reboot_enabled: Optional[bool] = None
