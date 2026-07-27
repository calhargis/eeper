"""Storage targets — where recordings and clips are written.

The app CANNOT discover or mount disks, by design: the api and recorder run with
``cap_drop: ALL`` and ``no-new-privileges``, so ``mount(2)`` is unavailable and ``/dev`` +
``/sys/block`` aren't mounted. Giving them those powers would amount to host root. Instead
the OPERATOR prepares and mounts storage on the host and declares the candidates via
``EEPER_STORAGE_TARGETS``; the app only ever lists them and lets an admin pick one BY ID.

That id is validated against this allow-list on every read, so a stored (or forged) value
can never become an arbitrary filesystem path — the setting names a target, never a path.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# Same shape the API schema enforces, re-checked here so a hand-edited env can't smuggle
# something odd (a path, traversal) in through the back door.
_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

INTERNAL_ID = "internal"
_WRITE_PROBE = ".eeper-write-test"


@dataclass(frozen=True)
class StorageTarget:
    """A declared, already-mounted place eeper may write media."""

    id: str
    label: str
    path: str


@dataclass(frozen=True)
class StorageStatus:
    """A target plus what we could actually observe about it right now."""

    id: str
    label: str
    path: str
    mounted: bool
    writable: bool
    total_bytes: int | None
    free_bytes: int | None
    error: str | None  # not_mounted | not_writable | unreadable


def parse_targets(spec: str, media_root: str) -> list[StorageTarget]:
    """Parse ``id:Label:/path,id2:Label 2:/path2``.

    An empty/unset spec yields exactly one implicit target — the built-in media volume — so
    an unmodified deployment enumerates one option and the feature is inert. Malformed
    entries are skipped with a warning rather than crashing boot: a typo in an operator's
    env must not take the monitor down.
    """
    targets: list[StorageTarget] = [
        StorageTarget(id=INTERNAL_ID, label="Internal storage", path=media_root)
    ]
    seen = {INTERNAL_ID}
    for raw in (part.strip() for part in spec.split(",")):
        if not raw:
            continue
        # Split the path off the right so a label may contain a colon.
        head, _, path = raw.rpartition(":")
        tid, _, label = head.partition(":")
        tid, label, path = tid.strip(), label.strip(), path.strip()
        if not tid or not path:
            _log.warning("ignoring malformed storage target %r (want id:label:/path)", raw)
            continue
        if not _ID_RE.match(tid):
            _log.warning("ignoring storage target with invalid id %r", tid)
            continue
        if not path.startswith("/"):
            _log.warning("ignoring storage target %r: path %r is not absolute", tid, path)
            continue
        if tid in seen:
            # The implicit internal target may be redefined (to relabel or repoint it);
            # anything else duplicated is a mistake worth surfacing.
            if tid == INTERNAL_ID:
                targets[0] = StorageTarget(id=tid, label=label or "Internal storage", path=path)
            else:
                _log.warning("ignoring duplicate storage target id %r", tid)
            continue
        seen.add(tid)
        targets.append(StorageTarget(id=tid, label=label or tid, path=path))
    return targets


def probe(target: StorageTarget) -> StorageStatus:
    """Observe a target without ever raising. A target that has gone away must still be
    LISTED (as not mounted) — that is exactly what tells the user to plug the disk back
    in, so a failed probe degrades this one entry instead of the whole response."""
    path = Path(target.path)

    def unusable(error: str) -> StorageStatus:
        return StorageStatus(
            id=target.id,
            label=target.label,
            path=target.path,
            mounted=False,
            writable=False,
            total_bytes=None,
            free_bytes=None,
            error=error,
        )

    try:
        # os.path.ismount() catches "the operator forgot the bind mount", where the path
        # exists as an empty directory on the container's own filesystem. The built-in root
        # is exempt: it is a Docker volume inside the container, not a mount point.
        if not path.is_dir():
            return unusable("not_mounted")
        mounted = os.path.ismount(path) or target.id == INTERNAL_ID
        usage = shutil.disk_usage(path)  # statvfs: needs no capability, fine on a RO rootfs
    except OSError as exc:
        _log.warning("storage target %s (%s) is unreadable: %s", target.id, target.path, exc)
        return unusable("unreadable")

    # Actually write. A read-only remount, a full disk, and (most often here) a host
    # directory not owned by the container's uid all look fine to stat and only fail when
    # the recorder tries to create its segment dir — so stat alone is not enough evidence.
    probe_file = path / _WRITE_PROBE
    writable = False
    try:
        probe_file.touch()
        writable = True
    except OSError as exc:
        _log.warning("storage target %s (%s) is not writable: %s", target.id, target.path, exc)
    finally:
        with contextlib.suppress(OSError):
            probe_file.unlink()
    return StorageStatus(
        id=target.id,
        label=target.label,
        path=target.path,
        mounted=mounted,
        writable=writable,
        total_bytes=usage.total,
        free_bytes=usage.free,
        error=None if writable else "not_writable",
    )


def resolve_media_root(targets: list[StorageTarget], selected_id: str, fallback: str) -> str:
    """The media root to actually use.

    An unknown id falls back to the built-in root rather than failing: a target can vanish
    when an operator edits the env or unplugs a disk, and a baby monitor must keep recording
    somewhere rather than stop. This is also the choke point that makes the stored id
    incapable of naming an arbitrary path — it is only ever matched against the allow-list.
    """
    for target in targets:
        if target.id == selected_id:
            return target.path
    if selected_id and selected_id != INTERNAL_ID:
        _log.warning("storage target %r is not declared; falling back to %s", selected_id, fallback)
    return fallback
