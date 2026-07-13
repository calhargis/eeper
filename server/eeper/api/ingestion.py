"""MQTT sensor ingestion (M3.1).

Subscribes the device subtree ``eeper/dev/+/+`` over TLS as ``eeper-api``, validates
each reading against the sensor contract, and writes it to ``sensor_readings`` (also
advancing the device's ``last_seen_at``, which drives the online/offline signal).
Malformed or oversized messages are dropped and logged — never crashing or slowing
ingestion. The paho network thread only validates + enqueues; an async task drains the
thread-safe queue and writes batches, so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
from datetime import UTC, datetime

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.config import Settings
from eeper.api.models import Device, SensorReading
from eeper.api.schemas import SensorMessage

_log = logging.getLogger("eeper.api.ingestion")

_MAX_BYTES = 2048  # a reading is tiny; anything larger is malformed or hostile
_TOPIC = "eeper/dev/+/+"  # eeper/dev/{device_id}/{metric}
_QUEUE_MAX = 10000

# (device_id, metric, value, quality, ts) — the validated reading the writer persists.
_Reading = tuple[int, str, float, float, datetime]


class SensorIngestor:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._queue: queue.Queue[_Reading] = queue.Queue(maxsize=_QUEUE_MAX)
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._settings.mqtt_host and self._settings.mqtt_username)

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
        # Runs on the paho network thread; it must never raise (that would kill the loop),
        # so every failure mode is a logged drop.
        try:
            if len(msg.payload) > _MAX_BYTES:
                _log.warning(
                    "dropping oversized sensor message on %s (%d bytes)",
                    msg.topic,
                    len(msg.payload),
                )
                return
            parts = msg.topic.split("/")  # eeper / dev / {id} / {metric}
            if len(parts) != 4 or not parts[2].isdigit():
                _log.warning("dropping sensor message on unexpected topic %s", msg.topic)
                return
            device_id, metric = int(parts[2]), parts[3]
            if metric == "pulseox":
                return  # richer contract, quality-gated by the PulseOxIngestor (M4.2)
            reading = SensorMessage.model_validate_json(msg.payload)
            ts = datetime.fromtimestamp(reading.ts, tz=UTC)
            self._queue.put_nowait((device_id, metric, reading.value, reading.quality, ts))
        except ValidationError as exc:
            _log.warning("dropping malformed sensor message on %s: %s", msg.topic, exc.errors()[:1])
        except (ValueError, OverflowError, OSError) as exc:
            _log.warning("dropping unparseable sensor message on %s: %s", msg.topic, exc)
        except queue.Full:
            _log.warning("sensor ingestion queue full — dropping a reading")

    async def _consume(self) -> None:
        while True:
            batch = await asyncio.to_thread(self._drain)
            if batch:
                await self._write(batch)

    def _drain(self, timeout: float = 1.0, max_items: int = 500) -> list[_Reading]:
        items: list[_Reading] = []
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

    async def _write(self, batch: list[_Reading]) -> None:
        ids = {row[0] for row in batch}
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Device.id, Device.household_id).where(Device.id.in_(ids))
            )
            known: dict[int, str] = {r.id: r.household_id for r in rows}
            last_seen: dict[int, datetime] = {}
            for device_id, metric, value, quality, ts in batch:
                household = known.get(device_id)
                if household is None:
                    continue  # a reading for an unknown / removed device
                session.add(
                    SensorReading(
                        ts=ts,
                        household_id=household,
                        device_id=device_id,
                        metric=metric,
                        value=value,
                        quality=quality,
                    )
                )
                last_seen[device_id] = max(last_seen.get(device_id, ts), ts)
            for device_id, ts in last_seen.items():
                await session.execute(
                    update(Device).where(Device.id == device_id).values(last_seen_at=ts)
                )
            await session.commit()
