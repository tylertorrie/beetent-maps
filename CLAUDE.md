# Notes for Claude Code — Bee Tent Maps

## What this project is

A Windows desktop GUI app (`beetent_app.py`) for laying out leafcutter bee shelter
positions on satellite maps for canola pollination operations. The company is
**Corteva** (main user: Tyler). Fields are pivot-irrigated circles or polygon
boundaries. The app places shelter pins at calculated positions within female bays,
exports GPS coordinates, and generates PDF maps.

## Running the app

Launch **windowless** with `pythonw.exe` so no console window opens (the app is
designed for this — it wraps subprocess to hide git console flashes). There's a
"Bee Tent Maps" desktop shortcut pointing at pythonw for the user.

```powershell
cd C:\Users\tyler\beetent-maps   # or wherever cloned
pythonw beetent_app.py           # windowless (preferred)
# python beetent_app.py          # only for debugging — opens a console window
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
beetent_app.py        — Main GUI (customtkinter + tkintermapview). ~9300 lines.
maketentgrid.py       — Core calculation engine. get_tent_positions() is the key fn.
utmish.py             — ENU coordinate conversion (local East-North-Up)
shapefile.py          — Vendored pyshp for .shp boundary upload
simplekml/            — Vendored for KML export
fpdf/                 — Vendored PDF generation
fields/               — Saved field JSON files (git-tracked, auto-synced)
  bay_presets.json    — Saved bay calculator presets
  cost_prefs.json     — Cost Estimator: by_year{year:{inputs+contract $/acre}} + global home pin (not per-field)
maps_api_key.txt      — Google Maps API key (gitignored secret; repo is public)
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
  "two_pivots": False,                      # rare: one field served by TWO pivots
  "PP2_Latitude": "", "PP2_Longitude": "",  # second pivot point (when two_pivots)
  "pivot_tracks2": [radius_m, ...],         # second pivot's tracks (independent)
  "Radius2": "",                            # second pivot circle radius (no-boundary fields)
  "corner_arms": [[], []],                  # corner arm paths
  "shelter_overrides": {idx: [lat,lon]}     # manually dragged shelter positions (LIVE set for the current combo)
  "tray_overrides": {idx: count}            # manual per-shelter tray counts (LIVE set for the current combo)
  "spray_both_ways": False,                 # square grid sprayable at 0° AND 90° (rare opt-in)
  "adjust_by_combo": {combo_key: {shelter_overrides, tray_overrides}}  # moves/deletes kept PER settings combo
  "home_to_parking_km": 0, "home_to_parking_min": 0,  # cached Google road dist/time home→parking (Cost Estimator)
  "home_coords_used": [lat,lon]              # home pin used for the cache (detect a moved depot)
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
| Crew travel line | Purple `#A855F7`, 3px, toggle-able (🚜 Crews menu) |

## Crews menu + crew travel line

The 🚜 **Crews** toolbar menu (next to Shelters) toggles an estimated crew travel
line: a snake driven down the CENTRE of the male bays nearest the shelters, with a
total-km readout on the status line. The geometry comes from
`maketentgrid.crew_route(field_dict, use_metric, shelters=None) -> (route_latlon,
total_m)` — a pure function that mirrors the male-bay overlay (`resolve_row_mask`/
`mask_runs`, pass tiling, planting-angle rotation, bay shift), groups shelters by
nearest male-bay centre and snakes column to column. Each pass runs the FULL length to
the field boundary and consecutive passes are joined by following the boundary perimeter
(the headland) — never across the crop. If `parking_pin` is set, the route starts AND
ends there (parking → boundary → along the headland → field → back). Route length is
computed in the rotated metric frame (shift-invariant). `_redraw_crews` uses the cached
off-thread tents (`_ensure_tents`); the Cost Estimator calls `crew_route` per field for
driving time.

The line is **editable** like a boundary: 🚜 Crews → "Edit Crew Route" drops draggable
vertex markers (drag/add via map-click/delete via 🗑), saved per-field as
`crew_route_override` (`[[lat,lon],...]`). When present, `_redraw_crews` draws it as-is
and `_field_cost` uses its length; "Reset Crew Route" clears it. Excluded from the tent
cache key; cleaned up on field switch.

## Cost Estimator view

Nav-drawer view (💰) with three `CTkSegmentedButton` tabs:
- **General Information** — cost inputs (items + depreciation life, chemical, **fuel**,
  labour) in `self._cost_vars` and bid **$/acre per company** (`self._contract_vars`,
  Contracts card). **Everything here is stored PER PRICING YEAR**: a year dropdown
  (`_cost_year_combo`) switches the whole form. The in-session source of truth is
  `self._cost_year_cache` `{year: {cost-var keys + contract_per_acre}}`;
  `_cost_apply_year`/`_cost_capture_year` swap widget values in/out on year change
  (`_cost_year_changed`), `_resolve_year_data` carries forward from the most recent
  earlier year when a year has no data, and `_save_cost_prefs` flushes the cache to
  `cost_prefs.json` under `by_year`. `_load_cost_prefs` (once, at build) rebuilds the
  cache and **migrates** any old flat top-level format via `_legacy_flat`. Per-year
  reads for costing: `_cost_inputs_for_year(y)` / `_contract_rates_for_year(y)`. The
  Travel card holds the **Google Maps API key** (gitignored `maps_api_key.txt`, NOT
  cost_prefs — the repo is public) and the **global** home/depot pin (`home_lat`/
  `home_lon`, NOT per-year). "💾 Save settings" → `_save_cost_settings` (key + the year
  cache). `_build_contract_rows` is rebuilt per visit so new companies appear.
- **Cost Estimator** — company/year scope picker + per-field checkboxes; `_field_cost(f, c)`
  computes AMORTIZED items (unit cost ÷ life-years × qty; bees = 1-yr full cost) +
  chemical (per acre) + **fuel** + labour. **Labour per task** (setup/bees/removal) =
  *work* (shelters × per-shelter-min/60 person-hours × pay — **invariant to crew count**)
  + *travel* (crews × emp_per_crew people × home↔parking round-trip × pay). Crew count
  (`crews_X` × `emp_per_crew_X`) only shortens each task's wall-clock **duration**
  (`dur_X`, shown). **Fuel** = (crews × round-trip km + in-field `crew_route` km) ×
  `fuel_l_per_km` × `fuel_cost_per_l`, per task. The home↔parking road distance/time is
  fetched via Google (`_drive_distance_google`, Distance Matrix API) by the
  **"↻ Update travel times"** button (`_cost_update_travel`, off-thread) and cached on
  each field as `home_to_parking_km`/`home_to_parking_min`/`home_coords_used`;
  `_field_cost` only READS that cache. `_field_cost` returns `cost_per_acre` (= total ÷
  acres); `_cost_compute` also attaches `contract_rate`/`contract_value`/`net_profit`
  per field (revenue = the field-year's contract rate × acres; net profit = contract
  value − total cost; plus `profit_per_acre`). Both heroes (this tab + Profitability)
  show **Cost / ac** and **Profit / ac** next to the headline figure; the cost hero adds
  a **Contract value / Total cost / Net profit** trio when a rate is set. Per-field cards
  + CSV + PDF carry contract value, net profit, cost/ac and profit/ac. **PDF caveat:**
  fpdf core fonts are latin-1 — any dynamic text (line labels, calc strings, field names)
  must go through `_pdf_txt` or non-ASCII chars (em-dash, →) raise UnicodeEncodeError on
  export. The
  field picker is **collapsible** (`_cost_toggle_field_list`). The breakdown groups in a
  fixed `_COST_CAT_ORDER` (Items, Bees, Chemical, Fuel, Labour); the `lines` list keeps
  same-group rows contiguous so each group renders as ONE card (Items =
  shelters+trays+blocks+flags together). Exports CSV + landscape PDF to `~/Downloads`,
  archived to the output library (`_archive_cost_to_library`).
- **Profitability** — a **live ranking** (no compute button). `_cost_switch_tab` calls
  `_profit_open` on entry: it reuses cached `_profit_rows` and just re-derives revenue/
  profit from the CURRENT contract rates (`_profit_apply_rates`, cheap — so rate edits
  show on return without re-costing), or auto-runs `_profit_compute` once if empty.
  `_profit_compute` (off-thread, own company/year scope) costs each field with **its own
  year's** prices/rate (`_cost_inputs_for_year`/`_contract_rates_for_year`); the ↻ Refresh
  button + scope combos re-run it. `_profit_render` headlines **profit / acre** and ranks
  BOTH companies and fields by profit/acre, high→low (total profit shown secondary). A red
  **❗** (`_field_profit_warnings`) flags fields missing info that skews results (no acreage
  / shelters / contract rate / travel), reasons listed inline.

### Home pin (global depot)

One global pin (not per-field) for the company base. Set via Boundary ▸ **Set Home Pin
(depot)** (`_mode_set_home` → `click_mode="set_home"`), drawn as a blue **H** marker in
`_redraw_field_info` (reads `self._home_pin`, not `current_field`), draggable like the
E/P pins (`_on_field_info_drag` routes `home_pin` to `_set_home_pin`). Persisted
immediately to `cost_prefs.json`; gated on the `show_field_info` toggle.

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
- Handle draggable via drag system → `_on_track_drag((pivot_num, idx), lat, lon)`
- Toggle visibility: `show_tracks` BooleanVar, `_toggle_tracks()`
- Delete via popup dialog (`_mode_delete_track_ui`)

## Two-pivot fields (rare)

Some fields are served by **two pivots**. Toggle via 🎯 Pivot → "Toggle Two Pivots";
place the 2nd with "Set 2nd Pivot Point" (orange pin, draggable). Each pivot keeps its
OWN independent tracks (`pivot_tracks` / `pivot_tracks2`) and radius (`Radius` / `Radius2`).

- **One global shelter grid** still spans the whole field (anchored in pivot-1's ENU
  frame), so shelter rows **line up across both circles** — that's the whole point.
- Three things become **nearest-pivot aware** in `get_tent_positions` (helpers
  `_nearest_pivot` / `_outside_field_circles` / `_edge_inside`): the inner no-shelter
  zone, the track exclusion (a candidate is checked against the tracks of whichever
  pivot it's CLOSEST to), and — when no boundary is drawn — the outer edge (field =
  UNION of the two circles; grid generation range is grown to cover both).
- With a drawn boundary, that polygon defines the area for both pivots (the common case).
- App track add/drag/delete route to the nearest pivot; `_track_hit` returns
  `(pivot_num, idx)`. Single-pivot fields are byte-for-byte unchanged (`pivot2_enu=None`).
- Not yet two-pivot-aware: acres auto-calc for a no-boundary union (draw a boundary to
  get correct acres/shelter-count there); the span-length bulk editor edits pivot 1.

## Known issues / recent fixes

- **Manual moves are scoped per settings combo**: `shelter_overrides` / `tray_overrides`
  hold only the LIVE combo's moves; `adjust_by_combo` stores every combo's set.
  `_combo_key()` = shelter mode+count + `shelters_in_outside_pass` + `spray_both_ways`;
  `_sync_combo_adjustments()` swaps the live set in/out (called on each shelter redraw,
  in each setting handler, and on field load). If you add a NEW setting that changes the
  base grid (and thus which override indices are valid), add it to `_combo_key()` too, or
  moves made under it will mis-apply when it's toggled.

- **get_tent_positions is the perf hot spot**: it runs point-in-polygon over the
  (often hundreds-of-vertex) boundary inside a placement binary search, so it costs
  ~2–12 s on big fields. It must NOT be called on the main thread per redraw. The app
  memoises it per field state and computes it off-thread via `_ensure_tents()` /
  `_tent_cache` (`_after_tents_ready()` redraws when done); the cache key omits
  `shelter_overrides`/`tray_overrides` (applied to the result, not read by the engine).
  New code that needs planned shelter positions should call `_ensure_tents`, not
  `maketentgrid.get_tent_positions` directly. `_point_in_polygon_bb` adds a bbox
  fast-reject in the engine hot loop.

- **Cross-field pivot/LLD leak when switching fields**: the 2.5s `_autosave_tick`
  timer could fire while `_form_from_field` was still repopulating the form widgets
  one-by-one (each `v.set()` pumps trace callbacks). A mid-load snapshot captured a
  half-old/half-new field — e.g. the new field's `PP_Latitude` but the previous
  field's `PP_Longitude` + `lld` — and `save_field` wrote that mix to disk under the
  new field's name. Confirmed in git history (e.g. SE 14-9-15 took NW 1-10-15's
  longitude+lld; Big Field took NW 1-20-15's pivot). Fixed by a `_loading_field`
  guard set for the whole duration of `_form_from_field`; `_autosave_tick` skips when
  it (or `_activating_field`) is set. If you add new timer/`after`-driven writers of
  field data, gate them on these flags too.

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
