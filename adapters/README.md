# adapters — edge shim containers

Small, optional containers that convert non-conforming hardware into eeper's
normalization contracts at the edge (or on the server host). They target
*protocols, not devices*, so the server never contains hardware-specific code.

Planned (Phase 1, M1.3):

- **ffmpeg USB adapter** — V4L2 UVC webcam → contract-conformant RTSP/H.264.
- **rpicam CSI adapter** — Raspberry Pi Camera Module (incl. NoIR) → RTSP/H.264.

Each adapter builds as its own multi-arch image and is validated against the
same RTSP contract as native cameras.
