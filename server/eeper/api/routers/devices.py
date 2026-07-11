"""Sensor-device onboarding (M3.1).

Pairing mints a per-device MQTT credential and a dynamic-security ACL scoped to the
node's ``eeper/dev/{id}/#`` subtree — the username/password are returned ONCE and never
stored. Management is admin-only; listing is open to any authenticated household member
(with a derived online flag from the last reading). An input node, never a medical
device.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from eeper.api.config import Settings
from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep, SettingsDep
from eeper.api.models import Device
from eeper.api.mqtt_provisioner import MqttProvisioner, ProvisionError, device_topic_prefix
from eeper.api.schemas import DeviceCreate, DeviceOut, DevicePaired, MessageOut

router = APIRouter(prefix="/devices", tags=["devices"])

# A node silent longer than this reads as offline. Matches the sensor firmware's
# heartbeat cadence (M3.2); the offline flip is computed on read, so it needs no worker.
HEARTBEAT_WINDOW = timedelta(seconds=90)


def _provisioner(settings: Settings) -> MqttProvisioner:
    port = settings.mqtt_tls_port if settings.mqtt_ca_cert else settings.mqtt_port
    return MqttProvisioner(
        settings.mqtt_host,
        port,
        settings.mqtt_ca_cert,
        settings.mqtt_username,
        settings.mqtt_password,
    )


def _online(device: Device) -> bool | None:
    if device.last_seen_at is None:
        return None
    return datetime.now(UTC) - device.last_seen_at <= HEARTBEAT_WINDOW


def _device_out(device: Device) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        name=device.name,
        kind=device.kind,
        enabled=device.enabled,
        online=_online(device),
        last_seen_at=device.last_seen_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DevicePaired)
async def pair_device(
    body: DeviceCreate, admin: AdminUser, session: SessionDep, settings: SettingsDep
) -> DevicePaired:
    prov = _provisioner(settings)
    if not prov.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "MQTT is not configured on this server."
        )
    # Reject an obvious duplicate before minting a credential.
    dup = await session.execute(
        select(Device.id).where(Device.household_id == admin.household_id, Device.name == body.name)
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A device with this name is already registered."
        )

    device = Device(household_id=admin.household_id, name=body.name, kind=body.kind)
    session.add(device)
    try:
        await session.flush()  # assign the id the MQTT account + topic subtree derive from
    except IntegrityError:  # lost a race with a concurrent pairing of the same name
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A device with this name is already registered."
        ) from None

    # Provision the per-device MQTT account off the event loop. On failure, tear down any
    # partial dynsec state and drop the half-created row so a retry is clean.
    try:
        username, password = await asyncio.to_thread(prov.provision_device, device.id)
    except ProvisionError as exc:
        with contextlib.suppress(ProvisionError):
            await asyncio.to_thread(prov.deprovision_device, device.id)
        await session.rollback()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not provision the device on the MQTT broker: {exc}"
        ) from exc

    device.mqtt_username = username
    await session.commit()
    return DevicePaired(
        **_device_out(device).model_dump(),
        mqtt_username=username,
        mqtt_password=password,
        topic_prefix=device_topic_prefix(device.id),
    )


@router.get("", response_model=list[DeviceOut])
async def list_devices(user: CurrentUser, session: SessionDep) -> list[DeviceOut]:
    rows = await session.execute(
        select(Device).where(Device.household_id == user.household_id).order_by(Device.id)
    )
    return [_device_out(d) for d in rows.scalars()]


@router.delete("/{device_id}", response_model=MessageOut)
async def unpair_device(
    device_id: int, admin: AdminUser, session: SessionDep, settings: SettingsDep
) -> MessageOut:
    device = await session.get(Device, device_id)
    if device is None or device.household_id != admin.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    # Revoke the MQTT account first so the node can no longer publish, then drop the row.
    await asyncio.to_thread(_provisioner(settings).deprovision_device, device_id)
    await session.delete(device)
    await session.commit()
    return MessageOut(detail="Device unpaired.")
