// Web Push subscription management. Enabling asks for notification permission,
// subscribes via the service worker's PushManager using the server's VAPID public
// key, and saves the subscription to the API. The service worker (static/push-sw.js)
// shows the notification when a push arrives. Everything degrades gracefully: an
// unsupported browser, a denied permission, or push disabled server-side (no VAPID)
// all just return false.

import { deletePushSubscription, fetchVapidKey, savePushSubscription } from '$lib/api';

export function pushSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof window !== 'undefined' &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

// Whether an existing subscription was created with the current server VAPID key —
// after a key rotation an old subscription can never receive our pushes.
function keyMatches(sub: PushSubscription, vapidKey: string): boolean {
  const appKey = sub.options.applicationServerKey;
  if (!appKey) return false;
  const bytes = new Uint8Array(appKey);
  let raw = '';
  for (const b of bytes) raw += String.fromCharCode(b);
  const b64 = btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return b64 === vapidKey;
}

/** Ask permission, subscribe, and persist. Returns true only if push is now active;
 * a failed persist (or a denied permission / disabled server) throws or returns false
 * so the caller never claims notifications are on when they aren't. */
export async function enablePush(): Promise<boolean> {
  if (!pushSupported()) return false;
  if ((await Notification.requestPermission()) !== 'granted') return false;
  const key = await fetchVapidKey();
  if (!key) return false; // push disabled server-side (no VAPID keypair)
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub && !keyMatches(sub, key)) {
    await sub.unsubscribe(); // stale VAPID key — resubscribe with the current one
    sub = null;
  }
  // Modern browsers accept the base64url VAPID key as a string directly.
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await savePushSubscription(sub.toJSON()); // throws on a failed persist
  return true;
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  // Remove the server row FIRST (a failure throws before we unsubscribe, so the two
  // sides stay consistent); then drop the browser subscription.
  await deletePushSubscription(sub.endpoint);
  await sub.unsubscribe();
}
