import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

// M3.3 slice 3: the Tonight timeline renders a replayed night's fused sleep/wake +
// distressed state, and scrubbing to a nudge marker plays that event's clip. Runs on
// the same core+video+record+insight stack as the `tonight` project (system Chrome for
// H.264), AFTER it — so the sound camera + a promoted clip already exist. It reuses that
// admin, then seeds a night of fused_states directly into the db (a "replayed night",
// since generating one live would take a real night) and asserts the UI.
const ADMIN = process.env.EEPER_TEST_ADMIN ?? 'tonightadmin';
const PASSWORD = 'correct horse battery staple';
const SOUND_SOURCE = process.env.EEPER_TEST_SOUND ?? 'rtsp://synthetic-camera:8554/cam-sound';
const CAMERA = 'sound';

const DEPLOY_DIR = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '../../deploy');

function pgPassword(): string {
  const env = fs.readFileSync(path.join(DEPLOY_DIR, '.env'), 'utf8');
  const line = env.split('\n').find((l) => l.startsWith('POSTGRES_PASSWORD='));
  if (!line) throw new Error('POSTGRES_PASSWORD not found in deploy/.env');
  return line.slice('POSTGRES_PASSWORD='.length).trim();
}

// Seed a replayed night into the durable fused_states log: awake → asleep → a 15-min
// distressed wake → asleep (still asleep now), ending within the timeline's window.
function seedReplayedNight(): void {
  const rows = [
    ['170 minutes', 'wake', 'calm', 0.5, 0.6],
    ['160 minutes', 'sleep', 'calm', 0.1, 0.7],
    ['60 minutes', 'wake', 'distressed', 0.8, 0.7],
    ['45 minutes', 'sleep', 'calm', 0.1, 0.7],
  ]
    .map(
      ([ago, sleep, arousal, act, conf]) =>
        `(now() - interval '${ago}', 'default', '${sleep}', '${arousal}', ${act}, ${conf}, 'sensor')`,
    )
    .join(',');
  const sql =
    'INSERT INTO fused_states (ts, household_id, sleep, arousal, activity, confidence, contributing_inputs) VALUES ' +
    rows +
    ';';
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
      sql,
    ],
    { cwd: DEPLOY_DIR, stdio: 'pipe' },
  );
}

test('Tonight timeline renders a replayed night and scrubbing to an event plays its clip', async ({
  page,
}) => {
  test.setTimeout(180_000);

  await page.goto('/');
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
      const cams = (await (await fetch('/api/v1/cameras')).json()) as { name: string }[];
      if (!cams.some((c) => c.name === CAMERA)) {
        await fetch('/api/v1/cameras', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ name: CAMERA, source_url: SOUND_SOURCE }),
        });
      }
      return true;
    },
    { ADMIN, PASSWORD, SOUND_SOURCE, CAMERA },
  );
  expect(ok, 'authenticated + camera registered').toBeTruthy();

  await page.goto('/tonight');

  // Wait for a nudge with its auto-promoted clip (the cam-sound onset drives it), so a
  // real, playable clip is available for the scrub assertion.
  const withClip = page.locator('[data-testid="event"] .head:not([disabled])').first();
  await expect(withClip).toBeVisible({ timeout: 150_000 });

  // Seed the replayed night, then reload so the timeline query picks it up.
  seedReplayedNight();
  await page.reload();

  // The track renders the night's sleep/wake bands, including the distressed span.
  await expect(page.getByTestId('timeline')).toBeVisible();
  await expect(
    page.locator('[data-testid="timeline-segment"][data-sleep="sleep"]').first(),
  ).toBeVisible();
  await expect(
    page.locator('[data-testid="timeline-segment"][data-sleep="wake"]').first(),
  ).toBeVisible();
  await expect(
    page.locator('[data-testid="timeline-segment"][data-arousal="distressed"]').first(),
  ).toBeVisible();

  // Scrub: tapping a nudge marker on the track opens and plays its clip.
  const marker = page.locator('[data-testid="timeline-event"].has-clip').first();
  await expect(marker).toBeVisible();
  await marker.click();

  const video = page.getByTestId('clip-video');
  await expect(video).toBeVisible();
  const result = await video.evaluate(async (el: HTMLVideoElement) => {
    await new Promise<void>((resolve, reject) => {
      if (el.readyState >= 2) return resolve();
      el.addEventListener('loadeddata', () => resolve(), { once: true });
      el.addEventListener('error', () => reject(new Error('video error')), { once: true });
      setTimeout(() => reject(new Error('loadeddata timeout')), 15_000);
    });
    await el.play();
    const t0 = el.currentTime;
    await new Promise((r) => setTimeout(r, 1000));
    return { readyState: el.readyState, width: el.videoWidth, advanced: el.currentTime > t0 };
  });
  expect(result.readyState, 'HAVE_CURRENT_DATA+').toBeGreaterThanOrEqual(2);
  expect(result.width, 'decoded frame width').toBeGreaterThan(0);
  expect(result.advanced, 'playback advanced').toBeTruthy();
});
