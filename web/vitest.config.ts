import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// Minimal unit-test harness for Svelte 5 runes logic (kept separate from the
// SvelteKit vite.config so the PWA/kit plugins don't run under the test compiler).
export default defineConfig({
  plugins: [svelte({ compilerOptions: { runes: true } })],
  test: {
    include: ['src/**/*.{test,spec}.ts'],
    environment: 'node',
  },
});
