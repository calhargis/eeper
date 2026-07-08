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
EOF

exec mediamtx /tmp/mediamtx.yml
