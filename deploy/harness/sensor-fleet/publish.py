"""Synthetic sensor fleet — replays scripted sensor traces over MQTT.

Publishes the eeper sensor contract for a small fleet of nodes (mmWave, PIR,
pulse-ox), so later milestones (and the M0.3 harness self-test) have a live
source of realistic messages without hardware. Timing, dropout, and malformed
output are controllable via env vars for fuzzing/resilience tests later.

Contract:
  eeper/{node}/motion   -> {ts, type, value, unit, quality}
  eeper/{node}/pulseox  -> {ts, hr, spo2, perfusion, quality}

Env:
  MQTT_HOST (default mosquitto), MQTT_PORT (1883), PUBLISH_INTERVAL (1.0 s),
  DROPOUT_RATE (0.0), MALFORMED_RATE (0.0), SEED (1).
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import sys
import time
from types import FrameType
from typing import Any

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
INTERVAL = float(os.environ.get("PUBLISH_INTERVAL", "1.0"))
DROPOUT_RATE = float(os.environ.get("DROPOUT_RATE", "0.0"))
MALFORMED_RATE = float(os.environ.get("MALFORMED_RATE", "0.0"))
SEED = int(os.environ.get("SEED", "1"))

_running = True


def _stop(_signum: int, _frame: FrameType | None) -> None:
    global _running
    _running = False


def _motion(
    node: str, kind: str, value: float, unit: str, quality: float, ts: float
) -> tuple[str, dict[str, Any]]:
    return f"eeper/{node}/motion", {
        "ts": ts,
        "type": kind,
        "value": round(value, 4),
        "unit": unit,
        "quality": round(quality, 3),
    }


def _pulseox(
    node: str, hr: int, spo2: int, perfusion: float, quality: float, ts: float
) -> tuple[str, dict[str, Any]]:
    return f"eeper/{node}/pulseox", {
        "ts": ts,
        "hr": hr,
        "spo2": spo2,
        "perfusion": round(perfusion, 3),
        "quality": round(quality, 3),
    }


def _sample(
    rng: random.Random, tick: int, ts: float
) -> list[tuple[str, dict[str, Any]]]:
    """One scripted reading per node for this tick."""
    phase = tick * INTERVAL
    return [
        # 60 GHz mmWave: a smooth movement index with light noise.
        _motion(
            "mmwave-1",
            "movement",
            value=max(
                0.0, 0.5 + 0.4 * math.sin(phase / 20.0) + rng.uniform(-0.05, 0.05)
            ),
            unit="index",
            quality=0.95,
            ts=ts,
        ),
        # PIR: occupancy that flips occasionally.
        _motion(
            "pir-1",
            "presence",
            value=float((tick // 15) % 2),
            unit="bool",
            quality=1.0,
            ts=ts,
        ),
        # Pulse-ox: plausible resting infant ranges with jitter and a quality flag.
        _pulseox(
            "pulseox-1",
            hr=120 + rng.randint(-6, 6),
            spo2=98 + rng.randint(-2, 1),
            perfusion=1.5 + rng.uniform(-0.3, 0.3),
            quality=rng.choice([0.9, 0.95, 0.4]),  # occasional low-quality sample
            ts=ts,
        ),
    ]


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    rng = random.Random(SEED)  # noqa: S311 - synthetic test data, not security

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    print(
        f"sensor-fleet publishing to {MQTT_HOST}:{MQTT_PORT} every {INTERVAL}s",
        flush=True,
    )

    tick = 0
    while _running:
        ts = time.time()
        for topic, payload in _sample(rng, tick, ts):
            if rng.random() < DROPOUT_RATE:
                continue  # simulated dropout
            body = json.dumps(payload)
            if rng.random() < MALFORMED_RATE:
                body = body[: len(body) // 2]  # simulated truncated/malformed message
            client.publish(topic, body, qos=0)
        tick += 1
        time.sleep(INTERVAL)

    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
