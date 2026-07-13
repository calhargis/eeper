// Same-origin API client. All calls go to /api/v1 behind the edge proxy; the
// httpOnly session cookie is sent automatically.

export type User = { id: number; username: string; role: string };
export type SystemStatus = { first_boot_required: boolean; version: string };
export type Camera = {
  id: number;
  name: string;
  codec: string;
  width: number;
  height: number;
  enabled: boolean;
  has_audio: boolean;
  online: boolean | null;
  last_checked: string | null;
};

export function api(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`/api/v1${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
}

export async function detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function fetchStatus(): Promise<SystemStatus> {
  const res = await api('/system/status');
  return (await res.json()) as SystemStatus;
}

export async function fetchSession(): Promise<User | null> {
  const res = await api('/auth/session');
  return res.ok ? ((await res.json()) as User) : null;
}

export async function fetchCameras(): Promise<Camera[]> {
  const res = await api('/cameras');
  if (!res.ok) throw new Error(`could not load cameras (${res.status})`);
  return (await res.json()) as Camera[];
}

// ── M2.4: nudge events + notifications ────────────────────────────────────────

export type EventItem = {
  id: number;
  ts: string;
  camera_id: number;
  type: string;
  value: string;
  previous_value: string | null;
  confidence: number;
  clip_id: number | null;
};

export type NotificationPreferences = {
  push_enabled: boolean;
  quiet_hours_enabled: boolean;
  quiet_hours_start: number; // minutes-of-day
  quiet_hours_end: number;
  timezone: string;
};

export async function fetchEvents(limit = 100): Promise<EventItem[]> {
  const res = await api(`/events?limit=${limit}`);
  if (!res.ok) throw new Error(`could not load events (${res.status})`);
  return (await res.json()) as EventItem[];
}

export async function fetchVapidKey(): Promise<string> {
  const res = await api('/push/vapid-key');
  if (!res.ok) return '';
  return ((await res.json()) as { public_key: string }).public_key;
}

export async function savePushSubscription(sub: PushSubscriptionJSON): Promise<void> {
  const res = await api('/users/me/push-subscriptions', {
    method: 'POST',
    body: JSON.stringify(sub),
  });
  if (!res.ok) throw new Error(`could not save subscription (${res.status})`);
}

export async function deletePushSubscription(endpoint: string): Promise<void> {
  const res = await api(`/users/me/push-subscriptions?endpoint=${encodeURIComponent(endpoint)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`could not remove subscription (${res.status})`);
}

export async function fetchPreferences(): Promise<NotificationPreferences> {
  const res = await api('/users/me/notification-preferences');
  if (!res.ok) throw new Error(`could not load preferences (${res.status})`);
  return (await res.json()) as NotificationPreferences;
}

export async function updatePreferences(
  patch: Partial<NotificationPreferences>,
): Promise<NotificationPreferences> {
  const res = await api('/users/me/notification-preferences', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`could not save preferences (${res.status})`);
  return (await res.json()) as NotificationPreferences;
}

// ── M3.1: sensor devices ──────────────────────────────────────────────────────

export type DeviceKind = 'mmwave' | 'pir' | 'other';

export type Device = {
  id: number;
  name: string;
  kind: string;
  enabled: boolean;
  online: boolean | null; // null until the node's first reading arrives
  last_seen_at: string | null;
};

// The pairing response — the node's MQTT identity, returned ONCE. The password is
// never stored server-side or echoed again, so the UI must surface it immediately.
export type PairedDevice = Device & {
  mqtt_username: string;
  mqtt_password: string;
  topic_prefix: string;
};

export async function fetchDevices(): Promise<Device[]> {
  const res = await api('/devices');
  if (!res.ok) throw new Error(`could not load devices (${res.status})`);
  return (await res.json()) as Device[];
}

export async function pairDevice(name: string, kind: DeviceKind): Promise<PairedDevice> {
  const res = await api('/devices', { method: 'POST', body: JSON.stringify({ name, kind }) });
  if (!res.ok) throw new Error(await detail(res, 'Could not pair the device.'));
  return (await res.json()) as PairedDevice;
}

export async function unpairDevice(id: number): Promise<void> {
  const res = await api(`/devices/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(await detail(res, 'Could not unpair the device.'));
}

// ── M3.3: fused sleep/wake timeline (the Tonight scrubbable track) ─────────────

export type FusedSegment = {
  start: string;
  end: string;
  sleep: 'sleep' | 'wake';
  arousal: 'calm' | 'distressed';
  is_open: boolean;
};

export type SleepSession = { started_at: string; ended_at: string | null };

export type TonightTimeline = {
  start: string;
  end: string;
  segments: FusedSegment[];
  sessions: SleepSession[];
};

export async function fetchTimeline(): Promise<TonightTimeline> {
  const res = await api('/fusion/timeline');
  if (!res.ok) throw new Error(`could not load the timeline (${res.status})`);
  return (await res.json()) as TonightTimeline;
}
