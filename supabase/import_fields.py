#!/usr/bin/env python3
"""One-time (re-runnable) import of the local field data into Supabase.

Reads the git-tracked `fields/` tree and upserts every field, plus bay presets
and cost prefs, into the Postgres tables from migrations/0001_init.sql. Uses the
SERVICE-ROLE key (bypasses RLS) — run it from the office machine, never ship the
key to a browser.

Setup:
    pip install -r supabase/requirements.txt
    set SUPABASE_URL=https://<project>.supabase.co
    set SUPABASE_SERVICE_KEY=<service_role key>
    python supabase/import_fields.py
"""
import os, sys, json, glob
from pathlib import Path

REPO   = Path(__file__).resolve().parent.parent
FIELDS = REPO / "fields"


def _client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables first.")
    try:
        from supabase import create_client
    except ImportError:
        sys.exit("pip install -r supabase/requirements.txt")
    return create_client(url, key)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! skip {path}: {e}")
        return None


def import_fields(sb):
    rows = []
    # fields/<Company>/<Year>/<Name>.json  (skip *_map.json and the top-level
    # bay_presets.json / cost_prefs.json handled separately).
    for p in glob.glob(str(FIELDS / "*" / "*" / "*.json")):
        rel = Path(p).relative_to(FIELDS).parts
        if len(rel) != 3 or rel[2].endswith("_map.json"):
            continue
        data = _load(p)
        if data is None:
            continue
        company, year, name = rel[0], rel[1], rel[2][:-5]
        rows.append({"company": company, "year": year, "name": name, "data": data})
    if rows:
        # Upsert on the natural key so re-runs update in place.
        sb.table("fields").upsert(rows, on_conflict="company,year,name").execute()
    print(f"fields:      {len(rows)} upserted")


def import_config(sb):
    presets = _load(FIELDS / "bay_presets.json")
    if isinstance(presets, list) and presets:
        sb.table("bay_presets").upsert(
            [{"name": p.get("name", "preset"), "data": p} for p in presets]).execute()
        print(f"bay_presets: {len(presets)} upserted")
    cost = _load(FIELDS / "cost_prefs.json")
    if isinstance(cost, dict):
        sb.table("cost_prefs").upsert({"id": 1, "data": cost}).execute()
        print("cost_prefs:  1 upserted")


if __name__ == "__main__":
    sb = _client()
    import_fields(sb)
    import_config(sb)
    print("done.")
