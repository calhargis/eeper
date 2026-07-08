#!/bin/sh
# Build the ffmpeg command and render the mediamtx config onto tmpfs (the rootfs
# is read-only), then hand off to mediamtx. The config is written with a heredoc
# (shell expansion — no sed, so env values with '&', '|' or '\' render verbatim).
# The SAME image runs in production (a real V4L2 device) and in CI (a synthetic
# lavfi source) through the identical encode + serve path — only the input differs.
set -eu

FPS="${FPS:-15}"
SIZE="${SIZE:-1280x720}"
RTSP_PATH="${RTSP_PATH:-cam}"

if [ -n "${V4L2_DEVICE:-}" ]; then
  # Production / bench: a real UVC webcam. MJPEG input by default — raw YUYV is
  # USB-bandwidth-starved above ~10 fps at 720p+. Override with V4L2_INPUT_FORMAT.
  input="-f v4l2 -input_format ${V4L2_INPUT_FORMAT:-mjpeg} -framerate ${FPS} -video_size ${SIZE} -i ${V4L2_DEVICE}"
else
  # CI / no device: a synthetic source through the identical encode + serve path.
  input="-re -f lavfi -i testsrc2=size=${SIZE}:rate=${FPS}"
fi

# The path key MUST match ffmpeg's publish target (RTSP_PATH), or mediamtx refuses
# the publish and serves nothing.
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
  ${RTSP_PATH}:
    runOnInit: ffmpeg ${input} -c:v libx264 -profile:v baseline -pix_fmt yuv420p -preset ultrafast -tune zerolatency -g ${FPS} -f rtsp -rtsp_transport tcp rtsp://localhost:8554/${RTSP_PATH}
    runOnInitRestart: yes
EOF

exec mediamtx /tmp/mediamtx.yml
