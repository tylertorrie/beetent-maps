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

// Work-mode camera: a John-Deere-style guidance view. viewTilt 45 = 3D angled,
// 0 = bird's-eye (top-down). followCam keeps the camera locked on the crew,
// heading-up; a user pan/rotate detaches it until they tap Recenter.
let viewTilt = 0;         // 0 = 2D bird's-eye (default); 56 = 3D angled
let followCam = true;
const WORK_ZOOM = 18;     // default close zoom when entering Work mode

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

  // A user pan (not a programmatic camera move — those have no originalEvent)
  // detaches the guidance follow-cam until they tap Recenter. Zoom/rotate keep
  // following so pinch-zoom doesn't fight the lock.
  map.on("dragstart", (e) => { if (e && e.originalEvent) detachFollow(); });

  map.on("load", async () => {
    map.addSource("field", { type: "geojson", data: emptyFC() });

    // Overlay colors are kept identical to the desktop planner so a crew and an
    // operator are literally looking at the same colors (see TABLET_DESIGN_BRIEF).
    map.addLayer({ id: "boundary-line", type: "line",
      filter: ["==", ["get", "type"], "boundary"],
      source: "field", paint: { "line-color": "#E9F4D6", "line-width": 2.5 } });

    // Phase-2 toggleable overlays (exported by the desktop into the field GeoJSON).
    // Drawn UNDER the shelters so the pins stay on top. Hidden by default; the
    // Layers slide-over turns them on.
    // Pivot tracks are drawn as the buffer/exclusion band only (track-buffer-line
    // below) — the old single centre-line (pivot_track) is no longer rendered.
    map.addLayer({ id: "male-bays-fill", type: "fill",
      filter: ["==", ["get", "type"], "male_bay"], source: "field",
      layout: { visibility: "none" },
      paint: { "fill-color": "#2E9BF0", "fill-opacity": 0.12 } });
    map.addLayer({ id: "male-bays-line", type: "line",
      filter: ["==", ["get", "type"], "male_bay"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#2E9BF0", "line-width": 3, "line-opacity": 0.85, "line-dasharray": [3, 2] } });
    map.addLayer({ id: "alignment-line", type: "line",
      filter: ["==", ["get", "type"], "alignment"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#86E0FF", "line-width": 1.6, "line-opacity": 0.8 } });
    map.addLayer({ id: "sprayer-pass-line", type: "line",
      filter: ["==", ["get", "type"], "sprayer_pass"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#FF5A52", "line-width": 1.5, "line-opacity": 0.8, "line-dasharray": [4, 3] } });
    map.addLayer({ id: "sprayer-limit-line", type: "line",
      filter: ["==", ["get", "type"], "sprayer_limit"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#FF5A52", "line-width": 2, "line-dasharray": [4, 3] } });

    // Tire / edge zones, wet zones (fills first so they sit under everything).
    map.addLayer({ id: "tire-zone-fill", type: "fill",
      filter: ["==", ["get", "type"], "tire_zone"], source: "field",
      layout: { visibility: "none" },
      paint: { "fill-color": "#E0951F", "fill-opacity": 0.3 } });
    map.addLayer({ id: "edge-zone-fill", type: "fill",
      filter: ["==", ["get", "type"], "edge_zone"], source: "field",
      layout: { visibility: "none" },
      paint: { "fill-color": "#E0951F", "fill-opacity": 0.22 } });
    map.addLayer({ id: "wet-zone-fill", type: "fill",
      filter: ["==", ["get", "type"], "wet_zone"], source: "field",
      layout: { visibility: "none" },
      paint: { "fill-color": "#39B7D6", "fill-opacity": 0.3 } });
    map.addLayer({ id: "wet-zone-line", type: "line",
      filter: ["==", ["get", "type"], "wet_zone"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#39B7D6", "line-width": 2 } });
    // Pivot-track buffer/exclusion band — dashed orange edge circles (radius ± exclusion).
    // This is now the ONLY pivot-track rendering (the centre-line was dropped).
    map.addLayer({ id: "track-buffer-line", type: "line",
      filter: ["==", ["get", "type"], "track_buffer"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#FF8A2B", "line-width": 2.5, "line-dasharray": [4, 3] } });
    map.addLayer({ id: "planter-pass-line", type: "line",
      filter: ["==", ["get", "type"], "planter_pass"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#8FBE3C", "line-width": 1.4, "line-opacity": 0.8, "line-dasharray": [2, 3] } });
    map.addLayer({ id: "crew-route-line", type: "line",
      filter: ["==", ["get", "type"], "crew_route"], source: "field",
      layout: { visibility: "none" },
      paint: { "line-color": "#A06BFF", "line-width": 4, "line-opacity": 0.95 } });
    map.addLayer({ id: "planter-number-label", type: "symbol",
      filter: ["==", ["get", "type"], "planter_number"], source: "field",
      layout: { visibility: "none", "text-field": ["get", "label"],
                "text-font": ["OpenSans"], "text-size": 12, "text-allow-overlap": true },
      paint: { "text-color": "#8FBE3C", "text-halo-color": "#000", "text-halo-width": 1.2 } });

    // Shelter scan-pins (always on): placed = filled honey-yellow with a dark
    // outline (the outline is what makes yellow read over satellite); not-placed
    // = hollow yellow ring.
    map.addLayer({ id: "shelters", type: "circle",
      filter: ["==", ["get", "type"], "shelter"],
      source: "field",
      paint: {
        "circle-radius": 7,
        "circle-color": ["case", ["get", "visited"], "#FFCE3A", "rgba(255,206,58,0.12)"],
        "circle-stroke-color": ["case", ["get", "visited"], "#1A1A1A", "#FFCE3A"],
        "circle-stroke-width": 2.2,
      } });

    map.addLayer({ id: "shelter-labels", type: "symbol",
      filter: ["==", ["get", "type"], "shelter"],
      source: "field",
      layout: { "text-field": ["get", "label"], "text-font": ["OpenSans"],
                "text-size": 11, "text-offset": [0, 1.2] },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.4 } });

    map.on("click", "shelters", (e) => openPoint(e.features[0]));

    // Scanned actual shelter placements — bright green pins dropped at the exact
    // scan position (distinct from the yellow/green planned shelters).
    map.addSource("scans", { type: "geojson", data: emptyFC() });
    map.addLayer({ id: "scan-pins", type: "circle",
      filter: ["==", ["get", "type"], "scan"],
      source: "scans",
      paint: {
        "circle-radius": 7, "circle-color": "#19e36b",
        "circle-stroke-color": "#04361b", "circle-stroke-width": 2,
      } });

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

    applyLabelMode();
    // Open on the all-fields Map view (every field shown, fitted to view); fall
    // back to single-field Work mode only when no fields are synced yet (sample).
    const fieldCount = await loadFieldList();
    setMode(fieldCount > 0 ? "map" : "work");
  });
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

// ---- Position source --------------------------------------------------------
// The globe (or the dev simulator) over WebSocket.
function connectPosition() {
  // An insecure ws:// can't run from an https page (mixed content). The globe
  // link only applies when the app is served over http (e.g. straight from an
  // ESP32 AP); on https hosting the tablet's own GPS is the live source.
  if (location.protocol === "https:") return;
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

  // Guidance follow-cam: keep the crew centred, direction-of-travel up. easeTo
  // with no `zoom` preserves the user's pinch zoom.
  if (mode === "work" && followCam && map.isStyleLoaded()) {
    const hdg = (typeof p.course === "number" && !isNaN(p.course)) ? p.course : map.getBearing();
    map.easeTo({ center: [p.lon, p.lat], bearing: hdg, pitch: viewTilt, duration: 450 });
  }

  updateStatus(p, source);
  if (window.beePublish) window.beePublish.setPos(p);
  if (mode === "work") checkProximity(p);
}

// Display preferences — device-local. units drives distance/accuracy strings;
// legendPref shows the on-map key; highContrast is a sunlight-boost scrim/filter.
let units = (localStorage.getItem("beeUnits") === "metric") ? "metric" : "imperial";
let legendPref = localStorage.getItem("beeLegend") !== "0";
let highContrast = localStorage.getItem("beeHC") === "1";
function fmtDist(m) {
  return units === "metric" ? `${m.toFixed(1)} m` : `${feet(m).toFixed(1)} ft`;
}
function fmtAcc(m) {
  return units === "metric" ? `±${m.toFixed(1)} m` : `±${feet(m).toFixed(1)} ft`;
}

// The top-bar GPS pill: RTK teal when we have a good fix, warm amber for a coarse
// fix, muted grey for no signal. Reads the receiver — not a demo toggle.
function updateStatus(p, source) {
  const pill = document.getElementById("gpspill");
  const lab = document.getElementById("gps-label");
  if (!pill || !lab) return;
  let cls = "fix-warn", txt = "GPS";
  if (source === "globe") {
    if (p.fix === 4) { cls = "fix-rtk"; txt = "RTK · " + fixLabel(p); }
    else if (p.fix === 5) { cls = "fix-warn"; txt = "RTK float · " + fixLabel(p); }
    else if (p.fix === 2) { cls = "fix-warn"; txt = "DGPS"; }
    else if (p.fix === 1) { cls = "fix-warn"; txt = "GPS"; }
    else { cls = "fix-none"; txt = "NO FIX"; }
  } else {
    // tablet GPS: accuracy in the current units; RTK-teal only when very tight.
    const good = p.acc != null && p.acc <= 2.0;
    cls = good ? "fix-rtk" : "fix-warn";
    txt = "GPS · " + (p.acc != null ? fmtAcc(p.acc) : "—");
  }
  pill.className = cls;
  lab.textContent = txt;
}

// Best available accuracy string for a globe fix (hdop is unitless; fall back to it).
function fixLabel(p) {
  if (p.acc != null) return fmtAcc(p.acc);
  if (p.hdop != null) return "HDOP " + p.hdop;
  return units === "metric" ? "±0.4 m" : "±1.2 ft";
}

// Bring up tablet GPS once the globe goes quiet; flag total signal loss.
function statusWatchdog() {
  const now = Date.now();
  if (now - lastGlobeTs > GLOBE_STALE_MS && geoState === "off") startGeo();
  if (now - lastAnyTs > GLOBE_STALE_MS + 2000) {
    posSource = "none";
    const pill = document.getElementById("gpspill");
    const lab = document.getElementById("gps-label");
    if (pill) pill.className = "fix-none";
    if (lab) lab.textContent = "NO FIX";
  }
  setTimeout(statusWatchdog, 1500);
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
  return items.length;
}

// ---- Field-update alert ------------------------------------------------------
// The office edits fields on the desktop and pushes to GitHub; this polls the
// index and alerts the crew when something changed (especially the field they're
// on), so they can pull the latest without guessing.
let updateState = { activeChanged: false, changedFiles: [] };

function loadStamps() {
  try { return JSON.parse(localStorage.getItem("beeFieldStamps") || "null"); } catch (e) { return null; }
}
function saveStamps(s) { try { localStorage.setItem("beeFieldStamps", JSON.stringify(s)); } catch (e) {} }

async function checkFieldUpdates() {
  if (!navigator.onLine) return;
  let items;
  try {
    const r = await fetch(FIELDS_INDEX, { cache: "no-store" });
    if (!r.ok) return;
    items = (await r.json()).fields || [];
  } catch (e) { return; }
  const stamps = {};
  for (const it of items) stamps[it.file] = it.updated || it.file;
  const prev = loadStamps();
  if (prev === null) { saveStamps(stamps); return; }     // first run → baseline, no alert
  const changed = Object.keys(stamps).filter((f) => prev[f] !== stamps[f]);
  if (!changed.length) return;
  for (const f of changed) if (!updateState.changedFiles.includes(f)) updateState.changedFiles.push(f);
  if (activeFieldFile && updateState.changedFiles.includes(activeFieldFile)) updateState.activeChanged = true;
  saveStamps(stamps);                                    // advance baseline (don't re-alert same change)
  showUpdateBanner();
  try { if (navigator.vibrate) navigator.vibrate(140); } catch (e) {}
}

function showUpdateBanner() {
  const b = document.getElementById("updatebanner");
  const n = updateState.changedFiles.length;
  const msg = updateState.activeChanged
    ? "This field was updated in the office"
    : n + " field" + (n === 1 ? "" : "s") + " updated in the office";
  b.innerHTML =
    '<span class="ub-dot"></span>' +
    `<span class="ub-msg">${msg}</span>` +
    '<button class="ub-btn">Tap to refresh</button>' +
    '<button class="ub-x">✕</button>';
  b.querySelector(".ub-btn").onclick = () => applyFieldUpdates();
  b.querySelector(".ub-x").onclick = () => dismissUpdateBanner();
  b.classList.remove("hidden");
}

function dismissUpdateBanner() {
  updateState = { activeChanged: false, changedFiles: [] };
  document.getElementById("updatebanner").classList.add("hidden");
}

async function applyFieldUpdates() {
  const reloadActive = updateState.activeChanged;
  dismissUpdateBanner();
  await loadFieldList();                       // refresh the picker list
  if (mode === "map") showAllFields();         // refresh the all-fields overview
  if (reloadActive) await reloadActiveField(); // pull the new geometry into the open field
}

// Re-fetch the active field and swap in the new geometry WITHOUT moving the
// camera (keeps the crew's view/zoom/follow) — visited + scan state is restored
// from IndexedDB, so progress is never lost.
async function reloadActiveField() {
  if (!activeFieldFile) return;
  let fc = null;
  try {
    const r = await fetch("fields/" + activeFieldFile, { cache: "no-store" });
    if (r.ok) { fc = await r.json(); await beeDB.putField(activeFieldFile, fc); }
  } catch (e) {}
  if (!fc) return;
  applyLocalCalibIfPending(fc, activeFieldFile);
  activeField = fc;
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
  refreshScanLayer(); updateScanProgress();
  updateFieldSwitcher(); updatePlacementReadout();
  publishFieldState(activeField.name || "Field");
}

// ---- Field calibration -------------------------------------------------------
// Crew drives partway down the most-centred male bay and taps Calibrate; we shift
// every bay + shelter flag sideways so the estimated grid lands on their real
// position (sprayer tracks/zones do NOT move), then queue the correction to the
// office. Works offline — the desktop bakes it into the field file when it can.

// Port of the desktop utmish projection (latlon_to_enu) so the lateral projection
// matches the frame the exported bay_centers_m are computed in.
const _UTM = (function () {
  const K0 = 0.9996, E = 0.00669438, E_P2 = E / (1 - E), R = 6378137;
  const M1 = 1 - E / 4 - 3 * E * E / 64 - 5 * E * E * E / 256;
  const M2 = 3 * E / 8 + 3 * E * E / 32 + 45 * E * E * E / 1024;
  const M3 = 15 * E * E / 256 + 45 * E * E * E / 1024;
  const M4 = 35 * E * E * E / 3072;
  function fromLonLat(lon, lat, clon) {
    const latR = lat * Math.PI / 180, lonR = lon * Math.PI / 180, clonR = clon * Math.PI / 180;
    const ls = Math.sin(latR), lc = Math.cos(latR), lt = ls / lc, lt2 = lt * lt, lt4 = lt2 * lt2;
    const n = R / Math.sqrt(1 - E * ls * ls), c = E_P2 * lc * lc;
    const a = lc * (lonR - clonR), a2 = a * a, a3 = a2 * a, a4 = a3 * a, a5 = a4 * a, a6 = a5 * a;
    const m = R * (M1 * latR - M2 * Math.sin(2 * latR) + M3 * Math.sin(4 * latR) - M4 * Math.sin(6 * latR));
    const easting = K0 * n * (a + a3 / 6 * (1 - lt2 + c)
      + a5 / 120 * (5 - 18 * lt2 + lt4 + 72 * c - 58 * E_P2)) + 500000;
    let northing = K0 * (m + n * lt * (a2 / 2 + a4 / 24 * (5 - lt2 + 9 * c + 4 * c * c)
      + a6 / 720 * (61 - 58 * lt2 + lt4 + 600 * c - 330 * E_P2)));
    if (lat < 0) northing += 10000000;
    return [easting, northing];
  }
  return {
    enu(lat, lon, plat, plon) {            // ENU metres relative to the pivot
      const p = fromLonLat(plon, plat, plon), q = fromLonLat(lon, lat, plon);
      return [q[0] - p[0], q[1] - p[1]];
    },
  };
})();

// Equirectangular translate of one (lat,lon) by (east,north) metres — mirrors the
// desktop _shift_pt; exact enough for the small calibration delta.
function shiftLatLon(lat, lon, de, dn) {
  return [lat + dn / 111111.0, lon + de / (111111.0 * Math.cos(lat * Math.PI / 180))];
}

const CALIB_SHIFT_TYPES = new Set(
  ["male_bay", "alignment", "shelter", "planter_pass", "planter_number", "crew_route"]);

// Translate the bay-following overlay features of an FC by (de,dn) metres in place.
function translateField(fc, de, dn) {
  if ((!de && !dn) || !fc || !fc.features) return;
  const shiftPt = (c) => { const s = shiftLatLon(c[1], c[0], de, dn); return [s[1], s[0]]; };
  const walk = (g) => {
    if (!g) return;
    if (g.type === "Point") g.coordinates = shiftPt(g.coordinates);
    else if (g.type === "LineString") g.coordinates = g.coordinates.map(shiftPt);
    else if (g.type === "Polygon") g.coordinates = g.coordinates.map((r) => r.map(shiftPt));
  };
  for (const f of fc.features) if (CALIB_SHIFT_TYPES.has(f.properties.type)) walk(f.geometry);
}

function loadCalib(file) {
  try { return (JSON.parse(localStorage.getItem("beeCalib") || "{}") || {})[file] || null; }
  catch (e) { return null; }
}
function saveCalib(file, rec) {
  try {
    const all = JSON.parse(localStorage.getItem("beeCalib") || "{}") || {};
    if (rec) all[file] = rec; else delete all[file];
    localStorage.setItem("beeCalib", JSON.stringify(all));
  } catch (e) {}
}

// On (re)load: if a local calibration is pending and the GeoJSON hasn't baked it in
// yet, re-apply it to the fresh features; once baked in (applied_id ≥ our id), drop it.
function applyLocalCalibIfPending(fc, file) {
  const rec = loadCalib(file);
  if (!rec) return;
  const appliedId = (fc.calibration && fc.calibration.applied_id) || 0;
  if (appliedId >= rec.id) { saveCalib(file, null); return; }   // desktop caught up
  translateField(fc, rec.de, rec.dn);
}

function goodFix() {
  if (!pos) return false;
  if (posSource === "globe") return pos.fix === 4 || pos.fix === 5;   // RTK fixed / float
  if (posSource === "tablet") return pos.acc != null && pos.acc <= 2.0;
  return false;
}

function feet(m) { return m / 0.3048; }

// Compute the incremental bay shift to bring the nearest *displayed* male-bay
// centreline onto the crew. Returns {de,dn,offM,absE,absN,id} or null.
function calcCalibration() {
  const cal = activeField && activeField.calibration;
  if (!cal || !cal.bay_centers_m || !cal.bay_centers_m.length || !cal.pivot) return null;
  const cur = loadCalib(activeFieldFile) || { de: 0, dn: 0 };
  const [plat, plon] = cal.pivot;
  const [ldx, ldy] = cal.lat_axis;
  const [e, n] = _UTM.enu(pos.lat, pos.lon, plat, plon);
  const xCrew = e * ldx + n * ldy;
  const curLat = cur.de * ldx + cur.dn * ldy;       // lateral component already applied
  let best = Infinity, xBay = 0;
  for (const c of cal.bay_centers_m) {
    const disp = c + curLat;                          // where this bay is shown now
    if (Math.abs(xCrew - disp) < Math.abs(best)) { best = xCrew - disp; xBay = disp; }
  }
  const dde = best * ldx, ddn = best * ldy;           // incremental delta
  const newDe = cur.de + dde, newDn = cur.dn + ddn;   // new total override
  const base = cal.base_shift || [0, 0];
  return { dde, ddn, de: newDe, dn: newDn, offM: best,
           absE: base[0] + newDe, absN: base[1] + newDn };
}

function startCalibration() {
  if (!activeField || !(activeField.calibration && activeField.calibration.bay_centers_m)) {
    toast("No bay calibration data for this field."); return;
  }
  if (!goodFix()) { showCalDialog(null); return; }     // poor-fix branch
  const c = calcCalibration();
  if (!c) { toast("Couldn't compute a calibration."); return; }
  showCalDialog(c);
}

function closeCalDialog() { document.getElementById("caldialog").classList.add("hidden"); }

// Branches on GPS fix quality: a good (RTK) fix → confirm the shift; a poor fix →
// "need a better GPS fix" with a disabled apply.
function showCalDialog(c) {
  const el = document.getElementById("caldialog");
  const accM = (pos && pos.acc != null) ? pos.acc : null;
  const accStr = accM != null ? fmtAcc(accM) : (units === "metric" ? "±0.4 m" : "±1.2 ft");
  let card;
  if (c) {
    const dist = fmtDist(Math.abs(c.offM));
    card =
      '<div class="cal-card"><div class="cal-top"><div class="cal-head">' +
      '<div class="cal-icon ok">⌖</div>' +
      '<div><div class="cal-title">Calibrate grid</div>' +
      `<div class="cal-fix ok">RTK fix · ${accStr} accuracy</div></div></div>` +
      `<div class="cal-body"><b>Shift bays &amp; flags ${dist} to your position?</b> ` +
      'This nudges the whole grid to match where you’re standing on the most-centred male bay. ' +
      'Sprayer tracks stay put.</div></div>' +
      '<div class="cal-row"><button class="cal-cancel">Cancel</button>' +
      '<button class="cal-ok">Apply shift</button></div></div>';
  } else {
    card =
      '<div class="cal-card"><div class="cal-top"><div class="cal-head">' +
      '<div class="cal-icon warn">⚠</div>' +
      '<div><div class="cal-title">Need a better GPS fix</div>' +
      `<div class="cal-fix warn">Current accuracy ${accStr}</div></div></div>` +
      '<div class="cal-body">Too coarse to calibrate safely. Move to open sky, hold still, and ' +
      'wait for an RTK fix before shifting the grid.</div></div>' +
      '<div class="cal-row"><button class="cal-cancel">Cancel</button>' +
      '<button class="cal-ok disabled">Waiting for RTK…</button></div></div>';
  }
  el.innerHTML = card;
  el.classList.remove("hidden");
  el.onclick = (e) => { if (e.target === el) closeCalDialog(); };
  el.querySelector(".cal-cancel").onclick = closeCalDialog;
  if (c) el.querySelector(".cal-ok").onclick = () => { closeCalDialog(); applyCalibration(c); };
}

// Green success toast (auto-dismiss). Distance already applied.
function showCalSuccess(dist) {
  const el = document.getElementById("caltoast");
  el.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"></path></svg>' +
    `<div><div class="ct-h">Calibrated</div><div class="ct-sub">Shift of ${dist} applied · queued for the office</div></div>`;
  el.classList.remove("hidden");
  clearTimeout(showCalSuccess._t);
  showCalSuccess._t = setTimeout(() => el.classList.add("hidden"), 2800);
}

function applyCalibration(c) {
  // Shift the displayed grid by the incremental delta and persist the new total.
  translateField(activeField, c.dde, c.ddn);
  map.getSource("field").setData(activeField);
  const id = Date.now();
  saveCalib(activeFieldFile, { id, de: c.de, dn: c.dn });
  // Queue the ABSOLUTE bay_shift to the office (idempotent, last-write-wins).
  const rec = { id, e: c.absE, n: c.absN, lat: pos.lat, lon: pos.lon,
                crew: (window.beePublish ? beePublish.getCrew().name : ""), ts: Date.now() / 1000 };
  sendCalibration(activeFieldFile, rec);
  showCalSuccess(fmtDist(Math.abs(c.offM)));
  try { if (navigator.vibrate) navigator.vibrate(120); } catch (e) {}
}

// Send to Firebase; if the relay is down, queue in localStorage and flush on reconnect.
function sendCalibration(file, rec) {
  const fieldId = file.replace(/\.geojson$/i, "");
  let p = null;
  try { p = window.beePublish ? beePublish.pushCalibration(fieldId, rec) : null; } catch (e) {}
  if (!p) {
    try {
      const q = JSON.parse(localStorage.getItem("beeCalibQueue") || "{}") || {};
      q[fieldId] = rec; localStorage.setItem("beeCalibQueue", JSON.stringify(q));
    } catch (e) {}
  }
}
function flushCalibQueue() {
  let q;
  try { q = JSON.parse(localStorage.getItem("beeCalibQueue") || "{}") || {}; } catch (e) { return; }
  let changed = false;
  for (const fieldId of Object.keys(q)) {
    let p = null;
    try { p = window.beePublish ? beePublish.pushCalibration(fieldId, q[fieldId]) : null; } catch (e) {}
    if (p) { delete q[fieldId]; changed = true; }
  }
  if (changed) { try { localStorage.setItem("beeCalibQueue", JSON.stringify(q)); } catch (e) {} }
}

// Lightweight toast reusing the update banner's slot styling.
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3200);
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
  // Apply the freshly-synced data NOW so a manual Sync shows the office's latest
  // immediately: refresh the all-fields overview and swap the new geometry into
  // the open field (camera + placement progress preserved). Clears any pending
  // "field updated" alert since we just pulled everything.
  try { if (mode === "map") await showAllFields(); } catch (e) {}
  try { if (activeFieldFile) await reloadActiveField(); } catch (e) {}
  try { dismissUpdateBanner(); } catch (e) {}
}

// Manual "Sync now" from the always-visible top-bar button. Pulls the latest
// field data + tiles and applies them, with toast feedback (the Fields-drawer
// note isn't visible when triggered from the top bar).
async function manualSyncNow() {
  if (!navigator.onLine) { toast("Offline — connect to Wi-Fi to sync."); return; }
  const btn = document.getElementById("btn-topsync");
  if (btn) btn.classList.add("syncing");
  toast("Syncing latest field data…");
  try {
    await syncAll();
    toast("Synced ✓  latest field data loaded.");
  } catch (e) {
    toast("Sync failed — try again.");
  }
  if (btn) btn.classList.remove("syncing");
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
  applyLocalCalibIfPending(fc, activeFieldFile);  // re-apply an un-baked-in crew calibration
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
  activeField.name = fname;
  publishFieldState(fname);
  updateFieldSwitcher(); updatePlacementReadout();
  fitToField();
  refreshScanLayer(); updateScanProgress();   // show this field's scanned pins
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
  updatePlacementReadout(); updateFieldSwitcher();
}

// Bottom-bar "Mark placed": mark the shelter the crew is standing at (the proximity
// shelter, else the nearest planned shelter) as placed — one-tap field logging.
function markPlaced() {
  if (!activeField) { toast("Open a field first."); return; }
  let target = null;
  const shelters = activeField.features.filter((f) => f.properties.type === "shelter");
  if (proxShelter) target = shelters.find((f) => f.properties.label === proxShelter);
  if (!target && pos) {
    let bestD = Infinity;
    for (const f of shelters) {
      const [lo, la] = f.geometry.coordinates;
      const d = haversine(pos.lat, pos.lon, la, lo);
      if (d < bestD) { bestD = d; target = f; }
    }
  }
  if (!target) { toast("No shelter to mark — need a GPS position."); return; }
  const label = target.properties.label;
  const already = visited[label] ? visited[label].visited : !!target.properties.visited;
  visited[label] = { visited: !already, note: (visited[label] && visited[label].note) || target.properties.note || "" };
  target.properties.visited = !already;
  map.getSource("field").setData(activeField);
  if (activeFieldFile) beeDB.putState(activeFieldFile, { ...visited }).catch(() => {});
  if (window.beePublish) { const p = fieldProgress(); window.beePublish.setProgress(p.placed, p.placedIds); }
  updatePlacementReadout(); updateFieldSwitcher();
  if (navigator.vibrate) navigator.vibrate(60);
  toast(already ? `${label} un-marked` : `${label} marked placed ✓`);
}

// ---- View switching ---------------------------------------------------------
const FIELD_LAYERS = ["boundary-line", "male-bays-fill", "male-bays-line",
  "alignment-line", "sprayer-pass-line", "sprayer-limit-line",
  "tire-zone-fill", "edge-zone-fill", "wet-zone-fill", "wet-zone-line",
  "track-buffer-line", "planter-pass-line", "crew-route-line", "planter-number-label",
  "shelters", "shelter-labels", "scan-pins"];
const MAP_LAYERS = ["allfields-fill", "allfields-line", "allfields-label"];

// Per-overlay toggles for Work mode. Each crew member can switch overlays on/off
// independently (multiple at once); the choice persists per device. Phase 2 appends
// rows here (male bays, alignment lines, sprayer passes, …) once they're exported
// into the field GeoJSON. "scan-pins" is intentionally not toggleable — always on.
// Shelter scan-pins are ALWAYS on (locked) — not in this list. Each row carries a
// swatch descriptor (color; line=true → dashed line swatch, else filled dot) so the
// slide-over can draw the on-map key next to each toggle. Defaults per the spec:
// Boundaries + Pivot tracks on, everything else off.
const LAYER_TOGGLES = [
  { key: "boundary",  label: "Boundaries",      layers: ["boundary-line"], def: true,  color: "#E9F4D6", line: false },
  { key: "track_buf", label: "Pivot tracks",    layers: ["track-buffer-line"], def: true, color: "#FF8A2B", line: true },
  { key: "male_bays", label: "Male bays",       layers: ["male-bays-fill", "male-bays-line"], def: false, color: "#2E9BF0", line: true },
  { key: "alignment", label: "Alignment lines", layers: ["alignment-line"], def: false, color: "#86E0FF", line: true },
  { key: "sprayer",   label: "Sprayer passes",  layers: ["sprayer-pass-line", "sprayer-limit-line"], def: false, color: "#FF5A52", line: true },
  { key: "tire_edge", label: "Tire & edge zones", layers: ["tire-zone-fill", "edge-zone-fill"], def: false, color: "#E0951F", line: false },
  { key: "wet",       label: "Wet zones",       layers: ["wet-zone-fill", "wet-zone-line"], def: false, color: "#39B7D6", line: false },
  { key: "planter",   label: "Planter passes",  layers: ["planter-pass-line", "planter-number-label"], def: false, color: "#8FBE3C", line: true },
  { key: "crew",      label: "Crew route",      layers: ["crew-route-line"], def: false, color: "#A06BFF", line: true },
];

let layerState = loadLayerState();   // { key: bool }

function loadLayerState() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("beeLayerState") || "{}") || {}; } catch (e) {}
  const st = {};
  for (const t of LAYER_TOGGLES) st[t.key] = (t.key in saved) ? !!saved[t.key] : (t.def !== false);
  return st;
}
function saveLayerState() {
  try { localStorage.setItem("beeLayerState", JSON.stringify(layerState)); } catch (e) {}
}

function setLayers(ids, vis) {
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
}

// Apply each overlay's individual on/off state (Work mode only).
function applyLayerVisibility() {
  if (mode !== "work") return;
  for (const t of LAYER_TOGGLES) setLayers(t.layers, layerState[t.key] ? "visible" : "none");
  // Shelter scan-pins are always on (locked in the slide-over).
  setLayers(["shelters", "shelter-labels", "scan-pins"], "visible");
  updateLayerCount();
}

// Layer-count badge = active toggleable overlays + 1 for the locked scan-pins row.
function updateLayerCount() {
  const el = document.getElementById("layer-count");
  if (!el) return;
  let n = 1;
  for (const t of LAYER_TOGGLES) if (layerState[t.key]) n++;
  el.textContent = String(n);
}

function setMode(m) {
  mode = m;
  // Segmented control active state.
  for (const [id, key] of [["btn-work", "work"], ["btn-map", "map"], ["btn-system", "system"]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("active", m === key);
  }
  // Work-mode chrome (bottom bar, legend, view toggle, FAB) only in Work.
  const workChrome = ["actionbar", "viewtoggle", "btn-follow"];
  for (const id of workChrome) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", m !== "work");
  }
  toggleEl("legend", m === "work" && legendPref);
  toggleEl("mapcards", m === "map");
  toggleEl("systemview", m === "system");
  if (m !== "work") { closeSheet("layerscrim"); hideArrival(); }

  if (m === "system") {
    buildSystemView();
    return;                                      // System overlays the map; leave layers as-is
  }
  if (m === "work") {
    setLayers(MAP_LAYERS, "none");
    applyLayerVisibility();                      // honour per-overlay toggles
    updateFieldSwitcher();
    startWorkCam();
  } else {
    setLayers(FIELD_LAYERS, "none"); setLayers(MAP_LAYERS, "visible");
    updateFieldSwitcher();
    showAllFields();
    buildFieldCards();
  }
}

function toggleEl(id, show) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("hidden", !show);
}

// Field switcher label: the open field + placement in Work; "All fields" in Map.
function updateFieldSwitcher() {
  const name = document.getElementById("fs-name");
  const sub = document.getElementById("fs-sub");
  if (!name || !sub) return;
  if (mode === "map") {
    name.textContent = "All fields";
    sub.textContent = "tap a field below to open";
    return;
  }
  if (!activeField) { name.textContent = "No field"; sub.textContent = "tap to pick a field"; return; }
  const p = fieldProgress();
  name.textContent = activeField.name || document.getElementById("fs-name").textContent || "Field";
  sub.textContent = `${p.placed} / ${p.total} placed · tap to switch field`;
  updatePlacementReadout();
}

// Bottom-bar placement readout (count + honey progress bar).
function updatePlacementReadout() {
  const p = fieldProgress();
  const cnt = document.getElementById("pl-count");
  const fill = document.getElementById("pl-fill");
  if (cnt) cnt.textContent = `${p.placed} / ${p.total}`;
  if (fill) fill.style.width = (p.total ? Math.round((p.placed / p.total) * 100) : 0) + "%";
}

// ---- Work-mode guidance camera ----------------------------------------------
// Enter the tilted, crew-centred, heading-up view. Falls back to fitting the
// whole field when there's no live position yet.
function startWorkCam() {
  followCam = true;
  updateFollowBtn();
  if (pos) {
    const hdg = (typeof pos.course === "number" && !isNaN(pos.course)) ? pos.course : map.getBearing();
    map.easeTo({ center: [pos.lon, pos.lat], zoom: Math.max(map.getZoom(), WORK_ZOOM),
                 bearing: hdg, pitch: viewTilt, duration: 600 });
  } else {
    map.easeTo({ pitch: viewTilt, duration: 400 });
    fitToField();
  }
}

// 2D = bird's-eye (top-down, pitch 0); 3D = camera-behind-the-vehicle (pitch 56 +
// heading-up). Uses MapLibre's native pitch/bearing (the spec prefers this over a
// CSS ground-plane transform). Default view is 2D.
const PITCH_3D = 56;
function setView(v) {
  viewTilt = v === "3d" ? PITCH_3D : 0;
  document.body.classList.toggle("view3d", v === "3d");
  const b2 = document.getElementById("btn-2d"), b3 = document.getElementById("btn-3d");
  if (b2) b2.classList.toggle("active", v === "2d");
  if (b3) b3.classList.toggle("active", v === "3d");
  const hdg = (pos && typeof pos.course === "number" && !isNaN(pos.course)) ? pos.course
            : (v === "2d" ? 0 : map.getBearing());
  map.easeTo({ pitch: viewTilt, bearing: (v === "2d" ? 0 : hdg), duration: 550 });
}

// A manual pan/rotate/pitch detaches the follow-camera until the crew taps the FAB.
function detachFollow() {
  if (mode === "work" && followCam) { followCam = false; updateFollowBtn(); }
}
// FAB toggles the GPS follow-camera. Turning it on re-centres on the crew, heading-up.
function toggleFollow() {
  followCam = !followCam;
  updateFollowBtn();
  if (followCam && pos) {
    const hdg = (typeof pos.course === "number" && !isNaN(pos.course)) ? pos.course : map.getBearing();
    map.easeTo({ center: [pos.lon, pos.lat], bearing: hdg, pitch: viewTilt, duration: 500 });
  }
}
function updateFollowBtn() {
  const btn = document.getElementById("btn-follow");
  const lab = document.getElementById("follow-label");
  if (!btn) return;
  btn.classList.toggle("following", followCam);
  btn.classList.toggle("detached", !followCam);
  if (lab) lab.textContent = followCam ? "FOLLOW" : "RECENTER";
}

// ---- Work-mode layers slide-over --------------------------------------------
// A right-anchored sheet of toggle switches. The always-on shelter scan-pins are a
// locked row at the top (rendered in index.html-independent markup here). Each row
// draws the on-map swatch (dashed line for line overlays, filled dot for fills).
function buildLayersPanel() {
  const body = document.getElementById("layers-body");
  if (!body) return;
  body.innerHTML =
    '<div class="locked-row"><span class="lr-dot"></span>' +
    '<div class="lr-text"><div class="lr-h">Shelter scan-pins</div><div class="lr-sub">Always on</div></div>' +
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#B4AD9E" stroke-width="2">' +
    '<rect x="5" y="11" width="14" height="9" rx="2"></rect><path d="M8 11V8a4 4 0 0 1 8 0v3"></path></svg></div>';
  for (const t of LAYER_TOGGLES) {
    const on = !!layerState[t.key];
    const row = document.createElement("button");
    row.className = "layer-row " + (on ? "on" : "off");
    const swatch = t.line
      ? `border-radius:3px;background:${on ? t.color : "transparent"};border:2px solid ${t.color};`
      : `border-radius:50%;background:${t.color};border:2px solid rgba(26,26,26,.35);`;
    row.innerHTML =
      `<span class="lr-swatch" style="${swatch}"></span>` +
      `<span class="lr-label">${t.label}</span>` +
      `<span class="switch ${on ? "on" : ""}"><span class="knob"></span></span>`;
    row.onclick = () => {
      layerState[t.key] = !layerState[t.key];
      saveLayerState(); applyLayerVisibility(); buildLayersPanel();
    };
    body.appendChild(row);
  }
}
function openLayersPanel() { buildLayersPanel(); show("layerscrim"); }
function toggleLayersPanel() {
  const el = document.getElementById("layerscrim");
  if (el.classList.contains("hidden")) openLayersPanel();
  else closeSheet("layerscrim");
}
function resetLayersDefault() {
  for (const t of LAYER_TOGGLES) layerState[t.key] = (t.def !== false);
  saveLayerState(); applyLayerVisibility(); buildLayersPanel();
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

let arrivalLabel = null;   // shelter label the arrival banner currently references
function arrive(feature) {
  const label = feature.properties.label;
  const trays = feature.properties.trays;
  arrivalLabel = label;
  const set = trays != null;
  const sub = set ? `Trays: ${trays} · logged` : "Tray count not set";
  const el = document.getElementById("arrival");
  el.innerHTML =
    '<div class="ar-disc"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1E8A45" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"></path></svg></div>' +
    `<div class="ar-body"><div class="ar-title">You're at ${label}</div>` +
    `<div class="ar-sub ${set ? "set" : "unset"}">${sub}</div></div>` +
    `<button class="ar-cta">${set ? "Edit trays" : "Set trays"}</button>` +
    '<button class="ar-x">✕</button>';
  el.querySelector(".ar-cta").onclick = () => arriveSetTrays(label);
  el.querySelector(".ar-x").onclick = () => { proxShelter = null; hideArrival(); };
  el.classList.remove("hidden");
  el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";   // restart drop
  if (navigator.vibrate) navigator.vibrate([200, 80, 200]);
  beep();
}

// "Set trays" from the arrival banner → open the Scan drawer in tray mode with this
// shelter preselected, so the crew scans each tray going into it.
function arriveSetTrays(label) {
  scanMode = "tray";
  trayShelterQr = label;
  openScan();
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
  let items = null;
  // Prefer the freshest list when online (so newly-exported fields appear without
  // a manual Sync); fall back to the cached index when offline.
  if (navigator.onLine) {
    try {
      const r = await fetch(FIELDS_INDEX, { cache: "no-store" });
      if (r.ok) { items = (await r.json()).fields || []; await beeDB.putIndex(items).catch(() => {}); }
    } catch (e) { /* offline-ish */ }
  }
  if (!items) {
    try { items = (await beeDB.getIndex()) || []; } catch (e) { items = []; }
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

// ---- Map-mode field cards ---------------------------------------------------
// A horizontally-scrolling rail of field cards (status dot + name + badge +
// progress). Tapping a card opens that field in Work mode.
async function buildFieldCards() {
  const rail = document.getElementById("mapcards-rail");
  if (!rail) return;
  const idx = (await beeDB.getIndex().catch(() => null)) || [];
  rail.innerHTML = "";
  if (!idx.length) {
    rail.innerHTML = '<div style="color:#F3EFE6;font-weight:600;padding:14px;">No fields synced yet — tap “Sync now”.</div>';
    return;
  }
  for (const it of idx) {
    let fc = await beeDB.getField(it.file).catch(() => null);
    if (!fc) { try { const r = await fetch("fields/" + it.file); if (r.ok) fc = await r.json(); } catch (e) {} }
    let total = 0, placed = 0, lld = "";
    if (fc) {
      const st = (await beeDB.getState(it.file).catch(() => null)) || {};
      for (const f of (fc.features || [])) if (f.properties.type === "shelter") {
        total++;
        const v = st[f.properties.label];
        if (v ? v.visited : f.properties.visited) placed++;
      }
      lld = (fc.field && (fc.field.lld || fc.field.LLD)) || "";
    }
    const pct = total ? Math.round((placed / total) * 100) : 0;
    const done = total > 0 && placed === total;
    const active = it.file === activeFieldFile;
    const started = placed > 0;
    const ring = done ? "#34C97B" : (started ? "#FF8A2B" : "#8A8477");
    const badge = done ? "DONE" : (active ? "ACTIVE" : (started ? "IN PROGRESS" : "NOT STARTED"));
    const badgeBg = done ? "#DBF0EE" : (active ? "#FBF1DD" : "#EFEAE1");
    const badgeFg = done ? "#0C5B57" : (active ? "#6B4A0E" : "#8A8477");
    const barColor = done ? "#34C97B" : "linear-gradient(90deg,#B87514,#E0951F)";
    const pctColor = done ? "#1E8A45" : "#9A5B12";
    const sub = [it.company, lld || it.year].filter(Boolean).join(" · ");

    const card = document.createElement("button");
    card.className = "fcard" + (active ? " active" : "");
    card.innerHTML =
      `<div class="fc-top"><span class="fc-dot" style="background:${ring};"></span>` +
      `<span class="fc-name">${it.name}</span>` +
      `<span class="fc-badge" style="background:${badgeBg};color:${badgeFg};">${badge}</span></div>` +
      `<div class="fc-sub">${sub}</div>` +
      `<div class="fc-prog"><span>${placed} / ${total} placed</span><span style="color:${pctColor};">${pct}%</span></div>` +
      `<div class="fc-bar"><span style="width:${pct}%;background:${barColor};"></span></div>`;
    card.onclick = () => { loadField("fields/" + it.file, it.name); setMode("work"); };
    rail.appendChild(card);
  }
}

// ---- System design-system reference (hidden page; need not ship) ------------
let _systemBuilt = false;
function buildSystemView() {
  if (_systemBuilt) return;
  _systemBuilt = true;
  const el = document.getElementById("systemview");
  if (!el) return;
  const palette = [["Paper", "#F4F1EA"], ["Surface", "#FFFFFF"], ["Border", "#D8D2C4"],
    ["Ink", "#221F1A"], ["Ink 2", "#5C564B"], ["Ink 3", "#938C7E"],
    ["Honey", "#B87514"], ["Honey tint", "#FBF1DD"], ["Positive", "#1E8A45"], ["Danger", "#C4433B"]];
  const overlays = [["Shelter pin", "#FFCE3A"], ["Pivot point", "#F5453D"], ["Pivot track", "#FF8A2B"],
    ["Male bay", "#2E9BF0"], ["Alignment", "#86E0FF"], ["Sprayer", "#FF5A52"],
    ["Wet zone", "#39B7D6"], ["Planter", "#8FBE3C"], ["Crew route", "#A06BFF"], ["Home/depot", "#2F7FE6"]];
  const sw = (arr, dark) => arr.map(([n, h]) =>
    `<div><div class="sw-chip" style="background:${h};"></div><div class="sw-name">${n}</div><div class="sw-hex">${h}</div></div>`).join("");
  el.innerHTML =
    '<div class="sys-wrap">' +
    '<div class="sys-h1">Bee Tent Maps — Field Kit</div>' +
    '<div class="sys-lead">Same family as the desktop planner — light warm shell, honey accent, dark satellite map as the hero — re-flowed for one-handed field use: bigger targets (≥56px), chunkier type, and chrome that stays legible in direct sun.</div>' +
    '<div class="sys-eyebrow">Interface — light shell</div>' +
    `<div class="sys-grid6">${sw(palette)}</div>` +
    '<div class="sys-eyebrow">Map overlays — tuned to pop over satellite</div>' +
    `<div class="sys-grid5">${sw(overlays, true)}</div>` +
    '<div class="sys-eyebrow">Touch components &amp; field states</div>' +
    '<div class="sys-card" style="display:flex;flex-direction:column;gap:22px;">' +
      '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;">' +
        '<button class="primary-btn" style="height:56px;">Primary</button>' +
        '<button class="secondary-btn" style="height:56px;">Secondary</button>' +
        '<span class="switch on"><span class="knob"></span></span>' +
        '<span class="switch"><span class="knob"></span></span>' +
      '</div>' +
      '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;">' +
        '<span style="display:inline-flex;align-items:center;gap:9px;height:44px;padding:0 15px;border-radius:12px;border:1px solid #B6DED9;background:#DBF0EE;color:#0C5B57;font-size:14px;font-weight:800;"><span style="width:11px;height:11px;border-radius:50%;background:#127C77;"></span>RTK · ±1.2 ft</span>' +
        '<span style="display:inline-flex;align-items:center;gap:9px;height:44px;padding:0 15px;border-radius:12px;border:1px solid #F2DFC2;background:#FCF3E7;color:#9A5B12;font-size:14px;font-weight:800;"><span style="width:11px;height:11px;border-radius:50%;background:#D9822B;"></span>GPS · ±11 ft</span>' +
      '</div>' +
    '</div>' +
    '<div class="sys-note">Family note · Overlay colors are kept identical to the desktop planner so a crew and an operator are literally looking at the same colors. Male bays stay desktop-blue (#2E9BF0) for that parity.</div>' +
    '</div>';
}

// ---- Sheets -----------------------------------------------------------------
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function closeSheet(id) { document.getElementById(id).classList.add("hidden"); }

// ---- Shelter-crew checklist -------------------------------------------------
// Three phases. {n} fills with the active field's shelter count; {n3} with the
// nesting blocks needed (3 per shelter). Tick state persists per field in
// IndexedDB (meta store), so it survives a reload with no signal.
const CHECKLIST = [
  { title: "Before Leaving for the Field", items: [
    "Are the vehicles fueled and do the tires look good?",
    "Do you have {n} shelters loaded?",
    "Do you have {n3} nesting blocks ready? (3 per shelter)",
    "Do you have enough anchors and supplies for {n} shelters?",
    "Is the scanning app up to date?",
    "Do you have charged batteries and tools?",
    "Do you have tow straps to pull out if stuck?",
  ] },
  { title: "In the Field", items: [
    "Confirm flag placement allows shelters to sit two rows away from the male bay.",
    "Confirm the scanning app is working and you have service.",
    "Do not drive on the crop if at all possible.",
    "Make sure shelters line up in nice lines in all directions.",
  ] },
  { title: "After the Task", items: [
    "Is all the garbage picked up from the field and the corner?",
    "Make sure all batteries are being charged.",
    "Mark the field as complete in the app.",
    "Confirm you placed {n} shelters.",
    "Trailers and trucks are parked nicely out of the way.",
    "No blocks left with holes pointing up (they fill with rainwater).",
  ] },
];

let checklistState = {};    // {sectionIdx.itemIdx: bool} for the active field
let checklistKey = null;

function fillCounts(text, n) {
  const nStr = (typeof n === "number") ? String(n) : "—";
  const n3Str = (typeof n === "number") ? String(n * 3) : "—";
  return text.replace(/\{n3\}/g, n3Str).replace(/\{n\}/g, nStr);
}

function updateChecklistProgress() {
  let done = 0, total = 0;
  CHECKLIST.forEach((sec, si) => sec.items.forEach((_it, ii) => {
    total++; if (checklistState[si + "." + ii]) done++;
  }));
  const el = document.getElementById("cl-progress");
  if (el) el.textContent = done + " / " + total;
}

async function openChecklist() {
  closeSheet("fieldsheet"); closeSheet("pointsheet");
  const body = document.getElementById("checklist-body");
  body.innerHTML = "";
  if (!activeField) {
    body.innerHTML = '<div class="cl-empty">Open a field first (Fields button) ' +
      'to load its shelter count, then re-open the checklist.</div>';
    const el = document.getElementById("cl-progress"); if (el) el.textContent = "";
    show("checklistsheet");
    return;
  }
  const n = fieldProgress().total;
  checklistKey = "checklist:" + (activeFieldFile || "nofield");
  try { checklistState = (await beeDB.getMeta(checklistKey)) || {}; }
  catch { checklistState = {}; }

  CHECKLIST.forEach((sec, si) => {
    const h = document.createElement("div");
    h.className = "cl-section"; h.textContent = sec.title;
    body.appendChild(h);
    sec.items.forEach((it, ii) => {
      const id = si + "." + ii;
      const checked = !!checklistState[id];
      const lbl = document.createElement("label");
      lbl.className = "cl-item" + (checked ? " done" : "");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = checked;
      cb.onchange = () => {
        checklistState[id] = cb.checked;
        lbl.classList.toggle("done", cb.checked);
        if (checklistKey) beeDB.putMeta(checklistKey, { ...checklistState }).catch(() => {});
        updateChecklistProgress();
      };
      const span = document.createElement("span");
      span.textContent = fillCounts(it, n);
      lbl.appendChild(cb); lbl.appendChild(span);
      body.appendChild(lbl);
    });
  });

  const foot = document.createElement("div");
  foot.className = "cl-foot";
  const reset = document.createElement("button");
  reset.textContent = "Reset checklist";
  reset.onclick = () => {
    checklistState = {};
    if (checklistKey) beeDB.putMeta(checklistKey, {}).catch(() => {});
    openChecklist();
  };
  foot.appendChild(reset);
  body.appendChild(foot);

  updateChecklistProgress();
  show("checklistsheet");
}

// ---- QR scanning: shelter placement + tray placement ------------------------
// Offline-first (Phase A): scans are stored in IndexedDB and dropped as pins on
// the map. Hardware keyboard-wedge scanners type into #scan-input (works on the
// ESP32 over http); the camera path runs only in a secure context (https).
let scanMode = "shelter";        // "shelter" | "tray"
let trayShelterQr = null;        // shelter the trays are being attached to
let _lastCamVal = null, _lastCamTs = 0;   // debounce repeated camera decodes

function pointInRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if (((yi > lat) !== (yj > lat)) &&
        (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function fcBoundaryContains(fc, lat, lon) {
  for (const f of (fc.features || [])) {
    const p = f.properties || {};
    if (p.type === "boundary" && f.geometry && f.geometry.type === "Polygon") {
      if (pointInRing(lat, lon, f.geometry.coordinates[0])) return true;
    }
  }
  return false;
}
// Which field's boundary contains (lat,lon): active field first, then any cached.
// field_id is the geojson FILENAME (a stable string key, e.g.
// "Proven_Seeds__2026__NW_1-10-15.geojson"); the geojson's own `field` property
// is an object (company/year/pivot), not an id.
async function detectField(lat, lon) {
  if (activeField && fcBoundaryContains(activeField, lat, lon))
    return { id: activeFieldFile, name: activeField.name || "field" };
  const idx = (await beeDB.getIndex().catch(() => null)) || [];
  for (const it of idx) {
    const fc = await beeDB.getField(it.file).catch(() => null);
    if (fc && fcBoundaryContains(fc, lat, lon))
      return { id: it.file, name: fc.name || it.name };
  }
  return null;
}
function scanFieldId() { return activeFieldFile || null; }
function crewWho() { return window.beePublish ? window.beePublish.getCrew().name : "—"; }
function nowIso() { return new Date().toISOString(); }

function setScanStatus(msg, cls) {
  const el = document.getElementById("scan-status");
  if (el) { el.textContent = msg || ""; el.className = cls || ""; }
}
function focusScanInput() {
  const i = document.getElementById("scan-input");
  if (i) { i.value = ""; setTimeout(() => i.focus(), 50); }
}

async function handleScan(raw) {
  const qr = (raw || "").trim();
  if (!qr) return;
  if (scanMode === "shelter") return handleShelterScan(qr);
  return handleTrayScan(qr);
}

async function handleShelterScan(qr) {
  if (!pos || typeof pos.lat !== "number") {
    setScanStatus("No GPS position yet — wait for a fix, then scan.", "warn"); return false;
  }
  const lat = pos.lat, lon = pos.lon;
  const fld = await detectField(lat, lon);
  const rec = {
    shelter_qr: qr, lat, lon,
    field_id: fld ? fld.id : (scanFieldId() || ""),
    field_name: fld ? fld.name : (activeField ? activeField.name : ""),
    placed_at: nowIso(), placed_by: crewWho(),
    gps_source: posSource,
    fix: (pos.fix != null ? pos.fix : null),
    hdop: (pos.hdop != null ? pos.hdop : null),
    acc: (pos.acc != null ? pos.acc : null),
    synced: false,
  };
  await beeDB.addShelterScan(rec).catch(() => {});
  syncShelterRec(rec);            // push to Firebase if online (else stays queued)
  await refreshScanLayer();
  await updateScanProgress();
  if (navigator.vibrate) navigator.vibrate(60);
  if (!fld) setScanStatus(`Shelter ${qr} saved — but it's outside every field boundary.`, "warn");
  else setScanStatus(`Shelter ${qr} placed in ${fld.name}.`, "ok");
  flashRecent("Shelter " + qr, fld ? fld.name : "no field");
  return true;
}

async function handleTrayScan(qr) {
  if (!trayShelterQr) {
    trayShelterQr = qr;                       // first tray-mode scan = the shelter
    document.getElementById("btn-scan-clearshelter").classList.remove("hidden");
    await updateTrayContext();
    setScanStatus(`Shelter ${qr} selected — now scan each tray going in it.`, "ok");
    if (navigator.vibrate) navigator.vibrate(40);
    return true;
  }
  const rec = {
    tray_qr: qr, shelter_qr: trayShelterQr,
    field_id: scanFieldId() || "",
    scanned_at: nowIso(), scanned_by: crewWho(),
    synced: false,
  };
  await beeDB.addTrayScan(rec).catch(() => {});
  syncTrayRec(rec);               // push to Firebase if online (else stays queued)
  await updateTrayContext();
  if (navigator.vibrate) navigator.vibrate(60);
  setScanStatus(`Tray ${qr} added to shelter ${trayShelterQr}.`, "ok");
  flashRecent("Tray " + qr, "→ " + trayShelterQr);
  return true;
}

async function updateTrayContext() {
  const box = document.getElementById("scan-context");
  if (!box) return;
  if (scanMode !== "tray") { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  if (!trayShelterQr) { box.textContent = "Scan a shelter QR first to attach trays to it."; return; }
  const all = (await beeDB.allTrayScans().catch(() => [])) || [];
  const n = all.filter((t) => t.shelter_qr === trayShelterQr).length;
  box.innerHTML = `Shelter <b>${trayShelterQr}</b> &mdash; <b>${n}</b> tray${n === 1 ? "" : "s"} scanned`;
}

async function refreshScanLayer() {
  if (!map.getSource("scans")) return;
  const fid = scanFieldId();
  const all = (await beeDB.allShelterScans().catch(() => [])) || [];
  const here = all.filter((s) => !fid || s.field_id === fid);
  map.getSource("scans").setData({
    type: "FeatureCollection",
    features: here.map((s) => ({
      type: "Feature", properties: { type: "scan", label: s.shelter_qr },
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    })),
  });
}

async function updateScanProgress() {
  const el = document.getElementById("scan-progress");
  if (!el) return;
  const fid = scanFieldId();
  const all = (await beeDB.allShelterScans().catch(() => [])) || [];
  const scanned = all.filter((s) => !fid || s.field_id === fid).length;
  const planned = fieldProgress().total;
  const pct = planned ? Math.round((scanned / planned) * 100) : 0;
  el.textContent = planned ? `${scanned}/${planned} · ${pct}%` : `${scanned} scanned`;
}

function flashRecent(title, sub) {
  const box = document.getElementById("scan-recent");
  if (!box) return;
  const row = document.createElement("div"); row.className = "row";
  const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  row.innerHTML = `<span>${title}</span><small>${sub} · ${t}</small>`;
  box.prepend(row);
  while (box.children.length > 6) box.removeChild(box.lastChild);
}

function setScanMode(m) {
  scanMode = m;
  document.getElementById("scan-mode-shelter").classList.toggle("active", m === "shelter");
  document.getElementById("scan-mode-tray").classList.toggle("active", m === "tray");
  document.getElementById("btn-scan-clearshelter").classList.toggle("hidden", m !== "tray" || !trayShelterQr);
  updateTrayContext();
  setScanStatus(m === "shelter"
    ? "Scan a shelter QR to drop a pin at your position."
    : "Scan a shelter QR, then scan each tray going in it.", "");
  focusScanInput();
}

async function openScan() {
  closeSheet("fieldsheet"); closeSheet("pointsheet"); closeSheet("checklistsheet");
  await refreshScanLayer();
  await updateScanProgress();
  await updateTrayContext();
  flushScans();                 // push anything queued while offline
  show("scansheet");
  setScanMode(scanMode);
}

// ---- Firebase sync of scans (Phase B) ---------------------------------------
// Every scan is saved locally first; these push it to the persistent Firebase
// tree when the relay is up and mark the local record synced. On the ESP32 (http,
// no SDK) the push is a no-op and the record stays queued until the tablet has
// internet again, when flushScans() drains the queue.
function syncShelterRec(rec) {
  if (!window.beePublish) return;
  const p = beePublish.pushShelterScan(rec);
  if (p) p.then(() => { rec.synced = true; beeDB.addShelterScan(rec).catch(() => {}); })
         .catch(() => {});
}
function syncTrayRec(rec) {
  if (!window.beePublish) return;
  const p = beePublish.pushTrayScan(rec);
  if (p) p.then(() => { rec.synced = true; beeDB.addTrayScan(rec).catch(() => {}); })
         .catch(() => {});
}
async function flushScans() {
  if (!window.beePublish || !beePublish.enabled) return;
  for (const s of (await beeDB.allShelterScans().catch(() => [])) || []) if (!s.synced) syncShelterRec(s);
  for (const t of (await beeDB.allTrayScans().catch(() => [])) || []) if (!t.synced) syncTrayRec(t);
}

// ---- Camera QR (secure context only; hardware scanner is the field path) ----
let camStream = null, camRAF = null, camDetector = null, camCanvas = null, _lastJsqrTs = 0, _camArmed = true;
async function openCamera() {
  if (!window.isSecureContext) {
    setScanStatus("Camera needs https — use a hardware scanner on the field network.", "warn"); return;
  }
  const hasBD = ("BarcodeDetector" in window);          // fast native path (Android Chrome)
  if (!hasBD && typeof jsQR !== "function") {            // jsQR = cross-platform fallback (iOS)
    setScanStatus("Can't decode QR by camera here — use a hardware scanner.", "warn"); return;
  }
  try {
    camDetector = hasBD ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    const v = document.getElementById("cam-video");
    v.srcObject = camStream;
    v.muted = true; v.setAttribute("playsinline", ""); v.setAttribute("autoplay", "");  // iOS: inline, no fullscreen
    await v.play();
    _camArmed = true;
    document.getElementById("camoverlay").classList.remove("hidden");
    scanCameraLoop();
  } catch (e) {
    setScanStatus("Couldn't open the camera: " + (e.message || e), "err");
  }
}
async function scanCameraLoop() {
  if (!camStream) return;
  try {
    const v = document.getElementById("cam-video");
    let val = null;
    if (camDetector) {
      const codes = await camDetector.detect(v);
      if (codes && codes.length) val = codes[0].rawValue;
    } else if (typeof jsQR === "function" && v.videoWidth) {
      const t = Date.now();
      if (t - _lastJsqrTs >= 120) {                     // throttle the software decode
        _lastJsqrTs = t;
        if (!camCanvas) camCanvas = document.createElement("canvas");
        const s = Math.min(1, 800 / Math.max(v.videoWidth, v.videoHeight));  // downscale for speed
        const w = Math.round(v.videoWidth * s), h = Math.round(v.videoHeight * s);
        camCanvas.width = w; camCanvas.height = h;
        const ctx = camCanvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(v, 0, 0, w, h);
        const code = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "dontInvert" });
        if (code) val = code.data;
      }
    }
    if (val) {
      if (_camArmed && (val !== _lastCamVal || Date.now() - _lastCamTs > 1500)) {
        _camArmed = false;                       // disarm until the code leaves the frame
        _lastCamVal = val; _lastCamTs = Date.now();
        const ok = await handleScan(val);
        if (ok && scanMode === "shelter") { closeCamera(); return; }   // one shelter, then close
      }
    } else {
      _camArmed = true;                          // frame cleared — ready for the next code
    }
  } catch (e) { /* transient — keep looping */ }
  camRAF = requestAnimationFrame(scanCameraLoop);
}
function closeCamera() {
  if (camRAF) { cancelAnimationFrame(camRAF); camRAF = null; }
  if (camStream) { camStream.getTracks().forEach((t) => t.stop()); camStream = null; }
  document.getElementById("camoverlay").classList.add("hidden");
  focusScanInput();
}

// ---- Display preferences (More sheet) ---------------------------------------
function applyDisplayPrefs() {
  document.body.classList.toggle("hc", highContrast);
  toggleEl("legend", mode === "work" && legendPref);
  const hcs = document.getElementById("tog-hc"); if (hcs) hcs.classList.toggle("on", highContrast);
  const lgs = document.getElementById("tog-legend"); if (lgs) lgs.classList.toggle("on", legendPref);
  const ui = document.getElementById("unit-imp"); if (ui) ui.classList.toggle("active", units === "imperial");
  const um = document.getElementById("unit-met"); if (um) um.classList.toggle("active", units === "metric");
}
function setUnits(u) {
  units = u; try { localStorage.setItem("beeUnits", u); } catch (e) {}
  applyDisplayPrefs();
  if (pos) updateStatus(pos, posSource);          // refresh the pill's accuracy string
}
function toggleHC() { highContrast = !highContrast; try { localStorage.setItem("beeHC", highContrast ? "1" : "0"); } catch (e) {} applyDisplayPrefs(); }
function toggleLegend() { legendPref = !legendPref; try { localStorage.setItem("beeLegend", legendPref ? "1" : "0"); } catch (e) {} applyDisplayPrefs(); }
function openMore() {
  const cn = document.getElementById("crewname");
  if (cn && window.beePublish) cn.textContent = window.beePublish.getCrew().name;
  applyDisplayPrefs();
  show("moresheet");
}

// ---- Online / offline indicator ---------------------------------------------
function updateNet() {
  document.getElementById("netpill").classList.toggle("hidden", navigator.onLine);
}

// ---- Wire up ----------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  connectPosition();          // globe over ws:// (http hosting only)
  startGeo();                 // tablet GPS — the source on https, fallback on http
  statusWatchdog();           // source pill + brings GPS up if the globe drops
  // Offline app shell on real https hosting (e.g. GitHub Pages). Skipped on
  // localhost so it can't serve stale code during local sim development.
  const lh = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if ("serviceWorker" in navigator && window.isSecureContext && !lh) {
    navigator.serviceWorker.register("sw.js").catch((e) => console.warn("SW reg failed", e));
  }
  if (window.beeTiles) beeTiles.evictIfNeeded();   // trim cache if it grew past the cap
  updateNet();
  window.addEventListener("online", updateNet);
  window.addEventListener("offline", updateNet);

  // Defensive wiring: a missing element (e.g. a stale cached index.html) must
  // never throw and halt the rest of the wiring — that's what froze the app.
  const bind = (id, ev, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(ev, fn); else console.warn("missing #" + id);
  };

  // Field-update alert: baseline now, then poll while online + on focus/reconnect.
  checkFieldUpdates();
  setInterval(checkFieldUpdates, 45000);
  window.addEventListener("online", checkFieldUpdates);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkFieldUpdates(); });

  applyDisplayPrefs();

  // Screen switcher + field switcher
  bind("btn-work", "click", () => { if (!activeField) setMode("map"); else setMode("work"); });
  bind("btn-map", "click", () => setMode("map"));
  bind("btn-system", "click", () => setMode("system"));
  bind("fieldswitch", "click", () => setMode("map"));

  // Work-mode view + camera
  bind("btn-2d", "click", () => setView("2d"));
  bind("btn-3d", "click", () => setView("3d"));
  bind("btn-follow", "click", toggleFollow);

  // Bottom action bar
  bind("btn-layers", "click", toggleLayersPanel);
  bind("btn-calibrate", "click", startCalibration);
  bind("btn-markplaced", "click", markPlaced);
  bind("btn-scan", "click", openScan);
  bind("btn-checklist", "click", openChecklist);
  flushCalibQueue();
  window.addEventListener("online", flushCalibQueue);
  setInterval(flushCalibQueue, 30000);

  // Layer slide-over
  bind("btn-close-layers", "click", () => closeSheet("layerscrim"));
  bind("btn-layers-done", "click", () => closeSheet("layerscrim"));
  bind("btn-layers-default", "click", resetLayersDefault);
  bind("layerscrim", "click", (e) => { if (e.target.id === "layerscrim") closeSheet("layerscrim"); });

  // More sheet: crew, units, prefs, manage fields
  bind("btn-more", "click", openMore);
  bind("btn-close-more", "click", () => closeSheet("moresheet"));
  bind("unit-imp", "click", () => setUnits("imperial"));
  bind("unit-met", "click", () => setUnits("metric"));
  bind("tog-hc", "click", toggleHC);
  bind("tog-legend", "click", toggleLegend);
  bind("btn-managefields", "click", () => { closeSheet("moresheet"); loadFieldList(); show("fieldsheet"); });

  bind("btn-close-fields", "click", () => closeSheet("fieldsheet"));
  bind("btn-close-checklist", "click", () => closeSheet("checklistsheet"));

  // Scan drawer + camera
  bind("btn-scan", "click", openScan);
  bind("btn-close-scan", "click", () => closeSheet("scansheet"));
  bind("scan-mode-shelter", "click", () => setScanMode("shelter"));
  bind("scan-mode-tray", "click", () => { trayShelterQr = null; setScanMode("tray"); });
  bind("btn-scan-clearshelter", "click", () => {
    trayShelterQr = null;
    document.getElementById("btn-scan-clearshelter").classList.add("hidden");
    updateTrayContext();
    setScanStatus("Scan a shelter QR to attach trays to it.", "");
    focusScanInput();
  });
  bind("btn-scan-camera", "click", openCamera);
  bind("btn-cam-close", "click", closeCamera);
  const si = document.getElementById("scan-input");
  if (si) si.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); const v = si.value; si.value = ""; handleScan(v); }
  });
  // Drain the offline scan queue to Firebase on reconnect + periodically.
  window.addEventListener("online", () => setTimeout(flushScans, 1500));
  setInterval(flushScans, 20000);
  bind("btn-sync", "click", () => syncAll());
  bind("btn-topsync", "click", manualSyncNow);
  bind("btn-close-point", "click", () => { commitPoint(); closeSheet("pointsheet"); });
  const ptv = document.getElementById("pt-visited");
  if (ptv) ptv.onchange = commitPoint;

  // Crew identity (shown on the office Monitor view).
  const cn = document.getElementById("crewname");
  if (cn && window.beePublish) cn.textContent = window.beePublish.getCrew().name;
  bind("btn-crew", "click", () => {
    const name = prompt("Crew name (shown on the office map):",
                        window.beePublish ? window.beePublish.getCrew().name : "");
    if (name && window.beePublish) { window.beePublish.setCrew(name); if (cn) cn.textContent = name; }
  });
});
