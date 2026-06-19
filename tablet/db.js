/* IndexedDB store for offline field use.
 *
 * Works on plain http (unlike service workers / the Cache API, which need https
 * or localhost), so it's how the PWA stays usable when served from the ESP32 AP
 * with no internet:
 *   - fields : the GeoJSON for each field, keyed by filename, cached on load /
 *              by an explicit Sync so crews can pre-load at the yard.
 *   - state  : per-field {label: {visited, note}} placement progress, so marking
 *              shelters survives a reload with no signal.
 *   - meta   : the field index list (for the picker) and misc.
 */
"use strict";

window.beeDB = (function () {
  const NAME = "beetent";
  // tiles = cached satellite image bytes; tile_meta = key -> last-used timestamp
  // (kept separate so eviction can scan timestamps without loading the imagery).
  // shelter_scans = scanned actual placements keyed by shelter QR;
  // tray_scans = scanned trays keyed by tray QR (each carries its shelter_qr).
  const STORES = ["fields", "state", "meta", "tiles", "tile_meta",
                  "shelter_scans", "tray_scans"];
  let dbp = null;

  const createMissing = (db) => {
    for (const s of STORES) if (!db.objectStoreNames.contains(s)) db.createObjectStore(s);
  };

  function open() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      // Open at the existing version (creates v1 if brand new).
      const req = indexedDB.open(NAME);
      req.onupgradeneeded = () => createMissing(req.result);   // brand-new DB
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const db = req.result;
        if (STORES.every((s) => db.objectStoreNames.contains(s))) { resolve(db); return; }
        // DB exists but some stores are missing — bump the version to recreate
        // them (self-heals an interrupted upgrade or a malformed DB).
        const v = db.version + 1;
        db.close();
        const up = indexedDB.open(NAME, v);
        up.onupgradeneeded = () => createMissing(up.result);
        up.onsuccess = () => resolve(up.result);
        up.onerror = () => reject(up.error);
      };
    });
    return dbp;
  }

  // Run one request in a transaction; resolves with the request result.
  async function run(store, mode, make) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(store, mode);
      const req = make(t.objectStore(store));
      t.oncomplete = () => resolve(req.result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }

  // Run over multiple stores (no return value); resolves on commit.
  async function runTx(stores, mode, fn) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(stores, mode);
      fn(...stores.map((s) => t.objectStore(s)));
      t.oncomplete = () => resolve();
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }

  // LRU eviction: if the tile cache exceeds maxCount, delete the least-recently
  // used tiles down to targetCount. Scans tile_meta (timestamps only, tiny) for
  // ordering; tiles with no meta entry (legacy) sort as oldest and go first.
  async function evictTiles(maxCount, targetCount) {
    const db = await open();
    const keys = await new Promise((res, rej) => {
      const r = db.transaction("tiles", "readonly").objectStore("tiles").getAllKeys();
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
    if (keys.length <= maxCount) return 0;
    const used = await new Promise((res, rej) => {
      const m = new Map();
      const cur = db.transaction("tile_meta", "readonly").objectStore("tile_meta").openCursor();
      cur.onsuccess = () => { const c = cur.result; if (c) { m.set(c.key, c.value); c.continue(); } else res(m); };
      cur.onerror = () => rej(cur.error);
    });
    keys.sort((a, b) => (used.get(a) || 0) - (used.get(b) || 0));   // oldest first
    const drop = keys.slice(0, keys.length - targetCount);
    await runTx(["tiles", "tile_meta"], "readwrite", (tiles, meta) => {
      for (const k of drop) { tiles.delete(k); meta.delete(k); }
    });
    return drop.length;
  }

  return {
    getIndex: () => run("meta", "readonly", s => s.get("fields_index")),
    putIndex: (list) => run("meta", "readwrite", s => s.put(list, "fields_index")),
    getMeta: (key) => run("meta", "readonly", s => s.get(key)),
    putMeta: (key, val) => run("meta", "readwrite", s => s.put(val, key)),
    // Scans — keyed by QR so a re-scan updates in place (no duplicates).
    addShelterScan: (rec) => run("shelter_scans", "readwrite", s => s.put(rec, rec.shelter_qr)),
    allShelterScans: () => run("shelter_scans", "readonly", s => s.getAll()),
    addTrayScan: (rec) => run("tray_scans", "readwrite", s => s.put(rec, rec.tray_qr)),
    allTrayScans: () => run("tray_scans", "readonly", s => s.getAll()),
    getField: (file) => run("fields", "readonly", s => s.get(file)),
    putField: (file, fc) => run("fields", "readwrite", s => s.put(fc, file)),
    getState: (file) => run("state", "readonly", s => s.get(file)),
    putState: (file, map) => run("state", "readwrite", s => s.put(map, file)),
    getTile: (key) => run("tiles", "readonly", s => s.get(key)),
    putTile: (key, buf) => runTx(["tiles", "tile_meta"], "readwrite",
      (tiles, meta) => { tiles.put(buf, key); meta.put(Date.now(), key); }),
    touchTiles: (keys) => runTx(["tile_meta"], "readwrite",
      (meta) => { const now = Date.now(); for (const k of keys) meta.put(now, k); }),
    countTiles: () => run("tiles", "readonly", s => s.count()),
    evictTiles: (maxCount, targetCount) => evictTiles(maxCount, targetCount),
  };
})();
