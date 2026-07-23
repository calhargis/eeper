import { expect, test, type Page } from '@playwright/test';

// Runs against a provisioned core+video stack (go2rtc + the synthetic camera).
// The suite provisions an admin, a viewer, and a registered camera via the API,
// then drives the PWA Live view. Asserts the M1.2 [AUTO] criteria:
//   1. authed user reaches Live view; getStats() shows flowing frames within 3s.
//   2. unauthenticated /live redirects to sign-in; a viewer-role user can watch.
//   3. steady-state WebRTC playout latency stays within the LAN budget.
// The source (and admin, to avoid a first-boot clash with a co-running suite) are
// env-overridable so the M1.3 adapter CI job can point this same suite at the USB
// adapter's stream; the defaults keep the M1.2 `live` job byte-identical.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'liveadmin';
const VIEWER = process.env.EEPER_TEST_VIEWER ?? 'liveviewer';
const PASSWORD = 'correct horse battery staple'; // >= 12 chars
const SOURCE = process.env.EEPER_TEST_SOURCE ?? 'rtsp://synthetic-camera:8554/cam';
const FRAME_BUDGET_MS = 3000;
const LATENCY_BUDGET_MS = 500;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ playwright, baseURL }) => {
  const ctx = await playwright.request.newContext({ baseURL, ignoreHTTPSErrors: true });
  // First-boot the admin (or log in if the stack was already provisioned).
  let res = await ctx.post('/api/v1/system/first-boot', {
    data: { username: ADMIN, password: PASSWORD },
  });
  if (res.status() === 409) {
    res = await ctx.post('/api/v1/auth/login', { data: { username: ADMIN, password: PASSWORD } });
  }
  expect(res.ok()).toBeTruthy();
  // A non-admin viewer ("grandparent mode") must also be able to watch.
  const viewer = await ctx.post('/api/v1/users', {
    data: { username: VIEWER, password: PASSWORD, role: 'viewer' },
  });
  expect([201, 409]).toContain(viewer.status());
  // Register the synthetic camera.
  const cam = await ctx.post('/api/v1/cameras', { data: { name: 'nursery', source_url: SOURCE } });
  expect([201, 409]).toContain(cam.status());
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

/** Poll the Live view's exposed frame counter until video frames flow. */
async function waitForFrames(
  page: Page,
  budgetMs: number,
): Promise<{ frames: number; ms: number }> {
  const video = page.getByTestId('live-video');
  await video.waitFor();
  const start = Date.now();
  let frames = 0;
  while (Date.now() - start < budgetMs) {
    frames = Number(await video.getAttribute('data-frames'));
    if (frames > 0) break;
    await page.waitForTimeout(150);
  }
  return { frames, ms: Date.now() - start };
}

test('authenticated user reaches Live view and frames flow within 3s', async ({ page }) => {
  await signIn(page, ADMIN);
  await page.goto('/live');
  const { frames, ms } = await waitForFrames(page, FRAME_BUDGET_MS);
  expect(
    frames,
    `expected decoded frames within ${FRAME_BUDGET_MS}ms (took ${ms}ms)`,
  ).toBeGreaterThan(0);
  await expect(page.getByTestId('live-status')).toHaveText(/LIVE/);
});

test('the Live view presents an input picker with the camera as the default input', async ({
  page,
}) => {
  await signIn(page, ADMIN);
  await page.goto('/live');
  // The redesigned Live view is an input picker; the camera is the primary input,
  // selected by default, and its live view renders without any interaction.
  await page.getByTestId('input-picker').waitFor();
  await expect(page.getByTestId('live-view')).toHaveAttribute('data-kind', 'camera');
  const camChip = page.locator('[data-testid^="input-cam-"]').first();
  await expect(camChip).toHaveAttribute('aria-pressed', 'true');
  const { frames } = await waitForFrames(page, FRAME_BUDGET_MS);
  expect(frames, 'camera view should show flowing frames by default').toBeGreaterThan(0);
});

test('listen-in: audio packets flow in the Live view', async ({ page }) => {
  // Criterion 2 (M2.1): audio is negotiated (Opus) and packets flow. The <video>
  // stays muted — mute is a local playout control only; RTP still arrives.
  await signIn(page, ADMIN);
  await page.goto('/live');
  // A video-only source (e.g. the USB adapter) has no listen-in; skip there.
  const hasAudio = await page.evaluate(async () => {
    const cams = (await (await fetch('/api/v1/cameras')).json()) as { has_audio: boolean }[];
    return cams.length > 0 && cams[0].has_audio === true;
  });
  test.skip(!hasAudio, 'source has no audio track (video-only) — listen-in not applicable');
  await waitForFrames(page, FRAME_BUDGET_MS);
  const video = page.getByTestId('live-video');

  // An audio track must be negotiated at all (else no go2rtc Opus source).
  await expect(video, 'no audio track negotiated — check the go2rtc Opus source').toHaveAttribute(
    'data-audio-track',
    '1',
    { timeout: 5000 },
  );

  // Packets must arrive and keep arriving.
  let first = 0;
  const start = Date.now();
  while (Date.now() - start < 5000) {
    first = Number(await video.getAttribute('data-audio-packets'));
    if (first > 0) break;
    await page.waitForTimeout(200);
  }
  expect(first, 'audio packets should be received').toBeGreaterThan(0);
  await page.waitForTimeout(1200);
  const second = Number(await video.getAttribute('data-audio-packets'));
  expect(second, 'audio packets should keep flowing').toBeGreaterThan(first);
});

test('steady-state playout latency stays within the LAN budget', async ({ page }) => {
  await signIn(page, ADMIN);
  await page.goto('/live');
  await waitForFrames(page, FRAME_BUDGET_MS);
  // Let the jitter buffer settle, then read the exposed average playout delay.
  const video = page.getByTestId('live-video');
  let latency = NaN;
  const start = Date.now();
  while (Date.now() - start < 3000) {
    const raw = await video.getAttribute('data-latency-ms');
    if (raw) {
      latency = Number(raw);
      break;
    }
    await page.waitForTimeout(200);
  }
  expect(Number.isFinite(latency), 'jitter-buffer latency should be reported').toBeTruthy();
  console.log(`steady-state jitter-buffer latency: ${latency}ms`);
  expect(latency).toBeLessThan(LATENCY_BUDGET_MS);
});

test('unauthenticated access to Live view redirects to sign-in', async ({ page }) => {
  await page.goto('/live');
  // adapter-static returns the shell for every path, so this is a client-side
  // guard: the Live view resolves the session and swaps to the sign-in view.
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByTestId('live-video')).toHaveCount(0);
});

test('viewer-role user can access the Live view', async ({ page }) => {
  await signIn(page, VIEWER);
  await page.goto('/live');
  const { frames } = await waitForFrames(page, FRAME_BUDGET_MS);
  expect(frames, 'viewer should see flowing frames').toBeGreaterThan(0);
});
