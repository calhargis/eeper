"""The per-camera ffmpeg audio-decode command."""

from __future__ import annotations

from eeper.insight.window import WindowSpec


def decode_command(rtsp_url: str, spec: WindowSpec) -> list[str]:
    """Decode a camera's audio to raw 16 kHz mono s16le PCM on stdout (no video,
    no re-encode of video — audio only). Fixed arg list, no shell."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-vn",
        "-ac",
        str(spec.channels),
        "-ar",
        str(spec.sample_rate),
        "-f",
        "s16le",
        "-",
    ]
