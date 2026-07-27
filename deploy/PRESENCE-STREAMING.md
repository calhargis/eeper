# Stream only when the crib is occupied

An optional setting that turns the camera **and** clip recording off while the crib is empty,
and brings them back by itself when someone is detected. It only appears when a paired input
can actually answer the presence question — today that means a
[thermal node](../docs/thermal-node.md).

**Settings → Camera → "Only stream when the crib is occupied"**

## What it does and does not do

Removing the camera's stream registration stops go2rtc pulling RTSP from the adapter, which
lets an on-demand adapter idle its encoder. That is where the saving comes from. It does
**not** stop the camera container — the api runs with no Docker socket and cannot, by design
(giving a network-facing service that power would be giving it root on the host).

The room microphone is deliberately left running. Listening costs almost nothing next to
video, and hearing the room is the one thing you still want when the picture is off.

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
