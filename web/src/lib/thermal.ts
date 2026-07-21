// Live thermal grid stream over WebSocket (/api/v1/ws/thermal/{deviceId}). The server
// relays the paired node's latest 32×24 grid frame; the Thermal view renders it as a
// relative false-color heatmap. Grids are live-only (never stored); surface temps shown
// as a relative heatmap, the occupant as presence — never a body-temperature readout.
// Reconnects with capped exponential backoff; the httpOnly session cookie rides the
// same-origin upgrade, so no token plumbing is needed. Mirrors realtime.ts.

export interface ThermalFrame {
  ts: number;
  grid: number[]; // 768 values, row-major (24 rows × 32 cols), °C
  t_min: number;
  t_max: number;
  t_mean: number;
  quality: number;
}

export const THERMAL_COLS = 32;
export const THERMAL_ROWS = 24;

export type ThermalStream = { close: () => void };

export function subscribeToThermal(
  deviceId: number,
  onFrame: (f: ThermalFrame) => void,
  onStatus?: (connected: boolean) => void,
): ThermalStream {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const wsUrl = (): string => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/api/v1/ws/thermal/${deviceId}`;
  };

  const connect = (): void => {
    if (closed) return;
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      backoff = 1000;
      onStatus?.(true);
    };
    ws.onmessage = (ev: MessageEvent) => {
      try {
        const f = JSON.parse(ev.data as string) as ThermalFrame;
        if (Array.isArray(f.grid) && f.grid.length === THERMAL_COLS * THERMAL_ROWS) onFrame(f);
      } catch {
        /* ignore a malformed frame */
      }
    };
    ws.onclose = () => {
      ws = null;
      onStatus?.(false);
      if (closed) return;
      timer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 30_000);
    };
    ws.onerror = () => {
      ws?.close(); // let onclose schedule the reconnect
    };
  };

  connect();

  return {
    close: () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    },
  };
}
