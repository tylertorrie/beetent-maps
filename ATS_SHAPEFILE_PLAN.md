# ATS Shapefile Integration Plan

Notes on replacing the math-based `geocode_lld()` in `beetent_app.py` with an
official Alberta Township System (ATS) polygon lookup.

## Problem

The current `geocode_lld()` uses simplified DLS math that doesn't account for:

- Correction lines (every 4 townships, range boundaries jog east to compensate
  for meridian convergence)
- Road allowances (every 2 miles)
- NAD27 → WGS84 datum shift
- Actual surveyed monument positions vs. idealized grid

Result: LLD polygons can be off by several hundred metres in some areas (~400m
offset observed at NW-14-11-15-W4).

## Sources (all free for Alberta)

- **Alberta Township System (ATS) v4.1** — official surveyed boundaries
  - [GeoDiscover Alberta](https://geodiscover.alberta.ca/)
  - Alberta Open Data portal
  - Section-level is free; quarter-section is sometimes paid (via AltaLIS)
- **Saskatchewan**: Information Services Corporation (ISC) / SaskGeomatics
- **Manitoba**: Manitoba Land Initiative

## Approach

1. Download the ATS Section shapefile once (~10–30 MB compressed)
2. Convert to a compact lookup format:
   - SQLite with R-tree spatial index, OR
   - Packed JSON keyed by `mer-twp-rng-sec`
   - Target: ~5 MB after processing
3. Replace `geocode_lld()` with a lookup:
   - Parse the LLD as before to get `(twp, rng, mer, sec, quarter)`
   - Fetch the section polygon from the file
   - Derive quarter polygon by subdividing the section (or download QSection
     layer separately for exact quarter boundaries)

## File-size considerations

| Resolution      | Raw     | Compressed | Bundle in repo? |
| --------------- | ------- | ---------- | --------------- |
| Section-level   | ~12 MB  | ~5 MB      | Yes             |
| Quarter-level   | ~50 MB  | ~15 MB     | Borderline      |
| LSD-level       | ~200 MB | ~60 MB     | No (lazy load)  |

## Decisions to make before starting

1. **Coverage** — Alberta only, or also Saskatchewan/Manitoba?
2. **Resolution** — section-level (synthesize quarters) or quarter-level (exact)?
3. **Bundling** — ship in git repo (auto-syncs), or download to local cache on
   first launch?

## Recommended starting point

**Alberta only, section-level, bundled in repo.**

- Accuracy is good enough for the field-finding use case
- Zero-config setup (works after `git pull`)
- ~5 MB is well within reasonable repo size
- Can expand to Saskatchewan / quarter-level later if needed

## Implementation steps (once data is in hand)

1. Tyler downloads ATS Section shapefile from GeoDiscover Alberta
2. Write a one-time conversion script that:
   - Reads the shapefile via the vendored `shapefile.py`
   - Extracts `(mer, twp, rng, sec)` and polygon corners
   - Writes a compact lookup file (SQLite or JSON) into `fields/` or a new
     `geo/` directory
3. Modify `geocode_lld()` in `beetent_app.py`:
   - Keep the regex parser (still need to interpret the LLD string)
   - Replace the math with a lookup against the new file
   - For quarter sections, subdivide the section polygon by the
     `_QUARTER` fractions (or look up directly if QSection data is available)
4. Test against a few known LLDs to confirm sub-100m accuracy

## Open questions

- Does the AltaLIS QSection product include LSD-level subdivision?
- Is the Alberta Open Data ATS download in EPSG:4326 (WGS84 lat/lon) or
  EPSG:3400 (10TM)? If projected, we'll need to reproject during the
  conversion step.
- Should the old math-based geocoder remain as a fallback for areas outside
  the bundled shapefile (e.g., if a user enters a Saskatchewan LLD)?
