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
  sources: {
    esri: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Esri World Imagery",
    },
  },
  layers: [{ id: "sat", type: "raster", source: "esri" }],
};

// ---- State ------------------------------------------------------------------
let map;
let meMarker;            // live position marker
let pos = null;          // last position object from the source
let mode = "overview";   // "overview" | "ground"
let activeField = null;  // GeoJSON FeatureCollection
let followGround = true; // recenter on each fix while in ground view
const visited = {};      // label -> {visited, note}  (local only for now)

// ---- Map setup --------------------------------------------------------------
function initMap() {
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

    map.addLayer({ id: "tracks-fill", type: "fill",
      filter: ["==", ["get", "type"], "pivot_track"],
      source: "field", paint: { "fill-color": "#FF6600", "fill-opacity": 0.15 } });

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
      layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.2] },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.2 } });

    map.on("click", "shelters", (e) => openPoint(e.features[0]));

    loadFieldList();
  });

  // In ground view a user pan turns off auto-follow until they re-enter ground.
  map.on("dragstart", () => { if (mode === "ground") followGround = false; });
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

// ---- Position source --------------------------------------------------------
function connectPosition() {
  let ws;
  try { ws = new WebSocket(WS_URL); }
  catch (e) { console.warn("WS construct failed", e); return; }

  ws.onmessage = (ev) => {
    try { onPosition(JSON.parse(ev.data)); } catch (e) { /* ignore junk frame */ }
  };
  ws.onclose = () => { setFix(null); setTimeout(connectPosition, 2000); };
  ws.onerror = () => ws.close();
}

function onPosition(p) {
  pos = p;
  meMarker.setLngLat([p.lon, p.lat]);
  if (typeof p.course === "number") meMarker.setRotation(p.course);
  if (!meMarker._map) meMarker.addTo(map);

  setFix(p.fix);
  document.getElementById("sats").textContent = p.sats ?? 0;
  document.getElementById("hdop").textContent = (p.hdop ?? "--");
  if (window.beePublish) window.beePublish.setPos(p);

  if (mode === "ground" && followGround) {
    map.easeTo({ center: [p.lon, p.lat], zoom: GROUND_ZOOM, duration: 250 });
  }
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
  ul.innerHTML = "";
  let items = [];
  try {
    const r = await fetch(FIELDS_INDEX, { cache: "no-store" });
    if (r.ok) items = (await r.json()).fields || [];
  } catch (e) { /* offline / no index yet */ }

  if (!items.length) {
    document.getElementById("syncnote").textContent =
      "No field index found — showing the bundled sample.";
    await loadField("fields/sample_field.geojson", "North Quarter (sample)");
  }

  for (const it of items) {
    const li = document.createElement("li");
    li.innerHTML = `${it.name}<small>${it.company} &middot; ${it.year}</small>`;
    li.onclick = () => { loadField("fields/" + it.file, it.name); closeSheet("fieldsheet"); };
    ul.appendChild(li);
  }
}

async function loadField(url, name) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    const fc = await r.json();
    activeField = fc;
    activeFieldFile = url.split("/").pop();   // e.g. Corteva__2026__North_Quarter.geojson
    // Re-apply any local visited state for this field.
    for (const f of fc.features) {
      if (f.properties.type === "shelter") {
        const v = visited[f.properties.label];
        if (v) { f.properties.visited = v.visited; f.properties.note = v.note; }
      }
    }
    map.getSource("field").setData(fc);
    const fname = name || fc.name || "Field";
    document.getElementById("fieldname").textContent = fname;
    publishFieldState(fname);
    fitToField();
  } catch (e) {
    console.error("loadField failed", e);
  }
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
  if (window.beePublish) {
    const p = fieldProgress();
    window.beePublish.setProgress(p.placed, p.placedIds);
  }
  // TODO: persist visited/notes to IndexedDB for offline (Phase 3).
}

// ---- View switching ---------------------------------------------------------
function setMode(m) {
  mode = m;
  document.getElementById("btn-overview").classList.toggle("active", m === "overview");
  document.getElementById("btn-ground").classList.toggle("active", m === "ground");
  if (m === "overview") fitToField();
  else { followGround = true; if (pos) map.easeTo({ center: [pos.lon, pos.lat], zoom: GROUND_ZOOM }); }
}

// ---- Sheets -----------------------------------------------------------------
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function closeSheet(id) { document.getElementById(id).classList.add("hidden"); }

// ---- Wire up ----------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  connectPosition();

  document.getElementById("btn-overview").onclick = () => setMode("overview");
  document.getElementById("btn-ground").onclick = () => setMode("ground");
  document.getElementById("btn-fields").onclick = () => { loadFieldList(); show("fieldsheet"); };
  document.getElementById("btn-close-fields").onclick = () => closeSheet("fieldsheet");
  document.getElementById("btn-sync").onclick = () => loadFieldList();
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
