/* Node harness for the tablet's "other crews" feature (no browser needed).
 *
 * Exercises the two pieces that carry real logic:
 *   1. beePublish.onCrews  — self-exclusion, stale-node drop, bad-coord drop
 *   2. refreshCrewLayer    — Work mode = same field only, Map mode = all crews,
 *                            plus the "name placed/total" label
 * Both are pulled from the SHIPPED files so the test can't drift from the app.
 * Prints JSON results; tests/test_tablet_crews.py asserts on them.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import vm from "vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);
const TABLET = path.join(ROOT, "tablet");

const results = {};

// ── 1. publish.js onCrews ───────────────────────────────────────────────────
let crewHandler = null;
const fakeRef = {
  onDisconnect: () => ({ remove: () => {} }),
  on: (evt, cb) => { crewHandler = cb; return cb; },
  off: () => {},
  child: () => fakeRef,
  set: () => Promise.resolve(),
};
const sandbox = {
  console: { info() {}, warn() {}, log() {} },
  setInterval: () => 0,
  setTimeout: () => 0,
  Date,
  Math,
  JSON,
  String,
  localStorage: {
    _d: { beeCrewId: "crew-me", beeCrewName: "Me" },
    getItem(k) { return this._d[k] ?? null; },
    setItem(k, v) { this._d[k] = String(v); },
  },
  firebase: {
    initializeApp: () => {},
    database: () => ({ ref: () => fakeRef }),
  },
};
sandbox.window = sandbox;
sandbox.window.FIREBASE_CONFIG = { fake: true };
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(TABLET, "publish.js"), "utf8"), sandbox);
sandbox.window.beePublish._init();

results.relayEnabled = sandbox.window.beePublish.enabled;

let delivered = null;
const unsub = sandbox.window.beePublish.onCrews((list) => { delivered = list; });
results.onCrewsReturnsUnsub = typeof unsub === "function";

const now = Date.now() / 1000;
const rows = [
  { id: "crew-me", name: "Me", lat: 49.78, lon: -112.2, ts: now, field_file: "A.geojson" },      // self → drop
  { id: "crew-b", name: "Bravo", lat: 49.781, lon: -112.201, ts: now, field_file: "A.geojson",
    placed: 12, total: 40 },                                                                      // keep
  { id: "crew-c", name: "Charlie", lat: 49.79, lon: -112.19, ts: now - 600, field_file: "A.geojson" }, // stale → drop
  { id: "crew-d", name: "Delta", lat: null, lon: -112.19, ts: now, field_file: "A.geojson" },      // bad coords → drop
  { id: "crew-e", name: "Echo", lat: 49.60, lon: -112.40, ts: now, field_file: "B.geojson" },      // other field
];
crewHandler({ forEach: (fn) => rows.forEach((r) => fn({ val: () => r })) });
results.deliveredIds = (delivered || []).map((c) => c.id);

// ── 2. app.js refreshCrewLayer ──────────────────────────────────────────────
const appSrc = fs.readFileSync(path.join(TABLET, "app.js"), "utf8");
const m = appSrc.match(/function refreshCrewLayer\(\) \{[\s\S]*?\n\}/);
if (!m) { console.log(JSON.stringify({ error: "refreshCrewLayer not found" })); process.exit(1); }

let lastData = null;
const ctx = {
  _crewsLatest: delivered || [],
  mode: "work",
  activeFieldFile: "A.geojson",
  map: {
    getSource: (id) => (id === "crews"
      ? { setData: (d) => { lastData = d; } } : null),
  },
  document: { getElementById: () => null },
  console,
};
vm.createContext(ctx);
vm.runInContext(m[0], ctx);

// Work mode, field A → only crews on A
ctx.refreshCrewLayer();
results.workModeLabels = lastData.features.map((f) => f.properties.label);

// Work mode, a different field → none
ctx.activeFieldFile = "Z.geojson";
ctx.refreshCrewLayer();
results.workModeOtherField = lastData.features.length;

// Map mode → every active crew regardless of field
ctx.mode = "map";
ctx.refreshCrewLayer();
results.mapModeCount = lastData.features.length;
results.mapModeCoords = lastData.features.map((f) => f.geometry.coordinates);

// unsubscribe must not throw
unsub();
results.unsubOk = true;

console.log(JSON.stringify(results, null, 2));
