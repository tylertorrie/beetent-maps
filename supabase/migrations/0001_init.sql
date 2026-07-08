-- Bee Tent Maps → Supabase — Phase 0 foundations
-- Run once in the Supabase SQL editor (or `supabase db push`). Idempotent-ish:
-- safe to re-run (uses if-not-exists / drop-if-exists guards).

create extension if not exists pgcrypto;

-- ── Fields: the authoring store (was fields/<company>/<year>/<name>.json).
-- Identity is (company, year, name); everything else (boundary, angles,
-- shelters, test_shelters, tracks, cost cache, …) rides in `data` as JSONB so
-- we don't have to model ~60 keys up front. ──────────────────────────────────
create table if not exists public.fields (
  id          uuid primary key default gen_random_uuid(),
  company     text not null,
  year        text not null,
  name        text not null,
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now(),
  unique (company, year, name)
);
create index if not exists fields_company_year_idx on public.fields (company, year);

-- ── Live crew positions (was Firebase crews/<id>). Realtime. ─────────────────
create table if not exists public.crews (
  id          text primary key,
  name        text,
  lat         double precision,
  lon         double precision,
  course      double precision,
  fix         int,
  sats        int,
  hdop        double precision,
  field       text,
  field_file  text,
  placed      int,
  total       int,
  placed_ids  jsonb,
  ts          timestamptz not null default now()
);

-- ── Persistent shelter/tray scans (was scans/<field>/{shelters,trays}/<qr>). ─
create table if not exists public.scans (
  id          uuid primary key default gen_random_uuid(),
  field_key   text not null,
  kind        text not null check (kind in ('shelter','tray')),
  qr          text not null,
  data        jsonb not null default '{}'::jsonb,
  ts          timestamptz not null default now(),
  unique (field_key, kind, qr)
);
create index if not exists scans_field_idx on public.scans (field_key);

-- ── Crew corrections back to the office (were Firebase calibration/ & direction/). ─
create table if not exists public.calibration (
  field_key   text primary key,
  data        jsonb not null default '{}'::jsonb,   -- {id, de, dn, crew, ts}
  updated_at  timestamptz not null default now()
);
create table if not exists public.direction (
  field_key   text primary key,
  data        jsonb not null default '{}'::jsonb,   -- {id, plant_angle, spray_angle, crew, ts}
  updated_at  timestamptz not null default now()
);

-- ── Small config (were fields/cost_prefs.json, fields/bay_presets.json). ──────
create table if not exists public.cost_prefs (
  id          int primary key default 1,
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now(),
  constraint cost_prefs_singleton check (id = 1)
);
create table if not exists public.bay_presets (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  data        jsonb not null default '{}'::jsonb
);

-- ── Realtime: stream the live tables to the web Monitor. Guard duplicates so
-- re-running the migration doesn't error. ────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array['crews','scans','calibration','direction'] loop
    begin
      execute format('alter publication supabase_realtime add table public.%I;', t);
    exception when duplicate_object then null;
    end;
  end loop;
end $$;

-- ── RLS: single-org start. Authenticated users get full access; the desktop
-- app uses the service-role key (bypasses RLS). Tighten to per-company/user
-- policies once multi-tenant. Drop-then-create keeps this re-runnable. ────────
do $$
declare t text;
begin
  foreach t in array array['fields','crews','scans','calibration','direction','cost_prefs','bay_presets'] loop
    execute format('alter table public.%I enable row level security;', t);
    execute format('drop policy if exists "authenticated full access" on public.%I;', t);
    execute format(
      'create policy "authenticated full access" on public.%I for all to authenticated using (true) with check (true);',
      t);
  end loop;
end $$;
