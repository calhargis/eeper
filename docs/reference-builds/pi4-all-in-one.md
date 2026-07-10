# Reference Build: Pi 4 All-in-One (Three-Input Node + Server)

**Status:** draft — being assembled and validated by a project maintainer
**Role:** single-box eeper deployment: one Raspberry Pi 4 captures video, thermal, and audio _and_ runs the full server stack (media gateway, insight engine, database, API, PWA), with remote access via Tailscale/WireGuard.
**Doc home:** `docs/reference-builds/pi4-all-in-one.md` (seed for the M5.2 sample-hardware guide; this build is also a candidate second bench profile alongside the Pi 5 reference.)

> **Scope note:** thermal (MLX90640) is a post-v1 candidate input — it is not part of the v1 milestones. This build includes it for experimentation; everything else maps to planned v1 functionality. See "Software mapping" below.

---

## 1. What this build demonstrates

- The **adapter ingestion path**: a CSI camera turned into a contract-conformant RTSP stream (M1.3), rather than a native RTSP camera.
- **Graceful multi-input fusion** on one device: optical video, audio, and a thermal presence grid feeding the insight engine.
- The performance philosophy: the entire v1 stack inside a 4 GB Pi 4's budget, enabled by hardware H.264 encode and decimated inference.

## 2. Bill of materials

Owner-supplied base (already on hand):

| Part                            | Notes                                                                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Raspberry Pi 4, **4 GB** (2020) | 4 GB is the comfortable minimum for the full single-box stack. 2 GB is tight; 1 GB should run capture-only and point at a server elsewhere. |
| Fan case                        | Required for sustained encode + inference; a bare Pi 4 will thermal-throttle.                                                               |
| 32 GB microSD                   | Retained as boot device or recovery spare only — see Storage.                                                                               |

To purchase (~$150–200 total):

| #   | Part                                                               | Purpose                                        | Est.   | Source / exact terms                                                                                                                    |
| --- | ------------------------------------------------------------------ | ---------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Raspberry Pi **Camera Module 3 NoIR**, standard FOV                | Night-capable main video                       | ~$25   | raspberrypi.com/products/camera-module-3 → NoIR variant, via listed resellers. Includes Pi-4-compatible ribbon.                         |
| 2   | **850 nm IR illuminator**                                          | Invisible night lighting                       | $13–25 | Search "850nm IR illuminator CCTV" (e.g., Tendelux AI4/DI20). Must be 850 nm, not 940 nm. Own wall adapter — never powered from the Pi. |
| 3   | **MLX90640 breakout, 55° FOV**                                     | Thermal 32×24 array (post-v1 input)            | ~$75   | adafruit.com/product/4407. 55° concentrates pixels on a crib at 1–1.5 m; 110° (product/4469) only for whole-room coverage.              |
| 4   | STEMMA QT → female jumper cable                                    | Solder-free I²C hookup                         | ~$2    | Adafruit accessory on the MLX90640 page.                                                                                                |
| 5   | **USB microphone**, omnidirectional, class-compliant               | Audio input                                    | $10–20 | Search "mini USB omnidirectional microphone conference"; boundary-style conference mics suit far-field pickup.                          |
| 6   | **240–256 GB SSD** (SATA + USB 3.0 enclosure, or portable USB SSD) | Working disk for OS data, Postgres, recordings | $30–45 | e.g., Kingston A400 240 GB + UGREEN USB 3.0 enclosure.                                                                                  |
| 7   | Official **Pi 4 PSU 5.1 V / 3 A USB-C** _(verify first)_           | Stable power under load                        | ~$10   | Only if current PSU is not the official 3 A unit. Check: `vcgencmd get_throttled` must return `0x0`.                                    |

Optional: rigid camera mount / mini tripod / printed bracket (~$10) so camera + thermal + IR aim as one unit; used Wyze Cam v3 (~$25) to exercise the native-RTSP path (see `wyze-v3-thingino` reference build, incl. the T31AL secure-boot variant caveat).

## 3. Wiring & interfaces

No interface conflicts — each input has its own bus:

| Input                    | Interface | Pins / port                                                                                   |
| ------------------------ | --------- | --------------------------------------------------------------------------------------------- |
| Camera Module 3 NoIR     | CSI       | Ribbon into the CSI connector (contacts facing correctly; blue tab toward USB ports on Pi 4). |
| MLX90640                 | I²C       | GPIO 2 (SDA), GPIO 3 (SCL), 3V3, GND — via STEMMA QT jumpers.                                 |
| USB mic                  | USB-A     | Any port.                                                                                     |
| SSD                      | USB 3.0   | Blue USB 3 port.                                                                              |
| (future) INMP441 I²S mic | I²S       | GPIO 18–21 — does not overlap I²C; coexists with the MLX90640.                                |

Assembly notes:

- **IR illuminator placement:** a foot or more away from the lens, not beside it, to avoid washout from near-surface reflections. Separate power.
- **MLX90640 refresh:** default I²C speed supports ~8 Hz, which exceeds our needs (presence/trends want 2–4 Hz). Refresh above 8 Hz requires raising the I²C baudrate in `config.txt`; not recommended here.
- **Mounting geometry:** camera + mic within ~1.5 m of the crib. Cry detection is validated near-field; this is a performance requirement, not a suggestion (see M2.3/M2.5 history). Aim the 55° thermal FOV to frame the crib.

## 4. Storage layout

Continuous recording + Postgres will destroy a microSD card. Two supported layouts:

1. **SSD-boot (preferred):** flash Raspberry Pi OS Lite (64-bit) to the SSD, boot from USB (Pi 4 supports native USB boot; update bootloader EEPROM if pre-2021). microSD retired to recovery spare.
2. **Split:** microSD boots the OS; the SSD mounts at `/data` and carries Postgres volumes, media segments, and Docker's data-root.

In both layouts, **nothing write-heavy lives on the card.** The 2020-vintage 32 GB card in this build is treated as expendable.

## 5. Software mapping

| Function                            | Component                                                                                          | Milestone                 |
| ----------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------- |
| CSI camera → RTSP/H.264             | `rpicam` adapter container (hardware H.264 encode — the Pi 4 retains the encoder the Pi 5 dropped) | M1.3                      |
| Stream fan-out, WebRTC live view    | go2rtc (`video` profile)                                                                           | M1.1–M1.2                 |
| Audio capture                       | mic via adapter into stream / ALSA capture                                                         | M2.1                      |
| Cry detection, motion score, states | insight-engine (`video`, `audio` profiles)                                                         | M2.2–M2.3, M3.3           |
| Thermal grid → MQTT                 | small publisher service → `eeper/{node}/thermal` (post-v1; rides the standard sensor contract)     | post-v1                   |
| Server stack                        | core profile: caddy, api, timescaledb, web                                                         | M0.x                      |
| Remote access                       | Tailscale or WireGuard on the host                                                                 | Section 12 of master plan |

## 6. Bring-up checklist

1. PSU check: `vcgencmd get_throttled` → `0x0` under load. Non-zero = replace supply before debugging anything else.
2. Camera check: `rpicam-hello --list-cameras` shows the Module 3; capture a night frame with IR on and confirm exposure.
3. I²C check: `i2cdetect -y 1` shows the MLX90640 at `0x33`; read one frame with the reference script.
4. Mic check: `arecord -l` lists the device; record 10 s and confirm level.
5. Storage check: confirm boot/data layout per Section 4; run `fio` or `dd` sanity write to the SSD.
6. Stack: `docker compose --profile core --profile video --profile audio up -d`; complete first-boot wizard.
7. Contract validation: registered adapter stream passes the same checks as a native camera (H.264, ≤1080p, audio track present in go2rtc).
8. Thermal (optional): publisher container up; readings visible on the MQTT topic and in device health.
9. Soak: 24 h run; CPU sustained < 60 %, temperature < 80 °C, zero throttle flags, no stream drops. (Mirrors the Phase 1 exit criterion; results recorded below.)

## 7. Expected performance envelope

With hardware encode and decimated inference (1–5 fps vision sampling, 1 s audio windows, ≤4 Hz thermal), the full stack fits a Pi 4 with margin against the < 60 % sustained-CPU gate. Heaviest consumers: ONNX audio/vision inference and Postgres background work. Not supported on this node: model training, multiple large vision models, >1080p — those live on a workstation.

## 8. Validation record

| Date | Check                          | Result | Notes |
| ---- | ------------------------------ | ------ | ----- |
| —    | (fill in as bring-up proceeds) |        |       |

## 9. Known caveats

- Pi 4 4 GB is the floor for single-box comfort; watch memory pressure if adding services.
- 2.4 GHz Wi-Fi is fine for this node's outbound streams, but the _server_ role prefers Ethernet if the jack is available — one less variable.
- MLX90640 reads surface temperature only; per the project safety stance it feeds presence/trends, never temperature-as-vital-sign claims.
- If the single box ever feels cramped, the sanctioned split is: Pi 4 becomes a capture node (adapters + MQTT publishers only), server stack moves to any other Linux box. No architectural change required.
