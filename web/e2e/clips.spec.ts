import { expect, test } from '@playwright/test';

// Criterion 3 (M1.4): a promoted clip actually PLAYS in a real browser. Runs on
// system Chrome (the `clips` project pins channel:'chrome' — Playwright's bundled
// Chromium can't decode H.264). Runs against core+video+record with the recorder
// producing segments; provisions/logs in as an admin, promotes a clip via the API,
// then decodes it in a <video> and asserts frames actually advance.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'clipadmin';
const PASSWORD = 'correct horse battery staple';
const SOURCE = process.env.EEPER_TEST_SOURCE ?? 'rtsp://synthetic-camera:8554/cam';

test('a promoted clip plays in the browser', async ({ page }) => {
  await page.goto('/');

  // Authenticate in the browser context so the session cookie rides the <video> request.
  const authed = await page.evaluate(
    async ({ ADMIN, PASSWORD }) => {
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
      return res.ok;
    },
    { ADMIN, PASSWORD },
  );
  expect(authed, 'authenticated').toBeTruthy();

  const cameraId = await page.evaluate(async (source) => {
    const list = (await (await fetch('/api/v1/cameras')).json()) as { id: number }[];
    if (list.length > 0) return list[0].id;
    const reg = await fetch('/api/v1/cameras', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'nursery', source_url: source }),
    });
    if (reg.status === 201) return ((await reg.json()) as { id: number }).id;
    return ((await (await fetch('/api/v1/cameras')).json()) as { id: number }[])[0].id;
  }, SOURCE);

  // Promote a clip over a recently-finalized window; retry while segments warm up.
  const clipId = await page.evaluate(async (cid) => {
    for (let i = 0; i < 20; i++) {
      const start = new Date(Date.now() - 10_000).toISOString();
      const end = new Date(Date.now() - 4_000).toISOString();
      const res = await fetch(`/api/v1/cameras/${cid}/clips`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ start, end }),
      });
      if (res.status === 201) return ((await res.json()) as { id: number }).id;
      await new Promise((r) => setTimeout(r, 2000));
    }
    return null;
  }, cameraId);
  expect(clipId, 'clip promoted').not.toBeNull();

  const result = await page.evaluate(async (id) => {
    const video = document.createElement('video');
    video.muted = true;
    video.setAttribute('playsinline', '');
    video.src = `/api/v1/clips/${id}/media`;
    document.body.appendChild(video);
    await new Promise<void>((resolve, reject) => {
      video.addEventListener('loadeddata', () => resolve(), { once: true });
      video.addEventListener('error', () => reject(new Error('video error')), { once: true });
      setTimeout(() => reject(new Error('loadeddata timeout')), 15_000);
    });
    await video.play();
    const t0 = video.currentTime;
    await new Promise((r) => setTimeout(r, 1000));
    return {
      readyState: video.readyState,
      videoWidth: video.videoWidth,
      advanced: video.currentTime > t0,
      errorCode: video.error ? video.error.code : null,
    };
  }, clipId);

  expect(result.errorCode, 'no <video> error').toBeNull();
  expect(result.readyState, 'HAVE_CURRENT_DATA+').toBeGreaterThanOrEqual(2);
  expect(result.videoWidth, 'decoded frame has width').toBeGreaterThan(0);
  expect(result.advanced, 'playback position advanced').toBeTruthy();
});
