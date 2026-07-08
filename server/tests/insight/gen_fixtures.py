"""Deterministic motion fixtures for the C1 ordering test.

Synthesizes gray frame sequences in pure Python — a white block on a mid-gray
field, moving a fixed number of pixels per frame (ping-pong so it stays on
screen). Displacement + block size are graded so the normalized frame-diff score
ranks still < rolling < sitting_up with clear margin, independent of ffmpeg. The
generator is intentionally ignorant of the scoring formula, so the ordering test
is not circular. Measured scores: still 0.000, rolling ~0.012, sitting_up ~0.052.
"""

from __future__ import annotations

from eeper.insight.frame import FRAME_SPEC, FrameSpec

_BG = 128  # mid-gray background
_BOX = 255  # white block
_BOX_TOP = 40  # y of the block's top edge

# kind -> (pixels moved per frame, block width, block height)
FIXTURES: dict[str, tuple[int, int, int]] = {
    "still": (0, 40, 40),
    "rolling": (6, 40, 40),
    "sitting_up": (18, 44, 60),
}


def synth_sequence(
    displacement: int, box_w: int, box_h: int, nframes: int = 25, spec: FrameSpec = FRAME_SPEC
) -> list[bytes]:
    w, h, frame_bytes = spec.width, spec.height, spec.frame_bytes
    span = max(1, w - box_w)
    frames: list[bytes] = []
    for i in range(nframes):
        buf = bytearray([_BG]) * frame_bytes
        # ping-pong the block's left edge within [0, span] so it never leaves frame
        pos = int(abs(((i * displacement) % (2 * span)) - span))
        for yy in range(_BOX_TOP, min(_BOX_TOP + box_h, h)):
            row = yy * w
            for xx in range(pos, min(pos + box_w, w)):
                buf[row + xx] = _BOX
        frames.append(bytes(buf))
    return frames


def fixture(kind: str) -> list[bytes]:
    return synth_sequence(*FIXTURES[kind])
