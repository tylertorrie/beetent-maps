/* Service worker — caches the app shell so the installed PWA launches with no
 * internet (only possible on https, e.g. GitHub Pages). Field data and satellite
 * tiles are handled separately by the app's IndexedDB cache (db.js / tiles.js),
 * so this SW deliberately does NOT shadow /fields/ or cross-origin tile requests.
 */
const CACHE = "beetent-shell-v3";   // bump to push shell changes (icons + iOS tags)
const ASSETS = [
  "./", "index.html", "app.js", "db.js", "tiles.js", "publish.js", "style.css",
  "manifest.webmanifest",
  "vendor/maplibre-gl.js", "vendor/maplibre-gl.css",
  "vendor/fonts/OpenSans/0-255.pbf",
  "fields/sample_field.geojson",
  "icon-180.png", "icon-192.png", "icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // best-effort: don't fail the whole install if one asset 404s
      .then((c) => Promise.allSettled(ASSETS.map((a) => c.add(a))))
      .then(() => self.skipWaiting())
  );
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
  // Passthrough: non-GET, cross-origin (Esri tiles, Firebase, gstatic), and the
  // field files (the app's IndexedDB cache owns offline for those).
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/fields/")) return;
  e.respondWith(
    caches.match(e.request).then((hit) => {
      if (hit) return hit;
      if (e.request.mode === "navigate") return caches.match("index.html").then((i) => i || fetch(e.request));
      return fetch(e.request);
    })
  );
});
