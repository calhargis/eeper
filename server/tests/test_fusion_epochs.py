"""Unit tests for epoch featurization (M3.3)."""

from __future__ import annotations

from pytest import approx

from eeper.fusion.epochs import Sample, featurize


def test_bins_and_aggregates_by_field() -> None:
    # Two epochs of 30 s. Continuous fields average; presence/cry take the max.
    samples = [
        Sample(0, "motion", 0.2, "camera"),
        Sample(10, "motion", 0.4, "camera"),
        Sample(5, "presence", 0.0, "sensor"),
        Sample(20, "presence", 1.0, "sensor"),
        Sample(35, "motion", 0.9, "camera"),  # epoch 1
    ]
    feats = featurize(samples, start=0.0, n_epochs=2)
    assert feats[0].motion == approx(0.3)  # mean(0.2, 0.4)
    assert feats[0].presence == 1.0  # max(0.0, 1.0)
    assert feats[1].motion == 0.9
    assert feats[0].inputs == ("camera", "sensor")


def test_absent_field_is_none_not_zero() -> None:
    feats = featurize([Sample(0, "motion", 0.5)], start=0.0, n_epochs=2)
    assert feats[0].motion == 0.5
    assert feats[0].radar_move is None and feats[0].sound is None  # no radar/audio samples
    assert feats[1].motion is None  # empty epoch → all None (a gap, not a zero)


def test_out_of_range_and_unknown_samples_are_ignored() -> None:
    samples = [
        Sample(-5, "motion", 1.0),  # before start
        Sample(999, "motion", 1.0),  # past the last epoch
        Sample(0, "bogus", 1.0),  # unknown field
        Sample(0, "motion", 0.5),  # the only valid one
    ]
    feats = featurize(samples, start=0.0, n_epochs=2)
    assert feats[0].motion == 0.5
    assert feats[1].motion is None


def test_cry_is_max_within_the_epoch() -> None:
    feats = featurize(
        [Sample(0, "cry", 0.0), Sample(10, "cry", 1.0), Sample(20, "cry", 0.0)],
        start=0.0,
        n_epochs=1,
    )
    assert feats[0].cry == 1.0
