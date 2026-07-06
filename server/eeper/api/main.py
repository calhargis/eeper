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
from eeper.api.db import create_schema
from eeper.api.routers import account, auth, system


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_schema()
    yield


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

app.include_router(v1)
