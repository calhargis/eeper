import { expect, test } from '@playwright/test';

// M2.4 (web): the Tonight view shows a nudge live over the WebSocket — no reload — and
// its auto-promoted clip plays in the browser on tap. Runs on system Chrome (the
// `tonight` project pins channel:'chrome' for H.264 decode) against core+video+record
// +insight with the synthetic cam-sound source firing sustained-sound onsets; the
// api-side nudge worker auto-promotes the clip. The Web Push subscription flow itself
// (real OS push) is the [MANUAL] bench — here we assert the settings UI renders.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'tonightadmin';
const PASSWORD = 'correct horse battery staple';
const SOUND_SOURCE = process.env.EEPER_TEST_SOUND ?? 'rtsp://synthetic-camera:8554/cam-sound';
// The camera name is load-bearing, not cosmetic: a camera's source_url is unique, and
// the insight suites that run after this harness in the same job register cam-sound as
// "sound" and recover from the resulting 409 by looking the source up *by name*. Using
// any other name here registers cam-sound first, so that name lookup finds nothing and
// the downstream suites fail. Keep this the canonical "sound".
const CAMERA = 'sound';

test('a live nudge appears in Tonight and its clip plays', async ({ page }) => {
  test.setTimeout(180_000); // a real onset + post-roll + recorder finalization

  await page.goto('/');
  // Authenticate in-browser (so the session cookie rides the WS + <video>) and make
  // sure the sound camera is registered.
  const ok = await page.evaluate(
    async ({ ADMIN, PASSWORD, SOUND_SOURCE, CAMERA }) => {
      let res = await fetch('/api/v1/system/first-boot', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username: ADMIN, password: PASSWORD }),
      });
      if (res.status === 409) {
        res = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ username: ADMIN, password: PASSWORD }),
        });
      }
      if (!res.ok) return false;
      const cams = (await (await fetch('/api/v1/cameras')).json()) as {
        name: string;
      }[];
      if (!cams.some((c) => c.name === CAMERA)) {
        await fetch('/api/v1/cameras', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            name: CAMERA,
            source_url: SOUND_SOURCE,
          }),
        });
      }
      return true;
    },
    { ADMIN, PASSWORD, SOUND_SOURCE, CAMERA },
  );
  expect(ok, 'authenticated + camera registered').toBeTruthy();

  await page.goto('/tonight');

  // The notifications settings UI renders (push opt-in lives here).
  await expect(page.getByTestId('push-toggle')).toBeVisible();

  // A sustained-sound nudge appears live (WebSocket-driven, no reload).
  const firstEvent = page.getByTestId('event').first();
  await expect(firstEvent).toBeVisible({ timeout: 90_000 });
  await expect(firstEvent).toHaveAttribute('data-event-type', 'sound_elevated');

  // Wait until some event has its auto-promoted clip (its head button becomes enabled
  // when clip_id lands via the clip-ready broadcast), then tap it.
  const withClip = page.locator('[data-testid="event"] .head:not([disabled])').first();
  await expect(withClip).toBeVisible({ timeout: 120_000 });
  await withClip.click();

  // The clip plays in the browser.
  const video = page.getByTestId('clip-video');
  await expect(video).toBeVisible();
  const result = await video.evaluate(async (el: HTMLVideoElement) => {
    await new Promise<void>((resolve, reject) => {
      if (el.readyState >= 2) return resolve();
      el.addEventListener('loadeddata', () => resolve(), { once: true });
      el.addEventListener('error', () => reject(new Error('video error')), {
        once: true,
      });
      setTimeout(() => reject(new Error('loadeddata timeout')), 15_000);
    });
    await el.play();
    const t0 = el.currentTime;
    await new Promise((r) => setTimeout(r, 1000));
    return {
      readyState: el.readyState,
      width: el.videoWidth,
      advanced: el.currentTime > t0,
    };
  });
  expect(result.readyState, 'HAVE_CURRENT_DATA+').toBeGreaterThanOrEqual(2);
  expect(result.width, 'decoded frame width').toBeGreaterThan(0);
  expect(result.advanced, 'playback advanced').toBeTruthy();
});
