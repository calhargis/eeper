import { sveltekit } from '@sveltejs/kit/vite';
import { SvelteKitPWA } from '@vite-pwa/sveltekit';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [
    sveltekit(),
    // PWA: a Workbox service worker precaches the built app shell so the PWA is
    // installable and loads offline. registration is done explicitly in app.html
    // (injectRegister: null) so it is deterministic under our CSP.
    SvelteKitPWA({
      strategies: 'generateSW',
      registerType: 'autoUpdate',
      injectRegister: null,
      // adapterFallback must match svelte.config.js `fallback: 'index.html'` so
      // the plugin precaches the SPA shell — otherwise navigateFallback points at
      // a non-precached URL and the installed PWA fails to load offline.
      kit: { spa: true, adapterFallback: '/index.html' },
      manifest: {
        name: 'eeper',
        short_name: 'eeper',
        description: 'Self-hosted baby monitor — a sleep-insight and awareness tool.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#0b1220',
        theme_color: '#0b1220',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icons/icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precache the built client assets + the SPA shell only. Never the API:
        // the SDP relay is a POST (uncacheable) and navigations must reach it live.
        globPatterns: ['**/*.{js,css,html,ico,png,svg,webmanifest}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
      },
    }),
  ],
});
