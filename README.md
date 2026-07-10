# eeper



An open source, self-hosted baby monitoring system that runs on hardware you already own. eeper turns any combination of a camera, a microphone, and inexpensive DIY sensors into live monitoring plus sleep insights — sleep/wake state, movement level, calm/distressed detection, and long-term sleep history and trends — all processed locally, on your own network.

**Status: pre-alpha (design phase).** The architecture and implementation plan are complete; code is landing phase by phase. See [Roadmap](#roadmap).

---

## ⚠️ What eeper is — and is not

eeper is a **sleep-insight and awareness tool**. It is **not a medical device**. It does not diagnose, treat, or prevent any condition, and it must never be relied upon to detect apnea, SIDS risk, or any health emergency.

The U.S. FDA has warned against consumer infant monitors that make vital-sign claims without marketing authorization, and independent testing of such devices has documented both false alarms and false reassurance. eeper takes that seriously by design: notifications are worded as nudges to go check on your baby, never as clinical alerts, and the optional pulse-oximetry input is insights-only, quality-gated, and gated behind an explicit disclaimer.

Nothing in this project replaces adult supervision or [safe-sleep practices](https://safetosleep.nichd.nih.gov/).

---

## Features

**Live monitoring**
- Sub-second live video and audio in any browser, installable as a phone app (PWA)
- Works with cameras you already have: any RTSP/ONVIF IP camera, USB webcams, Raspberry Pi camera modules, even an old phone running an IP-camera app
- Night-vision support (e.g., Pi Camera Module 3 NoIR + IR illuminator)

**Insights**
- Cry detection with tappable event clips and push-notification nudges
- Movement level, sleep/wake state, and calm/distressed state, fused from whatever inputs you have
- Tonight timeline: scrub through the night's states and events
- Trends: sleep duration, wake counts, longest stretch, week-over-week patterns

**Inputs — use any, all, or any combination**
- 📹 Video (the only required input for a complete, useful system)
- 🎤 Audio (from the camera or a standalone mic node)
- 📡 Motion: 60 GHz mmWave radar (contactless presence + respiration estimate), PIR, or camera-derived motion with zero extra hardware
- 🫀 Pulse oximetry (optional, insights-only — see the warning above)

**Self-hosted and private**
- Runs entirely on your LAN; no cloud, no account, no telemetry
- All ML inference is local (CPU by default; optional Hailo/CUDA acceleration)
- Remote access via WireGuard/Tailscale only — nothing is exposed to the internet
- TLS everywhere (yes, on your LAN too), forced credential setup, per-device sensor credentials

## How it works

```
cameras (RTSP/H.264) ──► media gateway (go2rtc) ──► WebRTC live view
                                  │
sensor nodes (ESP32) ──► MQTT ──► insight engine ──► states, events, nudges
                                  │
                            TimescaleDB ──► history & trends ──► PWA
```

eeper targets **protocols, not devices**. The server consumes standard RTSP/H.264 video and a small MQTT JSON contract for sensors, so it runs on any amd64/arm64 Linux box — a Raspberry Pi 4/5, a NAS, an old laptop, a mini-PC, or a VM. Hardware-specific code is confined to tiny optional adapter containers at the edge.

Full details: [Master Plan](./docs/MASTER_PLAN.md) · [Implementation Plan](./docs/IMPLEMENTATION_PLAN.md)

## Quick start

> Not yet functional — this is the target install experience for v1.0.

```bash
git clone https://github.com/YOUR_ORG/eeper.git
cd eeper/deploy
./install.sh          # checks prerequisites, generates secrets, provisions local TLS
docker compose --profile core --profile video up -d
```

Then open the printed URL, complete the first-boot wizard (you'll be required to create admin credentials — there are no defaults), and add your first camera.

Requirements: Docker + Compose v2, an amd64 or arm64 Linux host, and at least one camera that can produce an RTSP stream (or a USB/Pi camera using the bundled adapters).

## Reference hardware

Nothing below is required — it's the tested reference set:

| Role | Reference part | Notes |
|---|---|---|
| Server | Raspberry Pi 5 (4 GB) | Or any Linux box; Pi is a target, not a requirement |
| Camera | Pi Camera Module 3 NoIR + IR illuminator | Any RTSP/ONVIF or USB camera works |
| Microphone | I2S MEMS mic (INMP441) on ESP32, or USB mic | Camera audio also works |
| Motion | Seeed 60 GHz mmWave on ESP32 (ESPHome) | Contactless; PIR as budget option |
| Pulse-ox (optional) | MAX30102 on ESP32 | Insights-only; read the warning above |

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Secure skeleton: Compose, TLS, auth, CI | 🔜 next |
| 1 | Video: live view, adapters, recording | ⬜ |
| 2 | Audio, cry detection, event clips, nudges | ⬜ |
| 3 | Sensor bus, sleep/wake + distress fusion, Tonight timeline | ⬜ |
| 4 | Trends, optional pulse-ox, ops polish | ⬜ |
| 5 | Security review, performance gates, v1.0 release | ⬜ |

## Contributing

Contributions are welcome once Phase 0 lands. Two things to know up front:

1. **The safety boundary is non-negotiable.** PRs that add medical claims, clinical alarm language, or diagnostic features will be declined — this is encoded in the PR template and even enforced by CI (notification copy is linted against clinical terms).
2. **Tests are the definition of done.** Every milestone ships with automated criteria (and labeled manual procedures where hardware/perception is involved). See the [Implementation Plan](./docs/IMPLEMENTATION_PLAN.md).

Development uses conventional commits, mypy/TS strict typing, and multi-arch CI (amd64 + arm64).

## Privacy

All video, audio, and sensor data stays on your hardware. There is no telemetry, no phone-home, and no cloud dependency for any feature. A visible "pause monitoring" control stops capture entirely. Back up your data with one `pg_dump` and one directory copy.

## License

To be finalized before first release — current target is AGPL-3.0 for the server and MIT for client libraries. See the open questions in the [Master Plan](./docs/MASTER_PLAN.md).

---

*eeper is a community project and is not affiliated with, cleared by, or endorsed by the FDA or any medical body. Always follow your pediatrician's guidance.*
