import type { Camera, Device } from '$lib/api';

// Structural signatures of the input lists — the fields that actually change the picker or
// a view's IDENTITY. `last_checked` (cameras) and `last_seen_at` (devices) advance on every
// health poll for a live source, so they are DELIBERATELY excluded: if they were included,
// the 3s poll would reassign the state each tick, re-deriving `inputs`/`selected` into new
// objects and tearing the thermal WebSocket down + reconnecting every few seconds. Comparing
// signatures lets the poll update state only when something a viewer would notice changed.

export function camerasSignature(cameras: Camera[]): string {
  return cameras
    .map(
      (c) =>
        `${c.id}:${c.name}:${c.codec}:${c.width}x${c.height}:${c.enabled}:${c.has_audio}:${c.online}`,
    )
    .join('|');
}

export function devicesSignature(devices: Device[]): string {
  return devices.map((d) => `${d.id}:${d.name}:${d.kind}:${d.enabled}:${d.online}`).join('|');
}
