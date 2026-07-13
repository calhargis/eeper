"""Load persisted extractor signals into per-epoch fusion features (M3.3).

Bridges the DB (``state_history`` movement/sound/cry levels, ``sensor_readings``
movement/presence) to the pure fusion package's :class:`EpochFeatures`. The two signal
shapes are handled differently:

* ``state_history`` stores only *transitions* of a step-function level, so a level is
  carried forward until the next change — including a seed transition from before the
  window, so an unchanged level isn't misread as "absent".
* ``sensor_readings`` are dense point samples, binned into the epoch (mean movement,
  max presence).

Signals from multiple cameras/devices are combined per epoch (any source moving ⇒
motion), and a modality with no data in an epoch is left ``None`` so the fusion degrades
to whatever is live.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import PulseOxReading, SensorReading, StateHistory
from eeper.fusion.model import EpochFeatures

# state_history level → 0..1 scalar per fusion field.
_MOVEMENT_MAP = {"low": 0.1, "medium": 0.5, "high": 0.9}
_SOUND_MAP = {"quiet": 0.1, "elevated": 0.75}
_CRY_MAP = {"quiet": 0.0, "crying": 1.0}
# state_type → (EpochFeatures field, value map)
_STATE_KINDS = {
    "movement_level": ("motion", _MOVEMENT_MAP),
    "sound_level": ("sound", _SOUND_MAP),
    "cry": ("cry", _CRY_MAP),
}
_SENSOR_METRICS = ("movement", "presence")


def _carry_forward(
    transitions: list[tuple[float, float]], n_epochs: int, epoch_seconds: int
) -> list[float | None]:
    """Step-function value per epoch. ``transitions`` are ``(offset_seconds, value)``
    sorted ascending, where a negative offset is the seed (the level already in effect
    at the window start). An epoch before the first known transition stays ``None``."""
    out: list[float | None] = [None] * n_epochs
    cur: float | None = None
    ti = 0
    for i in range(n_epochs):
        epoch_end = (i + 1) * epoch_seconds
        while ti < len(transitions) and transitions[ti][0] < epoch_end:
            cur = transitions[ti][1]
            ti += 1
        out[i] = cur
    return out


async def active_households(session: AsyncSession, since: datetime) -> list[str]:
    """Households with any extractor signal since ``since`` — the ones worth fusing."""
    seen: set[str] = set()
    for model in (StateHistory, SensorReading):
        rows = await session.execute(select(model.household_id).where(model.ts >= since).distinct())
        seen.update(rows.scalars())
    return sorted(seen)


async def load_epoch_features(
    session: AsyncSession,
    household_id: str,
    window_start: datetime,
    n_epochs: int,
    epoch_seconds: int,
) -> list[EpochFeatures]:
    """Build ``n_epochs`` of :class:`EpochFeatures` for one household starting at
    ``window_start`` (UTC), from the persisted extractor signals."""
    window_end = window_start + timedelta(seconds=n_epochs * epoch_seconds)

    def offset(ts: datetime) -> float:
        return (ts - window_start).total_seconds()

    # ── step-function levels (movement/sound/cry), carried forward per camera ──
    # In-window transitions + one seed transition per (camera, state_type) from before
    # the window so an unchanged level is known, not None.
    kinds = list(_STATE_KINDS)
    win = await session.execute(
        select(StateHistory.camera_id, StateHistory.state_type, StateHistory.ts, StateHistory.value)
        .where(
            StateHistory.household_id == household_id,
            StateHistory.state_type.in_(kinds),
            StateHistory.ts >= window_start,
            StateHistory.ts < window_end,
        )
        .order_by(StateHistory.camera_id, StateHistory.state_type, StateHistory.ts)
    )
    seed = await session.execute(
        select(StateHistory.camera_id, StateHistory.state_type, StateHistory.ts, StateHistory.value)
        .where(
            StateHistory.household_id == household_id,
            StateHistory.state_type.in_(kinds),
            StateHistory.ts < window_start,
        )
        .distinct(StateHistory.camera_id, StateHistory.state_type)
        .order_by(StateHistory.camera_id, StateHistory.state_type, StateHistory.ts.desc())
    )
    # (camera_id, state_type) → sorted [(offset, mapped_value)] with the seed first.
    series: dict[tuple[int, str], list[tuple[float, float]]] = {}
    for cam, st, ts, value in [*seed, *win]:
        mapped = _STATE_KINDS[st][1].get(value)
        if mapped is None:
            continue  # unknown level string — skip rather than guess
        series.setdefault((cam, st), []).append((offset(ts), mapped))
    for pairs in series.values():
        pairs.sort(key=lambda p: p[0])

    # Per-field, per-epoch value = max across cameras (any camera's signal counts).
    field_epochs: dict[str, list[float | None]] = {
        field: [None] * n_epochs for field, _ in _STATE_KINDS.values()
    }
    for (_, st), pairs in series.items():
        field = _STATE_KINDS[st][0]
        cam_values = _carry_forward(pairs, n_epochs, epoch_seconds)
        acc = field_epochs[field]
        for i, v in enumerate(cam_values):
            if v is None:
                continue
            prev = acc[i]
            acc[i] = v if prev is None else max(prev, v)

    # ── dense sensor readings (movement mean, presence max) ──
    sensor = await session.execute(
        select(SensorReading.ts, SensorReading.metric, SensorReading.value).where(
            SensorReading.household_id == household_id,
            SensorReading.metric.in_(_SENSOR_METRICS),
            SensorReading.ts >= window_start,
            SensorReading.ts < window_end,
        )
    )
    move_bins: list[list[float]] = [[] for _ in range(n_epochs)]
    presence_bins: list[list[float]] = [[] for _ in range(n_epochs)]
    for ts, metric, value in sensor:
        idx = int(offset(ts) // epoch_seconds)
        if not 0 <= idx < n_epochs:
            continue
        (move_bins if metric == "movement" else presence_bins)[idx].append(value)

    # ── pulse-ox HR (M4.2), mean per epoch — the table holds ONLY quality-gated samples,
    # so fusion consumes HR only when quality-gated pulse-ox exists. Absent otherwise. ──
    hr_rows = await session.execute(
        select(PulseOxReading.ts, PulseOxReading.hr).where(
            PulseOxReading.household_id == household_id,
            PulseOxReading.ts >= window_start,
            PulseOxReading.ts < window_end,
        )
    )
    hr_bins: list[list[float]] = [[] for _ in range(n_epochs)]
    for ts, hr in hr_rows:
        idx = int(offset(ts) // epoch_seconds)
        if 0 <= idx < n_epochs:
            hr_bins[idx].append(hr)

    features: list[EpochFeatures] = []
    for i in range(n_epochs):
        motion = field_epochs["motion"][i]
        sound = field_epochs["sound"][i]
        cry = field_epochs["cry"][i]
        radar = sum(move_bins[i]) / len(move_bins[i]) if move_bins[i] else None
        presence = max(presence_bins[i]) if presence_bins[i] else None
        hr = sum(hr_bins[i]) / len(hr_bins[i]) if hr_bins[i] else None
        inputs = []
        if motion is not None or sound is not None or cry is not None:
            inputs.append("camera")
        if radar is not None or presence is not None:
            inputs.append("sensor")
        if hr is not None:
            inputs.append("pulseox")
        features.append(
            EpochFeatures(
                motion=motion,
                radar_move=radar,
                presence=presence,
                sound=sound,
                cry=cry,
                hr=hr,
                inputs=tuple(inputs),
            )
        )
    return features
