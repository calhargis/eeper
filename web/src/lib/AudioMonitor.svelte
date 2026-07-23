<script lang="ts">
  // Audio-only view: play the standalone host microphone (the `mic` go2rtc stream) and
  // visualise the sound arriving, so a caregiver can focus purely on what they hear.
  // A Web Audio AnalyserNode taps the same MediaStream the <audio> element plays and
  // drives a live spectrum + an overall level. Owns its peer connection + AudioContext:
  // mounting connects, unmounting tears everything down. Awareness only — the room's
  // sound, never a medical or diagnostic signal.
  import { onDestroy, onMount } from 'svelte';
  import { connectMic, type LiveSession } from '$lib/webrtc';

  type Status = 'connecting' | 'live' | 'error';
  let status = $state<Status>('connecting');
  let errorMsg = $state('');
  let volume = $state(80); // 0..100
  let level = $state(0); // 0..1 smoothed overall loudness
  const BARS = 20;
  let bars = $state<number[]>(Array(BARS).fill(0)); // 0..1 per band

  let audioEl = $state<HTMLAudioElement | undefined>();
  let session: LiveSession | null = null;
  let ctx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let raf = 0;
  let destroyed = false;

  $effect(() => {
    if (audioEl) audioEl.volume = Math.min(1, Math.max(0, volume / 100));
  });

  // A moving spectrum + RMS-ish level from the analyser, smoothed per frame so the
  // meter glides rather than flickers.
  function tick(): void {
    if (!analyser) return;
    const bins = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(bins);
    // Sample the lower bins (voice/room energy lives there), one slice per bar.
    const used = Math.min(bins.length, 48);
    const next = bars.slice();
    let sum = 0;
    for (let i = 0; i < BARS; i++) {
      const start = Math.floor((i * used) / BARS);
      const end = Math.max(start + 1, Math.floor(((i + 1) * used) / BARS));
      let peak = 0;
      for (let j = start; j < end; j++) peak = Math.max(peak, bins[j]);
      const target = peak / 255;
      next[i] = next[i] * 0.6 + target * 0.4; // smooth
      sum += target;
    }
    bars = next;
    level = level * 0.7 + Math.min(1, (sum / BARS) * 1.6) * 0.3;
    raf = requestAnimationFrame(tick);
  }

  function startMeter(stream: MediaStream): void {
    try {
      const AC =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AC) return; // no Web Audio — playback still works, just no meter
      ctx = new AC();
      void ctx.resume(); // the input selection was a user gesture, so this is allowed
      const src = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 128;
      analyser.smoothingTimeConstant = 0.8;
      src.connect(analyser); // read-only tap; not routed to destination (the <audio> plays)
      raf = requestAnimationFrame(tick);
    } catch {
      /* metering is best-effort; playback is the point */
    }
  }

  onMount(() => {
    void (async () => {
      const el = audioEl;
      if (!el) return;
      let s: LiveSession;
      try {
        s = await connectMic();
      } catch (err) {
        if (destroyed) return;
        status = 'error';
        errorMsg = err instanceof Error ? err.message : 'could not connect to the microphone';
        return;
      }
      if (destroyed) {
        s.pc.close();
        return;
      }
      session = s;
      el.srcObject = s.stream;
      void el.play().catch(() => {});
      status = 'live';
      startMeter(s.stream);
    })();
  });

  onDestroy(() => {
    destroyed = true;
    if (raf) cancelAnimationFrame(raf);
    analyser?.disconnect();
    void ctx?.close();
    session?.pc.close();
    session = null;
  });
</script>

<div class="monitor" data-testid="audio-monitor" data-status={status}>
  <div class="mic" class:live={status === 'live'} style="--glow: {level}">
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      aria-hidden="true"
    >
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" stroke-linecap="round" />
      <path d="M12 18v3" stroke-linecap="round" />
    </svg>
  </div>

  <div
    class="spectrum"
    data-testid="audio-level"
    data-level={level.toFixed(2)}
    role="img"
    aria-label="Live audio level"
  >
    {#each bars as h, i (i)}
      <span class="bar" style="height: {Math.round(6 + h * 94)}%"></span>
    {/each}
  </div>

  <div class="status">
    <span class="pill" class:pill--ok={status === 'live'}>
      {status === 'live' ? '● Listening' : status === 'error' ? 'Unavailable' : 'Connecting…'}
    </span>
  </div>

  <label class="vol">
    <span>Volume</span>
    <input
      type="range"
      min="0"
      max="100"
      step="1"
      bind:value={volume}
      data-testid="audio-volume"
      aria-label="Volume"
    />
  </label>

  <p class="caveat">
    The room's sound, live from the microphone — for awareness, <strong
      >not a medical or diagnostic signal</strong
    >.
  </p>

  <!-- The mic stream plays here (audio-only, unmuted). -->
  <audio bind:this={audioEl} autoplay data-testid="audio-el"></audio>
</div>

{#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}

<style>
  .monitor {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--sp-4);
    padding: var(--sp-6) var(--sp-4);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r);
  }
  .mic {
    display: grid;
    place-items: center;
    width: 5rem;
    height: 5rem;
    border-radius: var(--r-pill);
    color: var(--text-muted);
    background: var(--surface-2);
    transition: color 0.2s ease;
  }
  .mic.live {
    color: var(--accent);
    /* glow tracks the live level */
    box-shadow: 0 0 calc(6px + var(--glow, 0) * 40px) var(--accent-subtle);
  }
  .mic svg {
    width: 2.4rem;
    height: 2.4rem;
  }
  .spectrum {
    display: flex;
    align-items: flex-end;
    justify-content: center;
    gap: 3px;
    width: 100%;
    max-width: 22rem;
    height: 96px;
  }
  .bar {
    flex: 1;
    max-width: 12px;
    min-height: 4px;
    border-radius: var(--r-pill);
    background: linear-gradient(
      to top,
      var(--accent),
      color-mix(in srgb, var(--accent) 55%, var(--warn))
    );
    transition: height 0.06s linear;
  }
  .status {
    min-height: 1.5rem;
  }
  .vol {
    flex-direction: row;
    align-items: center;
    gap: var(--sp-3);
    width: 100%;
    max-width: 22rem;
    color: var(--text-2);
    font-size: var(--fs-sm);
  }
  .vol input {
    flex: 1;
    accent-color: var(--accent);
    cursor: pointer;
  }
  .caveat {
    margin: 0;
    text-align: center;
    color: var(--text-muted);
    font-size: var(--fs-xs);
    line-height: 1.5;
    max-width: 24rem;
  }
  audio {
    display: none;
  }
  .error {
    color: var(--danger);
    padding: var(--sp-3) var(--sp-4) 0;
    font-size: var(--fs-sm);
  }
</style>
