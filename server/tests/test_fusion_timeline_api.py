"""GET /fusion/timeline (M3.3 slice 3): the Tonight timeline reads fused-state segments
+ sleep sessions derived from the durable fused_states log.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eeper.api.models import FusedState

_PW = "correct horse battery staple"


async def _admin(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.client.post(
        "/api/v1/system/first-boot", json={"username": "admin", "password": _PW}
    )
    assert r.status_code == 201, r.text


async def _seed(postgres_url: str, rows: list[FusedState]) -> None:
    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add_all(rows)
        await s.commit()
    await engine.dispose()


async def test_timeline_segments_and_sessions(api, postgres_url: str) -> None:  # type: ignore[no-untyped-def]
    await _admin(api)
    now = api.clock["now"]

    def fs(minutes_ago: int, sleep: str, arousal: str = "calm") -> FusedState:
        return FusedState(
            ts=now - timedelta(minutes=minutes_ago),
            household_id="default",
            sleep=sleep,
            arousal=arousal,
            activity=0.5,
            confidence=0.6,
            contributing_inputs="sensor",
        )

    # awake → asleep → a 15-min distressed wake → asleep (still asleep now).
    await _seed(
        postgres_url,
        [
            fs(170, "wake"),
            fs(160, "sleep"),
            fs(60, "wake", "distressed"),
            fs(45, "sleep"),
        ],
    )

    r = await api.client.get("/api/v1/fusion/timeline")
    assert r.status_code == 200, r.text
    body = r.json()

    # Segments cover both states and surface the distressed span; the last is open.
    assert {seg["sleep"] for seg in body["segments"]} == {"sleep", "wake"}
    assert any(seg["arousal"] == "distressed" for seg in body["segments"])
    assert body["segments"][-1]["is_open"] is True
    assert body["segments"][-1]["sleep"] == "sleep"

    # The 15-min wake (> the 10-min consolidation break) splits into two sessions; the
    # night is still in progress so the last one is open.
    assert len(body["sessions"]) == 2
    assert body["sessions"][0]["ended_at"] is not None
    assert body["sessions"][-1]["ended_at"] is None


async def test_timeline_empty_is_one_open_wake_segment(api, postgres_url: str) -> None:  # type: ignore[no-untyped-def]
    await _admin(api)
    r = await api.client.get("/api/v1/fusion/timeline")
    assert r.status_code == 200
    body = r.json()
    assert len(body["segments"]) == 1
    assert body["segments"][0] == {
        **body["segments"][0],
        "sleep": "wake",
        "arousal": "calm",
        "is_open": True,
    }
    assert body["sessions"] == []


async def test_timeline_requires_auth(api) -> None:  # type: ignore[no-untyped-def]
    async with api.fresh() as anon:
        r = await anon.get("/api/v1/fusion/timeline")
    assert r.status_code == 401
