# Notes for Claude Code — Bee Tent Maps

## What this project is

A Windows desktop GUI app (`beetent_app.py`) for laying out leafcutter bee shelter
positions on satellite maps for canola pollination operations. The company is
**Corteva** (main user: Tyler). Fields are pivot-irrigated circles or polygon
boundaries. The app places shelter pins at calculated positions within female bays,
exports GPS coordinates, and generates PDF maps.

## Running the app

```powershell
cd C:\Users\tyler\beetent-maps   # or wherever cloned
python beetent_app.py
```

Dependencies: `customtkinter tkintermapview pillow requests fpdf2`
Install with: `python -m pip install customtkinter tkintermapview pillow requests fpdf2`

## Repository

- **GitHub:** https://github.com/tylertorrie/beetent-maps
- **Remote name:** `origin`
- **Branch:** `master`
- Token stored in the git remote URL (Windows Credential Manager also has it)

### Syncing workflow

Field data and code both live in the repo. The app auto-syncs:
- **On startup** → `git pull` (picks up changes from other device)
- **On save/delete field** → `git add fields/ && git commit && git push`
- **On bay preset change** → same

A Claude Code **Stop hook** (`.claude/settings.json`) also auto-commits and pushes
the whole repo (`git add -A`) after each Claude turn, so code/doc changes sync to
GitHub without a manual push. Remove or edit it via `/hooks` if undesired.

To sync code changes after editing `beetent_app.py` or `maketentgrid.py`:
```powershell
git add beetent_app.py maketentgrid.py
git commit -m "description"
git push origin master
```

## File structure

```
beetent_app.py        — Main GUI (customtkinter + tkintermapview). ~1400 lines.
maketentgrid.py       — Core calculation engine. get_tent_positions() is the key fn.
utmish.py             — ENU coordinate conversion (local East-North-Up)
shapefile.py          — Vendored pyshp for .shp boundary upload
simplekml/            — Vendored for KML export
fpdf/                 — Vendored PDF generation
fields/               — Saved field JSON files (git-tracked, auto-synced)
  bay_presets.json    — Saved bay calculator presets
  Corteva/2026/       — Field files per company/year
.gitignore            — Excludes CSV, ODS, build output, Claude worktrees
```

## Architecture — beetent_app.py

### Data model

`current_field` dict (also saved as JSON in `fields/`):
```python
{
  "Name": "",
  "company": "Corteva", "year": "2026",
  "PP_Latitude": "", "PP_Longitude": "",   # pivot point
  "Spray_angle": "0",                       # planting angle in degrees
  "Sprayer_width": "133",                   # ft — auto-filled by bay calculator
  "num_structures": "",                     # target shelter count
  "spacing": "",                            # along-row spacing (ft or m)
  "shelter_spacing": "",                    # lateral shelter spacing override
  "directional_offset": "",
  "row_spacing_in": "22",                   # inches — bay calculator input
  "num_female_rows": "8",                   # bay calculator input
  "num_male_rows": "2",                     # bay calculator input
  "boundary_polygon": [[lat,lon], ...],     # drawn or uploaded boundary
  "pivot_tracks": [radius_m, ...],          # track exclusion circles
  "corner_arms": [[], []],                  # corner arm paths
  "shelter_overrides": {idx: [lat,lon]}     # manually dragged shelter positions
}
```

### Key methods

| Method | What it does |
|--------|-------------|
| `_redraw_shelters()` | Calls `maketentgrid.get_tent_positions()`, draws yellow pins |
| `_redraw_tracks()` | Draws orange pivot track circles + grey ↔ drag handles |
| `_redraw_passes()` | Draws sprayer pass lines + red outer sprayer boundary polygon |
| `_redraw_bays()` | Draws green male bay bands over the field |
| `_redraw_boundary()` | Draws the field boundary polygon + vertex drag handles |
| `_calc_bays()` | Computes female_m, male_m, auto-fills Sprayer_width |
| `_git_pull()` | Background thread: git pull on startup |
| `_git_push(msg)` | Background thread: git add/commit/push fields/ |
| `_upload_boundary()` | File dialog for .shp/.kml/.kmz boundary import |
| `_bind_drag_system()` | Wires up pin drag — called 300ms after init |

### Drag system

All draggable pins (pivot, shelter, track handles, boundary vertices) use a single
registry-based system. No tkintermapview marker is used as a drag indicator —
instead a plain canvas oval is drawn to avoid `canvas.update()` reentrancy bugs.

```python
_drag_registry   # dict: key → {lat, lon, circle_color, outside_color, update_fn}
_drag_item       # key of pin currently being dragged (None if not dragging)
_drag_temp_oval  # canvas item ID of the drag indicator oval
_drag_moved      # True once mouse moved >4px from press point
_just_dragged    # True for one click cycle after drag ends (prevents click firing)
```

`<ButtonPress-1>`, `<B1-Motion>`, `<ButtonRelease-1>` all bound on `map_widget.canvas`
**without** `add="+"` — they replace tkintermapview's handlers. When no pin is hit,
events are forwarded to `map_widget.button_press/mouse_move/button_release`.

During pin drag, `canvas.scan_mark(event.x, event.y)` is called on every motion
event to prevent tkintermapview from panning the map.

### Overlay colours

| Element | Colour |
|---------|--------|
| Shelter pins | Yellow `#FFD700` / dark gold `#B8860B` |
| Pivot point | Red / dark red |
| Track circles | Orange `#FF6600` |
| Track handles | Grey `#999999` / `#555555` |
| Boundary polygon | Yellow `#FFD700` outline |
| Male bay bands | Green `#003300` fill |
| Outer sprayer limit | Bright red `#FF2200` outline, 2px, always visible |
| Sprayer pass lines | Red `#FF3333`, 1px, toggle-able |

## Architecture — maketentgrid.py

### get_tent_positions(field_dict, use_metric=True)

Returns `[(lat, lon), ...]` in NW-snake order. Key logic:

1. **Row width**: when bay params (num_female_rows, num_male_rows, row_spacing_in)
   are present, uses `tent_row_width = female_m + male_m` — NOT the Sprayer_width
   field — so shelter rows always align with actual female bay centres regardless
   of what the user typed in Sprayer_width.

2. **lat_offset** (shelter position within female bay):
   ```python
   female_m = (num_female_rows + 1) * row_spacing_in * 0.0254
   lat_offset = max(0.0, female_m / 2 - 1.2192)  # 4ft = 1.2192m from male bay edge
   ```
   This places the shelter in the female bay, 4ft from the male bay boundary,
   in the lateral (cross-row) direction. Works for any planting angle because
   lat_offset is in the pre-rotation coordinate frame.

3. **Spacing priority**:
   - User-given spacing → use as-is, do NOT trim to num_structures
   - num_structures (no spacing) → `find_exact_spacing()` then trim
   - Neither → `calculate_spacing()` auto

4. **NW-snake sort**: shelters numbered from NW corner, snaking S then N through rows.

### Sprayer_width vs tent_row_width

`Sprayer_width` (ft, from field dict) is used for:
- Sprayer pass overlay drawing
- Legacy CSV processing (`process_field_data`)

`tent_row_width` (metres, computed from bay params) is used for:
- Shelter grid row spacing in `get_tent_positions()`
- `find_exact_spacing()` and `calculate_spacing()` calls within that function

These must be kept separate to avoid shelters landing in male bays when the
user hasn't updated Sprayer_width to match their actual bay configuration.

## Bay calculator

UI fields: `row_spacing_in` (inches), `num_female_rows`, `num_male_rows`

`_calc_bays()` computes:
```python
f_in = (nf + 1) * rs    # female bay width in inches
m_in = (nm + 1) * rs    # male bay width in inches
Sprayer_width = (f_in + m_in) / 12  # auto-filled in feet
```

Bay presets saved to `fields/bay_presets.json` as a list of dicts, each with
a `"name"` key and the four field values. Loaded at startup, shown in a
CTkComboBox. New/Delete buttons manage the list.

## Outer sprayer boundary

Always drawn (not gated by show_passes toggle) in `_redraw_passes()`:
```python
inset = inset_polygon_enu(poly_enu, width_m)  # one sprayer-width inside boundary
self.outer_sprayer_poly = map_widget.set_polygon(inset_latlon, outline_color="#FF2200")
```

`inset_polygon_enu()` is a module-level function that offsets polygon edges inward.
Works well for convex (typical farm field) polygons.

## Boundary upload

`_upload_boundary()` handles:
- `.shp` → `shapefile.Reader(path).shape(0).points`
- `.kml` → `xml.etree.ElementTree` parse, extract `<coordinates>`
- `.kmz` → `zipfile` extract → KML parse

Boundary stored as `current_field["boundary_polygon"] = [[lat, lon], ...]`

## Pivot tracks

- Stored as `current_field["pivot_tracks"] = [radius_m, ...]`
- Each track draws two orange circles (±exclusion zone) + a grey ↔ drag handle
- Handle draggable via drag system → `_on_track_drag(idx, lat, lon)`
- Toggle visibility: `show_tracks` BooleanVar, `_toggle_tracks()`
- Delete via popup dialog (`_mode_delete_track_ui`)

## Known issues / recent fixes

- **Duplicate pins on drag**: was caused by `tkintermapview.marker.delete()` calling
  `canvas.update()` which re-entered the event loop mid-delete. Fixed by replacing
  the tkintermapview temp marker with a plain `canvas.create_oval()` item.

- **Shelters in male bay at some angles**: was caused by Sprayer_width mismatch.
  Fixed by using `tent_row_width = female_m + male_m` from bay params.

- **Old track resize mechanism**: old `_make_resize_cb` / `_on_track_resize_motion`
  bound `<Motion>` and caused repeated `_redraw_tracks()` calls during drag.
  Both methods are now no-ops; drag system handles everything.

## Syntax check workflow

Always run before copying to main directory:
```powershell
cd "path\to\worktree"
"C:\Users\tyler\AppData\Local\Programs\Python\Python312\python.exe" -c "import ast; ast.parse(open('beetent_app.py', encoding='utf-8').read()); print('OK')"
```

Then copy:
```powershell
Copy-Item "worktree\beetent_app.py" "C:\Users\tyler\beetent-maps\beetent_app.py" -Force
```

## Roadmap

- **Web / phone access**: app is currently desktop-only (tkinter). Eventual plan is
  Flask/FastAPI backend + Leaflet.js frontend, reusing `maketentgrid.py` as-is.
  The GitHub repo + auto-sync infrastructure is already in place for this.

- **Google Drive integration**: extend `maketentgrid.py` to read field CSVs directly
  from Google Drive. Extend the existing CSV input path (GUI file picker + `csv_file`
  CLI arg). Don't propose unprompted.

- **Sync button**: a "☁ Sync" button that forces an immediate git pull/push cycle,
  for users who want to manually trigger sync outside of save events.

## Platform

Windows primary (Tyler's laptop + desktop). Python 3.12 / 3.14 both in use.
Git configured with username `Tyler`, email `tyler@beetentmaps.local`.
GitHub account: `tylertorrie`.
