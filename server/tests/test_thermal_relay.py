"""M8.2: the live thermal grid relay — the per-device WebSocket hub and the paho→async
plumbing that validates the raw §4.5 grid and fans the latest frame out. The MQTT
connection + the WS endpoint's auth are exercised elsewhere (mirrors /ws/events); this
pins the pure logic: fan-out, dead-socket drop, contract validation, and latest-wins."""

from __future__ import annotations

import json
from typing import cast

from starlette.websockets import WebSocket

from eeper.api.schemas import THERMAL_CELLS
from eeper.api.thermal_relay import ThermalGridHub, ThermalGridRelay


def _grid_payload(ts: float = 1000.0, fill: float = 21.0) -> bytes:
    grid = [fill] * THERMAL_CELLS
    return json.dumps(
        {"ts": ts, "grid": grid, "t_min": fill, "t_max": fill, "t_mean": fill, "quality": 1.0}
    ).encode()


class _FakeWS:
    def __init__(self, *, dead: bool = False) -> None:
        self.sent: list[str] = []
        self._dead = dead

    async def send_text(self, text: str) -> None:
        if self._dead:
            raise RuntimeError("socket closed")
        self.sent.append(text)


class _Msg:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


# ── the hub ──────────────────────────────────────────────────────────────────


async def test_hub_fans_a_frame_to_registered_clients() -> None:
    hub = ThermalGridHub()
    a, b = _FakeWS(), _FakeWS()
    await hub.register(1, cast(WebSocket, a))
    await hub.register(1, cast(WebSocket, b))
    await hub.broadcast(1, "frame")
    assert a.sent == ["frame"] and b.sent == ["frame"]


async def test_hub_is_scoped_per_device() -> None:
    hub = ThermalGridHub()
    a, b = _FakeWS(), _FakeWS()
    await hub.register(1, cast(WebSocket, a))
    await hub.register(2, cast(WebSocket, b))
    await hub.broadcast(1, "one")
    assert a.sent == ["one"] and b.sent == []


async def test_hub_drops_a_dead_socket() -> None:
    hub = ThermalGridHub()
    dead = _FakeWS(dead=True)
    await hub.register(1, cast(WebSocket, dead))
    await hub.broadcast(1, "frame")  # must not raise
    assert not hub.has_clients(1)  # the dead socket was unregistered


async def test_hub_unregister_clears_the_device() -> None:
    hub = ThermalGridHub()
    ws = _FakeWS()
    await hub.register(1, cast(WebSocket, ws))
    await hub.unregister(1, cast(WebSocket, ws))
    assert not hub.has_clients(1)


# ── the relay's message plumbing ─────────────────────────────────────────────


def _relay() -> ThermalGridRelay:
    from eeper.api.config import Settings

    # settings is only used by start(); the plumbing tests never touch it.
    return ThermalGridRelay(ThermalGridHub(), Settings.model_construct())


def test_relay_enqueues_a_valid_grid() -> None:
    r = _relay()
    r._on_message(None, None, _Msg("eeper/dev/7/thermal", _grid_payload()))  # type: ignore[arg-type]
    device_id, payload = r._queue.get_nowait()
    assert device_id == 7
    assert json.loads(payload)["grid"][0] == 21.0  # the exact JSON the browser renders


def test_relay_drops_a_malformed_grid() -> None:
    r = _relay()
    r._on_message(None, None, _Msg("eeper/dev/7/thermal", b'{"grid": [1,2,3]}'))  # type: ignore[arg-type]
    assert r._queue.empty()  # too-short grid fails §4.5 validation → dropped


def test_relay_drops_an_unexpected_topic() -> None:
    r = _relay()
    r._on_message(None, None, _Msg("eeper/dev/x/thermal", _grid_payload()))  # type: ignore[arg-type]
    r._on_message(None, None, _Msg("eeper/insight/state", _grid_payload()))  # type: ignore[arg-type]
    assert r._queue.empty()


def test_drain_latest_coalesces_to_the_newest_frame_per_device() -> None:
    r = _relay()
    r._queue.put_nowait((1, "old-1"))
    r._queue.put_nowait((1, "new-1"))
    r._queue.put_nowait((2, "only-2"))
    latest = r._drain_latest(timeout=0.01)
    assert latest == {1: "new-1", 2: "only-2"}  # a heatmap never needs a stale frame
