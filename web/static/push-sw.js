// eeper Web Push handler. This is importScripts'd into the generated Workbox service
// worker (see vite.config.ts workbox.importScripts), so the precache/offline behaviour
// is untouched and this only adds push. It shows a notification when a nudge push
// arrives — keyed on the event id (tag) so a retried push replaces rather than stacks
// the parent's notification — and focuses (or opens) the Tonight view on tap.
//
// The vocabulary is awareness, never clinical (Master Plan §2); the copy comes from
// the server, which lints it against a clinical/alarm denylist.
/* global clients */

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const title = data.title || 'eeper';
  const hasTag = data.event_id != null;
  const options = {
    body: data.body || 'Nursery activity',
    tag: hasTag ? 'e' + data.event_id : undefined,
    // renotify is only valid WITH a tag — setting it without one throws + suppresses
    // the whole notification, so a payload missing event_id would be silently dropped.
    renotify: hasTag,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url || '/tonight' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/tonight';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.includes(target) && 'focus' in w) return w.focus();
      }
      return clients.openWindow(target);
    }),
  );
});
