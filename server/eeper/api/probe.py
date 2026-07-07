"""Probe an RTSP source with ffprobe to validate the stream contract.

Kept small and defensive: the ``-timeout`` input option bounds the RTSP connect
(``-rw_timeout`` does not), ``-select_streams v:0`` avoids reading an audio track
as the video stream, and a hard asyncio timeout + kill is the belt-and-suspenders
bound so a dead camera can never hang the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int
    height: int


class ProbeError(Exception):
    """Base: the source failed the probe."""


class ProbeUnavailable(ProbeError):
    """The source could not be reached/read (unreachable, timeout, ffprobe error)."""


class ProbeRejected(ProbeError):
    """The source was reachable but carries no conformant video stream. Distinct
    from :class:`ProbeUnavailable` so the caller can answer 422 (non-conformant)
    rather than 502 (upstream unreachable)."""


async def probe_video(source_url: str, timeout_seconds: float) -> VideoInfo:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        str(int(timeout_seconds * 1_000_000)),  # microseconds; bounds the connect
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height",
        "-of",
        "json",
        source_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:  # ffprobe missing from the image
        raise ProbeUnavailable("ffprobe is not available") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds + 2)
    except TimeoutError as exc:
        raise ProbeUnavailable("probe timed out") from exc
    finally:
        # Guarantee the child never outlives this probe. communicate() sets
        # returncode on success, so this only fires on timeout OR on task
        # cancellation (e.g. shutdown) — otherwise an unkilled ffprobe would hold
        # the RTSP connection open until it self-times-out.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(BaseException):
                await proc.wait()

    if proc.returncode != 0:
        raise ProbeUnavailable(f"could not read stream: {stderr.decode(errors='replace')[:200]}")

    try:
        streams = json.loads(stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise ProbeUnavailable("unparseable probe output") from exc
    # Reachable but carrying no conformant video stream is a contract rejection
    # (422), not an upstream failure (502).
    if not streams:
        raise ProbeRejected("no video stream found")

    info = streams[0]
    codec = info.get("codec_name")
    width = info.get("width")
    height = info.get("height")
    if not isinstance(codec, str) or not isinstance(width, int) or not isinstance(height, int):
        raise ProbeRejected("incomplete video stream info")
    return VideoInfo(codec=codec, width=width, height=height)
