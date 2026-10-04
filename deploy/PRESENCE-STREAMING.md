# Stream only when the crib is occupied

An optional setting that turns the camera **and** clip recording off while the crib is empty,
and brings them back by itself when someone is detected. It only appears when a paired input
can actually answer the presence question — today that means a
[thermal node](../docs/thermal-node.md).

**Settings → Camera → "Only stream when the crib is occupied"**

## What it does and does not do

Removing the camera's stream registration stops go2rtc pulling RTSP from the adapter. That
only saves power if the adapter is **on-demand** — capturing and encoding only while something
reads it. An earlier version of this page claimed it always did; it did not. Until the camera
adapter gained `sourceOnDemand` and the audio adapter `runOnDemand`, both kept capturing and
encoding to nobody through every "stopped" period. Both are now on-demand by default (see
[adapters/README.md](../adapters/README.md)).

It does **not** stop the containers themselves — the api runs with no Docker socket and cannot,
by design (giving a network-facing service that power would be giving it root on the host).

The room microphone stops with the camera. Listening costs little, but a live microphone in a
nursery is a privacy question, not only a power one.

## Low-power mode — what goes quiet over an empty crib

Measured on a Raspberry Pi 4 with every input active, roughly: insight engine 69% of one core,
thermal node 17%, audio adapter 13%, camera adapter 11%, go2rtc 8%, recorder 5%. With the crib
known to be empty:

| Component            | Over an empty crib                                                                     |
| -------------------- | -------------------------------------------------------------------------------------- |
| Camera adapter       | idle — sensor and hardware encoder stop 10 s after the last reader leaves              |
| Audio adapter        | idle — ffmpeg capture stops 10 s after the last listener leaves                        |
| Insight engine       | paused — no video or audio decoding (it used to crash-loop against the removed stream) |
| Recorder             | stopped — no segments of an empty crib                                                 |
| Camera health probes | paused — each probe opens the camera, which would keep it awake                        |
| Thermal node         | **slows, never stops** — one frame every `EEPER_THERMAL_IDLE_INTERVAL_S` (opt-in)      |

The thermal node is the one input that must keep running: it is how the system notices a baby
being put down. With idle mode on, it drops to one frame every 15 s once the crib has been empty
for two minutes, and wakes to full rate on the **first** frame that looks like a body — the
presence gate needs ~8 s of continuous frames to confirm, which one frame every 15 s could never
supply. Worst case from a baby being put down to the stream starting is roughly one idle interval
plus the 8 s confirmation plus a couple of seconds for the camera to start. See
[docs/thermal-node.md](../docs/thermal-node.md).

The idle interval is capped at 45 s. The server treats a presence input silent for 90 s as stale,
and **stale fails open** — the camera comes back on — so a slower node would undo the savings.

## The rule, and why it leans the way it does

The camera stops **only** when a working presence input actively reports an empty crib.
Everything else keeps it live:

| Situation                         | Camera  |
| --------------------------------- | ------- |
| Setting is off                    | **on**  |
| No presence input paired          | **on**  |
| Sensor hasn't reported for 90 s   | **on**  |
| "Start anyway" override is active | **on**  |
| Sensor reports someone present    | **on**  |
| Sensor reports the crib empty     | **off** |

A camera that stays on unnecessarily costs a little power. A camera that is off while the
baby is in the crib defeats the product. So every uncertain case resolves to _keep watching_ —
in particular, a sensor that has gone quiet means "we cannot tell", never "nobody is there".

## Start anyway

Live view shows **No baby detected in crib** with a **Start anyway** button whenever the
camera has been gated off. It brings the stream up for 30 minutes regardless of presence, and
is available to **any** household member, not just admins: someone looking at that message
must be able to check for themselves. Enabling the gating is the admin decision; overriding it
for half an hour is not.

The override is an expiry, not a flag, so a forgotten one lapses on its own instead of
disabling the feature indefinitely. Turning the setting off also clears it.

## Timing

The camera comes back within a few seconds of presence being detected: the thermal node's own
hysteresis takes ~8 s to confirm someone is there (see
[docs/thermal-node.md](../docs/thermal-node.md)), and the api's reconcile tick re-registers the
stream shortly after. Going the other way is slower on purpose — the node holds presence for
45 s after it stops seeing anyone, so a brief occlusion never blanks the picture.

## Limitations

- Presence means **a warm body**, not specifically your baby. An adult leaning over the crib
  reads as presence.
- The thermal node keeps sampling at its normal rate while the camera is off. Throttling it
  when idle would need a command channel from the server to the node, and the broker ACLs
  currently only let a device publish. That is a separate change.
