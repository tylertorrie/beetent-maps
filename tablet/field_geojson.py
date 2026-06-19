"""Build the tablet GeoJSON for a field.

Pure functions — no GUI, no tkinter. The desktop app computes shelter positions
(it already has the geometry engine) and hands them here as plain lat/lon lists;
this module just serialises them into the FeatureCollection the PWA consumes.

One file per field is written under tablet/fields/, plus an index.json manifest
the PWA fetches to list available fields. Both ride the existing GitHub auto-sync.
"""
from __future__ import annotations

import datetime
import json
import math
import re
from pathlib import Path

TABLET_FIELDS_DIR = Path(__file__).resolve().parent / "fields"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "field"


def _circle_ring(lat: float, lon: float, radius_m: float, n: int = 64):
    """[lon,lat] ring approximating a circle of radius_m around (lat,lon).
    Equirectangular approximation — plenty accurate at field/pivot scale."""
    ring = []
    mlat = 111320.0
    mlon = 111320.0 * max(math.cos(math.radians(lat)), 1e-6)
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        ring.append([lon + (radius_m * math.sin(a)) / mlon,
                     lat + (radius_m * math.cos(a)) / mlat])
    return ring


def field_filename(company: str, year: str, name: str) -> str:
    """Stable per-field filename, e.g. Corteva__2026__North_Quarter.geojson."""
    return f"{_slug(company)}__{_slug(year)}__{_slug(name)}.geojson"


def build_feature_collection(field: dict, shelter_latlons, boundary_latlon=None,
                             shelter_trays=None, tracks=None) -> dict:
    """field: the current_field dict. shelter_latlons: [(lat, lon), ...] as drawn.
    boundary_latlon: [[lat, lon], ...] or None.
    shelter_trays: [int, ...] aligned 1:1 with shelter_latlons (tray count per
        shelter), or None.
    tracks: [(center_lat, center_lon, radius_m), ...] pivot wheel-track circles,
        or None.
    Returns a GeoJSON dict."""
    features = []

    if boundary_latlon:
        ring = [[float(lon), float(lat)] for lat, lon in boundary_latlon]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"type": "boundary", "label": field.get("Name", "")},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })

    for (clat, clon, radius_m) in (tracks or []):
        try:
            features.append({
                "type": "Feature",
                "properties": {"type": "pivot_track", "radius_m": float(radius_m)},
                "geometry": {"type": "LineString",
                             "coordinates": _circle_ring(float(clat), float(clon), float(radius_m))},
            })
        except (TypeError, ValueError):
            pass

    for i, (lat, lon) in enumerate(shelter_latlons, 1):
        props = {"type": "shelter", "label": f"S-{i:02d}", "visited": False, "note": ""}
        if shelter_trays and i - 1 < len(shelter_trays):
            try:
                props["trays"] = int(shelter_trays[i - 1])
            except (TypeError, ValueError):
                pass
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        })

    pivot = None
    try:
        pivot = [float(field["PP_Longitude"]), float(field["PP_Latitude"])]
    except (KeyError, TypeError, ValueError):
        pass

    return {
        "type": "FeatureCollection",
        "name": field.get("Name", ""),
        "field": {
            "company": field.get("company", ""),
            "year": field.get("year", ""),
            "pivot": pivot,
        },
        "features": features,
    }


def write_field(field: dict, shelter_latlons, boundary_latlon=None,
                shelter_trays=None, tracks=None,
                fields_dir: Path = TABLET_FIELDS_DIR) -> Path:
    """Write one field's GeoJSON and refresh index.json. Returns the file path."""
    fields_dir.mkdir(parents=True, exist_ok=True)
    company = field.get("company", "")
    year = field.get("year", "")
    name = field.get("Name", "")
    fname = field_filename(company, year, name)
    fc = build_feature_collection(field, shelter_latlons, boundary_latlon,
                                  shelter_trays=shelter_trays, tracks=tracks)
    (fields_dir / fname).write_text(json.dumps(fc, indent=2), encoding="utf-8")
    _update_index(fields_dir, company, year, name, fname)
    return fields_dir / fname


def _update_index(fields_dir: Path, company: str, year: str, name: str, fname: str):
    index_path = fields_dir / "index.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"updated": "", "fields": []}

    entry = {"name": name, "company": company, "year": year, "file": fname}
    fields = [e for e in data.get("fields", []) if e.get("file") != fname]
    fields.append(entry)
    fields.sort(key=lambda e: (e.get("company", ""), e.get("year", ""), e.get("name", "")))
    data["fields"] = fields
    data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
