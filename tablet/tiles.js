/* Offline satellite tiles.
 *
 * Registers a MapLibre `beetile://` protocol that serves raster tiles cache-first:
 *   1. IndexedDB (tiles store)  → instant, works offline
 *   2. network (Esri imagery)   → when online; the fetched tile is cached too
 *   3. transparent tile         → offline + uncached, so the green bg shows through
 *
 * `cacheFieldTiles()` pre-downloads every tile covering a field's bbox over the
 * zoom range, so a crew that taps Sync at the yard has full imagery in the field.
 * The map source uses tiles: ["beetile://{z}/{x}/{y}"] (see app.js).
 */
"use strict";

window.beeTiles = (function () {
  const ZMIN = 13, ZMAX = 19;          // Esri native max = 19; MapLibre overzooms past it
  const MAX_TILES_PER_FIELD = 2000;    // safety cap (~30 MB) per field
  const CONCURRENCY = 6;

  // 1x1 transparent PNG, used when a tile is missing offline.
  const TRANSPARENT = Uint8Array.from(
    atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="),
    (c) => c.charCodeAt(0)).buffer;

  const tileUrl = (z, x, y) =>
    `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;

  const lon2tile = (lon, z) => Math.floor((lon + 180) / 360 * Math.pow(2, z));
  const lat2tile = (lat, z) => {
    const r = (lat * Math.PI) / 180;
    return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * Math.pow(2, z));
  };

  function bboxOf(fc) {
    let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90, any = false;
    const visit = ([lo, la]) => {
      if (lo < minLon) minLon = lo; if (lo > maxLon) maxLon = lo;
      if (la < minLat) minLat = la; if (la > maxLat) maxLat = la; any = true;
    };
    const walk = (g) => {
      if (!g) return;
      if (g.type === "Point") visit(g.coordinates);
      else if (g.type === "Polygon") g.coordinates.forEach((r) => r.forEach(visit));
      else if (g.type === "LineString") g.coordinates.forEach(visit);
    };
    (fc.features || []).forEach((f) => walk(f.geometry));
    return any ? { minLon, minLat, maxLon, maxLat } : null;
  }

  function tileList(fc) {
    const b = bboxOf(fc);
    if (!b) return [];
    const out = [];
    for (let z = ZMIN; z <= ZMAX; z++) {
      const x0 = lon2tile(b.minLon, z), x1 = lon2tile(b.maxLon, z);
      const y0 = lat2tile(b.maxLat, z), y1 = lat2tile(b.minLat, z);  // higher lat = lower y
      for (let x = x0; x <= x1; x++)
        for (let y = y0; y <= y1; y++) out.push(`${z}/${x}/${y}`);
      if (out.length > MAX_TILES_PER_FIELD) return out.slice(0, MAX_TILES_PER_FIELD);
    }
    return out;
  }

  function registerProtocol() {
    if (typeof maplibregl === "undefined" || !maplibregl.addProtocol) return;
    maplibregl.addProtocol("beetile", async (params) => {
      const p = params.url.replace("beetile://", "").split("/");
      const z = +p[0], x = +p[1], y = +p[2], key = `${z}/${x}/${y}`;
      let buf = null;
      try { buf = await beeDB.getTile(key); } catch (e) { /* ignore */ }
      if (buf) return { data: buf };
      if (navigator.onLine) {
        try {
          const r = await fetch(tileUrl(z, x, y));
          if (r.ok) {
            const ab = await r.arrayBuffer();
            beeDB.putTile(key, ab).catch(() => {});   // opportunistically cache
            return { data: ab };
          }
        } catch (e) { /* fall through to transparent */ }
      }
      return { data: TRANSPARENT.slice(0) };
    });
  }

  // Download + store every tile covering a field, skipping already-cached ones.
  async function cacheFieldTiles(fc, onProgress) {
    const keys = tileList(fc);
    let done = 0, stored = 0, i = 0;
    async function one(key) {
      try {
        const have = await beeDB.getTile(key).catch(() => null);
        if (!have) {
          const [z, x, y] = key.split("/").map(Number);
          const r = await fetch(tileUrl(z, x, y));
          if (r.ok) { await beeDB.putTile(key, await r.arrayBuffer()); stored++; }
        }
      } catch (e) { /* skip this tile */ }
      done++;
      if (onProgress) onProgress(done, keys.length);
    }
    async function worker() { while (i < keys.length) await one(keys[i++]); }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    return { total: keys.length, stored };
  }

  return { registerProtocol, cacheFieldTiles, tileCount: () => beeDB.countTiles() };
})();
