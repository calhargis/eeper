import { describe, expect, test } from 'vitest';
import type { Camera, Device } from '$lib/api';
import { camerasSignature, devicesSignature } from './live-inputs';

// Regression guard for the thermal-view "keeps reconnecting" bug. The Live view polls the
// device/camera lists every 3s; a live thermal node's last_seen_at advances every tick. If
// the poll reassigned state on that alone, `inputs`/`selected` would re-derive and the
// ThermalHeatmap WebSocket would tear down + reconnect ~every 3s. The signature must stay
// stable across a heartbeat-only change, and must change when something structural does.

const dev = (over: Partial<Device> = {}): Device => ({
  id: 1,
  name: 'crib',
  kind: 'thermal',
  enabled: true,
  online: true,
  last_seen_at: '2026-07-23T00:00:00Z',
  ...over,
});

const cam = (over: Partial<Camera> = {}): Camera => ({
  id: 1,
  name: 'nursery',
  codec: 'h264',
  width: 1280,
  height: 720,
  enabled: true,
  has_audio: true,
  online: true,
  last_checked: '2026-07-23T00:00:00Z',
  ...over,
});

describe('devicesSignature', () => {
  test('is unchanged when only last_seen_at advances (the poll must not churn state)', () => {
    expect(devicesSignature([dev({ last_seen_at: 'earlier' })])).toBe(
      devicesSignature([dev({ last_seen_at: 'later' })]),
    );
  });
  test('changes when online flips', () => {
    expect(devicesSignature([dev({ online: true })])).not.toBe(
      devicesSignature([dev({ online: false })]),
    );
  });
  test('changes when a device is added/removed or renamed', () => {
    expect(devicesSignature([dev()])).not.toBe(devicesSignature([dev(), dev({ id: 2 })]));
    expect(devicesSignature([dev()])).not.toBe(devicesSignature([dev({ name: 'other' })]));
  });
});

describe('camerasSignature', () => {
  test('is unchanged when only last_checked advances', () => {
    expect(camerasSignature([cam({ last_checked: 'earlier' })])).toBe(
      camerasSignature([cam({ last_checked: 'later' })]),
    );
  });
  test('changes when online or has_audio changes', () => {
    expect(camerasSignature([cam({ online: true })])).not.toBe(
      camerasSignature([cam({ online: false })]),
    );
    expect(camerasSignature([cam({ has_audio: true })])).not.toBe(
      camerasSignature([cam({ has_audio: false })]),
    );
  });
});
