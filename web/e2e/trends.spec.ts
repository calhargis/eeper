import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

// M4.1 slice 3: the Trends view renders sleep-duration / wake-count charts for a seeded
// month, an admin can export CSV, and a viewer role sees the charts but no export button.
// Runs on a fresh core stack (db is TimescaleDB, so the trends_nightly continuous
// aggregate exists) — no video, so the bundled Chromium is fine.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'trendsadmin';
const VIEWER = process.env.EEPER_TEST_VIEWER ?? 'trendsviewer';
const PASSWORD = 'correct horse battery staple';

const DEPLOY_DIR = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '../../deploy');

function pgPassword(): string {
  const env = fs.readFileSync(path.join(DEPLOY_DIR, '.env'), 'utf8');
  const line = env.split('\n').find((l) => l.startsWith('POSTGRES_PASSWORD='));
  if (!line) throw new Error('POSTGRES_PASSWORD not found in deploy/.env');
  return line.slice('POSTGRES_PASSWORD='.length).trim();
}

// Seed a month of nightly sessions, then materialize the continuous aggregate (the
// refresh must be its own command — it can't run in a multi-statement string).
function seedTrends(): void {
  const insert =
    'INSERT INTO sleep_sessions (started_at, household_id, ended_at, total_sleep_s, ' +
    "wake_count, longest_stretch_s) SELECT d, 'default', d + interval '8 hours', " +
    '28800 + (extract(epoch from d)::bigint % 3600), (extract(epoch from d)::bigint % 4), ' +
    '14400 + (extract(epoch from d)::bigint % 1800) ' +
    "FROM generate_series(now() - interval '30 days', now(), interval '1 day') d";
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
      '-c',
      "CALL refresh_continuous_aggregate('trends_nightly', NULL, NULL)",
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
  const viewer = await ctx.post('/api/v1/users', {
    data: { username: VIEWER, password: PASSWORD, role: 'viewer' },
  });
  expect([201, 409]).toContain(viewer.status());
  await ctx.dispose();
  seedTrends();
});

async function signIn(page: Page, username: string): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign in' }).waitFor();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
}

test('admin sees sleep-trends charts and can export CSV', async ({ page }) => {
  await signIn(page, ADMIN);
  await page.getByRole('link', { name: 'Trends' }).click();
  await expect(page.getByTestId('trends')).toBeVisible();

  // Charts render one bar per seeded night (~30).
  await expect(page.locator('[data-testid="sleep-chart-bar"]').first()).toBeVisible();
  expect(await page.locator('[data-testid="sleep-chart-bar"]').count()).toBeGreaterThanOrEqual(28);
  await expect(page.getByTestId('wakes-chart')).toBeVisible();

  // The admin can export, and the download is the CSV of the same data.
  const link = page.getByTestId('export-csv');
  await expect(link).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  const file = await download.path();
  const csv = fs.readFileSync(file, 'utf8').trim().split(/\r?\n/); // CSV line terminator is CRLF
  expect(csv[0]).toBe('night,sessions,total_sleep_hours,wakes,longest_stretch_hours');
  expect(csv.length - 1).toBeGreaterThanOrEqual(28); // one row per night
});

test('a viewer has no Trends link and is redirected away from /trends', async ({ page }) => {
  // Grandparent mode (M4.3): viewers are scoped to Live + Tonight. Trends is admin-only —
  // no nav link, and a direct visit bounces to Tonight. (The full role sweep lives in
  // roles.spec.ts; this keeps the Trends suite honest about the gating.)
  await signIn(page, VIEWER);
  await expect(page.getByRole('link', { name: 'Trends' })).toHaveCount(0);
  await page.goto('/trends');
  await expect(page).toHaveURL(/\/tonight$/);
  await expect(page.getByTestId('export-csv')).toHaveCount(0);
});
