"""Thermal features ingestion (M6.1, §4.5).

Subscribes ``eeper/dev/+/thermal_features``, validates each message against the
:class:`ThermalFeaturesMessage` contract, and stores the derived features in
``thermal_features`` while advancing the device's ``last_seen_at`` (device health). Only
the low-rate DERIVED features are ingested — the raw 32×24 grid is characterization-time
only and is never persisted here. There is no quality gate: the node already dropped
failed/malformed grids and only publishes features from good frames.

Mirrors the M3.1 :class:`SensorIngestor` (a paho thread validates + enqueues; an async
task batch-writes), so the event loop is never blocked. A thermal node is an ordinary
paired device — pairing is the opt-in; nothing here special-cases it beyond the richer
contract. Surface features only; never a body-temperature readout (§2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from datetime import UTC, datetime

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.config import Settings
from eeper.api.models import Device, ThermalFeaturesReading
from eeper.api.schemas import ThermalFeaturesMessage

_log = logging.getLogger("eeper.api.thermal_ingestion")

_MAX_BYTES = 2048
_TOPIC = "eeper/dev/+/thermal_features"
_QUEUE_MAX = 10000

# (device_id, ts, presence, confidence, area, centroid_row, centroid_col)
_Features = tuple[int, datetime, bool, float, float, float | None, float | None]


class ThermalIngestor:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._queue: queue.Queue[_Features] = queue.Queue(maxsize=_QUEUE_MAX)
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = threading.Lock()
        self._accepted: dict[int, int] = {}

    @property
    def enabled(self) -> bool:
        # A thermal node is a normal paired device; ingest whenever the bus is configured.
        return bool(self._settings.mqtt_host and self._settings.mqtt_username)

    def stats(self) -> dict[int, int]:
        """A snapshot of ``device_id -> accepted feature-messages`` for the current run."""
        with self._lock:
            return dict(self._accepted)

    async def start(self) -> None:
        if not self.enabled:
            return
        s = self._settings
        client = mqtt.Client(CallbackAPIVersion.VERSION2)
        client.username_pw_set(s.mqtt_username, s.mqtt_password)
        if s.mqtt_ca_cert:
            client.tls_set(ca_certs=s.mqtt_ca_cert)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = lambda c, *_a: c.subscribe(_TOPIC, qos=1)
        client.on_message = self._on_message
        port = s.mqtt_tls_port if s.mqtt_ca_cert else s.mqtt_port
        client.connect_async(s.mqtt_host, port, keepalive=30)
        client.loop_start()
        self._client = client
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _on_message(self, _client: mqtt.Client, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        # Runs on the paho network thread; must never raise.
        try:
            if len(msg.payload) > _MAX_BYTES:
                _log.warning("dropping oversized thermal message on %s", msg.topic)
                return
            parts = msg.topic.split("/")  # eeper / dev / {id} / thermal_features
            if len(parts) != 4 or not parts[2].isdigit():
                _log.warning("dropping thermal message on unexpected topic %s", msg.topic)
                return
            device_id = int(parts[2])
            f = ThermalFeaturesMessage.model_validate_json(msg.payload)
            centroid = f.warm_region_centroid
            row = centroid[0] if centroid is not None else None
            col = centroid[1] if centroid is not None else None
            ts = datetime.fromtimestamp(f.ts, tz=UTC)
            self._queue.put_nowait(
                (device_id, ts, f.presence, f.presence_confidence, f.warm_region_area, row, col)
            )
            with self._lock:
                self._accepted[device_id] = self._accepted.get(device_id, 0) + 1
        except ValidationError as exc:
            _log.warning(
                "dropping malformed thermal message on %s: %s", msg.topic, exc.errors()[:1]
            )
        except (ValueError, OverflowError, OSError) as exc:
            _log.warning("dropping unparseable thermal message on %s: %s", msg.topic, exc)
        except queue.Full:
            _log.warning("thermal ingestion queue full — dropping a features message")

    async def _consume(self) -> None:
        while True:
            batch = await asyncio.to_thread(self._drain)
            if batch:
                await self._write(batch)

    def _drain(self, timeout: float = 1.0, max_items: int = 500) -> list[_Features]:
        items: list[_Features] = []
        try:
            items.append(self._queue.get(timeout=timeout))
        except queue.Empty:
            return items
        for _ in range(max_items - 1):
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    async def _write(self, batch: list[_Features]) -> None:
        ids = {row[0] for row in batch}
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Device.id, Device.household_id).where(Device.id.in_(ids))
            )
            known: dict[int, str] = {r.id: r.household_id for r in rows}
            last_seen: dict[int, datetime] = {}
            for device_id, ts, presence, confidence, area, row, col in batch:
                household = known.get(device_id)
                if household is None:
                    continue  # a message for an unknown / removed device
                session.add(
                    ThermalFeaturesReading(
                        ts=ts,
                        household_id=household,
                        device_id=device_id,
                        presence=presence,
                        presence_confidence=confidence,
                        warm_region_area=area,
                        centroid_row=row,
                        centroid_col=col,
                    )
                )
                last_seen[device_id] = max(last_seen.get(device_id, ts), ts)
            for device_id, ts in last_seen.items():
                await session.execute(
                    update(Device).where(Device.id == device_id).values(last_seen_at=ts)
                )
            await session.commit()
