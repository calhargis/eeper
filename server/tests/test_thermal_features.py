"""M6.1 thermal feature extractor: an empty room reports no presence; a warm body is
detected with a centroid near it; a bigger body yields more area + confidence. This pins
the extractor's DIRECTION and determinism — its accuracy is the separate M6.2 gate."""

from __future__ import annotations

import random

import pytest

from eeper.thermal.features import (
    CELLS,
    COLS,
    DEFAULT_FEATURE_PARAMS,
    DEFAULT_GATE_PARAMS,
    ROWS,
    PresenceGate,
    ThermalFeatures,
    derive_features,
)
from eeper.thermal.sensor import Scene, WarmBlob, render


def test_empty_room_no_presence() -> None:
    grid = render(Scene(ambient_c=21.0), random.Random(1))
    f = derive_features(grid)
    assert f.presence is False
    assert f.warm_region_centroid is None
    assert f.presence_confidence == 0.0


def test_warm_body_detected_with_centroid_near_it() -> None:
    blob = WarmBlob(row=12.0, col=16.0, radius=3.0, delta_c=8.0)
    grid = render(Scene(ambient_c=21.0, blobs=(blob,)), random.Random(2))
    f = derive_features(grid)
    assert f.presence is True
    assert 0.0 < f.presence_confidence <= 1.0
    assert f.warm_region_centroid is not None
    row, col = f.warm_region_centroid
    assert abs(row - 12.0 / (ROWS - 1)) < 0.1
    assert abs(col - 16.0 / (COLS - 1)) < 0.1


def test_bigger_body_more_area_and_confidence() -> None:
    small = derive_features(
        render(Scene(blobs=(WarmBlob(12.0, 16.0, 2.0, 8.0),)), random.Random(3))
    )
    big = derive_features(render(Scene(blobs=(WarmBlob(12.0, 16.0, 5.0, 8.0),)), random.Random(3)))
    assert big.warm_region_area > small.warm_region_area
    assert big.presence_confidence >= small.presence_confidence


def test_render_is_reproducible_and_full_size() -> None:
    a = render(Scene(), random.Random(7))
    b = render(Scene(), random.Random(7))
    assert a == b and len(a) == CELLS


def test_wrong_length_raises() -> None:
    with pytest.raises(ValueError, match="cells"):
        derive_features([21.0] * 10)


# ── contrast: the discriminator that separates an empty crib from an occupied one ────
#
# The numbers below are measured, not invented. 475 frames of continuously occupied crib on
# a live deployment held contrast between 5.9 and 8.3 °C (median 6.8) with warm area
# 0.065–0.163 — never once near the 4.0 °C acquire threshold. An empty
# crib has only sensor noise and slow room gradients, and it was those gradients — enough
# cells drifting over a FIXED offset, with no notion of how much hotter they were — that the
# old count-only rule reported as a body.


def _gate() -> PresenceGate:
    return PresenceGate()


def test_broad_gentle_warmth_is_not_a_body() -> None:
    """The regression that motivated this change: a wide, barely-warm region (sun on a rail,
    a warm wall, sensor drift) clears the warm-cell COUNT easily but is nowhere near
    body-warm. Counting cells alone called this presence."""
    grid = render(
        Scene(ambient_c=21.0, blobs=(WarmBlob(row=12.0, col=16.0, radius=4.0, delta_c=3.5),)),
        random.Random(11),
    )
    f = derive_features(grid)
    assert f.warm_region_area >= DEFAULT_FEATURE_PARAMS.min_area, (
        "precondition: the old count-only rule would have called this presence"
    )
    assert f.contrast_c < DEFAULT_FEATURE_PARAMS.enter_contrast_c
    assert f.presence is False


def test_a_real_body_clears_the_contrast_gate_with_margin() -> None:
    """A scene matching the measured occupied deployment must not merely pass — it must pass
    with room to spare, or normal variation would put it back on the boundary."""
    grid = render(
        Scene(ambient_c=23.5, blobs=(WarmBlob(row=12.0, col=16.0, radius=4.5, delta_c=9.0),)),
        random.Random(12),
    )
    f = derive_features(grid)
    assert f.presence is True
    assert f.contrast_c > DEFAULT_FEATURE_PARAMS.enter_contrast_c + 2.0
    assert f.presence_confidence > 0.5


def test_a_hot_speck_is_not_a_body() -> None:
    """Contrast alone is not enough either — a phone charger or a sunlit screw head is
    scorching over a handful of cells. Area still has to agree."""
    grid = render(
        Scene(ambient_c=21.0, blobs=(WarmBlob(row=12.0, col=16.0, radius=1.2, delta_c=12.0),)),
        random.Random(13),
    )
    f = derive_features(grid)
    assert f.contrast_c > DEFAULT_FEATURE_PARAMS.enter_contrast_c
    assert f.warm_region_area < DEFAULT_FEATURE_PARAMS.min_area
    assert f.presence is False


def test_confidence_takes_the_weaker_evidence() -> None:
    """A large lukewarm region should not read as confident just because it is large."""
    lukewarm = derive_features(
        render(Scene(blobs=(WarmBlob(12.0, 16.0, 6.0, 5.5),)), random.Random(11))
    )
    hot = derive_features(render(Scene(blobs=(WarmBlob(12.0, 16.0, 4.5, 9.0),)), random.Random(11)))
    assert lukewarm.presence and hot.presence
    assert lukewarm.warm_region_area > 0
    assert lukewarm.presence_confidence < hot.presence_confidence


# ── the gate: measured flapping was a median run of 2.3 s ────────────────────────────


def _feats(contrast: float, area: float = 0.09) -> ThermalFeatures:
    """A features record with just the fields the gate reads."""
    return ThermalFeatures(
        presence=contrast >= DEFAULT_FEATURE_PARAMS.enter_contrast_c,
        presence_confidence=1.0,
        warm_region_area=area,
        warm_region_centroid=(0.5, 0.5),
        contrast_c=contrast,
    )


def test_a_short_blip_never_reaches_the_output() -> None:
    """The headline fix. On the deployment, 62 of 70 presence runs were under 30 s and the
    median was 2.3 s — dithering, not observation. None of it should survive."""
    gate = _gate()
    now = 1000.0
    for _ in range(3):  # a 3-second blip, repeated
        for _ in range(3):
            assert gate.update(_feats(8.0), now) is False
            now += 1.0
        for _ in range(20):  # back to empty
            assert gate.update(_feats(1.2), now) is False
            now += 1.0


def test_sustained_presence_is_reported() -> None:
    gate = _gate()
    now = 1000.0
    reported = [gate.update(_feats(7.2), now + i) for i in range(30)]
    assert reported[-1] is True
    assert any(r is False for r in reported), "it should take a moment to be believed"
    # ...and once believed, it stays believed for a genuine occupancy.
    assert all(gate.update(_feats(7.2), now + 30 + i) for i in range(600))


def test_a_settling_baby_is_not_lost() -> None:
    """Contrast hysteresis. A baby who burrows under a blanket radiates less through it and
    can fall below the ACQUIRE threshold. Reporting an empty crib for a child who never moved
    is the one error this feature must not make."""
    gate = _gate()
    now = 1000.0
    for i in range(30):  # established presence
        gate.update(_feats(7.2), now + i)
    assert gate.state is True
    now += 30
    # Now hovering between the exit and enter thresholds, for a long time.
    for i in range(600):
        assert gate.update(_feats(3.4), now + i) is True


def test_a_real_departure_is_eventually_reported() -> None:
    """Holding presence must not mean holding it forever — an empty crib has to be reported,
    just not hastily."""
    gate = _gate()
    now = 1000.0
    for i in range(30):
        gate.update(_feats(7.2), now + i)
    now += 30
    reported = [gate.update(_feats(1.1), now + i) for i in range(200)]
    assert reported[0] is True, "not instantly"
    assert reported[-1] is False, "but it does clear"


def test_a_backwards_clock_does_not_strand_the_gate() -> None:
    """A Pi has no RTC; an NTP step can move the clock backwards mid-window. A naive elapsed
    check would then need to wait out a negative duration and never flip again."""
    gate = _gate()
    assert gate.update(_feats(7.2), 5000.0) is False  # opens the acquire window
    assert gate.update(_feats(7.2), 4000.0) is False  # ...then the clock steps back 1000 s
    # The window must have restarted from the new clock, not be waiting out a negative
    # elapsed that can never be satisfied.
    reported = [gate.update(_feats(7.2), 4000.0 + i) for i in range(1, 40)]
    assert reported[-1] is True, "the gate must still be able to acquire after a clock step"


def _scene(delta_c: float, radius: float = 4.5, seed: int = 31) -> ThermalFeatures:
    """Real extractor output for a body of a given warmth — so the gate tests are driven by
    records the extractor can actually produce, not hand-built ones."""
    blobs = (WarmBlob(12.0, 16.0, radius, delta_c),) if delta_c > 0 else ()
    return derive_features(render(Scene(ambient_c=23.5, blobs=blobs), random.Random(seed)))


def test_evidence_is_reported_even_when_the_frame_verdict_is_absent() -> None:
    """The bug this pins: shape used to be computed only when the per-frame verdict said
    presence. In the band where the gate HOLDS presence on the lower threshold — a baby
    settled under a blanket — that published 'someone is there, confidence zero, location
    unknown' for exactly the child the hysteresis exists to keep track of."""
    settled = _scene(delta_c=4.2, radius=4.0)
    assert settled.presence is False, "precondition: below the acquire threshold"
    assert DEFAULT_FEATURE_PARAMS.exit_contrast_c <= settled.contrast_c
    assert settled.warm_region_centroid is not None, "the warm region is still described"
    assert settled.presence_confidence > 0.0


def test_an_empty_frame_reports_no_shape() -> None:
    """The converse: with no warm region there is genuinely nothing to describe, and the
    record must not invent one."""
    empty = _scene(delta_c=0.0)
    assert empty.presence is False
    assert empty.warm_region_centroid is None
    assert empty.presence_confidence == 0.0


def test_the_gate_holds_a_settling_baby_using_real_frames() -> None:
    gate = _gate()
    occupied, settled = _scene(9.0), _scene(delta_c=4.2, radius=4.0)
    now = 1000.0
    for i in range(20):
        gate.update(occupied, now + i)
    assert gate.state is True
    # The frame verdict flips to absent, but the gate's lower hold threshold keeps presence.
    assert settled.presence is False
    assert all(gate.update(settled, now + 20 + i) for i in range(300))


def test_presence_is_released_when_the_warm_region_vanishes_not_just_the_heat() -> None:
    """Area has to keep agreeing too. A body that leaves behind a small very hot patch (a
    warmed mattress dimple) still has high contrast — it must not hold presence forever."""
    gate = _gate()
    occupied = _scene(9.0)
    for i in range(20):
        gate.update(occupied, 1000.0 + i)
    assert gate.state is True
    speck = derive_features(
        render(Scene(23.5, blobs=(WarmBlob(12.0, 16.0, 1.2, 12.0),)), random.Random(13))
    )
    assert speck.contrast_c > DEFAULT_FEATURE_PARAMS.exit_contrast_c, "still hot..."
    assert speck.warm_region_area < DEFAULT_FEATURE_PARAMS.min_area, "...but no longer a body"
    reported = [gate.update(speck, 1100.0 + i) for i in range(120)]
    assert reported[-1] is False


def test_the_release_window_is_the_documented_length() -> None:
    """The most safety-relevant constant in the change: how long presence survives contrary
    evidence. Bracketed on both sides so it cannot drift unnoticed in either direction."""
    gate = _gate()
    occupied, empty = _scene(9.0), _scene(0.0)
    for i in range(20):
        gate.update(occupied, 1000.0 + i)
    assert gate.state is True
    start = 1020.0
    reported = {t: gate.update(empty, start + t) for t in range(0, 60)}
    hold = DEFAULT_GATE_PARAMS.min_off_seconds
    assert reported[int(hold) - 5] is True, "must not release early"
    assert reported[int(hold) + 5] is False, "but must release"
    assert hold > DEFAULT_GATE_PARAMS.min_on_seconds * 3, (
        "releasing presence must be markedly slower than acquiring it"
    )


def test_a_sensor_outage_is_not_mistaken_for_sustained_evidence() -> None:
    """A sustain window is meant to prove something HELD, which takes observations. The
    publisher drops frames on an I²C read failure, so without a gap check a sensor that
    stalls mid-window and recovers minutes later would have the dead time counted as
    agreement — two isolated blips either side of an outage could latch presence."""
    gate = _gate()
    occupied = _scene(9.0)
    assert gate.update(occupied, 1000.0) is False  # opens the acquire window
    # ...the sensor now stalls for five minutes, then returns a single warm frame.
    assert gate.update(occupied, 1300.0) is False, "the dead time is not evidence"
    # Only continuous observation acquires it.
    assert [gate.update(occupied, 1300.0 + i) for i in range(1, 20)][-1] is True
