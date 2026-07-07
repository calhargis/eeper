import { defineConfig, devices } from '@playwright/test';

// Browser harness: drives the PWA against a running stack (default the local
// dev stack over HTTPS with the internal CA — hence ignoreHTTPSErrors). Point
// EEPER_E2E_URL elsewhere to run against another deployment.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  // The wizard spec mutates persistent server state (creates the admin), so a
  // retry would re-run against an already-provisioned stack and fail differently
  // — retrying can't recover it. install.sh health-gates the stack before tests.
  retries: 0,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: process.env.EEPER_E2E_URL ?? 'https://localhost',
    ignoreHTTPSErrors: true,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
