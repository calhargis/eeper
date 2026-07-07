"""Harness self-test (M0.3): the synthetic camera streams and the synthetic
sensor fleet publishes. Assumes the harness compose is already up (the CI job
brings it up first). Becomes a required check for later milestones."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import paho.mqtt.client as mqtt

HARNESS_DIR = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(HARNESS_DIR / "docker-compose.yml")]
MQTT_PORT = int(os.environ.get("HARNESS_MQTT_PORT", "1883"))
RTSP_URL = "rtsp://localhost:8554/cam"


def _ffprobe_streams() -> list[dict[str, object]]:
    """Probe the RTSP stream from inside the camera container (it has ffprobe)."""
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "synthetic-camera",
            "ffprobe",
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            RTSP_URL,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return []
    try:
        return list(json.loads(result.stdout).get("streams", []))
    except json.JSONDecodeError:
        return []


def test_synthetic_camera_streams() -> None:
    streams: list[dict[str, object]] = []
    for _ in range(30):  # ffmpeg needs a moment to start publishing
        streams = _ffprobe_streams()
        if streams:
            break
        time.sleep(2)
    assert streams, "synthetic camera RTSP stream is not readable"

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    assert video is not None, f"no video stream: {streams}"
    assert video.get("codec_name") == "h264", f"expected H.264: {video}"
    assert video.get("width") == 1280 and video.get("height") == 720, (
        f"unexpected size: {video}"
    )
    assert audio is not None and audio.get("codec_name") == "aac", (
        f"no AAC audio: {streams}"
    )


def test_sensor_fleet_publishing() -> None:
    received: dict[str, dict[str, object]] = {}

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            received[msg.topic] = payload

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect("127.0.0.1", MQTT_PORT, keepalive=30)
    client.subscribe("eeper/#")
    client.loop_start()
    # Fleet publishes every 0.5s; collect a few cycles.
    deadline = time.time() + 15
    while (
        time.time() < deadline
        and not {
            "eeper/mmwave-1/motion",
            "eeper/pir-1/motion",
            "eeper/pulseox-1/pulseox",
        }
        <= received.keys()
    ):
        time.sleep(0.5)
    client.loop_stop()
    client.disconnect()

    assert "eeper/mmwave-1/motion" in received, (
        f"no mmWave motion; got {list(received)}"
    )
    assert "eeper/pir-1/motion" in received, f"no PIR presence; got {list(received)}"
    assert "eeper/pulseox-1/pulseox" in received, f"no pulse-ox; got {list(received)}"

    motion = received["eeper/mmwave-1/motion"]
    assert {"ts", "type", "value", "unit", "quality"} <= motion.keys(), motion
    pulseox = received["eeper/pulseox-1/pulseox"]
    assert {"ts", "hr", "spo2", "perfusion", "quality"} <= pulseox.keys(), pulseox
