/* Publish this crew's live position + progress to the Firebase relay.
 *
 * Drives the desktop Monitor view. Entirely optional and online-only: if there's
 * no firebase-config.js, no internet, or the SDK didn't load, this no-ops and the
 * rest of the app works unchanged.
 *
 * Writes to  crews/<crewId>  at ~0.5 Hz (well within the free tier). onDisconnect
 * removes the node so a crew drops off the office map when the tablet goes away.
 */
"use strict";

window.beePublish = (function () {
  const PUSH_MS = 2000;

  let ref = null;       // firebase db ref for this crew
  let scansRef = null;  // persistent scans/<field>/{shelters,trays}/<qr> tree
  let calibRef = null;  // calibration/<field> — crew bay-shift corrections
  let enabled = false;

  // Firebase keys may not contain . # $ [ ] / — sanitise field ids and QR codes.
  function fbKey(s) { return String(s == null ? "" : s).replace(/[.#$\[\]\/]/g, "_"); }
  function fieldKey(fieldId) {
    return fbKey(String(fieldId || "").replace(/\.geojson$/i, "")) || "_nofield";
  }
  let pos = null;       // last position object
  let field = "—";
  let fieldFile = null; // geojson filename — lets the office locate the geometry
  let total = 0;
  let placed = 0;
  let placedIds = [];   // labels of placed shelters, for the Monitor state-mirror

  function crewId() {
    let id = localStorage.getItem("beeCrewId");
    if (!id) { id = "crew-" + Math.random().toString(36).slice(2, 6); localStorage.setItem("beeCrewId", id); }
    return id;
  }
  function crewName() {
    return localStorage.getItem("beeCrewName") || crewId();
  }

  function init() {
    if (!window.FIREBASE_CONFIG || typeof firebase === "undefined") {
      console.info("Relay publish disabled (no firebase-config.js or offline).");
      return;
    }
    try {
      firebase.initializeApp(window.FIREBASE_CONFIG);
      ref = firebase.database().ref("crews/" + crewId());
      ref.onDisconnect().remove();
      scansRef = firebase.database().ref("scans");   // persistent — NOT removed
      calibRef = firebase.database().ref("calibration");
      enabled = true;
      setInterval(write, PUSH_MS);
      console.info("Relay publish enabled as", crewId());
    } catch (e) {
      console.warn("Relay publish init failed:", e);
    }
  }

  function write() {
    if (!enabled || !ref || !pos) return;
    ref.set({
      id: crewId(), name: crewName(),
      lat: pos.lat, lon: pos.lon, course: pos.course ?? null,
      fix: pos.fix ?? 0, sats: pos.sats ?? 0, hdop: pos.hdop ?? null,
      field: field, field_file: fieldFile, placed: placed, total: total,
      placed_ids: placedIds,
      ts: Date.now() / 1000,
    }).catch(() => { /* transient network error — next tick retries */ });
  }

  return {
    get enabled() { return enabled; },
    getCrew() { return { id: crewId(), name: crewName() }; },
    setCrew(name) { if (name) localStorage.setItem("beeCrewName", name); write(); },
    setPos(p) { pos = p; },
    setField(name, totalShelters, file) {
      field = name || "—"; total = totalShelters || 0; fieldFile = file || null; write();
    },
    setProgress(placedCount, ids) {
      placed = placedCount || 0; placedIds = ids || []; write();
    },
    // Push a scan to the persistent scans tree. Returns the set() Promise so the
    // caller can mark the local record synced, or null when the relay is down
    // (offline / no SDK) — the caller leaves it queued and flushes on reconnect.
    pushShelterScan(rec) {
      if (!enabled || !scansRef || !rec || !rec.shelter_qr) return null;
      return scansRef.child(fieldKey(rec.field_id)).child("shelters")
                     .child(fbKey(rec.shelter_qr)).set(rec);
    },
    pushTrayScan(rec) {
      if (!enabled || !scansRef || !rec || !rec.tray_qr) return null;
      return scansRef.child(fieldKey(rec.field_id)).child("trays")
                     .child(fbKey(rec.tray_qr)).set(rec);
    },
    // Crew bay-shift calibration → calibration/<field>. Returns the set() Promise
    // (so the caller can mark it sent) or null when the relay is down (offline).
    pushCalibration(fieldId, rec) {
      if (!enabled || !calibRef || !fieldId || !rec) return null;
      return calibRef.child(fieldKey(fieldId)).set(rec);
    },
    _init: init,
  };
})();

window.addEventListener("DOMContentLoaded", () => window.beePublish._init());
