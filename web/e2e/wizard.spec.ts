import { expect, test } from '@playwright/test';

// Runs against a FRESH stack (no admin yet), so the app opens on the first-boot
// wizard. Exercises the whole M0.2/M0.3 browser flow: create admin → signed in
// → sign out → sign in.
// The username is deliberately distinct from any role name so asserting it
// proves the username (not the role) rendered.
const USERNAME = 'primaryadmin';
const PASSWORD = 'correct horse battery staple';

test('first-boot wizard: create admin, sign out, sign in', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Create your admin account' })).toBeVisible();
  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByLabel('Confirm password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create admin' }).click();

  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
  await expect(page.locator('main strong')).toHaveText(USERNAME); // the echoed username

  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();

  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
});
