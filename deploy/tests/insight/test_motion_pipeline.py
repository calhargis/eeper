"""M2.2 integration: motion onset -> MQTT event + state_history row within 2 s
(C3), and video-only graceful degradation (C5-live).

The synthetic ``cam-motion`` source alternates still<->moving on an 8 s cycle, so a
real motion onset is guaranteed inside the observation window. Onset time is taken
from the motion tap's own ts and the transition time from the state_history row's
ts — both insight-internal clocks — so the 2 s budget is measured on the pipeline,
not on the test harness's docker-exec latency.
"""

from __future__ import annotations

import subprocess
import time

import httpx

_EPS = 0.003  # a smoothed score above this means motion has begun (still phase ~0)
_BUDGET_S = 2.0


def test_insight_and_mqtt_containers_hardened(stack) -> None:
    for service in ("insight", "mqtt"):
        cid = stack.container_id(service)
        assert cid, f"{service} not running"
        out = subprocess.run(
            [
                "docker",
                "inspect",
                cid,
                "--format",
                "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        user, readonly, cap_drop = out.split("|")
        assert user and user not in ("root", "0", "0:0"), (
            f"{service} runs as root ({user!r})"
        )
        assert readonly == "true", f"{service} rootfs is not read-only"
        assert "ALL" in cap_drop, (
            f"{service} does not drop all capabilities ({cap_drop})"
        )


def test_mqtt_broker_has_no_host_port(stack) -> None:
    # The broker must be internal-only (published movement events never leave the
    # host network). A mapped host port would be a regression.
    cid = stack.container_id("mqtt")
    ports = subprocess.run(
        ["docker", "inspect", cid, "--format", "{{json .NetworkSettings.Ports}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert "HostPort" not in ports, f"mqtt broker exposes a host port: {ports}"


def test_mqtt_broker_refuses_plaintext_and_anonymous(stack) -> None:
    # M3.1 TLS enforcement. There is no plaintext listener, so a plaintext connection
    # to the TLS port is refused; and with anonymous access off, even a valid TLS
    # connection without credentials is rejected. Both must fail (non-zero exit).
    cid = stack.container_id("mqtt")

    def _pub(*extra: str) -> int:
        return subprocess.run(
            ["docker", "exec", cid, "mosquitto_pub", "-h", "127.0.0.1", "-p", "8883",
             *extra, "-t", "probe", "-m", "x"],
            capture_output=True, text=True, check=False,
        ).returncode

    assert _pub() != 0, "broker accepted a plaintext connection on the TLS port"
    assert _pub("--cafile", "/mosquitto/certs/mqtt-ca.crt") != 0, (
        "broker accepted an anonymous TLS connection (allow_anonymous should be off)"
    )


def _await_onset(stack, camera_id: int, deadline: float) -> float:
    """Wait for a still stretch then the next still->moving onset; return the motion
    tap's ts (insight-internal epoch) at the onset."""
    armed = False
    while time.time() < deadline:
        motion = stack.read_motion(camera_id)
        if motion is not None:
            if motion["score"] < _EPS:
                armed = True  # in a still phase — ready to catch the next onset
            elif armed and motion["score"] >= _EPS:
                return float(motion["ts"])
        time.sleep(0.15)
    raise AssertionError("never observed a still->moving motion onset")


def test_motion_onset_produces_state_and_event_within_2s(
    stack, admin: httpx.Client, motion_camera: dict
) -> None:
    camera_id = motion_camera["id"]
    assert motion_camera["has_audio"] is False  # cam-motion has no audio track
    # State transitions publish on a per-signal-type retained topic (M2.3), so a
    # movement change and a sound/cry change never clobber each other's last value.
    topic = f"eeper/insight/state/cam{camera_id}/movement_level"

    deadline = time.time() + 45
    t_onset = _await_onset(stack, camera_id, deadline)

    # Within budget of the onset, a non-low state_history row must appear whose
    # transition time is within 2 s of the onset (measured on insight-internal ts).
    row_ts: float | None = None
    while time.time() < deadline:
        state = stack.latest_state(camera_id)
        if state is not None and state[1] != "low" and state[0] >= t_onset - 1.0:
            row_ts = state[0]
            break
        time.sleep(0.2)
    assert row_ts is not None, (
        "no non-low state_history row appeared for the motion onset"
    )
    latency = row_ts - t_onset
    assert latency < _BUDGET_S, (
        f"state change took {latency:.2f}s from onset (> {_BUDGET_S}s)"
    )

    # And the movement-level event must be on MQTT (retained, so a late subscriber
    # still sees the last transition). cam-motion cycles back to still ~4 s later, so
    # the retained topic may already read the subsequent low transition — whose
    # `previous` still proves the non-low event was published. Either form confirms it.
    event = stack.mqtt_retained(topic)
    assert event is not None, "no retained movement-level event on MQTT"
    assert event["state_type"] == "movement_level"
    assert event["contributing_inputs"] == ["motion"]
    non_low = event["value"] in ("medium", "high")
    assert non_low or event.get("previous") in ("medium", "high"), (
        f"no non-low movement-level event was published (event={event})"
    )
    if non_low:
        assert abs(event["ts"] - row_ts) < _BUDGET_S, (
            "MQTT event ts diverges from the DB row"
        )


def test_video_only_camera_runs_and_reports_only_motion(
    stack, admin: httpx.Client, noaudio_camera: dict
) -> None:
    # C5-live: a video-only source has no audio branch; the engine still runs and
    # derives motion, and the only contributing extractor is motion.
    camera_id = noaudio_camera["id"]
    assert noaudio_camera["has_audio"] is False

    deadline = time.time() + 30
    motion = None
    while time.time() < deadline:
        motion = stack.read_motion(camera_id)
        if motion is not None and motion["frames_scored"] > 0:
            break
        time.sleep(0.5)
    assert motion is not None, (
        "insight produced no motion output for the video-only camera"
    )
    assert motion["contributing_inputs"] == ["motion"], motion["contributing_inputs"]

    # testsrc2 is always moving, so a non-low state row is produced for it too.
    deadline = time.time() + 20
    state = None
    while time.time() < deadline:
        state = stack.latest_state(camera_id)
        if state is not None:
            break
        time.sleep(0.5)
    assert state is not None, "no state_history row for the video-only camera"
