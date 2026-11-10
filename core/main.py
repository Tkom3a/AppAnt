from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from models import DeviceRegister, PoolApplyBody, PoolOrderBody, PoolSlot, PoolsCommand, PresetCreate, SettingsPatch, SleepCommand
from monitor import build_monitor, save_pool_order, worker_name
from store import StateStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.api_token in {"", "CHANGE_ME_LONG_SECRET"}:
        print("WARNING: смените API_TOKEN в файле .env перед использованием")
    store = StateStore(settings.data_dir() / "state.json")
    monitor = build_monitor(store)
    app.state.store = store
    app.state.monitor = monitor
    app.state.settings = settings
    monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="УВЫ control", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_monitor():
    return app.state.monitor


def require_token(
    x_api_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    expected = get_settings().api_token
    provided = None
    if x_api_token:
        provided = x_api_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api token",
        )


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/miners", dependencies=[Depends(require_token)])
async def list_miners():
    monitor = get_monitor()
    store = app.state.store
    settings = app.state.settings
    items = []
    for miner in monitor.miners:
        snap = store.get_snapshot(miner.id)
        if snap:
            item = snap.model_dump()
        else:
            item = {
                "id": miner.id,
                "name": miner.name,
                "firmware": miner.firmware,
                "port": miner.port,
                "expected_ths": miner.expected_ths,
                "auto_reboot": miner.auto_reboot,
                "status": "unknown",
                "online": False,
                "hashrate_ths": None,
                "temperature_c": None,
                "elapsed_seconds": None,
                "last_ping_at": None,
                "last_error": None,
                "last_reboot_at": None,
                "last_reboot_reason": None,
                "cooldown_until": None,
                "auto_reboots_in_window": 0,
                "pools": [],
                "fans_rpm": [],
                "temperatures": [],
            }
        item["worker"] = miner.worker
        items.append(item)
    return {
        "location_ip": settings.location_ip,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "auto_reboot_enabled": store.state.auto_reboot_enabled,
        "miners": items,
    }


@app.post("/api/miners/refresh", dependencies=[Depends(require_token)])
async def refresh_miners():
    snapshots = await get_monitor().poll_all()
    return {"miners": [item.model_dump() for item in snapshots]}


@app.post("/api/miners/{miner_id}/reboot", dependencies=[Depends(require_token)])
async def reboot_miner(miner_id: str):
    monitor = get_monitor()
    try:
        miner = monitor.miner_by_id(miner_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="miner not found")
    snapshot = await monitor.reboot(miner, reason="manual from app", automatic=False)
    return snapshot.model_dump()


@app.get("/api/events", dependencies=[Depends(require_token)])
async def events():
    return {"events": [item.model_dump() for item in app.state.store.state.events]}


@app.get("/api/settings", dependencies=[Depends(require_token)])
async def get_app_settings():
    return _settings_payload()


@app.patch("/api/settings", dependencies=[Depends(require_token)])
async def patch_settings(body: SettingsPatch):
    store = app.state.store
    changes = {}
    if body.auto_reboot_enabled is not None:
        changes["auto_reboot_enabled"] = body.auto_reboot_enabled
    if body.sleep_enabled is not None:
        changes["sleep_enabled"] = body.sleep_enabled
    if body.sleep_start is not None:
        changes["sleep_start"] = _hhmm(body.sleep_start)
    if body.sleep_end is not None:
        changes["sleep_end"] = _hhmm(body.sleep_end)
    if changes:
        store.update(**changes)
    return _settings_payload()


def _settings_payload() -> dict:
    settings = app.state.settings
    store = app.state.store
    return {
        "auto_reboot_enabled": store.state.auto_reboot_enabled,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "reboot_cooldown_seconds": settings.reboot_cooldown_seconds,
        "hashrate_alert_ratio": settings.hashrate_alert_ratio,
        "hashrate_reboot_ratio": settings.hashrate_reboot_ratio,
        "location_ip": settings.location_ip,
        "sleep_enabled": store.state.sleep_enabled,
        "sleep_start": store.state.sleep_start,
        "sleep_end": store.state.sleep_end,
        "sleep_timezone": store.state.sleep_timezone,
        "sleep_applied": store.state.sleep_applied,
        "pool_presets": [item.model_dump() for item in store.state.pool_presets],
    }


def _hhmm(value: str) -> str:
    parts = value.strip().split(":")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Время нужно в формате ЧЧ:ММ")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise HTTPException(status_code=400, detail="Время нужно в формате ЧЧ:ММ")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="Время нужно в формате ЧЧ:ММ")
    return f"{hour:02d}:{minute:02d}"


def _pool_slots(pools: list[PoolSlot]) -> list[dict]:
    slots = []
    for item in pools[:3]:
        slots.append(
            {
                "url": item.url.strip(),
                "user": item.user.strip(),
                "password": item.password,
            }
        )
    while len(slots) < 3:
        slots.append({"url": "", "user": "", "password": ""})
    if not slots[0]["url"]:
        raise HTTPException(status_code=400, detail="Укажите адрес первого пула")
    return slots


def _miner_or_404(miner_id: str):
    try:
        return get_monitor().miner_by_id(miner_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="miner not found")


@app.get("/api/miners/{miner_id}/pools", dependencies=[Depends(require_token)])
async def read_miner_pools(miner_id: str):
    miner = _miner_or_404(miner_id)
    result = await get_monitor().read_pools(miner)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["detail"] or "не удалось прочитать пулы")
    return result


@app.post("/api/miners/{miner_id}/pools", dependencies=[Depends(require_token)])
async def write_miner_pools(miner_id: str, body: PoolsCommand):
    miner = _miner_or_404(miner_id)
    result = await get_monitor().write_pools(miner, _pool_slots(body.pools), body.keep_worker)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["detail"] or "не удалось записать пулы")
    return result


@app.put("/api/pools/order", dependencies=[Depends(require_token)])
async def reorder_pools(body: PoolOrderBody):
    try:
        save_pool_order(get_monitor().settings, body.order)
    except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return get_monitor().pool_file_status()


@app.get("/api/pools/config", dependencies=[Depends(require_token)])
async def pool_config():
    return get_monitor().pool_file_status()


@app.post("/api/pools/apply-config", dependencies=[Depends(require_token)])
async def apply_pool_config(body: PoolApplyBody):
    try:
        result = await get_monitor().apply_pool_file(body.miner_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="асик не найден")
    if not result.get("ok", True) and "results" not in result:
        raise HTTPException(status_code=400, detail=result.get("detail") or "pools.json")
    return result


@app.post("/api/pools/apply", dependencies=[Depends(require_token)])
async def apply_pools(body: PoolsCommand):
    monitor = get_monitor()
    try:
        miners = monitor.miners_by_ids(body.miner_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="miner not found")
    slots = _pool_slots(body.pools)
    known = [item.worker for item in monitor.miners]
    results = []
    for miner in miners:
        prepared = slots
        if not body.keep_worker:
            prepared = []
            for slot in slots:
                item = dict(slot)
                item["user"] = worker_name(item.get("user", ""), miner.worker, known)
                prepared.append(item)
        results.append(await monitor.write_pools(miner, prepared, body.keep_worker))
    return {"results": results}


@app.post("/api/presets", dependencies=[Depends(require_token)])
async def create_preset(body: PresetCreate):
    from uuid import uuid4

    from models import PoolPreset

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Нужно название набора")
    slots = _pool_slots(body.pools)
    preset = PoolPreset(
        id=uuid4().hex[:8],
        name=name,
        pools=[PoolSlot(**item) for item in slots],
    )
    presets = list(app.state.store.state.pool_presets)
    presets.append(preset)
    app.state.store.update(pool_presets=presets)
    return preset.model_dump()


@app.delete("/api/presets/{preset_id}", dependencies=[Depends(require_token)])
async def delete_preset(preset_id: str):
    presets = [item for item in app.state.store.state.pool_presets if item.id != preset_id]
    app.state.store.update(pool_presets=presets)
    return {"ok": True}


@app.post("/api/miners/{miner_id}/sleep", dependencies=[Depends(require_token)])
async def sleep_miner(miner_id: str, body: SleepCommand):
    miner = _miner_or_404(miner_id)
    return await get_monitor().set_sleep_one(miner, body.sleep, "вручную")


@app.post("/api/sleep", dependencies=[Depends(require_token)])
async def sleep_miners(body: SleepCommand):
    monitor = get_monitor()
    try:
        miners = monitor.miners_by_ids(body.miner_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="miner not found")
    results = []
    for miner in miners:
        results.append(await monitor.set_sleep_one(miner, body.sleep, "вручную"))
    return {"results": results}


@app.post("/api/devices", dependencies=[Depends(require_token)])
async def register_device(body: DeviceRegister):
    app.state.store.add_expo_token(body.token)
    return {"ok": True, "count": len(app.state.store.state.expo_tokens)}


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/favicon.ico")
async def favicon():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(ico)
    logo = STATIC_DIR / "asic-logo.png"
    if logo.exists():
        return FileResponse(logo)
    return FileResponse(STATIC_DIR / "favicon-32.png")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
