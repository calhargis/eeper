<script lang="ts">
  // Tonight view: the night's nudge events (sustained sound, and experimental cry),
  // each with a tappable clip. New events arrive live over the /ws/events WebSocket —
  // no reload — and an event's clip appears as soon as the worker has promoted it.
  // A notifications panel manages Web Push opt-in + quiet hours. Any authenticated
  // household member can view; the route guards on the session.
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    fetchCameras,
    fetchEvents,
    fetchPreferences,
    fetchSession,
    fetchTimeline,
    updatePreferences,
    type Camera,
    type EventItem,
    type NotificationPreferences,
    type TonightTimeline,
    type User,
  } from '$lib/api';
  import { subscribeToEvents, type EventStream } from '$lib/realtime';
  import { currentSubscription, disablePush, enablePush, pushSupported } from '$lib/push';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let events = $state<EventItem[]>([]);
  let cameraNames = $state<Record<number, string>>({});
  let expandedId = $state<number | null>(null);
  let timeline = $state<TonightTimeline | null>(null);
  let prefs = $state<NotificationPreferences | null>(null);
  let pushActive = $state(false);
  let pushBusy = $state(false);
  let errorMsg = $state('');

  let stream: EventStream | null = null;
  let destroyed = false;

  const LABELS: Record<string, string> = {
    sound_elevated: 'Sound in the nursery',
    cry_detected: 'Possible crying',
  };
  const label = (type: string): string => LABELS[type] ?? 'Nursery activity';
  const cameraName = (id: number): string => cameraNames[id] ?? `Camera ${id}`;

  function fmtTime(iso: string): string {
    const d = new Date(iso);
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const secs = Math.round((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return `${time} · just now`;
    if (secs < 3600) return `${time} · ${Math.round(secs / 60)}m ago`;
    return time;
  }

  function mergeEvent(e: EventItem): void {
    const idx = events.findIndex((x) => x.id === e.id);
    if (idx >= 0)
      events[idx] = e; // e.g. its clip was just promoted
    else events = [e, ...events]; // a fresh nudge — prepend
  }

  function toggleClip(e: EventItem): void {
    if (e.clip_id === null) return;
    expandedId = expandedId === e.id ? null : e.id;
  }

  // ── timeline geometry ──
  // Position a timestamp as a 0–100% offset across the timeline's window.
  function pct(iso: string): number {
    if (!timeline) return 0;
    const s = Date.parse(timeline.start);
    const span = Date.parse(timeline.end) - s || 1;
    return Math.max(0, Math.min(100, ((Date.parse(iso) - s) / span) * 100));
  }
  const segWidth = (seg: { start: string; end: string }): number =>
    Math.max(0.2, pct(seg.end) - pct(seg.start));
  // Events that fall within the timeline window, overlaid as markers.
  const timelineEvents = $derived(
    timeline
      ? events.filter((e) => {
          const t = Date.parse(e.ts);
          return t >= Date.parse(timeline!.start) && t <= Date.parse(timeline!.end);
        })
      : [],
  );
  const fmtClock = (iso: string): string =>
    new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  // Scrub to an event: open its clip and bring the event card into view.
  function scrubTo(e: EventItem): void {
    if (e.clip_id === null) return;
    expandedId = e.id;
    document
      .querySelector(`[data-event-id="${e.id}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // ── notifications ──
  const pad = (n: number): string => String(n).padStart(2, '0');
  const minutesToTime = (m: number): string => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
  const timeToMinutes = (t: string): number => {
    const [h, m] = t.split(':').map(Number);
    return (h || 0) * 60 + (m || 0);
  };

  async function savePref(patch: Partial<NotificationPreferences>): Promise<void> {
    try {
      prefs = await updatePreferences(patch);
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : 'could not save settings';
    }
  }

  async function togglePush(): Promise<void> {
    if (pushBusy) return;
    pushBusy = true;
    errorMsg = '';
    try {
      if (pushActive) {
        await disablePush();
        pushActive = false;
      } else {
        pushActive = await enablePush();
        if (!pushActive)
          errorMsg = 'Notifications were not enabled (permission denied or unavailable).';
      }
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : 'could not update notifications';
    } finally {
      pushBusy = false;
    }
  }

  onMount(() => {
    void (async () => {
      const session = await fetchSession();
      if (destroyed) return; // unmounted during the session check — don't open a socket
      if (!session) {
        void goto('/');
        return;
      }
      user = session;
      ready = true;
      // Open the live stream BEFORE the initial fetch so events during the fetch window
      // aren't lost; mergeEvent dedups the overlap by id.
      stream = subscribeToEvents(mergeEvent);
      try {
        const [evts, cams, p, tl] = await Promise.all([
          fetchEvents(),
          fetchCameras().catch(() => [] as Camera[]),
          fetchPreferences().catch(() => null),
          fetchTimeline().catch(() => null),
        ]);
        for (const e of [...evts].reverse()) mergeEvent(e); // baseline, ending newest-first
        cameraNames = Object.fromEntries(cams.map((c) => [c.id, c.name]));
        prefs = p;
        timeline = tl;
      } catch (err) {
        errorMsg = err instanceof Error ? err.message : 'could not load tonight';
      }
      pushActive = (await currentSubscription()) !== null;
    })();
  });

  onDestroy(() => {
    destroyed = true;
    stream?.close();
  });
</script>

<svelte:head><title>eeper — tonight</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header>
    <a href="/" class="back" aria-label="Back">‹ eeper</a>
    <span class="title">Tonight</span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  {#if timeline && timeline.segments.length > 0}
    <section class="timeline" data-testid="timeline">
      <div class="tl-head">
        <span>Tonight's sleep</span>
        <span class="tl-range">{fmtClock(timeline.start)} – {fmtClock(timeline.end)}</span>
      </div>
      <div class="track" data-testid="timeline-track">
        {#each timeline.segments as seg (seg.start)}
          <div
            class="seg"
            class:asleep={seg.sleep === 'sleep'}
            class:distressed={seg.arousal === 'distressed'}
            data-testid="timeline-segment"
            data-sleep={seg.sleep}
            data-arousal={seg.arousal}
            style="left:{pct(seg.start)}%; width:{segWidth(seg)}%"
            title="{seg.sleep === 'sleep' ? 'Asleep' : 'Awake'}{seg.arousal === 'distressed'
              ? ' · distressed'
              : ''}"
          ></div>
        {/each}
        {#each timelineEvents as e (e.id)}
          <button
            type="button"
            class="marker"
            class:has-clip={e.clip_id !== null}
            data-testid="timeline-event"
            data-event-id={e.id}
            style="left:{pct(e.ts)}%"
            title="{label(e.type)} · {fmtClock(e.ts)}"
            aria-label="{label(e.type)} at {fmtClock(e.ts)}"
            onclick={() => scrubTo(e)}
          ></button>
        {/each}
      </div>
      <div class="legend">
        <span><i class="sw asleep"></i> asleep</span>
        <span><i class="sw awake"></i> awake</span>
        <span><i class="sw distressed"></i> distressed</span>
        <span><i class="sw dot"></i> nudge</span>
      </div>
    </section>
  {/if}

  <section class="notify" data-testid="notify-settings">
    <div class="row">
      <span>Notifications</span>
      {#if pushSupported()}
        <button
          class="toggle"
          class:on={pushActive}
          data-testid="push-toggle"
          data-active={pushActive ? '1' : '0'}
          disabled={pushBusy}
          onclick={togglePush}
        >
          {pushActive ? 'On' : 'Off'}
        </button>
      {:else}
        <span class="muted">Not supported on this browser</span>
      {/if}
    </div>
    {#if prefs}
      <label class="row quiet">
        <span>Quiet hours</span>
        <input
          type="checkbox"
          data-testid="quiet-hours-toggle"
          checked={prefs.quiet_hours_enabled}
          onchange={(e) => savePref({ quiet_hours_enabled: e.currentTarget.checked })}
        />
      </label>
      {#if prefs.quiet_hours_enabled}
        <div class="row times">
          <input
            type="time"
            data-testid="quiet-start"
            value={minutesToTime(prefs.quiet_hours_start)}
            onchange={(e) => savePref({ quiet_hours_start: timeToMinutes(e.currentTarget.value) })}
          />
          <span class="muted">to</span>
          <input
            type="time"
            data-testid="quiet-end"
            value={minutesToTime(prefs.quiet_hours_end)}
            onchange={(e) => savePref({ quiet_hours_end: timeToMinutes(e.currentTarget.value) })}
          />
        </div>
      {/if}
    {/if}
  </section>

  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}

  {#if events.length === 0}
    <p class="empty" data-testid="events-empty">A quiet night — no nudges yet.</p>
  {:else}
    <ul class="events" data-testid="event-list">
      {#each events as e (e.id)}
        <li class="event" data-testid="event" data-event-id={e.id} data-event-type={e.type}>
          <button class="head" onclick={() => toggleClip(e)} disabled={e.clip_id === null}>
            <span class="label">{label(e.type)}</span>
            <span class="sub">{cameraName(e.camera_id)} · {fmtTime(e.ts)}</span>
            {#if e.clip_id === null}<span class="pending">clip pending…</span>{/if}
          </button>
          {#if expandedId === e.id && e.clip_id !== null}
            <video
              class="clip"
              data-testid="clip-video"
              data-clip-id={e.clip_id}
              src={`/api/v1/clips/${e.clip_id}/media`}
              controls
              autoplay
              muted
              playsinline
            ></video>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
{/if}

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
  }
  .timeline {
    padding: 0.5rem 1rem 0.75rem;
  }
  .tl-head {
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    color: #cbd5ea;
    margin-bottom: 0.4rem;
  }
  .tl-range {
    color: #8a93a6;
  }
  .track {
    position: relative;
    height: 2.25rem;
    background: #17233c; /* awake = the track base */
    border: 1px solid #26314a;
    border-radius: 0.4rem;
    overflow: hidden;
  }
  .seg {
    position: absolute;
    top: 0;
    bottom: 0;
    min-width: 1px;
  }
  .seg.asleep {
    background: #1f6f4a; /* asleep band */
  }
  .seg.distressed {
    background: repeating-linear-gradient(45deg, #7a2530, #7a2530 5px, #93313d 5px, #93313d 10px);
  }
  .marker {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    width: 0.7rem;
    height: 0.7rem;
    border-radius: 50%;
    border: 2px solid #0b1220;
    background: #8a93a6;
    cursor: default;
    padding: 0;
  }
  .marker.has-clip {
    background: #f2c14e;
    cursor: pointer;
  }
  .legend {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    margin-top: 0.4rem;
    font-size: 0.72rem;
    color: #8a93a6;
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }
  .legend .sw {
    width: 0.8rem;
    height: 0.8rem;
    border-radius: 0.2rem;
    display: inline-block;
  }
  .legend .sw.asleep {
    background: #1f6f4a;
  }
  .legend .sw.awake {
    background: #17233c;
    border: 1px solid #26314a;
  }
  .legend .sw.distressed {
    background: #93313d;
  }
  .legend .sw.dot {
    background: #f2c14e;
    border-radius: 50%;
  }
  .back {
    color: #e8ecf5;
    text-decoration: none;
    font-weight: 600;
  }
  .title {
    font-weight: 600;
  }
  .who {
    color: #8a93a6;
    font-size: 0.85rem;
  }
  .loading,
  .empty {
    text-align: center;
    color: #8a93a6;
    margin: 3rem 1rem;
  }
  .notify {
    margin: 0.5rem 1rem 1rem;
    padding: 0.75rem 1rem;
    background: #131c2e;
    border: 1px solid #26314a;
    border-radius: 0.5rem;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .quiet {
    margin-top: 0.6rem;
  }
  .times {
    margin-top: 0.5rem;
    justify-content: flex-start;
    gap: 0.5rem;
  }
  .muted {
    color: #8a93a6;
    font-size: 0.85rem;
  }
  input[type='time'] {
    background: #0f1626;
    color: #e8ecf5;
    border: 1px solid #26314a;
    border-radius: 0.3rem;
    padding: 0.25rem 0.4rem;
  }
  .toggle {
    min-width: 3rem;
    padding: 0.3rem 0.7rem;
    border: 1px solid #26314a;
    border-radius: 0.4rem;
    background: #0f1626;
    color: #8a93a6;
    cursor: pointer;
  }
  .toggle.on {
    border-color: #2b6cb0;
    background: #17233c;
    color: #7ee0a6;
  }
  .events {
    list-style: none;
    margin: 0;
    padding: 0 1rem;
  }
  .event {
    border-bottom: 1px solid #1a2336;
  }
  .head {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.15rem;
    padding: 0.7rem 0;
    background: none;
    border: none;
    color: #e8ecf5;
    text-align: left;
    cursor: pointer;
  }
  .head:disabled {
    cursor: default;
  }
  .label {
    font-weight: 600;
  }
  .sub {
    color: #8a93a6;
    font-size: 0.82rem;
  }
  .pending {
    color: #6b7385;
    font-size: 0.78rem;
  }
  .clip {
    width: 100%;
    border-radius: 0.4rem;
    background: #000;
    margin: 0.25rem 0 0.75rem;
  }
  .error {
    color: #ff8f8f;
    padding: 0 1rem;
    font-size: 0.9rem;
  }
</style>
