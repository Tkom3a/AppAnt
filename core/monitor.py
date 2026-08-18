from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import Settings, get_settings
from miner_client import MinerClient, MinerReading, credentials_for
from models import EventItem, MinerConfig, MinerSnapshot, MinerStatus
from notify import Notify
from store import StateStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_miners(settings: Settings) -> list[MinerConfig]:
    path = settings.miners_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден {path}. Скопируйте miners.json.example в miners.json"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("miners.json must be a list")
    return [MinerConfig.model_validate(item) for item in raw]


class Monitor:
    def __init__(self, settings: Settings, store: StateStore, notify: Notify) -> None:
        self.settings = settings
        self.store = store
        self.notify = notify
        self.client = MinerClient(settings.location_ip, settings.miner_timeout_seconds)
        self.miners = load_miners(settings)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def miner_by_id(self, miner_id: str) -> MinerConfig:
        for miner in self.miners:
            if miner.id == miner_id:
                return miner
        raise KeyError(miner_id)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="asic-monitor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        await self.poll_all()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                await self.poll_all()

    async def poll_all(self) -> list[MinerSnapshot]:
        results = await asyncio.gather(
            *[self.poll_one(miner) for miner in self.miners],
            return_exceptions=False,
        )
        return list(results)

    async def poll_one(self, miner: MinerConfig) -> MinerSnapshot:
        user, password = credentials_for(miner.id, miner.firmware, self.settings)
        reading = await self.client.read(
            miner.firmware,
            miner.port,
            user,
            password,
            miner.expected_ths,
        )
        previous = self.store.get_snapshot(miner.id)
        snapshot = self._build_snapshot(miner, reading, previous)
        await self._react(miner, snapshot, previous)
        self.store.put_snapshot(snapshot)
        return snapshot

    def _build_snapshot(
        self,
        miner: MinerConfig,
        reading: MinerReading,
        previous: Optional[MinerSnapshot],
    ) -> MinerSnapshot:
        now = utcnow()
        cooldown_until = parse_iso(previous.cooldown_until if previous else None)
        in_cooldown = bool(cooldown_until and cooldown_until > now)
        hashrate = reading.hashrate_ths if reading.online else 0.0
        if hashrate is None:
            hashrate = 0.0

        status: MinerStatus
        if in_cooldown:
            status = "cooldown"
        elif not reading.online:
            status = "down"
        elif hashrate <= 0:
            status = "down"
        elif hashrate < self._alert_ths(miner):
            status = "low"
        else:
            status = "healthy"

        return MinerSnapshot(
            id=miner.id,
            name=miner.name,
            firmware=miner.firmware,
            port=miner.port,
            expected_ths=miner.expected_ths,
            auto_reboot=miner.auto_reboot,
            status=status,
            online=reading.online,
            hashrate_ths=round(hashrate, 2) if reading.online else None,
            temperature_c=reading.temperature_c,
            elapsed_seconds=reading.elapsed_seconds,
            last_ping_at=iso(now),
            last_error=None if reading.online else reading.error,
            last_reboot_at=previous.last_reboot_at if previous else None,
            last_reboot_reason=previous.last_reboot_reason if previous else None,
            cooldown_until=previous.cooldown_until if previous else None,
            auto_reboots_in_window=previous.auto_reboots_in_window if previous else 0,
        )

    def _alert_ths(self, miner: MinerConfig) -> float:
        if miner.alert_ths is not None:
            return miner.alert_ths
        return miner.expected_ths * self.settings.hashrate_alert_ratio

    def _reboot_ths(self, miner: MinerConfig) -> float:
        if miner.reboot_ths is not None:
            return miner.reboot_ths
        return miner.expected_ths * self.settings.hashrate_reboot_ratio

    async def _react(
        self,
        miner: MinerConfig,
        snapshot: MinerSnapshot,
        previous: Optional[MinerSnapshot],
    ) -> None:
        prev_status = previous.status if previous else "unknown"
        if snapshot.status in {"low", "down"} and prev_status not in {"low", "down"}:
            ths = snapshot.hashrate_ths
            text = (
                f"{miner.name}: нет связи ({snapshot.last_error or 'offline'})"
                if snapshot.status == "down" and not snapshot.online
                else f"{miner.name}: хэшрейт {self._fmt(ths)} при норме {miner.expected_ths:g} TH/s"
            )
            await self._emit("warn" if snapshot.status == "low" else "crit", miner.id, text)

        should_reboot = self._should_auto_reboot(miner, snapshot)
        if should_reboot:
            await self.reboot(miner, reason="auto: low/absent hashrate", automatic=True)

    def _should_auto_reboot(self, miner: MinerConfig, snapshot: MinerSnapshot) -> bool:
        if not self.store.state.auto_reboot_enabled:
            return False
        if not miner.auto_reboot:
            return False
        cooldown_until = parse_iso(snapshot.cooldown_until)
        if cooldown_until and cooldown_until > utcnow():
            return False
        hashrate = snapshot.hashrate_ths if snapshot.hashrate_ths is not None else 0.0
        if snapshot.online and hashrate >= self._reboot_ths(miner):
            return False
        return True

    async def reboot(
        self,
        miner: MinerConfig,
        reason: str,
        automatic: bool,
    ) -> MinerSnapshot:
        now = utcnow()
        if automatic:
            recent = [
                item
                for item in self.store.state.reboot_times.get(miner.id, [])
                if (parsed := parse_iso(item))
                and parsed
                >= now
                - timedelta(seconds=self.settings.auto_reboot_window_seconds)
            ]
            if len(recent) >= self.settings.max_auto_reboots:
                snapshot = self.store.get_snapshot(miner.id)
                if snapshot:
                    if snapshot.auto_reboots_in_window <= self.settings.max_auto_reboots:
                        await self._emit(
                            "crit",
                            miner.id,
                            f"{miner.name}: авторебут остановлен — уже "
                            f"{len(recent)} перезагрузок за окно. Проверьте асик вручную.",
                        )
                        snapshot.auto_reboots_in_window = self.settings.max_auto_reboots + 1
                        self.store.put_snapshot(snapshot)
                    return snapshot
                raise RuntimeError("miner snapshot missing")

        user, password = credentials_for(miner.id, miner.firmware, self.settings)
        ok, detail = await self.client.reboot(
            miner.firmware,
            miner.port,
            user,
            password,
        )
        previous = self.store.get_snapshot(miner.id)
        cooldown = now + timedelta(seconds=self.settings.reboot_cooldown_seconds)
        snapshot = previous or MinerSnapshot(
            id=miner.id,
            name=miner.name,
            firmware=miner.firmware,
            port=miner.port,
            expected_ths=miner.expected_ths,
            auto_reboot=miner.auto_reboot,
        )
        snapshot.status = "rebooting" if ok else snapshot.status
        snapshot.last_reboot_at = iso(now)
        snapshot.last_reboot_reason = reason
        snapshot.cooldown_until = iso(cooldown)
        snapshot.last_error = None if ok else detail
        if ok and automatic:
            count = self.store.add_reboot_time(
                miner.id,
                iso(now),
                self.settings.auto_reboot_window_seconds,
            )
            snapshot.auto_reboots_in_window = count
        self.store.put_snapshot(snapshot)

        if ok:
            wait_min = self.settings.reboot_cooldown_seconds // 60
            kind = "авто" if automatic else "ручная"
            await self._emit(
                "warn",
                miner.id,
                f"{miner.name}: {kind} перезагрузка. Жду {wait_min} мин. ({reason})",
            )
        else:
            await self._emit(
                "crit",
                miner.id,
                f"{miner.name}: не удалось перезагрузить — {detail}",
            )
        return snapshot

    async def _emit(self, level: str, miner_id: Optional[str], message: str) -> None:
        title = "УВЫ control"
        self.store.add_event(
            EventItem(ts=iso(), level=level, miner_id=miner_id, message=message)
        )
        await self.notify.send(title, message, level=level)

    @staticmethod
    def _fmt(ths: Optional[float]) -> str:
        if ths is None:
            return "нет данных"
        return f"{ths:.1f} TH/s"


def build_monitor(store: StateStore) -> Monitor:
    settings = get_settings()
    notify = Notify(
        ntfy_url=settings.ntfy_url,
        ntfy_topic=settings.ntfy_topic,
        telegram_bot_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
        get_expo_tokens=lambda: list(store.state.expo_tokens),
    )
    monitor = Monitor(settings, store, notify)
    if not store.state.snapshots:
        store.update(auto_reboot_enabled=settings.auto_reboot_enabled)
    return monitor
