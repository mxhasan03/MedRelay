// MedRelay courier PWA service worker (Phase 5).
//
// Scope, honestly stated: this worker provides cache-first serving for the
// static shell assets listed in PRECACHE_URLS below, so the courier PWA's
// JS/manifest/icon shell loads even with no connectivity. It deliberately
// does NOT cache server-rendered HTML pages or any of the courier action
// endpoints (job offer accept/decline, status advance, package scan,
// location ping) — those are inherently dynamic/state-mutating, and offline
// handling for them is the offline event queue's job
// (static/js/offline-queue.js: queue locally, retry with an idempotency key
// once back online), not this service worker's. This is a real, working
// cache-first static-asset cache — not a full offline-app-shell (no
// navigation/page caching) — see docs/CURRENT_STATUS.md "Phase 5" for the
// exact honesty statement on what was/wasn't verified with a real browser.

const CACHE_NAME = "medrelay-courier-shell-v1";
const PRECACHE_URLS = [
  "/static/manifest.json",
  "/static/icons/icon.svg",
  "/static/js/courier.js",
  "/static/js/offline-queue.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isCacheableStaticAsset =
    event.request.method === "GET" && url.pathname.startsWith("/static/");
  if (!isCacheableStaticAsset) {
    return; // let the browser handle everything else (dynamic pages/APIs) normally
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        return cached; // cache-first
      }
      return fetch(event.request).then((response) => {
        if (response && response.ok) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone));
        }
        return response;
      });
    })
  );
});
