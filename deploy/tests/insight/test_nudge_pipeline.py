"""M2.4 end-to-end (the Phase-2 nudge path): a sustained sound on the synthetic
camera fires a `sound_elevated` nudge, the api-side nudge worker auto-promotes a
pre/post-roll clip, the events API returns the event with its clip reference, and the
clip is playable.

This exercises the whole chain — insight engine writes the nudge event (pending) ->
LISTEN/NOTIFY wakes the worker -> the worker waits the post-roll then cuts a clip from
the recorded segments -> `event.clip_id` is linked -> the clip streams. cam-sound fires
an onset every 40 s, so a later onset (once the recorder has warmed up) always has
segment coverage even if the first does not.
"""

from __future__ import annotations

import time

import httpx


def test_sound_nudge_auto_promotes_a_playable_clip(
    stack, admin: httpx.Client, sound_camera: dict
) -> None:
    camera_id = sound_camera["id"]
    assert sound_camera["has_audio"] is True

    # Poll the events API until a sound_elevated event with an auto-promoted clip
    # appears (onset <= 40 s + post-roll + recorder finalization + worker retries).
    deadline = time.time() + 180
    event_with_clip: dict | None = None
    while time.time() < deadline:
        resp = admin.get(f"/api/v1/events?camera_id={camera_id}")
        if resp.status_code == 200:
            for event in resp.json():
                if event["type"] == "sound_elevated" and event["clip_id"] is not None:
                    event_with_clip = event
                    break
        if event_with_clip is not None:
            break
        time.sleep(3)

    assert event_with_clip is not None, (
        "no sound_elevated event with an auto-promoted clip appeared within budget"
    )

    # The linked clip is playable H.264 MP4.
    clip_id = event_with_clip["clip_id"]
    media = admin.get(f"/api/v1/clips/{clip_id}/media")
    assert media.status_code == 200, media.text
    assert media.headers["content-type"] == "video/mp4"
    assert len(media.content) > 1000, "clip media is implausibly small"

    # The clip metadata is retrievable and belongs to this camera.
    meta = admin.get(f"/api/v1/clips/{clip_id}").json()
    assert meta["camera_id"] == camera_id
    assert meta["codec"] == "h264"
    assert meta["duration_seconds"] > 0
