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
  const STORES = ["fields", "state", "meta"];
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

  return {
    getIndex: () => run("meta", "readonly", s => s.get("fields_index")),
    putIndex: (list) => run("meta", "readwrite", s => s.put(list, "fields_index")),
    getField: (file) => run("fields", "readonly", s => s.get(file)),
    putField: (file, fc) => run("fields", "readwrite", s => s.put(fc, file)),
    getState: (file) => run("state", "readonly", s => s.get(file)),
    putState: (file, map) => run("state", "readwrite", s => s.put(map, file)),
  };
})();
