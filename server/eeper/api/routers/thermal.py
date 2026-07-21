"""Live thermal grid WebSocket (Phase 8 / M8.2).

A same-household member opens ``/ws/thermal/{device_id}`` to receive a paired thermal
node's live 32×24 grid frames, which the Thermal view renders as a relative false-color
heatmap. Grids are relayed live and never stored; the occupant is shown as presence, never
a body-temperature readout (§2, §7.4). Auth is the browser's access cookie (same JWT the
HTTP dependency validates); the device must belong to the caller's household and be a
thermal node.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.config import Settings, get_settings
from eeper.api.db import get_sessionmaker
from eeper.api.models import Device, User
from eeper.api.tokens import decode_access_token

router = APIRouter(tags=["thermal"])


async def _authenticate_ws(
    websocket: WebSocket, session: AsyncSession, settings: Settings
) -> User | None:
    token = websocket.cookies.get(settings.access_cookie_name)
    if not token:
        return None
    payload = decode_access_token(settings.secret_key, token)
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return await session.get(User, user_id)


@router.websocket("/ws/thermal/{device_id}")
async def ws_thermal(websocket: WebSocket, device_id: int) -> None:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        user = await _authenticate_ws(websocket, session, settings)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        device = await session.get(Device, device_id)
        # Household-scoped, and only a thermal node has a grid stream to relay.
        if device is None or device.household_id != user.household_id or device.kind != "thermal":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    hub = websocket.app.state.thermal_grid_hub
    await websocket.accept()
    await hub.register(device_id, websocket)
    try:
        # The client sends nothing; receive_text blocks until the socket closes.
        with contextlib.suppress(Exception):
            while True:
                await websocket.receive_text()
    finally:
        await hub.unregister(device_id, websocket)
