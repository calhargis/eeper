"""The per-camera ffmpeg segment command."""

from __future__ import annotations

from pathlib import Path

from eeper.recorder.layout import SEG_OUTPUT_PATTERN


def segment_command(rtsp_url: str, out_dir: Path, segment_seconds: int) -> list[str]:
    """Record ``rtsp_url`` into fixed-duration MPEG-TS segments with no re-encode.

    -c copy keeps CPU near zero. MPEG-TS (no trailing moov) means a segment killed
    mid-write stays decodable and every prior segment is independently valid. No
    -reset_timestamps: continuous PTS across segments is what lets a later -c copy
    concat stay DTS-monotonic when a clip crosses segment boundaries.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-segment_format",
        "mpegts",
        "-strftime",
        "1",
        str(out_dir / SEG_OUTPUT_PATTERN),
    ]
