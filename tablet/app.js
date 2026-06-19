/* Bee Tent Field PWA — scaffold.
 *
 * Responsibilities:
 *   1. Connect to the position source (ESP32 WebSocket in the field; the bundled
 *      simulator during development) and keep a live position + heading.
 *   2. Render a field (boundary + shelter points) from GeoJSON.
 *   3. Two views: Overview (whole field) and Ground (locked to position, zoomed in).
 *   4. Mark shelters placed / add notes, stored locally (IndexedDB later; in-memory
 *      for the scaffold).
 *
 * It deliberately does NO geometry math — shelter positions are computed by the
 * desktop app and baked into the GeoJSON.
 */
"use strict";

// ---- Config -----------------------------------------------------------------
// The ESP32 serves this page over http and streams position on this WS port.
// Same host as the page so it works whether on the ESP32 AP or the dev sim.
const WS_URL = `ws://${location.hostname || "localhost"}:8081`;
const FIELDS_INDEX = "fields/index.json";   // manifest the desktop app maintains
const GROUND_ZOOM = 21;

// Esri World Imagery — free satellite raster for the scaffold. Real deployment
// falls back to vector-only (no base) when offline on the ESP32 AP.
const SATELLITE = {
  version: 8,
  // Vendored font (tablet/vendor/fonts) so shelter labels render offline. The
  // {fontstack} matches the "text-font" on the labels layer below ("OpenSans").
  glyphs: "vendor/fonts/{fontstack}/{range}.pbf",
  sources: {
    esri: {
      type: "raster",
      // Served via the beetile:// protocol (tiles.js): cache-first from IndexedDB,
      // network fallback, so imagery works offline once cached.
      tiles: ["beetile://{z}/{x}/{y}"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Esri World Imagery",
    },
  },
  layers: [
    // Solid base so the map shows a field-green backdrop (not blank) when
    // satellite tiles can't load offline; geometry layers still render on top.
    { id: "bg", type: "background", paint: { "background-color": "#1b2b1b" } },
    { id: "sat", type: "raster", source: "esri" },
  ],
};

// ---- State ------------------------------------------------------------------
let map;
let meMarker;            // live position marker
let pos = null;          // last position object from the source
let mode = "work";        // "work" (active field) | "map" (all synced fields)
let activeField = null;   // GeoJSON FeatureCollection
let labelMode = "number"; // shelter pin labels: "number" | "trays"
let proxShelter = null;   // label of the shelter we're currently within 10 ft of
const visited = {};       // label -> {visited, note}  (local only for now)

// Position source: prefer the globe (ESP32/sim over WebSocket); fall back to the
// tablet's own GPS when the globe goes quiet. The top-bar pill shows which.
let posSource = "none";          // "globe" | "tablet" | "none"
let lastGlobeTs = 0, lastTabletTs = 0, lastAnyTs = 0;
let geoState = "off";            // "off" | "ok" | "denied" | "unavailable"
const GLOBE_STALE_MS = 4000;     // globe considered lost after this gap

// ---- Map setup --------------------------------------------------------------
function initMap() {
  if (window.beeTiles) beeTiles.registerProtocol();   // must precede map creation
  map = new maplibregl.Map({
    container: "map",
    style: SATELLITE,
    center: [-113.4912, 53.5461],
    zoom: 14,
    attributionControl: false,
  });

  const el = document.createElement("div");
  el.className = "me-arrow";
  meMarker = new maplibregl.Marker({ element: el, rotationAlignment: "map" });

  map.on("load", () => {
    map.addSource("field", { type: "geojson", data: emptyFC() });

    map.addLayer({ id: "boundary-line", type: "line",
      filter: ["==", ["get", "type"], "boundary"],
      source: "field", paint: { "line-color": "#FFD700", "line-width": 2 } });

    map.addLayer({ id: "tracks-line", type: "line",
      filter: ["==", ["get", "type"], "pivot_track"],
      source: "field",
      paint: { "line-color": "#FF6600", "line-width": 1.5, "line-opacity": 0.85 } });

    map.addLayer({ id: "shelters", type: "circle",
      filter: ["==", ["get", "type"], "shelter"],
      source: "field",
      paint: {
        "circle-radius": 7,
        "circle-color": ["case", ["get", "visited"], "#1faa59", "#FFD700"],
        "circle-stroke-color": "#000", "circle-stroke-width": 1.5,
      } });

    map.addLayer({ id: "shelter-labels", type: "symbol",
      filter: ["==", ["get", "type"], "shelter"],
      source: "field",
      layout: { "text-field": ["get", "label"], "text-font": ["OpenSans"],
                "text-size": 11, "text-offset": [0, 1.2] },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.2 } });

    map.on("click", "shelters", (e) => openPoint(e.features[0]));

    // All-fields overview ("Map" view): every synced field's boundary + name.
    map.addSource("allfields", { type: "geojson", data: emptyFC() });
    map.addLayer({ id: "allfields-fill", type: "fill",
      filter: ["==", ["get", "type"], "boundary"],
      source: "allfields", paint: { "fill-color": "#FFD700", "fill-opacity": 0.08 } });
    map.addLayer({ id: "allfields-line", type: "line",
      filter: ["==", ["get", "type"], "boundary"],
      source: "allfields", paint: { "line-color": "#FFD700", "line-width": 2 } });
    map.addLayer({ id: "allfields-label", type: "symbol",
      filter: ["==", ["get", "type"], "label"],
      source: "allfields",
      layout: { "text-field": ["get", "name"], "text-font": ["OpenSans"],
                "text-size": 14, "text-allow-overlap": true },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.5 } });
    map.on("click", "allfields-fill", (e) => {
      const f = e.features[0];
      if (f && f.properties.file) loadField("fields/" + f.properties.file, f.properties.name);
      setMode("work");
    });

    loadFieldList();
    applyLabelMode();
    setMode("work");
  });
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

// ---- Position source --------------------------------------------------------
// The globe (or the dev simulator) over WebSocket.
function connectPosition() {
  let ws;
  try { ws = new WebSocket(WS_URL); }
  catch (e) { setTimeout(connectPosition, 2000); return; }
  ws.onmessage = (ev) => {
    try { onPosition(JSON.parse(ev.data), "globe"); } catch (e) { /* junk frame */ }
  };
  ws.onclose = () => setTimeout(connectPosition, 2000);
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

// The tablet's built-in GPS — the fallback when the globe isn't connected.
// NOTE: browsers only allow geolocation in a SECURE context (https or localhost),
// so this stays denied when the app is served over plain http (e.g. an ESP32 AP).
function startGeo() {
  if (!navigator.geolocation) { geoState = "unavailable"; return; }
  navigator.geolocation.watchPosition(
    (gp) => {
      geoState = "ok";
      const h = gp.coords.heading;
      onPosition({
        lat: gp.coords.latitude, lon: gp.coords.longitude,
        course: (h != null && !isNaN(h)) ? h : undefined,
        acc: gp.coords.accuracy, sats: null, hdop: null,
      }, "tablet");
    },
    (err) => { geoState = (err && err.code === 1) ? "denied" : "unavailable"; },
    { enableHighAccuracy: true, maximumAge: 1000, timeout: 12000 }
  );
}

function onPosition(p, source) {
  const now = Date.now();
  if (source === "globe") lastGlobeTs = now;
  else if (source === "tablet") {
    lastTabletTs = now;
    if (now - lastGlobeTs < GLOBE_STALE_MS) return;   // globe wins while it's fresh
  }
  lastAnyTs = now;
  posSource = source;

  pos = p;
  meMarker.setLngLat([p.lon, p.lat]);
  if (typeof p.course === "number" && !isNaN(p.course)) meMarker.setRotation(p.course);
  if (!meMarker._map) meMarker.addTo(map);

  updateStatus(p, source);
  if (window.beePublish) window.beePublish.setPos(p);
  if (mode === "work") checkProximity(p);
}

function updateStatus(p, source) {
  const src = document.getElementById("srcpill");
  if (source === "globe") {
    src.textContent = "\u{1F6F0} GLOBE"; src.className = "src-globe";
    setFix(p.fix);                                   // RTK FIX / FLOAT / etc.
  } else {
    src.textContent = "\u{1F4F1} TABLET GPS"; src.className = "src-tablet";
    const fb = document.getElementById("fixbadge");
    fb.className = "fix-tablet";
    fb.textContent = (p.acc != null) ? `±${Math.round(p.acc)} m` : "GPS";
  }
  document.getElementById("sats").textContent = (p.sats ?? "—");
  document.getElementById("hdop").textContent = (p.hdop ?? "--");
}

// Bring up tablet GPS once the globe goes quiet; flag total signal loss.
function statusWatchdog() {
  const now = Date.now();
  if (now - lastGlobeTs > GLOBE_STALE_MS && geoState === "off") startGeo();
  if (now - lastAnyTs > GLOBE_STALE_MS + 2000) {
    posSource = "none";
    const src = document.getElementById("srcpill");
    src.textContent = "— NO SIGNAL"; src.className = "src-none";
    setFix(null);
  }
  setTimeout(statusWatchdog, 1500);
}

function setFix(fix) {
  const b = document.getElementById("fixbadge");
  const map_ = { 4: ["RTK FIX", "fix-rtk"], 5: ["RTK FLOAT", "fix-float"],
                 2: ["DGPS", "fix-dgps"], 1: ["GPS", "fix-gps"] };
  const [txt, cls] = map_[fix] || ["NO FIX", "fix-none"];
  b.textContent = txt;
  b.className = cls;
}

// ---- Field loading ----------------------------------------------------------
async function loadFieldList() {
  const ul = document.getElementById("fieldlist");
  const note = document.getElementById("syncnote");
  ul.innerHTML = "";
  let items = [], fromCache = false;
  try {
    const r = await fetch(FIELDS_INDEX, { cache: "no-store" });
    if (!r.ok) throw new Error("http " + r.status);
    items = (await r.json()).fields || [];
    await beeDB.putIndex(items);                 // keep a copy for offline
  } catch (e) {
    items = (await beeDB.getIndex().catch(() => null)) || [];   // offline fallback
    fromCache = true;
  }

  if (!items.length) {
    note.textContent = fromCache
      ? "Offline and no fields cached yet — showing the bundled sample."
      : "No field index found — showing the bundled sample.";
    await loadField("fields/sample_field.geojson", "North Quarter (sample)");
  } else {
    note.textContent = fromCache
      ? `Offline — ${items.length} field(s) from cache.`
      : `${items.length} field(s). Tap Sync to cache all for offline.`;
  }

  for (const it of items) {
    const li = document.createElement("li");
    li.innerHTML = `${it.name}<small>${it.company} &middot; ${it.year}</small>`;
    li.onclick = () => { loadField("fields/" + it.file, it.name); closeSheet("fieldsheet"); };
    ul.appendChild(li);
  }
}

// Download the index + every field's GeoJSON into IndexedDB so the whole set is
// available offline. Run at the yard before heading out (the "Sync" button).
async function syncAll() {
  const note = document.getElementById("syncnote");
  if (!navigator.onLine) { note.textContent = "Offline — connect to sync."; return; }
  note.textContent = "Syncing…";
  let items = [];
  try {
    const r = await fetch(FIELDS_INDEX, { cache: "no-store" });
    items = (await r.json()).fields || [];
    await beeDB.putIndex(items);
  } catch (e) { note.textContent = "Sync failed — no field index."; return; }
  let ok = 0;
  for (const it of items) {
    try {
      const r = await fetch("fields/" + it.file, { cache: "no-store" });
      if (!r.ok) continue;
      const fc = await r.json();
      await beeDB.putField(it.file, fc);
      ok++;
      // Pre-download satellite tiles covering this field for offline imagery.
      if (window.beeTiles) {
        await beeTiles.cacheFieldTiles(fc, (d, t) => {
          if (d % 20 === 0 || d === t) note.textContent = `Maps: ${it.name} — ${d}/${t} tiles`;
        });
      }
    } catch (e) { /* skip this one */ }
  }
  let evicted = 0;
  if (window.beeTiles) { try { evicted = await beeTiles.evictIfNeeded(); } catch (e) {} }
  let tiles = 0;
  try { tiles = window.beeTiles ? await beeTiles.tileCount() : 0; } catch (e) {}
  note.textContent = `Synced ${ok}/${items.length} field(s); ${tiles} map tiles cached`
    + (evicted ? ` (evicted ${evicted} old)` : "") + ".";
  loadFieldList();
}

async function loadField(url, name) {
  activeFieldFile = url.split("/").pop();   // e.g. Corteva__2026__North_Quarter.geojson
  let fc = null;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("http " + r.status);
    fc = await r.json();
    await beeDB.putField(activeFieldFile, fc);          // cache for offline
  } catch (e) {
    fc = await beeDB.getField(activeFieldFile).catch(() => null);   // offline fallback
    if (!fc) {
      document.getElementById("syncnote").textContent =
        "This field isn't cached for offline — Sync while online first.";
      return;
    }
  }
  activeField = fc;
  proxShelter = null; hideArrival();              // reset proximity for the new field
  if (window.beeTiles) beeTiles.touchField(fc);   // keep this field's tiles fresh (LRU)

  // Load this field's saved placement state from IndexedDB into `visited`.
  const saved = (await beeDB.getState(activeFieldFile).catch(() => null)) || {};
  Object.keys(visited).forEach((k) => delete visited[k]);
  Object.assign(visited, saved);
  for (const f of fc.features) {
    if (f.properties.type === "shelter") {
      const v = visited[f.properties.label];
      if (v) { f.properties.visited = v.visited; f.properties.note = v.note; }
      else { f.properties.visited = !!f.properties.visited; }
    }
  }

  map.getSource("field").setData(fc);
  const fname = name || fc.name || "Field";
  document.getElementById("fieldname").textContent = fname;
  publishFieldState(fname);
  fitToField();
}

function fitToField() {
  if (!activeField) return;
  const b = new maplibregl.LngLatBounds();
  let any = false;
  for (const f of activeField.features) {
    eachCoord(f.geometry, ([lon, lat]) => { b.extend([lon, lat]); any = true; });
  }
  if (any) map.fitBounds(b, { padding: 60, maxZoom: 18 });
}

function eachCoord(geom, fn) {
  if (!geom) return;
  if (geom.type === "Point") fn(geom.coordinates);
  else if (geom.type === "Polygon") geom.coordinates.forEach((r) => r.forEach(fn));
  else if (geom.type === "LineString") geom.coordinates.forEach(fn);
}

let activeFieldFile = null;   // geojson filename of the active field (for the Monitor mirror)

// Shelter totals + which shelters are placed, for the office Monitor view.
function fieldProgress() {
  let total = 0;
  const placedIds = [];
  for (const f of (activeField?.features || [])) {
    if (f.properties.type === "shelter") {
      total++;
      if (f.properties.visited) placedIds.push(f.properties.label);
    }
  }
  return { total, placed: placedIds.length, placedIds };
}

function publishFieldState(name) {
  if (!window.beePublish) return;
  const { total, placed, placedIds } = fieldProgress();
  window.beePublish.setField(name, total, activeFieldFile);
  window.beePublish.setProgress(placed, placedIds);
}

// ---- Point detail -----------------------------------------------------------
let openLabel = null;
function openPoint(feature) {
  openLabel = feature.properties.label;
  const v = visited[openLabel] || { visited: !!feature.properties.visited, note: feature.properties.note || "" };
  document.getElementById("pt-label").textContent = openLabel;
  const [lon, lat] = feature.geometry.coordinates;
  document.getElementById("pt-coords").textContent = `${lat.toFixed(7)}, ${lon.toFixed(7)}`;
  document.getElementById("pt-visited").checked = v.visited;
  document.getElementById("pt-note").value = v.note;
  show("pointsheet");
}

function commitPoint() {
  if (!openLabel) return;
  visited[openLabel] = {
    visited: document.getElementById("pt-visited").checked,
    note: document.getElementById("pt-note").value,
  };
  // Reflect in the live data so the marker recolours.
  for (const f of (activeField?.features || [])) {
    if (f.properties.label === openLabel) {
      f.properties.visited = visited[openLabel].visited;
      f.properties.note = visited[openLabel].note;
    }
  }
  map.getSource("field").setData(activeField);
  if (activeFieldFile) beeDB.putState(activeFieldFile, { ...visited }).catch(() => {});
  if (window.beePublish) {
    const p = fieldProgress();
    window.beePublish.setProgress(p.placed, p.placedIds);
  }
}

// ---- View switching ---------------------------------------------------------
const FIELD_LAYERS = ["boundary-line", "tracks-line", "shelters", "shelter-labels"];
const MAP_LAYERS = ["allfields-fill", "allfields-line", "allfields-label"];

function setLayers(ids, vis) {
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
}

function setMode(m) {
  mode = m;
  document.getElementById("btn-work").classList.toggle("active", m === "work");
  document.getElementById("btn-map").classList.toggle("active", m === "map");
  document.getElementById("worktools").classList.toggle("hidden", m !== "work");
  if (m === "work") {
    setLayers(MAP_LAYERS, "none"); setLayers(FIELD_LAYERS, "visible");
    fitToField();
  } else {
    setLayers(FIELD_LAYERS, "none"); setLayers(MAP_LAYERS, "visible");
    hideArrival();
    showAllFields();
  }
}

// Shelter pin labels: numbers vs tray counts.
function applyLabelMode() {
  if (!map.getLayer("shelter-labels")) return;
  const expr = labelMode === "trays"
    ? ["case", ["has", "trays"], ["concat", ["to-string", ["get", "trays"]], "t"], "—"]
    : ["get", "label"];
  map.setLayoutProperty("shelter-labels", "text-field", expr);
  const btn = document.getElementById("btn-labelmode");
  if (btn) btn.textContent = labelMode === "trays" ? "Show numbers" : "Show trays";
}
function toggleLabelMode() {
  labelMode = labelMode === "trays" ? "number" : "trays";
  applyLabelMode();
}

// ---- Proximity alert (within 10 ft of a shelter) ----------------------------
const ARRIVE_M = 3.048;   // 10 feet

function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000, toR = Math.PI / 180;
  const dLat = (lat2 - lat1) * toR, dLon = (lon2 - lon1) * toR;
  const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function checkProximity(p) {
  if (!activeField) return;
  let best = null, bestD = Infinity;
  for (const f of activeField.features) {
    if (f.properties.type !== "shelter") continue;
    const [lo, la] = f.geometry.coordinates;
    const d = haversine(p.lat, p.lon, la, lo);
    if (d < bestD) { bestD = d; best = f; }
  }
  if (best && bestD <= ARRIVE_M) {
    if (proxShelter !== best.properties.label) { proxShelter = best.properties.label; arrive(best); }
  } else if (proxShelter && bestD > ARRIVE_M + 2) {   // 2 m hysteresis before re-arming
    proxShelter = null; hideArrival();
  }
}

function arrive(feature) {
  const label = feature.properties.label;
  const trays = feature.properties.trays;
  const el = document.getElementById("arrival");
  if (labelMode === "trays") {
    // Bee crew: lead with how many trays to place at this shelter.
    el.innerHTML = (trays != null)
      ? `&#128029; Put ${trays} tray${trays === 1 ? "" : "s"} here` +
        `<div class="arrive-sub">${label}</div>`
      : `&#10003; You're at ${label}<div class="arrive-sub">tray count not set</div>`;
  } else {
    // Shelter / flagging crew: just the shelter number.
    el.innerHTML = `&#10003; You're at ${label}`;
  }
  el.classList.remove("hidden");
  el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";   // restart pop
  if (navigator.vibrate) navigator.vibrate([200, 80, 200]);
  beep();
}
function hideArrival() {
  const el = document.getElementById("arrival");
  if (el) el.classList.add("hidden");
}

let audioCtx = null;
function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "sine"; o.frequency.value = 880;
    o.connect(g); g.connect(audioCtx.destination);
    const t = audioCtx.currentTime;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
    o.start(t); o.stop(t + 0.4);
  } catch (e) { /* audio blocked — vibration + banner still fire */ }
}

// ---- All-fields overview ("Map" view) ---------------------------------------
async function buildAllFields() {
  const feats = [];
  let items = [];
  try { items = (await beeDB.getIndex()) || []; } catch (e) { /* ignore */ }
  if (!items.length) {
    try { const r = await fetch(FIELDS_INDEX, { cache: "no-store" }); if (r.ok) items = (await r.json()).fields || []; }
    catch (e) { /* offline, no index */ }
  }
  for (const it of items) {
    let fc = null;
    try { fc = await beeDB.getField(it.file); } catch (e) { /* ignore */ }
    if (!fc) { try { const r = await fetch("fields/" + it.file); if (r.ok) fc = await r.json(); } catch (e) {} }
    if (!fc) continue;
    const b = (fc.features || []).find((f) => f.properties.type === "boundary");
    if (!b) continue;
    feats.push({ type: "Feature", properties: { type: "boundary", name: it.name, file: it.file }, geometry: b.geometry });
    const ring = (b.geometry.coordinates && b.geometry.coordinates[0]) || [];
    if (ring.length) {
      let sx = 0, sy = 0;
      for (const [x, y] of ring) { sx += x; sy += y; }
      feats.push({ type: "Feature", properties: { type: "label", name: it.name },
                   geometry: { type: "Point", coordinates: [sx / ring.length, sy / ring.length] } });
    }
  }
  return { type: "FeatureCollection", features: feats };
}

async function showAllFields() {
  const fc = await buildAllFields();
  if (map.getSource("allfields")) map.getSource("allfields").setData(fc);
  const b = new maplibregl.LngLatBounds();
  let any = false;
  for (const f of fc.features) eachCoord(f.geometry, ([lon, lat]) => { b.extend([lon, lat]); any = true; });
  if (any) map.fitBounds(b, { padding: 50, maxZoom: 15 });
}

// ---- Sheets -----------------------------------------------------------------
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function closeSheet(id) { document.getElementById(id).classList.add("hidden"); }

// ---- Online / offline indicator ---------------------------------------------
function updateNet() {
  document.getElementById("netpill").classList.toggle("hidden", navigator.onLine);
}

// ---- Wire up ----------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  connectPosition();
  statusWatchdog();                                // globe→tablet-GPS fallback + signal pill
  if (window.beeTiles) beeTiles.evictIfNeeded();   // trim cache if it grew past the cap
  updateNet();
  window.addEventListener("online", updateNet);
  window.addEventListener("offline", updateNet);

  document.getElementById("btn-work").onclick = () => setMode("work");
  document.getElementById("btn-map").onclick = () => setMode("map");
  document.getElementById("btn-labelmode").onclick = toggleLabelMode;
  document.getElementById("btn-fields").onclick = () => { loadFieldList(); show("fieldsheet"); };
  document.getElementById("btn-close-fields").onclick = () => closeSheet("fieldsheet");
  document.getElementById("btn-sync").onclick = () => syncAll();
  document.getElementById("btn-close-point").onclick = () => { commitPoint(); closeSheet("pointsheet"); };
  document.getElementById("pt-visited").onchange = commitPoint;

  // Crew identity (shown on the office Monitor view).
  const cn = document.getElementById("crewname");
  if (window.beePublish) cn.textContent = window.beePublish.getCrew().name;
  document.getElementById("btn-crew").onclick = () => {
    const name = prompt("Crew name (shown on the office map):",
                        window.beePublish ? window.beePublish.getCrew().name : "");
    if (name && window.beePublish) { window.beePublish.setCrew(name); cn.textContent = name; }
  };
});
