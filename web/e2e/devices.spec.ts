import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

// Runs against a fresh core stack (db + api + the hardened MQTT broker) brought up by
// install.sh — no video profile. Asserts the M3.1 device-onboarding [AUTO] criteria:
//   1. an admin pairs a sensor node through the UI; the per-device MQTT credential is
//      shown once (username / password / topic).
//   2. publishing a reading AS that node (over TLS, from inside the broker container)
//      lands in ingestion and the node's health flips Online in the UI.
//   3. unpairing removes the node; an unauthenticated /devices redirects to sign-in.
// The admin is env-overridable to avoid a first-boot clash if suites ever co-run.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'deviceadmin';
const PASSWORD = 'correct horse battery staple'; // >= 12 chars

// The compose project lives in deploy/; publish AS a device the same way the Python
// sensor suite does — the broker has no host port, so exec mosquitto_pub inside it.
const DEPLOY_DIR = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '../../deploy');
const COMPOSE = ['compose', '-f', path.join(DEPLOY_DIR, 'docker-compose.yml'), '--profile', 'core'];

test.describe.configure({ mode: 'serial' });

function mqttPublish(user: string, password: string, topic: string, payload: string): void {
  execFileSync(
    'docker',
    [
      ...COMPOSE,
      'exec',
      '-T',
      'mqtt',
      'mosquitto_pub',
      '-h',
      '127.0.0.1',
      '-p',
      '8883',
      '--cafile',
      '/mosquitto/certs/mqtt-ca.crt',
      '-u',
      user,
      '-P',
      password,
      '-q',
      '1',
      '-t',
      topic,
      '-m',
      payload,
    ],
    { cwd: DEPLOY_DIR, stdio: 'pipe' },
  );
}

function reading(): string {
  return JSON.stringify({
    ts: Date.now() / 1000,
    type: 'movement',
    value: 0.5,
    unit: 'index',
    quality: 0.9,
  });
}

test.beforeAll(async ({ playwright, baseURL }) => {
  const ctx = await playwright.request.newContext({ baseURL, ignoreHTTPSErrors: true });
  // First-boot the admin (or log in if the stack was already provisioned by a retry).
  let res = await ctx.post('/api/v1/system/first-boot', {
    data: { username: ADMIN, password: PASSWORD },
  });
  if (res.status() === 409) {
    res = await ctx.post('/api/v1/auth/login', { data: { username: ADMIN, password: PASSWORD } });
  }
  expect(res.ok()).toBeTruthy();
  await ctx.dispose();
});

async function signIn(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign in' }).waitFor();
  await page.getByLabel('Username').fill(ADMIN);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Signed in' })).toBeVisible();
}

test('unauthenticated /devices redirects to sign-in', async ({ page }) => {
  await page.context().clearCookies();
  await page.goto('/devices');
  // The route guard bounces an unauthenticated visitor back to the sign-in root.
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});

test('pair a node via the UI, publish as it, and see its health flip Online', async ({ page }) => {
  await signIn(page);
  await page.getByRole('link', { name: 'Devices' }).click();
  await expect(page.getByRole('heading', { name: 'Paired devices' })).toBeVisible();

  // Pair a node through the form.
  await page.getByLabel('Name').fill('Crib mmWave');
  await page.getByLabel('Type').selectOption('mmwave');
  await page.getByRole('button', { name: 'Pair device' }).click();

  // The one-time credential is surfaced (never shown again).
  const creds = page.getByTestId('paired-credentials');
  await expect(creds).toBeVisible();
  const username = (await page.getByTestId('paired-username').textContent())?.trim() ?? '';
  const password = (await page.getByTestId('paired-password').textContent())?.trim() ?? '';
  expect(username).toMatch(/^dev-\d+$/);
  expect(password.length).toBeGreaterThan(0);
  const deviceId = Number(username.replace('dev-', ''));

  // The node lists as paired but never-seen until it publishes.
  const status = page.getByTestId(`device-${deviceId}-status`);
  await expect(status).toHaveText('Never seen');

  // Publish a reading AS the node over TLS; ingestion is async (batched ~1 s), so
  // reload-poll the list until the derived health flips Online.
  mqttPublish(username, password, `eeper/dev/${deviceId}/movement`, reading());
  await expect(async () => {
    await page.reload();
    await expect(page.getByTestId(`device-${deviceId}-status`)).toHaveText('Online');
  }).toPass({ timeout: 25_000, intervals: [1000, 2000, 3000] });

  // Unpair removes the node from the list.
  page.once('dialog', (d) => void d.accept()); // confirm() prompt
  await page.getByTestId(`unpair-${deviceId}`).click();
  await expect(page.getByTestId(`device-${deviceId}`)).toHaveCount(0);
});
