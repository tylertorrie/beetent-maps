# Bee Tent Maps — Handoff: Unfinished Work

Written to brief a fresh chat (and/or a new base build) on what was in flight but
**not finished**. For "what the app is" and how it's built, see `CLAUDE.md` — this
doc only covers open threads, decisions already made, and gotchas.

Last commit at handoff: `5c5c11c feat(web): show the computed shelter grid`.

---

## 0. TL;DR — what's still open

1. **Web + Supabase planner (#10)** — the big one. Shell + read-only field view +
   computed shelter grid are DONE. **Database schema, RLS/roles, auth, and
   in-browser editing are NOT started.**
2. **"Push all fields to Supabase" desktop button** — offered, not built. Existing
   Supabase rows won't have the computed grid until each field is re-saved.
3. **Deeper "single source of truth"** — only the drift *test* was done; the tablet
   still hand-ports calibration/rotation math.
4. A handful of **smaller nice-to-haves** never started (photo capture, crew
   breadcrumb trails, billing export, keyboard shortcuts beyond undo, dark mode).
5. **A field data caveat**: Wordmans Carrots has 17 shelter overrides made under
   the *old* grid — they may need a one-click "Reflow to Grid" to re-snap.

Everything else from the 10-item roadmap this session is **done and pushed**.

---

## 1. Crown jewels — carry these into ANY new build

- **`maketentgrid.py`** — the pure placement engine. `get_tent_positions()`,
  `bay_slot_lefts()`, `male_bay_shelter_laterals()`, `resolve_row_mask()`,
  `mask_runs()`, `crew_route()`, `field_warnings()`. No GUI deps. This is the
  source of truth for placement geometry; every field-geometry bug the app has
  ever hit lives here.
- **`tests/`** — 113 passing tests (`python -m pytest`, ~80s):
  - `test_geometry.py` — property/unit tests + a **golden regression**
    (`baseline_positions.json`: shelter count + position hash per field). Regenerate
    intentionally with `python tests/_gen_baseline.py` after a deliberate geometry
    change; an accidental shift fails loudly.
  - `test_js_python_parity.py` — **drift guard**: extracts the tablet's JS math
    from `tablet/app.js`, runs it in Node, asserts it equals the Python to
    sub-mm. Mutation-tested (works).
  - `test_tablet_crews.py` — the tablet "other crews" logic, run in Node against
    the shipped files.
- **The field data format** — `fields/<Company>/<Year>/<Name>.json`, mirrored to
  Supabase `fields` table (`company/year/name/data jsonb/updated_at`). The
  `data` blob is the whole field dict. Documented in `CLAUDE.md`.
- **Auto-push policy** — every change is committed + pushed to
  `origin/master` each turn (also a Stop hook). Field data + code both live in
  the repo and auto-sync across devices.

---

## 2. Roadmap status (this session)

| # | Item | Status |
|---|------|--------|
| 1 | Geometry regression test suite | ✅ done (`68270b3`) |
| 2 | Field-save validation warnings | ✅ done (`508432c`) |
| 3 | Reflow to Grid + reindexing safety | ✅ done (`a7613c5`) |
| 4 | Bay-width label reconcile (label-only) | ✅ done (`8e7d5e6`) |
| 5 | Field search / recents / persisted UI state | ✅ done (`940e6df`) |
| 6 | Whole-field undo/redo (Ctrl+Z/Y) | ✅ done (`3e12321`) |
| 7 | Season-over-season reporting (Seasons tab) | ✅ done (`22a7555`) |
| 8 | Single source of truth (chose: **drift test**) | ✅ test done (`9969162`); deeper version NOT done |
| 9 | Live multi-crew positions on tablet | ✅ done (`bc9fcff`) |
| 10 | **Web + Supabase full planner** | 🟡 **partial** — see below |

---

## 3. UNFINISHED IN DETAIL

### 3a. #10 — Web + Supabase full planner (the main open project)

**Goal (agreed):** a browser app that eventually EDITS fields like the desktop.
Users = **crews + staff, with roles**. Conflict rule = **last-write-wins**.
Recommended (not explicitly confirmed) rollout: **read-only first, then enable
editing** — same destination, smaller blast radius.

**DONE (`web/` folder, commits `1dff044`, `5c5c11c`):**
- Vanilla JS + MapLibre SPA (no build step; matches the desktop/tablet family).
- Reads the Supabase `fields` table via `web/supabase.js` (`beeData.*`).
- Field list w/ live search + company/year scope pickers.
- Per-field overlays from the raw dict: boundary, pivot(s), tracks, manual/test
  pins, entrance/parking, wet zones — at desktop overlay colours.
- **Computed shelter grid** shown: the desktop's `supabase_sync.py` now runs
  `get_tent_positions()` and attaches `computed_shelters=[[lat,lon],…]` to each
  mirrored field, so the web draws the grid WITHOUT porting the engine.
- Supabase Auth sign-in dialog (email/password).
- **Demo mode**: no `config.js` → runs against bundled `sample_field.json`
  (re-baked with its 61 shelters). `config.example.js` is the template;
  `config.js` is gitignored (URL + anon key; anon key is browser-safe).

**NOT DONE (the remaining phases of #10):**
1. **Database schema + RLS policies** — the `fields` table exists (written by the
   service-role key from the desktop). No **row-level-security** policies yet, no
   **roles** table, no anon/auth read grants. This is the foundation editing
   sits on. Needs SQL migrations the user runs in the Supabase dashboard.
   - Roles: crews vs staff, with different read/write scopes.
   - Reads via the **anon** key must be gated by RLS (currently the browser can't
     read until policies exist — demo mode hides this).
2. **In-browser editing** — the web app is READ-ONLY. Editing (drag pivot/boundary/
   pins, change bay params, save back) is unbuilt. Needs last-write-wins write
   path + `updated_at` guard.
3. **Desktop "authoritative" inversion** — today the desktop owns local JSON files
   and mirrors *to* Supabase (one-way). A real web planner makes Supabase the
   source of truth and the desktop reads *from* it. This inversion is the
   riskiest part and was deliberately deferred.
4. **"Push all fields to Supabase" one-off** — offered, not built. Right now a
   field only mirrors (with `computed_shelters`) when it's **saved on the
   desktop**. Existing Supabase rows are stale until re-saved. A desktop button
   that loops every field → `supabase_sync.upsert_field` would light them all up.
5. **Hosting** — undecided. Current tablet is GitHub Pages; the web app could go
   there or Supabase hosting. CDN vs vendored MapLibre/Supabase-js also undecided
   (web currently uses CDN; tablet vendors for offline).

**Files:** `web/{index.html,style.css,app.js,supabase.js,config.example.js,
README.md,sample_field.json}`, `supabase_sync.py`, `supabase_config.json`
(gitignored, service-role key — office desktop only).

### 3b. Single source of truth for bay geometry (deeper than #8)

Chosen + done: a **drift test** (`test_js_python_parity.py`). NOT done: the
bigger move where the desktop **precomputes everything the tablet draws**
(calibration values, rotation) into the exported field data so the tablet does
**zero geometry math**. The tablet still hand-ports `_UTM.enu` / `shiftLatLon` /
`calcCalibration` / `rotateField` / `translateField` in `tablet/app.js`. The web
app already follows the good pattern (draws pushed `computed_shelters`, no engine).

### 3c. Smaller open items (recommended earlier, never started)

- **Photo capture at a shelter** (tablet) — crew taps a shelter, snaps a photo,
  syncs to office w/ GPS + timestamp. Builds on existing scan/Firebase plumbing.
- **Crew breadcrumb trails** (tablet) — colour trail per crew of where each has
  driven, so overlap is obvious. (Basic "see each other" IS done; trails are the
  upgrade that wasn't chosen.)
- **Billing / invoice export** — per-company billing summary (acres × contract
  rate, itemized) as PDF/CSV from the Financial View. Data already exists.
- **Keyboard shortcuts** beyond undo/redo — toggle layers, switch tools, save.
- **Dark mode / high-contrast** for the desktop; **multi-select shelters**.

---

## 4. Decisions already made (don't re-litigate)

- **Male bays are blue `#2E9BF0`** (desktop-planner parity), not green.
- **Bay width = rows × row-spacing** (`n·rs`), NOT `(n+1)·rs`. The Bay-panel label
  was fixed to match the map/engine. Labels only — no geometry moved.
- **Gap-aware bays**: `bay_gap_in` inserts a real inter-bay gap (it used to
  wrongly shrink the male band to zero — that was the Wordmans/Carrots
  "hairlines on one side" bug). `bay_slot_lefts()` in `maketentgrid.py`.
- **Web planner**: crews + staff **with roles**; **last-write-wins**.
- **#8** resolved as a drift *test*, not a rewrite.
- **#9** resolved as basic "see each other", not breadcrumb trails.

---

## 5. Known gotchas / caveats

- **Wordmans Carrots (Hytech/2026) has 17 shelter overrides** made under the
  *old* (pre-gap-aware) grid. They pin shelters to old positions. Fix: open it →
  **Shelters ▸ Reflow to Grid** (re-snaps to the recomputed grid; keeps manual
  added pins). Carrots is its twin (same grower).
- **MapLibre canvas + the headless preview**: in THIS session's tooling, the
  MapLibre WebGL canvas would **not paint** for either the tablet or the web app
  (style/tiles never finished loading; DOM chrome rendered fine). All map-drawing
  features were verified via feature-geometry/DOM inspection, **not** a live
  satellite render. A memory note claims the tablet DOES render via
  `preview_start "tablet"` (sim_server) in other sessions — so **just try it in a
  real browser**; treat the render itself as "verified-by-logic, eyeball-pending".
- **Verified-by-logic-not-eyeball** features this session: tablet "other crews"
  pins, web overlays incl. the shelter grid. The math/feature-building is tested;
  the on-map pixels are not.
- **Autosave writes to disk every 2.5s on change** and mirrors to Supabase — so a
  field only pushes its `computed_shelters` when saved. Editing a JSON file on
  disk while the app is open will be overwritten by autosave.
- **Undo/redo** is built on the autosave change-detector (a whole drag = one undo
  step), keyed per field, capped at 40. `Ctrl+Z` inside a text entry stays with
  the entry.

---

## 6. Suggested next steps (if continuing as-is)

1. **Supabase schema + RLS + roles** — the foundation. SQL migrations for a
   `roles`/membership model + policies on `fields` (crews read their scope; staff
   read/write; anon denied). Then the web app can read via the anon key for real.
2. **"Push all fields" desktop button** — so existing rows get `computed_shelters`
   without hand-saving each field.
3. **Web editing** (last-write-wins) once RLS is in.
4. Then the smaller items as desired.

## 7. If moving to a NEW base build

- Port `maketentgrid.py` **as-is** (or wrap it) and bring the **test suite +
  `baseline_positions.json`** with it — that's the safety net.
- Keep the **field JSON schema** (or migrate it deliberately, regenerating the
  golden baseline).
- The **Supabase mirror + web shell** are already the "modern stack" seed — likely
  reusable depending on the new base.
- Re-establish the **JS↔Python drift guard** if any math stays duplicated.
