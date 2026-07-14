import { expect, test, type Page } from '@playwright/test';

// M4.3 role sweep: "grandparent mode". A viewer is scoped to Live + Tonight only — the
// home surfaces just those two, every management route (Trends / Devices / Pulse-ox /
// Settings) redirects a viewer back to Tonight, and the CSV export API denies them. An
// admin reaches every surface. Runs on a fresh core stack; bundled Chromium is fine.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'rolesadmin';
const VIEWER = process.env.EEPER_TEST_VIEWER ?? 'rolesviewer';
const PASSWORD = 'correct horse battery staple';

const ADMIN_ROUTES = ['/trends', '/devices', '/pulseox', '/settings'];

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
});

async function signIn(page: Page, username: string): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign in' }).waitFor();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
}

test('a viewer is scoped to Live + Tonight, denied every management route', async ({ page }) => {
  await signIn(page, VIEWER);

  // The home shows only Live + Tonight — no management links.
  await expect(page.getByRole('link', { name: 'Open live view' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Tonight' })).toBeVisible();
  for (const name of ['Trends', 'Devices', 'Pulse-ox', 'Settings']) {
    await expect(page.getByRole('link', { name })).toHaveCount(0);
  }

  // The two allowed routes load for a viewer.
  await page.goto('/tonight');
  await expect(page).toHaveURL(/\/tonight$/);
  await page.goto('/live');
  await expect(page).toHaveURL(/\/live$/);

  // Every management route bounces a viewer back to Tonight.
  for (const route of ADMIN_ROUTES) {
    await page.goto(route);
    await expect(page, `viewer should be redirected from ${route}`).toHaveURL(/\/tonight$/);
  }

  // And the CSV export API denies them (server-side gate, using the viewer's cookies).
  const res = await page.request.get('/api/v1/trends/export.csv');
  expect(res.status()).toBe(403);
});

test('an admin reaches every management surface', async ({ page }) => {
  await signIn(page, ADMIN);

  for (const name of ['Trends', 'Devices', 'Settings']) {
    await expect(page.getByRole('link', { name })).toBeVisible();
  }

  await page.goto('/settings');
  await expect(page.getByTestId('settings')).toBeVisible();
  await expect(page.getByTestId('settings-account')).toBeVisible();

  await page.goto('/trends');
  await expect(page).toHaveURL(/\/trends$/);
  await page.goto('/devices');
  await expect(page).toHaveURL(/\/devices$/);
  await expect(page.getByTestId('pair-form')).toBeVisible(); // admin-only pairing UI
});
