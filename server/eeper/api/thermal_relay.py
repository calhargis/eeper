"""Live thermal grid relay (Phase 8 / M8.2).

Subscribes the raw 32×24 grid on ``eeper/dev/+/thermal`` and fans the *latest* frame per
device out to browsers over a WebSocket, so the Thermal view can render a live false-color
heatmap. This is LIVE-ONLY — grids are never persisted (only the derived features are, via
:class:`~eeper.api.thermal_ingestion.ThermalIngestor`). The grid carries surface
temperatures; the UI renders them as a *relative* heatmap and shows the occupant as
presence — never a body-temperature readout (§2, §7.4).

Mirrors the ingestor's MQTT plumbing (a paho thread validates + enqueues; an async task
drains), but instead of a DB write it broadcasts to the per-device WebSocket hub. Latest
frame wins: if frames pile up (slow client, burst), only the freshest is delivered — a
heatmap only ever needs the newest grid.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
from collections import defaultdict

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError
from starlette.websockets import WebSocket

from eeper.api.config import Settings
from eeper.api.schemas import ThermalGridMessage

_log = logging.getLogger("eeper.api.thermal_relay")

_TOPIC = "eeper/dev/+/thermal"
_MAX_BYTES = 16384  # a 768-float grid JSON is ~6–8 KB; cap well above that
_QUEUE_MAX = 2000


class ThermalGridHub:
    """Fans a device's latest grid frame out to that device's connected WebSocket clients.

    Keyed by ``device_id``; a send to a dead socket drops that client rather than raising.
    Household scoping is enforced at connect time by the WS endpoint (only a same-household
    user is registered under a device id)."""

    def __init__(self) -> None:
        self._clients: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, device_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[device_id].add(ws)

    async def unregister(self, device_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[device_id].discard(ws)
            if not self._clients[device_id]:
                self._clients.pop(device_id, None)

    def has_clients(self, device_id: int) -> bool:
        return bool(self._clients.get(device_id))

    async def broadcast(self, device_id: int, payload: str) -> None:
        async with self._lock:
            targets = list(self._clients.get(device_id, ()))
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — a dead socket drops, never stalls delivery
                await self.unregister(device_id, ws)


class ThermalGridRelay:
    """Subscribes the raw grid topic and relays the latest frame per device to the hub."""

    def __init__(self, hub: ThermalGridHub, settings: Settings) -> None:
        self._hub = hub
        self._settings = settings
        self._queue: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=_QUEUE_MAX)
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
        client.on_connect = lambda c, *_a: c.subscribe(_TOPIC, qos=0)  # live: newest wins, no QoS
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
                return
            parts = msg.topic.split("/")  # eeper / dev / {id} / thermal
            if len(parts) != 4 or not parts[2].isdigit():
                return
            # Validate against the §4.5 contract, then relay the exact JSON the browser
            # renders (grid + t_min/t_max/quality/ts). Never store it.
            ThermalGridMessage.model_validate_json(msg.payload)
            self._queue.put_nowait((int(parts[2]), msg.payload.decode()))
        except ValidationError:
            _log.warning("dropping malformed thermal grid on %s", msg.topic)
        except (ValueError, UnicodeDecodeError):
            _log.warning("dropping unparseable thermal grid on %s", msg.topic)
        except queue.Full:
            pass  # live stream: a full queue just means we drop this frame

    async def _consume(self) -> None:
        while True:
            latest = await asyncio.to_thread(self._drain_latest)
            for device_id, payload in latest.items():
                if self._hub.has_clients(device_id):
                    await self._hub.broadcast(device_id, payload)

    def _drain_latest(self, timeout: float = 0.5) -> dict[int, str]:
        """Block for the next frame, then coalesce everything already queued — keeping only
        the newest grid per device (a heatmap never needs a stale frame)."""
        latest: dict[int, str] = {}
        try:
            device_id, payload = self._queue.get(timeout=timeout)
        except queue.Empty:
            return latest
        latest[device_id] = payload
        while True:
            try:
                device_id, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            latest[device_id] = payload
        return latest
