#!/bin/sh
# Render the rpiCamera config onto tmpfs (read-only rootfs) with a heredoc, then
# hand off to mediamtx. Contract defaults: 720p15, baseline, IDR = fps. Capture is
# Pi-only — validated on the [MANUAL] bench.
set -eu

WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-15}"
BITRATE="${BITRATE:-3000000}"
HFLIP="${HFLIP:-false}"
VFLIP="${VFLIP:-false}"
# On-demand: the camera captures and encodes only while something is reading the stream.
# Default on. Readers (the recorder, the insight engine, a viewer) keep it running whenever
# the crib is being watched; with presence gating, nothing reads an empty crib, so the
# sensor and the hardware encoder go idle instead of encoding 1080p to nobody.
ON_DEMAND="${ON_DEMAND:-yes}"
# Focus, for cameras that have a motor (Camera Module 3 / imx708). Unset = mediamtx's own
# default, which on this sensor leaves the lens at its power-on rest position — near macro,
# so the picture is unusable after every boot. For a fixed nursery mount set AF_MODE=manual
# and LENS_POSITION to a calibrated value (dioptres: 0 = infinity, 0.5 = 2 m, 1 = 1 m);
# continuous AF hunts in low light, which is exactly when a monitor matters. See README.
AF_MODE="${AF_MODE:-}"
LENS_POSITION="${LENS_POSITION:-}"

cat > /tmp/mediamtx.yml <<EOF
logLevel: info
rtspAddress: :8554
rtmp: no
hls: no
webrtc: no
srt: no
api: no
metrics: no
pprof: no
playback: no
paths:
  cam:
    source: rpiCamera
    rpiCameraWidth: ${WIDTH}
    rpiCameraHeight: ${HEIGHT}
    rpiCameraFPS: ${FPS}
    rpiCameraCodec: hardwareH264
    rpiCameraH264Profile: baseline
    rpiCameraH264Level: '4.1'
    rpiCameraIDRPeriod: ${FPS}
    rpiCameraBitrate: ${BITRATE}
    rpiCameraHFlip: ${HFLIP}
    rpiCameraVFlip: ${VFLIP}
    sourceOnDemand: ${ON_DEMAND}
    sourceOnDemandStartTimeout: 10s
    sourceOnDemandCloseAfter: 10s
EOF

# Focus settings are only written when asked for: a camera without a focus motor (Module 2,
# the HQ camera) has nothing for them to drive, and an unknown control should not be sent.
if [ -n "${AF_MODE}" ]; then
  echo "    rpiCameraAfMode: ${AF_MODE}" >> /tmp/mediamtx.yml
fi
if [ -n "${LENS_POSITION}" ]; then
  echo "    rpiCameraLensPosition: ${LENS_POSITION}" >> /tmp/mediamtx.yml
fi

exec mediamtx /tmp/mediamtx.yml
