import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchStatus } from './api';

// A bare fetch() has no timeout, so a request on a connection that died without closing —
// routine on a phone that slept or switched Wi-Fi/cellular/VPN — never settles: it neither
// resolves nor rejects. That hang is what froze the app on "Loading" with no recovery but
// clearing website data. Every request must therefore be abortable and time-bounded.

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  vi.useRealTimers();
});

describe('API request timeouts', () => {
  it('sends an abort signal with every request', async () => {
    let seen: RequestInit | undefined;
    globalThis.fetch = vi.fn((_url: unknown, init?: RequestInit) => {
      seen = init;
      return Promise.resolve(new Response('{"first_boot_required":false,"version":"t"}'));
    }) as unknown as typeof fetch;

    await fetchStatus();
    expect(seen?.signal, 'a request with no signal can hang forever').toBeInstanceOf(AbortSignal);
    expect(seen?.signal?.aborted).toBe(false);
  });

  it('aborts a request that never responds, instead of hanging forever', async () => {
    vi.useFakeTimers();
    let captured: AbortSignal | undefined;
    // A server that accepts the request and then goes silent — the black-hole case.
    globalThis.fetch = vi.fn((_url: unknown, init?: RequestInit) => {
      captured = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('AbortError')));
      });
    }) as unknown as typeof fetch;

    const pending = fetchStatus();
    const settled = vi.fn();
    void pending.then(settled, settled);

    await vi.advanceTimersByTimeAsync(9_000); // still within the budget
    expect(captured?.aborted, 'must not abort early').toBe(false);
    expect(settled).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(2_000); // past the 10s budget
    expect(captured?.aborted, 'a hung request must be aborted').toBe(true);
    await expect(pending).rejects.toThrow(); // the caller sees a failure it can retry
  });

  it('respects a caller-supplied signal rather than overriding it', async () => {
    const own = new AbortController();
    let seen: RequestInit | undefined;
    globalThis.fetch = vi.fn((_url: unknown, init?: RequestInit) => {
      seen = init;
      return Promise.resolve(new Response('{}'));
    }) as unknown as typeof fetch;

    // fetchStatus doesn't take a signal, so exercise the pass-through via a direct call.
    const { api } = await import('./api');
    await api('/system/status', { signal: own.signal });
    expect(seen?.signal).toBe(own.signal);
  });
});
