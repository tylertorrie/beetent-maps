# Bee Tent Maps — Web planner (shell)

The browser front-end for the eventual full web planner (#10). Reads field data
from **Supabase** — the same `fields` table the desktop already mirrors into via
`supabase_sync.py` — and draws each field on a satellite map. Same "field kit"
look as the desktop + tablet.

## Status (scaffold)

This is the **shell**: field list (search + company/year scope), a satellite map,
and per-field overlays drawn from the raw field dict — **boundary, pivot(s),
pivot tracks, manual/test pins, entrance/parking, wet zones** — plus a sign-in
dialog wired to Supabase Auth.

Not yet (later phases): the **computed shelter grid** (needs the placement engine
available in the browser), **editing**, and **role-gated writes** (crews + staff,
last-write-wins).

## Run it

Static files — serve the folder and open it:

```powershell
python -m http.server 8080 --directory web
# then open http://localhost:8080
```

With **no `config.js`** it runs in **demo mode** against the bundled
`sample_field.json`, so the shell works with no backend.

## Point it at Supabase

1. `cp config.example.js config.js`
2. Fill in your project **URL** and **anon/public** key (Supabase → Project
   Settings → API). The anon key is safe in the browser — row-level security on
   the `fields` table is what gates access. The **service_role** key never goes
   here (it stays on the office desktop in `supabase_config.json`).
3. Reload — the badge flips from **demo** to **live** and the list reads from
   Postgres.

`config.js` is gitignored.

## Files

| File | What |
|---|---|
| `index.html` | shell markup (top bar, sidebar, map, auth dialog) |
| `style.css` | the shared light "field kit" design system |
| `supabase.js` | data layer — `beeData.listFields/getField/signIn/…`; demo fallback |
| `app.js` | map + overlay rendering, list, search/scope, auth chrome |
| `config.example.js` | template for `config.js` |
| `sample_field.json` | bundled demo field |
