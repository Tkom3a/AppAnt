from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from httpx import DigestAuth


@dataclass
class MinerReading:
    online: bool
    hashrate_ths: Optional[float] = None
    temperature_c: Optional[float] = None
    elapsed_seconds: Optional[int] = None
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


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
    return MinerReading(
        online=True,
        hashrate_ths=ths,
        temperature_c=temp,
        elapsed_seconds=int(elapsed) if elapsed is not None else None,
        raw=payload if isinstance(payload, dict) else None,
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
    return MinerReading(
        online=True,
        hashrate_ths=to_ths(hashrate, expected_ths),
        temperature_c=float(temp) if temp is not None else None,
        elapsed_seconds=int(elapsed) if elapsed is not None else None,
        raw=payload if isinstance(payload, dict) else None,
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
                    return reading
            return MinerReading(online=False, error=last_error)

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
            return parse_vnish(payload, expected_ths)

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
