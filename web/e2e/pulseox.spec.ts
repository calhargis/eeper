import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

// M4.2 slice 3: the pulse-ox view (OPTIONAL, INSIGHTS-ONLY). Runs on a core stack whose
// api has EEPER_PULSEOX_PROFILE_ENABLED=true (set in the CI job env), so the profile half
// of the gate is on. The suite asserts the whole flow — the disclaimer + acknowledge, then
// the HR trend-context chart — and that the accuracy caveat is present on EVERY pulse-ox
// view (the M4.2 copy criterion). Bundled Chromium is fine (no video).
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'pulseoxadmin';
const PASSWORD = 'correct horse battery staple';

const DEPLOY_DIR = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '../../deploy');

function pgPassword(): string {
  const env = fs.readFileSync(path.join(DEPLOY_DIR, '.env'), 'utf8');
  const line = env.split('\n').find((l) => l.startsWith('POSTGRES_PASSWORD='));
  if (!line) throw new Error('POSTGRES_PASSWORD not found in deploy/.env');
  return line.slice('POSTGRES_PASSWORD='.length).trim();
}

// Seed a few hours of quality-gated pulse-ox samples so the trend chart has bars.
function seedReadings(): void {
  const insert =
    'INSERT INTO pulseox_readings (ts, household_id, device_id, hr, spo2, perfusion, quality) ' +
    "SELECT g, 'default', 1, 120 + (extract(epoch from g)::bigint % 20), 98, 4, 0.9 " +
    "FROM generate_series(now() - interval '3 hours', now() - interval '5 minutes', " +
    "interval '5 minutes') g";
  execFileSync(
    'docker',
    [
      'compose',
      '-f',
      path.join(DEPLOY_DIR, 'docker-compose.yml'),
      'exec',
      '-T',
      '-e',
      `PGPASSWORD=${pgPassword()}`,
      'db',
      'psql',
      '-U',
      'eeper',
      '-d',
      'eeper',
      '-c',
      insert,
    ],
    { cwd: DEPLOY_DIR, stdio: 'pipe' },
  );
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ playwright, baseURL }) => {
  const ctx = await playwright.request.newContext({ baseURL, ignoreHTTPSErrors: true });
  let res = await ctx.post('/api/v1/system/first-boot', {
    data: { username: ADMIN, password: PASSWORD },
  });
  if (res.status() === 409) {
    res = await ctx.post('/api/v1/auth/login', { data: { username: ADMIN, password: PASSWORD } });
  }
  expect(res.ok()).toBeTruthy();
  // Confirm the deployment actually has the profile on (the gate's first half).
  const status = await ctx.get('/api/v1/pulseox/status');
  expect(status.ok()).toBeTruthy();
  expect((await status.json()).profile_enabled).toBe(true);
  await ctx.dispose();
  seedReadings();
});

async function signIn(page: Page, username: string): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign in' }).waitFor();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
}

test('admin acknowledges the disclaimer then sees the HR trend, caveat on every view', async ({
  page,
}) => {
  await signIn(page, ADMIN);

  // The nav link only appears because the profile is enabled on this deployment.
  await page.getByRole('link', { name: 'Pulse-ox' }).click();
  await expect(page.getByTestId('pulseox')).toBeVisible();

  // View 1 — the disclaimer (not yet acknowledged). The accuracy caveat is present.
  await expect(page.getByTestId('pulseox-disclaimer')).toBeVisible();
  await expect(page.getByTestId('pulseox-caveat')).toBeVisible();
  const caveat = (await page.getByTestId('pulseox-caveat').textContent())?.trim();
  expect(caveat && caveat.length).toBeTruthy();

  // Acknowledge → the gate's second half.
  await page.getByTestId('pulseox-acknowledge').click();

  // View 2 — the HR trend context. The caveat is STILL present (every view).
  await expect(page.getByTestId('pulseox-trend')).toBeVisible();
  await expect(page.getByTestId('pulseox-caveat')).toBeVisible();
  expect((await page.getByTestId('pulseox-caveat').textContent())?.trim()).toBe(caveat);

  // The seeded samples render as trend bars (a few hours → a few hourly bars).
  await expect(page.locator('[data-testid="pulseox-hr-chart-bar"]').first()).toBeVisible();
  expect(await page.locator('[data-testid="pulseox-hr-chart-bar"]').count()).toBeGreaterThanOrEqual(
    2,
  );

  // The acknowledgment persists — a reload lands straight on the trend, caveat present.
  await page.reload();
  await expect(page.getByTestId('pulseox-trend')).toBeVisible();
  await expect(page.getByTestId('pulseox-caveat')).toBeVisible();
});
