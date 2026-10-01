/* Service worker: caches the app shell only. API data is private and live, so /api/* is never cached. */
const CACHE = "hunter-shell-v13";
const SHELL = [
  "/", "/static/styles.css?v=web-8", "/static/app.js?v=web-8", "/i18n/vi.json", "/manifest.webmanifest",
  "/apple-touch-icon.png", "/favicon.png", "/static/icons/icon-192.png", "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;   // never cache data
  // network first (fresh deploys), fall back to the cached shell when offline
  e.respondWith(
    fetch(e.request, { cache: "no-cache" })
      .then((res) => {
        if (res.ok && (SHELL.includes(url.pathname) || SHELL.includes(url.pathname + url.search))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});
