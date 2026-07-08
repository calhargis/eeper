"""M2.2 integration: backpressure (C4).

Run against a stack where the scorer is artificially slowed
(EEPER_INSIGHT_SCORER_DELAY_MS via the insight-motion-test overlay). Because the
producer keeps feeding at the frame rate while the scorer lags, the FrameRing drops
the backlog rather than queueing it: the newest frame the scorer processes stays
fresh (< 3 s) and far fewer frames are scored than fed. The deterministic
memory-bound proof (ring capped at maxlen) lives in the server unit tests; this
confirms the drop path end-to-end on a real stack.
"""

from __future__ import annotations

import time

import httpx

_FRESHNESS_BUDGET_S = 3.0


def test_slow_scorer_drops_frames_and_stays_fresh(
    stack, admin: httpx.Client, motion_camera: dict
) -> None:
    camera_id = motion_camera["id"]

    # Wait until the (slowed) scorer has produced output.
    deadline = time.time() + 40
    while time.time() < deadline:
        motion = stack.read_motion(camera_id)
        if motion is not None and motion["frames_scored"] > 2:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("slowed scorer produced no output within 40s")

    # Observe for a window: freshness must stay under budget throughout, and the
    # producer must feed far more frames than the scorer consumes (drop, not queue).
    samples = 0
    worst_freshness = 0.0
    first = stack.read_motion(camera_id)
    assert first is not None
    end = time.time() + 20
    last = first
    while time.time() < end:
        motion = stack.read_motion(camera_id)
        if motion is not None:
            worst_freshness = max(worst_freshness, motion["freshness_seconds"])
            assert motion["freshness_seconds"] < _FRESHNESS_BUDGET_S, (
                f"processed-frame freshness {motion['freshness_seconds']}s exceeded "
                f"{_FRESHNESS_BUDGET_S}s — scorer is queueing, not dropping"
            )
            last = motion
            samples += 1
        time.sleep(0.5)

    assert samples >= 5, f"too few tap samples ({samples})"
    fed = last["frames_fed"] - first["frames_fed"]
    scored = last["frames_scored"] - first["frames_scored"]
    assert scored > 0, "scorer made no progress"
    # A ~2 s delay against a 5 fps producer should drop the large majority of frames.
    assert fed > scored * 5, f"expected frames dropped (fed={fed}, scored={scored})"
    assert worst_freshness < _FRESHNESS_BUDGET_S
