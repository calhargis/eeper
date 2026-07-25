# eeper "live-monitor lite" mode

A stripped deployment for very low-RAM hardware — e.g. a **Raspberry Pi 3 / 1 GB**. It
serves only what a plain baby-cam needs and drops everything heavy:

| Kept                               | Dropped                                                    |
| ---------------------------------- | ---------------------------------------------------------- |
| Login / accounts (change password) | ML insight engine (cry + motion detection)                 |
| Camera live view (WebRTC)          | Sleep/wake fusion, Tonight, Trends                         |
| Room-audio live view (listen-in)   | MQTT sensor / thermal / pulse-ox ingestion + those views   |
|                                    | Segment recorder, event nudges / Web Push                  |
|                                    | The MQTT broker, and the TimescaleDB hypertables/aggregate |

The web app hides the nav for the dropped surfaces automatically (it reads a `lite` flag
from `/api/v1/system/status`), so you just see **Live** (and **Settings** as an admin).

## Bring it up

```sh
# On the eeper host, from deploy/:
EEPER_LITE=1 sudo ./eeper-up.sh
```

That is equivalent to:

```sh
docker compose -f docker-compose.yml -f docker-compose.lite.yml --profile lite up -d --remove-orphans
```

The `lite` profile runs exactly six services — **db, api, web, caddy, go2rtc**, plus your
**camera adapter** — with the `docker-compose.lite.yml` overlay swapping TimescaleDB for a
small, low-memory plain Postgres and setting `EEPER_LITE=true` on the api.

### Camera + audio adapters (deployment override)

The camera/audio adapters live in your local `docker-compose.override.yml`. For lite, tag
them with the `lite` profile and set a low-bitrate preset. On a Pi, prefer the **CSI**
adapter — it uses the Pi's **hardware** H.264 encoder, so encoding costs almost no CPU:

```yaml
services:
  csi-adapter:
    profiles: [video, lite]
    environment:
      WIDTH: '640'
      HEIGHT: '480'
      FPS: '15' # or 10 for even less CPU/bandwidth
      BITRATE: '800000' # 0.8 Mbps
  audio-adapter:
    profiles: [video, lite]
    environment:
      BITRATE: '24000' # 24 kbps Opus — plenty for room monitoring
```

(A USB/UVC camera also works but encodes in software `libx264`, which is the main CPU risk
on a Pi 3 — cap it to `SIZE=640x480`, `FPS=10`. A hardware `h264_v4l2m2m` path for USB is a
possible future addition.) Also set `EEPER_AUDIO_SOURCE_URL=rtsp://audio-adapter:8554/mic`
so the api serves the standalone room-audio stream.

## Important: lite uses its own database

Lite runs plain Postgres on a **separate volume** (`db-data-lite`). A plain-Postgres binary
cannot open a data directory that was initialised by TimescaleDB, so **a host is either
"full" or "lite", not both** — switching a running host between them starts a fresh, empty
database (you'd re-create the admin via first-boot). Pick one mode per host.

## Rough footprint

Full stack (measured on a Pi 4): ~860 MB resident, ~2 of 4 cores busy — the biggest costs
are the ML insight engine, Postgres, and video encoding. Lite drops the insight engine and
the recorder, skips MQTT, and shrinks Postgres, which brings the resident set to roughly
**~450–550 MB** for {caddy + web + go2rtc + api + small-Postgres} plus the camera/audio
adapters (~30–60 MB each while streaming). With the CSI hardware encoder, CPU for a single
reduced-quality feed is light. That fits a 1 GB Pi 3 for a **basic single-camera live
monitor**; the full ML/insight experience still wants a Pi 4/5.
