"""Quality-gated pulse-ox ingestion (M4.2).

Subscribes ``eeper/dev/+/pulseox`` (only when the pulse-ox profile is enabled), validates
each message against the :class:`PulseOxMessage` contract, and applies the QUALITY GATE:
a sample below the confidence threshold is discarded — never stored, never fused — and
counted, so the discard rate is observable per device. Accepted samples land in
``pulseox_readings``. Mirrors the M3.1 :class:`SensorIngestor` (paho thread validates +
enqueues; an async task batch-writes), so the event loop is never blocked.

Insights-only: heart-rate / blood-oxygen / perfusion feed trends and fusion features,
never a vital-sign readout or an alarm.
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
from eeper.api.models import Device, PulseOxReading
from eeper.api.schemas import PulseOxMessage

_log = logging.getLogger("eeper.api.pulseox_ingestion")

_MAX_BYTES = 2048
_TOPIC = "eeper/dev/+/pulseox"
_QUEUE_MAX = 10000

# (device_id, hr, spo2, perfusion, quality, ts) — an accepted sample the writer persists.
_Sample = tuple[int, float, float, float, float, datetime]


class PulseOxIngestor:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._queue: queue.Queue[_Sample] = queue.Queue(maxsize=_QUEUE_MAX)
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        # Per-device (accepted, discarded) counters for discard-rate observability.
        self._lock = threading.Lock()
        self._accepted: dict[int, int] = {}
        self._discarded: dict[int, int] = {}

    @property
    def enabled(self) -> bool:
        # Only ingest when the deployment has turned the profile on (the acknowledged-
        # disclaimer half of the gate is enforced at the API/UI; here the profile is the
        # switch that even starts the subscriber).
        return self._settings.pulseox_profile_enabled and bool(self._settings.mqtt_host)

    def stats(self) -> dict[int, tuple[int, int]]:
        """A snapshot of ``device_id -> (accepted, discarded)`` for the current run."""
        with self._lock:
            devices = set(self._accepted) | set(self._discarded)
            return {d: (self._accepted.get(d, 0), self._discarded.get(d, 0)) for d in devices}

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
        # Runs on the paho network thread; must never raise. THE QUALITY GATE lives here:
        # a low-confidence sample is counted and dropped, not enqueued.
        try:
            if len(msg.payload) > _MAX_BYTES:
                _log.warning("dropping oversized pulse-ox message on %s", msg.topic)
                return
            parts = msg.topic.split("/")  # eeper / dev / {id} / pulseox
            if len(parts) != 4 or not parts[2].isdigit():
                _log.warning("dropping pulse-ox message on unexpected topic %s", msg.topic)
                return
            device_id = int(parts[2])
            reading = PulseOxMessage.model_validate_json(msg.payload)
            if reading.quality < self._settings.pulseox_quality_threshold:
                self._bump(self._discarded, device_id)  # gated out — not stored, not fused
                return
            ts = datetime.fromtimestamp(reading.ts, tz=UTC)
            self._queue.put_nowait(
                (device_id, reading.hr, reading.spo2, reading.perfusion, reading.quality, ts)
            )
            self._bump(self._accepted, device_id)
        except ValidationError as exc:
            _log.warning(
                "dropping malformed pulse-ox message on %s: %s", msg.topic, exc.errors()[:1]
            )
        except (ValueError, OverflowError, OSError) as exc:
            _log.warning("dropping unparseable pulse-ox message on %s: %s", msg.topic, exc)
        except queue.Full:
            _log.warning("pulse-ox ingestion queue full — dropping a sample")

    def _bump(self, counter: dict[int, int], device_id: int) -> None:
        with self._lock:
            counter[device_id] = counter.get(device_id, 0) + 1

    async def _consume(self) -> None:
        while True:
            batch = await asyncio.to_thread(self._drain)
            if batch:
                await self._write(batch)

    def _drain(self, timeout: float = 1.0, max_items: int = 500) -> list[_Sample]:
        items: list[_Sample] = []
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

    async def _write(self, batch: list[_Sample]) -> None:
        ids = {row[0] for row in batch}
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Device.id, Device.household_id).where(Device.id.in_(ids))
            )
            known: dict[int, str] = {r.id: r.household_id for r in rows}
            last_seen: dict[int, datetime] = {}
            for device_id, hr, spo2, perfusion, quality, ts in batch:
                household = known.get(device_id)
                if household is None:
                    continue  # a sample for an unknown / removed device
                session.add(
                    PulseOxReading(
                        ts=ts,
                        household_id=household,
                        device_id=device_id,
                        hr=hr,
                        spo2=spo2,
                        perfusion=perfusion,
                        quality=quality,
                    )
                )
                last_seen[device_id] = max(last_seen.get(device_id, ts), ts)
            for device_id, ts in last_seen.items():
                await session.execute(
                    update(Device).where(Device.id == device_id).values(last_seen_at=ts)
                )
            await session.commit()
