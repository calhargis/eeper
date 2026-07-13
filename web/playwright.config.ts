import { defineConfig, devices } from '@playwright/test';

// Browser harness: drives the PWA against a running stack (default the local
// dev stack over HTTPS with the internal CA — hence ignoreHTTPSErrors). Point
// EEPER_E2E_URL elsewhere to run against another deployment.
//
// Two projects, run in SEPARATE CI jobs against different stacks:
//   - `wizard` needs a VIRGIN stack (first-boot). It mutates persistent state
//     (creates the admin), so a retry can't recover it — retries: 0.
//   - `live` needs a PROVISIONED core+video stack (go2rtc + synthetic camera);
//     it re-signs-in per test against a stable stack, so a CI retry is safe.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: process.env.EEPER_E2E_URL ?? 'https://localhost',
    ignoreHTTPSErrors: true,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'wizard',
      testMatch: /wizard\.spec\.ts/,
      retries: 0,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'live',
      testMatch: /live\.spec\.ts/,
      retries: process.env.CI ? 1 : 0,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Clip playback (M1.4) needs H.264 decode, which Playwright's bundled
      // Chromium lacks — pin system Chrome (installed via `playwright install chrome`).
      name: 'clips',
      testMatch: /clips\.spec\.ts/,
      retries: process.env.CI ? 1 : 0,
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
    {
      // Tonight view (M2.4): a live nudge over WebSocket + its auto-promoted clip
      // playing on tap. Also needs H.264 decode (system Chrome) and a core+video+record
      // +insight stack with the cam-sound source.
      name: 'tonight',
      testMatch: /tonight\.spec\.ts/,
      retries: process.env.CI ? 1 : 0,
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
    {
      // Devices view (M3.1): pair a sensor node through the UI, publish AS it over the
      // hardened MQTT broker (from inside the broker container), and watch its health
      // flip online. Needs only the core stack (db + api + broker) — no video/H.264.
      name: 'devices',
      testMatch: /devices\.spec\.ts/,
      retries: process.env.CI ? 1 : 0,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Tonight timeline (M3.3): renders a replayed night's fused sleep/wake +
      // distressed state and scrubs to a nudge's clip. Runs after `tonight` on the same
      // core+video+record+insight stack (needs a promoted clip → system Chrome for H.264).
      name: 'timeline',
      testMatch: /timeline\.spec\.ts/,
      retries: process.env.CI ? 1 : 0,
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
  ],
});
