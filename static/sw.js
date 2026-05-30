const CACHE = "hidlink-v1";
const PRECACHE = [
  "/",
  "/static/js/chart.min.js",
  "/static/manifest.json",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  if (e.request.url.indexOf("/api/") !== -1) return;
  e.respondWith(
    caches.open(CACHE).then((c) =>
      c.match(e.request).then((r) => r || fetch(e.request))
    )
  );
});
