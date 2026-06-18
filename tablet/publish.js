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
  let enabled = false;
  let pos = null;       // last position object
  let field = "—";
  let total = 0;
  let placed = 0;

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
      field: field, placed: placed, total: total,
      ts: Date.now() / 1000,
    }).catch(() => { /* transient network error — next tick retries */ });
  }

  return {
    get enabled() { return enabled; },
    getCrew() { return { id: crewId(), name: crewName() }; },
    setCrew(name) { if (name) localStorage.setItem("beeCrewName", name); write(); },
    setPos(p) { pos = p; },
    setField(name, totalShelters) { field = name || "—"; total = totalShelters || 0; write(); },
    setProgress(placedCount) { placed = placedCount || 0; write(); },
    _init: init,
  };
})();

window.addEventListener("DOMContentLoaded", () => window.beePublish._init());
