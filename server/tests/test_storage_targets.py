"""Storage targets — choosing which disk recordings and clips are written to.

The security-relevant property under test: the stored setting is an ID, and an ID only ever
becomes a path by matching the operator's declared allow-list. eeper cannot enumerate,
mount, or format disks (the api and recorder run ``cap_drop: ALL`` with no ``/dev``), so
anything that let a stored value name an arbitrary path would be the whole attack surface
of this feature.

Pure where it can be: real directories in tmp_path, no Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eeper.api.storage import (
    INTERNAL_ID,
    StorageTarget,
    parse_targets,
    probe,
    resolve_media_root,
)
from tests.conftest import Harness

ADMIN = {"username": "storadmin", "password": "correct horse battery staple"}


# ── parsing the operator's declaration ───────────────────────────────────────


def test_unset_spec_yields_only_the_internal_target() -> None:
    """An untouched deployment must offer exactly one choice, so the feature is inert."""
    targets = parse_targets("", "/media")
    assert [t.id for t in targets] == [INTERNAL_ID]
    assert targets[0].path == "/media"


def test_parses_declared_targets_and_keeps_internal_first() -> None:
    targets = parse_targets("ssd:External SSD:/storage/ssd", "/media")
    assert [(t.id, t.label, t.path) for t in targets] == [
        (INTERNAL_ID, "Internal storage", "/media"),
        ("ssd", "External SSD", "/storage/ssd"),
    ]


def test_label_may_contain_a_colon() -> None:
    """The path is split off the right, so a label like "SSD: the big one" survives."""
    (_, ssd) = parse_targets("ssd:SSD: the big one:/storage/ssd", "/media")
    assert ssd.label == "SSD: the big one"
    assert ssd.path == "/storage/ssd"


@pytest.mark.parametrize(
    "spec",
    [
        "no-colons-at-all",  # missing label + path
        ":Label:/storage/x",  # no id
        "ssd:Label:relative/path",  # not absolute
        "../evil:Label:/storage/x",  # id that isn't an id
        "SSD:Label:/storage/x",  # uppercase (the id shape is fixed)
    ],
)
def test_malformed_entries_are_skipped_not_fatal(spec: str) -> None:
    """A typo in the operator's env must not take the baby monitor down — the entry is
    dropped with a warning and everything else still works."""
    targets = parse_targets(spec, "/media")
    assert [t.id for t in targets] == [INTERNAL_ID]


def test_internal_may_be_relabelled_but_others_may_not_be_duplicated() -> None:
    targets = parse_targets("internal:SD card:/media,ssd:A:/a,ssd:B:/b", "/media")
    assert targets[0].label == "SD card"
    assert [t.id for t in targets] == [INTERNAL_ID, "ssd"]
    assert targets[1].path == "/a", "the first declaration of an id wins"


# ── resolving an id to a path (the choke point) ──────────────────────────────


def test_resolve_matches_the_allow_list() -> None:
    targets = parse_targets("ssd:External SSD:/storage/ssd", "/media")
    assert resolve_media_root(targets, "ssd", "/media") == "/storage/ssd"
    assert resolve_media_root(targets, INTERNAL_ID, "/media") == "/media"


def test_resolve_falls_back_when_the_target_is_gone() -> None:
    """An operator can remove a target from the env while a household still has it
    selected. Recording must continue somewhere rather than stop."""
    targets = parse_targets("", "/media")
    assert resolve_media_root(targets, "ssd", "/media") == "/media"


@pytest.mark.parametrize("hostile", ["/etc", "../../etc/shadow", "/storage/ssd", ""])
def test_a_stored_value_can_never_name_an_arbitrary_path(hostile: str) -> None:
    """Even if a row is edited directly in the database, the id is only ever compared
    against declared targets — it is never joined onto or used as a path."""
    targets = parse_targets("ssd:External SSD:/storage/ssd", "/media")
    assert resolve_media_root(targets, hostile, "/media") == "/media"


# ── probing what's actually there ────────────────────────────────────────────


def test_probe_reports_a_usable_directory(tmp_path: Path) -> None:
    status = probe(StorageTarget(id="ssd", label="External SSD", path=str(tmp_path)))
    assert status.writable is True
    assert status.error is None
    assert status.total_bytes and status.free_bytes


def test_probe_leaves_nothing_behind(tmp_path: Path) -> None:
    probe(StorageTarget(id="ssd", label="External SSD", path=str(tmp_path)))
    assert list(tmp_path.iterdir()) == [], "the write test must clean up after itself"


def test_probe_reports_a_missing_target_instead_of_raising(tmp_path: Path) -> None:
    """An unplugged disk must still be LISTED — "not mounted" is the message that tells
    someone to plug it back in."""
    status = probe(StorageTarget(id="ssd", label="External SSD", path=str(tmp_path / "gone")))
    assert status.mounted is False
    assert status.writable is False
    assert status.error == "not_mounted"


def test_probe_reports_an_unwritable_target(tmp_path: Path) -> None:
    """The common real failure: the host directory isn't owned by the container's uid, so
    it stats fine and only fails when the recorder creates its segment dir."""
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        status = probe(StorageTarget(id="ssd", label="External SSD", path=str(ro)))
    finally:
        ro.chmod(0o700)
    assert status.writable is False
    assert status.error == "not_writable"
    assert status.total_bytes, "space is still reported — only writing failed"


def test_probe_flags_a_declared_target_that_is_not_a_mount_point(tmp_path: Path) -> None:
    """Catches the operator who declared the target but forgot the bind mount: the path
    exists as an empty dir inside the container, and writes silently land on the SD card."""
    plain = tmp_path / "not-a-mount"
    plain.mkdir()
    status = probe(StorageTarget(id="ssd", label="External SSD", path=str(plain)))
    assert status.mounted is False
    assert status.writable is True, "writable but not a mount — the UI warns on this pair"


def test_internal_is_mounted_even_though_it_is_not_a_mount_point(tmp_path: Path) -> None:
    """The built-in media volume is a Docker volume inside the container, so ismount() is
    False for it — it must not be reported as a missing disk."""
    assert probe(StorageTarget(id=INTERNAL_ID, label="Internal", path=str(tmp_path))).mounted


# ── the API ──────────────────────────────────────────────────────────────────


async def _sign_in_admin(api: Harness) -> None:
    r = await api.client.post("/api/v1/system/first-boot", json=ADMIN)
    assert r.status_code == 201, r.text


async def test_lists_only_internal_by_default(api: Harness) -> None:
    await _sign_in_admin(api)
    body = (await api.client.get("/api/v1/recording/storage-targets")).json()
    assert body["selected_id"] == INTERNAL_ID
    assert [t["id"] for t in body["targets"]] == [INTERNAL_ID]


async def test_lists_declared_targets_with_free_space(api: Harness, tmp_path: Path) -> None:
    api.settings.storage_targets = f"ssd:External SSD:{tmp_path}"
    await _sign_in_admin(api)
    body = (await api.client.get("/api/v1/recording/storage-targets")).json()
    ssd = next(t for t in body["targets"] if t["id"] == "ssd")
    assert ssd["label"] == "External SSD"
    assert ssd["writable"] is True
    assert ssd["free_bytes"] > 0


async def test_admin_can_select_a_declared_target(api: Harness, tmp_path: Path) -> None:
    api.settings.storage_targets = f"ssd:External SSD:{tmp_path}"
    await _sign_in_admin(api)
    r = await api.client.patch("/api/v1/recording/settings", json={"storage_target_id": "ssd"})
    assert r.status_code == 200, r.text
    assert r.json()["storage_target_id"] == "ssd"
    listing = (await api.client.get("/api/v1/recording/storage-targets")).json()
    assert listing["selected_id"] == "ssd"


async def test_rejects_an_undeclared_target(api: Harness) -> None:
    """Storing an id this deployment doesn't declare would read back as accepted while
    recordings kept landing on the old disk. Reject it instead."""
    await _sign_in_admin(api)
    r = await api.client.patch("/api/v1/recording/settings", json={"storage_target_id": "ssd"})
    assert r.status_code == 422, r.text


async def test_selection_drives_the_effective_media_root(api: Harness, tmp_path: Path) -> None:
    """The end of the chain: what the recorder and clip promotion actually use."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from eeper.api.recording_settings import read_media_root

    api.settings.storage_targets = f"ssd:External SSD:{tmp_path}"
    await _sign_in_admin(api)
    await api.client.patch("/api/v1/recording/settings", json={"storage_target_id": "ssd"})

    engine = create_async_engine(api.settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            assert await read_media_root(s, api.settings) == str(tmp_path)
            # Now the operator removes the target from the env — recording must fall back
            # to the built-in root rather than stop.
            api.settings.storage_targets = ""
            assert await read_media_root(s, api.settings) == api.settings.media_root
    finally:
        await engine.dispose()


async def test_viewers_can_read_but_not_select(api: Harness, tmp_path: Path) -> None:
    api.settings.storage_targets = f"ssd:External SSD:{tmp_path}"
    await _sign_in_admin(api)
    r = await api.client.post(
        "/api/v1/users", json={"username": "viewer2", "password": "x" * 12, "role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text
    async with api.fresh() as viewer:
        login = {"username": "viewer2", "password": "x" * 12}
        assert (await viewer.post("/api/v1/auth/login", json=login)).status_code == 200
        assert (await viewer.get("/api/v1/recording/storage-targets")).status_code == 200
        r = await viewer.patch("/api/v1/recording/settings", json={"storage_target_id": "ssd"})
        assert r.status_code == 403, r.text


# ── the recorder follows the selection ───────────────────────────────────────


async def test_recorder_restarts_its_children_when_the_target_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each ffmpeg child holds an open output file under the OLD root and cannot be
    redirected in place, so a target change has to stop and respawn them. Without this the
    setting would look applied while every segment kept landing on the old disk."""
    from eeper.api.config import Settings
    from eeper.recorder.supervisor import RecorderSupervisor

    class _FakeProc:
        returncode = None

    settings = Settings(database_url="postgresql+asyncpg://x/x", secret_key="x" * 16)
    sup = RecorderSupervisor(sessionmaker=None, settings=settings)  # type: ignore[arg-type]
    desired_root = "/media"
    desired_ids = {1, 2}
    spawned: list[tuple[int, str]] = []
    stopped: list[int] = []

    async def fake_desired() -> tuple[str, set[int]]:
        return desired_root, set(desired_ids)

    async def fake_spawn(camera_id: int, media_root: str) -> None:
        spawned.append((camera_id, media_root))
        sup._children[camera_id] = _FakeProc()  # type: ignore[assignment]

    async def fake_stop(camera_id: int) -> None:
        stopped.append(camera_id)
        sup._children.pop(camera_id, None)

    monkeypatch.setattr(sup, "_desired", fake_desired)
    monkeypatch.setattr(sup, "_spawn", fake_spawn)
    monkeypatch.setattr(sup, "_stop_child", fake_stop)

    await sup.reconcile()
    assert sorted(spawned) == [(1, "/media"), (2, "/media")]

    # A steady tick must not churn the children.
    spawned.clear()
    await sup.reconcile()
    assert spawned == [] and stopped == []

    # Now an admin picks the SSD.
    desired_root = "/storage/ssd"
    await sup.reconcile()
    assert sorted(stopped) == [1, 2], "children on the old root must be torn down"
    assert sorted(spawned) == [(1, "/storage/ssd"), (2, "/storage/ssd")]
