/* Bee Tent Maps — Web planner shell (read-only preview).
 *
 * Reads fields from Supabase (or the bundled sample in demo mode) and draws the
 * geometry that lives directly in the field dict — boundary, pivot(s), tracks,
 * manual/test pins, entrance/parking, wet zones — on a satellite MapLibre map.
 *
 * NOT YET (later phases): the computed shelter grid (needs the placement engine),
 * editing, and role-gated writes. The overlay colours match the desktop + tablet.
 */
"use strict";

let map = null;
let allFields = [];          // [{company, year, name, updated_at}]
let activeKey = null;        // "company|year|name" currently shown

const COLORS = {
  boundary: "#00CED1", pivot: "#F5453D", pivot2: "#FF7A00", track: "#FF8A2B",
  manual: "#FFD700", manualEdge: "#1A1A1A", test: "#1E90FF", testEdge: "#0A3D7A",
  wet: "#39B7D6", entrance: "#16A34A", parking: "#F59E0B",
};

const $ = (id) => document.getElementById(id);
const fkey = (f) => `${f.company}|${f.year}|${f.name}`;

// ---- Map --------------------------------------------------------------------
const SAT_STYLE = {
  version: 8,
  sources: {
    esri: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256, maxzoom: 19, attribution: "Esri World Imagery",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#2C2A24" } },
    { id: "sat", type: "raster", source: "esri" },
  ],
};

function initMap() {
  map = new maplibregl.Map({
    container: "map", style: SAT_STYLE,
    center: [-112.201, 49.783], zoom: 12, attributionControl: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("load", () => {
    map.addSource("field", { type: "geojson", data: emptyFC() });
    map.addLayer({ id: "wet-fill", type: "fill",
      filter: ["==", ["get", "t"], "wet"], source: "field",
      paint: { "fill-color": COLORS.wet, "fill-opacity": 0.3 } });
    map.addLayer({ id: "boundary", type: "line",
      filter: ["==", ["get", "t"], "boundary"], source: "field",
      paint: { "line-color": COLORS.boundary, "line-width": 2.5 } });
    map.addLayer({ id: "track", type: "line",
      filter: ["==", ["get", "t"], "track"], source: "field",
      paint: { "line-color": COLORS.track, "line-width": 2, "line-dasharray": [3, 2] } });
    // Computed shelter grid — desktop-yellow with a dark outline (the outline is
    // what makes yellow read over satellite). Handed to us pre-computed; the web
    // never runs the placement engine.
    map.addLayer({ id: "shelters", type: "circle",
      filter: ["==", ["get", "t"], "shelter"], source: "field",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 3, 16, 6],
        "circle-color": COLORS.manual,
        "circle-stroke-color": COLORS.manualEdge, "circle-stroke-width": 1.6,
      } });
    map.addLayer({ id: "pins", type: "circle",
      filter: ["in", ["get", "t"], ["literal", ["manual", "test", "entrance", "parking", "pivot"]]],
      source: "field",
      paint: {
        "circle-radius": ["match", ["get", "t"], "pivot", 7, 6],
        "circle-color": ["get", "c"],
        "circle-stroke-color": ["get", "e"], "circle-stroke-width": 2,
      } });
  });
}

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

function circleRing(lat, lon, radM, n = 72) {
  const R = 6378137, out = [];
  for (let i = 0; i <= n; i++) {
    const a = (2 * Math.PI * i) / n;
    const dLat = ((radM * Math.cos(a)) / R) * (180 / Math.PI);
    const dLon = ((radM * Math.sin(a)) / (R * Math.cos((lat * Math.PI) / 180))) * (180 / Math.PI);
    out.push([lon + dLon, lat + dLat]);
  }
  return out;
}

function num(v) { const n = parseFloat(v); return Number.isFinite(n) ? n : null; }

/** Build a MapLibre FeatureCollection from the raw field dict. */
function fieldFeatures(f) {
  const feats = [];
  const pt = (t, lat, lon, c, e) => feats.push({
    type: "Feature", properties: { t, c: c || null, e: e || null },
    geometry: { type: "Point", coordinates: [lon, lat] },
  });

  const bp = f.boundary_polygon || [];
  if (bp.length >= 3) {
    feats.push({ type: "Feature", properties: { t: "boundary" },
      geometry: { type: "LineString", coordinates: bp.map(([la, lo]) => [lo, la]).concat([[bp[0][1], bp[0][0]]]) } });
  }
  (f.wet_zones || []).forEach((ring) => {
    if (ring && ring.length >= 3)
      feats.push({ type: "Feature", properties: { t: "wet" },
        geometry: { type: "Polygon", coordinates: [ring.map(([la, lo]) => [lo, la])] } });
  });

  const plat = num(f.PP_Latitude), plon = num(f.PP_Longitude);
  if (plat != null && plon != null) {
    (f.pivot_tracks || []).forEach((r) => {
      const rad = num(r); if (rad)
        feats.push({ type: "Feature", properties: { t: "track" },
          geometry: { type: "LineString", coordinates: circleRing(plat, plon, rad) } });
    });
    pt("pivot", plat, plon, COLORS.pivot, "#7a0f0f");
  }
  const p2lat = num(f.PP2_Latitude), p2lon = num(f.PP2_Longitude);
  if (f.two_pivots && p2lat != null && p2lon != null) {
    (f.pivot_tracks2 || []).forEach((r) => {
      const rad = num(r); if (rad)
        feats.push({ type: "Feature", properties: { t: "track" },
          geometry: { type: "LineString", coordinates: circleRing(p2lat, p2lon, rad) } });
    });
    pt("pivot", p2lat, p2lon, COLORS.pivot2, "#5a2900");
  }

  (f.manual_shelter_pins || []).forEach((p) => {
    const la = num(p[0]), lo = num(p[1]);
    if (la != null && lo != null) pt("manual", la, lo, COLORS.manual, COLORS.manualEdge);
  });
  (f.test_shelters || []).forEach((p) => {
    const la = num(p[0]), lo = num(p[1]);
    if (la != null && lo != null) pt("test", la, lo, COLORS.test, COLORS.testEdge);
  });
  if (Array.isArray(f.entrance_pin))
    pt("entrance", num(f.entrance_pin[0]), num(f.entrance_pin[1]), COLORS.entrance, "#0B5D27");
  if (Array.isArray(f.parking_pin))
    pt("parking", num(f.parking_pin[0]), num(f.parking_pin[1]), COLORS.parking, "#8A5E00");

  // Computed shelter grid (pushed by the desktop; [[lat,lon],...]).
  (f.computed_shelters || []).forEach((p) => {
    const la = num(p[0]), lo = num(p[1]);
    if (la != null && lo != null)
      feats.push({ type: "Feature", properties: { t: "shelter" },
        geometry: { type: "Point", coordinates: [lo, la] } });
  });

  return { type: "FeatureCollection", features: feats };
}

function fitTo(fc) {
  const b = new maplibregl.LngLatBounds();
  let any = false;
  const walk = (g) => {
    if (!g) return;
    if (g.type === "Point") { b.extend(g.coordinates); any = true; }
    else if (g.type === "LineString") g.coordinates.forEach((c) => { b.extend(c); any = true; });
    else if (g.type === "Polygon") g.coordinates.forEach((r) => r.forEach((c) => { b.extend(c); any = true; }));
  };
  fc.features.forEach((f) => walk(f.geometry));
  if (any) map.fitBounds(b, { padding: 70, maxZoom: 16, duration: 500 });
}

// ---- Field detail -----------------------------------------------------------
async function openField(meta) {
  activeKey = fkey(meta);
  renderList();
  $("maphint").classList.add("hidden");
  let f;
  try {
    f = await beeData.getField(meta.company, meta.year, meta.name);
  } catch (e) {
    $("maphint").textContent = "Couldn't load that field: " + (e.message || e);
    $("maphint").classList.remove("hidden");
    return;
  }
  if (!f) return;
  const fc = fieldFeatures(f);
  if (map.getSource("field")) map.getSource("field").setData(fc);
  fitTo(fc);

  const acres = num(f.acres);
  const nShelters = (f.computed_shelters || []).length;
  const nTracks = (f.pivot_tracks || []).length + (f.two_pivots ? (f.pivot_tracks2 || []).length : 0);
  $("fc-name").textContent = f.Name || meta.name;
  $("fc-sub").textContent = [f.company, f.year, f.lld].filter(Boolean).join(" · ");
  const stat = (k, v) => `<div class="fc-stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  $("fc-stats").innerHTML =
    stat("Acres", acres != null ? acres.toFixed(1) : "—") +
    stat("Shelters", nShelters || "—") +
    stat("Pivot tracks", nTracks) +
    stat("Manual pins", (f.manual_shelter_pins || []).length);
  // The grid is pushed by the desktop; if a field predates that it just needs a
  // re-save on the desktop to populate. Say so rather than showing a bare map.
  const note = $("fieldcard").querySelector(".fc-note");
  note.textContent = nShelters
    ? "Read-only preview. Editing arrives in a later phase."
    : "No computed shelter grid yet — re-save this field on the desktop to publish it.";
  $("fieldcard").classList.remove("hidden");
  $("legend").classList.remove("hidden");
}

// ---- Sidebar list -----------------------------------------------------------
function renderScopeOptions() {
  const cos = [...new Set(allFields.map((f) => f.company))].sort();
  const yrs = [...new Set(allFields.map((f) => String(f.year)))].sort().reverse();
  const fill = (sel, items, allLabel) => {
    const cur = sel.value;
    sel.innerHTML = `<option value="">${allLabel}</option>` +
      items.map((i) => `<option${i === cur ? " selected" : ""}>${i}</option>`).join("");
  };
  fill($("scope-co"), cos, "All companies");
  fill($("scope-yr"), yrs, "All years");
}

function renderList() {
  const q = ($("search").value || "").trim().toLowerCase();
  const co = $("scope-co").value, yr = $("scope-yr").value;
  const rows = allFields.filter((f) => {
    if (co && f.company !== co) return false;
    if (yr && String(f.year) !== yr) return false;
    if (q && ![f.name, f.company, String(f.year)].some((s) => String(s).toLowerCase().includes(q))) return false;
    return true;
  });
  const ul = $("fieldlist");
  ul.innerHTML = "";
  for (const f of rows) {
    const li = document.createElement("li");
    if (fkey(f) === activeKey) li.className = "active";
    li.innerHTML = `<div class="fl-name">${f.name}</div>` +
      `<div class="fl-sub">${f.company} · ${f.year}</div>`;
    li.onclick = () => openField(f);
    ul.appendChild(li);
  }
  $("listnote").textContent = `${rows.length} of ${allFields.length} field(s)` +
    (beeData.live() ? "" : " · demo");
}

async function loadFields() {
  $("listnote").textContent = "Loading…";
  try {
    allFields = await beeData.listFields();
  } catch (e) {
    allFields = [];
    $("listnote").textContent = "Load failed: " + (e.message || e);
    return;
  }
  renderScopeOptions();
  renderList();
}

// ---- Auth chrome ------------------------------------------------------------
function setMode() {
  const live = beeData.live();
  const badge = $("modebadge");
  badge.textContent = live ? "live" : "demo";
  badge.classList.toggle("live", live);
}

async function refreshWhoami() {
  const user = await beeData.currentUser();
  $("whoami").textContent = user ? user.email : "";
  $("btn-auth").textContent = user ? "Sign out" : (beeData.live() ? "Sign in" : "Demo mode");
}

function openAuth() { $("auth-err").textContent = ""; $("authscrim").classList.remove("hidden"); }
function closeAuth() { $("authscrim").classList.add("hidden"); }

async function doSignIn() {
  const email = $("auth-email").value.trim(), pass = $("auth-pass").value;
  $("auth-err").textContent = "";
  try {
    await beeData.signIn(email, pass);
    closeAuth();
    await refreshWhoami();
  } catch (e) {
    $("auth-err").textContent = e.message || String(e);
  }
}

// ---- Wire up ----------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  setMode();
  loadFields();
  refreshWhoami();
  beeData.onAuth(() => refreshWhoami());

  $("search").addEventListener("input", renderList);
  $("scope-co").addEventListener("change", () => { renderList(); });
  $("scope-yr").addEventListener("change", () => { renderList(); });
  $("btn-refresh").addEventListener("click", loadFields);
  $("fc-close").addEventListener("click", () => {
    $("fieldcard").classList.add("hidden"); $("legend").classList.add("hidden");
    activeKey = null; renderList();
    if (map.getSource("field")) map.getSource("field").setData(emptyFC());
    $("maphint").classList.remove("hidden");
  });

  $("btn-auth").addEventListener("click", async () => {
    const user = await beeData.currentUser();
    if (user) { await beeData.signOut(); await refreshWhoami(); return; }
    if (!beeData.live()) { openAuth(); return; }   // shows the demo-mode note
    openAuth();
  });
  $("auth-cancel").addEventListener("click", closeAuth);
  $("auth-go").addEventListener("click", doSignIn);
  $("auth-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doSignIn(); });
});
