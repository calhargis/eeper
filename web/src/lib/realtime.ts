// Live event stream over WebSocket (/api/v1/ws/events). The nudge worker broadcasts
// each event when it fires and again when its clip is ready, so the Tonight view
// updates without a reload. Reconnects with capped exponential backoff; the httpOnly
// session cookie rides the same-origin upgrade, so no token plumbing is needed.

import type { EventItem } from '$lib/api';

export type EventStream = { close: () => void };

export function subscribeToEvents(onEvent: (e: EventItem) => void): EventStream {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const wsUrl = (): string => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/api/v1/ws/events`;
  };

  const connect = (): void => {
    if (closed) return;
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      backoff = 1000; // reset backoff on a clean connection
    };
    ws.onmessage = (ev: MessageEvent) => {
      try {
        onEvent(JSON.parse(ev.data as string) as EventItem);
      } catch {
        /* ignore a malformed frame */
      }
    };
    ws.onclose = () => {
      ws = null;
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
