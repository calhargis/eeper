"""In-process API test harness: a real Postgres (testcontainers), the ASGI app
wired to it, an injectable clock, and a fresh schema per test.

This is the 'stack harness' in unit-test form: it lets the auth matrix and the
brute-force lockout (via clock control) run deterministically without the full
Compose stack.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from eeper.api.clock import get_now
from eeper.api.config import Settings, get_settings
from eeper.api.db import get_session
from eeper.api.main import app
from eeper.api.models import Base

_BASE_URL = "https://testserver"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    # The context manager stops the container itself, so skip the ryuk reaper
    # (avoids pulling/starting an extra image — a common local-Docker hang).
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@dataclass
class Harness:
    """Per-test API harness."""

    client: httpx.AsyncClient  # default client (own cookie jar)
    fresh: Callable[[], httpx.AsyncClient]  # make additional clients (use via `async with`)
    clock: dict[str, datetime]  # mutate clock["now"] to advance time
    settings: Settings


@pytest_asyncio.fixture
async def api(postgres_url: str) -> AsyncIterator[Harness]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    clock = {"now": datetime.now(UTC)}
    settings = Settings(
        database_url=postgres_url,
        secret_key="test-secret-key-0123456789abcdef",
        max_failed_logins=3,
        lockout_seconds=300,
    )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_now] = lambda: clock["now"]
    app.dependency_overrides[get_settings] = lambda: settings

    transport = httpx.ASGITransport(app=app)

    def fresh() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=_BASE_URL)

    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        yield Harness(client=client, fresh=fresh, clock=clock, settings=settings)

    app.dependency_overrides.clear()
    await engine.dispose()
