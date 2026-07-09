"""The real Web Push send path (M2.4): generate a VAPID keypair + a browser
subscription keypair, point the subscription at a headless mock push service, and
assert pywebpush actually encrypts and POSTs the nudge (and that a 410 tells us the
subscription is gone). This exercises the VAPID/HTTP mechanics the worker's faked
policy tests don't."""

from __future__ import annotations

import base64
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from eeper.api import push_service
from eeper.api.models import PushSubscription


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _vapid_private_key() -> str:
    priv = ec.generate_private_key(ec.SECP256R1())
    return _b64(priv.private_numbers().private_value.to_bytes(32, "big"))


def _browser_subscription(endpoint: str) -> PushSubscription:
    priv = ec.generate_private_key(ec.SECP256R1())
    point = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return PushSubscription(
        user_id=1, endpoint=endpoint, p256dh=_b64(point), auth=_b64(os.urandom(16))
    )


class _MockPushService:
    """A headless push service: records one POST and replies with a chosen status."""

    def __init__(self, reply_status: int = 201) -> None:
        self.received: list[bytes] = []
        self._reply = reply_status
        received, reply = self.received, reply_status

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
                length = int(self.headers.get("Content-Length", "0"))
                received.append(self.rfile.read(length))
                self.send_response(reply)
                self.end_headers()

            def log_message(self, *_: object) -> None:  # silence
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _MockPushService:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


async def test_real_push_reaches_the_headless_service() -> None:
    with _MockPushService(reply_status=201) as service:
        sub = _browser_subscription(f"http://127.0.0.1:{service.port}/push")
        result = await push_service.send_push(
            sub,
            {"title": "Sound in the nursery", "body": "Tap to see the moment.", "url": "/tonight"},
            vapid_private_key=_vapid_private_key(),
            vapid_subject="mailto:test@eeper.local",
        )
    assert result == "sent"
    assert len(service.received) == 1
    assert service.received[0]  # an encrypted (non-empty) body was delivered


async def test_push_reports_gone_subscription() -> None:
    with _MockPushService(reply_status=410) as service:  # push service: subscription expired
        sub = _browser_subscription(f"http://127.0.0.1:{service.port}/push")
        result = await push_service.send_push(
            sub,
            {"title": "x", "body": "y"},
            vapid_private_key=_vapid_private_key(),
            vapid_subject="mailto:test@eeper.local",
        )
    assert result == "gone"  # the worker deletes the subscription on this


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
