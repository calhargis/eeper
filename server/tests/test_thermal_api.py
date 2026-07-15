"""M6.1 thermal ingestion against a real DB (slice 2): the ingestor stores derived
features in ``thermal_features`` and advances the device's ``last_seen_at`` (the health
signal), a message for an unknown device is ignored, and ``thermal`` is a valid device
kind (so pairing goes through the exact M3.1 flow with no special-casing)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eeper.api.config import Settings
from eeper.api.models import Device, ThermalFeaturesReading
from eeper.api.schemas import DeviceCreate
from eeper.api.thermal_ingestion import ThermalIngestor


def _ingestor(postgres_url: str) -> ThermalIngestor:
    sm = async_sessionmaker(create_async_engine(postgres_url), expire_on_commit=False)
    return ThermalIngestor(sm, Settings(database_url=postgres_url, secret_key="x" * 16))


async def _seed_thermal_device(postgres_url: str) -> int:
    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        dev = Device(name="thermal-crib", kind="thermal", household_id="default")
        s.add(dev)
        await s.commit()
        await s.refresh(dev)
        device_id = dev.id
    await engine.dispose()
    return device_id


async def test_write_stores_features_and_updates_health(api, postgres_url: str) -> None:  # type: ignore[no-untyped-def]
    device_id = await _seed_thermal_device(postgres_url)
    ing = _ingestor(postgres_url)
    ts = datetime.now(UTC)
    await ing._write([(device_id, ts, True, 0.7, 0.12, 0.5, 0.4)])

    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(ThermalFeaturesReading).where(
                        ThermalFeaturesReading.device_id == device_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        r = rows[0]
        assert r.presence is True
        assert r.warm_region_area == 0.12
        assert r.centroid_row == 0.5 and r.centroid_col == 0.4
        dev = await s.get(Device, device_id)
        assert dev is not None and dev.last_seen_at is not None  # device health advanced
    await engine.dispose()


async def test_write_ignores_unknown_device(api, postgres_url: str) -> None:  # type: ignore[no-untyped-def]
    ing = _ingestor(postgres_url)
    await ing._write([(999999, datetime.now(UTC), True, 0.5, 0.1, 0.5, 0.5)])

    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        count = (
            await s.execute(select(func.count()).select_from(ThermalFeaturesReading))
        ).scalar_one()
        assert count == 0  # a message for an unknown / removed device is never stored
    await engine.dispose()


def test_thermal_is_a_valid_device_kind() -> None:
    assert DeviceCreate(name="crib", kind="thermal").kind == "thermal"
