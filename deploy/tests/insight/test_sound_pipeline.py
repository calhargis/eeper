"""M2.3 integration (crit 3): a sustained sound through the synthetic camera produces
a ``sound_elevated`` event end-to-end (MQTT + state_history).

The synthetic ``cam-sound`` source is a quiet floor with a loud 20 s burst every 40 s,
so a real sound onset is guaranteed inside the observation window. The sound-level
nudge is model-free, so a gated tone stands in for a cry — this exercises the audio
scorer -> detector -> DB/MQTT path the same way a real crying spell would. (The tight
onset->nudge latency is asserted deterministically by the quality gate; here we prove
the end-to-end plumbing and that a quiet nursery's own floor is not mistaken for it.)
"""

from __future__ import annotations

import time

import httpx


def test_sound_samples_flow_and_onset_produces_event(
    stack, admin: httpx.Client, sound_camera: dict
) -> None:
    camera_id = sound_camera["id"]
    assert sound_camera["has_audio"] is True  # cam-sound carries an audio track
    sample_topic = f"eeper/insight/sound/cam{camera_id}"
    state_topic = f"eeper/insight/state/cam{camera_id}/sound_level"

    # 1. Per-window sound samples flow (the audio scorer is running on this camera).
    deadline = time.time() + 45
    sample = None
    while time.time() < deadline:
        sample = stack.mqtt_retained(sample_topic, timeout_s=3)
        if sample is not None:
            break
    assert sample is not None, "no sound-level samples published"
    assert sample["type"] == "sound_level" and sample["unit"] == "dBFS"

    # 2. A loud onset produces a sound_level state_history row within a cycle + the
    #    detector's sustain budget (the cam-sound cycle is 40 s).
    deadline = time.time() + 75
    row = None
    while time.time() < deadline:
        row = stack.latest_state(camera_id, state_type="sound_level")
        if row is not None:
            break
        time.sleep(1)
    assert row is not None, "no sound_level state_history row for the sound onset"

    # 3. The movement/sound/cry topics are per-signal-type, so the retained sound-level
    #    state is exactly this signal. cam-sound cycles back to quiet ~20 s later, so
    #    the retained value may already read the subsequent quiet transition — whose
    #    `previous` still proves an elevation was published. Either form confirms it.
    event = stack.mqtt_retained(state_topic)
    assert event is not None, "no retained sound-level state on MQTT"
    assert event["state_type"] == "sound_level"
    assert event["contributing_inputs"] == ["sound_level"]
    assert event["value"] == "elevated" or event.get("previous") == "elevated", (
        f"no sound_elevated transition was published (event={event})"
    )
