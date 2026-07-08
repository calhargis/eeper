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
