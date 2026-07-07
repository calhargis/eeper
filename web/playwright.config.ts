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
  ],
});
