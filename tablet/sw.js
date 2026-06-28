/* Service worker — keeps the installed PWA launchable offline (https only, e.g.
 * GitHub Pages). Field data and satellite tiles are owned by the app's IndexedDB
 * cache (db.js / tiles.js), so this SW deliberately does NOT shadow /fields/ or
 * cross-origin tile requests.
 *
 * Strategy: NETWORK-FIRST for the app shell (with a short timeout → cache
 * fallback). Cache-first previously meant a stale index.html / app.js could be
 * served indefinitely; any version skew between them then froze the app. Network
 * -first guarantees an online launch gets the latest, consistent code, while a
 * field launch (offline) still falls back to the cached shell.
 */
const CACHE = "beetent-shell-v8";   // bump to force a fresh atomic re-cache
const NET_TIMEOUT = 3500;           // ms before a slow network falls back to cache
const ASSETS = [
  "./", "index.html", "app.js", "db.js", "tiles.js", "publish.js", "style.css",
  "manifest.webmanifest",
  "vendor/maplibre-gl.js", "vendor/maplibre-gl.css",
  "vendor/jsQR.js",
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

function networkFirst(request) {
  const cacheFallback = () => caches.match(request).then((hit) =>
    hit || (request.mode === "navigate" ? caches.match("index.html") : undefined));
  return new Promise((resolve) => {
    let settled = false;
    const done = (r) => { if (!settled && r) { settled = true; resolve(r); } };
    // cap the network wait so a flaky signal can't hang the launch
    const t = setTimeout(() => cacheFallback().then(done), NET_TIMEOUT);
    fetch(request).then((resp) => {
      clearTimeout(t);
      if (resp && resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
        done(resp);
      } else {
        cacheFallback().then((c) => { if (c) done(c); else { settled = true; resolve(resp); } });
      }
    }).catch(() => {
      clearTimeout(t);
      cacheFallback().then((c) => { if (c) done(c); else { settled = true; resolve(Response.error()); } });
    });
  });
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Passthrough: non-GET, cross-origin (Esri tiles, Firebase, gstatic), and the
  // field files (the app's IndexedDB cache owns offline for those).
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/fields/")) return;
  e.respondWith(networkFirst(e.request));
});
