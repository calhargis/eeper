# adapters — edge shim containers

Small, optional containers that convert non-conforming hardware into eeper's
normalization contract at the edge (or on the server host). They target
*protocols, not devices*, so the server never contains hardware-specific code.
Each adapter embeds a static [mediamtx](https://github.com/bluenviron/mediamtx)
RTSP listener and an encoder, and serves one contract-conformant stream at
`rtsp://<adapter>:8554/cam` (H.264 baseline, ≤1080p, ~1s GOP) that the gateway
pulls like any native camera.

## `usb/` — ffmpeg USB (V4L2) adapter (amd64 + arm64)

A UVC webcam (`/dev/videoN`) → H.264 RTSP. With no `V4L2_DEVICE` set it uses a
synthetic `lavfi` input through the identical encode + serve path (this is how CI
exercises it — hosted runners can't open a real V4L2 device; see
[docs/ci.md](../docs/ci.md)).

| Env                 | Default    | Meaning                                              |
| ------------------- | ---------- | ---------------------------------------------------- |
| `V4L2_DEVICE`       | _(unset)_  | Capture device (e.g. `/dev/video0`). Unset → lavfi.  |
| `SIZE`              | `1280x720` | Capture/encode resolution (≤1080p).                  |
| `FPS`               | `15`       | Frame rate (also the GOP / keyframe interval).       |
| `V4L2_INPUT_FORMAT` | `mjpeg`    | UVC input format (raw YUYV starves USB above ~10fps).|
| `RTSP_PATH`         | `cam`      | Served path (`rtsp://…:8554/<path>`).                |

**Device access (production/bench)** — minimum privilege, still non-root +
read-only + `cap_drop: ALL`. Grant just the one device and the host `video` group:

```yaml
usb-adapter:
  image: ghcr.io/calhargis/eeper/usb:latest
  environment: { V4L2_DEVICE: /dev/video0 }
  devices: ['/dev/video0']
  group_add: ['${EEPER_VIDEO_GID:-44}'] # host `video` gid varies — check `getent group video`
  read_only: true
  security_opt: [no-new-privileges:true]
  cap_drop: [ALL]
  tmpfs: [/tmp]
```

No `--privileged`, no added capabilities.

## `csi/` — rpicam CSI adapter (arm64 / Raspberry Pi only)

The Raspberry Pi Camera Module (incl. NoIR) → H.264 RTSP via mediamtx's native
`rpiCamera` source (libcamera). Built arm64-only. Capture is validated on the
**[MANUAL] Pi bench**, not in CI (there is no Pi in CI; CI only proves the arm64
image builds and is CRITICAL-clean).

| Env               | Default      | Meaning                           |
| ----------------- | ------------ | --------------------------------- |
| `WIDTH` / `HEIGHT`| `1280`/`720` | Capture resolution (≤1080p).      |
| `FPS`             | `15`         | Frame rate (also the IDR period). |
| `BITRATE`         | `3000000`    | H.264 bitrate (bps).              |
| `HFLIP` / `VFLIP` | `false`      | Flip the image.                   |

**Device access (bench, Pi only)** — libcamera's buffer allocation needs the Pi's
DMA/udev surfaces, which a non-root container can't reach cleanly, so the bench
runs it **`privileged`** — a deliberate, documented, bench-scoped relaxation
(mediamtx's own Pi guide requires it):

```yaml
csi-adapter:
  image: ghcr.io/calhargis/eeper/csi:latest
  privileged: true
  tmpfs: ['/dev/shm:exec']
  volumes: ['/run/udev:/run/udev:ro']
```

The image installs Debian's `libcamera`; a Raspberry Pi OS deployment may need the
Pi-tuned libcamera from `archive.raspberrypi.com` for the Pi ISP pipeline —
adjust on the bench if the camera isn't detected.

## `audio/` — ffmpeg ALSA microphone adapter (amd64 + arm64)

A USB/ALSA capture device (`hw:CARD=…`) → Opus RTSP. With no `ALSA_DEVICE` set it
uses a synthetic `sine` `lavfi` input through the identical encode + serve path
(this is how CI exercises it — hosted runners have no capture device). The served
stream carries **only an audio track**, so it never registers as a camera; instead
the api merges it into a camera's go2rtc stream (`EEPER_AUDIO_SOURCE_URL`) to light up
listen-in + the sustained-sound nudge, and re-serves it standalone as `mic` for a
camera-independent "listen to the room".

| Env          | Default | Meaning                                             |
| ------------ | ------- | --------------------------------------------------- |
| `ALSA_DEVICE`| _(unset)_ | Capture device (e.g. `plughw:CARD=microphone,DEV=0`). Unset → synthetic sine. |
| `RATE`       | `48000` | Capture/encode sample rate (Opus is 48 kHz-native).  |
| `CHANNELS`   | `1`     | Capture channels (a nursery mic is mono).            |
| `BITRATE`    | `48000` | Opus bitrate (bps) — ample for voice + ambient.      |
| `RTSP_PATH`  | `mic`   | Served path (`rtsp://…:8554/<path>`).                |

**Device access (production/bench)** — still non-root + read-only + `cap_drop:
ALL`. ALSA nodes are `root:audio` (mode 0660), so grant just `/dev/snd` and the
host `audio` group; prefer `CARD=<id>` over a card index (indices renumber across
reboots). Check the id with `cat /proc/asound/cards` and the gid with `getent
group audio`:

```yaml
audio-adapter:
  image: ghcr.io/calhargis/eeper/audio:latest
  environment: { ALSA_DEVICE: plughw:CARD=microphone,DEV=0 }
  devices: ['/dev/snd']
  group_add: ['${EEPER_AUDIO_GID:-29}'] # host `audio` gid varies — check `getent group audio`
  read_only: true
  security_opt: [no-new-privileges:true]
  cap_drop: [ALL]
  tmpfs: [/tmp]
```

No `--privileged`, no added capabilities.

## Phones

An old phone running an RTSP-camera app needs no adapter — eeper pulls its
`rtsp://` stream directly. See
[docs/adapters/phone-rtsp.md](../docs/adapters/phone-rtsp.md).
