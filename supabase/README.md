# Bee Tent Maps → Supabase (web migration)

Incremental move of the desktop app to the browser, backed by Supabase. The
**desktop app stays authoritative** while the web app grows view-by-view; nothing
in daily use breaks during the transition.

## Architecture (decided)
- **Data / auth / realtime / storage:** Supabase (Postgres + Auth + Realtime).
- **Engine:** the tested `maketentgrid.py` stays Python, exposed via a small
  FastAPI service (`api/`) — no risky re-port of `get_tent_positions`.
- **Frontend:** a SvelteKit app (to be scaffolded) — the browser versions of the
  Map / Monitor / Financial / Files views. The `tablet/` PWA (MapLibre) is the seed.

Supabase consolidates three things the app juggles today: git field-sync, the
Firebase relay (crews/scans/calibration/direction), and GitHub-Pages hosting.

## Phase 0 — foundations (this folder)
1. **Create a Supabase project** at supabase.com. Copy from Project Settings → API:
   `Project URL`, the `anon` key, and the `service_role` key (keep the service key
   secret — office/server only).
2. **Apply the schema:** paste `migrations/0001_init.sql` into the SQL editor and
   run it (or `supabase db push` with the CLI). Creates `fields`, `crews`, `scans`,
   `calibration`, `direction`, `cost_prefs`, `bay_presets`; enables Realtime on the
   live tables; sets a single-org RLS policy (tighten later).
3. **Import existing data:**
   ```
   pip install -r supabase/requirements.txt
   set SUPABASE_URL=https://<project>.supabase.co
   set SUPABASE_SERVICE_KEY=<service_role key>
   python supabase/import_fields.py
   ```
   Re-runnable (upserts on company/year/name).

## Engine API (`api/`)
```
pip install -r api/requirements.txt
uvicorn api.main:app --reload --port 8787
```
- `GET  /health`
- `POST /tents`       → `{field, use_metric}` ⇒ planned shelter positions
- `POST /crew-route`  → `{field, use_metric}` ⇒ crew travel line + metres

## Next (not built yet)
- **Phase 1:** desktop app writes fields to Supabase alongside git (a `supabase_sync`
  path in `beetent_app.py`), so the DB is populated live.
- **Phase 2:** SvelteKit app — auth + read-only Map view (fields from Supabase,
  MapLibre render, shelter positions via the engine API).
- **Phase 3+:** web editing → Realtime Monitor → Financial/Files → retire
  git-sync + Firebase + Pages.
