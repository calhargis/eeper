# Prior Art & Positioning

eeper did not appear in a vacuum. This document catalogs the projects and products that overlap with what we're building, what we learned or borrowed from each, and where eeper deliberately differs. It exists for three reasons: intellectual honesty, helping new contributors understand positioning, and answering "doesn't X already do this?" once instead of repeatedly in issues.

If you know of a relevant project that isn't listed, please open a PR — this file is meant to be complete, not flattering.

---

## Direct prior art: DIY baby monitors

### OpenBabyMonitor (lars-frogner)

<https://github.com/lars-frogner/OpenBabyMonitor> · GPL-3.0 · last release Jan 2022 · unmaintained (per its own README)

The closest ancestor to eeper's core loop. A Raspberry Pi (down to a $10 Pi Zero W) becomes a self-contained monitor: flashable disk image, local web app, live audio/video streaming, and cry detection via either a loudness threshold or a neural network trained on Google's AudioSet to distinguish crying, babbling, and ambient sound. It also offers a clever access-point mode where the monitor spawns its own Wi-Fi network when none is available (e.g., travel).

**What it proved:** a solo developer shipped live streaming plus NN cry detection on a Pi Zero in 2021. The kernel of this product category is feasible on trivial hardware.

**What we took from it:**

- The three-way _cry / babble / ambient_ classification framing — "babble" is a smart confuser class that our audio fixture library (M2.0) includes as a direct result.
- Its published power measurements (~0.6 W cry detection, ~1.7 W at 1080p streaming on a Pi Zero) as calibration data for a possible future battery/stroller variant.
- Access-point mode, recorded in the master plan's open questions as a post-v1 candidate.

**Where eeper differs:** OpenBabyMonitor is device-first — the Pi _is_ the sensor, server, and website, distributed as one image. eeper is protocol-first: any RTSP camera, any MQTT sensor, server on any Linux box via Docker. OpenBabyMonitor has one input pair (mic + optional Pi camera) and no fusion, no sleep/wake or distress states, no sessions, no history/trends, no event clips, and no true push notifications (its web app requires a live, awake browser tab — a limitation its README documents and our Web Push work exists to remove). Its stack (PHP, Raspbian Buster) reflects its 2021 origins. Its GPL-3.0 license is one-way compatible with our AGPL-3.0 target should we ever adapt code rather than ideas; so far we have taken only ideas, with attribution here.

### Assorted one-off Pi baby monitor builds

Numerous blog-post-scale projects exist (motionEye + a mic, Pi + MotionEyeOS, phone-app repurposing guides). They collectively demonstrate sustained hobbyist demand for self-hosted baby monitoring, and collectively share the same shape: single device, live view, sometimes motion alerts, no insight layer, no maintained codebase. We treat them as demand signal rather than technical prior art.

---

## Adjacent open source infrastructure (things we use or deliberately don't compete with)

### go2rtc

<https://github.com/AlexxIT/go2rtc> · MIT

Not prior art — a dependency. go2rtc is our media gateway: it ingests nearly any camera protocol and re-serves WebRTC/RTSP/HLS without re-encoding. eeper's "any camera just works" property is largely go2rtc's ingestion breadth plus our contract validation on top. We deliberately did not build a media gateway.

### Frigate

<https://frigate.video> · MIT

The strongest open source project in the adjacent "camera + local AI" space: a full NVR with object detection, event clips, and hardware-accelerator support, tightly integrated with Home Assistant. Frigate answers "what's happening on my cameras?" for security use cases. eeper answers a narrower, deeper question — "how is my baby sleeping?" — which needs multi-modal fusion (audio, radar, thermal, optional pulse-ox), state machines over time (sleep sessions), and longitudinal trends, none of which are Frigate's mission. Where scopes overlap (recording, clips, camera health), we accept convergent design and cite it: our ring-buffer + event-clip recorder is the standard NVR pattern Frigate also uses. Users wanting general home surveillance should run Frigate; the two coexist happily on one network, even sharing cameras via go2rtc.

### Home Assistant (+ ESPHome)

<https://home-assistant.io> · Apache-2.0

The gravitational center of self-hosted home automation. eeper is not a Home Assistant integration by design — we want a focused, self-contained product with its own opinionated UI, timeline, and safety stance — but we adopt its ecosystem conventions where they cost nothing: ESPHome for sensor-node firmware (M3.2) and MQTT topic discipline, so eeper sensor nodes are trivially reusable by HA users and vice versa. A first-class HA integration (exposing eeper states as HA entities) is a welcome post-v1 community contribution; the MQTT event bus makes it straightforward.

### Thingino / OpenIPC

<https://thingino.com> · <https://openipc.org>

Open-source replacement firmware for consumer IP cameras (Thingino targets Ingenic-SoC cams such as the Wyze Cam v3). Not competitors — enablers. They convert cloud-locked hardware into the exact kind of standards-compliant RTSP camera eeper's contract assumes, with full local ownership. Our sample-hardware guide documents the Wyze v3 + Thingino path, including the T31AL secure-boot variant caveat and the conditionally-supported status of pan/tilt models.

### docker-wyze-bridge

<https://github.com/mrlt8/docker-wyze-bridge> · AGPL-3.0

Bridges unmodified Wyze cameras (still on stock firmware and cloud accounts) into RTSP streams. A pragmatic option for users unwilling to flash firmware, and its streams satisfy our camera contract. We prefer and document the Thingino path because it removes the cloud dependency rather than tunneling around it, but bridge-fed cameras work with eeper unmodified — a nice demonstration of the protocol-first architecture.

---

## Commercial products (positioning, not prior art)

**Owlet, Nanit, Miku, Cubo Ai, et al.** define the commercial smart-baby-monitor category: polished hardware, cloud services, subscription features, and — in Owlet's case — the only OTC FDA-cleared pulse-ox sock. They are relevant to eeper mainly as contrast: cloud-dependent, subscription-oriented, closed. eeper's positioning is the inverse (local, free, open, user-owned), and its safety stance is _more_ conservative than the category norm: no vital-sign claims, insights only. The FDA's September 2025 warning about unauthorized vital-sign baby monitors (see master plan §2) shapes our boundary here. We do not attempt feature parity with these products' proprietary hardware (e.g., cleared medical sensors), and we say so plainly in user-facing docs.

**Classic audio baby monitors** (the $30 radio kind) remain the baseline the whole category is measured against: they do one thing — transmit sound — with total reliability. Our episode-level cry-detection gate (M2.3) and the sustained-sound-level fallback exist because a smart monitor that misses sustained crying is worse than that $30 baseline, and we hold ourselves to beating it before layering intelligence on top.

---

## Summary: the gap eeper occupies

Every capability in eeper exists _somewhere_ in the ecosystem: streaming (go2rtc), NVR + detection (Frigate), cry detection on a Pi (OpenBabyMonitor), open camera firmware (Thingino), sensor firmware (ESPHome). What does not exist, as of this writing, is a maintained project that composes them into a baby-focused whole: multi-modal input fusion under a graceful-degradation contract, sleep-state machines and longitudinal trends, real push notifications, a locked-down-by-default security posture, run-anywhere packaging, and an explicit insights-not-alarms safety stance. That composition — not any single component — is the project.

_Last reviewed: 2026-07-10. If a maintained project matching this composition appears, this file should say so honestly, and we should talk to them._
