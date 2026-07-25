<script lang="ts">
  // App shell for every route: the "calm nightlight" design system, a role-aware mobile
  // bottom tab bar (grandparent mode → Live + Tonight only), and the persistent safety
  // disclaimer (the product is a monitor, never a medical device).
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { fetchPulseoxStatus, fetchSession, fetchStatus, type User } from '$lib/api';

  let { children } = $props();

  let user = $state<User | null>(null);
  let pulseoxOn = $state(false);
  // "Live-monitor lite" builds (EEPER_LITE) only serve login + camera + audio; the
  // insight/fusion/trends/devices routes don't exist, so we show just Live (+ Settings).
  let lite = $state(false);

  const path = $derived($page.url.pathname);
  const isAdmin = $derived(user?.role === 'admin');
  // The tab bar appears once signed in and off the sign-in screen.
  const showNav = $derived(!!user && path !== '/');

  type Tab = { href: string; label: string; icon: string };
  const ICON = {
    live: '<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="3"/>',
    tonight: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>',
    trends: '<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/>',
    devices: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/>',
    pulseox: '<path d="M3 12h4l2 6 4-14 2 8h6"/>',
    settings:
      '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 00-.1-1.3l2-1.6-2-3.4-2.4 1a7 7 0 00-2.3-1.3L14 2h-4l-.3 2.4a7 7 0 00-2.3 1.3l-2.4-1-2 3.4 2 1.6A7 7 0 005 12a7 7 0 00.1 1.3l-2 1.6 2 3.4 2.4-1a7 7 0 002.3 1.3L10 22h4l.3-2.4a7 7 0 002.3-1.3l2.4 1 2-3.4-2-1.6A7 7 0 0019 12z"/>',
  };
  const tabs = $derived<Tab[]>(
    lite
      ? [
          { href: '/live', label: 'Live', icon: ICON.live },
          // Settings stays for admins so a password can still be changed; the other
          // management surfaces have no backend in lite and are omitted.
          ...(isAdmin ? [{ href: '/settings', label: 'Settings', icon: ICON.settings }] : []),
        ]
      : [
          { href: '/live', label: 'Live', icon: ICON.live },
          { href: '/tonight', label: 'Tonight', icon: ICON.tonight },
          ...(isAdmin
            ? ([
                { href: '/trends', label: 'Trends', icon: ICON.trends },
                { href: '/devices', label: 'Devices', icon: ICON.devices },
                ...(pulseoxOn ? [{ href: '/pulseox', label: 'Pulse-ox', icon: ICON.pulseox }] : []),
                { href: '/settings', label: 'Settings', icon: ICON.settings },
              ] as Tab[])
            : []),
        ],
  );

  async function refresh(): Promise<void> {
    user = await fetchSession();
    if (user?.role === 'admin') {
      try {
        pulseoxOn = (await fetchPulseoxStatus()).profile_enabled;
      } catch {
        pulseoxOn = false;
      }
    }
  }

  onMount(async () => {
    // The lite flag is a build/deploy constant, so read it once (unauthenticated).
    try {
      lite = (await fetchStatus()).lite ?? false;
    } catch {
      lite = false;
    }
    await refresh();
  });
  // Re-check the session on navigation so the tab bar appears right after sign-in.
  $effect(() => {
    void path;
    void refresh();
  });
</script>

<div class="app" class:has-nav={showNav}>
  <div class="app-main">
    {@render children()}
  </div>
  <footer class="safety">Not a medical device — a sleep-insight and awareness tool only.</footer>
</div>

{#if showNav}
  <nav class="tabbar" aria-label="Primary">
    {#each tabs as tab (tab.href)}
      <a
        href={tab.href}
        class="tab"
        class:active={path === tab.href || path.startsWith(tab.href + '/')}
        aria-current={path === tab.href ? 'page' : undefined}
      >
        <!-- eslint-disable svelte/no-at-html-tags -- icons are hardcoded, trusted SVG path constants -->
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true">{@html tab.icon}</svg
        >
        <!-- eslint-enable svelte/no-at-html-tags -->
        <span>{tab.label}</span>
      </a>
    {/each}
  </nav>
{/if}

<style>
  .app {
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    /* Contain the appbar's full-bleed (negative margins) so it never adds a horizontal
       scrollbar. `clip` (unlike `hidden`) doesn't create a scroll container, so the
       sticky appbar keeps sticking to the viewport top. */
    overflow-x: clip;
  }
  .app.has-nav {
    padding-bottom: calc(66px + env(safe-area-inset-bottom));
  }
  /* Every route renders inside one centered column. On a phone it fills the screen; on a
     wider desktop/tablet it stays a centered column (matching the centered tab bar)
     instead of stretching to the left edge. The width is generous (--app-w) so the camera
     and other views stay large; text-heavy pages cap themselves narrower via .container. */
  .app-main {
    width: 100%;
    max-width: var(--app-w);
    margin-inline: auto;
  }
  .safety {
    margin-top: auto;
    text-align: center;
    color: var(--text-muted);
    font-size: var(--fs-xs);
    padding: var(--sp-5) var(--sp-4) var(--sp-4);
  }

  .tabbar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 20;
    display: flex;
    justify-content: space-around;
    gap: var(--sp-1);
    padding: 6px var(--sp-2) calc(6px + env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--bg) 78%, transparent);
    backdrop-filter: saturate(1.4) blur(16px);
    border-top: 1px solid var(--border);
  }
  .tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 6px 2px;
    min-height: var(--tap);
    border-radius: var(--r-sm);
    color: var(--text-muted);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.01em;
  }
  .tab svg {
    width: 24px;
    height: 24px;
  }
  .tab.active {
    color: var(--accent);
  }
  .tab.active svg {
    filter: drop-shadow(0 0 10px var(--accent-subtle));
  }
  @media (min-width: 720px) {
    .tabbar {
      max-width: 30rem;
      margin-inline: auto;
      border: 1px solid var(--border);
      border-radius: var(--r-pill);
      bottom: var(--sp-4);
      box-shadow: var(--shadow);
    }
  }
</style>
