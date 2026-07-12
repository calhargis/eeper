"""Provision per-device MQTT credentials + ACLs via mosquitto's dynamic-security
control API (M3.1).

At pair time the api mints a fresh MQTT account for a sensor node and scopes it — via
its own dynsec role — to that node's ``eeper/dev/{id}/#`` subtree, so one device can
never read or write another device's (or the internal insight) topics. The api
authenticates as ``eeper-api`` (which carries the dynsec admin role) and publishes
command batches to ``$CONTROL/dynamic-security/v1``; the broker persists the change.

This is a short-lived synchronous MQTT round-trip; callers run it off the event loop
(``asyncio.to_thread``). The device password is generated here, returned to the caller
once, and never stored — only its dynsec username is kept.
"""

from __future__ import annotations

import contextlib
import json
import queue
import secrets
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

_CONTROL = "$CONTROL/dynamic-security/v1"
_RESPONSE = "$CONTROL/dynamic-security/v1/response"


class ProvisionError(RuntimeError):
    """A dynamic-security control command failed or timed out."""


def device_username(device_id: int) -> str:
    return f"dev-{device_id}"


def device_topic_prefix(device_id: int) -> str:
    return f"eeper/dev/{device_id}/"


class MqttProvisioner:
    def __init__(self, host: str, port: int, ca_cert: str, username: str, password: str) -> None:
        self._host = host
        self._port = port
        self._ca = ca_cert
        self._user = username
        self._pw = password

    @property
    def enabled(self) -> bool:
        return bool(self._host and self._user)

    def _run(self, commands: list[dict[str, object]]) -> None:
        """Publish a dynsec command batch and raise if any command errors."""
        responses: queue.Queue[bytes] = queue.Queue()
        subscribed = threading.Event()
        client = mqtt.Client(CallbackAPIVersion.VERSION2)
        client.username_pw_set(self._user, self._pw)
        if self._ca:
            client.tls_set(ca_certs=self._ca)
        client.on_message = lambda _c, _u, msg: responses.put(msg.payload)
        # Subscribe to the response topic in on_connect and only publish once the SUBACK
        # is in — otherwise the broker can run the command and publish the response
        # before our subscription registers, and we'd miss it.
        client.on_connect = lambda c, *_a: c.subscribe(_RESPONSE)
        client.on_subscribe = lambda *_a: subscribed.set()
        try:
            client.connect(self._host, self._port, keepalive=15)
        except OSError as exc:  # broker unreachable / TLS failure
            raise ProvisionError(f"cannot reach the MQTT control API: {exc}") from exc
        client.loop_start()
        try:
            if not subscribed.wait(timeout=10):
                raise ProvisionError("could not subscribe to the dynamic-security response")
            client.publish(_CONTROL, json.dumps({"commands": commands}), qos=1)
            try:
                payload = responses.get(timeout=10)
            except queue.Empty as exc:
                raise ProvisionError("no response from the dynamic-security control API") from exc
        finally:
            client.loop_stop()
            client.disconnect()
        for r in json.loads(payload).get("responses", []):
            if r.get("error"):
                raise ProvisionError(f"{r.get('command')}: {r['error']}")

    def provision_device(self, device_id: int) -> tuple[str, str]:
        """Create the device's account + a role scoped to its subtree. Returns
        ``(username, plaintext_password)`` — the password is not persisted."""
        username = device_username(device_id)
        password = secrets.token_urlsafe(24)
        subtree = f"{device_topic_prefix(device_id)}#"
        self._run(
            [
                {"command": "createClient", "username": username, "password": password},
                {"command": "createRole", "rolename": username},
                {
                    "command": "addRoleACL",
                    "rolename": username,
                    "acltype": "publishClientSend",
                    "topic": subtree,
                    "allow": True,
                },
                {
                    "command": "addRoleACL",
                    "rolename": username,
                    "acltype": "subscribePattern",
                    "topic": subtree,
                    "allow": True,
                },
                {"command": "addClientRole", "username": username, "rolename": username},
            ]
        )
        return username, password

    def deprovision_device(self, device_id: int) -> None:
        """Remove the device's account + role. Missing account / repeat delete must not
        block unpairing (the DB row is the source of truth), so errors are suppressed."""
        username = device_username(device_id)
        with contextlib.suppress(ProvisionError):
            self._run(
                [
                    {"command": "deleteClient", "username": username},
                    {"command": "deleteRole", "rolename": username},
                ]
            )
