#!/bin/sh
# Build the ffmpeg command and render the mediamtx config onto tmpfs (the rootfs
# is read-only), then hand off to mediamtx. The config is written with a heredoc
# (shell expansion — no sed, so env values with '&', '|' or '\' render verbatim).
# The SAME image runs in production (a real ALSA capture device) and in CI (a
# synthetic sine source) through the identical encode + serve path — only the
# input differs.
set -eu

RATE="${RATE:-48000}"
CHANNELS="${CHANNELS:-1}"
BITRATE="${BITRATE:-48000}"
RTSP_PATH="${RTSP_PATH:-mic}"

if [ -n "${ALSA_DEVICE:-}" ]; then
  # Production / bench: a real ALSA capture device (a USB mic). Use a `plughw:`
  # device so ALSA converts the card's native rate/format to what we ask for —
  # a mic that only offers 44.1/48 kHz still yields the 48 kHz Opus we want.
  input="-f alsa -ac ${CHANNELS} -ar ${RATE} -i ${ALSA_DEVICE}"
else
  # CI / no device: a synthetic sine through the identical encode + serve path,
  # so the hosted runner exercises everything but the literal ALSA device open.
  input="-re -f lavfi -i sine=frequency=440:sample_rate=${RATE}"
fi

# Opus is a first-class WebRTC audio codec, so go2rtc forwards it to the browser
# with no transcode. `-application audio` favours fidelity (a cry / white-noise
# machine) over the speech-tuned `voip` profile; `-vn` guards against any stray
# video track. The OUTPUT `-ac` pins the channel count (a `plughw:` device can
# upmix a mono mic to stereo — pinning it here keeps the stream mono, halving the
# bitrate). The path key MUST match ffmpeg's publish target, or mediamtx refuses
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
    runOnInit: ffmpeg ${input} -vn -ac ${CHANNELS} -c:a libopus -b:a ${BITRATE} -application audio -f rtsp -rtsp_transport tcp rtsp://localhost:8554/${RTSP_PATH}
    runOnInitRestart: yes
EOF

exec mediamtx /tmp/mediamtx.yml
