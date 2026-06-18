# Bee Tent Field — tablet PWA (scaffold)

A browser app for the field crew. It shows the field, the live GPS position from
the John Deere globe (via an ESP32 WiFi bridge), and which shelters to place.

This folder is **Phase 1–2** of the field-tablet plan: the app runs against a
simulator now, with real field data flowing from the desktop app via GitHub.

## Run the simulator (no hardware, no installs)

```powershell
cd C:\Users\tyler\beetent-maps\tablet
python sim_server.py
```

Then open <http://localhost:8000>. You'll see a simulated unit driving a loop
around the sample field, with a live heading arrow, RTK fix badge, and the
Overview / Ground views. Stop with Ctrl+C.

> Open `http://localhost:8000`, **not** the `index.html` file directly — the app
> needs to be served over HTTP for the WebSocket and tiles to work, exactly like
> it will be on the ESP32.

## How the pieces map to the real system

| Scaffold piece            | Real deployment                                        |
|---------------------------|--------------------------------------------------------|
| `sim_server.py` HTTP :8000 | ESP32 serves these static files over its WiFi AP       |
| `sim_server.py` WS :8081   | ESP32 streams parsed NMEA from the globe over WebSocket |
| `fields/*.geojson`         | Written by the desktop app, synced via GitHub          |
| `fields/index.json`        | Manifest the desktop app maintains; PWA lists from it   |

## Field data

The desktop app (`beetent_app.py`) writes one GeoJSON per field here on save,
using `field_geojson.py`. Shelter points are computed by the existing geometry
engine — the PWA does **no** math, it only plots.

## Not done yet (later phases)

- Vendor MapLibre locally (currently CDN) so it works offline on the ESP32 AP.
- IndexedDB cache + service worker for true offline use.
- Visited/notes write-back beyond the current browser session.
- Live position relay to the desktop "Monitor" view (Phase 4).
