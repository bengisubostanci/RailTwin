"""RailTwin arka ucu: canlı dijital ikiz üzerinde REST + WebSocket API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from twin_core import FAULT_KINDS

from .config import settings
from .db import Database
from .ml import TwinModels
from .mqtt import MqttBridge
from .scenario import BASELINE, PRESETS, STRATEGIES, ScenarioParams, run_scenario
from .twin import LiveTwin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("railtwin")

SNAPSHOT_KEY = "railtwin:snapshot"


class Hub:
    """Canlı anlık görüntüleri bağlı panolara dağıtır."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, message: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(message, default=str)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001 - herhangi bir gönderim hatası istemcinin ayrıldığı anlamına gelir
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


db = Database(settings.database_url, settings.telemetry_retention)
models = TwinModels()
twin = LiveTwin(db, models)
bridge = MqttBridge(twin, db)
hub = Hub()
cache: aioredis.Redis | None = None


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        try:
            await db.flush()
        except Exception:  # noqa: BLE001 - bir yazma başarısız olsa bile veri almaya devam et
            log.exception("Telemetri yazımı (flush) başarısız oldu")


async def _evaluate_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        try:
            await twin.evaluate()
            snapshot = twin.snapshot()
            if cache is not None:
                await cache.set(SNAPSHOT_KEY, json.dumps(snapshot, default=str), ex=30)
            await hub.broadcast({"type": "snapshot", "data": snapshot})
            for event in twin.drain_events():
                await hub.broadcast(event)
        except Exception:  # noqa: BLE001
            log.exception("Değerlendirme döngüsü başarısız oldu")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global cache
    await db.connect()
    cache = aioredis.from_url(settings.redis_url, decode_responses=True)
    await asyncio.to_thread(models.load_or_train, settings.baseline_steps, settings.model_cache_dir)
    tasks = [asyncio.create_task(c()) for c in (bridge.run, _flush_loop, _evaluate_loop)]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await db.flush()
        await db.close()
        await cache.aclose()


app = FastAPI(
    title="RailTwin API",
    description="Canlı demiryolu dijital ikizi için REST ve WebSocket API.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"]
)


# ------------------------------------------------------------------- durum
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "model_ready": models.ready,
        "mqtt_connected": twin.mqtt_connected,
        "topology_loaded": twin.topology is not None,
        "assets_online": len(twin.readings),
    }


@app.get("/api/topology")
async def topology():
    if twin.topology is None:
        raise HTTPException(503, "Simülatörün hat topolojisini yayınlaması bekleniyor")
    return twin.topology


@app.get("/api/state")
async def state():
    if cache is not None:
        cached = await cache.get(SNAPSHOT_KEY)
        if cached:
            return json.loads(cached)
    return twin.snapshot()


# --------------------------------------------------------------- varlıklar
def _require_asset(asset_id: str) -> str:
    if asset_id not in twin.types:
        raise HTTPException(404, f"Bilinmeyen varlık {asset_id}")
    return twin.types[asset_id]


@app.get("/api/assets")
async def assets():
    return sorted((twin.asset_view(a) for a in twin.readings), key=lambda a: a["id"])


@app.get("/api/assets/{asset_id}")
async def asset(asset_id: str):
    asset_type = _require_asset(asset_id)
    return {**twin.asset_view(asset_id), "fault_kinds": FAULT_KINDS[asset_type]}


@app.get("/api/assets/{asset_id}/telemetry")
async def asset_telemetry(asset_id: str, minutes: int = Query(15, ge=1, le=1440),
                          bucket_s: int = Query(5, ge=1, le=3600)):
    _require_asset(asset_id)
    return await db.telemetry(asset_id, minutes, bucket_s)


class FaultRequest(BaseModel):
    kind: str | None = None


@app.post("/api/assets/{asset_id}/faults", status_code=202)
async def inject_fault(asset_id: str, body: FaultRequest):
    asset_type = _require_asset(asset_id)
    if body.kind is not None and body.kind not in FAULT_KINDS[asset_type]:
        raise HTTPException(422, f"{body.kind}, {asset_type} türü için geçerli bir arıza değil")
    await _control({"action": "inject_fault", "asset_id": asset_id, "kind": body.kind})
    return {"status": "injected", "asset_id": asset_id, "kind": body.kind or "random"}


@app.post("/api/assets/{asset_id}/maintenance")
async def dispatch_maintenance(asset_id: str):
    _require_asset(asset_id)
    view = twin.asset_view(asset_id)
    await _control({"action": "repair", "asset_id": asset_id})
    return await db.create_work_order(asset_id, view["recommended_action"], view["cause"], view["health"])


async def _control(command: dict) -> None:
    try:
        await bridge.send_control(command)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


class EnvironmentRequest(BaseModel):
    ambient_c: float | None = Field(None, ge=-30, le=50)
    load_factor: float | None = Field(None, ge=0.05, le=1.3)


@app.post("/api/environment")
async def set_environment(body: EnvironmentRequest):
    await _control({"action": "set_env", **body.model_dump(exclude_none=True)})
    if body.load_factor is not None:
        twin.environment["load_factor"] = body.load_factor
    if body.ambient_c is not None:
        twin.environment["ambient_c"] = body.ambient_c
    return twin.environment


# ---------------------------------------------------------------- alarmlar
@app.get("/api/alerts")
async def alerts(active: bool = False, limit: int = Query(100, ge=1, le=500)):
    return await db.alerts(active, limit)


@app.post("/api/alerts/{alert_id}/ack")
async def acknowledge(alert_id: int):
    if not await db.acknowledge_alert(alert_id):
        raise HTTPException(404, "Alarm bulunamadı")
    twin.acknowledge(alert_id)
    return {"id": alert_id, "acknowledged": True}


# -------------------------------------------------------------------- bakım
@app.get("/api/maintenance")
async def maintenance():
    ranked = sorted((twin.asset_view(a) for a in twin.readings), key=lambda a: a["health"])
    return {"assets": ranked, "work_orders": await db.work_orders(20)}


# --------------------------------------------------------------- senaryolar
class ScenarioRequest(BaseModel):
    headway_min: float = Field(BASELINE.headway_min, ge=1.5, le=30)
    service_hours: float = Field(BASELINE.service_hours, ge=4, le=24)
    load_factor: float = Field(BASELINE.load_factor, ge=0.05, le=1.5)
    ambient_c: float = Field(BASELINE.ambient_c, ge=-30, le=50)
    fleet_size: int = Field(BASELINE.fleet_size, ge=2, le=40)
    max_speed_kmh: float = Field(BASELINE.max_speed_kmh, ge=40, le=140)
    maintenance_strategy: Literal["reactive", "preventive", "predictive"] = BASELINE.maintenance_strategy
    horizon_days: int = Field(BASELINE.horizon_days, ge=7, le=180)


@app.get("/api/scenarios/config")
async def scenario_config():
    return {"baseline": asdict(BASELINE), "presets": PRESETS, "strategies": list(STRATEGIES)}


@app.post("/api/scenarios/run")
async def scenario_run(body: ScenarioRequest):
    return await asyncio.to_thread(run_scenario, ScenarioParams(**body.model_dump()))


# ---------------------------------------------------------------- websocket
@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "data": twin.snapshot()}, default=str))
        while True:
            await ws.receive_text()  # istemciler yalnızca dinler; bu, bağlantı kopmalarını algılar
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
