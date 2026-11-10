from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from httpx import DigestAuth


@dataclass
class ControlView:
    ok: bool
    detail: str = ""
    pools: Optional[list[dict[str, str]]] = None
    sleeping: Optional[bool] = None


@dataclass
class MinerReading:
    online: bool
    hashrate_ths: Optional[float] = None
    temperature_c: Optional[float] = None
    elapsed_seconds: Optional[int] = None
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None
    pools: Optional[list[dict[str, Any]]] = None
    fans: Optional[list[int]] = None
    temperatures: Optional[list[dict[str, Any]]] = None


def credentials_for(miner_id: str, firmware: str, settings) -> tuple[str, str]:
    env = settings.extra_env()
    key = miner_id.upper().replace("-", "_")
    if firmware == "vnish":
        password = env.get(f"MINER_{key}_PASSWORD", settings.vnish_password)
        return "", password
    user = env.get(f"MINER_{key}_USER", settings.antminer_user)
    password = env.get(f"MINER_{key}_PASSWORD", settings.antminer_password)
    return user, password


def to_ths(value: Any, expected_ths: float) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if expected_ths > 0 and number > expected_ths * 5:
        return number / 1000.0
    if expected_ths <= 0 and number >= 1000:
        return number / 1000.0
    return number


def _walk(obj: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.append((str(key), value))
            found.extend(_walk(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk(item))
    return found


def _first_number(pairs: list[tuple[str, Any]], keys: list[str]) -> Optional[float]:
    lowered = {key.lower(): key for key, _ in pairs}
    for wanted in keys:
        real = lowered.get(wanted.lower())
        if real is None:
            continue
        for key, value in pairs:
            if key == real:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
    return None


def _plain_number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pools_from_items(items: Any) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return pools
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("URL") or "").strip()
        user = str(item.get("user") or item.get("User") or "").strip()
        status = str(item.get("status") or item.get("Status") or "").strip()
        flag = item.get("Stratum Active", item.get("stratum_active"))
        active = bool(flag) or status.lower() in {"alive", "active", "working", "enabled"}
        if not url and not user:
            continue
        pools.append({"url": url, "user": user, "status": status, "active": active})
    if pools and not any(pool["active"] for pool in pools):
        pools[0]["active"] = True
    return pools[:3]


def _hardware_antminer(payload: Any) -> tuple[list[dict[str, Any]], list[int]]:
    temps: list[dict[str, Any]] = []
    fans: list[int] = []
    labels = {
        "temp_max": "макс.",
        "temperature": "плата",
        "temp1": "плата 1",
        "temp2": "плата 2",
        "temp3": "плата 3",
        "temp2_1": "чипы 1",
        "temp2_2": "чипы 2",
        "temp2_3": "чипы 3",
    }
    seen: set[str] = set()
    seen_fans: set[str] = set()
    for key, value in _walk(payload):
        number = _plain_number(value)
        if number is None:
            continue
        low = key.lower()
        if low in labels and low not in seen:
            seen.add(low)
            temps.append({"label": labels[low], "value": round(number, 1)})
        fan_key = low.replace(" ", "")
        if fan_key in seen_fans:
            continue
        if fan_key in {"fanspeedin", "fanspeedout"} or (
            low.startswith("fan") and "pwm" not in low and "num" not in low and "speed" not in low
        ):
            rpm = int(number)
            if 100 <= rpm <= 20000:
                seen_fans.add(fan_key)
                fans.append(rpm)
    return temps[:8], fans[:8]


def _hardware_vnish(miner: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    temps: list[dict[str, Any]] = []
    chip = miner.get("chip_temp")
    pcb = miner.get("pcb_temp")
    if isinstance(chip, dict) and _plain_number(chip.get("max")) is not None:
        temps.append({"label": "чипы", "value": round(float(chip["max"]), 1)})
    if isinstance(pcb, dict) and _plain_number(pcb.get("max")) is not None:
        temps.append({"label": "платы", "value": round(float(pcb["max"]), 1)})
    chains = miner.get("chains")
    if isinstance(chains, list):
        for index, chain in enumerate(chains, 1):
            if not isinstance(chain, dict):
                continue
            for key, title in (("chip_temp", "чипы"), ("pcb_temp", "плата")):
                block = chain.get(key)
                if isinstance(block, dict) and _plain_number(block.get("max")) is not None:
                    temps.append({"label": f"{title} {index}", "value": round(float(block["max"]), 1)})
    fans: list[int] = []
    cooling = miner.get("cooling") if isinstance(miner.get("cooling"), dict) else {}
    for fan in cooling.get("fans") or []:
        if not isinstance(fan, dict):
            continue
        rpm = _plain_number(fan.get("rpm"))
        if rpm is None:
            continue
        fans.append(int(rpm))
    return temps[:12], fans[:8]


def parse_antminer(payload: Any, expected_ths: float) -> MinerReading:
    pairs = _walk(payload)
    hashrate = _first_number(
        pairs,
        [
            "rate_5s",
            "GHS 5s",
            "ghs_5s",
            "GHS5s",
            "rate_30m",
            "GHS av",
            "ghs_av",
            "rate_avg",
            "rate_average",
            "total rate",
            "total_rate",
        ],
    )
    temp = _first_number(
        pairs,
        ["temp_max", "temp1", "temperature", "temp"],
    )
    elapsed = _first_number(pairs, ["elapsed", "Elapsed"])
    unit = None
    for key, value in pairs:
        if key.lower() in {"rate_unit", "unit"} and isinstance(value, str):
            unit = value.lower()
            break
    ths = None
    if hashrate is not None:
        if unit and "th" in unit:
            ths = hashrate
        elif unit and "gh" in unit:
            ths = hashrate / 1000.0
        else:
            ths = to_ths(hashrate, expected_ths)
    temps, fans = _hardware_antminer(payload)
    pools = []
    if isinstance(payload, dict):
        pools = _pools_from_items(payload.get("POOLS") or payload.get("pools"))
    if temp is None and temps:
        temp = max(item["value"] for item in temps)
    return MinerReading(
        online=True,
        hashrate_ths=ths,
        temperature_c=temp,
        elapsed_seconds=int(elapsed) if elapsed is not None else None,
        raw=payload if isinstance(payload, dict) else None,
        pools=pools,
        fans=fans,
        temperatures=temps,
    )


def parse_vnish(payload: Any, expected_ths: float) -> MinerReading:
    miner = payload.get("miner", payload) if isinstance(payload, dict) else {}
    if not isinstance(miner, dict):
        miner = {}
    hashrate = None
    for key in (
        "hr_realtime",
        "instant_hashrate",
        "hr_average",
        "average_hashrate",
        "hr_nominal",
    ):
        if miner.get(key) not in (None, "", 0, 0.0):
            hashrate = miner.get(key)
            break
    if hashrate in (None, "", 0, 0.0):
        hashrate = miner.get("hr_realtime", miner.get("instant_hashrate", 0))
    temp = None
    chip = miner.get("chip_temp")
    pcb = miner.get("pcb_temp")
    if isinstance(chip, dict):
        temp = chip.get("max")
    elif isinstance(pcb, dict):
        temp = pcb.get("max")
    elapsed = None
    status = miner.get("miner_status")
    if isinstance(status, dict):
        elapsed = status.get("miner_state_time")
    temps, fans = _hardware_vnish(miner)
    pools = _pools_from_items(miner.get("pools"))
    if temp is None and temps:
        temp = max(item["value"] for item in temps)
    return MinerReading(
        online=True,
        hashrate_ths=to_ths(hashrate, expected_ths),
        temperature_c=float(temp) if temp is not None else None,
        elapsed_seconds=int(elapsed) if elapsed is not None else None,
        raw=payload if isinstance(payload, dict) else None,
        pools=pools,
        fans=fans,
        temperatures=temps,
    )


class MinerClient:
    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout

    def _url(self, port: int, path: str) -> str:
        return f"http://{self.host}:{port}{path}"

    async def read(
        self,
        firmware: str,
        port: int,
        user: str,
        password: str,
        expected_ths: float,
    ) -> MinerReading:
        try:
            if firmware == "vnish":
                return await self._read_vnish(port, password, expected_ths)
            return await self._read_antminer(port, user, password, expected_ths)
        except httpx.TimeoutException:
            return MinerReading(online=False, error="timeout")
        except httpx.HTTPError as exc:
            return MinerReading(online=False, error=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return MinerReading(online=False, error=str(exc)[:200])

    async def reboot(
        self,
        firmware: str,
        port: int,
        user: str,
        password: str,
    ) -> tuple[bool, str]:
        try:
            if firmware == "vnish":
                return await self._reboot_vnish(port, password)
            return await self._reboot_antminer(port, user, password)
        except httpx.TimeoutException:
            # Асик часто обрывает соединение в момент ребута — это успех.
            return True, "reboot sent (timeout after request)"
        except httpx.HTTPError as exc:
            return False, str(exc)[:200]

    async def _read_antminer(
        self,
        port: int,
        user: str,
        password: str,
        expected_ths: float,
    ) -> MinerReading:
        auth = DigestAuth(user, password)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            last_error = "no response"
            for path in ("/cgi-bin/summary.cgi", "/cgi-bin/stats.cgi"):
                response = await client.get(self._url(port, path), auth=auth)
                if response.status_code in (401, 403):
                    return MinerReading(online=False, error="auth failed")
                if response.status_code >= 400:
                    last_error = f"HTTP {response.status_code} on {path}"
                    continue
                payload = _as_json(response)
                if payload is None:
                    last_error = f"invalid json on {path}"
                    continue
                reading = parse_antminer(payload, expected_ths)
                if reading.hashrate_ths is not None or reading.online:
                    await self._fill_antminer_extra(client, port, auth, reading)
                    return reading
            return MinerReading(online=False, error=last_error)

    async def _fill_antminer_extra(self, client, port: int, auth, reading: MinerReading) -> None:
        try:
            response = await client.get(self._url(port, "/cgi-bin/pools.cgi"), auth=auth)
            payload = _as_json(response) if response.status_code < 400 else None
            if isinstance(payload, dict):
                pools = _pools_from_items(payload.get("POOLS") or payload.get("pools"))
                if pools:
                    reading.pools = pools
        except (httpx.HTTPError, ValueError):
            pass
        if reading.temperatures or reading.fans:
            return
        try:
            response = await client.get(self._url(port, "/cgi-bin/stats.cgi"), auth=auth)
            payload = _as_json(response) if response.status_code < 400 else None
            if isinstance(payload, dict):
                temps, fans = _hardware_antminer(payload)
                if temps:
                    reading.temperatures = temps
                if fans:
                    reading.fans = fans
        except (httpx.HTTPError, ValueError):
            pass

    async def _reboot_antminer(
        self,
        port: int,
        user: str,
        password: str,
    ) -> tuple[bool, str]:
        auth = DigestAuth(user, password)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                self._url(port, "/cgi-bin/reboot.cgi"),
                auth=auth,
            )
            if response.status_code in (401, 403):
                return False, "auth failed"
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code}"
            return True, "ok"

    async def _vnish_token(
        self,
        client: httpx.AsyncClient,
        port: int,
        password: str,
    ) -> str:
        response = await client.post(
            self._url(port, "/api/v1/unlock"),
            json={"pw": password},
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("vnish did not return token")
        return str(token)

    async def _read_vnish(
        self,
        port: int,
        password: str,
        expected_ths: float,
    ) -> MinerReading:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            token = await self._vnish_token(client, port, password)
            response = await client.get(
                self._url(port, "/api/v1/summary"),
                headers={"Authorization": token},
            )
            if response.status_code in (401, 403):
                return MinerReading(online=False, error="auth failed")
            response.raise_for_status()
            payload = _as_json(response)
            if payload is None:
                return MinerReading(online=False, error="invalid json")
            reading = parse_vnish(payload, expected_ths)
            if not reading.pools:
                try:
                    settings = await self._vnish_call(client, port, token, "GET", "/api/v1/settings")
                    data = _as_json(settings) if settings.status_code < 400 else None
                    if isinstance(data, dict):
                        reading.pools = _pools_from_items(_find_pools(data) or [])
                except (httpx.HTTPError, ValueError):
                    pass
            return reading

    async def _reboot_vnish(
        self,
        port: int,
        password: str,
    ) -> tuple[bool, str]:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            token = await self._vnish_token(client, port, password)
            # pyasic: system/* хочет Bearer
            response = await client.post(
                self._url(port, "/api/v1/system/reboot"),
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code in (401, 403):
                response = await client.post(
                    self._url(port, "/api/v1/system/reboot"),
                    headers={"Authorization": token},
                )
            if response.status_code in (401, 403):
                return False, "auth failed"
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code}"
            return True, "ok"

    async def read_control(
        self,
        firmware: str,
        port: int,
        user: str,
        password: str,
    ) -> ControlView:
        try:
            if firmware == "vnish":
                return await self._read_vnish_control(port, password)
            return await self._read_antminer_control(port, user, password)
        except httpx.TimeoutException:
            return ControlView(ok=False, detail="timeout")
        except httpx.HTTPError as exc:
            return ControlView(ok=False, detail=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return ControlView(ok=False, detail=str(exc)[:200])

    async def set_pools(
        self,
        firmware: str,
        port: int,
        user: str,
        password: str,
        pools: list[dict[str, str]],
    ) -> ControlView:
        try:
            if firmware == "vnish":
                return await self._set_vnish_pools(port, password, pools)
            return await self._set_antminer_pools(port, user, password, pools)
        except httpx.TimeoutException:
            return ControlView(ok=False, detail="таймаут записи. Проверьте пулы в веб-морде асика")
        except httpx.HTTPError as exc:
            return ControlView(ok=False, detail=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return ControlView(ok=False, detail=str(exc)[:200])

    async def set_sleep(
        self,
        firmware: str,
        port: int,
        user: str,
        password: str,
        sleep: bool,
    ) -> ControlView:
        try:
            if firmware == "vnish":
                return await self._set_vnish_sleep(port, password, sleep)
            return await self._set_antminer_sleep(port, user, password, sleep)
        except httpx.TimeoutException:
            return ControlView(ok=False, detail="таймаут. Асик мог уже принять команду")
        except httpx.HTTPError as exc:
            return ControlView(ok=False, detail=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return ControlView(ok=False, detail=str(exc)[:200])

    async def _read_antminer_control(
        self,
        port: int,
        user: str,
        password: str,
    ) -> ControlView:
        conf = await self._get_antminer_conf(port, user, password)
        if isinstance(conf, ControlView):
            return conf
        return ControlView(
            ok=True,
            pools=_slots_from_raw(conf.get("pools")),
            sleeping=_sleeping_from_conf(conf),
        )

    async def _get_antminer_conf(
        self,
        port: int,
        user: str,
        password: str,
    ) -> dict[str, Any] | ControlView:
        auth = DigestAuth(user, password)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(self._url(port, "/cgi-bin/get_miner_conf.cgi"), auth=auth)
        if response.status_code in (401, 403):
            return ControlView(ok=False, detail="auth failed")
        if response.status_code >= 400:
            return ControlView(ok=False, detail=f"HTTP {response.status_code}")
        payload = _as_json(response)
        if not isinstance(payload, dict):
            return ControlView(ok=False, detail="invalid json")
        return payload

    async def _post_antminer_conf(
        self,
        port: int,
        user: str,
        password: str,
        body: dict[str, Any],
    ) -> tuple[bool, str]:
        auth = DigestAuth(user, password)
        timeout = max(self.timeout, 40.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                self._url(port, "/cgi-bin/set_miner_conf.cgi"),
                auth=auth,
                json=body,
            )
        return _conf_result(response)

    async def _set_antminer_pools(
        self,
        port: int,
        user: str,
        password: str,
        pools: list[dict[str, str]],
    ) -> ControlView:
        conf = await self._get_antminer_conf(port, user, password)
        if isinstance(conf, ControlView):
            return conf
        body = _antminer_write_body(conf, pools, conf.get("miner-mode", conf.get("bitmain-work-mode")))
        ok, detail = await self._post_antminer_conf(port, user, password, body)
        if not ok:
            ok, detail = await self._post_antminer_conf(
                port,
                user,
                password,
                {"pools": _antminer_pools(pools), "version": conf.get("version", 4)},
            )
        if not ok:
            return ControlView(ok=False, detail=detail)
        return ControlView(ok=True, detail="ok", pools=_slots_from_raw(_antminer_pools(pools)))

    async def _set_antminer_sleep(
        self,
        port: int,
        user: str,
        password: str,
        sleep: bool,
    ) -> ControlView:
        conf = await self._get_antminer_conf(port, user, password)
        if isinstance(conf, ControlView):
            return conf
        slots = _slots_from_raw(conf.get("pools"))
        body = _antminer_write_body(conf, slots, 1 if sleep else 0)
        ok, detail = await self._post_antminer_conf(port, user, password, body)
        if not ok:
            mode = 1 if sleep else 0
            ok, detail = await self._post_antminer_conf(
                port,
                user,
                password,
                {
                    "miner-mode": mode,
                    "bitmain-work-mode": mode,
                    "version": conf.get("version", 4),
                    "pools": _antminer_pools(slots),
                },
            )
        if not ok:
            return ControlView(ok=False, detail=detail, sleeping=None)
        return ControlView(ok=True, detail="ok", sleeping=sleep, pools=slots)

    async def _read_vnish_control(self, port: int, password: str) -> ControlView:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            token = await self._vnish_token(client, port, password)
            settings = await self._vnish_call(client, port, token, "GET", "/api/v1/settings")
            status = await self._vnish_call(client, port, token, "GET", "/api/v1/status")
        if settings.status_code in (401, 403):
            return ControlView(ok=False, detail="auth failed")
        if settings.status_code >= 400:
            return ControlView(ok=False, detail=f"HTTP {settings.status_code}")
        payload = _as_json(settings)
        if not isinstance(payload, dict):
            return ControlView(ok=False, detail="invalid json")
        raw_pools = _find_pools(payload)
        sleeping = None
        status_payload = _as_json(status) if status.status_code < 400 else None
        if isinstance(status_payload, dict):
            sleeping = _sleeping_from_vnish(status_payload)
        return ControlView(ok=True, pools=_slots_from_raw(raw_pools), sleeping=sleeping)

    async def _set_vnish_pools(
        self,
        port: int,
        password: str,
        pools: list[dict[str, str]],
    ) -> ControlView:
        async with httpx.AsyncClient(timeout=max(self.timeout, 40.0), follow_redirects=True) as client:
            token = await self._vnish_token(client, port, password)
            current = await self._vnish_call(client, port, token, "GET", "/api/v1/settings")
            if current.status_code in (401, 403):
                return ControlView(ok=False, detail="auth failed")
            if current.status_code >= 400:
                return ControlView(ok=False, detail=f"HTTP {current.status_code}")
            payload = _as_json(current)
            if not isinstance(payload, dict):
                return ControlView(ok=False, detail="invalid json")
            body = _replace_vnish_pools(payload, pools)
            saved = await self._vnish_call(client, port, token, "POST", "/api/v1/settings", body)
            if saved.status_code in (401, 403):
                return ControlView(ok=False, detail="auth failed")
            if saved.status_code >= 400:
                return ControlView(ok=False, detail=f"HTTP {saved.status_code}")
            restarted = await self._vnish_call(client, port, token, "POST", "/api/v1/mining/restart")
        detail = "ok"
        if restarted.status_code >= 400:
            detail = "пулы записаны, перезапуск майнинга не подтверждён"
        return ControlView(ok=True, detail=detail, pools=_slots_from_raw(_antminer_pools(pools)))

    async def _set_vnish_sleep(self, port: int, password: str, sleep: bool) -> ControlView:
        path = "/api/v1/mining/pause" if sleep else "/api/v1/mining/resume"
        async with httpx.AsyncClient(timeout=max(self.timeout, 20.0), follow_redirects=True) as client:
            token = await self._vnish_token(client, port, password)
            response = await self._vnish_call(client, port, token, "POST", path)
        if response.status_code in (401, 403):
            return ControlView(ok=False, detail="auth failed")
        if response.status_code >= 400:
            return ControlView(ok=False, detail=f"HTTP {response.status_code}")
        return ControlView(ok=True, detail="ok", sleeping=sleep)

    async def _vnish_call(
        self,
        client: httpx.AsyncClient,
        port: int,
        token: str,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        last: httpx.Response | None = None
        for header in (f"Bearer {token}", token):
            response = await client.request(
                method,
                self._url(port, path),
                headers={"Authorization": header},
                json=payload,
            )
            last = response
            if response.status_code not in (401, 403):
                return response
        assert last is not None
        return last


def _slots_from_raw(raw: Any) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw[:3]:
            if not isinstance(item, dict):
                continue
            slots.append(
                {
                    "url": str(item.get("url") or ""),
                    "user": str(item.get("user") or ""),
                    "password": str(item.get("pass") or item.get("password") or ""),
                }
            )
    while len(slots) < 3:
        slots.append({"url": "", "user": "", "password": ""})
    return slots


def _antminer_pools(pools: list[dict[str, str]]) -> list[dict[str, str]]:
    slots = _slots_from_raw(pools)
    return [{"url": item["url"], "user": item["user"], "pass": item["password"]} for item in slots]


def _sleeping_from_conf(conf: dict[str, Any]) -> Optional[bool]:
    for key in ("miner-mode", "bitmain-work-mode"):
        if key not in conf:
            continue
        try:
            return int(conf[key]) == 1
        except (TypeError, ValueError):
            continue
    return None


def _antminer_write_body(
    conf: dict[str, Any],
    pools: list[dict[str, str]],
    mode: Any,
) -> dict[str, Any]:
    kept = (
        "bitmain-fan-ctrl",
        "bitmain-fan-pwm",
        "bitmain-hashrate-percent",
        "bitmain-freq-level",
        "freq-level",
        "bitmain-user-ip-cat",
        "bitmain-energy-consumption",
        "version",
    )
    body: dict[str, Any] = {}
    for key in kept:
        if key in conf:
            body[key] = conf[key]
    body.setdefault("version", 4)
    body["pools"] = _antminer_pools(pools)
    if mode is not None:
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            pass
        body["miner-mode"] = mode
        body["bitmain-work-mode"] = mode
    return body


def _conf_result(response: httpx.Response) -> tuple[bool, str]:
    if response.status_code in (401, 403):
        return False, "auth failed"
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    data = _as_json(response)
    if isinstance(data, dict) and str(data.get("stats", "")).lower() == "error":
        return False, str(data.get("msg") or data.get("code") or "error")[:200]
    return True, "ok"


def _find_pools(payload: dict[str, Any]) -> Any:
    candidates = [payload]
    for key in ("settings", "miner"):
        node = payload.get(key)
        if isinstance(node, dict):
            candidates.append(node)
            miner = node.get("miner")
            if isinstance(miner, dict):
                candidates.append(miner)
    for node in candidates:
        if isinstance(node.get("pools"), list):
            return node["pools"]
    return None


def _replace_vnish_pools(payload: dict[str, Any], pools: list[dict[str, str]]) -> dict[str, Any]:
    body = json.loads(json.dumps(payload))
    fresh = _antminer_pools(pools)

    def replace(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        existing = node.get("pools")
        if isinstance(existing, list):
            rich: list[dict[str, Any]] = []
            for index, slot in enumerate(fresh):
                base = dict(existing[index]) if index < len(existing) and isinstance(existing[index], dict) else {}
                base.update(slot)
                rich.append(base)
            node["pools"] = rich
            return True
        for key in ("miner", "settings"):
            if replace(node.get(key)):
                return True
        return False

    if not replace(body):
        miner = body.get("miner")
        if not isinstance(miner, dict):
            miner = {}
            body["miner"] = miner
        miner["pools"] = fresh
    return body


def _sleeping_from_vnish(payload: dict[str, Any]) -> Optional[bool]:
    state = payload.get("miner_state")
    miner = payload.get("miner")
    if state is None and isinstance(miner, dict):
        status = miner.get("miner_status")
        if isinstance(status, dict):
            state = status.get("miner_state")
        elif isinstance(status, str):
            state = status
    if not isinstance(state, str):
        return None
    value = state.lower()
    if value in {"stopped", "sleeping", "paused", "pause"}:
        return True
    if value in {"mining", "starting", "initializing", "auto-tuning", "restarting"}:
        return False
    return None


def _as_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError:
        text = response.text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
