"""The per-camera ffmpeg video-decode command (gray frame sampler)."""

from __future__ import annotations

from eeper.insight.frame import FrameSpec


def frame_decode_command(rtsp_url: str, spec: FrameSpec) -> list[str]:
    """Decode a camera's video to raw 8-bit gray frames on stdout at ``spec.fps``,
    downscaled to ``spec.width`` x ``spec.height`` (yields exact ``frame_bytes``
    frames). ``-an`` drops audio — the separate audio child owns that stream.
    Fixed arg list, no shell."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-an",
        "-vf",
        f"fps={spec.fps},scale={spec.width}:{spec.height},format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
