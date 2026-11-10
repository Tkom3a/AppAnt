from __future__ import annotations

import asyncio
import json
import traceback
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


def in_sleep_window(start: str, end: str, tz_name: str, now: Optional[datetime] = None) -> bool:
    from zoneinfo import ZoneInfo

    moment = now or utcnow()
    try:
        local = moment.astimezone(ZoneInfo(tz_name or "Europe/Moscow"))
    except Exception:
        # В python:3.12-slim нет tzdata, пока пакет не установлен.
        local = moment.astimezone(timezone(timedelta(hours=3)))

    def minutes(value: str) -> int:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)

    try:
        start_m = minutes(start)
        end_m = minutes(end)
    except (ValueError, AttributeError):
        return False
    current = local.hour * 60 + local.minute
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= current < end_m
    return current >= start_m or current < end_m


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


def load_pool_file(settings: Settings) -> list[dict]:
    path = settings.pools_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден {path.name}. Скопируйте pools.json.example в pools.json"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("pools.json должен быть списком")
    items: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        try:
            priority = int(item.get("priority", index + 1))
        except (TypeError, ValueError):
            priority = index + 1
        items.append(
            {
                "name": str(item.get("name") or f"Пул {index + 1}").strip(),
                "url": str(item.get("url") or "").strip(),
                "user": str(item.get("user") or item.get("wallet") or "").strip(),
                "password": str(item.get("pass") if item.get("pass") is not None else item.get("password") or "x"),
                "priority": priority,
                "order": index,
            }
        )
    items.sort(key=lambda pool: (pool["priority"], pool["order"]))
    filled = [pool for pool in items if pool["url"]]
    if not filled:
        raise ValueError("В pools.json нет пула с адресом")
    return filled[:3]


def save_pool_order(settings: Settings, order: list[int]) -> None:
    path = settings.pools_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден {path.name}. Скопируйте pools.json.example в pools.json"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("pools.json должен быть списком")
    ranked: list[tuple[dict, int, int]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("url") or "").strip():
            continue
        try:
            priority = int(item.get("priority", index + 1))
        except (TypeError, ValueError):
            priority = index + 1
        ranked.append((item, priority, index))
    ranked.sort(key=lambda row: (row[1], row[2]))
    ranked = ranked[:3]
    if sorted(order) != list(range(len(ranked))):
        raise ValueError("Передан неполный порядок пулов")
    chosen = {id(ranked[index][0]) for index in order}
    rewritten: list[dict] = []
    for position, index in enumerate(order):
        item = dict(ranked[index][0])
        item["priority"] = position + 1
        rewritten.append(item)
    for item in raw:
        if isinstance(item, dict) and id(item) not in chosen:
            rewritten.append(item)
    path.write_text(
        json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def worker_name(wallet: str, worker: str, known: Optional[list[str]] = None) -> str:
    wallet = wallet.strip()
    suffix = str(worker or "").strip().lstrip(".")
    if not wallet or not suffix:
        return wallet
    if "." in wallet:
        head, tail = wallet.rsplit(".", 1)
        tail = tail.strip()
        known_set = {str(item or "").strip().lstrip(".") for item in (known or [])}
        known_set.discard("")
        if tail in known_set or tail.isdigit():
            wallet = head
    return f"{wallet}.{suffix}"


def slots_for_miner(pools: list[dict], worker: str, known: Optional[list[str]] = None) -> list[dict]:
    slots = [
        {
            "url": pool["url"],
            "user": worker_name(pool["user"], worker, known),
            "password": pool["password"],
        }
        for pool in pools[:3]
    ]
    while len(slots) < 3:
        slots.append({"url": "", "user": "", "password": ""})
    return slots


class Monitor:
    def __init__(self, settings: Settings, store: StateStore, notify: Notify) -> None:
        self.settings = settings
        self.store = store
        self.notify = notify
        self.client = MinerClient(settings.location_ip, settings.miner_timeout_seconds)
        self.miners_error = ""
        try:
            self.miners = load_miners(settings)
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
            self.miners = []
            self.miners_error = str(exc)
            print(f"ОШИБКА: miners.json не прочитан ({settings.miners_path()}): {exc}")
        pools_path = settings.pools_path()
        if pools_path.is_dir():
            print(
                "ОШИБКА: pools.json смонтирован как папка. "
                "Остановите контейнер, удалите эту папку на сервере и положите файл pools.json"
            )
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
        while not self._stop.is_set():
            try:
                await self.poll_all()
            except Exception:
                traceback.print_exc()
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def poll_all(self) -> list[MinerSnapshot]:
        results = await asyncio.gather(
            *[self.poll_one(miner) for miner in self.miners],
            return_exceptions=False,
        )
        await self.apply_sleep_schedule()
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
        if self.intentionally_sleeping(miner.id) or self.schedule_wants_sleep():
            snapshot.status = "sleeping"
            snapshot.last_error = None
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
        if snapshot.status == "sleeping" or self.intentionally_sleeping(miner.id) or self.schedule_wants_sleep():
            return False
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

    def intentionally_sleeping(self, miner_id: str) -> bool:
        return bool(self.store.state.sleep_applied.get(miner_id))

    def schedule_wants_sleep(self) -> bool:
        state = self.store.state
        if not state.sleep_enabled:
            return False
        return in_sleep_window(state.sleep_start, state.sleep_end, state.sleep_timezone)

    def miners_by_ids(self, miner_ids: list[str]) -> list[MinerConfig]:
        if not miner_ids:
            return list(self.miners)
        return [self.miner_by_id(item) for item in miner_ids]

    async def apply_sleep_schedule(self) -> None:
        state = self.store.state
        if not state.sleep_enabled:
            return
        should_sleep = in_sleep_window(state.sleep_start, state.sleep_end, state.sleep_timezone)
        for miner in self.miners:
            if state.sleep_applied.get(miner.id) is should_sleep:
                continue
            await self.set_sleep_one(miner, should_sleep, "расписание")
            state = self.store.state

    async def set_sleep_one(self, miner: MinerConfig, sleep: bool, reason: str) -> dict:
        user, password = credentials_for(miner.id, miner.firmware, self.settings)
        view = await self.client.set_sleep(miner.firmware, miner.port, user, password, sleep)
        if view.ok:
            self.store.set_sleep_applied(miner.id, sleep)
            snapshot = self.store.get_snapshot(miner.id)
            if snapshot:
                snapshot.status = "sleeping" if sleep else "unknown"
                snapshot.last_error = None
                self.store.put_snapshot(snapshot)
            verb = "сон" if sleep else "пробуждение"
            await self._emit("info", miner.id, f"{miner.name}: {verb} ({reason})")
        else:
            verb = "усыпить" if sleep else "разбудить"
            await self._emit("warn", miner.id, f"{miner.name}: не удалось {verb} — {view.detail}")
        return {"id": miner.id, "name": miner.name, "ok": view.ok, "detail": view.detail}

    async def read_pools(self, miner: MinerConfig) -> dict:
        user, password = credentials_for(miner.id, miner.firmware, self.settings)
        view = await self.client.read_control(miner.firmware, miner.port, user, password)
        return {
            "id": miner.id,
            "name": miner.name,
            "firmware": miner.firmware,
            "ok": view.ok,
            "detail": view.detail,
            "sleeping": view.sleeping,
            "pools": view.pools or [],
        }

    async def write_pools(
        self,
        miner: MinerConfig,
        pools: list[dict],
        keep_worker: bool,
    ) -> dict:
        user, password = credentials_for(miner.id, miner.firmware, self.settings)
        slots = [dict(item) for item in pools]
        if keep_worker:
            current = await self.client.read_control(miner.firmware, miner.port, user, password)
            if not current.ok or not current.pools:
                return {
                    "id": miner.id,
                    "name": miner.name,
                    "ok": False,
                    "detail": current.detail or "не удалось прочитать текущие воркеры",
                }
            for index, slot in enumerate(slots):
                existing = current.pools[index]["user"] if index < len(current.pools) else ""
                if existing:
                    slot["user"] = existing
        view = await self.client.set_pools(miner.firmware, miner.port, user, password, slots)
        if miner.firmware == "vnish":
            method = "Vnish: пароль и unlock"
        else:
            method = "Antminer: логин и digest"
        if view.ok:
            await self._emit("info", miner.id, f"{miner.name}: пулы обновлены ({method})")
        else:
            await self._emit("warn", miner.id, f"{miner.name}: не удалось сменить пул — {view.detail}")
        return {
            "id": miner.id,
            "name": miner.name,
            "firmware": miner.firmware,
            "ok": view.ok,
            "detail": view.detail or method,
        }

    def pool_file_status(self) -> dict:
        self.miners = load_miners(self.settings)
        try:
            pools = load_pool_file(self.settings)
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
            return {"ok": False, "detail": str(exc), "pools": [], "miners": []}
        primary = pools[0]["user"] if pools else ""
        known = [miner.worker for miner in self.miners]
        return {
            "ok": True,
            "detail": "",
            "pools": [
                {
                    "name": pool["name"],
                    "url": pool["url"],
                    "user": pool["user"],
                    "priority": pool["priority"],
                }
                for pool in pools
            ],
            "miners": [
                {
                    "id": miner.id,
                    "name": miner.name,
                    "firmware": miner.firmware,
                    "worker": miner.worker,
                    "worker_user": worker_name(primary, miner.worker, known),
                }
                for miner in self.miners
            ],
        }

    async def apply_pool_file(self, miner_ids: Optional[list[str]] = None) -> dict:
        status = self.pool_file_status()
        if not status["ok"]:
            return status
        pools = load_pool_file(self.settings)
        miners = self.miners_by_ids(miner_ids or [])
        known = [miner.worker for miner in self.miners]
        results = []
        for miner in miners:
            slots = slots_for_miner(pools, miner.worker, known)
            results.append(await self.write_pools(miner, slots, keep_worker=False))
        status["results"] = results
        return status

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
