# Hardware guide

eeper runs on any amd64 or arm64 Linux host with Docker. This guide lists the **reference
profile** (what the performance gate targets) and sensible alternatives — you do not need
a Pi.

## Reference profile (the performance target)

This is the profile the [bench gate](./performance.md) must pass; it is a good "known-good"
build, not a minimum.

| Part          | Reference choice                     | Notes                                              |
| ------------- | ------------------------------------ | -------------------------------------------------- |
| Compute       | Raspberry Pi 5, 4 GB                 | arm64; a Pi 4 4 GB also works (see below)          |
| Storage       | 32 GB+ A2 microSD or a USB SSD       | SSD strongly preferred for the recording buffer    |
| Camera        | 1080p RTSP camera (or a USB/CSI cam) | H.264; RTSP is the first-class contract            |
| Microphone    | USB mic or the camera's audio        | for sound / cry insights                           |
| Sensors (opt) | 2× ESP32 nodes (mmWave + PIR)        | see [firmware/](../firmware/README.md)             |
| Power         | Official 5 V / 5 A USB-C supply      | undervoltage throttles the CPU and skews the bench |

A full worked build is in
[reference-builds/pi4-all-in-one.md](./reference-builds/pi4-all-in-one.md).

## Alternatives (no Pi required)

| Host                 | Works? | Notes                                                                 |
| -------------------- | ------ | --------------------------------------------------------------------- |
| NAS (Synology/QNAP)  | ✅     | Run the Compose stack in the NAS's Docker/Container Manager.          |
| Old laptop / mini-PC | ✅     | amd64; typically far more headroom than the reference Pi.             |
| x86 mini server      | ✅     | Ideal for many cameras.                                               |
| Raspberry Pi 4 4 GB  | ✅     | Meets the CPU budget for the reference profile; Pi 5 has more margin. |
| Pi Zero / 1 GB SBCs  | ⚠️     | Below the reference profile; may not hold the CPU budget under load.  |

The install flow is identical everywhere — see [install.md](./install.md). The two
`[MANUAL]` cold-start testers for v1.0 deliberately install on **non-Pi** hardware (a NAS
or laptop) to keep the docs honest.

## Cameras

- **RTSP** is the first-class contract. Most IP cameras and NVRs expose an
  `rtsp://…` stream; point eeper at it.
- **Phone as a camera** — see [adapters/phone-rtsp.md](./adapters/phone-rtsp.md).
- **USB / CSI cameras** — bridged to RTSP by the adapters in [adapters/](../adapters/README.md).

Prefer a camera that streams **H.264**; it is the codec the recorder and WebRTC path are
tuned for.

## Sensors (optional)

mmWave presence + PIR motion nodes sharpen sleep/wake fusion but are entirely optional —
eeper works camera-only. Build them from the [reference firmware](../firmware/README.md)
(ESPHome configs + a MicroPython template + the optional MAX3010x pulse-ox node).

## Storage & retention

The recording ring buffer is bounded by a byte quota (default 10 GiB) with optional age
policies; see [operations/backup-restore.md](./operations/backup-restore.md) for backups
and the retention knobs in `deploy/docker-compose.yml`. Use an SSD if you record
continuously — sustained writes wear a microSD quickly.
