/**
 * Bee Tent Maps — Google Sheet scan-log endpoint (Phase D).
 *
 * SETUP
 *   1. Open the Google Sheet you want the scans logged to.
 *   2. Extensions → Apps Script. Delete any sample code, paste THIS file, Save.
 *   3. Deploy → New deployment → type "Web app".
 *        Execute as:      Me
 *        Who has access:  Anyone   (the desktop posts to it directly)
 *      Deploy, authorise, and copy the Web app URL (ends in /exec).
 *   4. In firebase_config.json (next to beetent_app.py) set:
 *        "sheets_url": "<that /exec URL>"
 *      Optionally set SECRET below and the matching "sheets_secret" in the JSON.
 *
 * The desktop POSTs {secret, rows:[{kind, key, ...}]} as scans arrive. Each row
 * carries a stable key (kind|field|qr) and is UPSERTED into the Shelters or
 * Trays tab on that key — so repeats, re-scans, and app restarts never create
 * duplicate rows (a re-scan just updates its existing row).
 *
 * Data tree: Field → Shelter → Trays  (Samples can be added as a 3rd tab later).
 */

var SECRET = "";   // optional shared secret; leave "" to skip the check

var COLS = {
  shelter: ["key", "field", "shelter_qr", "lat", "lon",
            "placed_at", "placed_by", "gps_source", "fix", "hdop", "acc"],
  tray:    ["key", "field", "shelter_qr", "tray_qr", "scanned_at", "scanned_by"]
};

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    if (SECRET && String(body.secret || "") !== SECRET) {
      return out_({ ok: false, error: "bad secret" });
    }
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var rows = body.rows || [];
    rows.forEach(function (r) { upsert_(ss, r); });
    return out_({ ok: true, n: rows.length });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// Quick browser check that the deployment is live.
function doGet() {
  return out_({ ok: true, service: "beetent scan log" });
}

function upsert_(ss, r) {
  var kind = (r.kind === "tray") ? "tray" : "shelter";
  var name = (kind === "tray") ? "Trays" : "Shelters";
  var cols = COLS[kind];
  var sh = ss.getSheetByName(name) || ss.insertSheet(name);
  if (sh.getLastRow() === 0) sh.appendRow(cols);

  var values = cols.map(function (c) { return (r[c] != null) ? r[c] : ""; });
  var key = String(r.key || "");
  var rowIdx = -1;
  var n = sh.getLastRow() - 1;            // data rows, excluding the header
  if (key && n > 0) {
    var keyCol = sh.getRange(2, 1, n, 1).getValues();
    for (var i = 0; i < keyCol.length; i++) {
      if (String(keyCol[i][0]) === key) { rowIdx = i + 2; break; }
    }
  }
  if (rowIdx > 0) sh.getRange(rowIdx, 1, 1, cols.length).setValues([values]);
  else sh.appendRow(values);
}

function out_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
