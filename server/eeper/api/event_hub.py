"""In-process WebSocket event hub (M2.4): fans nudge events out to connected
Tonight-view clients, so an event appears live without a reload.

The nudge worker calls :meth:`EventHub.broadcast` when it broadcasts an event (and
again when its clip is ready); the ``/ws/events`` endpoint registers each connected
client under its household. Delivery is best-effort and idempotent from the client's
side — messages carry the event id, so a client that receives the same event twice
(broadcast + clip-ready, or a reconnect replay) just updates the same row. A send to a
dead socket drops that client rather than raising."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from starlette.websockets import WebSocket

from eeper.api.models import Event
from eeper.api.schemas import EventOut

_log = logging.getLogger("eeper.api.event_hub")


def event_to_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        ts=event.ts,
        camera_id=event.camera_id,
        type=event.type,
        value=event.value,
        previous_value=event.previous_value,
        confidence=event.confidence,
        clip_id=event.clip_id,
    )


def event_message(event: Event) -> dict[str, Any]:
    """The JSON-safe wire form shared by the WebSocket broadcast and the events API."""
    return event_to_out(event).model_dump(mode="json")


class EventHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, household_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[household_id].add(ws)

    async def unregister(self, household_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[household_id].discard(ws)
            if not self._clients[household_id]:
                self._clients.pop(household_id, None)

    async def broadcast(self, household_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected client in the household. A failed
        send drops that client; never raises to the caller (the worker)."""
        async with self._lock:
            targets = list(self._clients.get(household_id, ()))
        if not targets:
            return
        payload = json.dumps(message)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — a dead socket drops, never stalls delivery
                await self.unregister(household_id, ws)
