from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from models import DeviceRegister, SettingsPatch
from monitor import build_monitor
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
            items.append(snap.model_dump())
        else:
            items.append(
                {
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
                }
            )
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
    settings = app.state.settings
    store = app.state.store
    return {
        "auto_reboot_enabled": store.state.auto_reboot_enabled,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "reboot_cooldown_seconds": settings.reboot_cooldown_seconds,
        "hashrate_alert_ratio": settings.hashrate_alert_ratio,
        "hashrate_reboot_ratio": settings.hashrate_reboot_ratio,
        "location_ip": settings.location_ip,
    }


@app.patch("/api/settings", dependencies=[Depends(require_token)])
async def patch_settings(body: SettingsPatch):
    store = app.state.store
    if body.auto_reboot_enabled is not None:
        store.update(auto_reboot_enabled=body.auto_reboot_enabled)
    return {
        "auto_reboot_enabled": store.state.auto_reboot_enabled,
    }


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
    return FileResponse(STATIC_DIR / "uvy-logo.png")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
