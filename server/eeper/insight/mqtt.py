"""MQTT publishing for insight events (M2.2).

Publishes per-camera motion samples and movement-level state changes. Fire-and-
forget from the asyncio scoring loop: paho runs its network loop in a background
thread (``loop_start``) and ``publish`` is thread-safe and non-blocking, so a
down/unreachable broker never blocks or crashes the engine — samples are dropped
(qos0) or bounded-queued (qos1, capped) while paho auto-reconnects. With no host
configured the publisher is an inert no-op (graceful degradation).

Topics (all under ``eeper/{node}/``): per-tick samples ``motion/cam{id}`` and
``sound/cam{id}`` (qos0, not retained); state transitions on a per-signal-type
retained topic ``state/cam{id}/{state_type}`` where ``state_type`` is
``movement_level`` | ``sound_level`` | ``cry`` (qos1, retained, so signals never
clobber each other's last-known state).

The vocabulary is deliberately non-clinical: movement + sound level, never a vital
sign. (This class predates the sound/cry signals — the name is historical.)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

_log = logging.getLogger("eeper.insight.mqtt")
_MAX_QUEUED = 1000  # bounded backlog while the broker is unreachable


class MotionPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        node: str,
        *,
        tls_ca: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        self._node = node
        self._client: mqtt.Client | None = None
        if not host:
            return  # MQTT disabled — no-op publisher
        client = mqtt.Client(CallbackAPIVersion.VERSION2)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.max_queued_messages_set(_MAX_QUEUED)
        client.on_disconnect = self._on_disconnect
        # M3.1: on a hardened broker, authenticate and verify the broker's TLS cert
        # against the MQTT CA. Set before connect so the first (async) connect uses them.
        if username:
            client.username_pw_set(username, password)
        if tls_ca:
            client.tls_set(ca_certs=tls_ca)
        # connect_async + loop_start never block the event loop and never raise if
        # the broker is down; the background thread keeps retrying.
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        _log.warning("mqtt disconnected (%s); auto-reconnecting", reason_code)

    def publish_motion(self, camera_id: int, score: float, confidence: float, ts: float) -> None:
        """A per-tick motion sample. Mirrors the sensor contract exactly:
        eeper/{node}/motion/cam{id} -> {ts, type, value, unit, quality}."""
        self._publish(
            f"eeper/{self._node}/motion/cam{camera_id}",
            {
                "ts": ts,
                "type": "movement",
                "value": round(score, 5),
                "unit": "index",
                "quality": round(confidence, 3),
            },
            qos=0,
            retain=False,
        )

    def publish_sound(
        self, camera_id: int, loudness_dbfs: float, elevation_db: float, ts: float
    ) -> None:
        """A per-tick sound-level sample: loudness and how far it sits above the
        adaptive baseline. eeper/{node}/sound/cam{id}."""
        self._publish(
            f"eeper/{self._node}/sound/cam{camera_id}",
            {
                "ts": ts,
                "type": "sound_level",
                "value": round(loudness_dbfs, 1),
                "unit": "dBFS",
                "elevation": round(elevation_db, 1),
            },
            qos=0,
            retain=False,
        )

    def publish_state(
        self,
        camera_id: int,
        state_type: str,
        value: str,
        previous: str | None,
        confidence: float,
        contributing: list[str],
        ts: float,
    ) -> None:
        """A signal state transition (movement_level / sound_level / cry). Retained,
        on a per-state-type topic so signals never clobber each other's last-known
        state, so a subscriber that connects just after a change still receives it.
        eeper/{node}/state/cam{id}/{state_type}."""
        self._publish(
            f"eeper/{self._node}/state/cam{camera_id}/{state_type}",
            {
                "ts": ts,
                "state_type": state_type,
                "value": value,
                "previous": previous,
                "confidence": round(confidence, 3),
                "contributing_inputs": contributing,
            },
            qos=1,
            retain=True,
        )

    def _publish(self, topic: str, payload: dict[str, Any], *, qos: int, retain: bool) -> None:
        if self._client is None:
            return
        try:
            self._client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
        except Exception:  # a publish error must never reach the scoring loop
            _log.exception("mqtt publish to %s failed", topic)

    def close(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
