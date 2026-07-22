"""Optional Supabase mirror for field data (desktop → Postgres).

The desktop stays the authoritative authoring tool; this best-effort, background
dual-write keeps the Supabase `fields` table in sync so the web app sees live
data. It is a complete NO-OP unless BOTH:
  1. the `supabase` package is installed  (pip install supabase), and
  2. credentials are present — either supabase_config.json next to this file, or
     the SUPABASE_URL / SUPABASE_SERVICE_KEY environment variables.

Uses the service-role key (the office machine is trusted); that key is gitignored
and never shipped to a browser. Every call runs on a daemon thread and swallows
errors, so a missing config, offline network, or Supabase hiccup can never block
or crash the app.
"""
import os
import json
import datetime
import threading
from pathlib import Path

_CFG = Path(__file__).resolve().parent / "supabase_config.json"
_client = None
_tried = False
_lock = threading.Lock()


def _load_client():
    global _client, _tried
    with _lock:
        if _tried:
            return _client
        _tried = True
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if _CFG.exists():
            try:
                c = json.loads(_CFG.read_text(encoding="utf-8"))
                url = c.get("url") or url
                key = c.get("service_key") or key
            except Exception:
                pass
        if not url or not key:
            return None
        try:
            from supabase import create_client
            _client = create_client(url, key)
        except Exception:
            _client = None
        return _client


def enabled():
    """True if Supabase mirroring is configured and available."""
    return _load_client() is not None


def _bg(fn):
    threading.Thread(target=fn, daemon=True).start()


def _clean(f):
    """JSON-round-trip so the blob is guaranteed serializable (mirrors the
    autosave's json.dumps(..., default=str))."""
    return json.loads(json.dumps(f, default=str))


def _with_computed_shelters(blob):
    """Attach the computed shelter grid so the web app can draw it WITHOUT the
    placement engine — the desktop's Python (maketentgrid) stays the single source
    of truth for placement; the web only ever draws what it's handed.

    `computed_shelters` = [[lat, lon], ...] in NW-snake order (the base grid;
    manual drags/adds are a later refinement). Pure + best-effort — any failure
    just leaves the key off, and the field still mirrors."""
    try:
        import maketentgrid
        pos = maketentgrid.get_tent_positions(dict(blob), use_metric=True)
        blob["computed_shelters"] = [[round(float(la), 7), round(float(lo), 7)]
                                     for la, lo in pos]
    except Exception:
        pass
    return blob


def upsert_field(f):
    """Mirror one field to Supabase (background, best-effort)."""
    def job():
        sb = _load_client()
        if sb is None:
            return
        co = str(f.get("company") or "").strip()
        yr = str(f.get("year") or "").strip()
        nm = str(f.get("Name") or "").strip()
        if not (co and yr and nm):
            return
        try:
            sb.table("fields").upsert({
                "company": co, "year": yr, "name": nm,
                "data": _with_computed_shelters(_clean(f)),
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, on_conflict="company,year,name").execute()
        except Exception:
            pass
    _bg(job)


def delete_field(co, yr, nm):
    """Remove a field from Supabase (background, best-effort)."""
    def job():
        sb = _load_client()
        if sb is None:
            return
        try:
            (sb.table("fields").delete()
               .eq("company", str(co)).eq("year", str(yr)).eq("name", str(nm))
               .execute())
        except Exception:
            pass
    _bg(job)
