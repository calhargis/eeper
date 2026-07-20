<script lang="ts">
  // Live view: WebRTC playback of the selected camera, camera health dots, and
  // multi-camera switching. Any authenticated household member (admin or the
  // 'viewer'/grandparent role) can watch; the route guards on the session.
  import { onDestroy, onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { fetchCameras, fetchSession, type Camera, type User } from '$lib/api';
  import {
    connectCamera,
    inboundAudioStats,
    inboundVideoStats,
    type LiveSession,
  } from '$lib/webrtc';

  type Status = 'idle' | 'connecting' | 'live' | 'error';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let cameras = $state<Camera[]>([]);
  let selectedId = $state<number | null>(null);
  let videoEl = $state<HTMLVideoElement | undefined>();
  let status = $state<Status>('idle');
  let framesDecoded = $state(0);
  let jitterBufferMs = $state<number | null>(null);
  let audioTrack = $state(false);
  let audioPackets = $state(0);
  let muted = $state(true);
  let errorMsg = $state('');

  let live: LiveSession | null = null;
  let statsTimer: ReturnType<typeof setInterval> | null = null;
  let healthTimer: ReturnType<typeof setInterval> | null = null;
  // Bumped on every connect()/teardown/destroy so a slow, superseded negotiation
  // resolving late can detect it's stale and close its own peer connection rather
  // than leak it or clobber the current session.
  let connectGen = 0;
  let destroyed = false;

  const selected = $derived(cameras.find((c) => c.id === selectedId) ?? null);

  function fmtChecked(iso: string | null): string {
    if (!iso) return 'not checked yet';
    const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    return `${Math.round(secs / 60)}m ago`;
  }

  function teardown(): void {
    connectGen++; // invalidate any in-flight connect()
    if (statsTimer) {
      clearInterval(statsTimer);
      statsTimer = null;
    }
    if (live) {
      live.pc.close();
      live = null;
    }
    if (videoEl) videoEl.srcObject = null;
    framesDecoded = 0;
    jitterBufferMs = null;
    audioTrack = false;
    audioPackets = 0;
  }

  async function pollStats(): Promise<void> {
    if (!live) return;
    const s = await inboundVideoStats(live.pc);
    framesDecoded = s.framesDecoded;
    jitterBufferMs = s.jitterBufferMs;
    const a = await inboundAudioStats(live.pc);
    audioTrack = a.hasTrack;
    audioPackets = a.packetsReceived;
  }

  async function connect(id: number): Promise<void> {
    teardown();
    const gen = connectGen; // teardown() just bumped it; this attempt owns `gen`
    selectedId = id;
    status = 'connecting';
    errorMsg = '';
    const el = videoEl;
    if (!el) return;
    let session: LiveSession;
    try {
      session = await connectCamera(id);
    } catch (err) {
      if (gen !== connectGen || destroyed) return; // superseded/unmounted
      status = 'error';
      errorMsg = err instanceof Error ? err.message : 'could not connect to the stream';
      return;
    }
    // A newer connect(), a teardown, or unmount happened while negotiating —
    // this session is stale, so close it instead of leaking or clobbering.
    if (gen !== connectGen || destroyed) {
      session.pc.close();
      return;
    }
    live = session;
    el.srcObject = session.stream;
    status = 'live';
    statsTimer = setInterval(() => {
      void pollStats();
    }, 500);
  }

  async function loadCameras(): Promise<void> {
    try {
      cameras = await fetchCameras();
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : 'could not load cameras';
      return;
    }
    // If the camera being watched was removed, tear the session down rather than
    // leave it running behind the unmounted <video> (the empty-state branch).
    if (selectedId !== null && !cameras.some((c) => c.id === selectedId)) {
      teardown();
      selectedId = null;
      status = 'idle';
    }
  }

  onMount(() => {
    void (async () => {
      const session = await fetchSession();
      if (!session) {
        void goto('/'); // client-side guard: no session -> back to sign-in
        return;
      }
      user = session;
      ready = true;
      await tick(); // let the <video> mount before we attach a stream
      await loadCameras();
      if (cameras.length > 0) await connect(cameras[0].id);
      healthTimer = setInterval(() => {
        void loadCameras();
      }, 3000);
    })();
  });

  onDestroy(() => {
    destroyed = true;
    teardown(); // bumps connectGen, so any in-flight connect() closes its own pc
    if (healthTimer) clearInterval(healthTimer);
  });
</script>

<svelte:head>
  <title>eeper — live</title>
</svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">eeper</span>
    <span class="spacer"></span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  {#if cameras.length === 0}
    <p class="empty">No cameras are registered yet.</p>
  {:else}
    <div class="stage">
      <video
        bind:this={videoEl}
        autoplay
        playsinline
        {muted}
        data-testid="live-video"
        data-frames={framesDecoded}
        data-latency-ms={jitterBufferMs === null ? '' : Math.round(jitterBufferMs)}
        data-audio-track={audioTrack ? '1' : '0'}
        data-audio-packets={audioPackets}
      ></video>

      <div
        class="badge"
        class:on={status === 'live' && framesDecoded > 0}
        data-testid="live-status"
        data-status={status}
        data-frames={framesDecoded}
      >
        {#if status === 'live' && framesDecoded > 0}
          ● LIVE
        {:else if status === 'error'}
          Signal unavailable
        {:else}
          Connecting…
        {/if}
      </div>

      {#if selected?.has_audio}
        <button
          class="mute"
          data-testid="listen-toggle"
          aria-label={muted ? 'Listen in' : 'Mute listen-in'}
          onclick={() => (muted = !muted)}
        >
          {muted ? 'Listen in' : 'Mute'}
        </button>
      {/if}
    </div>

    {#if selected}
      <p class="meta">
        <span class="dot" class:online={selected.online} class:offline={selected.online === false}
        ></span>
        <strong>{selected.name}</strong>
        · {selected.online ? 'online' : selected.online === false ? 'offline' : 'checking'}
        · {fmtChecked(selected.last_checked)}
      </p>
    {/if}

    {#if cameras.length > 1}
      <div class="cameras" role="tablist" aria-label="Cameras">
        {#each cameras as cam (cam.id)}
          <button
            class="cam"
            class:selected={cam.id === selectedId}
            role="tab"
            aria-selected={cam.id === selectedId}
            onclick={() => connect(cam.id)}
          >
            <span class="dot" class:online={cam.online} class:offline={cam.online === false}></span>
            {cam.name}
          </button>
        {/each}
      </div>
    {/if}
  {/if}

  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}
{/if}

<style>
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .loading,
  .empty {
    text-align: center;
    color: var(--text-muted);
    margin: var(--sp-7) var(--sp-4);
  }
  .stage {
    position: relative;
    background: #000;
    aspect-ratio: 16 / 9;
    max-height: 70vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
  }
  .badge {
    position: absolute;
    top: var(--sp-2);
    left: var(--sp-2);
    font-size: var(--fs-xs);
    letter-spacing: 0.03em;
    padding: var(--sp-1) var(--sp-2);
    border-radius: var(--r-sm);
    background: rgba(0, 0, 0, 0.6);
    color: var(--text-2);
  }
  .badge.on {
    color: var(--ok);
  }
  .mute {
    position: absolute;
    bottom: var(--sp-2);
    right: var(--sp-2);
    min-height: var(--tap);
    font-size: var(--fs-sm);
    padding: var(--sp-2) var(--sp-3);
    border: none;
    border-radius: var(--r-sm);
    background: rgba(0, 0, 0, 0.6);
    color: var(--text);
    cursor: pointer;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-4) 0;
    color: var(--text-2);
    font-size: var(--fs-sm);
  }
  .dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: var(--r-pill);
    background: var(--text-muted); /* unknown */
    flex: none;
  }
  .dot.online {
    background: var(--ok);
  }
  .dot.offline {
    background: var(--danger);
  }
  .cameras {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sp-2);
    padding: var(--sp-3) var(--sp-4);
  }
  .cam {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    min-height: var(--tap);
    padding: var(--sp-2) var(--sp-3);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    font-size: var(--fs-sm);
  }
  .cam.selected {
    border-color: var(--accent);
    background: var(--surface-2);
  }
  .error {
    color: var(--danger);
    padding: 0 var(--sp-4);
    font-size: var(--fs-sm);
  }
</style>
