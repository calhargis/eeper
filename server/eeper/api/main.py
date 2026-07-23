"""FastAPI application entrypoint.

All routes live under ``/api/v1`` (the versioned seam from the Master Plan). The
edge Caddy proxy forwards ``/api/*`` here and terminates TLS; this service never
faces the network directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from eeper import __version__
from eeper.api.camera_monitor import CameraMonitor
from eeper.api.config import get_settings
from eeper.api.db import create_schema_and_hypertables, get_sessionmaker
from eeper.api.event_hub import EventHub
from eeper.api.fusion_worker import FusionWorker
from eeper.api.gateway import Go2rtcClient
from eeper.api.ingestion import SensorIngestor
from eeper.api.nudge_worker import NudgeWorker
from eeper.api.pulseox_ingestion import PulseOxIngestor
from eeper.api.routers import (
    account,
    audio,
    auth,
    cameras,
    clips,
    devices,
    events,
    fusion,
    pulseox,
    system,
    thermal,
    tokens,
    trends,
    users,
)
from eeper.api.thermal_ingestion import ThermalIngestor
from eeper.api.thermal_relay import ThermalGridHub, ThermalGridRelay


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_schema_and_hypertables()
    settings = get_settings()
    gateway = Go2rtcClient(settings.go2rtc_url)
    monitor = CameraMonitor(gateway, get_sessionmaker(), settings)
    hub = EventHub()
    worker = NudgeWorker(get_sessionmaker(), settings, hub)
    ingestor = SensorIngestor(get_sessionmaker(), settings)  # M3.1: MQTT sensor readings
    fusion = FusionWorker(get_sessionmaker(), settings)  # M3.3: sleep/wake + distress fusion
    pulseox = PulseOxIngestor(get_sessionmaker(), settings)  # M4.2: quality-gated pulse-ox
    thermal = ThermalIngestor(get_sessionmaker(), settings)  # M6.1: thermal features
    thermal_grid_hub = ThermalGridHub()  # M8.2: live grid fan-out to the Thermal view
    thermal_relay = ThermalGridRelay(thermal_grid_hub, settings)
    app.state.thermal_grid_hub = thermal_grid_hub  # the /ws/thermal endpoint registers here
    app.state.thermal_relay = thermal_relay
    app.state.gateway = gateway
    app.state.monitor = monitor
    app.state.hub = hub  # the /ws/events endpoint registers clients here
    app.state.worker = worker
    app.state.ingestor = ingestor
    app.state.fusion = fusion
    app.state.pulseox = pulseox  # the /pulseox/health endpoint reads its discard stats
    app.state.thermal = thermal
    await monitor.start()  # reconcile go2rtc + begin the health/keep-warm loop
    await worker.start()  # LISTEN/NOTIFY + reconciliation poll for nudge delivery
    await ingestor.start()  # subscribe eeper/dev/# -> validate -> sensor_readings
    await fusion.start()  # per-epoch fuse of every extractor -> fused_states transitions
    await pulseox.start()  # subscribe eeper/dev/+/pulseox -> quality-gate -> pulseox_readings
    await thermal.start()  # subscribe eeper/dev/+/thermal_features -> thermal_features
    await thermal_relay.start()  # subscribe eeper/dev/+/thermal grid -> live WS heatmap fan-out
    try:
        yield
    finally:
        await thermal_relay.stop()
        await thermal.stop()
        await pulseox.stop()
        await fusion.stop()
        await ingestor.stop()
        await worker.stop()
        await monitor.stop()


app = FastAPI(
    title="eeper API",
    version=__version__,
    lifespan=lifespan,
    # Interactive docs are off by default (nothing extra exposed); the versioned
    # OpenAPI schema is still published for integrators.
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
)

v1 = APIRouter(prefix="/api/v1")


@v1.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Unauthenticated liveness probe (used by the container healthcheck)."""
    return {"status": "ok", "version": __version__}


v1.include_router(system.router)
v1.include_router(auth.router)
v1.include_router(account.router)
v1.include_router(users.router)
v1.include_router(tokens.router)
v1.include_router(cameras.router)
v1.include_router(audio.router)
v1.include_router(clips.router)
v1.include_router(devices.router)
v1.include_router(events.router)
v1.include_router(fusion.router)
v1.include_router(trends.router)
v1.include_router(pulseox.router)
v1.include_router(thermal.router)

app.include_router(v1)
