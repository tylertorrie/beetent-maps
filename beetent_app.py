#!/usr/bin/python3
"""
Bee Tent Maps — modern GUI for leafcutter bee shelter layout generation.
"""
import tkinter as tk
import tkinter.filedialog, tkinter.messagebox, tkinter.simpledialog
import tkinter.ttk as ttk
import tkinter.font as tkfont
import customtkinter as ctk
import tkintermapview
import math, os, sys, threading, json, re, csv, datetime, zipfile, struct, glob, time
import subprocess, shutil
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maketentgrid
import utmish

# Hide the brief cmd-window flash that subprocess.run shows by default when
# launched from a pythonw process (git pull / push / fetch on save & startup).
# Wrap subprocess.run once at module load so every caller inside the file —
# and inside any function that does its own `import subprocess` — picks up
# the flag without each call site having to remember to pass it.
if sys.platform.startswith("win"):
    _orig_subprocess_run = subprocess.run
    def _quiet_run(*args, **kw):
        kw.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        return _orig_subprocess_run(*args, **kw)
    subprocess.run = _quiet_run
    # Same for Popen so any long-running git operations stay hidden too.
    _orig_subprocess_popen = subprocess.Popen
    class _QuietPopen(_orig_subprocess_popen):
        def __init__(self, *args, **kw):
            kw.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
            super().__init__(*args, **kw)
    subprocess.Popen = _QuietPopen

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ── Light UI palette (everything outside the map). Map overlay colours are
# set separately in the _redraw_* methods and are intentionally left alone. ──
UI_CARD   = "#FFFFFF"   # section cards / popups
UI_BORDER = "#E3E5E8"   # dividers, borders
UI_HOVER  = "#EDEFF2"   # hover / header highlight
UI_TEXT   = "#1F2A37"   # primary text
UI_MUTED  = "#6B7280"   # hints / secondary text
UI_ACCENT = "#0E9384"   # teal — data readouts
UI_WARN   = "#C2410C"   # warnings
UI_SELECT = "#CFE2FF"   # table/list selection

# ── Typography ──────────────────────────────────────────────────────────────
# Desktop equivalent of a Google-Fonts setup: bundled Inter TTFs (fonts/) are
# loaded into the process at startup so Tk can use them.
# Single-family rule: Inter everywhere.
#   Inter Medium → app/page titles + section headings (weight 500)
#   Inter        → all other text — body, labels, nav, menus, buttons,
#                  inputs, modals, pop-ups, badges, tooltips (weight 400)
# FONT_LABEL is kept as a separate alias for the few "emphasized sub-label"
# call sites; it now resolves to the same regular weight as FONT_BODY (per the
# flatter hierarchy: only true headings get weight 500).
FONT_HEADING = "Inter Medium"   # falls back to Tk default if unavailable
FONT_BODY    = "Inter"
FONT_LABEL   = "Inter"
_FONTS_DIR   = Path(__file__).parent / "fonts"

def _load_bundled_fonts():
    """Register the bundled TTFs with the OS for this process so Tk can use
    them without a system-wide install. Windows only; no-op/graceful elsewhere.
    Tk falls back to its default font for any family that fails to load."""
    try:
        if sys.platform.startswith("win") and _FONTS_DIR.exists():
            FR_PRIVATE = 0x10
            for ttf in _FONTS_DIR.glob("*.ttf"):
                try:
                    ctypes.windll.gdi32.AddFontResourceExW(str(ttf), FR_PRIVATE, 0)
                except Exception:
                    pass
    except Exception:
        pass

def _apply_typography(root):
    """Make Inter the default for all UI text (covers CTk widgets, ttk, menus,
    dialogs, message boxes). Headings opt into Inter Medium explicitly via
    the FONT_HEADING constant."""
    try:
        ctk.ThemeManager.theme.setdefault("CTkFont", {})
        ctk.ThemeManager.theme["CTkFont"]["family"] = FONT_BODY
    except Exception:
        pass
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkTooltipFont", "TkIconFont", "TkCaptionFont", "TkSmallCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=FONT_BODY)
        except Exception:
            pass

import ctypes  # noqa: E402  (Windows font loading)
_load_bundled_fonts()

SATELLITE_URL = "https://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}&s=Ga"
DATA_DIR      = Path(__file__).parent / "fields"
ASSETS_DIR    = Path(__file__).parent / "assets"   # bundled logo (synced via git)
DEFAULT_LAT, DEFAULT_LON, DEFAULT_ZOOM = 49.86, -111.96, 10

# Sentinels for the Company / Year dropdowns — used to export across a whole
# category. Not valid folder names; guarded out of save/load/new flows.
ALL_COMPANIES = "— All companies —"
ALL_YEARS     = "— All years —"

# Sentinel for the shelter-move undo stack: marks "no override existed before".
_UNDO_MISSING = object()

# ── Prairie LLD geocoder ───────────────────────────────────────────────────────
_MERIDIANS = {1:-97.4551, 2:-102.0, 3:-106.0, 4:-110.0, 5:-114.0, 6:-118.0}
_QUARTER   = {"NE":(0.75,0.25),"NW":(0.75,0.75),"SE":(0.25,0.25),"SW":(0.25,0.75)}
_HALF      = {"N":(0.5,1.0,0.0,1.0),"S":(0.0,0.5,0.0,1.0),
              "E":(0.0,1.0,0.0,0.5),"W":(0.0,1.0,0.5,1.0)}

_ATS_SECTIONS = None  # populated on first use: dict (mer,twp,rng,sec) -> (lat_min,lat_max,lon_min,lon_max)

def _load_ats_sections():
    """Load Alberta Township System V4.1 section bboxes from packed binary file."""
    global _ATS_SECTIONS
    path = Path(__file__).parent / "fields" / "ats_sections.bin"
    if not path.exists():
        _ATS_SECTIONS = {}
        return _ATS_SECTIONS
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != b"ATS1":
                _ATS_SECTIONS = {}
                return _ATS_SECTIONS
            (count,) = struct.unpack("<I", f.read(4))
            data = f.read()
        rec = struct.Struct("<BBBBffff")
        size = rec.size
        lookup = {}
        for i in range(count):
            m, t, r, s, lat_min, lat_max, lon_min, lon_max = rec.unpack_from(data, i * size)
            lookup[(m, t, r, s)] = (lat_min, lat_max, lon_min, lon_max)
        _ATS_SECTIONS = lookup
    except Exception:
        _ATS_SECTIONS = {}
    return _ATS_SECTIONS

def _ats_section_bbox(mer, twp, rng, sec):
    """Return (lat_min, lat_max, lon_min, lon_max) for the section, or None."""
    if _ATS_SECTIONS is None:
        _load_ats_sections()
    return _ATS_SECTIONS.get((mer, twp, rng, sec))

def _sec_pos(sec):
    idx=sec-1; row=idx//6
    return row, (idx%6) if row%2==0 else (5-idx%6)

def reverse_geocode_lld(lat, lon, granularity='quarter'):
    """Return the LLD string covering (lat, lon) at the requested granularity,
    or None if the point is outside the prairie LLD grid.

    granularity ∈ {'section', 'half', 'quarter'} (defaults to 'quarter'):
        section  → "32-14-22-W4"
        half     → "N-32-14-22-W4"     (N/S/E/W)
        quarter  → "NE-32-14-22-W4"

    Prefers the Alberta Township System V4.1 bbox lookup (when available);
    falls back to the math-grid for Saskatchewan, Manitoba, or AB sections
    that aren't in the shape file."""
    if _ATS_SECTIONS is None:
        _load_ats_sections()

    mer = twp = rng = sec = None
    sec_lat_min = sec_lat_max = sec_lon_min = sec_lon_max = None

    # ── 1) Exact ATS lookup. Linear scan over ~50k bboxes is fast enough for
    # the one-shot use this is called from (pivot placement / drag end).
    for key, bbox in _ATS_SECTIONS.items():
        lat_min, lat_max, lon_min, lon_max = bbox
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            mer, twp, rng, sec = key
            sec_lat_min, sec_lat_max = lat_min, lat_max
            sec_lon_min, sec_lon_max = lon_min, lon_max
            break

    # ── 2) Math fallback (SK/MB; or AB outside the bundled shape file).
    if mer is None:
        # Meridians get more negative as mer increases (1=-97.45 … 6=-118).
        # We want the meridian directly east of the point — the LAST mer in
        # iteration order whose lon is still > our lon (i.e., still east of us).
        chosen_mer = None
        for m in (1, 2, 3, 4, 5, 6):
            if _MERIDIANS[m] > lon:
                chosen_mer = m
            else:
                break
        if chosen_mer is None:
            return None
        mer = chosen_mer
        mlon = _MERIDIANS[mer]
        h = 9.6561
        # Township row (1..127): inverse of sw_lat = 49 + (twp-1)*h/111.12
        twp_calc = int((lat - 49.0) * 111.12 / h) + 1
        if not (1 <= twp_calc <= 127):
            return None
        twp = twp_calc
        sw_lat = 49.0 + (twp - 1) * h / 111.12
        clat = sw_lat + 0.5 * h / 111.12
        lkm = 1.0 / (111.12 * math.cos(math.radians(clat)))
        # Range: ranges go WEST of the meridian; sec_e = mlon - (rng-1)*h*lkm
        rng_calc = int((mlon - lon) / (h * lkm)) + 1
        if rng_calc < 1:
            return None
        rng = rng_calc
        # Section position within the township (6×6 snake-numbered grid).
        sr = int((lat - sw_lat) * 111.12 / (h / 6.0))
        sr = max(0, min(5, sr))
        twp_e = mlon - (rng - 1) * h * lkm
        sc = int((twp_e - lon) / (h / 6.0 * lkm))
        sc = max(0, min(5, sc))
        # Snake: even rows (0,2,4) west→east; odd rows reverse direction.
        # _sec_pos uses (col 0 = east end). Sections 1..6 go col 0..5 on row 0.
        if sr % 2 == 0:
            sec = sr * 6 + sc + 1
        else:
            sec = sr * 6 + (5 - sc) + 1
        # Synthesise the section's bbox so the quarter/half pick below works.
        sec_lat_min = sw_lat + sr * h / 6 / 111.12
        sec_lat_max = sec_lat_min + h / 6 / 111.12
        sec_lon_max = mlon - (rng - 1) * h * lkm - sc * h / 6 * lkm        # east
        sec_lon_min = sec_lon_max - h / 6 * lkm                            # west

    base = "%d-%d-%d-W%d" % (sec, twp, rng, mer)
    if granularity == 'section':
        return base

    mid_lat = (sec_lat_min + sec_lat_max) / 2
    mid_lon = (sec_lon_min + sec_lon_max) / 2
    ns = 'N' if lat >= mid_lat else 'S'
    ew = 'E' if lon >= mid_lon else 'W'
    if granularity == 'half':
        return "%s-%s" % (ns, base)
    return "%s%s-%s" % (ns, ew, base)

def geocode_lld(query):
    q = re.sub(r"[-\s,]+","-",query.strip().upper())
    twp=rng=mer=sec=quarter=half=None
    m=re.match(r"^(NE|NW|SE|SW)-(\d+)-(\d+)-(\d+)-W(\d)M?$",q)
    if m: quarter,sec,twp,rng,mer=m.group(1),int(m.group(2)),int(m.group(3)),int(m.group(4)),int(m.group(5))
    if twp is None:
        m=re.match(r"^([NSEW])-(\d+)-(\d+)-(\d+)-W(\d)M?$",q)
        if m and int(m.group(2))<=36:
            half,sec,twp,rng,mer=m.group(1),int(m.group(2)),int(m.group(3)),int(m.group(4)),int(m.group(5))
    if twp is None:
        m=re.match(r"^(\d{1,2})-(\d{1,2})-(\d+)-(\d+)-W(\d)M?$",q)
        if m and int(m.group(1))<=16 and int(m.group(2))<=36:
            sec,twp,rng,mer=int(m.group(2)),int(m.group(3)),int(m.group(4)),int(m.group(5))
    if twp is None:
        m=re.match(r"^(\d+)-(\d+)-(\d+)-W(\d)M?$",q)
        if m and int(m.group(1))<=36: sec,twp,rng,mer=int(m.group(1)),int(m.group(2)),int(m.group(3)),int(m.group(4))
    if twp is None:
        m=re.match(r"^(\d+)-(\d+)-W(\d)M?$",q)
        if m: twp,rng,mer=int(m.group(1)),int(m.group(2)),int(m.group(3))
    if twp is None or mer not in _MERIDIANS: return None

    # Prefer the official ATS V4.1 bbox when we have it (Alberta sections).
    ats_bbox = _ats_section_bbox(mer, twp, rng, sec) if sec is not None else None
    if ats_bbox is not None:
        sec_s, sec_n, lon_min, lon_max = ats_bbox
        sec_e, sec_w = lon_max, lon_min          # east = less negative; west = more negative
        sec_h = sec_n - sec_s
        sec_span = sec_w - sec_e                  # negative (west - east)
        lat = (sec_s + sec_n) / 2
        lon = (sec_e + sec_w) / 2
        label = "Sec %d Twp %d Rng %d W%dM" % (sec, twp, rng, mer)
        bnd_s, bnd_n, bnd_e, bnd_w = sec_s, sec_n, sec_e, sec_w
        if half is not None:
            fn_s, fn_e, fe_s, fe_e = _HALF[half]
            q_s = sec_s + fn_s * sec_h; q_n = sec_s + fn_e * sec_h
            q_e = sec_e + fe_s * sec_span; q_w = sec_e + fe_e * sec_span
            lat = (q_s + q_n) / 2; lon = (q_e + q_w) / 2
            label = "%s½ Sec %d Twp %d Rng %d W%dM" % (half, sec, twp, rng, mer)
            bnd_s, bnd_n, bnd_e, bnd_w = q_s, q_n, q_e, q_w
        elif quarter is not None:
            fs, fe = _QUARTER[quarter]
            q_s = sec_s + (fs - 0.25) * sec_h; q_n = sec_s + (fs + 0.25) * sec_h
            q_e = sec_e + (fe - 0.25) * sec_span; q_w = sec_e + (fe + 0.25) * sec_span
            lat = (q_s + q_n) / 2; lon = (q_e + q_w) / 2
            label = "%s Sec %d Twp %d Rng %d W%dM" % (quarter, sec, twp, rng, mer)
            bnd_s, bnd_n, bnd_e, bnd_w = q_s, q_n, q_e, q_w
        corners = [(bnd_s, bnd_w), (bnd_n, bnd_w), (bnd_n, bnd_e), (bnd_s, bnd_e)]
        return lat, lon, label, corners

    # Fall back to math-based grid (covers townships without sec, Saskatchewan/Manitoba, etc.)
    mlon=_MERIDIANS[mer]; h=9.6561
    sw=49.0+(twp-1)*h/111.12; clat=sw+0.5*h/111.12
    lkm=1.0/(111.12*math.cos(math.radians(clat)))
    twp_s=sw; twp_n=sw+h/111.12
    twp_e=mlon-(rng-1)*h*lkm; twp_w=mlon-rng*h*lkm
    lat,lon=clat,mlon-(rng-0.5)*h*lkm
    label="Twp %d Rng %d W%dM"%(twp,rng,mer)
    bnd_s,bnd_n,bnd_e,bnd_w=twp_s,twp_n,twp_e,twp_w
    if sec is not None:
        sr,sc=_sec_pos(sec)
        sec_s=sw+sr*h/6/111.12; sec_n=sw+(sr+1)*h/6/111.12
        sec_e=mlon-(rng-1)*h*lkm-sc*h/6*lkm; sec_w=mlon-(rng-1)*h*lkm-(sc+1)*h/6*lkm
        sec_h=sec_n-sec_s; sec_span=sec_w-sec_e
        lat=(sec_s+sec_n)/2; lon=(sec_e+sec_w)/2
        label="Sec %d Twp %d Rng %d W%dM"%(sec,twp,rng,mer)
        bnd_s,bnd_n,bnd_e,bnd_w=sec_s,sec_n,sec_e,sec_w
        if half is not None:
            fn_s,fn_e,fe_s,fe_e=_HALF[half]
            q_s=sec_s+fn_s*sec_h; q_n=sec_s+fn_e*sec_h
            q_e=sec_e+fe_s*sec_span; q_w=sec_e+fe_e*sec_span
            lat=(q_s+q_n)/2; lon=(q_e+q_w)/2
            label="%s½ Sec %d Twp %d Rng %d W%dM"%(half,sec,twp,rng,mer)
            bnd_s,bnd_n,bnd_e,bnd_w=q_s,q_n,q_e,q_w
        elif quarter is not None:
            fs,fe=_QUARTER[quarter]
            q_s=sec_s+(fs-0.25)*sec_h; q_n=sec_s+(fs+0.25)*sec_h
            q_e=sec_e+(fe-0.25)*sec_span; q_w=sec_e+(fe+0.25)*sec_span
            lat=(q_s+q_n)/2; lon=(q_e+q_w)/2
            label="%s Sec %d Twp %d Rng %d W%dM"%(quarter,sec,twp,rng,mer)
            bnd_s,bnd_n,bnd_e,bnd_w=q_s,q_n,q_e,q_w
    corners=[(bnd_s,bnd_w),(bnd_n,bnd_w),(bnd_n,bnd_e),(bnd_s,bnd_e)]
    return lat,lon,label,corners

# ── Geometry helpers ──────────────────────────────────────────────────────────
def haversine_m(lat1,lon1,lat2,lon2):
    R=6378137.0; dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    a=math.sin(dlat/2)**2+math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def despike_ring(ring, max_short_seg_m=2.0, min_reversal_deg=120.0, dup_eps_m=0.3):
    """Clean tracing artifacts from a boundary ring (list of (lat, lon)).

    Removes:
      • near-duplicate consecutive points (segment shorter than dup_eps_m), and
      • short "doubling-back" spikes — a vertex with a sharp reversal
        (turn ≥ min_reversal_deg) AND a short adjacent segment
        (≤ max_short_seg_m), i.e. the path darts out and straight back.

    Conservative by design: a genuine corner has long segments on both sides,
    so it is never removed. A closed-ring duplicate end point is preserved.
    Returns a NEW list of [lat, lon]; falls back to the input if too small.
    """
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if len(pts) < 4:
        return [list(p) for p in pts]
    # Strip a trailing duplicate of the first point (closed-ring form), then
    # treat the remainder as a cyclic ring; re-close at the end if it was closed.
    closed = (abs(pts[0][0] - pts[-1][0]) < 1e-12 and
              abs(pts[0][1] - pts[-1][1]) < 1e-12)
    if closed:
        pts = pts[:-1]
    if len(pts) < 4:
        return [list(p) for p in ring]
    mlat = sum(p[0] for p in pts) / len(pts)
    pd_lat = 111111.0
    pd_lon = 111111.0 * math.cos(math.radians(mlat))
    def _seg(a, b):
        return math.hypot((b[1]-a[1])*pd_lon, (b[0]-a[0])*pd_lat)
    def _turn(a, p, b):
        v1 = ((p[1]-a[1])*pd_lon, (p[0]-a[0])*pd_lat)
        v2 = ((b[1]-p[1])*pd_lon, (b[0]-p[0])*pd_lat)
        m1 = math.hypot(*v1); m2 = math.hypot(*v2)
        if m1 == 0 or m2 == 0: return 180.0
        c = max(-1.0, min(1.0, (v1[0]*v2[0] + v1[1]*v2[1]) / (m1*m2)))
        return math.degrees(math.acos(c))
    changed = True
    while changed and len(pts) >= 4:
        changed = False
        n = len(pts)
        for i in range(n):
            a = pts[(i-1) % n]; p = pts[i]; b = pts[(i+1) % n]
            l1 = _seg(a, p); l2 = _seg(p, b)
            if min(l1, l2) < dup_eps_m:                      # near-duplicate
                del pts[i]; changed = True; break
            if _turn(a, p, b) >= min_reversal_deg and min(l1, l2) <= max_short_seg_m:
                del pts[i]; changed = True; break            # doubling-back spike
    if closed:
        pts = pts + [pts[0]]
    return [list(p) for p in pts]

def circle_pts(lat,lon,r_m,n=90):
    pts=[]
    for i in range(n):
        b=math.radians(i*360/n)
        pts.append((lat+r_m/111111*math.cos(b), lon+r_m/(111111*math.cos(math.radians(lat)))*math.sin(b)))
    return pts

def polygon_area_m2(latlon_polygon):
    """Shoelace area in square metres for a lat/lon polygon. Uses ENU centred
    on the polygon centroid so distortion stays minimal at any latitude."""
    n = len(latlon_polygon)
    if n < 3: return 0.0
    lat0 = sum(p[0] for p in latlon_polygon) / n
    lon0 = sum(p[1] for p in latlon_polygon) / n
    pts = [latlon_to_enu(p[0], p[1], lat0, lon0) for p in latlon_polygon]
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5

ACRES_PER_M2 = 1.0 / 4046.8564224

def point_in_latlon_polygon(lat, lon, polygon):
    """Ray-casting point-in-polygon directly on lat/lon. Polygon is
    [(lat,lon), ...] or [[lat,lon], ...]. Accurate enough for clipping
    overlays at a single field's spatial scale."""
    inside = False
    n = len(polygon)
    if n < 3: return False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-30) + xi):
            inside = not inside
        j = i
    return inside

def latlon_to_enu(lat,lon,pivot_lat,pivot_lon):
    pe,pn=utmish.from_lonlat(pivot_lon,pivot_lat,pivot_lon)
    e,n=utmish.from_lonlat(lon,lat,pivot_lon)
    return e-pe, n-pn

def enu_to_latlon(e,n,pivot_lat,pivot_lon):
    pe,pn=utmish.from_lonlat(pivot_lon,pivot_lat,pivot_lon)
    lon2,lat2=utmish.to_lonlat(pe+e,pn+n,pivot_lon)
    return lat2,lon2

def _seg_cross_t(ax,ay,bx,by,cx,cy,dx,dy):
    """Parametric (t,u) of segment AB × CD intersection, or None if parallel."""
    rx,ry=bx-ax,by-ay; sx,sy=dx-cx,dy-cy
    den=rx*sy-ry*sx
    if abs(den)<1e-12: return None
    qx,qy=cx-ax,cy-ay
    return (qx*sy-qy*sx)/den, (qx*ry-qy*rx)/den

def _remove_inset_spikes(poly, _d=0):
    """Remove self-intersecting spikes from an inset polygon by detecting
    the first crossing of non-adjacent edges and short-cutting through it,
    keeping the larger-area sub-polygon.  Recurses until clean or depth limit."""
    if _d>12 or len(poly)<4: return poly
    n=len(poly)
    for i in range(n):
        ax,ay=poly[i]; bx,by=poly[(i+1)%n]
        j_end=n if i>0 else n-1
        for j in range(i+2, j_end):
            cx,cy=poly[j]; dx,dy=poly[(j+1)%n]
            r=_seg_cross_t(ax,ay,bx,by,cx,cy,dx,dy)
            if r is None: continue
            t,u=r
            if not(1e-8<t<1-1e-8 and 1e-8<u<1-1e-8): continue
            px=ax+t*(bx-ax); py=ay+t*(by-ay); pt=(px,py)
            outer=poly[:i+1]+[pt]+poly[j+1:]
            spike=poly[i+1:j+1]+[pt]
            def _a2(p):
                m=len(p)
                return abs(sum(p[k][0]*p[(k+1)%m][1]-p[(k+1)%m][0]*p[k][1]
                               for k in range(m)))
            keep=outer if (len(outer)>=3 and _a2(outer)>=_a2(spike)) else spike
            if len(keep)<3: keep=outer if len(outer)>=3 else spike
            return _remove_inset_spikes(keep, _d+1)
    return poly

def perimeter_band_quads(poly_enu, d_in, d_out):
    """Per-edge inward-offset band between depths d_in and d_out metres.

    Each boundary edge contributes ONE quad, so the band always follows the
    whole boundary — robust where a global polygon inset would self-intersect
    and collapse (concave necks narrower than the band, finely-traced edges).
    Used to draw the outside-round zones (perimeter sprayer pass) so they
    render all the way around any field shape. Returns a list of 4-point ENU
    polygons.
    """
    n = len(poly_enu)
    if n < 3:
        return []
    area2 = sum(poly_enu[i][0]*poly_enu[(i+1) % n][1] -
                poly_enu[(i+1) % n][0]*poly_enu[i][1] for i in range(n))
    wind = 1 if area2 > 0 else -1   # inward normal sign from winding
    quads = []
    for i in range(n):
        ax, ay = poly_enu[i]; bx, by = poly_enu[(i+1) % n]
        dx, dy = bx-ax, by-ay; L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        nx, ny = wind*(-dy/L), wind*(dx/L)
        quads.append([(ax+d_in*nx, ay+d_in*ny), (bx+d_in*nx, by+d_in*ny),
                      (bx+d_out*nx, by+d_out*ny), (ax+d_out*nx, ay+d_out*ny)])
    return quads

def inset_polygon_enu(poly_enu, dist, remove_spikes=True):
    """Offset every edge of poly_enu inward by dist metres.

    remove_spikes=True cleans self-intersections so the result fills cleanly;
    pass False when the result is only used as a CLIP polygon (even-odd ray
    casting tolerates self-intersection). On a deep inset of a finely-traced /
    concave boundary the spike cleanup can collapse whole sections, so the raw
    offset gives far better clip coverage.

    Simple parallel offset: each edge shifts inward by dist, corners
    joined by miter or bevel:
      - Concave corners (t >= 1) and shallow convex (d <= 1.5*dist):
        miter join — natural intersection, follows the boundary shape.
      - Sharp convex corners (t < 1 and d > 1.5*dist): bevel join —
        straight cut from one offset endpoint to the next, no spike.

    Normal direction is determined from the polygon's signed area
    (winding direction) rather than a per-edge centroid check, which
    is unreliable when the centroid is close to an edge.
    """
    n=len(poly_enu)
    if n<3: return []
    # Signed area: positive = CCW, negative = CW
    area2=sum(poly_enu[i][0]*poly_enu[(i+1)%n][1]-
              poly_enu[(i+1)%n][0]*poly_enu[i][1] for i in range(n))
    # For CCW: inward normal = (-dy, dx)/L
    # For CW:  inward normal = ( dy,-dx)/L  (flip sign)
    wind=1 if area2>0 else -1
    edges=[]; src_vertex=[]
    for i in range(n):
        e1,n1=poly_enu[i]; e2,n2=poly_enu[(i+1)%n]
        dx,dy=e2-e1,n2-n1; L=math.sqrt(dx*dx+dy*dy)
        if L<1e-9: continue
        nx,ny=wind*(-dy/L),wind*(dx/L)
        edges.append(((e1+dist*nx,n1+dist*ny),(e2+dist*nx,n2+dist*ny)))
        src_vertex.append((e2,n2))
    if len(edges)<3: return []
    miter_threshold=1.5*abs(dist)
    result=[]
    for i in range(len(edges)):
        a=edges[i]; b=edges[(i+1)%len(edges)]
        ax,ay=a[1][0]-a[0][0],a[1][1]-a[0][1]
        bx,by=b[1][0]-b[0][0],b[1][1]-b[0][1]
        det=ax*(-by)-(-bx)*ay
        if abs(det)<1e-9:
            result.append(((a[1][0]+b[0][0])/2,(a[1][1]+b[0][1])/2))
            continue
        ddx=b[0][0]-a[0][0]; ddy=b[0][1]-a[0][1]
        t=(ddx*(-by)-ddy*(-bx))/det
        ix,iy=a[0][0]+t*ax,a[0][1]+t*ay
        ovx,ovy=src_vertex[i]
        d=math.sqrt((ix-ovx)**2+(iy-ovy)**2)
        if t>=1.0 or d<=miter_threshold:
            result.append((ix,iy))
        else:
            result.append(a[1])
            result.append(b[0])
    return _remove_inset_spikes(result) if remove_spikes else result

def clip_line_to_polygon_intervals(px, py, dx, dy, polygon):
    """All inside-the-polygon intervals (in line-parameter t) for the
    infinite line through (px,py) with direction (dx,dy).

    Returns a list of (t_enter, t_exit) tuples. For a convex polygon
    that's a single interval; for a polygon with a concave bay or
    multiple lobes it's several. Used by `_band_polygon_enu` to produce
    one band polygon per inside-segment instead of a single bounding
    rectangle that fills across the gaps.
    """
    ts = []
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]; x2, y2 = polygon[(i+1) % n]
        ex, ey = x2 - x1, y2 - y1
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-9: continue
        t = ((x1 - px) * ey - (y1 - py) * ex) / denom
        u = ((x1 - px) * dy - (y1 - py) * dx) / denom
        if -1e-9 <= u <= 1 + 1e-9:
            ts.append(t)
    if len(ts) < 2: return []
    ts.sort()
    # Group sorted t-values into in/out intervals. With ray-casting
    # convention each pair (ts[0], ts[1]), (ts[2], ts[3]), … is an
    # inside-segment; rounding can produce odd-count lists, in which
    # case the final stray is dropped.
    intervals = []
    for i in range(0, len(ts) - 1, 2):
        a, b = ts[i], ts[i+1]
        if b - a > 1e-6:
            intervals.append((a, b))
    return intervals

def clip_line_to_polygon(px,py,dx,dy,polygon):
    ts=[]
    n=len(polygon)
    for i in range(n):
        x1,y1=polygon[i]; x2,y2=polygon[(i+1)%n]
        ex,ey=x2-x1,y2-y1
        denom=dx*ey-dy*ex
        if abs(denom)<1e-9: continue
        t=((x1-px)*ey-(y1-py)*ex)/denom
        u=((x1-px)*dy-(y1-py)*dx)/denom
        if -1e-9<=u<=1+1e-9: ts.append(t)
    if len(ts)<2: return None
    ts.sort(); return ts[0],ts[-1]

# ── Storage ───────────────────────────────────────────────────────────────────
def blank_field(company="",year=""):
    return dict(Name="",company=company,year=year,
                PP_Latitude="",PP_Longitude="",lld="",
                Planting_angle="",Spray_angle="",Sprayer_width="133",
                shelter_mode="trays_2",num_structures="",shelters_per_acre="",
                acres_per_shelter="",
                spacing="",shelter_spacing="",directional_offset="",
                row_spacing_in="22",num_female_rows="8",num_male_rows="2",
                bay_gap_in="0",           # extra gap (inches) at each male/female bay edge; 0 = none
                total_rows="20",          # total rows on the planter (may > num_female + num_male if unit repeats)
                row_layout="centered",   # "outer" | "centered" | "custom"
                custom_row_mask="",       # only used when row_layout == "custom"
                use_bays=True,            # False = blanket-planted crop, no female-bay constraint
                shelters_in_outside_pass="No",track_exclusion_ft="10",
                pass_edge_buffer_ft="25",   # ft shelters may intrude into a pass from its edge (0 = none / full outside-ring exclusion)
                tire_width_ft="14",         # ft machine/tire drive width shown down each pass centre (red zone)
                shelter_buffer_m="1.524",
                planter_passes=None,           # [[(lat,lon), ...], ...]  imported from JD
                use_imported_passes=True,      # when False or no data, use synthetic grid
                sprayer_passes=None,           # [[(lat,lon), ...], ...]  uploaded GPS sprayer tracks
                gals_per_acre="3",acres="",gals_per_tray="2",tray_distribution="even",
                boundary_polygon=None,pivot_tracks=[],corner_arms=[],
                boundary_inner=[],            # list of inner-exclusion polygons (JD-style "interior boundaries")
                sprayer_routes_around_inner=True,   # sprayer pass lines break around inner boundaries when True
                bays_through_inner=False,     # when True, planter bays draw through inner boundaries instead of clipping
                shelter_at_pivot=False,
                manual_shelter_pins=[],       # remembered when shelter_mode="manual"; restored if user switches back
                shelter_overrides={})

def _field_dir(company,year):
    d=DATA_DIR/company/str(year); d.mkdir(parents=True,exist_ok=True); return d

FIELD_NAME_BAD_CHARS = '#/\\:*?"<>|'   # JD rejects # and /; the rest break Windows file paths and shapefile names
FIELD_NAME_BAD_CHARS_HUMAN = '# / \\ : * ? " < > |'

def invalid_field_name_chars(name):
    """Return a list of bad characters present in `name`, or [] if clean.
    Used both for fields and for company/year names since all of them
    become folder / file name components on disk."""
    if not name: return []
    return sorted(set(c for c in name if c in FIELD_NAME_BAD_CHARS))

def save_field(f):
    if not f.get("Name"): return
    p=_field_dir(f.get("company","Default"),f.get("year",str(datetime.date.today().year)))/(f["Name"]+".json")
    with open(p,"w") as fp: json.dump(f,fp,indent=2)

def load_field(company,year,name):
    p=DATA_DIR/company/str(year)/(name+".json")
    return json.load(open(p)) if p.exists() else None

def _pt_in_poly(lat,lon,poly):
    """Ray-cast point-in-polygon. poly is [[lat,lon],...] or [(lat,lon),...].
    Returns True if (lat,lon) is inside the polygon.
    Casts a northward ray: checks if edge lon-coords straddle test lon,
    then whether the edge crossing is north of (greater lat than) test lat."""
    inside=False; n=len(poly); j=n-1
    for i in range(n):
        xi,yi=poly[i][0],poly[i][1]; xj,yj=poly[j][0],poly[j][1]
        if (yi>lon)!=(yj>lon) and lat<(xj-xi)*(lon-yi)/(yj-yi)+xi:
            inside=not inside
        j=i
    return inside

def delete_field_file(company,year,name):
    p=DATA_DIR/company/str(year)/(name+".json")
    if p.exists(): p.unlink()

def list_companies():
    return sorted(d.name for d in DATA_DIR.iterdir() if d.is_dir()) if DATA_DIR.exists() else []

def list_years(co):
    d=DATA_DIR/co
    return sorted((x.name for x in d.iterdir() if x.is_dir()),reverse=True) if d.exists() else []

def list_fields(co,yr):
    d=DATA_DIR/co/str(yr)
    return sorted(p.stem for p in d.glob("*.json")
                  if not p.stem.endswith("_map")) if d.exists() else []


# ── Export dialogs ────────────────────────────────────────────────────────────

def _center_on_parent(dialog, parent):
    """Position *dialog* at the centre of *parent* after widgets are laid out."""
    dialog.update_idletasks()
    dw = dialog.winfo_width();  dh = dialog.winfo_height()
    px = parent.winfo_rootx(); py = parent.winfo_rooty()
    pw = parent.winfo_width(); ph = parent.winfo_height()
    x = px + (pw - dw) // 2
    y = py + (ph - dh) // 2
    dialog.geometry("+%d+%d" % (x, y))

class _ExportFieldPicker(ctk.CTkToplevel):
    """Modal dialog — step 1 of export: choose which fields to export."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Export — Select Fields")
        self.resizable(True, True)
        self.grab_set()
        self.result = None          # [(co, yr, name), ...] or None if cancelled
        self._checkboxes = {}       # (co, yr, name) -> BooleanVar

        # ── Company / Year filter row ──────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(top, text="Company:").pack(side="left")
        self._co_var = ctk.StringVar(value=ALL_COMPANIES)
        self._co_cb  = ctk.CTkComboBox(top, variable=self._co_var,
                                        values=[ALL_COMPANIES]+list_companies(),
                                        width=170,
                                        command=lambda _: self._on_filter_change())
        self._co_cb.pack(side="left", padx=(4,14))

        ctk.CTkLabel(top, text="Year:").pack(side="left")
        _cur_yr = str(datetime.date.today().year)
        _all_yrs = sorted(set(y for c in list_companies() for y in list_years(c)), reverse=True)
        if _cur_yr not in _all_yrs:
            _all_yrs = [_cur_yr] + _all_yrs
        self._yr_var = ctk.StringVar(value=_cur_yr)
        self._yr_cb  = ctk.CTkComboBox(top, variable=self._yr_var,
                                        values=[ALL_YEARS] + _all_yrs,
                                        width=110,
                                        command=lambda _: self._on_filter_change())
        self._yr_cb.pack(side="left", padx=(4,0))

        # ── Select All / Deselect All + count ─────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(6,0))
        ctk.CTkButton(btn_row, text="Select All",   width=100,
                      command=self._select_all).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="Deselect All", width=110,
                      command=self._deselect_all).pack(side="left")
        self._count_lbl = ctk.CTkLabel(btn_row, text="")
        self._count_lbl.pack(side="right")

        # ── Scrollable field checklist ─────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self, width=440, height=320)
        self._scroll.pack(fill="both", expand=True, padx=12, pady=8)

        # ── OK / Cancel ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=(0,12))
        ctk.CTkButton(bot, text="Cancel", width=80, fg_color="grey40",
                      command=self._cancel).pack(side="right", padx=(6,0))
        self._ok_btn = ctk.CTkButton(bot, text="OK", width=80, command=self._ok)
        self._ok_btn.pack(side="right")

        self._rebuild_list()
        _center_on_parent(self, parent)

    # ── helpers ───────────────────────────────────────────────────────────
    def _on_filter_change(self):
        co = self._co_var.get()
        if co == ALL_COMPANIES:
            avail = sorted(set(y for c in list_companies() for y in list_years(c)), reverse=True)
        else:
            avail = list_years(co)
        yrs = [ALL_YEARS] + avail
        self._yr_cb.configure(values=yrs)
        if self._yr_var.get() not in yrs:
            self._yr_var.set(ALL_YEARS)
        self._rebuild_list()

    def _scope(self):
        co = self._co_var.get(); yr = self._yr_var.get()
        companies = list_companies() if co == ALL_COMPANIES else [co]
        out = []
        for c in companies:
            years = list_years(c) if yr == ALL_YEARS else [yr]
            for y in years:
                for name in list_fields(c, y):
                    out.append((c, y, name))
        return out

    def _rebuild_list(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._checkboxes.clear()
        scope = self._scope()
        multi = (self._co_var.get() == ALL_COMPANIES or
                 self._yr_var.get() == ALL_YEARS)
        for co, yr, name in scope:
            var = ctk.BooleanVar(value=True)
            label = "%s  (%s / %s)" % (name, co, yr) if multi else name
            ctk.CTkCheckBox(self._scroll, text=label, variable=var,
                            command=self._update_count).pack(anchor="w", pady=2)
            self._checkboxes[(co, yr, name)] = var
        self._update_count()

    def _select_all(self):
        for v in self._checkboxes.values(): v.set(True)
        self._update_count()

    def _deselect_all(self):
        for v in self._checkboxes.values(): v.set(False)
        self._update_count()

    def _update_count(self):
        total   = len(self._checkboxes)
        checked = sum(1 for v in self._checkboxes.values() if v.get())
        self._count_lbl.configure(text="%d of %d selected" % (checked, total))
        self._ok_btn.configure(state="normal" if checked > 0 else "disabled")

    def _ok(self):
        self.result = [(co, yr, name)
                       for (co, yr, name), v in self._checkboxes.items() if v.get()]
        self.destroy()

    def _cancel(self):
        self.destroy()          # self.result stays None


class _ExportTypePicker(ctk.CTkToplevel):
    """Modal dialog — step 2 of export: choose which output types to write."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Export — Select Output Types")
        self.resizable(False, False)
        self.grab_set()
        self.result = None      # dict or None if cancelled

        self._agps_var    = ctk.BooleanVar(value=True)
        self._jd_var      = ctk.BooleanVar(value=True)
        self._kml_var     = ctk.BooleanVar(value=True)
        self._geojson_var = ctk.BooleanVar(value=True)
        self._bnd_var     = ctk.BooleanVar(value=True)
        self._main_vars = [self._agps_var, self._jd_var,
                           self._kml_var, self._geojson_var, self._bnd_var]

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=16, pady=(16,4))

        ctk.CTkCheckBox(frame, text="AgGPS (Trimble)",
                        variable=self._agps_var,
                        command=self._update_ok).pack(anchor="w", pady=4)

        ctk.CTkCheckBox(frame, text="John Deere Shelter Buffer Zones",
                        variable=self._jd_var,
                        command=self._update_ok).pack(anchor="w", pady=4)

        ctk.CTkCheckBox(frame, text="Shelter Pins KML",
                        variable=self._kml_var,
                        command=self._update_ok).pack(anchor="w", pady=4)

        ctk.CTkCheckBox(frame, text="GeoJSON Files",
                        variable=self._geojson_var,
                        command=self._update_ok).pack(anchor="w", pady=4)

        ctk.CTkCheckBox(frame, text="Boundary Files",
                        variable=self._bnd_var,
                        command=self._update_ok).pack(anchor="w", pady=4)

        # ── Select All / Deselect All ──────────────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4,0))
        ctk.CTkButton(btn_row, text="Select All",   width=100,
                      command=self._select_all).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="Deselect All", width=110,
                      command=self._deselect_all).pack(side="left")

        # ── OK / Cancel ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=16, pady=(12,16))
        ctk.CTkButton(bot, text="Cancel", width=80, fg_color="grey40",
                      command=self._cancel).pack(side="right", padx=(6,0))
        self._ok_btn = ctk.CTkButton(bot, text="OK", width=80, command=self._ok)
        self._ok_btn.pack(side="right")

        self._update_ok()
        _center_on_parent(self, parent)

    # ── helpers ───────────────────────────────────────────────────────────
    def _select_all(self):
        for v in self._main_vars: v.set(True)
        self._update_ok()

    def _deselect_all(self):
        for v in self._main_vars: v.set(False)
        self._update_ok()

    def _update_ok(self):
        self._ok_btn.configure(
            state="normal" if any(v.get() for v in self._main_vars) else "disabled")

    def _ok(self):
        self.result = {
            "agps":     self._agps_var.get(),
            "jd":       self._jd_var.get(),
            "kml":      self._kml_var.get(),
            "geojson":  self._geojson_var.get(),
            "boundary": self._bnd_var.get(),
        }
        self.destroy()

    def _cancel(self):
        self.destroy()          # self.result stays None


# ── Application ───────────────────────────────────────────────────────────────
class BeetentApp(ctk.CTk):
    def __init__(self):
        # Tell Windows we're a standalone app (not a generic pythonw.exe
        # window) BEFORE any window is created. Without an AppUserModelID
        # Windows groups us under pythonw.exe in the taskbar and uses its
        # icon there even though iconbitmap sets the title-bar icon.
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "beetent.maps.app")
        except Exception:
            pass
        super().__init__()
        _apply_typography(self)   # Inter as the default UI font (headings use Inter Medium)
        self.title("Bee Tent Maps")
        self.geometry("1340x840")
        self.minsize(1160,650)
        self._set_window_icon()

        self.current_field = blank_field()
        self.click_mode    = None

        # Map overlays
        self.pivot_marker     = None
        self.field_circle     = None
        self.boundary_poly    = None
        self.boundary_pts     = []
        self._overview_polys  = {}   # (co,yr,name) → dim polygon overlay
        self._overview_field_bps = {}  # (co,yr,name) → [(lat,lon),...] for hit test
        self._overview_gen   = 0    # incremented to cancel stale background loads
        self.boundary_markers = []
        self.track_circles    = []
        self.track_handles    = []
        self.pass_paths       = []
        self.outer_sprayer_poly = None
        self.bay_polygons     = []
        self.lld_boundary_poly  = None
        self.lld_corners        = None   # cached corners of last LLD search (lat,lon list)
        self.lld_label          = ""     # label of the last LLD result, for status
        self.corner_arm_overlays    = []   # list of map path/polygon objects
        self.corner_arm_pts         = []   # points being drawn for in-progress path
        self.corner_arm_circle_center = None  # (lat,lon) for in-progress circle
        self.corner_arm_temp_markers = []
        self._editing_corner_arm_idx = None  # index into corner_arms being vertex-edited
        self.show_passes      = tk.BooleanVar(value=False)
        self.show_bays        = tk.BooleanVar(value=False)
        self.show_pivot       = tk.BooleanVar(value=True)   # pivot marker + tracks together
        self.show_tracks      = tk.BooleanVar(value=True)
        self.show_lld_box     = tk.BooleanVar(value=True)   # yellow LLD search highlight
        # Imported planter paths (JD Operations Center Seeding shapefiles).
        # Drawn as faint blue polylines so the user can see where the planter
        # actually went vs. the synthetic grid the bay calculator computes.
        self.show_planter_passes = tk.BooleanVar(value=False)
        self.planter_path_overlays = []
        # Uploaded GPS sprayer tracks — distinct from the synthetic angle-grid lines.
        self.show_sprayer_passes = tk.BooleanVar(value=False)
        self.sprayer_path_overlays = []
        # Sprayer-pass kill zones (the middle of each pass + the middle of the
        # outside pass, where shelters cannot be placed). Red translucent
        # bands so the user can visually verify the buffer.
        self.show_pass_buffer_overlay = tk.BooleanVar(value=False)
        self.pass_buffer_overlays = []
        # Inner boundary outline overlays (drawn by _redraw_boundary, but the
        # list lives here so _clear_all_overlays can wipe them too).
        self.boundary_inner_polys = []
        # Corner tracks (a.k.a. corner arms) — polygon paths and circles drawn
        # at absolute lat/lon (don't follow the pivot when it's moved). Used
        # for swing-arm pivot tracks, shelter belts, etc. that should exclude
        # shelters within the same width as a pivot track (track_exclusion_ft).
        self.show_corner_arms = tk.BooleanVar(value=False)
        self.shelter_markers    = []
        self.shelter_circle_polys = []
        self.shelter_positions  = []
        self.show_shelters      = tk.BooleanVar(value=False)
        # Boundary visibility (was always drawn; now togglable via toolbar checkbox)
        self.show_boundary      = tk.BooleanVar(value=True)
        # Master checkbox BooleanVars for each toolbar menu button
        self.pivot_visible_var    = tk.BooleanVar(value=False)
        self.boundary_visible_var = tk.BooleanVar(value=True)
        self.sprayer_visible_var  = tk.BooleanVar(value=False)
        self.planter_visible_var  = tk.BooleanVar(value=False)
        self.shelters_visible_var = tk.BooleanVar(value=False)
        self.pin_label_mode     = "off"   # "off" | "trays" | "shelters" — what each pin shows
        self._shelter_undo      = []   # stack of (override_key, prev_value) for Reset Move
        self.shelter_tray_counts= []  # parallel to shelter_positions; per-shelter int
        self.moving_shelter_idx = None
        self._shelter_refresh_id= None
        self._all_popups        = []
        self._menu_checkboxes   = []   # list of (CTkCheckBox, label_widget) per menu btn
        self.shelter_circle_var = tk.BooleanVar(value=False)
        self.field_labels       = {}

        # Drag system
        self._drag_registry = {}
        self._drag_item = None
        self._drag_track_idx = None   # index of pivot track being resized by band-drag
        self._pending_corner_idx = None  # corner arm clicked on press (for release popup)
        self._pending_boundary_click = False  # outer boundary edge clicked (for release popup)
        self._drag_last_latlon = None
        self._drag_start_xy = None
        self._drag_moved = False
        self._just_dragged = False
        self._pan_start_xy = None
        self._selected_bnd_vertex = None

        self._build_toolbar()
        self._build_body()
        self._init_map()
        self._refresh_unit_labels()
        self._refresh_company_list()
        self._refresh_preset_list()
        self._refresh_bee_preset_list()
        self._refresh_field_preset_list()
        self.bind("<Escape>", self._on_escape)
        self.bind("<Delete>", self._on_delete_key)
        for key in ("<Left>","<Right>","<Up>","<Down>",
                    "<Shift-Left>","<Shift-Right>","<Shift-Up>","<Shift-Down>"):
            self.bind(key, self._on_arrow_key)
        self.bind("<ButtonRelease-1>", self._on_global_popup_click, add="+")
        self.after(300, self._bind_drag_system)
        self.after(1000, self._git_pull)            # pull latest on startup
        self.after(300_000, self._check_for_app_update)  # then check every 5 min

    # ── Window icon / logo ──────────────────────────────────────────────────
    def _set_window_icon(self):
        ico = ASSETS_DIR / "logo.ico"
        if not ico.exists(): return
        try: self.iconbitmap(str(ico))
        except Exception: pass
        # CTk sets its own icon shortly after init; reassert ours afterwards.
        try: self.after(300, lambda: self._reassert_icon(str(ico)))
        except Exception: pass

    def _reassert_icon(self, ico):
        try: self.iconbitmap(ico)
        except Exception: pass

    # ── Toolbar ────────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar=ctk.CTkFrame(self,height=44,corner_radius=0)
        bar.pack(fill="x",side="top")
        try:
            from PIL import Image
            _logo = ASSETS_DIR / "logo.png"
            if _logo.exists():
                _im = Image.open(_logo)
                self._logo_img = ctk.CTkImage(light_image=_im, dark_image=_im, size=(26,26))
                ctk.CTkLabel(bar, image=self._logo_img, text="").pack(side="left", padx=(10,8), pady=6)
        except Exception:
            pass
        ctk.CTkLabel(bar,text="Legal Land Description:").pack(side="left",padx=(0,4),pady=8)
        # Structured LLD entry — one cell per part: Quarter-Section-Township-
        # Range-Meridian. Each cell auto-advances to the next once it's full,
        # which cuts down on mistyped legal land descriptions. The meridian
        # defaults to W4 (the common case for this operation).
        #   spec = (name, maxlen, kind, placeholder)
        self._lld_specs=[("qtr",2,"alpha","NW"),("sec",2,"digit","Sec"),
                         ("twp",2,"digit","Twp"),("rng",2,"digit","Rng"),
                         ("mer",2,"mer","W4")]
        self._lld_vars=[]; self._lld_entries=[]
        lld_box=ctk.CTkFrame(bar,fg_color="transparent")
        lld_box.pack(side="left",pady=8)
        for i,(name,maxlen,kind,ph) in enumerate(self._lld_specs):
            if i>0:
                ctk.CTkLabel(lld_box,text="-",width=8).pack(side="left")
            var=tk.StringVar(value="W4" if kind=="mer" else "")
            ent=ctk.CTkEntry(lld_box,width=48,textvariable=var,
                             placeholder_text=ph,justify="center")
            ent.pack(side="left")
            ent.bind("<KeyRelease>",lambda e,idx=i:self._on_lld_key(idx,e))
            self._lld_vars.append(var); self._lld_entries.append(ent)
        ctk.CTkButton(bar,text="Go",width=48,command=self._search_lld).pack(side="left",padx=(6,4),pady=8)
        # LLD highlight box toggle — the yellow rectangle around the searched
        # quarter section can get in the way once you're zoomed in working on
        # the field, so we let users hide/show it without re-searching.
        ctk.CTkSwitch(bar,text="LLD box",variable=self.show_lld_box,
                      command=self._toggle_lld_box,
                      font=ctk.CTkFont(family=FONT_LABEL,size=11)
                      ).pack(side="left",padx=(0,20),pady=8)
        # ── Right-side items packed FIRST so they always secure their space ──
        # Pack order within side="right": last packed = leftmost (closest to centre).
        # Units combo → Units label → Generate → PDF (PDF ends up leftmost = nearest centre)
        self.unit_var=tk.StringVar(value="Imperial")
        ctk.CTkComboBox(bar,variable=self.unit_var,values=["Imperial","Metric"],
                        width=100,command=self._on_unit_change).pack(side="right",padx=(0,12))
        ctk.CTkLabel(bar,text="Units:").pack(side="right",padx=(0,4))
        ctk.CTkButton(bar, text="⚙ Generate Output Files", fg_color="#1a5c8a",
                      font=ctk.CTkFont(family=FONT_LABEL, size=12),
                      command=self._generate).pack(side="right", padx=(0,4), pady=4)
        ctk.CTkButton(bar, text="📄 Field Summary PDF", fg_color="#4a3060",
                      font=ctk.CTkFont(family=FONT_LABEL, size=12),
                      command=self._export_field_pdf).pack(side="right", padx=(0,4), pady=4)
        # Update-ready button — hidden until a code update is pulled.
        self._update_btn=ctk.CTkButton(bar,text="🔄 Restart to update",
                                        fg_color="#1a6b3a",width=160,
                                        command=self._restart_app)
        # intentionally NOT packed here — shown on demand
        # ── Left-side status label (no fixed width — shrinks before buttons do) ──
        self.status_lbl=ctk.CTkLabel(bar,text="",text_color=UI_MUTED,anchor="w")
        self.status_lbl.pack(side="left",padx=16)

    # ── Popup menu helpers ─────────────────────────────────────────────────────
    def _make_menu_btn(self, bar, label, items, color="#2b2b2b",
                       toggle_var=None, toggle_fn=None):
        """Compound toolbar button: [☐]  label centred  [▾]
        toggle_var / toggle_fn drive the master on/off checkbox.
        The ▾ button opens the item dropdown as before."""
        popup = ctk.CTkFrame(self, fg_color=UI_CARD, border_width=1,
                             border_color=UI_BORDER, corner_radius=4)
        for item_label, item_cmd in items:
            ctk.CTkButton(popup, text=item_label, anchor="w", height=30,
                          fg_color="transparent", hover_color=UI_HOVER, text_color=UI_TEXT,
                          command=lambda p=popup, c=item_cmd: (p.place_forget(), c())
                          ).pack(fill="x", padx=2, pady=1)
        self._all_popups.append(popup)

        container = ctk.CTkFrame(bar, fg_color=color, corner_radius=6)

        # Right: dropdown trigger (packed first so it anchors right)
        ctk.CTkButton(container, text="▾", width=26,
                      fg_color="transparent", hover_color="#ffffff22",
                      text_color="white",
                      command=lambda p=popup, c=container: self._toggle_popup(p, c)
                      ).pack(side="right", padx=(0, 2), pady=2)

        # Centre: label fills the remaining space, text centred within it
        lbl = ctk.CTkLabel(container, text=label, text_color="white",
                           anchor="center", fg_color="transparent")
        lbl.pack(side="left", fill="x", expand=True, padx=8)

        # Left: master toggle checkbox — starts hidden; shown only when a field
        # is active.  Packed BEFORE the label (using before=) so layout is correct
        # when revealed.
        if toggle_var is not None and toggle_fn is not None:
            cb = ctk.CTkCheckBox(container, variable=toggle_var, text="",
                                 width=20, checkbox_width=16, checkbox_height=16,
                                 border_width=1,
                                 border_color="white", fg_color="white",
                                 checkmark_color="#333333", hover_color="#ffffff33",
                                 command=lambda: toggle_fn(toggle_var.get()))
            # Don't pack yet — hidden until a field is selected
            self._menu_checkboxes.append((cb, lbl))

        return container

    def _toggle_popup(self, popup, btn):
        # Set a one-shot flag so the global ButtonRelease handler (which fires
        # on the same event, one level up) knows not to immediately close the
        # popup we're about to open.
        self._popup_just_toggled = True
        if popup.winfo_ismapped():
            popup.place_forget(); return
        for p in self._all_popups:
            if p.winfo_exists(): p.place_forget()
        if btn is None: return
        btn.update_idletasks()
        rx = btn.winfo_rootx() - self.winfo_rootx()
        ry = btn.winfo_rooty() - self.winfo_rooty() + btn.winfo_height() + 2
        popup.place(x=rx, y=ry)
        popup.lift()

    def _close_all_popups(self, event=None):
        for p in self._all_popups:
            if p.winfo_exists(): p.place_forget()

    def _on_global_popup_click(self, event):
        """Close toolbar popups when clicking anywhere outside them."""
        # _toggle_popup sets this flag on the same ButtonRelease event so we
        # don't immediately close a popup that was just opened by the ▾ button.
        if getattr(self, '_popup_just_toggled', False):
            self._popup_just_toggled = False
            return
        if not any(p.winfo_exists() and p.winfo_ismapped()
                   for p in self._all_popups):
            return
        # Walk the widget ancestry from the click target upward.
        # If any ancestor is one of our popup frames, the click was inside —
        # leave the popup open. Otherwise close all.
        popup_paths = {str(p) for p in self._all_popups if p.winfo_exists()}
        w = event.widget
        while w is not None:
            if str(w) in popup_paths:
                return
            parent_path = w.winfo_parent()
            if not parent_path:
                break
            try:
                w = self.nametowidget(parent_path)
            except KeyError:
                break
        self._close_all_popups()

    def _set_menu_checkboxes_visible(self, visible):
        """Show or hide the master-toggle checkboxes on every toolbar menu button."""
        for cb, lbl in self._menu_checkboxes:
            if visible:
                # Re-insert the checkbox to the left of the label so centering is preserved
                cb.pack(side="left", padx=(7, 0), pady=5, before=lbl)
            else:
                cb.pack_forget()

    def _show_context_btn(self, text, cmd):
        self.btn_context.configure(text=text, command=cmd, state="normal", fg_color="#225588")
        if not self.btn_context.winfo_ismapped():
            self.btn_context.pack(side="right", padx=(4,0))

    def _hide_context_btn(self):
        self.btn_context.configure(state="disabled", text="", command=lambda: None)
        self.btn_context.pack_forget()   # remove entirely so no empty grey box shows

    # ── Themed text-input popup (matches the light theme + fonts) ────────────
    def _ask_string(self, title, prompt):
        """Light-themed input dialog (CTkInputDialog) replacing the native
        simpledialog. Returns the entered string, or None if cancelled."""
        try:
            return ctk.CTkInputDialog(title=title, text=prompt).get_input()
        except Exception:
            return tkinter.simpledialog.askstring(title, prompt)

    def _edit_track_exclusion(self):
        self._close_all_popups()
        cur=self.fv["track_exclusion_ft"].get() or "10"
        val=self._ask_string("Buffer Zone",
                             f"Buffer (clear) zone each side of pivot/corner tracks (ft).  Current: {cur}")
        if val is None: return
        val=val.strip()
        if val:
            self.fv["track_exclusion_ft"].set(val)   # write-trace → _redraw_tracks
            self._status(f"Buffer zone set to {val} ft.")

    def _edit_pass_edge_buffer(self):
        """Set BOTH the sprayer edge zone (how far in from each pass edge a
        shelter may sit) and the machine/tire width (the red drive zone down
        each pass centre) in one window."""
        self._close_all_popups()
        use_m = self.unit_var.get() == "Metric"
        unit = "m" if use_m else "ft"

        def _to_disp(ft):   # stored feet → displayed unit
            try: v = float(ft)
            except (ValueError, TypeError): v = 0.0
            return v * 0.3048 if use_m else v

        cur_edge = self.fv["pass_edge_buffer_ft"].get() or "25"
        cur_tire = self.fv["tire_width_ft"].get() or "14"
        try: sw_ft = float(self.fv["Sprayer_width"].get() or 0)
        except (ValueError, TypeError): sw_ft = 0.0

        def _to_ft(s):   # displayed unit → stored feet
            try: v = float(s.strip())
            except (ValueError, TypeError): return None
            if v < 0: return None
            return v / 0.3048 if use_m else v

        def _max_edge_ft():
            # The edge zone runs from a pass edge inward; the tire drive zone
            # sits in the pass centre. Max edge zone before the two meet is
            # half the sprayer width minus half the tire width. (tire_var is
            # resolved at call time, after it is created below.)
            t_ft = _to_ft(tire_var.get())
            if t_ft is None: t_ft = float(cur_tire or 14)
            return max(0.0, sw_ft / 2.0 - t_ft / 2.0)

        win = ctk.CTkToplevel(self)
        win.title("Sprayer Edge Zone & Tire Width")
        win.grab_set()
        ctk.CTkLabel(win, text="Sprayer edge zone & tire width",
                     font=ctk.CTkFont(family=FONT_HEADING, size=15)).pack(padx=24, pady=(18, 2))
        ctk.CTkLabel(win,
                     text=("Edge zone: how far IN from each pass EDGE a shelter may sit.\n"
                           "Tire width: the machine's drive zone down the pass centre."),
                     text_color=UI_MUTED,
                     font=ctk.CTkFont(size=12)).pack(padx=24, pady=(0, 8))

        erow = ctk.CTkFrame(win, fg_color="transparent"); erow.pack(padx=24, pady=4, fill="x")
        ctk.CTkLabel(erow, text="Edge zone:", width=110, anchor="w").pack(side="left")
        edge_var = tk.StringVar(value=("%g" % _to_disp(cur_edge)))
        ctk.CTkEntry(erow, textvariable=edge_var, width=90).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(erow, text=unit, width=24, anchor="w").pack(side="left")

        trow = ctk.CTkFrame(win, fg_color="transparent"); trow.pack(padx=24, pady=4, fill="x")
        ctk.CTkLabel(trow, text="Tire width:", width=110, anchor="w").pack(side="left")
        tire_var = tk.StringVar(value=("%g" % _to_disp(cur_tire)))
        ctk.CTkEntry(trow, textvariable=tire_var, width=90).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(trow, text=unit, width=24, anchor="w").pack(side="left")

        # Live max: depends on this field's sprayer width and the tire width
        # entered above, so it updates as the tire width changes.
        maxlbl = ctk.CTkLabel(win, text="", text_color=UI_ACCENT,
                              font=ctk.CTkFont(size=12))
        maxlbl.pack(padx=24, pady=(6, 2))

        def _refresh_max(*_):
            if sw_ft <= 0:
                maxlbl.configure(text="Set the sprayer width to bound the edge zone.")
                return
            maxlbl.configure(text="Max edge zone: %g %s  (sprayer %g − tire, ÷2)"
                             % (_to_disp(_max_edge_ft()), unit, _to_disp(sw_ft)))
        tire_var.trace_add("write", _refresh_max)
        _refresh_max()

        def do_apply():
            e_ft = _to_ft(edge_var.get())
            t_ft = _to_ft(tire_var.get())
            if e_ft is None or t_ft is None:
                self._status("Enter valid widths (>= 0)."); return
            m_ft = max(0.0, sw_ft / 2.0 - t_ft / 2.0)
            if sw_ft > 0 and e_ft > m_ft + 1e-6:
                tkinter.messagebox.showerror(
                    "Edge zone too wide",
                    "The sprayer edge zone can be at most %g %s for a %g %s sprayer "
                    "with a %g %s tire width — any wider and it would overlap the "
                    "machine's drive (tire) zone."
                    % (_to_disp(m_ft), unit, _to_disp(sw_ft), unit, _to_disp(t_ft), unit))
                return
            self.fv["pass_edge_buffer_ft"].set("%g" % e_ft)   # write-trace → _on_form_change
            self.fv["tire_width_ft"].set("%g" % t_ft)
            win.destroy()
            self._redraw_pass_buffer_overlay()
            self._status("Edge zone %g %s, tire width %g %s set."
                         % (_to_disp(e_ft), unit, _to_disp(t_ft), unit))

        ctk.CTkButton(win, text="Apply", height=36, command=do_apply).pack(
            fill="x", padx=24, pady=(8, 4))
        ctk.CTkButton(win, text="Cancel", height=36, fg_color="#555",
                      command=win.destroy).pack(fill="x", padx=24, pady=(0, 18))
        _center_on_parent(win, self)

    def _toggle_pass_buffer_overlay(self):
        """Show/hide the tire & sprayer edge zone overlay: RED stripes mark the
        machine/tire drive zone down each pass centre (plus the no-shelter
        middle); GREEN stripes mark the edge zone near each pass edge where
        shelters may sit."""
        self._close_all_popups()
        self.show_pass_buffer_overlay.set(not self.show_pass_buffer_overlay.get())
        self._redraw_pass_buffer_overlay()
        self._status("Tire & sprayer edge zone " +
                     ("shown." if self.show_pass_buffer_overlay.get() else "hidden."))

    def _toggle_route_around_inner(self):
        """Toggle whether sprayer passes route AROUND inner-boundary cutouts
        (lines break at the cutout edge — what really happens when the
        sprayer drives around a slough/building) vs. cut straight through
        them (default off doesn't change the pass-line drawing)."""
        self._close_all_popups()
        cur = bool(self.current_field.get("sprayer_routes_around_inner", True))
        self.current_field["sprayer_routes_around_inner"] = not cur
        self._redraw_passes()
        self._status("Sprayer " +
                     ("routes around" if not cur else "cuts straight through") +
                     " inner boundaries.")

    # ── Collapsible section card ────────────────────────────────────────────
    def _collapsible(self, parent, title, expanded=True):
        """A card with a clickable header that expands/collapses its content.
        Returns the content frame to build the section's widgets into."""
        wrap = ctk.CTkFrame(parent, fg_color=UI_CARD, corner_radius=8,
                            border_width=1, border_color=UI_BORDER)
        wrap.pack(fill="x", padx=8, pady=(0,8))
        hdr = ctk.CTkFrame(wrap, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6,2))
        tlbl = ctk.CTkLabel(hdr, text=title, anchor="w", text_color=UI_TEXT,
                            font=ctk.CTkFont(family=FONT_HEADING, size=13))
        tlbl.pack(side="left")
        chev = ctk.CTkLabel(hdr, text="▲", width=18, text_color=UI_MUTED,
                            font=ctk.CTkFont(family=FONT_BODY, size=12))
        chev.pack(side="right")
        content = ctk.CTkFrame(wrap, fg_color="transparent")
        content.pack(fill="x", padx=8, pady=(0,6))
        state = {"open": True}
        def toggle(_=None):
            state["open"] = not state["open"]
            if state["open"]:
                content.pack(fill="x", padx=8, pady=(0,6)); chev.configure(text="▲")
            else:
                content.pack_forget(); chev.configure(text="▼")
        for w in (hdr, tlbl, chev):
            w.bind("<Button-1>", toggle)
        if not expanded:
            toggle()
        return content

    # ── Body ───────────────────────────────────────────────────────────────────
    def _build_body(self):
        body=ctk.CTkFrame(self,corner_radius=0)
        body.pack(fill="both",expand=True)
        body.columnconfigure(0,weight=3); body.columnconfigure(1,weight=0); body.rowconfigure(0,weight=1)

        # Map frame
        mf=ctk.CTkFrame(body,corner_radius=8)
        mf.grid(row=0,column=0,sticky="nsew",padx=(8,4),pady=8)

        # ── Dropdown button bar ──
        bb=ctk.CTkFrame(mf,fg_color="transparent")
        bb.pack(fill="x",padx=6,pady=(6,2))

        # Pivot menu: pivot point + pivot tracks (concentric circles) + corner
        # tracks (polygon paths anchored to absolute lat/lon — stay put when
        # the pivot is moved). All share the same exclusion width.
        self._pivot_btn = self._make_menu_btn(bb, "🎯 Pivot", [
            ("Set Pivot Point",         self._mode_pivot),
            ("Draw Track Circle",       self._mode_track),
            ("Edit Span Lengths",       self._mode_edit_track_measurements),
            ("Set Buffer Zone (ft)",    self._edit_track_exclusion),
            ("Add Corner Path",         self._mode_add_corner_path),
        ], color="#1a6b3a",
           toggle_var=self.pivot_visible_var, toggle_fn=self._set_pivot_visible)
        self._pivot_btn.pack(side="left", padx=(0,4))

        self._bnd_btn = self._make_menu_btn(bb, "◌ Boundary", [
            ("Draw Outer",                self._mode_boundary),
            ("Draw Circle Outer Boundary", self._mode_boundary_circle),
            ("Upload File",           self._upload_boundary),
            ("Add Inner Boundary",    self._mode_add_inner_boundary),
            ("Delete Inner",          self._mode_delete_inner_boundary),
        ], color="#5a3a8a",
           toggle_var=self.boundary_visible_var, toggle_fn=self._set_boundary_visible)
        self._bnd_btn.pack(side="left", padx=(0,4))

        self._sp_btn = self._make_menu_btn(bb, "⋰⋮⋱ Sprayer", [
            ("Shift",                           self._mode_shift_sprayer),
            ("Import Sprayer Data (.shp/.geojson)", self._import_sprayer_data),
            ("Toggle Uploaded Paths on/off",    self._toggle_sprayer_passes),
            ("Clear Uploaded Paths",            self._clear_sprayer_data),
            ("Set Sprayer Edge Zone and Tire Width", self._edit_pass_edge_buffer),
            ("Toggle Tire and Sprayer Edge Zone",    self._toggle_pass_buffer_overlay),
            ("Toggle Pass Through Inner Boundaries", self._toggle_route_around_inner),
        ], color="#2a5a4a",
           toggle_var=self.sprayer_visible_var, toggle_fn=self._set_sprayer_visible)
        self._sp_btn.pack(side="left", padx=(0,4))

        # Planter menu: synthetic bay overlay (from bay-calculator inputs) PLUS
        # imported planter passes from a John Deere Operations Center Seeding
        # shapefile (the actual path the planter took on this field).
        self._pl_btn = self._make_menu_btn(bb, "🌱 Planter", [
            ("Shift",                     self._mode_shift_planter),
            ("Import Planter Data (.shp)", self._import_planter_data),
            ("Clear Planter Data",        self._clear_planter_passes),
            ("Toggle Bays Through Inner Boundaries", self._toggle_bays_through_inner),
        ], color="#3a5a1a",
           toggle_var=self.planter_visible_var, toggle_fn=self._set_planter_visible)
        self._pl_btn.pack(side="left", padx=(0,4))

        self._shelter_btn = self._make_menu_btn(bb, "🐝 Shelters", [
            ("Add Shelter Pin",      self._mode_add_shelter),
            ("Numbers: Tray count",  lambda: self._set_pin_mode("trays")),
            ("Numbers: Shelter #",   lambda: self._set_pin_mode("shelters")),
            ("Numbers: Off",         lambda: self._set_pin_mode("off")),
            ("Toggle Shelter Buffer Zone",   self._toggle_shelter_buffers),
            ("Set Shelter Buffer Size",      self._edit_shelter_buffer),
        ], color="#5a3000",
           toggle_var=self.shelters_visible_var, toggle_fn=self._set_shelters_visible)
        self._shelter_btn.pack(side="left", padx=(0,4))

        ctk.CTkButton(bb, text="↶ Reset Move", width=110, fg_color="#4a2a00",
                      command=self._undo_shelter_move).pack(side="left", padx=(0,4))

        # Context action button (only shown when a mode needs a "Done" action)
        self.btn_context = ctk.CTkButton(bb, text="", width=130, fg_color="#225588",
                                          state="disabled", command=lambda: None)
        # starts hidden — _show_context_btn packs it when a mode needs it

        self.map_frame=mf

        # ── Right panel (scrollable) ──
        right_outer=ctk.CTkFrame(body,width=370,corner_radius=8)
        right_outer.grid(row=0,column=1,sticky="nsew",padx=(4,8),pady=8)
        right_outer.pack_propagate(False)

        right=ctk.CTkScrollableFrame(right_outer,fg_color="transparent")
        right.pack(fill="both",expand=True)

        # Company / Year
        for label,var_attr,cb_attr,new_cmd,values_init in [
            ("Company:","company_var","company_cb",self._new_company,[]),
            ("Year:",   "year_var",   "year_cb",   self._new_year,   [str(datetime.date.today().year)])
        ]:
            row=ctk.CTkFrame(right,fg_color="transparent")
            row.pack(fill="x",padx=8,pady=(6,0))
            ctk.CTkLabel(row,text=label,width=65,anchor="e").pack(side="left")
            var=tk.StringVar(value=values_init[0] if values_init else "")
            setattr(self,var_attr,var)
            cb=ctk.CTkComboBox(row,variable=var,values=values_init,width=140,
                               command=self._on_company_change if "company" in var_attr else self._on_year_change)
            cb.pack(side="left",padx=4); setattr(self,cb_attr,cb)
            ctk.CTkButton(row,text="+",width=30,command=new_cmd).pack(side="left")

        # Field list — sortable Field / Company / Year columns (collapsible).
        # Fields starts expanded so the user lands on something usable; the
        # other right-side panels (Field Details / Bay / Bee) stay collapsed
        # until the user opens them.
        lf=self._collapsible(right,"Fields",expanded=True)
        _st=ttk.Style()
        try: _st.theme_use("default")
        except Exception: pass
        _st.configure("Fields.Treeview",background=UI_CARD,foreground=UI_TEXT,
                      fieldbackground=UI_CARD,borderwidth=0,rowheight=22,font=(FONT_BODY,10))
        _st.configure("Fields.Treeview.Heading",background=UI_HOVER,foreground=UI_TEXT,
                      relief="flat",font=(FONT_LABEL,10))
        _st.map("Fields.Treeview",background=[("selected",UI_SELECT)],foreground=[("selected",UI_TEXT)])
        _st.map("Fields.Treeview.Heading",background=[("active",UI_BORDER)])
        tree_wrap=ctk.CTkFrame(lf,fg_color="transparent")
        tree_wrap.pack(fill="x")
        self.field_tree=ttk.Treeview(tree_wrap,columns=("field","company","year"),show="headings",
                                     height=7,style="Fields.Treeview",selectmode="browse")
        for col,label,w,anchor in (("field","Field",130,"w"),("company","Company",110,"w"),("year","Year",55,"center")):
            self.field_tree.heading(col,text=label,command=lambda c=col:self._sort_fields(c))
            self.field_tree.column(col,width=w,anchor=anchor,stretch=(col=="field"))
        # CTk scrollbar to match the right-hand panel's scrollbar style.
        _fvsb=ctk.CTkScrollbar(tree_wrap,command=self.field_tree.yview)
        self.field_tree.configure(yscrollcommand=_fvsb.set)
        _fvsb.pack(side="right",fill="y",padx=(2,0))
        self.field_tree.pack(side="left",fill="x",expand=True)
        self.field_tree.bind("<<TreeviewSelect>>",self._on_field_select)
        self.field_tree.bind("<ButtonPress-1>",self._on_field_click)
        self.field_tree.bind("<Double-Button-1>",self._on_field_double_click)
        self._rename_entry=None; self._rename_ctx=None; self._last_field_press=(0,None)
        self._field_rows={}            # tree item id -> (company, year, name)
        self._field_sort_col=None; self._field_sort_rev=False
        br=ctk.CTkFrame(lf,fg_color="transparent"); br.pack(fill="x",pady=(3,0))
        ctk.CTkButton(br,text="+ New",width=58,command=self._new_field).pack(side="left")
        ctk.CTkButton(br,text="Load CSV",width=72,fg_color="#555",command=self._load_csv).pack(side="left",padx=4)
        ctk.CTkButton(br,text="💾 Save",width=66,command=self._save_field).pack(side="left",padx=(0,4))
        ctk.CTkButton(br,text="Delete",width=60,fg_color="#8b1a1a",command=self._delete_field).pack(side="right")

        # Field Details (collapsible)
        fd=self._collapsible(right,"Field Details",expanded=False)

        # Field preset (reuse a field's fixed geometry — name, pivot, tracks,
        # acres, boundary, corner zones — across years)
        fpr=ctk.CTkFrame(fd,fg_color="transparent"); fpr.pack(fill="x",pady=(4,0))
        ctk.CTkLabel(fpr,text="Preset:",width=55,anchor="w").pack(side="left")
        self.field_preset_var=tk.StringVar()
        self.field_preset_cb=ctk.CTkComboBox(fpr,variable=self.field_preset_var,
                                              values=["— Create New —"],width=150,
                                              command=self._on_field_preset_selected)
        self.field_preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(fpr,text="💾",width=30,command=self._overwrite_field_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(fpr,text="🗑",width=30,command=self._delete_field_preset).pack(side="left")
        fpr2=ctk.CTkFrame(fd,fg_color="transparent"); fpr2.pack(fill="x",pady=(2,2))
        ctk.CTkLabel(fpr2,text="Name:",width=55,anchor="w").pack(side="left")
        self.field_preset_name_var=tk.StringVar()
        self._loaded_field_preset_name=None
        ctk.CTkEntry(fpr2,textvariable=self.field_preset_name_var,width=110).pack(side="left",padx=(2,2))
        ctk.CTkButton(fpr2,text="Update Name",width=90,command=self._save_field_preset_unified).pack(side="left")

        fs=ctk.CTkFrame(fd,fg_color="transparent"); fs.pack(fill="x",pady=(4,0))
        self.fv={}; self.hint_labels={}; self.field_labels={}
        form_rows=[
            ("Name",               "Name",                  "Field name — used as folder/file name", False),
            ("company",            "Company",                "Type any name. New companies are created automatically on save.", False),
            ("PP_Latitude",        "Pivot Latitude",         "Decimal degrees — or click 📍 on map",  False),
            ("PP_Longitude",       "Pivot Longitude",        "Decimal degrees",                        False),
            ("lld",                "Legal Land Description", "Auto-filled to NE/NW/SE/SW when pivot is placed. Editable — type a section (32-14-22-W4), half (N-32-14-22-W4), or quarter (NE-32-14-22-W4).", False),
            ("Planting_angle",     "Planting Angle (°)",     "Crop row direction. Blank = same as Spray Angle.", False),
            ("Spray_angle",        "Spray Angle (°)",        "Sprayer pass direction. Blank = same as Planting Angle.", False),
            ("Sprayer_width",      "Sprayer Width (ft)",     "",                                       False),
            ("acres",              "Acres",                  "Total field area in acres",              False),
        ]
        for key,display,hint,unit_dep in form_rows:
            lbl=ctk.CTkLabel(fs,text=display,anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11))
            lbl.pack(fill="x")
            if unit_dep: self.field_labels[key]=lbl
            if hint:
                hl=ctk.CTkLabel(fs,text=hint,anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10))
                hl.pack(fill="x")
                if unit_dep: self.hint_labels[key]=hl
            v=tk.StringVar(); ctk.CTkEntry(fs,textvariable=v).pack(fill="x",pady=(0,5))
            self.fv[key]=v

        # ── Shelters: choose how the exact count is specified ──
        # Backing storage vars (round-trip via _form_from_field/_field_from_form);
        # only the one matching the chosen mode is shown in the entry below.
        for k in ("num_structures","spacing","shelters_per_acre","acres_per_shelter"):
            self.fv[k]=tk.StringVar()
        # Optional grid-row override: "" = automatic, N = aim for ~N rows of
        # shelters (fewer lateral columns → more rows for the same count).
        self.fv["shelter_rows"]=tk.StringVar()
        # Track exclusion lives in the Pivot menu now, but keep its backing var
        # (used by _redraw_tracks / get_tent_positions) and its write-trace.
        self.fv["track_exclusion_ft"]=tk.StringVar(value="10")
        # Sprayer-pass edge buffer (Sprayer menu). How far in from the edge of
        # any sprayer pass shelters can sit; the middle of each pass becomes
        # a kill zone of width max(0, sprayer_width − 2 × buffer). Defaults
        # to 25 ft — change via Sprayer → Set Edge Buffer (ft); 0 = no band /
        # full outside-ring exclusion.
        self.fv["pass_edge_buffer_ft"]=tk.StringVar(value="25")
        # Machine/tire drive width (ft) shown as the red zone down each pass
        # centre and the outside-round centre. Visual only — placement keeps
        # shelters in the edge zones regardless.
        self.fv["tire_width_ft"]=tk.StringVar(value="14")
        self._shelter_mode_labels={
            "Total shelters":           "total",
            "Shelters per acre":        "per_acre",
            "Acres per shelter":        "acres_per_shelter",
            "Spacing between shelters": "spacing",
            "1 tray per shelter":       "trays_1",
            "2 trays per shelter":      "trays_2",
            "Manual pins only":         "manual",
        }
        self._shelter_mode_inverse={v:k for k,v in self._shelter_mode_labels.items()}
        # Only modes with user-editable values have an fv key. The two trays
        # modes are auto-derived from bee allocation (gals/acre × acres ÷
        # gals/tray) — the entry just displays the computed count.
        self._shelter_mode_key={"total":"num_structures","per_acre":"shelters_per_acre",
                                 "acres_per_shelter":"acres_per_shelter","spacing":"spacing"}
        # Variables for the shelter mode dropdown live here so they're
        # defined when _form_from_field runs, but the UI for them is built
        # later under Bee Allocation (the shelter count is the primary
        # input to the bee math).
        self.shelter_mode_var=tk.StringVar(value="Total shelters")
        self.shelter_value_var=tk.StringVar()
        self.shelter_value_var.trace_add("write", self._on_shelter_value_change)

        # Shelter allocation toggle for the outside sprayer pass lives in Bee
        # Allocation. The outside round itself is always shown when a boundary
        # exists — there is no such thing as a field with no outside pass.
        self.shelters_in_outside_var = tk.StringVar(value="No")
        self.shelter_at_pivot_var = tk.StringVar(value="No")

        self.fv["Planting_angle"].set(""); self.fv["Spray_angle"].set(""); self.fv["Sprayer_width"].set("133")

        # Bay calculator (collapsible)
        bc=self._collapsible(right,"Bay Calculator",expanded=False)

        # Preset: dropdown row
        preset_row=ctk.CTkFrame(bc,fg_color="transparent")
        preset_row.pack(fill="x",pady=(2,0))
        ctk.CTkLabel(preset_row,text="Preset:",width=55,anchor="w").pack(side="left")
        self.preset_var=tk.StringVar()
        self.preset_cb=ctk.CTkComboBox(preset_row,variable=self.preset_var,
                                        values=["— Create New —"],width=160,
                                        command=self._on_preset_selected)
        self.preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(preset_row,text="💾",width=30,command=self._overwrite_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(preset_row,text="🗑",width=30,command=self._delete_preset).pack(side="left")
        # Name entry + rename button
        preset_name_row=ctk.CTkFrame(bc,fg_color="transparent")
        preset_name_row.pack(fill="x",pady=(2,2))
        ctk.CTkLabel(preset_name_row,text="Name:",width=55,anchor="w").pack(side="left")
        self.preset_name_var=tk.StringVar()
        self._loaded_preset_name=None
        ctk.CTkEntry(preset_name_row,textvariable=self.preset_name_var,width=110).pack(side="left",padx=(2,2))
        ctk.CTkButton(preset_name_row,text="Update Name",width=90,command=self._save_preset_unified).pack(side="left")

        ctk.CTkFrame(bc,height=1,fg_color=UI_BORDER).pack(fill="x",pady=(2,4))

        # Crop type: bays vs blanket-planted. Canola needs female bays so the
        # planter leaves the male strips empty for pollination access; other
        # crops are blanket-planted with no bay structure, so shelters can
        # sit anywhere in the field. With this off, get_tent_positions
        # ignores the row mask / bay layout and uses a uniform grid.
        self.use_bays_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bc, text="Crop uses planting bays (e.g. canola)",
                        variable=self.use_bays_var,
                        command=self._on_use_bays_toggle).pack(anchor="w", pady=(2,4))

        # Always-visible inputs: row spacing + total planter rows. These define
        # the planter pass width (rows × spacing) regardless of crop type.
        common_rows=[
            ("row_spacing_in", "Row Spacing (inches)"),
            ("total_rows",     "Total Rows on Planter"),
        ]
        for key,label in common_rows:
            ctk.CTkLabel(bc,text=label,anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
            v=tk.StringVar(); ctk.CTkEntry(bc,textvariable=v).pack(fill="x",pady=(0,4))
            self.fv[key]=v
        self.planter_pass_lbl=ctk.CTkLabel(bc,text="Planter pass: —",anchor="w",text_color=UI_ACCENT)
        self.planter_pass_lbl.pack(fill="x")

        # Bay-only widgets: female/male row counts (per repeat unit), bay
        # widths, row layout dropdown, mask preview, custom mask entry. All
        # wrapped in one frame so _on_use_bays_toggle can hide them as a group
        # when "Crop uses planting bays" is unchecked.
        self._bay_only_frame=ctk.CTkFrame(bc,fg_color="transparent")
        self._bay_only_frame.pack(fill="x")
        bay_only_rows=[
            ("num_female_rows",  "Female Rows (per repeat unit)"),
            ("num_male_rows",    "Male Rows (per repeat unit)"),
            ("bay_gap_in",       "Gap between male & female bays (inches)"),
        ]
        for key,label in bay_only_rows:
            ctk.CTkLabel(self._bay_only_frame,text=label,anchor="w",
                         font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
            v=tk.StringVar(); ctk.CTkEntry(self._bay_only_frame,textvariable=v).pack(fill="x",pady=(0,4))
            self.fv[key]=v
        self.repeats_lbl=ctk.CTkLabel(self._bay_only_frame,text="Repeats: —",
                                       anchor="w",text_color=UI_ACCENT)
        self.repeats_lbl.pack(fill="x")
        self.female_bay_lbl=ctk.CTkLabel(self._bay_only_frame,text="Female bay width: —",
                                          anchor="w",text_color=UI_ACCENT)
        self.female_bay_lbl.pack(fill="x")
        self.male_bay_lbl=ctk.CTkLabel(self._bay_only_frame,text="Male bay width: —",
                                        anchor="w",text_color=UI_ACCENT)
        self.male_bay_lbl.pack(fill="x")
        self.bay_gap_lbl=ctk.CTkLabel(self._bay_only_frame,text="Gap: none",
                                       anchor="w",text_color=UI_ACCENT)
        self.bay_gap_lbl.pack(fill="x")

        ctk.CTkLabel(self._bay_only_frame,text="Row layout",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x",pady=(8,0))
        ctk.CTkLabel(self._bay_only_frame,
            text="Outer = male rows split across both ends (joins next pass to form a male bay).\n"
                 "Centered = male rows as a single block in the middle.\n"
                 "Custom = type your own mask (M = male, F = female).",
            anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10),justify="left").pack(fill="x")
        self.row_layout_var=tk.StringVar(value="Centered male")
        self._row_layout_labels={"Outer male":"outer","Centered male":"centered","Custom":"custom"}
        self._row_layout_inverse={v:k for k,v in self._row_layout_labels.items()}
        ctk.CTkComboBox(self._bay_only_frame,variable=self.row_layout_var,
                        values=list(self._row_layout_labels.keys()),
                        command=lambda v: self._on_row_layout_change()
                        ).pack(fill="x",pady=(2,4))
        self.custom_mask_var=tk.StringVar(value="")
        self.custom_mask_entry=ctk.CTkEntry(self._bay_only_frame,textvariable=self.custom_mask_var,
                                             placeholder_text="e.g. MMFFFFFFFFFFFFFFFFMM")
        self.row_mask_lbl=ctk.CTkLabel(self._bay_only_frame,text="Mask: —",anchor="w",
                                        text_color=UI_ACCENT,
                                        font=ctk.CTkFont(family=FONT_BODY,size=10))
        self.row_mask_lbl.pack(fill="x",pady=(2,4))

        # Per-field switch: use the uploaded JD planter passes (if any) as the
        # ground truth for shelter placement, OR fall back to the synthetic
        # math grid computed from the bay calculator. Default ON — if you
        # have real data you almost always want to use it.
        self.use_imported_passes_var=tk.BooleanVar(value=False)
        self._planter_file_var=tk.StringVar(value="")
        ctk.CTkLabel(bc,text="Planter pass source",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x",pady=(8,0))
        self._use_planter_cb=ctk.CTkCheckBox(bc,text="Use uploaded planter data (if any)",
                        variable=self.use_imported_passes_var,
                        state="disabled",
                        command=self._on_form_change)
        self._use_planter_cb.pack(anchor="w",pady=(2,0))
        ctk.CTkLabel(bc,textvariable=self._planter_file_var,anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=10),
                     text_color="#888888").pack(fill="x",pady=(0,2))
        ctk.CTkButton(bc,text="Upload Planter Data (.shp)",
                      command=self._import_planter_data,
                      height=26).pack(fill="x",pady=(0,4))
        # No "Recalculate Bays" button — the bay widths and map redraw
        # automatically whenever any bay-calculator field changes.

        # ── Bee Allocation (collapsible) ──────────────────────────────────
        ba=self._collapsible(right,"Bee Allocation",expanded=False)

        bp_row=ctk.CTkFrame(ba,fg_color="transparent")
        bp_row.pack(fill="x",pady=(2,0))
        ctk.CTkLabel(bp_row,text="Preset:",width=55,anchor="w").pack(side="left")
        self.bee_preset_var=tk.StringVar()
        self.bee_preset_cb=ctk.CTkComboBox(bp_row,variable=self.bee_preset_var,
                                            values=["— Create New —"],width=160,
                                            command=self._on_bee_preset_selected)
        self.bee_preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(bp_row,text="💾",width=30,command=self._overwrite_bee_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(bp_row,text="🗑",width=30,command=self._delete_bee_preset).pack(side="left")
        bp_row2=ctk.CTkFrame(ba,fg_color="transparent")
        bp_row2.pack(fill="x",pady=(2,2))
        ctk.CTkLabel(bp_row2,text="Name:",width=55,anchor="w").pack(side="left")
        self.bee_preset_name_var=tk.StringVar()
        self._loaded_bee_preset_name=None
        ctk.CTkEntry(bp_row2,textvariable=self.bee_preset_name_var,width=110).pack(side="left",padx=(2,2))
        ctk.CTkButton(bp_row2,text="Update Name",width=90,command=self._save_bee_preset_unified).pack(side="left")

        ctk.CTkFrame(ba,height=1,fg_color=UI_BORDER).pack(fill="x",pady=(2,4))

        # Shelter count drives the bee math, so the mode + value live here at
        # the top of Bee Allocation (was previously under Field Details).
        ctk.CTkLabel(ba,text="Shelters",anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        ctk.CTkComboBox(ba,variable=self.shelter_mode_var,values=list(self._shelter_mode_labels.keys()),
                        command=self._on_shelter_mode_change).pack(fill="x",pady=(0,2))
        self._shelter_entry=ctk.CTkEntry(ba,textvariable=self.shelter_value_var)
        self._shelter_entry.pack(fill="x",pady=(0,2))
        self.shelter_hint_lbl=ctk.CTkLabel(ba,text="",anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10))
        self.shelter_hint_lbl.pack(fill="x",pady=(0,2))

        bee_rows=[
            ("gals_per_acre", "Gals/acre"),
            ("gals_per_tray", "Gals/tray"),
        ]
        for key,label in bee_rows:
            ctk.CTkLabel(ba,text=label,anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
            v=tk.StringVar(); ctk.CTkEntry(ba,textvariable=v).pack(fill="x",pady=(0,4))
            self.fv[key]=v

        self.bee_total_gals_lbl  = ctk.CTkLabel(ba,text="Total gals:   —", anchor="w",text_color=UI_ACCENT)
        self.bee_total_gals_lbl.pack(fill="x")
        self.bee_total_trays_lbl = ctk.CTkLabel(ba,text="Total trays:  —", anchor="w",text_color=UI_ACCENT)
        self.bee_total_trays_lbl.pack(fill="x")
        self.bee_per_shelter_lbl = ctk.CTkLabel(ba,text="Per shelter:  —", anchor="w",text_color=UI_ACCENT)
        self.bee_per_shelter_lbl.pack(fill="x")
        self.bee_short_lbl       = ctk.CTkLabel(ba,text="", anchor="w",text_color=UI_WARN)
        self.bee_short_lbl.pack(fill="x",pady=(0,4))

        ctk.CTkLabel(ba,text="Distribution:",anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        self.tray_dist_var = tk.StringVar(value="Spread evenly")
        self._tray_dist_labels = {
            "Spread evenly":      "even",
            "Outside edge first": "outside",
            "Alternating bays":   "alternating",
        }
        self._tray_dist_inverse = {v: k for k, v in self._tray_dist_labels.items()}
        ctk.CTkComboBox(ba, variable=self.tray_dist_var,
                        values=list(self._tray_dist_labels.keys()),
                        command=self._on_tray_dist_change).pack(fill="x",pady=(0,4))

        ctk.CTkFrame(ba, height=1, fg_color=UI_BORDER).pack(fill="x", pady=(4,4))
        ctk.CTkLabel(ba, text="Shelters in Outside Pass", anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL, size=11)).pack(fill="x")
        ctk.CTkLabel(ba, text="Allow shelters inside the outside boundary pass zone",
                     anchor="w", text_color=UI_MUTED,
                     font=ctk.CTkFont(size=10)).pack(fill="x")
        ctk.CTkSegmentedButton(ba, values=["Yes", "No"],
                               variable=self.shelters_in_outside_var,
                               command=lambda v: self._on_shelters_in_outside_toggle()
                               ).pack(fill="x", pady=(2, 8))

        ctk.CTkFrame(ba, height=1, fg_color=UI_BORDER).pack(fill="x", pady=(0,4))
        ctk.CTkLabel(ba, text="Shelter at Pivot Point", anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL, size=11)).pack(fill="x")
        ctk.CTkLabel(ba, text="Place a shelter pin at the field centre (pivot)",
                     anchor="w", text_color=UI_MUTED,
                     font=ctk.CTkFont(size=10)).pack(fill="x")
        ctk.CTkSegmentedButton(ba, values=["Yes", "No"],
                               variable=self.shelter_at_pivot_var,
                               command=lambda v: self._on_shelter_at_pivot_toggle()
                               ).pack(fill="x", pady=(2, 8))

        # No "Calculate Trays" button — the summary and the map redraw
        # automatically whenever any bee allocation field changes.

        self._setup_form_traces()

        # Save Field moved to the field-list row; Generate moved to the top bar.
        # Generation progress is reported on the status line (see _log), so the
        # old log textbox (and its empty white space) is gone.

        # Hidden track list widget — kept for internal track management logic
        _hidden_frame=tk.Frame(self)  # never packed
        self.track_lb=tk.Listbox(_hidden_frame)
        self.excl_var=tk.StringVar(value="10")

    def _setup_form_traces(self):
        for v in self.fv.values():
            v.trace_add("write", self._on_form_change)
        self.fv["track_exclusion_ft"].trace_add("write", self._on_track_excl_change)
        # Auto-recalc bays whenever a bay-calculator field changes (debounced).
        for k in ("row_spacing_in","total_rows","num_female_rows","num_male_rows","bay_gap_in"):
            if k in self.fv:
                self.fv[k].trace_add("write", self._on_bay_change)
        # Custom-mask writes feed into the bay redraw too (debounced via
        # _on_bay_change → _calc_bays → resolve mask label + redraw shelters).
        self.custom_mask_var.trace_add("write", self._on_bay_change)

    def _on_bay_change(self, *_):
        rid=getattr(self, "_bay_refresh_id", None)
        if rid:
            try: self.after_cancel(rid)
            except Exception: pass
        self._bay_refresh_id=self.after(400, self._calc_bays)

    def _on_form_change(self, *_):
        # Bee summary and the auto-mode shelter count recompute immediately.
        try: self._update_map_field_label()   # keep the on-map name in sync
        except Exception: pass
        try: self._refresh_bee_summary()
        except Exception: pass
        try: self._refresh_shelter_value_display()
        except Exception: pass
        if not self.show_shelters.get(): return
        if self._shelter_refresh_id:
            self.after_cancel(self._shelter_refresh_id)
        self._shelter_refresh_id = self.after(600, self._redraw_shelters)

    def _on_shelters_in_outside_toggle(self):
        """Shelter allocation for outside pass changed — recompute shelters."""
        self._on_form_change()

    def _on_shelter_at_pivot_toggle(self, _=None):
        """Pivot shelter toggle changed — recompute shelters."""
        self._on_form_change()

    def _on_track_excl_change(self, *_):
        if getattr(self, "_track_excl_refresh_id", None):
            self.after_cancel(self._track_excl_refresh_id)
        # Corner-track offset paths use the same excl_m as pivot tracks, so a
        # change to track_exclusion_ft needs to redraw both.
        def _refresh():
            self._redraw_tracks()
            self._redraw_corner_arms()
        self._track_excl_refresh_id = self.after(600, _refresh)

    def _init_map(self):
        # No on-disk tile cache: tiles live only in memory for the session, so
        # every launch pulls the latest imagery straight from Google.
        self.map_widget=tkintermapview.TkinterMapView(self.map_frame,corner_radius=6)
        self.map_widget.pack(fill="both",expand=True,padx=6,pady=(4,6))
        self.map_widget.set_tile_server(SATELLITE_URL,max_zoom=21)
        self.map_widget.set_position(DEFAULT_LAT,DEFAULT_LON)
        self.map_widget.set_zoom(DEFAULT_ZOOM)

        # Always-visible field name, overlaid top-right of the satellite
        # imagery (the map widget itself, BELOW the toolbar) so it never covers
        # the toolbar's ✔ Save / context buttons. Visible even when the side
        # panels are collapsed.
        self.map_field_label = ctk.CTkLabel(
            self.map_widget, text="", anchor="e",
            fg_color="transparent", text_color="#1E90FF",
            font=ctk.CTkFont(family=FONT_HEADING, size=16, weight="bold"))
        # Placed/hidden by _update_map_field_label (hidden until a field loads).
        self._update_map_field_label()

    # ── Bay Presets ────────────────────────────────────────────────────────────
    def _load_bay_presets(self):
        try:
            p=DATA_DIR/"bay_presets.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
        return []

    def _save_bay_presets(self, presets):
        try:
            DATA_DIR.mkdir(parents=True,exist_ok=True)
            (DATA_DIR/"bay_presets.json").write_text(json.dumps(presets,indent=2),encoding="utf-8")
            self._git_push("sync bay presets")
        except Exception as ex:
            tkinter.messagebox.showerror("Preset Error",str(ex))

    def _refresh_preset_list(self):
        presets=self._load_bay_presets()
        names=["— Create New —"]+[p["name"] for p in presets]
        self.preset_cb.configure(values=names)

    def _on_preset_selected(self, name):
        if name == "— Create New —":
            # Blank all bay calculator fields and clear the name entry
            for k in ("row_spacing_in","total_rows","num_female_rows","num_male_rows","bay_gap_in"):
                if k in self.fv: self.fv[k].set("")
            self.row_layout_var.set("Centered male")
            self.custom_mask_var.set("")
            self.shelter_mode_var.set("Total shelters")
            self.shelter_value_var.set("")
            self.preset_name_var.set("")
            self._loaded_preset_name = None
            self._on_row_layout_change()
            return
        if not name: return
        presets=self._load_bay_presets()
        for p in presets:
            if p["name"]==name:
                for k in ("row_spacing_in","total_rows","num_female_rows","num_male_rows","bay_gap_in"):
                    if k in p and k in self.fv: self.fv[k].set(str(p[k]))
                # Row layout & custom mask are new — older presets that lack
                # them default to "centered" (the historical implicit shape).
                rl = p.get("row_layout","centered")
                self.row_layout_var.set(self._row_layout_inverse.get(rl,"Centered male"))
                self.custom_mask_var.set(str(p.get("custom_row_mask","")))
                # Restore shelter mode + value if saved in preset
                s_mode = p.get("shelter_mode","")
                if s_mode and s_mode in self._shelter_mode_inverse:
                    self.shelter_mode_var.set(self._shelter_mode_inverse[s_mode])
                    s_key = self._shelter_mode_key.get(s_mode,"num_structures")
                    if s_key in p and s_key in self.fv:
                        self.fv[s_key].set(str(p[s_key]))
                        self._loading_shelter_value = True
                        self.shelter_value_var.set(str(p[s_key]))
                        self._loading_shelter_value = False
                self._on_row_layout_change()
                self.preset_name_var.set(name)
                self._loaded_preset_name = name
                break

    def _bay_preset_entry(self, name):
        """Build the dict written to bay_presets.json for the current bay-calc
        and shelter-allocation state. One spot so all save paths stay in sync."""
        s_mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        s_key  = self._shelter_mode_key.get(s_mode,"num_structures")
        entry = {"name":name,
                 "row_spacing_in":self.fv["row_spacing_in"].get(),
                 "total_rows":self.fv["total_rows"].get(),
                 "num_female_rows":self.fv["num_female_rows"].get(),
                 "num_male_rows":self.fv["num_male_rows"].get(),
                 "bay_gap_in":self.fv["bay_gap_in"].get(),
                 "row_layout":self._row_layout_labels.get(self.row_layout_var.get(),"centered"),
                 "custom_row_mask":self.custom_mask_var.get(),
                 "shelter_mode":s_mode}
        if s_key and s_key in self.fv:
            entry[s_key] = self.fv[s_key].get()
        return entry

    def _save_preset_unified(self):
        """Save bay-calc state under the name in the Name entry.
        - New name → creates a new preset.
        - Name matches existing → overwrites it.
        - Name differs from the preset that was loaded → renames it (removes old,
          saves under new name), so no duplicate is created."""
        name = self.preset_name_var.get().strip()
        if not name:
            self._status("Enter a preset name before saving."); return
        presets = self._load_bay_presets()
        loaded  = getattr(self, "_loaded_preset_name", None)
        # Remove old entry (rename case) and any existing entry with the target name
        presets = [p for p in presets if p["name"] != name and p["name"] != loaded]
        presets.append(self._bay_preset_entry(name))
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set(name)
        self._loaded_preset_name = name
        self._status(f"Saved bay preset: {name}")

    def _overwrite_preset(self):
        """💾 Save — write the current bay-calc state into the selected preset,
        keeping its name. Creates from the Name box only if nothing is selected."""
        loaded = getattr(self, "_loaded_preset_name", None)
        sel = self.preset_var.get()
        name = loaded or (sel if sel and sel != "— Create New —" else self.preset_name_var.get().strip())
        if not name:
            self._status("Pick a preset or type a name to save."); return
        presets = [p for p in self._load_bay_presets() if p["name"] != name]
        presets.append(self._bay_preset_entry(name))
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set(name)
        self.preset_name_var.set(name)
        self._loaded_preset_name = name
        self._status(f"Saved bay preset: {name}")

    def _delete_preset(self):
        name=self.preset_var.get()
        if not name or name=="— Create New —": return
        presets=[p for p in self._load_bay_presets() if p["name"]!=name]
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set("— Create New —")
        self.preset_name_var.set("")
        self._loaded_preset_name=None

    # ── Bee Allocation Presets ────────────────────────────────────────────────
    def _load_bee_presets(self):
        try:
            p=DATA_DIR/"bee_presets.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
        return []

    def _save_bee_presets(self, presets):
        try:
            DATA_DIR.mkdir(parents=True,exist_ok=True)
            (DATA_DIR/"bee_presets.json").write_text(json.dumps(presets,indent=2),encoding="utf-8")
            self._git_push("sync bee presets")
        except Exception as ex:
            tkinter.messagebox.showerror("Preset Error",str(ex))

    def _refresh_bee_preset_list(self):
        names=["— Create New —"]+[p["name"] for p in self._load_bee_presets()]
        self.bee_preset_cb.configure(values=names)

    def _on_bee_preset_selected(self, name):
        if name == "— Create New —":
            for k in ("gals_per_acre","gals_per_tray"):
                if k in self.fv: self.fv[k].set("")
            self.bee_preset_name_var.set("")
            self._loaded_bee_preset_name = None
            return
        if not name: return
        for p in self._load_bee_presets():
            if p["name"]==name:
                for k in ("gals_per_acre","gals_per_tray"):
                    if k in p and k in self.fv: self.fv[k].set(str(p[k]))
                # Restore the shelter rule (mode + value), then the distribution.
                s_mode = p.get("shelter_mode")
                if s_mode and s_mode in self._shelter_mode_inverse:
                    s_key = self._shelter_mode_key.get(s_mode)
                    if s_key and s_key in self.fv and s_key in p:
                        self.fv[s_key].set(str(p[s_key]))
                    self.shelter_mode_var.set(self._shelter_mode_inverse[s_mode])
                    self._on_shelter_mode_change()
                dist = p.get("tray_distribution")
                if dist and dist in self._tray_dist_inverse:
                    self.tray_dist_var.set(self._tray_dist_inverse[dist])
                    self._on_tray_dist_change()
                self.bee_preset_name_var.set(name)
                self._loaded_bee_preset_name = name
                break

    def _bee_preset_entry(self, name):
        """Build the dict written to bee_presets.json — gallons plus the shelter
        rule (mode + its value, e.g. Acres per shelter = 2) and the tray
        distribution, so a bee preset restores the whole allocation."""
        s_mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(), "total")
        s_key  = self._shelter_mode_key.get(s_mode)
        entry = {"name":name,
                 "gals_per_acre":self.fv["gals_per_acre"].get(),
                 "gals_per_tray":self.fv["gals_per_tray"].get(),
                 "shelter_mode":s_mode,
                 "tray_distribution":self._tray_dist_labels.get(self.tray_dist_var.get(),"even")}
        if s_key and s_key in self.fv:
            entry[s_key] = self.fv[s_key].get()
        return entry

    def _save_bee_preset_unified(self):
        name = self.bee_preset_name_var.get().strip()
        if not name:
            self._status("Enter a preset name before saving."); return
        presets = self._load_bee_presets()
        loaded  = getattr(self, "_loaded_bee_preset_name", None)
        presets = [p for p in presets if p["name"] != name and p["name"] != loaded]
        presets.append(self._bee_preset_entry(name))
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set(name)
        self._loaded_bee_preset_name = name
        self._status(f"Saved bee preset: {name}")

    def _overwrite_bee_preset(self):
        """💾 Save — write the current allocation (incl. shelter info) into the
        selected preset, keeping its name. Creates one from the Name box only
        when nothing is selected."""
        loaded = getattr(self, "_loaded_bee_preset_name", None)
        sel = self.bee_preset_var.get()
        name = loaded or (sel if sel and sel != "— Create New —" else self.bee_preset_name_var.get().strip())
        if not name:
            self._status("Pick a preset or type a name to save."); return
        presets = [p for p in self._load_bee_presets() if p["name"] != name]
        presets.append(self._bee_preset_entry(name))
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set(name)
        self.bee_preset_name_var.set(name)
        self._loaded_bee_preset_name = name
        self._status(f"Saved bee preset: {name}")

    def _delete_bee_preset(self):
        name=self.bee_preset_var.get()
        if not name or name=="— Create New —": return
        presets=[p for p in self._load_bee_presets() if p["name"]!=name]
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set("— Create New —")
        self.bee_preset_name_var.set("")
        self._loaded_bee_preset_name=None

    # ── Field Presets (fixed field geometry reused year to year) ──────────────
    # Captures only the physical layout that stays constant: pivot point,
    # pivot tracks + exclusion, acres, boundary polygon, corner zones. Leaves
    # year-specific values (name, planting angle, shelter count, bee allocation)
    # untouched so a new year's map starts from the known geometry.
    _FIELD_PRESET_SCALARS = ("Name","PP_Latitude","PP_Longitude","acres","track_exclusion_ft")

    def _load_field_presets(self):
        try:
            p=DATA_DIR/"field_presets.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
        return []

    def _save_field_presets(self, presets):
        try:
            DATA_DIR.mkdir(parents=True,exist_ok=True)
            (DATA_DIR/"field_presets.json").write_text(json.dumps(presets,indent=2),encoding="utf-8")
            self._git_push("sync field presets")
        except Exception as ex:
            tkinter.messagebox.showerror("Preset Error",str(ex))

    def _refresh_field_preset_list(self):
        names=["— Create New —"]+[p["name"] for p in self._load_field_presets()]
        self.field_preset_cb.configure(values=names)

    def _on_field_preset_selected(self, name):
        if name == "— Create New —":
            self.field_preset_name_var.set("")
            self._loaded_field_preset_name = None
            return
        if not name: return
        p=next((x for x in self._load_field_presets() if x["name"]==name), None)
        if not p: return
        # Scalar physical attrs → form vars + current_field
        for k in self._FIELD_PRESET_SCALARS:
            if k in p:
                if k in self.fv: self.fv[k].set(str(p[k]))
                self.current_field[k]=str(p[k])
        # Structured physical attrs → current_field
        self.current_field["pivot_tracks"]=list(p.get("pivot_tracks") or [])
        bp=p.get("boundary_polygon")
        self.current_field["boundary_polygon"]=[list(pt) for pt in bp] if bp else None
        self.current_field["corner_arms"]=p.get("corner_arms") or []
        self.boundary_pts=[]
        self._refresh_track_list()
        self._redraw_all()
        self.field_preset_name_var.set(name)
        self._loaded_field_preset_name = name
        self._status(f"Applied field preset: {name} — set name, angle & bees for this year.")

    def _field_preset_entry(self, name):
        """Build the dict written to field_presets.json for the current form state."""
        f=self._field_from_form()
        bp=f.get("boundary_polygon")
        entry={"name":name,
               "pivot_tracks":list(f.get("pivot_tracks") or []),
               "boundary_polygon":[list(pt) for pt in bp] if bp else None,
               "corner_arms":f.get("corner_arms") or []}
        for k in self._FIELD_PRESET_SCALARS:
            entry[k]=f.get(k,"")
        return entry

    def _save_field_preset_unified(self):
        name = self.field_preset_name_var.get().strip()
        if not name:
            self._status("Enter a preset name before saving."); return
        presets = self._load_field_presets()
        loaded  = getattr(self, "_loaded_field_preset_name", None)
        presets = [p for p in presets if p["name"] != name and p["name"] != loaded]
        presets.append(self._field_preset_entry(name))
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set(name)
        self._loaded_field_preset_name = name
        self._status(f"Saved field preset: {name}")

    def _overwrite_field_preset(self):
        """💾 Save — write the current field geometry into the selected preset,
        keeping its name. Creates from the Name box only if nothing is selected."""
        loaded = getattr(self, "_loaded_field_preset_name", None)
        sel = self.field_preset_var.get()
        name = loaded or (sel if sel and sel != "— Create New —" else self.field_preset_name_var.get().strip())
        if not name:
            self._status("Pick a preset or type a name to save."); return
        presets = [p for p in self._load_field_presets() if p["name"] != name]
        presets.append(self._field_preset_entry(name))
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set(name)
        self.field_preset_name_var.set(name)
        self._loaded_field_preset_name = name
        self._status(f"Saved field preset: {name}")

    def _delete_field_preset(self):
        name=self.field_preset_var.get()
        if not name or name=="— Create New —": return
        presets=[p for p in self._load_field_presets() if p["name"]!=name]
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set("— Create New —")
        self.field_preset_name_var.set("")
        self._loaded_field_preset_name=None

    # ── Bee tray math ────────────────────────────────────────────────────────
    def _compute_bee_distribution(self, num_shelters, row_indices=None,
                                  shelter_positions_latlon=None):
        """Return (total_trays, per_shelter_list, short_count, total_gals).

        Returns (None, [], 0, None) if any required input is missing.
        Always places exactly `extras = total_trays % num_shelters` upgrades
        (no over/under-allocation) — bee gallons are contracted, so the count
        must match the math regardless of which distribution strategy is used.

        Strategies (read from current_field['tray_distribution']):
          - "even":        2-D golden-ratio dither across rows (default)
          - "outside":     shelters closest to the field boundary get 2 first
          - "alternating": whole bays alternate; chosen bays are all-2,
                           others all-1, with at most one partial bay to make
                           the count match exactly
        """
        try:
            gpa = float((self.fv.get("gals_per_acre") or tk.StringVar()).get())
            acres = float((self.fv.get("acres") or tk.StringVar()).get())
            gpt = float((self.fv.get("gals_per_tray") or tk.StringVar()).get())
        except (ValueError, AttributeError):
            return None, [], 0, None
        if gpa <= 0 or acres <= 0 or gpt <= 0 or num_shelters <= 0:
            return None, [], 0, None
        total_gals = gpa * acres
        math_trays = int(math.ceil(total_gals / gpt))
        total_trays = max(math_trays, num_shelters)
        short = max(0, num_shelters - math_trays)
        base = total_trays // num_shelters
        extras = total_trays % num_shelters

        per = [base] * num_shelters
        if extras == 0:
            return total_trays, per, short, total_gals

        strategy = self._current_tray_strategy()

        # ──── Strategy: outside edge first ────────────────────────────────
        if strategy == "outside" and shelter_positions_latlon:
            per = self._distribute_outside(num_shelters, shelter_positions_latlon,
                                            extras, base)
            return total_trays, per, short, total_gals

        # ──── Strategy: alternating bays ──────────────────────────────────
        if strategy == "alternating" and row_indices is not None \
                and len(row_indices) == num_shelters:
            per = self._distribute_alternating(num_shelters, row_indices, extras, base)
            return total_trays, per, short, total_gals

        # ──── Strategy: even spread (default + fallback) ──────────────────
        if row_indices is None or len(row_indices) != num_shelters:
            for i in range(num_shelters):
                add = ((i + 1) * extras // num_shelters) - (i * extras // num_shelters)
                per[i] = base + add
            return total_trays, per, short, total_gals

        from collections import defaultdict
        rows = defaultdict(list)
        for i, r in enumerate(row_indices):
            rows[r].append(i)
        sorted_keys = sorted(rows.keys())
        PHI_INV = 0.6180339887498949
        cum_shelters = 0; cum_target = 0
        for k, rk in enumerate(sorted_keys):
            row_idxs = rows[rk]; n_r = len(row_idxs)
            cum_shelters += n_r
            new_cum_target = (cum_shelters * extras) // num_shelters
            e_r = new_cum_target - cum_target
            cum_target = new_cum_target
            if e_r <= 0: continue
            if e_r >= n_r:
                for idx in row_idxs: per[idx] = base + 1
                continue
            step = n_r / e_r
            offset = (((k + 1) * PHI_INV) % 1.0) * n_r
            chosen = set()
            for j in range(e_r):
                pos = int(offset + j * step) % n_r
                while pos in chosen:
                    pos = (pos + 1) % n_r
                chosen.add(pos)
            for pos in chosen:
                per[row_idxs[pos]] = base + 1
        return total_trays, per, short, total_gals

    def _distribute_outside(self, num_shelters, positions_latlon, extras, base):
        """Sort shelters by ascending distance to the field boundary;
        the `extras` closest to the boundary get +1 tray. Guarantees that
        no 2-tray shelter is more inward than any 1-tray shelter."""
        per = [base] * num_shelters
        # Compute distance to boundary for each shelter
        f = self.current_field
        try:
            plat = float(f.get("PP_Latitude") or "0")
            plon = float(f.get("PP_Longitude") or "0")
        except ValueError:
            return per
        boundary = f.get("boundary_polygon") or []
        # Convert pivot + boundary + shelters to ENU (pivot-centered metres)
        pe, pn = utmish.from_lonlat(plon, plat, plon)
        bnd_enu = []
        for la, lo in boundary:
            e, n = utmish.from_lonlat(lo, la, plon)
            bnd_enu.append((e - pe, n - pn))
        # If no boundary polygon, use the bounding circle of all shelters
        # as a fallback (rough approximation for circular fields).
        shelters_enu = []
        for lat, lon in positions_latlon:
            e, n = utmish.from_lonlat(lon, lat, plon)
            shelters_enu.append((e - pe, n - pn))
        if bnd_enu:
            def _dist(east, north):
                md2 = float("inf"); nb = len(bnd_enu)
                for i in range(nb):
                    ax, ay = bnd_enu[i]
                    bx, by = bnd_enu[(i + 1) % nb]
                    dx, dy = bx - ax, by - ay
                    seg2 = dx*dx + dy*dy
                    if seg2 > 0:
                        t = max(0.0, min(1.0,
                                          ((east-ax)*dx + (north-ay)*dy) / seg2))
                        px, py = ax + t*dx, ay + t*dy
                    else:
                        px, py = ax, ay
                    d2 = (east-px)**2 + (north-py)**2
                    if d2 < md2: md2 = d2
                return math.sqrt(md2)
            dists = [(_dist(e, n), i) for i, (e, n) in enumerate(shelters_enu)]
        else:
            # Circular fallback: use radius - dist_to_pivot
            r_max_sq = max((e*e + n*n) for e, n in shelters_enu) if shelters_enu else 0.0
            r_max = math.sqrt(r_max_sq)
            dists = [(r_max - math.sqrt(e*e + n*n), i)
                     for i, (e, n) in enumerate(shelters_enu)]
        dists.sort()  # ascending: closest to boundary first
        for d, i in dists[:extras]:
            per[i] = base + 1
        return per

    def _distribute_alternating(self, num_shelters, row_indices, extras, base):
        """Pick whole bays to be all-2-tray, evenly spaced through the bay
        list. If the chosen bays' total size doesn't match `extras` exactly,
        one bay is left "partial" (some shelters demoted to 1-tray, or some
        promoted from an adjacent bay). Always places exactly `extras`
        upgrades."""
        per = [base] * num_shelters
        if extras == 0:
            return per
        from collections import defaultdict
        rows = defaultdict(list)
        for i, r in enumerate(row_indices):
            rows[r].append(i)
        bay_order = sorted(rows.keys())
        num_bays = len(bay_order)
        if num_bays == 0:
            return per

        # Number of "all-2" bays we'd like — proportional to the ratio.
        n_all2 = max(1, round(num_bays * extras / num_shelters))
        n_all2 = min(n_all2, num_bays)

        # Evenly-spaced bay positions (centred so we don't always start at 0).
        positions = sorted({int((i + 0.5) * num_bays / n_all2)
                            for i in range(n_all2)})

        # Mark those bays' shelters as +1.
        placed = 0
        for p in positions:
            bay = rows[bay_order[p]]
            for idx in bay:
                per[idx] = base + 1
            placed += len(bay)

        chosen_set = set(positions)
        if placed > extras:
            # Demote some shelters in the LAST chosen bay back to 1-tray.
            excess = placed - extras
            last_bay = rows[bay_order[positions[-1]]]
            for idx in last_bay[-excess:]:
                per[idx] = base
        elif placed < extras:
            # Promote shelters in nearby non-chosen bays until we hit extras.
            deficit = extras - placed
            for p in range(num_bays):
                if p in chosen_set: continue
                bay = rows[bay_order[p]]
                take = min(deficit, len(bay))
                for idx in bay[:take]:
                    per[idx] = base + 1
                deficit -= take
                if deficit == 0: break
        return per

    def _on_tray_dist_change(self, _=None):
        """Persist the strategy on the field and trigger a recompute."""
        label = self.tray_dist_var.get()
        key = self._tray_dist_labels.get(label, "even")
        self.current_field["tray_distribution"] = key
        self._refresh_bee_summary()
        if self.show_shelters.get():
            self._redraw_shelters()

    def _current_tray_strategy(self):
        return self.current_field.get("tray_distribution") or "even"

    # ── Shelter count mode (per acre / total / spacing / trays / acres per) ───
    def _shelter_hint(self, mode):
        if mode=="per_acre":          return "Shelters per acre × Acres = exact count placed."
        if mode=="acres_per_shelter": return "e.g. 2 = one shelter per 2 acres (Acres ÷ value)."
        if mode=="spacing":           return "Distance between shelters. Fills the field at that spacing."
        if mode=="trays_1":           return "Auto: one tray per shelter. Count = total trays needed."
        if mode=="trays_2":           return "Auto: two trays per shelter. Count = total trays ÷ 2."
        return "Exact number of shelters to place (e.g. 135)."

    def _auto_shelter_count(self, mode):
        """Compute the shelter count for the auto modes (trays_1 / trays_2)
        from the bee allocation inputs. Returns int or None if inputs are
        incomplete. acres_per_shelter is NOT auto — it has its own entry."""
        try:
            gpa = float(self.fv.get("gals_per_acre", tk.StringVar()).get())
            gpt = float(self.fv.get("gals_per_tray", tk.StringVar()).get())
            ac  = float(self.fv.get("acres", tk.StringVar()).get())
        except (ValueError, AttributeError):
            return None
        if gpa <= 0 or gpt <= 0 or ac <= 0: return None
        total_trays = math.ceil(gpa * ac / gpt)
        divisor = 2 if mode == "trays_2" else 1
        return max(1, math.ceil(total_trays / divisor))

    def _refresh_shelter_value_display(self):
        """For the auto modes, push the computed count into the read-only
        entry so the user sees how many shelters they're getting. No-op for
        the editable modes."""
        mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        if mode not in ("trays_1","trays_2"): return
        n = self._auto_shelter_count(mode)
        self._loading_shelter_value=True
        self.shelter_value_var.set(str(n) if n is not None else "—")
        self._loading_shelter_value=False

    def _clear_shelter_overrides(self):
        """Wipe manually-moved positions and the in-session undo stack.
        Called whenever the shelter grid changes enough that old overrides
        would place pins in wrong locations (mode switch, count change)."""
        if self.current_field.get("shelter_overrides"):
            self.current_field["shelter_overrides"] = {}
        self._shelter_undo.clear()

    def _on_shelter_mode_change(self, _=None):
        mode=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        # Changing the shelter pattern invalidates any manually-moved pins —
        # clear them so the new grid starts clean.
        if mode != self.current_field.get("shelter_mode", ""):
            self._clear_shelter_overrides()
        self.current_field["shelter_mode"]=mode
        self._loading_shelter_value=True
        if mode in ("trays_1","trays_2"):
            # Auto mode: entry shows computed count, disabled so the user
            # doesn't try to edit it. Update display from current bee inputs.
            n = self._auto_shelter_count(mode)
            self.shelter_value_var.set(str(n) if n is not None else "—")
            try: self._shelter_entry.configure(state="disabled")
            except Exception: pass
        else:
            try: self._shelter_entry.configure(state="normal")
            except Exception: pass
            key=self._shelter_mode_key[mode]
            self.shelter_value_var.set(self.fv[key].get())
        self._loading_shelter_value=False
        self.shelter_hint_lbl.configure(text=self._shelter_hint(mode))
        if self.show_shelters.get(): self._redraw_shelters()

    def _on_shelter_value_change(self, *_):
        if getattr(self,"_loading_shelter_value",False): return
        mode=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        if mode in ("trays_1","trays_2"): return  # entry is read-only
        # New shelter count means a different grid — old pin positions are stale.
        self._clear_shelter_overrides()
        key=self._shelter_mode_key[mode]
        self.fv[key].set(self.shelter_value_var.get())   # fv trace → _on_form_change → redraw

    def _refresh_bee_summary(self):
        """Update the three computed lines under the Bee Allocation block.

        Total gallons and total trays come straight from the form values
        (gals/acre × acres, then ÷ gals/tray) so the user sees them update
        the instant they type — no need to draw shelters first. Per-shelter
        and the "math gives fewer than N" warning still need the actual
        shelter count, so those stay '—' until shelters are computed."""
        # Trays-based shelter modes derive their count from these same
        # numbers; refresh the displayed count so it stays in sync as the
        # user edits gpa / gpt / acres.
        try: self._refresh_shelter_value_display()
        except Exception: pass
        # Pull raw form numbers. Missing/invalid → leave as None so the totals
        # collapse to "—" rather than showing a misleading zero.
        def _num(key):
            v = self.fv.get(key)
            if v is None: return None
            try:
                x = float(v.get())
                return x if x > 0 else None
            except (ValueError, AttributeError):
                return None
        gpa = _num("gals_per_acre")
        acres = _num("acres")
        gpt = _num("gals_per_tray")

        # Total gallons line (needs gpa + acres only).
        if gpa is not None and acres is not None:
            total_gals = gpa * acres
            self.bee_total_gals_lbl.configure(text=f"Total gals:   {total_gals:g}")
        else:
            total_gals = None
            self.bee_total_gals_lbl.configure(text="Total gals:   —")

        # Total trays line (needs gpa + acres + gpt). Math-only baseline; if
        # there are more shelters than that, the per-shelter pass bumps each
        # to ≥ 1 and the displayed total goes up.
        n = len(self.shelter_positions or [])
        if total_gals is not None and gpt is not None:
            math_trays = int(math.ceil(total_gals / gpt))
            total_trays = max(math_trays, n) if n > 0 else math_trays
            self.bee_total_trays_lbl.configure(text=f"Total trays:  {total_trays}")
        else:
            math_trays = total_trays = None
            self.bee_total_trays_lbl.configure(text="Total trays:  —")

        # Per-shelter: prefer the placed count; fall back to form/bee-math count
        # so the value shows even before shelters are drawn on the map.
        n_ps = n
        if n_ps <= 0 and total_trays is not None:
            mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(), "total")
            if mode in ("trays_1", "trays_2"):
                n_ps = self._auto_shelter_count(mode) or 0
            elif mode == "total":
                try: n_ps = int(float(self.fv["num_structures"].get() or 0))
                except (ValueError, TypeError): pass
            elif mode == "per_acre":
                try:
                    spa = float(self.fv["shelters_per_acre"].get() or 0)
                    ac  = float(self.fv["acres"].get() or 0)
                    if spa > 0 and ac > 0: n_ps = max(1, int(math.ceil(spa * ac)))
                except (ValueError, TypeError): pass
            elif mode == "acres_per_shelter":
                try:
                    aps = float(self.fv["acres_per_shelter"].get() or 0)
                    ac  = float(self.fv["acres"].get() or 0)
                    if aps > 0 and ac > 0: n_ps = max(1, int(math.ceil(ac / aps)))
                except (ValueError, TypeError): pass

        if n_ps <= 0 or total_trays is None:
            self.bee_per_shelter_lbl.configure(text="Per shelter:  —")
            self.bee_short_lbl.configure(text="")
            return

        total_trays_d, per, short, _tg = self._compute_bee_distribution(n_ps)
        if per:
            lo, hi = min(per), max(per)
            ps_txt = f"{lo} trays" if lo == hi else f"{lo}–{hi} trays"
        else:
            ps_txt = "—"
        self.bee_per_shelter_lbl.configure(text=f"Per shelter:  {ps_txt}")
        if short > 0:
            self.bee_short_lbl.configure(text=f"⚠ {n_ps} shelters but bee math gives only {n_ps-short} trays — bumped up to 1 each")
        else:
            self.bee_short_lbl.configure(text="")

    # ── Company / Year ─────────────────────────────────────────────────────────
    def _all_years_union(self):
        yrs=set()
        for c in list_companies():
            yrs.update(list_years(c))
        return sorted(yrs,reverse=True)

    def _refresh_company_list(self):
        real=list_companies() or ["Default"]
        self.company_cb.configure(values=[ALL_COMPANIES]+real)
        # Startup default: All companies + the current calendar year. Gives
        # the user a "see everything from this year" view without having to
        # pick a specific company first. _on_company_change populates the
        # year list (union across companies) and would otherwise set year
        # to ALL_YEARS — we override that to the current year.
        self.company_var.set(ALL_COMPANIES)
        self._on_company_change(ALL_COMPANIES)
        self.year_var.set(str(datetime.date.today().year))
        self._refresh_field_list()

    def _on_company_change(self,val=None):
        co=self.company_var.get()
        if co==ALL_COMPANIES:
            yrs=self._all_years_union() or [str(datetime.date.today().year)]
            self.year_cb.configure(values=[ALL_YEARS]+yrs)
            self.year_var.set(ALL_YEARS)
            self._refresh_field_list(); return
        yrs=list_years(co) or [str(datetime.date.today().year)]
        self.year_cb.configure(values=[ALL_YEARS]+yrs)
        self.year_var.set(yrs[0]); self._refresh_field_list()

    def _on_year_change(self,val=None): self._refresh_field_list()

    def _new_company(self):
        n=self._ask_string("New Company","Company name:")
        if not n: return
        n = n.strip()
        bad = invalid_field_name_chars(n)
        if bad:
            tkinter.messagebox.showerror("Invalid company name",
                f"\"{' '.join(bad)}\" not allowed. JD Operations Center rejects # and /,\n"
                f"and these characters also break Windows folders:\n    {FIELD_NAME_BAD_CHARS_HUMAN}")
            return
        (DATA_DIR/n).mkdir(parents=True,exist_ok=True); self._refresh_company_list(); self.company_var.set(n); self._on_company_change()

    def _new_year(self):
        y=self._ask_string("New Year",f"Year (e.g. {datetime.date.today().year}):")
        if not y: return
        y = y.strip()
        bad = invalid_field_name_chars(y)
        if bad:
            tkinter.messagebox.showerror("Invalid year",
                f"\"{' '.join(bad)}\" not allowed in year folder name.\n"
                f"Use just the digits — e.g. {datetime.date.today().year}.")
            return
        (DATA_DIR/self.company_var.get()/y).mkdir(parents=True,exist_ok=True); self._on_company_change(); self.year_var.set(y); self._refresh_field_list()

    # ── Field list ─────────────────────────────────────────────────────────────
    def _refresh_field_list(self):
        for iid in self.field_tree.get_children():
            self.field_tree.delete(iid)
        self._field_rows={}
        # _export_scope expands the All-companies / All-years sentinels, so this
        # lists everything when All/All is selected and just the matching
        # company+year otherwise.
        for co,yr,name in self._export_scope():
            iid=self.field_tree.insert("","end",values=(name,co,yr))
            self._field_rows[iid]=(co,yr,name)
        if self._field_sort_col:
            self._apply_field_sort()
        self._redraw_overview_boundaries()

    def _sort_fields(self,col):
        if self._field_sort_col==col:
            self._field_sort_rev=not self._field_sort_rev
        else:
            self._field_sort_col=col; self._field_sort_rev=False
        self._apply_field_sort()

    def _apply_field_sort(self):
        col=self._field_sort_col
        rows=[(self.field_tree.set(iid,col).lower(),iid) for iid in self.field_tree.get_children()]
        rows.sort(reverse=self._field_sort_rev)
        for i,(_,iid) in enumerate(rows):
            self.field_tree.move(iid,"",i)

    def _on_field_click(self, event):
        """Deselect the active field when the user clicks it again.

        We compare the clicked row against the *active* field (current_field)
        rather than the Treeview's selection(): this ButtonPress widget binding
        runs before ttk's own class binding updates the selection, so
        selection() is stale here and a single click on the selected row would
        otherwise be missed (the old behaviour needed a double-click)."""
        iid = self.field_tree.identify_row(event.y)
        if not iid:
            return
        # If this is the 2nd press of a double-click on the same row (rename),
        # skip the click-to-deselect so the inline editor can open cleanly.
        now = getattr(event, "time", 0)
        last_t, last_iid = getattr(self, "_last_field_press", (0, None))
        self._last_field_press = (now, iid)
        if iid == last_iid and last_t and (now - last_t) < 400:
            return
        row = self._field_rows.get(iid)
        if not row:
            return
        co, yr, name = row
        cur = self.current_field or {}
        if (str(cur.get("company")) == str(co) and
                str(cur.get("year")) == str(yr) and
                str(cur.get("Name")) == str(name)):
            self._deactivate_field()
            return "break"   # prevent Treeview re-selecting the row

    def _on_field_select(self,_=None):
        sel=self.field_tree.selection()
        if not sel: return
        row=self._field_rows.get(sel[0])
        if not row: return
        self._activate_field(*row)

    def _deactivate_field(self):
        """Clear the active field and return to the overview (all dim outlines)."""
        self.field_tree.selection_set([])
        co=self.company_var.get(); yr=self.year_var.get()
        self.current_field=blank_field(
            "" if co==ALL_COMPANIES else co,
            "" if yr==ALL_YEARS else yr)
        self._set_menu_checkboxes_visible(False)
        self._clear_all_overlays()
        self._form_from_field()
        self._redraw_overview_boundaries()

    def _activate_field(self,co,yr,name):
        """Load a field and make it the active one. Called from the field list
        (double-click), from a map boundary click, and from git-pull auto-reload."""
        if getattr(self, '_activating_field', False):
            return
        self._activating_field = True
        try:
            self._activate_field_impl(co,yr,name)
        finally:
            # Defer clearing the guard so the <<TreeviewSelect>> event that
            # selection_set() queues is still blocked when it fires.  Clearing
            # immediately caused an infinite loop: each activation queued a new
            # event that started the next one as soon as the flag was reset.
            self.after(0, lambda: setattr(self, '_activating_field', False))

    def _activate_field_impl(self,co,yr,name):
        f=load_field(co,yr,name)
        if not f: return
        self.current_field=f
        # Highlight in the list (needed when triggered from a map click)
        for iid,row in self._field_rows.items():
            if row==(co,yr,name):
                self.field_tree.selection_set(iid)
                self.field_tree.see(iid)
                break
        # Load shows only the boundary — everything else stays off until the
        # user opts in via the toolbar menus.
        self.show_pivot.set(False)
        self.show_tracks.set(False)
        self.show_passes.set(False)
        self.show_bays.set(False)
        self.show_shelters.set(False)
        self.shelter_circle_var.set(False)
        self.show_corner_arms.set(False)
        self.show_planter_passes.set(False)
        self.show_sprayer_passes.set(False)
        self.show_pass_buffer_overlay.set(False)
        # Toolbar checkbox states — boundary on by default, everything else off
        self.show_boundary.set(True)
        self.pivot_visible_var.set(False)
        self.boundary_visible_var.set(True)
        self.sprayer_visible_var.set(False)
        self.planter_visible_var.set(False)
        self.shelters_visible_var.set(False)
        self._form_from_field()
        self._redraw_all()
        self._zoom_to_field()
        self._set_menu_checkboxes_visible(True)
        # Remove this field's dim overlay (it now has the bright active boundary)
        # and restore the previously-active field's dim overlay — handled by a
        # full redraw of overview boundaries in the background.
        self._redraw_overview_boundaries()

    def _redraw_overview_boundaries(self):
        """Draw dim outlines for every filtered field except the active one.
        Runs the JSON loading in a background thread; generation counter ensures
        a stale load doesn't overwrite a newer one."""
        # Clear existing dim overlays immediately
        for poly in list(self._overview_polys.values()):
            try: poly.delete()
            except Exception: pass
        self._overview_polys.clear()
        self._overview_field_bps.clear()

        scope=self._export_scope()
        active_co=str(self.current_field.get("company",""))
        active_yr=str(self.current_field.get("year",""))
        active_name=str(self.current_field.get("Name",""))
        self._overview_gen+=1
        gen=self._overview_gen

        def _load():
            results=[]
            for co,yr,name in scope:
                try:
                    f=load_field(co,yr,name)
                    bp=(f or {}).get("boundary_polygon") or []
                    if len(bp)>=3:
                        results.append((str(co),str(yr),str(name),bp))
                except Exception:
                    pass
            self.after(0,lambda: _apply(results))

        def _apply(results):
            if self._overview_gen!=gen: return  # superseded
            for co,yr,name,bp in results:
                key=(co,yr,name)
                self._overview_field_bps[key]=bp
                if co==active_co and yr==active_yr and name==active_name:
                    continue  # active field — bright boundary drawn by _redraw_boundary
                try:
                    poly=self.map_widget.set_polygon(
                        [tuple(p) for p in bp],
                        fill_color=None,outline_color="#FFA500",border_width=2)
                    self._overview_polys[key]=poly
                except Exception:
                    pass

        threading.Thread(target=_load,daemon=True).start()

    def _zoom_to_field(self):
        """Zoom the map so the field's outer boundary just fits in the frame.
        Falls back to the pivot point when no boundary is set."""
        try:
            bp = self.current_field.get("boundary_polygon") or []
            if bp and len(bp) >= 3:
                lats=[p[0] for p in bp]; lons=[p[1] for p in bp]
                cy=(max(lats)+min(lats))/2.0; cx=(max(lons)+min(lons))/2.0
                w=self.map_widget.winfo_width()  or 700
                h=self.map_widget.winfo_height() or 600
                R=6378137.0
                lat_span_m=(max(lats)-min(lats))*math.pi/180.0*R
                lon_span_m=(max(lons)-min(lons))*math.pi/180.0*R*math.cos(math.radians(cy))
                span_m=max(lat_span_m, lon_span_m, 1.0)*1.10   # 10% padding
                px=min(max(w,200), max(h,200))
                # Web-mercator ground resolution: 156543.03 * cos(lat) / 2^z m/px
                z=math.log2(156543.03*max(0.05, math.cos(math.radians(cy)))*px/span_m)
                z=max(1, min(20, int(z)))
                self.map_widget.set_position(cy, cx)
                self.map_widget.set_zoom(z)
            else:
                plat=float(self.current_field.get("PP_Latitude") or 0)
                plon=float(self.current_field.get("PP_Longitude") or 0)
                if plat and plon:
                    self.map_widget.set_position(plat, plon)
                    self.map_widget.set_zoom(14)
        except Exception:
            pass

    def _new_field(self):
        # Company / year default to the dropdown selection when it's a real
        # value; otherwise empty (the user types Company in Field Details
        # and the year defaults to current on save).
        co = self.company_var.get()
        if not co or co == ALL_COMPANIES: co = ""
        yr = self.year_var.get()
        if not yr or yr == ALL_YEARS: yr = str(datetime.date.today().year)
        self.current_field = blank_field(co, yr)
        self._form_from_field(); self._clear_all_overlays(); self._status("")

    def _delete_field(self):
        sel=self.field_tree.selection()
        if not sel: return
        row=self._field_rows.get(sel[0])
        if not row: return
        co,yr,name=row
        if tkinter.messagebox.askyesno("Delete",f"Delete '{name}' ({co} {yr})?"):
            delete_field_file(co,yr,name); self._refresh_field_list(); self._git_push(f"delete field: {name}")

    # ── Inline field rename (double-click the Field cell) ──────────────────────
    def _on_field_double_click(self, event):
        """Double-click the Field-name cell to edit it inline. Commits on Enter
        or when focus leaves the entry (renames the saved field file)."""
        self._cancel_field_rename()
        if self.field_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.field_tree.identify_column(event.x) != "#1":   # Field column only
            return
        iid = self.field_tree.identify_row(event.y)
        row = self._field_rows.get(iid)
        if not row:
            return
        co, yr, name = row
        bbox = self.field_tree.bbox(iid, "field")
        if not bbox:
            return
        x, y, w, h = bbox
        self._rename_ctx = (co, yr, name)
        ent = tk.Entry(self.field_tree, font=(FONT_BODY, 10),
                       bg=UI_CARD, fg=UI_TEXT, insertbackground=UI_TEXT,
                       relief="solid", borderwidth=1,
                       highlightthickness=1, highlightbackground=UI_ACCENT,
                       highlightcolor=UI_ACCENT)
        ent.insert(0, name)
        ent.select_range(0, "end")
        ent.icursor("end")
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        ent.bind("<Return>",   lambda e: self._commit_field_rename())
        ent.bind("<KP_Enter>", lambda e: self._commit_field_rename())
        ent.bind("<Escape>",   lambda e: self._cancel_field_rename())
        ent.bind("<FocusOut>", lambda e: self._commit_field_rename())
        self._rename_entry = ent
        return "break"

    def _cancel_field_rename(self):
        ent = getattr(self, "_rename_entry", None)
        if ent is not None:
            try: ent.destroy()
            except Exception: pass
        self._rename_entry = None
        self._rename_ctx = None

    def _commit_field_rename(self):
        ent = getattr(self, "_rename_entry", None)
        ctx = getattr(self, "_rename_ctx", None)
        if ent is None or ctx is None:
            self._cancel_field_rename(); return
        new_name = ent.get().strip()
        co, yr, old_name = ctx
        # Tear the entry down first so the FocusOut it triggers can't re-enter.
        self._rename_entry = None; self._rename_ctx = None
        try: ent.destroy()
        except Exception: pass
        if not new_name or new_name == old_name:
            return
        bad = invalid_field_name_chars(new_name)
        if bad:
            tkinter.messagebox.showerror("Invalid field name",
                f"A field name can't contain:  {' '.join(bad)}")
            return
        # Duplicate check (allow a case-only change of the same field).
        if new_name.lower() != old_name.lower() and load_field(co, yr, new_name) is not None:
            tkinter.messagebox.showerror("Name already in use",
                f"A field named \"{new_name}\" already exists in {co} {yr}.")
            return
        # Use the live form state if the renamed field is the active one, so any
        # unsaved edits are kept; otherwise load it from disk.
        cur = self.current_field or {}
        is_active = (str(cur.get("company")) == str(co) and
                     str(cur.get("year")) == str(yr) and
                     str(cur.get("Name")) == str(old_name))
        f = self._field_from_form() if is_active else load_field(co, yr, old_name)
        if not f:
            self._status("Could not load the field to rename."); return
        f["Name"] = new_name
        try:
            base = DATA_DIR / str(co) / str(yr)
            old_p = base / (old_name + ".json")
            new_p = base / (new_name + ".json")
            tmp_p = base / "._rename_tmp_.json"
            # Write updated content, then two-step rename so even a case-only
            # change is reflected on a case-insensitive filesystem (Windows).
            with open(old_p, "w", encoding="utf-8") as fp:
                json.dump(f, fp, indent=2)
            os.replace(str(old_p), str(tmp_p))
            os.replace(str(tmp_p), str(new_p))
        except Exception as ex:
            tkinter.messagebox.showerror("Rename failed", str(ex)); return
        if is_active:
            try: self.fv["Name"].set(new_name)
            except Exception: pass
        self._refresh_field_list()
        for iid, (c, y, n) in self._field_rows.items():
            if c == co and y == yr and n == new_name:
                self.field_tree.selection_set(iid); break
        self._git_push(f"rename field: {old_name} -> {new_name}")
        self._status(f'Renamed "{old_name}" to "{new_name}".')

    # ── Form helpers ───────────────────────────────────────────────────────────
    def _update_map_field_label(self):
        """Show the current field name overlaid at the top-right of the map.
        Hidden when no field is named. Called on load/new and on Name edits."""
        lbl = getattr(self, "map_field_label", None)
        if lbl is None:
            return
        try:
            name = (self.fv["Name"].get() or "").strip()
        except Exception:
            name = ""
        if name:
            lbl.configure(text="  " + name + "  ")
            lbl.place(relx=1.0, rely=0.0, x=-14, y=12, anchor="ne")
            lbl.lift()
        else:
            lbl.place_forget()

    def _form_from_field(self):
        f=self.current_field
        bf=blank_field()
        self._shelter_undo=[]   # undo history is per-field, reset on load/new
        for k,v in self.fv.items():
            val=f.get(k)
            v.set(str(bf.get(k,"")) if val is None else str(val))
        self._update_map_field_label()   # reflect the newly loaded field name
        self.shelters_in_outside_var.set(f.get("shelters_in_outside_pass", "No"))
        self.shelter_at_pivot_var.set("Yes" if f.get("shelter_at_pivot") else "No")
        # Row layout: dropdown + custom mask + use-imported-passes toggle.
        rl = f.get("row_layout","centered")
        self.row_layout_var.set(self._row_layout_inverse.get(rl,"Centered male"))
        self.custom_mask_var.set(str(f.get("custom_row_mask","")))
        # Default OFF when no planter data has been uploaded; respect the
        # saved value (which may be False if the user manually turned it off).
        has_planter = bool(f.get("planter_passes"))
        self.use_imported_passes_var.set(has_planter and bool(f.get("use_imported_passes", True)))
        self._planter_file_var.set(f.get("planter_file_name","") or "")
        self._use_planter_cb.configure(state="normal" if has_planter else "disabled")
        # Pre-existing fields default to bay mode (canola). New non-canola
        # fields will save the unchecked state. Also re-sync the frame
        # visibility so the bay-only widgets show/hide with the load.
        self.use_bays_var.set(bool(f.get("use_bays",True)))
        self._on_use_bays_toggle()
        self._on_row_layout_change()
        # Sync the tray-distribution dropdown
        dist_key = f.get("tray_distribution") or "even"
        self.tray_dist_var.set(self._tray_dist_inverse.get(dist_key, "Spread evenly"))
        # Sync the shelter-count mode dropdown + its single value entry
        s_mode = f.get("shelter_mode") or "total"
        self.shelter_mode_var.set(self._shelter_mode_inverse.get(s_mode,"Total shelters"))
        if s_mode in ("trays_1","trays_2"):
            # Auto modes: compute from acres × gals/acre ÷ gals/tray right now
            # so the count is always visible when the field loads.
            try: self._shelter_entry.configure(state="disabled")
            except Exception: pass
            self._refresh_shelter_value_display()
        else:
            try: self._shelter_entry.configure(state="normal")
            except Exception: pass
            s_key = self._shelter_mode_key.get(s_mode,"num_structures")
            self._loading_shelter_value=True
            self.shelter_value_var.set(self.fv[s_key].get())
            self._loading_shelter_value=False
        self.shelter_hint_lbl.configure(text=self._shelter_hint(s_mode))
        self._refresh_track_list()
        # Migrate old corner_arms [[pts],[pts]] format → new [{type,pts/lat/lon/radius_m}] format
        old = self.current_field.get("corner_arms")
        if isinstance(old, list) and old and not isinstance(old[0], dict):
            migrated = []
            for item in old:
                if isinstance(item, list) and len(item) >= 2:
                    migrated.append({"type": "path", "pts": item})
            self.current_field["corner_arms"] = migrated
        else:
            self.current_field.setdefault("corner_arms", [])
        self.current_field.setdefault("shelter_overrides",{})

    def _field_from_form(self):
        f=self.current_field
        for k,v in self.fv.items(): f[k]=v.get().strip()
        f["shelters_in_outside_pass"] = self.shelters_in_outside_var.get()
        f["shelter_at_pivot"] = (self.shelter_at_pivot_var.get() == "Yes")
        f["tray_distribution"]=self._tray_dist_labels.get(self.tray_dist_var.get(),"even")
        f["shelter_mode"]=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        f["row_layout"]=self._row_layout_labels.get(self.row_layout_var.get(),"centered")
        f["custom_row_mask"]=self.custom_mask_var.get().strip()
        f["use_imported_passes"]=bool(self.use_imported_passes_var.get())
        f["use_bays"]=bool(self.use_bays_var.get())
        # Company: prefer the form's Company entry (user can type anything,
        # including a brand-new name that will be created on save). Fall back
        # to the dropdown when the form field is blank.
        form_co = (self.fv.get("company") and self.fv["company"].get().strip()) or ""
        if form_co:
            f["company"] = form_co
        else:
            dd_co = self.company_var.get()
            if dd_co and dd_co != ALL_COMPANIES:
                f["company"] = dd_co
        # Year still comes from the top dropdown (current year by default).
        yr=self.year_var.get()
        if yr and yr!=ALL_YEARS: f["year"]=yr
        return f

    def _refresh_track_list(self):
        self.track_lb.delete(0,tk.END)
        for r in (self.current_field.get("pivot_tracks") or []):
            self.track_lb.insert(tk.END,"%.1f m  (%.1f ft)"%(r,r/0.3048))



    # ── Map ────────────────────────────────────────────────────────────────────
    def _lld_sanitize(self, idx):
        """Filter/uppercase/truncate one LLD cell. Returns (text, maxlen)."""
        name,maxlen,kind,_ = self._lld_specs[idx]
        var=self._lld_vars[idx]
        s=var.get().upper()
        if kind=="digit":
            s=re.sub(r"[^0-9]","",s)
        elif kind=="alpha":
            s=re.sub(r"[^NSEW]","",s)
        elif kind=="mer":
            s=re.sub(r"[^W0-9]","",s)
        s=s[:maxlen]
        if s!=var.get():
            var.set(s)
        return s,maxlen

    def _on_lld_key(self, idx, event):
        """Per-cell key handler: sanitize input and auto-advance to the next
        cell once the current one is full. Backspace on an empty cell hops back."""
        ks=event.keysym
        if ks=="Return":
            self._search_lld(); return
        if ks in ("Tab","ISO_Left_Tab","Left","Right","Up","Down",
                  "Shift_L","Shift_R","Control_L","Control_R"):
            return
        if ks=="BackSpace":
            if not self._lld_vars[idx].get() and idx>0:
                prev=self._lld_entries[idx-1]
                prev.focus_set()
                try: prev.icursor("end")
                except Exception: pass
            else:
                self._lld_sanitize(idx)
            return
        s,maxlen=self._lld_sanitize(idx)
        if len(s)>=maxlen and idx<len(self._lld_entries)-1:
            nxt=self._lld_entries[idx+1]
            nxt.focus_set()
            try:
                # Don't blow away a prefilled cell (e.g. the W4 meridian) — just
                # park the cursor at the end. Select empty cells for fast typing.
                if self._lld_vars[idx+1].get():
                    nxt.icursor("end")
                else:
                    nxt.select_range(0,"end")
            except Exception: pass

    def _lld_query(self):
        """Assemble the five cells into a dash-joined LLD string for geocoding.
        Blank leading cells (e.g. no quarter) are dropped so the section-level
        formats still parse. Meridian falls back to W4 when blank."""
        qtr=self._lld_vars[0].get().strip().upper()
        sec=self._lld_vars[1].get().strip()
        twp=self._lld_vars[2].get().strip()
        rng=self._lld_vars[3].get().strip()
        mer=self._lld_vars[4].get().strip().upper() or "W4"
        if not mer.startswith("W"):
            mer="W"+re.sub(r"\D","",mer)
        parts=[p for p in (qtr,sec,twp,rng,mer) if p]
        return "-".join(parts)

    def _search_lld(self):
        res=geocode_lld(self._lld_query())
        if res is None: self._status("❌ Could not parse LLD"); return
        lat,lon,label,corners=res
        self.map_widget.set_position(lat,lon)
        zoom=15 if label[:2] in ("NE","NW","SE","SW") or label[1:2]=="½" else 13 if label.startswith("Sec") else 11
        self.map_widget.set_zoom(zoom)
        # Stash result so the user can toggle the highlight off and back on
        # without re-searching. Only draws now if the toggle is on.
        self.lld_corners = corners
        self.lld_label = label
        self._render_lld_box()
        self._status(f"→ {label}")

    def _render_lld_box(self):
        """(Re)draw the LLD highlight rectangle iff `show_lld_box` is on and
        we have cached corners from a previous search."""
        if self.lld_boundary_poly:
            try: self.lld_boundary_poly.delete()
            except Exception: pass
            self.lld_boundary_poly = None
        if not self.show_lld_box.get(): return
        if not self.lld_corners: return
        try:
            self.lld_boundary_poly = self.map_widget.set_polygon(
                self.lld_corners, fill_color=None, outline_color="#FFFF88",
                border_width=2)
        except Exception:
            pass

    def _toggle_lld_box(self):
        """Switch handler — show or hide the cached LLD highlight."""
        self._render_lld_box()
        if self.show_lld_box.get():
            if self.lld_corners:
                self._status(f"LLD box on ({self.lld_label})." if self.lld_label
                             else "LLD box on.")
        else:
            self._status("LLD box hidden.")

    def _mode_pivot(self):
        self._close_all_popups()
        self.click_mode="pivot"; self._status("Click pivot point on map…")

    def _mode_boundary(self):
        self._close_all_popups()
        self.click_mode="boundary"; self.boundary_pts=[]; self._clear_boundary_overlays()
        self._show_context_btn("✔ Save Boundary", self._close_boundary)
        self._status("Click map to add boundary vertices. ✔ Save when done.")

    def _mode_boundary_circle(self):
        """Draw a circular outer boundary centred on the pivot point — same feel
        as placing a pivot track. The click radius is sampled into a vertex
        every 100 ft so the user can later drag/add/delete points (Edit)
        to fit fields that aren't a perfect circle."""
        self._close_all_popups()
        if not self.fv["PP_Latitude"].get() or not self.fv["PP_Longitude"].get():
            self._status("Set the pivot point first."); return
        self.click_mode="boundary_circle"
        self._show_context_btn("✔ Done", self._close_boundary_circle)
        self._status("Click the map to set the circle radius from the pivot point "
                     "(a boundary point every 100 ft). Click again to resize. "
                     "✔ Done when finished.")

    def _close_boundary_circle(self):
        self.click_mode=None
        self._hide_context_btn()
        bp=self.current_field.get("boundary_polygon") or []
        if bp:
            self._status(f"Circular boundary saved ({len(bp)} points). "
                         "Use Boundary → Edit Outer to drag, add, or delete points.")
        else:
            self._status("No circle drawn.")

    # ── Inner boundary (interior exclusion) ──────────────────────────────────
    def _mode_add_inner_boundary(self):
        """Click-to-add-vertices for a new inner exclusion polygon.
        Vertices use the same per-click marker pattern as the outer; on
        ✔ Save, the new ring is appended to current_field['boundary_inner']."""
        self._close_all_popups()
        bp = self.current_field.get("boundary_polygon")
        if not bp or len(bp) < 3:
            self._status("Draw the outer boundary first."); return
        self.click_mode = "inner_boundary"
        self.inner_pts = []
        # Re-use boundary_markers list so existing _on_map_click handlers and
        # _clear_boundary_markers continue to work for the in-progress draw.
        for m in self.boundary_markers:
            try: m.delete()
            except Exception: pass
        self.boundary_markers = []
        self._show_context_btn("✔ Save Inner Boundary", self._close_inner_boundary)
        self._status("Click map to draw an inner exclusion. ✔ Save when done.")

    def _close_inner_boundary(self):
        if len(getattr(self, "inner_pts", [])) < 3:
            self._status("Need ≥ 3 points for an inner boundary."); return
        inner = [list(p) for p in self.inner_pts]
        self.current_field.setdefault("boundary_inner", []).append(inner)
        self.inner_pts = []
        self.click_mode = None
        self._hide_context_btn()
        # Wipe the in-progress markers, then redraw the boundary block.
        for m in self.boundary_markers:
            try: m.delete()
            except Exception: pass
        self.boundary_markers = []
        self._redraw_boundary()
        if self.show_shelters.get(): self._redraw_shelters()
        if self.show_passes.get():   self._redraw_passes()
        if self.show_bays.get():     self._redraw_bays()
        n = len(self.current_field["boundary_inner"])
        self._status(f"Inner boundary #{n} saved ({len(inner)} vertices).")

    def _mode_delete_inner_boundary(self):
        """Pick an existing inner boundary to remove."""
        self._close_all_popups()
        inners = self.current_field.get("boundary_inner") or []
        if not inners:
            self._status("No inner boundaries to delete."); return
        win = ctk.CTkToplevel(self)
        win.title("Delete Inner Boundary")
        win.geometry("320x240"); win.grab_set()
        ctk.CTkLabel(win, text="Select inner boundary to delete:").pack(pady=(12,4))
        lb = tk.Listbox(win, bg=UI_CARD, fg=UI_TEXT,
                        selectbackground=UI_SELECT, selectforeground=UI_TEXT,
                        relief="flat", font=(FONT_BODY, 11), height=6,
                        activestyle="none", highlightthickness=1,
                        highlightbackground=UI_BORDER)
        for i, ring in enumerate(inners):
            lb.insert(tk.END, f"Inner #{i+1}: {len(ring)} pts")
        lb.pack(fill="x", padx=10, pady=4)
        def do_delete():
            sel = lb.curselection()
            if not sel: return
            del self.current_field["boundary_inner"][sel[0]]
            self._redraw_boundary()
            if self.show_shelters.get(): self._redraw_shelters()
            if self.show_passes.get():   self._redraw_passes()
            if self.show_bays.get():     self._redraw_bays()
            win.destroy()
            self._status("Inner boundary deleted.")
        ctk.CTkButton(win, text="Delete Selected", fg_color="#6b1a1a",
                      command=do_delete).pack(pady=(4,2))
        ctk.CTkButton(win, text="Cancel", command=win.destroy).pack()

    def _mode_edit_boundary(self):
        self._close_all_popups()
        bp=self.current_field.get("boundary_polygon")
        if not bp or len(bp)<3:
            self._status("Draw a boundary first."); return
        self.boundary_pts=[tuple(p) for p in bp]
        self._clear_boundary_overlays()
        self._unregister_drag_prefix("bnd_")
        self.boundary_markers=[]
        for i,(lat,lon) in enumerate(self.boundary_pts):
            m=self.map_widget.set_marker(lat,lon,text=str(i+1),
                                          marker_color_circle="#FFD700",marker_color_outside="#B8860B",
                                          command=self._make_bnd_vertex_cb(i))
            self.boundary_markers.append(m)
            # Pass marker so _b1_motion can drag the pin's canvas items
            # live with the cursor (rather than only snapping on release).
            self._register_drag(f"bnd_{i}",lat,lon,str(i+1),"#FFD700","#B8860B",
                                lambda la,lo,i=i: self._on_bnd_vertex_drag(i,la,lo),
                                marker=m)
        self._update_bnd_preview()
        self.click_mode="boundary_edit"
        self._selected_bnd_vertex=None
        self._show_context_btn("✔ Save Boundary", self._close_boundary)
        self._status("Click a vertex to select (drag to move, 🗑 Delete to remove). "
                     "Click the map to add a vertex (joins the nearest two). Esc to deselect. ✔ Save when finished.")

    def _make_bnd_vertex_cb(self,idx):
        def cb(marker):
            if self._just_dragged:
                self._just_dragged=False; return
            self._select_bnd_vertex(idx)
        return cb

    def _select_bnd_vertex(self,idx):
        if self._selected_bnd_vertex==idx:
            self._deselect_bnd_vertex(); return
        if self._selected_bnd_vertex is not None:
            self._redraw_bnd_vertex(self._selected_bnd_vertex,selected=False)
        self._selected_bnd_vertex=idx
        self._redraw_bnd_vertex(idx,selected=True)
        self._show_context_btn("🗑 Delete Vertex",self._delete_selected_bnd_vertex)
        self._status(f"Vertex {idx+1} selected — drag to move, click 🗑 Delete or press Del to remove.")

    def _deselect_bnd_vertex(self):
        if self._selected_bnd_vertex is not None:
            self._redraw_bnd_vertex(self._selected_bnd_vertex,selected=False)
        self._selected_bnd_vertex=None
        self._show_context_btn("✔ Save Boundary",self._close_boundary)
        self._status("Click a vertex to select (drag to move, 🗑 Delete to remove). "
                     "Click the map to add a vertex (joins the nearest two). ✔ Save when finished.")

    def _redraw_bnd_vertex(self,idx,selected=False):
        if idx>=len(self.boundary_pts) or idx>=len(self.boundary_markers): return
        lat,lon=self.boundary_pts[idx]
        try: self.boundary_markers[idx].delete()
        except Exception: pass
        cc="#FF6600" if selected else "#FFD700"
        oc="#CC3300" if selected else "#B8860B"
        m=self.map_widget.set_marker(lat,lon,text=str(idx+1),
                                      marker_color_circle=cc,marker_color_outside=oc,
                                      command=self._make_bnd_vertex_cb(idx))
        self.boundary_markers[idx]=m
        self._register_drag(f"bnd_{idx}",lat,lon,str(idx+1),cc,oc,
                            lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo),
                            marker=m)

    def _delete_selected_bnd_vertex(self):
        idx=self._selected_bnd_vertex
        if idx is None: return
        if len(self.boundary_pts)<=3:
            self._status("Cannot delete — boundary needs at least 3 vertices."); return
        self.boundary_pts.pop(idx)
        self.current_field["boundary_polygon"]=[list(p) for p in self.boundary_pts]
        self._selected_bnd_vertex=None
        self._mode_edit_boundary()
        self._status(f"Vertex deleted. {len(self.boundary_pts)} vertices remain.")

    def _try_insert_bnd_vertex(self, lat, lon):
        """In boundary-edit mode, clicking anywhere drops a new vertex and wires
        it into the boundary between the two nearest adjacent pins — i.e. the
        edge whose two endpoints are closest to the click (least added
        perimeter). Lets the user pull the outline out to a brand-new point, not
        just nudge an existing edge. Returns True (always inserts)."""
        pts=self.boundary_pts
        if len(pts)<2: return False
        n=len(pts); best_edge=0; best_cost=float('inf')
        for i in range(n):
            a=pts[i]; b=pts[(i+1)%n]
            # Extra perimeter from routing through the new point between a and b.
            cost=(haversine_m(lat,lon,a[0],a[1])
                  + haversine_m(lat,lon,b[0],b[1])
                  - haversine_m(a[0],a[1],b[0],b[1]))
            if cost<best_cost:
                best_cost=cost; best_edge=i
        ins=best_edge+1
        pts.insert(ins,(lat,lon))
        self.current_field["boundary_polygon"]=[list(p) for p in pts]
        self._selected_bnd_vertex=None
        self._mode_edit_boundary()      # rebuild markers + numbering
        self._select_bnd_vertex(ins)    # highlight the new vertex for dragging
        self._status(f"Vertex added ({len(pts)} total) — drag to position, "
                     "🗑 Delete to remove. ✔ Save when done.")
        return True

    def _on_delete_key(self,event):
        if self._selected_bnd_vertex is not None:
            self._delete_selected_bnd_vertex()

    def _on_arrow_key(self,event):
        if self._selected_bnd_vertex is None: return
        idx=self._selected_bnd_vertex
        if idx>=len(self.boundary_pts): return
        lat,lon=self.boundary_pts[idx]
        step_m=5.0 if "Shift" in event.keysym else 0.5
        dlat=step_m/111111.0
        dlon=step_m/(111111.0*math.cos(math.radians(lat)))
        if   event.keysym in ("Up",    "Shift_Up"):    lat+=dlat
        elif event.keysym in ("Down",  "Shift_Down"):  lat-=dlat
        elif event.keysym in ("Right", "Shift_Right"): lon+=dlon
        elif event.keysym in ("Left",  "Shift_Left"):  lon-=dlon
        else:
            k=event.keysym.replace("Shift_","")
            if   k=="Up":    lat+=dlat
            elif k=="Down":  lat-=dlat
            elif k=="Right": lon+=dlon
            elif k=="Left":  lon-=dlon
        self.boundary_pts[idx]=(lat,lon)
        self._redraw_bnd_vertex(idx,selected=True)
        self._update_bnd_preview()
        return "break"  # prevent arrow keys from scrolling the sidebar

    def _on_escape(self,event=None):
        if self._selected_bnd_vertex is not None:
            self._deselect_bnd_vertex()
        else:
            self._close_all_popups()

    def _upload_boundary(self):
        self._close_all_popups()
        path=tkinter.filedialog.askopenfilename(
            title="Open boundary file",
            filetypes=[("Boundary files","*.shp *.kml *.kmz"),
                       ("Shapefiles","*.shp"),("KML","*.kml"),("KMZ","*.kmz"),("All","*.*")])
        if not path: return
        try:
            ext=Path(path).suffix.lower()
            # Return list of polygons (each = list of (lat, lon)). JD exports
            # commonly bundle an outer field boundary with several "interior
            # boundary" cutouts (buildings, sloughs, pivot pads) in the same
            # file — we now split them instead of concatenating.
            polys=[]
            if ext==".shp":
                import shapefile as sf_mod
                r=sf_mod.Reader(path)
                for s in r.shapes():
                    pts=[(lat,lon) for lon,lat in s.points]
                    # A shape with `parts` can hold multiple polygon rings.
                    parts=list(getattr(s, "parts", []) or [])
                    if len(parts) > 1:
                        for k, start in enumerate(parts):
                            end = parts[k+1] if k+1 < len(parts) else len(s.points)
                            ring = [(lat, lon) for lon, lat in s.points[start:end]]
                            if len(ring) >= 3: polys.append(ring)
                    elif len(pts) >= 3:
                        polys.append(pts)
            elif ext==".kml":
                polys=self._parse_kml_polygons(path)
            elif ext==".kmz":
                with zipfile.ZipFile(path) as zf:
                    kml_name=next(n for n in zf.namelist() if n.endswith(".kml"))
                    kml_text=zf.read(kml_name).decode("utf-8")
                polys=self._parse_kml_polygons_text(kml_text)
            polys=[p for p in polys if len(p) >= 3]
            if not polys:
                tkinter.messagebox.showerror("Upload Error","No polygons with ≥ 3 points found."); return
            # Largest polygon by approximate area = outer boundary; rest = inner
            # exclusions. (Area in lat/lon-degree² is fine for ordering.)
            def _abs_area(ring):
                s = 0.0
                n = len(ring)
                for i in range(n):
                    x1, y1 = ring[i][1], ring[i][0]
                    x2, y2 = ring[(i+1) % n][1], ring[(i+1) % n][0]
                    s += x1 * y2 - x2 * y1
                return abs(s) * 0.5
            polys.sort(key=_abs_area, reverse=True)
            # Clean tracing artifacts (near-duplicate points + short doubling-
            # back spikes) from every imported ring.
            outer = despike_ring(polys[0])
            inners = [despike_ring(r) for r in polys[1:]]
            self.current_field["boundary_polygon"] = [list(p) for p in outer]
            self.current_field["boundary_inner"] = [[list(pt) for pt in inner] for inner in inners]
            self.boundary_pts = outer
            self._redraw_boundary()
            if inners:
                self._status(f"Loaded: 1 outer ({len(outer)} pts) + {len(inners)} inner boundary"
                             f"{'ies' if len(inners) != 1 else 'y'} from {Path(path).name}")
            else:
                self._status(f"Boundary loaded: {len(outer)} vertices from {Path(path).name}")
        except Exception as ex:
            tkinter.messagebox.showerror("Upload Error",str(ex))

    def _parse_kml_coords_text(self,text):
        root=ET.fromstring(text)
        ns_match=re.match(r'\{[^}]+\}',root.tag)
        ns=ns_match.group(0) if ns_match else ""
        for elem in root.iter(f"{ns}coordinates"):
            raw=elem.text.strip()
            pts=[]
            for token in raw.split():
                parts=token.split(",")
                if len(parts)>=2:
                    lon,lat=float(parts[0]),float(parts[1])
                    pts.append((lat,lon))
            if pts: return pts
        return []

    def _parse_kml_polygons(self, path):
        with open(path, encoding="utf-8") as fh: text = fh.read()
        return self._parse_kml_polygons_text(text)

    def _parse_kml_polygons_text(self, text):
        """All polygon rings in the KML — every <coordinates> element under
        any <Polygon>/<LinearRing>/<outerBoundaryIs>/<innerBoundaryIs>.
        Returns a list of [(lat,lon), ...] rings."""
        root = ET.fromstring(text)
        ns_match = re.match(r'\{[^}]+\}', root.tag)
        ns = ns_match.group(0) if ns_match else ""
        rings = []
        for elem in root.iter(f"{ns}coordinates"):
            if elem.text is None: continue
            raw = elem.text.strip()
            pts = []
            for token in raw.split():
                parts = token.split(",")
                if len(parts) >= 2:
                    try:
                        lon, lat = float(parts[0]), float(parts[1])
                        pts.append((lat, lon))
                    except ValueError:
                        continue
            if len(pts) >= 3:
                rings.append(pts)
        return rings

    def _mode_track(self):
        self._close_all_popups()
        if not self.fv["PP_Latitude"].get(): self._status("Set pivot first."); return
        self.click_mode="track"
        self._show_context_btn("✔ Done Adding Tracks", self._close_add_track)
        self._status("Click map to place track circles. ✔ Done when finished.")

    def _close_add_track(self):
        self.click_mode=None
        self._hide_context_btn()
        n=len(self.current_field.get("pivot_tracks") or [])
        self._status(f"{n} pivot track(s) saved.")

    def _on_map_click(self,coords):
        lat,lon=coords
        mode=self.click_mode

        if mode=="pivot":
            self.fv["PP_Latitude"].set(f"{lat:.7f}"); self.fv["PP_Longitude"].set(f"{lon:.7f}")
            self._autofill_lld(lat, lon)
            self.show_pivot.set(True)
            self._redraw_pivot()
            self.click_mode=None; self._status(f"Pivot: {lat:.5f}, {lon:.5f}")
            self._redraw_boundary(); self._redraw_passes(); self._redraw_tracks()

        elif mode=="boundary":
            self.boundary_pts.append((lat,lon))
            m=self.map_widget.set_marker(lat,lon,text=str(len(self.boundary_pts)),
                                          marker_color_circle="#FFD700",marker_color_outside="#B8860B")
            self.boundary_markers.append(m); self._update_bnd_preview()

        elif mode=="add_shelter":
            # Append an EXTRA shelter pin and redraw. These pins are additive —
            # they show alongside the existing algorithm/manual pins. Stays in
            # this mode until ✔ Done so several can be placed in one session.
            pins = self.current_field.setdefault("manual_shelter_pins", [])
            pins.append([lat, lon])
            self.show_shelters.set(True)
            self._redraw_shelters()
            self._status(f"Added extra pin #{len(pins)} — keep clicking, ✔ Done when finished.")

        elif mode=="inner_boundary":
            if not hasattr(self, "inner_pts"): self.inner_pts = []
            self.inner_pts.append((lat,lon))
            # Distinct orange-red marker so it doesn't get confused with the
            # yellow outer-boundary in-progress marker.
            m = self.map_widget.set_marker(lat, lon, text=str(len(self.inner_pts)),
                                            marker_color_circle="#FF6600",
                                            marker_color_outside="#993300")
            self.boundary_markers.append(m)

        elif mode=="boundary_circle":
            try:
                plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            except (ValueError,TypeError):
                self._status("Set the pivot point first."); self.click_mode=None; return
            r_m=haversine_m(plat,plon,lat,lon)
            if r_m<1.0:
                self._status("Click farther from the pivot to set the radius."); return
            spacing_m=100*0.3048                      # one vertex every 100 ft
            n=max(8,int(round(2*math.pi*r_m/spacing_m)))
            pts=circle_pts(plat,plon,r_m,n=n)
            self.current_field["boundary_polygon"]=[list(p) for p in pts]
            self.boundary_pts=[]
            self.boundary_visible_var.set(True)
            self.show_boundary.set(True)
            area_m2=polygon_area_m2([(p[0],p[1]) for p in pts])
            acres=area_m2*ACRES_PER_M2
            if acres>0:
                try: self.fv["acres"].set(f"{acres:.2f}")
                except Exception: pass
            self._redraw_boundary(); self._redraw_passes()
            if self.show_bays.get():     self._redraw_bays()
            if self.show_tracks.get():   self._redraw_tracks(skip_shelters=True)
            if self.show_shelters.get(): self._redraw_shelters()
            self._status(f"Circle radius {r_m/0.3048:.0f} ft → {n} boundary points "
                         f"({acres:.1f} ac). Click again to resize, or ✔ Done.")

        elif mode=="boundary_edit":
            # Clicking on (near) an edge inserts a new vertex; clicking empty
            # space deselects the current vertex.
            if not self._try_insert_bnd_vertex(lat,lon):
                self._deselect_bnd_vertex()

        elif isinstance(mode,tuple) and mode[0]=="move_boundary_vertex":
            idx=mode[1]
            self.boundary_pts[idx]=(lat,lon)
            try: self.boundary_markers[idx].delete()
            except Exception: pass
            m=self.map_widget.set_marker(lat,lon,text=str(idx+1),
                                          marker_color_circle="#FFD700",marker_color_outside="#B8860B",
                                          command=self._make_bnd_vertex_cb(idx))
            self.boundary_markers[idx]=m
            self._register_drag(f"bnd_{idx}",lat,lon,str(idx+1),"#FFD700","#B8860B",
                                lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo),
                                marker=m)
            self._update_bnd_preview()
            self.click_mode="boundary_edit"
            self._status("Vertex moved. Click another vertex or ✔ Save Boundary.")

        elif mode=="track":
            try:
                plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            except ValueError: self._status("Set pivot first."); return
            r_m=haversine_m(plat,plon,lat,lon)
            self.current_field.setdefault("pivot_tracks",[]).append(round(r_m,2))
            # Stay in track mode so multiple circles can be placed without
            # re-clicking the menu. click_mode cleared by ✔ Done.
            n=len(self.current_field["pivot_tracks"])
            self._status(f"Track {n} added: {r_m:.1f} m ({r_m/0.3048:.1f} ft). "
                         "Click for another, or ✔ Done.")
            self.show_tracks.set(True)
            self._refresh_track_list(); self._redraw_tracks()

        elif isinstance(mode,tuple) and mode[0]=="resize_track":
            idx=mode[1]
            try: self.map_widget.canvas.unbind("<Motion>")
            except AttributeError: pass
            try:
                plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            except ValueError: self.click_mode=None; return
            r_m=haversine_m(plat,plon,lat,lon)
            self.current_field["pivot_tracks"][idx]=round(r_m,2)
            self.click_mode=None; self._status(f"Track {idx+1} set: {r_m:.1f} m ({r_m/0.3048:.1f} ft)")
            self._refresh_track_list(); self._redraw_tracks()

        elif isinstance(mode,tuple) and mode[0]=="move_shelter":
            idx=mode[1]
            overrides=self.current_field.setdefault("shelter_overrides",{})
            overrides[str(idx)]=[lat,lon]
            self.click_mode=None; self.moving_shelter_idx=None
            self._status(f"Shelter #{idx+1} moved — save field to keep.")
            self._redraw_shelters()

        elif mode=="corner_arm_path":
            self.corner_arm_pts.append([lat,lon])
            m=self.map_widget.set_marker(lat,lon,text=str(len(self.corner_arm_pts)),
                                          marker_color_circle="#CC44FF",marker_color_outside="#9900CC")
            self.corner_arm_temp_markers.append(m)
            self._update_arm_path_preview()
            self._status(f"Corner path: {len(self.corner_arm_pts)} pts — click to add more, ✔ Done when finished")

        elif mode=="corner_arm_circle_center":
            self.corner_arm_circle_center=(lat,lon)
            m=self.map_widget.set_marker(lat,lon,text="⊙",
                                          marker_color_circle="#CC44FF",marker_color_outside="#9900CC")
            self.corner_arm_temp_markers.append(m)
            self.click_mode="corner_arm_circle_edge"
            self._status("Circle center set — click on the map to set the radius edge")

        elif mode=="corner_arm_circle_edge":
            if self.corner_arm_circle_center:
                clat,clon=self.corner_arm_circle_center
                r_m=haversine_m(clat,clon,lat,lon)
                arms=self.current_field.setdefault("corner_arms",[])
                arms.append({"type":"circle","lat":clat,"lon":clon,"radius_m":round(r_m,2)})
                self._clear_corner_arm_temp()
                self.click_mode=None; self._hide_context_btn()
                self.corner_arm_circle_center=None
                self._status(f"Corner circle added: r={r_m:.1f} m ({r_m/0.3048:.1f} ft) — "
                             "fixed to lat/lon, won't move when pivot does.")
                # Auto-enable visibility so the new circle is shown right away.
                self.show_corner_arms.set(True)
                self._redraw_corner_arms()
                if self.show_shelters.get(): self._redraw_shelters()

    # ── Boundary ───────────────────────────────────────────────────────────────
    def _close_boundary(self):
        if len(self.boundary_pts)<3: self._status("Need ≥ 3 points."); return
        self._selected_bnd_vertex=None
        # Clean tracing artifacts (near-duplicate points + short doubling-back
        # spikes from stray clicks) before committing the drawn polygon.
        self.boundary_pts=[tuple(p) for p in despike_ring(self.boundary_pts)]
        self.current_field["boundary_polygon"]=[list(p) for p in self.boundary_pts]
        self.click_mode=None
        self._hide_context_btn()
        self._clear_boundary_markers(); self._redraw_boundary()
        # Auto-fill acres from the drawn polygon. The user can still type their
        # own number into the Acres entry afterward to override the calculated
        # value (e.g. for known surveyed acreage that differs from the rough
        # outline). The next time they save a boundary, this will recompute.
        area_m2 = polygon_area_m2([(p[0], p[1]) for p in self.boundary_pts])
        acres = area_m2 * ACRES_PER_M2
        if acres > 0:
            try: self.fv["acres"].set(f"{acres:.2f}")
            except Exception: pass
            self._status(f"Boundary set ({len(self.boundary_pts)} vertices) — {acres:.2f} acres.")
        else:
            self._status(f"Boundary set ({len(self.boundary_pts)} vertices).")
        self.boundary_pts=[]
        self._redraw_passes()
        if self.show_bays.get():   self._redraw_bays()
        if self.show_tracks.get(): self._redraw_tracks(skip_shelters=True)
        if self.show_shelters.get(): self._redraw_shelters()

    def _clear_boundary(self):
        self._close_all_popups()
        self.current_field["boundary_polygon"]=None; self.boundary_pts=[]; self.click_mode=None
        self._hide_context_btn(); self._clear_boundary_overlays()
        self._status("Boundary cleared."); self._clear_passes(); self._clear_shelters()

    def _clear_boundary_markers(self):
        for m in self.boundary_markers:
            try: m.delete()
            except Exception: pass
        self.boundary_markers=[]

    def _clear_boundary_overlays(self):
        self._unregister_drag_prefix("bnd_")
        self._clear_boundary_markers()
        if self.boundary_poly: self.boundary_poly.delete(); self.boundary_poly=None
        # Inner-boundary outlines (drawn separately so they show with their
        # own colour and can be cleared / redrawn without touching the outer).
        for o in getattr(self, "boundary_inner_polys", []):
            try: o.delete()
            except Exception: pass
        self.boundary_inner_polys = []

    def _update_bnd_preview(self):
        if self.boundary_poly: self.boundary_poly.delete()
        if len(self.boundary_pts)>=2:
            self.boundary_poly=self.map_widget.set_polygon(
                self.boundary_pts,fill_color=None,outline_color="#FFD700",border_width=2)

    def _redraw_boundary(self):
        if self.boundary_poly: self.boundary_poly.delete(); self.boundary_poly=None
        for o in getattr(self, "boundary_inner_polys", []):
            try: o.delete()
            except Exception: pass
        self.boundary_inner_polys = []
        if not self.show_boundary.get(): return
        bp=self.current_field.get("boundary_polygon")
        if bp and len(bp)>=3:
            self.boundary_poly=self.map_widget.set_polygon(
                [tuple(p) for p in bp],fill_color=None,outline_color="#00CED1",border_width=2)
        # Inner boundaries: orange-red outlines so they read clearly as
        # excluded zones distinct from the outer boundary.
        for inner in (self.current_field.get("boundary_inner") or []):
            if not inner or len(inner) < 3: continue
            try:
                o = self.map_widget.set_polygon(
                    [(pt[0], pt[1]) for pt in inner],
                    fill_color=None, outline_color="#FF6600", border_width=2)
                self.boundary_inner_polys.append(o)
            except Exception:
                pass

    def _on_bnd_vertex_drag(self,idx,lat,lon):
        if self._selected_bnd_vertex==idx:
            self._selected_bnd_vertex=None
            self._show_context_btn("✔ Save Boundary",self._close_boundary)
        self.boundary_pts[idx]=(lat,lon)
        try: self.boundary_markers[idx].delete()
        except Exception: pass
        m=self.map_widget.set_marker(lat,lon,text=str(idx+1),
                                      marker_color_circle="#FFD700",marker_color_outside="#B8860B",
                                      command=self._make_bnd_vertex_cb(idx))
        self.boundary_markers[idx]=m
        self._register_drag(f"bnd_{idx}",lat,lon,str(idx+1),"#FFD700","#B8860B",
                            lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo),
                            marker=m)
        self._update_bnd_preview()
        # Live-update bays / passes / track clipping so the user sees the
        # downstream effect of the move without having to hit "Save Boundary".
        # Commit the in-progress points into the field so the redraw helpers
        # (which read current_field["boundary_polygon"]) see the new shape.
        if len(self.boundary_pts) >= 3:
            self.current_field["boundary_polygon"] = [list(p) for p in self.boundary_pts]
            if self.show_passes.get(): self._redraw_passes()
            if self.show_bays.get():   self._redraw_bays()
            if self.show_tracks.get(): self._redraw_tracks(skip_shelters=True)

    # ── Pivot tracks ───────────────────────────────────────────────────────────
    def _toggle_pivot(self):
        """Toggle the pivot point marker, pivot tracks, AND corner tracks
        together — they're all part of the same conceptual layer (pivot +
        anything anchored relative to it / around its kill zone)."""
        self._close_all_popups()
        on = not self.show_pivot.get()
        self._set_pivot_visible(on)
        self._status("Pivot " + ("shown." if on else "hidden."))

    # ── Master visibility setters (called by toolbar checkboxes) ──────────────
    def _set_pivot_visible(self, on):
        self.pivot_visible_var.set(on)
        self.show_pivot.set(on); self.show_tracks.set(on); self.show_corner_arms.set(on)
        self._redraw_pivot(); self._redraw_tracks(); self._redraw_corner_arms()

    def _set_boundary_visible(self, on):
        self.boundary_visible_var.set(on)
        self.show_boundary.set(on)
        self._redraw_boundary()

    def _set_sprayer_visible(self, on):
        self.sprayer_visible_var.set(on)
        if on:
            self.show_passes.set(True)
            self._redraw_passes()
            # Restore the pass/tire zone overlay to whatever it was before hiding
            if getattr(self, '_sprayer_was_buffer_on', False):
                self.show_pass_buffer_overlay.set(True)
                self._redraw_pass_buffer_overlay()
        else:
            # Remember the buffer overlay state so we can restore it on turn-on
            self._sprayer_was_buffer_on = self.show_pass_buffer_overlay.get()
            self.show_passes.set(False)
            self._clear_passes()
            self.show_pass_buffer_overlay.set(False)
            self._clear_pass_buffer_overlay()

    def _set_planter_visible(self, on):
        self.planter_visible_var.set(on)
        self.show_bays.set(on); self.show_planter_passes.set(on)
        if on: self._redraw_bays()
        else:  self._clear_bays()
        self._redraw_planter_passes()

    def _set_shelters_visible(self, on):
        self.shelters_visible_var.set(on)
        self.show_shelters.set(on)
        if on: self._redraw_shelters()
        else:  self._clear_shelters()

    def _redraw_pivot(self):
        """Draw or clear the pivot marker based on show_pivot."""
        if self.pivot_marker:
            try: self.pivot_marker.delete()
            except Exception: pass
            self.pivot_marker=None
        self._unregister_drag_prefix("pivot")
        if not self.show_pivot.get(): return
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
        except (ValueError,TypeError): return
        self.pivot_marker=self.map_widget.set_marker(plat,plon,text="Pivot",
                                                      marker_color_circle="red",marker_color_outside="darkred")
        self._register_drag("pivot",plat,plon,"Pivot","red","darkred",self._on_pivot_drag,marker=self.pivot_marker)

    def _toggle_tracks(self):
        self._close_all_popups()
        self.show_tracks.set(not self.show_tracks.get())
        self._redraw_tracks()

    def _mode_edit_track_measurements(self):
        """Dialog to type the length of each pivot SPAN (the segment from the
        previous tower, or the pivot for span 1, out to that tower). Internally
        the tracks are still stored as cumulative distance-from-pivot in metres;
        spans are just a convenient input that maps how pivots are actually
        measured (e.g. eight 179 ft spans with a short 66 ft final span). Lets
        the rings be corrected against real measurements when the satellite
        imagery is slightly off."""
        self._close_all_popups()
        use_m=self.unit_var.get()=="Metric"
        unit="m" if use_m else "ft"
        conv=1.0 if use_m else 1.0/0.3048   # stored metres → display unit
        tracks=sorted(self.current_field.get("pivot_tracks") or [])
        # Convert stored cumulative distances → per-span deltas for display
        spans_display=[]
        prev=0.0
        for t in tracks:
            spans_display.append((t-prev)*conv)
            prev=t

        win=ctk.CTkToplevel(self)
        win.title("Edit Pivot Span Lengths")
        win.geometry("380x500")
        win.grab_set()

        ctk.CTkLabel(win,text=f"Length of each span ({unit}):",
                     font=ctk.CTkFont(family=FONT_HEADING,size=12)).pack(pady=(12,2),padx=10)
        ctk.CTkLabel(win,text="Span N = previous tower (or pivot) out to tower N.",
                     text_color=UI_MUTED,font=ctk.CTkFont(size=10)).pack(padx=10,pady=(0,6))

        scroll=ctk.CTkScrollableFrame(win,height=200)
        scroll.pack(fill="both",expand=True,padx=10,pady=(0,6))

        entry_vars=[]
        def rebuild_rows(values):
            for w in scroll.winfo_children():
                w.destroy()
            entry_vars.clear()
            for i,val in enumerate(values):
                row=ctk.CTkFrame(scroll,fg_color="transparent")
                row.pack(fill="x",pady=2)
                ctk.CTkLabel(row,text=f"Span {i+1}:",width=70,anchor="w").pack(side="left")
                v=tk.StringVar(value=f"{val:.1f}")
                ctk.CTkEntry(row,textvariable=v,width=110).pack(side="left")
                ctk.CTkLabel(row,text=unit,width=24,anchor="w").pack(side="left",padx=(4,0))
                entry_vars.append(v)
        rebuild_rows(spans_display)

        ctk.CTkFrame(win,height=1,fg_color=UI_BORDER).pack(fill="x",padx=10,pady=4)
        ctk.CTkLabel(win,text="Equal spans",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(padx=10,anchor="w")
        eq_row=ctk.CTkFrame(win,fg_color="transparent")
        eq_row.pack(fill="x",padx=10,pady=2)
        ctk.CTkLabel(eq_row,text="# spans:",width=58,anchor="w").pack(side="left")
        count_v=tk.StringVar(value=str(len(tracks)) if tracks else "")
        ctk.CTkEntry(eq_row,textvariable=count_v,width=48).pack(side="left",padx=(2,8))
        ctk.CTkLabel(eq_row,text=f"span ({unit}):",width=62,anchor="w").pack(side="left")
        span_v=tk.StringVar()
        ctk.CTkEntry(eq_row,textvariable=span_v,width=60).pack(side="left",padx=(2,4))
        def apply_equal():
            try:
                span=float(span_v.get())
                if span<=0: raise ValueError
            except ValueError:
                self._status("Enter a valid span length."); return
            try:
                cnt=int(float(count_v.get())) if count_v.get().strip() else len(entry_vars)
            except ValueError:
                cnt=len(entry_vars)
            if cnt<=0:
                self._status("Enter how many spans."); return
            rebuild_rows([span]*cnt)
        ctk.CTkButton(eq_row,text="Apply",width=60,command=apply_equal).pack(side="left")
        ctk.CTkLabel(win,text="Fills every span with this length — then edit individual\nspans (e.g. a shorter final span).",
                     text_color=UI_MUTED,font=ctk.CTkFont(size=10),justify="left").pack(padx=10,anchor="w")

        btn_row=ctk.CTkFrame(win,fg_color="transparent")
        btn_row.pack(fill="x",padx=10,pady=(8,10))
        def do_save():
            new_tracks=[]
            cumulative=0.0
            for v in entry_vars:
                try:
                    val=float(v.get())
                except ValueError:
                    continue   # blank/invalid span → tower removed, rest shift inward
                if val<=0:
                    continue
                cumulative+=val/conv   # display span → metres, accumulate to distance-from-pivot
                new_tracks.append(round(cumulative,2))
            self.current_field["pivot_tracks"]=new_tracks
            if new_tracks: self.show_tracks.set(True)   # make the saved tracks visible
            self._refresh_track_list(); self._redraw_tracks()
            win.destroy()
            self._status(f"Saved {len(new_tracks)} span(s).")
        ctk.CTkButton(btn_row,text="Save",command=do_save).pack(side="left",expand=True,fill="x",padx=(0,4))
        ctk.CTkButton(btn_row,text="Cancel",fg_color="#555",command=win.destroy).pack(side="left",expand=True,fill="x")

    def _redraw_tracks(self, skip_shelters=False):
        for o in self.track_circles:
            try: o.delete()
            except Exception: pass
        self.track_circles=[]; self.track_handles=[]
        if not self.show_tracks.get(): return
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
        except (ValueError,TypeError): return
        excl_m=float(self.fv.get("track_exclusion_ft",self.excl_var).get() or "10")*0.3048
        bp=self.current_field.get("boundary_polygon")
        has_bnd = bool(bp and len(bp)>=3)
        pivot_tracks = self.current_field.get("pivot_tracks") or []
        for i,r_m in enumerate(pivot_tracks):
            for r,col,w in [(r_m+excl_m,"#FF2A2A",2),(max(1,r_m-excl_m),"#FF2A2A",2)]:
                pts = circle_pts(plat,plon,r,n=180)
                # Decide whether to draw as a single polygon (fast, matches the
                # historical look exactly) or as clipped path segments. We only
                # need the clipped path when the circle actually crosses the
                # boundary; for the common case where the track is entirely
                # inside the field, the polygon is identical and cheaper.
                if not has_bnd:
                    inside_count = None   # signal "no clip"
                else:
                    flags = [point_in_latlon_polygon(la, lo, bp) for la, lo in pts]
                    inside_count = sum(flags)
                if inside_count is None or inside_count == len(pts):
                    # Entire circle inside boundary (or no boundary at all) →
                    # draw as a closed polygon outline.
                    self.track_circles.append(self.map_widget.set_polygon(
                        pts, fill_color=None, outline_color=col, border_width=w))
                    continue
                if inside_count == 0:
                    # Entire circle outside the boundary → don't draw anything.
                    continue
                # Mixed: emit path segments only for the inside runs.
                pts_closed = pts + [pts[0]]
                flags_closed = flags + [flags[0]]
                segment = []
                def _flush(_seg, _col=col, _w=w):
                    if len(_seg) >= 2:
                        try:
                            path = self.map_widget.set_path(list(_seg),
                                                            color=_col, width=_w)
                            self.track_circles.append(path)
                        except Exception:
                            pass
                for k, (la, lo) in enumerate(pts_closed):
                    if flags_closed[k]:
                        segment.append((la, lo))
                    else:
                        _flush(segment); segment = []
                _flush(segment)
        if not skip_shelters and self.show_shelters.get(): self._redraw_shelters()

    def _on_track_drag(self,idx,lat,lon,final=False):
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
        except ValueError: return
        tracks=self.current_field.get("pivot_tracks") or []
        if not (0<=idx<len(tracks)): return
        r_m=haversine_m(plat,plon,lat,lon)
        tracks[idx]=round(r_m,2)
        self._status(f"Track {idx+1}: {r_m:.1f} m ({r_m/0.3048:.1f} ft)")
        if final:
            self._refresh_track_list(); self._redraw_tracks()
        else:
            # Live preview while dragging — skip the (expensive) shelter redraw
            self._redraw_tracks(skip_shelters=True)

    def _track_hit(self,lat,lon,mpp):
        """Return the index of the pivot track whose exclusion band contains the
        click point (lat,lon), or None. The band is r±excl_m, widened to a few
        pixels so it's easy to grab. Picks the closest track on overlap."""
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
        except (ValueError,TypeError): return None
        tracks=self.current_field.get("pivot_tracks") or []
        if not tracks: return None
        excl_m=float(self.fv.get("track_exclusion_ft",self.excl_var).get() or "10")*0.3048
        tol=max(excl_m, 12*mpp)   # grabbable even when the band is narrow
        d=haversine_m(plat,plon,lat,lon)
        best_idx=None; best_gap=tol
        for i,r_m in enumerate(tracks):
            gap=abs(d-r_m)
            if gap<=best_gap:
                best_gap=gap; best_idx=i
        return best_idx

    @staticmethod
    def _point_seg_dist_m(plat,plon,alat,alon,blat,blon):
        """Approx distance (metres) from point P to segment A–B using a local
        equirectangular projection centred at P."""
        lat0=math.radians(plat)
        mlat=111320.0; mlon=111320.0*math.cos(lat0)
        ax=(alon-plon)*mlon; ay=(alat-plat)*mlat
        bx=(blon-plon)*mlon; by=(blat-plat)*mlat
        dx=bx-ax; dy=by-ay
        seg2=dx*dx+dy*dy
        if seg2<=0: return math.hypot(ax,ay)
        t=-(ax*dx+ay*dy)/seg2
        t=max(0.0,min(1.0,t))
        cx=ax+t*dx; cy=ay+t*dy
        return math.hypot(cx,cy)

    def _corner_arm_hit(self,lat,lon,mpp):
        """Index of the corner arm whose path/circle is near the click, else None."""
        arms=self.current_field.get("corner_arms") or []
        if not arms: return None
        try:
            excl_m=float(self.fv.get("track_exclusion_ft",self.excl_var).get() or "10")*0.3048
        except (ValueError,AttributeError):
            excl_m=10*0.3048
        tol=max(excl_m,12*mpp)
        best=None; best_gap=tol
        for i,arm in enumerate(arms):
            if arm.get("type")=="circle":
                d=haversine_m(arm.get("lat",0),arm.get("lon",0),lat,lon)
                gap=abs(d-arm.get("radius_m",0))
            else:
                pts=arm.get("pts") or []
                if len(pts)<2: continue
                gap=min(self._point_seg_dist_m(lat,lon,pts[k][0],pts[k][1],
                                               pts[k+1][0],pts[k+1][1])
                        for k in range(len(pts)-1))
            if gap<=best_gap:
                best_gap=gap; best=i
        return best

    # ── Track / corner click popups ───────────────────────────────────────────
    def _edit_single_track(self,idx):
        """Edit the distance-from-pivot (span measurement) of one pivot track."""
        tracks=self.current_field.get("pivot_tracks") or []
        if not (0<=idx<len(tracks)): return
        use_m=self.unit_var.get()=="Metric"
        unit="m" if use_m else "ft"
        conv=1.0 if use_m else 1.0/0.3048
        cur=tracks[idx]*conv
        val=self._ask_string("Span Length",
            f"Distance from pivot to this track ({unit}).  Current: {cur:.1f}")
        if val is None: return
        try: v=float(val.strip())
        except ValueError: self._status("Enter a number."); return
        if v<=0: self._status("Span length must be greater than 0."); return
        tracks[idx]=round(v/conv,2)
        self.current_field["pivot_tracks"]=sorted(tracks)
        self.show_tracks.set(True)
        self._refresh_track_list(); self._redraw_tracks()
        self._status(f"Span length set to {v:.1f} {unit}.")

    def _delete_single_track(self,idx):
        tracks=self.current_field.get("pivot_tracks") or []
        if 0<=idx<len(tracks):
            tracks.pop(idx)
            self.current_field["pivot_tracks"]=tracks
            self._refresh_track_list(); self._redraw_tracks()
            self._status(f"Track deleted ({len(tracks)} remaining).")

    def _show_track_popup(self,idx):
        """Options popup for a clicked pivot track: edit span length, edit the
        buffer zone (exclusion), or delete the track."""
        tracks=self.current_field.get("pivot_tracks") or []
        if not (0<=idx<len(tracks)): return
        self._close_all_popups()
        use_m=self.unit_var.get()=="Metric"
        unit="m" if use_m else "ft"
        conv=1.0 if use_m else 1.0/0.3048
        dist=tracks[idx]*conv
        # Span length = this ring's distance minus the next ring inward (or the
        # pivot for the innermost ring). Tracks are stored as cumulative
        # distance-from-pivot, so we find the largest track below this one.
        this_m=tracks[idx]; prev_m=0.0
        for t in sorted(tracks):
            if t < this_m-1e-6: prev_m=t
            else: break
        span=(this_m-prev_m)*conv
        try: excl=float(self.fv.get("track_exclusion_ft",self.excl_var).get() or "10")
        except (ValueError,AttributeError): excl=10.0
        win=ctk.CTkToplevel(self)
        win.title(f"Pivot Track {idx+1}")
        win.geometry("400x400")
        win.grab_set()
        ctk.CTkLabel(win,text=f"Pivot Track {idx+1}",
                     font=ctk.CTkFont(family=FONT_HEADING,size=18)).pack(padx=24,pady=(20,6))
        ctk.CTkLabel(win,
                     text=(f"Distance from pivot point: {dist:.1f} {unit}\n"
                           f"Span length: {span:.1f} {unit}\n"
                           f"Buffer zone: {excl:g} ft"),
                     text_color=UI_MUTED,font=ctk.CTkFont(size=13),justify="left").pack(padx=24,pady=(0,14))
        def act(fn):
            win.destroy(); fn()
        ctk.CTkButton(win,text="Edit Span Length…",height=40,
                      font=ctk.CTkFont(size=13),
                      command=lambda:act(lambda:self._edit_single_track(idx))).pack(fill="x",padx=24,pady=4)
        ctk.CTkButton(win,text="Edit Buffer Zone…",height=40,
                      font=ctk.CTkFont(size=13),
                      command=lambda:act(self._edit_track_exclusion)).pack(fill="x",padx=24,pady=4)
        ctk.CTkButton(win,text="Delete Track",fg_color="#6b1a1a",height=40,
                      font=ctk.CTkFont(size=13),
                      command=lambda:act(lambda:self._delete_single_track(idx))).pack(fill="x",padx=24,pady=4)
        ctk.CTkButton(win,text="Cancel",fg_color="#555",height=40,
                      font=ctk.CTkFont(size=13),
                      command=win.destroy).pack(fill="x",padx=24,pady=(4,20))
        _center_on_parent(win,self)

    def _show_corner_track_popup(self,idx):
        """Options popup for a clicked corner track: edit the buffer zone or
        delete the corner. (No span length — corner tracks are free paths.)"""
        arms=self.current_field.get("corner_arms") or []
        if not (0<=idx<len(arms)): return
        self._close_all_popups()
        try: excl=float(self.fv.get("track_exclusion_ft",self.excl_var).get() or "10")
        except (ValueError,AttributeError): excl=10.0
        arm=arms[idx]
        desc=(f"{len(arm.get('pts',[]))} pts" if arm.get("type")!="circle"
              else f"r={arm.get('radius_m',0):.1f} m")
        win=ctk.CTkToplevel(self)
        win.title(f"Corner Track {idx+1}")
        win.grab_set()
        ctk.CTkLabel(win,text=f"Corner Track {idx+1}",
                     font=ctk.CTkFont(family=FONT_HEADING,size=13)).pack(padx=18,pady=(12,2))
        ctk.CTkLabel(win,text=f"{desc}\nBuffer zone: {excl:g} ft",
                     text_color=UI_MUTED,font=ctk.CTkFont(size=11),justify="left").pack(padx=18,pady=(0,8))
        def act(fn):
            win.destroy(); fn()
        def do_delete():
            if 0<=idx<len(self.current_field.get("corner_arms") or []):
                del self.current_field["corner_arms"][idx]
                self._redraw_corner_arms()
                if self.show_shelters.get(): self._redraw_shelters()
                self._status("Corner track deleted.")
        ctk.CTkButton(win,text="Edit Buffer Zone…",
                      command=lambda:act(self._edit_track_exclusion)).pack(fill="x",padx=18,pady=2)
        ctk.CTkButton(win,text="Edit Path Vertices…",
                      command=lambda:act(lambda:self._start_edit_corner_arm(idx))).pack(fill="x",padx=18,pady=2)
        ctk.CTkButton(win,text="Delete Corner Track",fg_color="#6b1a1a",
                      command=lambda:act(do_delete)).pack(fill="x",padx=18,pady=2)
        ctk.CTkButton(win,text="Cancel",fg_color="#555",command=win.destroy).pack(fill="x",padx=18,pady=(2,12))
        _center_on_parent(win,self)

    def _boundary_edge_hit(self, lat, lon, mpp):
        """True if (lat,lon) is near an edge of the active field's outer boundary."""
        bp=self.current_field.get("boundary_polygon")
        if not bp or len(bp)<2: return False
        tol=max(12*mpp, 6.0)
        n=len(bp)
        for i in range(n):
            a=bp[i]; b=bp[(i+1)%n]
            if self._point_seg_dist_m(lat,lon,a[0],a[1],b[0],b[1])<=tol:
                return True
        return False

    def _show_boundary_popup(self):
        """Options popup for a clicked outer boundary: edit or delete it."""
        bp=self.current_field.get("boundary_polygon")
        if not bp or len(bp)<3: return
        self._close_all_popups()
        win=ctk.CTkToplevel(self)
        win.title("Field Boundary")
        win.geometry("320x230")
        win.grab_set()
        ctk.CTkLabel(win,text="Field Boundary",
                     font=ctk.CTkFont(family=FONT_HEADING,size=16)).pack(padx=24,pady=(18,4))
        ctk.CTkLabel(win,text=f"{len(bp)} points",text_color=UI_MUTED,
                     font=ctk.CTkFont(size=12)).pack(padx=24,pady=(0,12))
        def act(fn): win.destroy(); fn()
        ctk.CTkButton(win,text="Edit Boundary",height=38,font=ctk.CTkFont(size=13),
                      command=lambda:act(self._mode_edit_boundary)).pack(fill="x",padx=24,pady=4)
        ctk.CTkButton(win,text="Delete Boundary",height=38,fg_color="#6b1a1a",
                      font=ctk.CTkFont(size=13),
                      command=lambda:act(self._clear_boundary)).pack(fill="x",padx=24,pady=4)
        ctk.CTkButton(win,text="Cancel",height=38,fg_color="#555",font=ctk.CTkFont(size=13),
                      command=win.destroy).pack(fill="x",padx=24,pady=(4,18))
        _center_on_parent(win,self)

    # ── Corner zones (paths and circles — unlimited) ──────────────────────────
    def _mode_add_corner_path(self):
        self._close_all_popups()
        self._cancel_corner_arm_drawing()
        self.corner_arm_pts=[]
        self.click_mode="corner_arm_path"
        self._show_context_btn("✔ Done Path", self._finish_corner_path)
        self._status("Corner path — click map to place points, ✔ Done when finished")

    def _update_arm_path_preview(self):
        if len(self.corner_arm_pts)<2: return
        # remove old preview overlays drawn during this session
        for o in list(self.corner_arm_overlays):
            if getattr(o,"_is_preview",False):
                try: o.delete()
                except Exception: pass
                self.corner_arm_overlays.remove(o)
        try:
            p=self.map_widget.set_path(
                [(lat,lon) for lat,lon in self.corner_arm_pts],color="#CC44FF",width=3)
            p._is_preview=True
            self.corner_arm_overlays.append(p)
        except Exception: pass

    def _finish_corner_path(self):
        if len(self.corner_arm_pts)<2:
            self._status("Need at least 2 points for a corner path."); return
        # remove preview overlay
        for o in list(self.corner_arm_overlays):
            if getattr(o,"_is_preview",False):
                try: o.delete()
                except Exception: pass
                self.corner_arm_overlays.remove(o)
        arms=self.current_field.setdefault("corner_arms",[])
        arms.append({"type":"path","pts":[list(p) for p in self.corner_arm_pts]})
        self._clear_corner_arm_temp()
        self.click_mode=None; self._hide_context_btn()
        n=len(arms)
        self._status(f"Corner path #{n} saved ({len(self.corner_arm_pts)} pts) — "
                     "fixed to lat/lon, won't move when pivot does.")
        self.corner_arm_pts=[]
        # Make the newly-added path visible without requiring a manual toggle.
        self.show_corner_arms.set(True)
        self._redraw_corner_arms()
        if self.show_shelters.get(): self._redraw_shelters()

    def _cancel_corner_arm_drawing(self):
        self._clear_corner_arm_temp()
        # remove any preview overlays
        for o in list(self.corner_arm_overlays):
            if getattr(o,"_is_preview",False):
                try: o.delete()
                except Exception: pass
                self.corner_arm_overlays.remove(o)
        self.click_mode=None; self._hide_context_btn()
        self.corner_arm_pts=[]; self.corner_arm_circle_center=None

    def _clear_corner_arm_temp(self):
        for m in self.corner_arm_temp_markers:
            try: m.delete()
            except Exception: pass
        self.corner_arm_temp_markers=[]

    def _mode_delete_corner_ui(self):
        self._close_all_popups()
        arms=self.current_field.get("corner_arms") or []
        if not arms:
            self._status("No corner zones to delete."); return
        win=ctk.CTkToplevel(self)
        win.title("Delete Corner Zone"); win.geometry("360x240"); win.grab_set()
        ctk.CTkLabel(win,text="Select zone to delete:").pack(pady=(12,4))
        lb=tk.Listbox(win,bg=UI_CARD,fg=UI_TEXT,selectbackground=UI_SELECT,selectforeground=UI_TEXT,
                      relief="flat",font=(FONT_BODY,11),height=6,
                      activestyle="none",highlightthickness=1,highlightbackground=UI_BORDER)
        for i,arm in enumerate(arms):
            if arm.get("type")=="circle":
                lb.insert(tk.END,f"Circle {i+1}: r={arm['radius_m']:.1f} m ({arm['radius_m']/0.3048:.1f} ft)")
            else:
                lb.insert(tk.END,f"Path {i+1}: {len(arm.get('pts',[]))} pts")
        lb.pack(fill="x",padx=10,pady=4)
        def do_delete():
            sel=lb.curselection()
            if not sel: return
            del self.current_field["corner_arms"][sel[0]]
            self._redraw_corner_arms()
            if self.show_shelters.get(): self._redraw_shelters()
            win.destroy(); self._status("Corner zone deleted.")
        ctk.CTkButton(win,text="Delete Selected",fg_color="#6b1a1a",command=do_delete).pack(pady=(4,2))
        ctk.CTkButton(win,text="Cancel",command=win.destroy).pack()

    # ── Corner path vertex editing ──────────────────────────────────────────────
    def _mode_edit_corner_path(self):
        self._close_all_popups()
        arms = self.current_field.get("corner_arms") or []
        path_arms = [(i, arm) for i, arm in enumerate(arms)
                     if arm.get("type") == "path" and len(arm.get("pts") or []) >= 2]
        if not path_arms:
            self._status("No corner paths to edit — use Add Corner Path first.")
            return
        if len(path_arms) == 1:
            self._start_edit_corner_arm(path_arms[0][0])
            return
        # Multiple paths — show a picker dialog
        win = ctk.CTkToplevel(self)
        win.title("Edit Corner Path")
        win.grab_set()
        _center_on_parent(win, self)
        ctk.CTkLabel(win, text="Select path to edit:").pack(pady=(10, 2))
        lb = tk.Listbox(win, height=min(6, len(path_arms)), bg="#2b2b2b",
                        fg="white", selectbackground="#1f6aa5")
        for i, arm in path_arms:
            lb.insert(tk.END, f"Path {i+1}: {len(arm.get('pts',[]))} pts")
        lb.pack(padx=10, pady=4, fill="x")
        def do_edit():
            sel = lb.curselection()
            if not sel: return
            arm_idx = path_arms[sel[0]][0]
            win.destroy()
            self._start_edit_corner_arm(arm_idx)
        ctk.CTkButton(win, text="Edit Selected", command=do_edit).pack(pady=(4, 2))
        ctk.CTkButton(win, text="Cancel", command=win.destroy).pack()

    def _start_edit_corner_arm(self, arm_idx):
        """Place draggable vertex markers on the selected corner arm path."""
        arms = self.current_field.get("corner_arms") or []
        if arm_idx >= len(arms): return
        arm = arms[arm_idx]
        if arm.get("type") != "path": return
        pts = arm.get("pts") or []
        if len(pts) < 2:
            self._status("Path has too few points."); return
        self._cancel_corner_arm_drawing()         # wipe any in-progress drawing markers
        self._unregister_drag_prefix("carm_v_")  # drop stale vertex drags
        self._editing_corner_arm_idx = arm_idx
        for i, pt in enumerate(pts):
            lat, lon = float(pt[0]), float(pt[1])
            m = self.map_widget.set_marker(lat, lon, text=str(i + 1),
                                           marker_color_circle="#CC44FF",
                                           marker_color_outside="#9900CC")
            self.corner_arm_temp_markers.append(m)
            self._register_drag(f"carm_v_{i}", lat, lon, str(i + 1),
                                "#CC44FF", "#9900CC",
                                lambda la, lo, vi=i: self._on_corner_arm_vertex_drag(vi, la, lo),
                                marker=m)
        self._show_context_btn("✔ Done Editing Path", self._finish_corner_arm_edit)
        self._status(f"Corner path {arm_idx+1}: drag vertices to reposition. ✔ Done when finished.")
        self._redraw_corner_arms()

    def _on_corner_arm_vertex_drag(self, vertex_idx, lat, lon):
        arm_idx = self._editing_corner_arm_idx
        if arm_idx is None: return
        arms = self.current_field.get("corner_arms") or []
        if arm_idx >= len(arms): return
        pts = arms[arm_idx].get("pts") or []
        if 0 <= vertex_idx < len(pts):
            pts[vertex_idx] = [lat, lon]
            arms[arm_idx]["pts"] = pts
        self._redraw_corner_arms()
        if self.show_shelters.get(): self._redraw_shelters()

    def _finish_corner_arm_edit(self):
        self._hide_context_btn()
        self._cancel_corner_arm_drawing()
        self._unregister_drag_prefix("carm_v_")
        self._editing_corner_arm_idx = None
        self._redraw_corner_arms()
        if self.show_shelters.get(): self._redraw_shelters()
        self._status("Corner path saved. Save field to persist.")

    def _offset_path_latlon(self, pts_latlon, excl_m):
        """Return (left, right) lat/lon polylines parallel to pts_latlon at
        perpendicular distance excl_m on each side. At interior vertices the
        offset uses the unit-bisector of the two adjacent perpendiculars, so
        the offset stays roughly constant through corners (purely visual —
        the shelter-exclusion math in maketentgrid is exact point-to-segment).
        """
        if len(pts_latlon) < 2: return [], []
        # Use first point as the local ENU origin so all offsets share the
        # same projection; small fields (< a few km) don't accumulate error.
        lat0, lon0 = pts_latlon[0]
        enu = [latlon_to_enu(la, lo, lat0, lon0) for la, lo in pts_latlon]
        n_pts = len(enu)
        perp = []
        for i in range(n_pts):
            # incoming segment direction (None at first vertex)
            in_d = None
            if i > 0:
                dx = enu[i][0] - enu[i-1][0]
                dy = enu[i][1] - enu[i-1][1]
                L = math.sqrt(dx*dx + dy*dy)
                if L > 0: in_d = (dx/L, dy/L)
            # outgoing segment direction (None at last vertex)
            out_d = None
            if i < n_pts - 1:
                dx = enu[i+1][0] - enu[i][0]
                dy = enu[i+1][1] - enu[i][1]
                L = math.sqrt(dx*dx + dy*dy)
                if L > 0: out_d = (dx/L, dy/L)
            # Build a unit perpendicular at this vertex. perp(d) = (-dy, dx).
            if in_d and out_d:
                px = (-in_d[1] - out_d[1]) * 0.5
                py = ( in_d[0] + out_d[0]) * 0.5
            elif in_d:
                px, py = -in_d[1], in_d[0]
            elif out_d:
                px, py = -out_d[1], out_d[0]
            else:
                px, py = 0.0, 0.0
            L = math.sqrt(px*px + py*py)
            if L > 0: px, py = px/L, py/L
            perp.append((px, py))
        left = []
        right = []
        for (e, n), (px, py) in zip(enu, perp):
            left.append(enu_to_latlon(e + excl_m * px, n + excl_m * py, lat0, lon0))
            right.append(enu_to_latlon(e - excl_m * px, n - excl_m * py, lat0, lon0))
        return left, right

    def _redraw_corner_arms(self):
        for o in self.corner_arm_overlays:
            if not getattr(o,"_is_preview",False):
                try: o.delete()
                except Exception: pass
        self.corner_arm_overlays=[o for o in self.corner_arm_overlays if getattr(o,"_is_preview",False)]
        # Respect the visibility toggle. We still tore down old overlays above
        # so toggling off actually clears them; toggling back on rebuilds.
        if not self.show_corner_arms.get(): return
        arms=self.current_field.get("corner_arms") or []
        col = "#FF2A2A"
        bp = self.current_field.get("boundary_polygon") or []
        try:
            excl_m = float(self.fv.get("track_exclusion_ft", self.excl_var).get() or "10") * 0.3048
        except (ValueError, AttributeError):
            excl_m = 10 * 0.3048

        def _draw_clipped(pts_ll, width):
            """Draw a polyline clipped to the field boundary."""
            if not bp or len(bp) < 3:
                if len(pts_ll) >= 2:
                    try: self.corner_arm_overlays.append(
                            self.map_widget.set_path(pts_ll, color=col, width=width))
                    except Exception: pass
                return
            flags = [point_in_latlon_polygon(la, lo, bp) for la, lo in pts_ll]
            seg = []
            for k, pt in enumerate(pts_ll):
                if flags[k]:
                    seg.append(pt)
                else:
                    if len(seg) >= 2:
                        try: self.corner_arm_overlays.append(
                                self.map_widget.set_path(seg, color=col, width=width))
                        except Exception: pass
                    seg = []
            if len(seg) >= 2:
                try: self.corner_arm_overlays.append(
                        self.map_widget.set_path(seg, color=col, width=width))
                except Exception: pass

        for arm in arms:
            try:
                if arm.get("type") == "circle":
                    o = self.map_widget.set_polygon(
                        circle_pts(arm["lat"], arm["lon"], arm["radius_m"]),
                        fill_color=None, outline_color=col, border_width=2)
                    self.corner_arm_overlays.append(o)
                else:
                    pts = arm.get("pts") or []
                    if len(pts) < 2: continue
                    pts_ll = [(p[0], p[1]) for p in pts]
                    left, right = self._offset_path_latlon(pts_ll, excl_m)
                    _draw_clipped(left, 2)
                    _draw_clipped(right, 2)
            except Exception:
                pass

    # ── Sprayer passes extras ──────────────────────────────────────────────────
    def _mode_edit_passes(self):
        self._close_all_popups()
        self._status("Sprayer pass editing: adjust Spray Angle or Sprayer Width in Field Details, then Toggle on/off to refresh.")

    # ── Planter passes extras ──────────────────────────────────────────────────
    # ── Shift dialogs / offsets ────────────────────────────────────────────────
    def _bay_shift(self):
        """(east, north) metres the planter bays + passes are offset by."""
        try:
            return (float(self.current_field.get("bay_shift_e_m") or 0),
                    float(self.current_field.get("bay_shift_n_m") or 0))
        except (ValueError, TypeError):
            return (0.0, 0.0)

    def _sprayer_shift(self):
        """(east, north) metres the sprayer passes are offset by."""
        try:
            return (float(self.current_field.get("sprayer_shift_e_m") or 0),
                    float(self.current_field.get("sprayer_shift_n_m") or 0))
        except (ValueError, TypeError):
            return (0.0, 0.0)

    @staticmethod
    def _shift_pt(lat, lon, d_e_m, d_n_m):
        """Translate a single (lat, lon) by (east, north) metres."""
        return (lat + d_n_m / 111111.0,
                lon + d_e_m / (111111.0 * math.cos(math.radians(lat))))

    @staticmethod
    def _field_combined_shift(f):
        """Total (east, north) metres shelters move by — planter (bay) shift +
        sprayer-pass shift — read from a field dict."""
        def _g(k):
            try: return float(f.get(k) or 0)
            except (ValueError, TypeError): return 0.0
        return (_g("bay_shift_e_m") + _g("sprayer_shift_e_m"),
                _g("bay_shift_n_m") + _g("sprayer_shift_n_m"))

    @staticmethod
    def _translate_latlon(passes, d_e_m, d_n_m):
        """Translate each [(lat,lon),...] polyline by (east, north) metres."""
        if not d_e_m and not d_n_m:
            return [[(float(p[0]), float(p[1])) for p in poly]
                    for poly in passes if poly and len(poly) >= 2]
        out = []
        for poly in passes:
            np = []
            for pt in poly:
                try:
                    lat, lon = float(pt[0]), float(pt[1])
                except (TypeError, ValueError, IndexError):
                    continue
                nlat = lat + d_n_m / 111111.0
                nlon = lon + d_e_m / (111111.0 * math.cos(math.radians(lat)))
                np.append((nlat, nlon))
            if len(np) >= 2:
                out.append(np)
        return out

    def _format_shift(self, e_m, n_m):
        """Human string for a shift vector in the current unit, e.g. '10 ft W'
        or '10 ft W + 5 ft N', or '0 ft' when there is no shift."""
        use_m = self.unit_var.get() == "Metric"
        unit = "m" if use_m else "ft"
        conv = 1.0 if use_m else 1.0 / 0.3048
        parts = []
        if abs(e_m) > 1e-6:
            parts.append(f"{round(abs(e_m)*conv,1):g} {unit} {'E' if e_m > 0 else 'W'}")
        if abs(n_m) > 1e-6:
            parts.append(f"{round(abs(n_m)*conv,1):g} {unit} {'N' if n_m > 0 else 'S'}")
        return " + ".join(parts) if parts else f"0 {unit}"

    def _open_shift_dialog(self, title, heading, apply_fn, current_shift=(0.0, 0.0)):
        """Generic N/E/S/W + distance shift dialog. apply_fn(d_e_m, d_n_m).
        current_shift = (east_m, north_m) already applied, shown at the top."""
        self._close_all_popups()
        use_m = self.unit_var.get() == "Metric"
        unit = "m" if use_m else "ft"
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.grab_set()
        ctk.CTkLabel(win, text=heading,
                     font=ctk.CTkFont(family=FONT_HEADING, size=15)).pack(padx=24, pady=(18, 2))
        ctk.CTkLabel(win, text=f"Currently shifted: {self._format_shift(*current_shift)}",
                     text_color=UI_MUTED,
                     font=ctk.CTkFont(size=12)).pack(padx=24, pady=(0, 8))
        dir_var = tk.StringVar(value="N")
        drow = ctk.CTkFrame(win, fg_color="transparent"); drow.pack(padx=24, pady=2)
        ctk.CTkLabel(drow, text="Direction:", width=70, anchor="w").pack(side="left")
        for d in ("N", "E", "S", "W"):
            ctk.CTkRadioButton(drow, text=d, variable=dir_var, value=d,
                               width=46).pack(side="left", padx=2)
        crow = ctk.CTkFrame(win, fg_color="transparent"); crow.pack(padx=24, pady=(8, 4))
        ctk.CTkLabel(crow, text="Distance:", width=70, anchor="w").pack(side="left")
        dist_var = tk.StringVar(value="")
        ctk.CTkEntry(crow, textvariable=dist_var, width=90).pack(side="left", padx=(2, 4))
        ctk.CTkLabel(crow, text=unit, width=24, anchor="w").pack(side="left")
        def do_shift():
            try:
                dist = float(dist_var.get().strip())
            except ValueError:
                self._status("Enter a distance."); return
            if dist == 0:
                win.destroy(); return
            dist_m = dist if use_m else dist * 0.3048
            d = dir_var.get()
            d_e = d_n = 0.0
            if   d == "N": d_n = dist_m
            elif d == "S": d_n = -dist_m
            elif d == "E": d_e = dist_m
            elif d == "W": d_e = -dist_m
            apply_fn(d_e, d_n)
            win.destroy()
            self._status(f"Shifted {dist:g} {unit} {d}.")
        ctk.CTkButton(win, text="Shift", height=36, command=do_shift).pack(
            fill="x", padx=24, pady=(8, 4))
        ctk.CTkButton(win, text="Cancel", height=36, fg_color="#555",
                      command=win.destroy).pack(fill="x", padx=24, pady=(0, 18))
        _center_on_parent(win, self)

    def _mode_shift_planter(self):
        """Shift the planter bays and planter pass lines by a compass direction +
        distance. Stored as a draw-time offset, so it works whether the bays come
        from imported passes or the synthetic bay grid."""
        self._open_shift_dialog("Shift Planter / Bays",
                                "Shift planter passes & bays",
                                self._apply_planter_shift,
                                current_shift=self._bay_shift())

    def _apply_planter_shift(self, d_e_m, d_n_m):
        se, sn = self._bay_shift()
        self.current_field["bay_shift_e_m"] = se + d_e_m
        self.current_field["bay_shift_n_m"] = sn + d_n_m
        self._shelter_undo.append(("shift", "planter", d_e_m, d_n_m))
        self._redraw_planter_shift_layers()

    def _mode_shift_sprayer(self):
        """Shift the sprayer pass lines by a compass direction + distance. The
        outside sprayer pass (inset from the boundary) is NOT moved — it's tied
        to the field boundary."""
        self._open_shift_dialog("Shift Sprayer Passes",
                                "Shift sprayer passes",
                                self._apply_sprayer_shift,
                                current_shift=self._sprayer_shift())

    def _apply_sprayer_shift(self, d_e_m, d_n_m):
        se, sn = self._sprayer_shift()
        self.current_field["sprayer_shift_e_m"] = se + d_e_m
        self.current_field["sprayer_shift_n_m"] = sn + d_n_m
        self._shelter_undo.append(("shift", "sprayer", d_e_m, d_n_m))
        self._redraw_sprayer_shift_layers()

    def _redraw_planter_shift_layers(self):
        if self.show_bays.get():     self._redraw_bays()
        self._redraw_planter_passes()
        if self.show_shelters.get(): self._redraw_shelters()

    def _redraw_sprayer_shift_layers(self):
        if self.show_passes.get():   self._redraw_passes()
        self._redraw_sprayer_passes()
        # Tire / green edge-band overlay moves with the interior passes.
        self._redraw_pass_buffer_overlay()
        if self.show_shelters.get(): self._redraw_shelters()

    # ── Imported planter passes (JD Operations Center Seeding shapefile) ─────
    def _import_planter_data(self):
        """File-picker for a JD Seeding shapefile. Parses it, stores the
        reconstructed pass polylines on the current field, and draws them
        as faint blue lines on the map."""
        self._close_all_popups()
        path = tkinter.filedialog.askopenfilename(
            title="Import JD Planter Data (.shp)",
            filetypes=[("Shapefile", "*.shp"), ("All files", "*.*")])
        if not path: return
        try:
            from maketentgrid import parse_jd_seeding_shapefile
            passes = parse_jd_seeding_shapefile(path)
        except Exception as ex:
            tkinter.messagebox.showerror("Import Error",
                f"Couldn't parse {Path(path).name}:\n{ex}")
            return
        if not passes:
            tkinter.messagebox.showwarning("Import",
                "No passes found in that shapefile. Is it a JD Seeding export?")
            return
        # Store as plain JSON-safe lists so it survives save_field round-trip.
        self.current_field["planter_passes"] = [
            [[lat, lon] for lat, lon in p] for p in passes
        ]
        fname = Path(path).name
        self.current_field["planter_file_name"] = fname
        self._planter_file_var.set(fname)
        self._use_planter_cb.configure(state="normal")
        self.use_imported_passes_var.set(True)
        self.show_planter_passes.set(True)
        self._redraw_planter_passes()
        n_pts = sum(len(p) for p in passes)
        self._status(f"Imported {len(passes)} passes ({n_pts:,} samples) from {fname}.")

    def _clear_planter_passes(self):
        """Remove the imported planter data from this field."""
        self._close_all_popups()
        self.current_field["planter_passes"] = None
        self.current_field["planter_file_name"] = None
        self._planter_file_var.set("")
        self._use_planter_cb.configure(state="disabled")
        self.use_imported_passes_var.set(False)
        self.show_planter_passes.set(False)
        self._redraw_planter_passes()
        self._status("Planter data cleared.")

    def _toggle_planter_passes(self):
        """Show/hide the imported planter-pass polylines."""
        self._close_all_popups()
        self.show_planter_passes.set(not self.show_planter_passes.get())
        self._redraw_planter_passes()
        self._status("Planter paths " +
                     ("shown." if self.show_planter_passes.get() else "hidden."))

    def _toggle_bays_through_inner(self):
        """Toggle whether the bay overlay clips at every inner cutout (default,
        the same behaviour as pivot tracks) or draws straight through them.
        Some fields have small interior boundaries that are still being
        planted through (e.g. an access lane that the planter just drives
        over) — for those the user wants bays continuous instead of broken
        at the cutout edge."""
        self._close_all_popups()
        cur = bool(self.current_field.get("bays_through_inner", False))
        self.current_field["bays_through_inner"] = not cur
        if self.show_bays.get():
            self._redraw_bays()
        self._status("Planter bays " +
                     ("draw through" if not cur else "stop at") +
                     " inner boundaries.")

    def _redraw_planter_passes(self):
        """Tear down old overlays and redraw if the layer is visible AND the
        current field has imported pass data."""
        for o in self.planter_path_overlays:
            try: o.delete()
            except Exception: pass
        self.planter_path_overlays = []
        if not self.show_planter_passes.get(): return
        passes = self.current_field.get("planter_passes") or []
        bse, bsn = self._bay_shift()
        passes = self._translate_latlon(passes, bse, bsn)   # honour planter Shift
        for poly in passes:
            if not poly or len(poly) < 2: continue
            try:
                p = self.map_widget.set_path(
                    [(lat, lon) for lat, lon in poly],
                    color="#1E90FF", width=1)
                self.planter_path_overlays.append(p)
            except Exception:
                pass

    # ── Uploaded sprayer passes ────────────────────────────────────────────────
    def _import_sprayer_data(self):
        """File-picker for a sprayer GPS file (.shp or .geojson).
        Parses passes, stores them on the current field, and draws them as
        orange polylines. Off by default — turns on automatically after import."""
        self._close_all_popups()
        path = tkinter.filedialog.askopenfilename(
            title="Import Sprayer Data",
            filetypes=[("Shapefile / GeoJSON", "*.shp *.geojson *.json"),
                       ("All files", "*.*")])
        if not path: return
        try:
            from maketentgrid import parse_sprayer_shapefile
            passes = parse_sprayer_shapefile(path)
        except Exception as ex:
            tkinter.messagebox.showerror("Import Error",
                f"Couldn't parse {Path(path).name}:\n{ex}")
            return
        if not passes:
            tkinter.messagebox.showwarning("Import",
                "No pass lines found in that file.")
            return
        self.current_field["sprayer_passes"] = [
            [[lat, lon] for lat, lon in p] for p in passes
        ]
        self.show_sprayer_passes.set(True)
        self._redraw_sprayer_passes()
        n_pts = sum(len(p) for p in passes)
        self._status(f"Imported {len(passes)} sprayer passes ({n_pts:,} pts) "
                     f"from {Path(path).name}.")

    def _clear_sprayer_data(self):
        """Remove uploaded sprayer pass data from this field."""
        self._close_all_popups()
        self.current_field["sprayer_passes"] = None
        self.show_sprayer_passes.set(False)
        self._redraw_sprayer_passes()
        self._status("Sprayer uploaded paths cleared.")

    def _toggle_sprayer_passes(self):
        """Show/hide the uploaded sprayer-pass polylines."""
        self._close_all_popups()
        passes = self.current_field.get("sprayer_passes") or []
        if not passes:
            self._status("No sprayer data uploaded yet — use Import Sprayer Data first.")
            return
        self.show_sprayer_passes.set(not self.show_sprayer_passes.get())
        self._redraw_sprayer_passes()
        self._status("Sprayer uploaded paths " +
                     ("shown." if self.show_sprayer_passes.get() else "hidden."))

    def _redraw_sprayer_passes(self):
        """Tear down old overlays and redraw if the layer is visible and data exists."""
        for o in self.sprayer_path_overlays:
            try: o.delete()
            except Exception: pass
        self.sprayer_path_overlays = []
        if not self.show_sprayer_passes.get(): return
        passes = self.current_field.get("sprayer_passes") or []
        sse, ssn = self._sprayer_shift()
        passes = self._translate_latlon(passes, sse, ssn)   # honour sprayer Shift
        for poly in passes:
            if not poly or len(poly) < 2: continue
            try:
                p = self.map_widget.set_path(
                    [(lat, lon) for lat, lon in poly],
                    color="#FF8C00", width=1)
                self.sprayer_path_overlays.append(p)
            except Exception:
                pass

    # ── Shelter preview ────────────────────────────────────────────────────────
    def _toggle_shelters(self):
        self.show_shelters.set(not self.show_shelters.get())
        if self.show_shelters.get():
            self._redraw_shelters(); self._status("Shelter pins shown.")
        else:
            self._clear_shelters(); self._status("Shelter pins hidden.")

    def _mode_add_shelter(self):
        """Click-to-add-pins mode. Each click drops an EXTRA shelter pin that
        is added on top of the existing (algorithm or manual) pins — the pins
        already on the map are left untouched. Drag to reposition, click to
        delete. ✔ Done to exit."""
        self._close_all_popups()
        self.click_mode = "add_shelter"
        self.show_shelters.set(True)
        self._redraw_shelters()
        self._show_context_btn("✔ Done Adding Pins", self._close_add_shelter)
        n = len(self.current_field.get("manual_shelter_pins") or [])
        if n:
            self._status(f"Click map to add extra shelter pins ({n} already added). "
                         "Drag a pin to move it, click a pin to delete it. ✔ Done when finished.")
        else:
            self._status("Click map to add extra shelter pins on top of the existing ones. "
                         "Drag a pin to move it, click a pin to delete it. ✔ Done when finished.")

    def _close_add_shelter(self):
        self.click_mode = None
        self._hide_context_btn()
        n = len(self.current_field.get("manual_shelter_pins") or [])
        self._status(f"{n} extra pin(s) added." if n else "Done adding pins.")

    def _set_pin_mode(self, mode):
        """Pin labels: 'trays' (tray count), 'shelters' (sequential #), or 'off'."""
        self.pin_label_mode = mode
        if self.show_shelters.get():
            self._redraw_shelters()
        self._status({"trays":"Pins show tray counts.",
                      "shelters":"Pins show shelter numbers.",
                      "off":"Pin numbers off."}.get(mode,""))

    def _clear_shelters(self):
        self._unregister_drag_prefix("shelter_")
        self._unregister_drag_prefix("manualpin_")
        for m in self.shelter_markers:
            try: m.delete()
            except Exception: pass
        self.shelter_markers=[]
        for p in self.shelter_circle_polys:
            try: p.delete()
            except Exception: pass
        self.shelter_circle_polys=[]

    def _toggle_shelter_buffers(self):
        turning_on = not self.shelter_circle_var.get()
        if turning_on:
            try: cur=float(self.current_field.get("shelter_buffer_m") or 0)
            except (ValueError,TypeError): cur=0.0
            if cur<=0:
                # No buffer size set yet — prompt for one first. _edit_shelter_buffer
                # turns the toggle on itself once a value > 0 is entered (and redraws).
                self._edit_shelter_buffer()
                return
        self.shelter_circle_var.set(turning_on)
        if self.show_shelters.get(): self._redraw_shelters()
        self._status("Buffer zone " + ("shown." if self.shelter_circle_var.get() else "hidden."))

    def _edit_shelter_buffer(self):
        self._close_all_popups()
        use_m=self.unit_var.get()=="Metric"
        unit="m" if use_m else "ft"
        cur_m=float(self.current_field.get("shelter_buffer_m") or 0)
        cur_disp=cur_m if use_m else cur_m/0.3048
        val=self._ask_string("Shelter Buffer",
            f"Buffer radius around each shelter ({unit}).  0 = no buffer.  Current: {cur_disp:g}")
        if val is None: return
        try: v=float(val.strip())
        except ValueError: self._status("Enter a number (0 for none)."); return
        if v<0: v=0
        self.current_field["shelter_buffer_m"]=str(v if use_m else v*0.3048)
        if v>0: self.shelter_circle_var.set(True)
        if self.show_shelters.get(): self._redraw_shelters()
        self._status(f"Shelter buffer set to {v:g} {unit}." if v>0 else "Shelter buffer removed (0).")

    def _record_shelter_change(self, idx):
        """Snapshot the override for this shelter before changing it, so Reset
        Move can step back through individual moves/deletes one at a time."""
        overrides=self.current_field.setdefault("shelter_overrides",{})
        key=str(idx)
        prev=overrides[key] if key in overrides else _UNDO_MISSING
        self._shelter_undo.append(("shelter",key,prev))

    def _undo_one_shift(self, which, d_e, d_n):
        """Reverse a single recorded planter/sprayer shift."""
        if which == "planter":
            e, n = self._bay_shift()
            self.current_field["bay_shift_e_m"] = e - d_e
            self.current_field["bay_shift_n_m"] = n - d_n
            self._redraw_planter_shift_layers()
        else:  # sprayer
            e, n = self._sprayer_shift()
            self.current_field["sprayer_shift_e_m"] = e - d_e
            self.current_field["sprayer_shift_n_m"] = n - d_n
            self._redraw_sprayer_shift_layers()

    def _undo_shelter_move(self):
        """Revert the most recent move/delete OR shift; repeat to step further back.

        When the in-session history is empty but the field still carries saved
        manual moves (shelter_overrides) from a previous session, offer to clear
        them all — otherwise stale moves (made before a placement change) can
        leave a shelter clumped onto a neighbour and a gap where it came from."""
        if not self._shelter_undo:
            ov = self.current_field.get("shelter_overrides") or {}
            if ov:
                if tkinter.messagebox.askyesno("Reset Moves",
                        f"No in-session changes to undo, but this field has "
                        f"{len(ov)} saved shelter move(s)/delete(s).\n\n"
                        f"Clear them so every shelter returns to its calculated "
                        f"position?"):
                    self.current_field["shelter_overrides"] = {}
                    if self.show_shelters.get(): self._redraw_shelters()
                    self._status(f"Cleared {len(ov)} saved shelter move(s).")
                    self._save_field()   # persist immediately so reopen sees the reset
                return
            self._status("Nothing to undo."); return
        entry=self._shelter_undo.pop()
        if entry[0]=="shift":
            _,which,d_e,d_n=entry
            self._undo_one_shift(which,d_e,d_n)
            what=f"{which} shift"
        else:
            # ("shelter", key, prev)  — also tolerate the legacy (key, prev) form
            if len(entry)==3: _,key,prev=entry
            else: key,prev=entry
            overrides=self.current_field.setdefault("shelter_overrides",{})
            if prev is _UNDO_MISSING:
                overrides.pop(key,None)
            else:
                overrides[key]=prev
            if self.show_shelters.get(): self._redraw_shelters()
            what="move"
        n=len(self._shelter_undo)
        self._status(f"Reverted last {what}." + (f" {n} earlier change(s) remain." if n else " No more to undo."))
        self._save_field()   # persist each undo step so reopen sees the current state

    def _on_shelter_tap(self, idx):
        """Called when a shelter pin is clicked without dragging — highlight then offer delete."""
        drag_key = f"shelter_{idx}"
        info = self._drag_registry.get(drag_key)
        hl_oval = None
        marker = info.get('marker') if info else None
        if marker and not getattr(marker,'deleted',True):
            try:
                cx,cy = marker.get_canvas_pos(marker.position)
                hl_oval=self.map_widget.canvas.create_oval(
                    cx-16,cy-47,cx+16,cy-15,
                    fill="#FF6600",outline="#FF0000",width=2,tags="shelter_hl")
                self.map_widget.canvas.update()
            except Exception: pass
        ans=tkinter.messagebox.askyesno("Delete Shelter",f"Delete shelter #{idx+1}?")
        if hl_oval:
            try: self.map_widget.canvas.delete(hl_oval)
            except Exception: pass
        if ans:
            self._delete_shelter(idx)

    def _delete_shelter(self, idx):
        mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(), "total")
        if mode == "manual":
            pins = self.current_field.get("manual_shelter_pins") or []
            if 0 <= idx < len(pins):
                pins.pop(idx)
            self._redraw_shelters()
            self._status(f"Manual pin deleted.")
        else:
            self._record_shelter_change(idx)
            overrides = self.current_field.setdefault("shelter_overrides", {})
            overrides[str(idx)] = None
            self._redraw_shelters()
            self._status(f"Shelter #{idx+1} deleted — ↶ Reset Move to undo.")

    def _on_shelter_drag(self, idx, lat, lon):
        mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(), "total")
        if mode == "manual":
            pins = self.current_field.get("manual_shelter_pins") or []
            if 0 <= idx < len(pins):
                pins[idx] = [lat, lon]
        else:
            self._record_shelter_change(idx)
            overrides = self.current_field.setdefault("shelter_overrides", {})
            overrides[str(idx)] = [lat, lon]
            self._status(f"Shelter #{idx+1} moved — ↶ Reset Move to undo.")
        self._redraw_shelters()

    # ── Extra (manual) pin handlers — additive pins, mutate manual_shelter_pins ──
    def _on_manualpin_drag(self, idx, lat, lon):
        pins = self.current_field.get("manual_shelter_pins") or []
        if 0 <= idx < len(pins):
            pins[idx] = [lat, lon]
        self._redraw_shelters()

    def _delete_manualpin(self, idx):
        pins = self.current_field.get("manual_shelter_pins") or []
        if 0 <= idx < len(pins):
            pins.pop(idx)
        self._redraw_shelters()
        self._status("Extra pin deleted.")

    def _on_manualpin_tap(self, idx):
        """Click an extra pin → confirm + delete."""
        if tkinter.messagebox.askyesno("Delete Pin", "Delete this extra shelter pin?"):
            self._delete_manualpin(idx)

    def _redraw_shelters(self):
        self._clear_shelters()
        if not self.show_shelters.get(): return
        f=self._field_from_form()
        use_m=self.unit_var.get()=="Metric"
        mode_key=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        positions, row_idxs = maketentgrid.get_tent_positions(f,use_metric=use_m,return_rows=True)
        # Shelters follow the planter (bay) Shift and the sprayer-pass Shift so
        # they stay in their female bays / clear of the shifted passes. Applied
        # to the algorithm grid only — manual pins and dragged overrides are
        # absolute placements and stay put.
        sse, ssn = self._field_combined_shift(self.current_field)
        if (sse or ssn) and positions:
            positions = [self._shift_pt(la, lo, sse, ssn) for la, lo in positions]
        overrides=self.current_field.get("shelter_overrides") or {}
        merged=list(positions)
        deleted=set()
        for k,v in overrides.items():
            try:
                idx=int(k)
                if 0<=idx<len(merged):
                    if v is None:
                        deleted.add(idx)
                    else:
                        merged[idx]=tuple(v)
            except (ValueError,TypeError): pass

        # Manual pins are ADDITIVE on top of the algorithm grid — they let the
        # user drop a few extra shelters without discarding the calculated ones.
        # In "Manual pins only" mode get_tent_positions already returned them as
        # `positions`, so we don't add them a second time.
        manual_pins=[] if mode_key=="manual" else list(self.current_field.get("manual_shelter_pins") or [])

        # Unified visible list: (lat, lon, kind, ident, row).  kind is "algo"
        # (ident = original merged index → shelter_overrides) or "manual"
        # (ident = index into manual_shelter_pins).
        visible=[]
        for i,(lat,lon) in enumerate(merged):
            if i in deleted: continue
            r=row_idxs[i] if (row_idxs and i<len(row_idxs)) else 0
            visible.append((lat,lon,"algo",i,r))
        for j,pt in enumerate(manual_pins):
            try: lat,lon=float(pt[0]),float(pt[1])
            except (TypeError,ValueError,IndexError): continue
            visible.append((lat,lon,"manual",j,-1))

        if not visible:
            self._status("⚠ No shelter positions — check field details and boundary.")
            self.shelter_positions=[]; self.shelter_tray_counts=[]
            self._refresh_bee_summary()
            return

        # Re-sort visible by NW-snake order (W→E columns, alternating N↔S
        # within each column) based on current lat/lon.  This renumbers dragged
        # and manually-added pins into the correct position rather than keeping
        # their original algorithm index, matching what get_tent_positions does.
        try:
            _plat = float(f.get("PP_Latitude") or 0)
            _plon = float(f.get("PP_Longitude") or 0)
            if _plat and _plon:
                _ang = float(f.get("Planting_angle") or f.get("Spray_angle") or 0)
                _cos_r = math.cos(math.radians(_ang))
                _sin_r = math.sin(math.radians(_ang))
                # Column width: matches get_tent_positions bay logic (2× gap_m).
                try:
                    _rs   = float(f.get("row_spacing_in") or 22)
                    _nf   = int(float(f.get("num_female_rows") or 0))
                    _nm   = int(float(f.get("num_male_rows")  or 0))
                    _gap  = float(f.get("bay_gap_in") or 0) * 0.0254
                    _col_w = (_nf+1)*_rs*0.0254 + (_nm+1)*_rs*0.0254 + 2.0*_gap
                except (ValueError, TypeError):
                    _col_w = 0.0
                if _col_w <= 0:
                    try: _col_w = float(f.get("Sprayer_width") or 0) * 0.3048
                    except (ValueError, TypeError): _col_w = 0.0
                if _col_w <= 0:
                    _col_w = 10.0
                _first_desc = (-_sin_r + _cos_r) > 0  # travel_nw > 0

                def _snake_key(entry):
                    la, lo = entry[0], entry[1]
                    e, n = latlon_to_enu(la, lo, _plat, _plon)
                    lat_v =  e * _cos_r + n * _sin_r   # lateral (E→W field axis)
                    trn_v = -e * _sin_r + n * _cos_r   # transverse (N→S field axis)
                    col = round(lat_v / _col_w)
                    desc = (col%2==0 and _first_desc) or (col%2==1 and not _first_desc)
                    return (col, -trn_v if desc else trn_v)

                visible.sort(key=_snake_key)
        except Exception:
            pass  # on any error keep original order

        vis_positions=[(v[0],v[1]) for v in visible]
        vis_rows=[v[4] for v in visible]
        n_visible=len(visible)
        self.shelter_positions=list(vis_positions)
        total_trays, per, short, _ = self._compute_bee_distribution(
            n_visible, vis_rows, shelter_positions_latlon=vis_positions)
        self.shelter_tray_counts=list(per) if per else [0]*n_visible
        mode=self.pin_label_mode
        try: BUFFER_M=float(self.current_field.get("shelter_buffer_m") or 0)
        except (ValueError,TypeError): BUFFER_M=0.0
        show_circles=self.shelter_circle_var.get() and BUFFER_M>0   # 0 size = no buffer
        for seq,(lat,lon,kind,ident,row) in enumerate(visible):
            cc="#FFD700"; oc="#B8860B"
            if mode=="shelters":
                lbl=str(seq+1)
            elif mode=="trays" and per:
                lbl=str(per[seq])
            else:
                lbl=""
            if kind=="manual":
                drag_key=f"manualpin_{ident}"
                drag_cb=(lambda la,lo,j=ident: self._on_manualpin_drag(j,la,lo))
            else:
                drag_key=f"shelter_{ident}"
                drag_cb=(lambda la,lo,i=ident: self._on_shelter_drag(i,la,lo))
            try:
                m=self.map_widget.set_marker(lat,lon,text=lbl,
                                              marker_color_circle=cc,
                                              marker_color_outside=oc,
                                              text_color="#000000",
                                              font=(FONT_LABEL,11))
                # Position text at the circle center (canvas_y - 31) AND switch
                # the canvas-text anchor from "south" to "center" so the visual
                # midpoint of the digit sits exactly at the circle midpoint.
                # Patch the marker's draw() so the anchor stays "center" when
                # tkintermapview redraws on pan / zoom.
                if lbl:
                    m.text_y_offset = -31
                    canvas = self.map_widget.canvas
                    _orig_draw = m.draw
                    def _draw_centered(event=None, _m=m, _c=canvas, _od=_orig_draw):
                        _od(event)
                        if _m.canvas_text:
                            try: _c.itemconfig(_m.canvas_text, anchor="center")
                            except Exception: pass
                    m.draw = _draw_centered
                    try: m.draw()
                    except Exception: pass
                self.shelter_markers.append(m)
                self._register_drag(drag_key,lat,lon,lbl,cc,oc,drag_cb,marker=m)
            except Exception: pass
            if show_circles:
                try:
                    p=self.map_widget.set_polygon(
                        circle_pts(lat,lon,BUFFER_M,n=36),
                        fill_color=None,outline_color="#FF4400",border_width=1)
                    self.shelter_circle_polys.append(p)
                except Exception: pass
        # Status line: trays when bee math is filled in, fall back to shelter count
        if per:
            counts={}
            for tc in per:
                counts[tc]=counts.get(tc,0)+1
            parts=[f"{c}×{tc}" for tc,c in sorted(counts.items(),reverse=True)]
            msg=f"{total_trays} trays: {' + '.join(parts)} ({n_visible} shelters)"
            if short>0:
                msg+=f" — short {short}"
            self._status(msg)
        else:
            self._status(f"{n_visible} shelters displayed.")
        self._refresh_bee_summary()

    # ── Sprayer pass overlay ───────────────────────────────────────────────────
    def _toggle_passes(self):
        self._close_all_popups()
        self.show_passes.set(not self.show_passes.get())
        if self.show_passes.get():
            self._redraw_passes()
        else:
            self._clear_passes()

    def _clear_passes(self):
        for p in self.pass_paths:
            try: p.delete()
            except Exception: pass
        self.pass_paths=[]
        if self.outer_sprayer_poly:
            try: self.outer_sprayer_poly.delete()
            except Exception: pass
            self.outer_sprayer_poly=None

    def _redraw_passes(self):
        self._clear_passes()
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            _spray = self.fv["Spray_angle"].get().strip()
            _plant = self.fv["Planting_angle"].get().strip()
            angle=float(_spray or _plant or 0)
            width_ft=float(self.fv["Sprayer_width"].get() or 133)
            width_m=width_ft*0.3048
            bp=self.current_field.get("boundary_polygon")
        except (ValueError,TypeError): return
        if not bp or len(bp)<3: return

        poly_enu=[(latlon_to_enu(lat,lon,plat,plon)) for lat,lon in bp]
        max_r=max(math.sqrt(e*e+n*n) for e,n in poly_enu)*1.1

        if not self.show_passes.get(): return

        # Outer sprayer limit (one sprayer-width inset from boundary) — always
        # drawn when a boundary exists; the outside round is always part of the
        # sprayer operation.
        if bp and len(bp) >= 3:
            inset=inset_polygon_enu(poly_enu,width_m)
            if len(inset)>=3:
                lpts=[enu_to_latlon(e,n,plat,plon) for e,n in inset]
                try:
                    self.outer_sprayer_poly=self.map_widget.set_polygon(
                        lpts,fill_color=None,outline_color="#33FF66",border_width=2)
                except Exception: pass

        rot=math.radians((0-angle+180)%360-180)
        cos_r,sin_r=math.cos(rot),math.sin(rot)
        tdx=-sin_r; tdy=cos_r
        ldx, ldy = cos_r, sin_r   # lateral direction (across rows)
        # Sprayer Shift offset — moves the pass lines only (the outside sprayer
        # limit above is tied to the boundary and is intentionally not shifted).
        sse, ssn = self._sprayer_shift()

        # Inner boundaries (cutouts) in ENU. When the
        # "sprayer_routes_around_inner" flag is on, every pass line is split
        # at the cutouts so it shows the sprayer driving around them instead
        # of straight through.
        route_around = bool(self.current_field.get("sprayer_routes_around_inner", True))
        inner_polys_enu = []
        if route_around:
            for inner in (self.current_field.get("boundary_inner") or []):
                if not inner or len(inner) < 3: continue
                inner_polys_enu.append(
                    [latlon_to_enu(pt[0], pt[1], plat, plon) for pt in inner])

        # Passes that lie entirely within the outside round zone are skipped —
        # the outside round already covers them so drawing them again is
        # misleading. A pass edge at x is "inside the round" if it never
        # crosses inset_polygon_enu(poly_enu, width_m).
        outside_round_inner = inset_polygon_enu(poly_enu, width_m)

        max_rows=int(max_r/width_m)+2
        for r in range(-max_rows,max_rows+1):
            lat_e=r*width_m; lat_n=0
            pe=lat_e*cos_r-lat_n*sin_r; pn=lat_n*cos_r+lat_e*sin_r
            # Skip a pass-edge line only when BOTH passes it borders lie fully
            # inside the outside round. Sampling the span on both sides of the
            # edge (not just the single edge line) keeps passes that only
            # partly poke out of the round and still spray uncovered interior.
            if len(outside_round_inner) >= 3:
                _covers = False
                for _i in range(13):
                    _cx = lat_e - width_m + (2.0 * width_m) * _i / 12.0
                    if clip_line_to_polygon_intervals(
                            _cx * ldx, _cx * ldy, tdx, tdy, outside_round_inner):
                        _covers = True; break
                if not _covers:
                    continue   # both bordering passes fully inside the round
            pe+=sse; pn+=ssn   # apply sprayer Shift (lateral part moves passes)

            # All inside-segments of the pass line against the outer boundary.
            # On non-convex fields a single line can enter, exit, and re-enter
            # the polygon — the old single-interval clip dropped the gap, so
            # the pass appeared to draw straight across open ground past the
            # field edge. Multi-interval clipping draws each inside-segment
            # separately and breaks at every gap.
            t_intervals = clip_line_to_polygon_intervals(pe, pn, tdx, tdy, poly_enu)
            if not t_intervals: continue
            # Subtract every inner cutout (one inner ring at a time).
            for inner_enu in inner_polys_enu:
                inner_intervals = clip_line_to_polygon_intervals(
                    pe, pn, tdx, tdy, inner_enu)
                if not inner_intervals: continue
                for (ti1, ti2) in inner_intervals:
                    new_intervals = []
                    for (a, b) in t_intervals:
                        if ti2 <= a or ti1 >= b:
                            new_intervals.append((a, b))   # no overlap
                        else:
                            if ti1 > a: new_intervals.append((a, ti1))
                            if ti2 < b: new_intervals.append((ti2, b))
                    t_intervals = new_intervals
            for (t1, t2) in t_intervals:
                if t2 - t1 < 0.01: continue   # skip degenerate slivers
                e1, n1 = pe + t1 * tdx, pn + t1 * tdy
                e2, n2 = pe + t2 * tdx, pn + t2 * tdy
                lat1, lon1 = enu_to_latlon(e1, n1, plat, plon)
                lat2, lon2 = enu_to_latlon(e2, n2, plat, plon)
                try:
                    path = self.map_widget.set_path(
                        [(lat1, lon1), (lat2, lon2)], color="#33FF66", width=2)
                    self.pass_paths.append(path)
                except Exception:
                    pass

    # ── Unit label refresh ─────────────────────────────────────────────────────
    def _on_unit_change(self,val=None):
        self._refresh_unit_labels()

    def _refresh_unit_labels(self):
        u=self.unit_var.get(); m=u=="Metric"; abb="m" if m else "ft"
        if "spacing" in self.field_labels:
            self.field_labels["spacing"].configure(text=f"Shelter Spacing ({abb})")
        if hasattr(self,"female_bay_lbl"):
            txt=self.female_bay_lbl.cget("text")
            if txt!="Female bay width: —": self._calc_bays()

    # ── Bay layout overlay ─────────────────────────────────────────────────────
    def _resolve_row_mask(self, nf, nm, layout, custom, total_rows=None):
        """Build the M/F-per-row mask string for the WHOLE planter.

        nf, nm are the female/male counts for one REPEAT UNIT. When
        total_rows > (nf+nm) the unit pattern repeats to fill the planter
        (e.g. 8F+2M centered = 'FFFFMMFFFF' (10 rows); on a 20-row planter
        the resolved mask is 'FFFFMMFFFFFFFFMMFFFF' = the unit × 2).

        layout:
          "outer"    → male rows split across both ends of the UNIT.
          "centered" → male rows as one block in the middle of the UNIT.
          "custom"   → user-supplied string of length total_rows (NOT
                       repeated — the user owns the whole pattern).
                       Invalid input falls back to centered.

        Returns a string of length total_rows (or nf+nm if total_rows is
        None/zero)."""
        unit = max(0, nf + nm)
        if unit == 0: return ""
        target = int(total_rows) if total_rows and int(total_rows) > 0 else unit
        if layout == "custom":
            s = "".join(c for c in (custom or "").upper() if c in "MF")
            if len(s) == target:
                return s
            layout = "centered"   # safety net
        if layout == "outer":
            left = nm // 2
            right = nm - left
            unit_mask = "M" * left + "F" * nf + "M" * right
        else:  # centered
            left_f = nf // 2
            right_f = nf - left_f
            unit_mask = "F" * left_f + "M" * nm + "F" * right_f
        # Repeat the unit to fill target rows. If target isn't an exact
        # multiple, repeat enough times then truncate so the mask length
        # always matches the planter exactly.
        if target == unit:
            return unit_mask
        copies = (target + unit - 1) // unit   # ceil divide
        return (unit_mask * copies)[:target]

    def _on_use_bays_toggle(self):
        """Crop-uses-bays checkbox flipped. Hides the female/male/layout
        widgets in blanket-planted mode (they don't apply), refreshes the
        bay overlay (no-op in blanket mode), and queues a shelter recompute
        so the new placement strategy takes effect immediately."""
        if self.use_bays_var.get():
            # Pack BEFORE the planter-pass-source row so the bay-only block
            # stays in its visual position.
            try: self._bay_only_frame.pack(fill="x")
            except Exception: pass
        else:
            try: self._bay_only_frame.pack_forget()
            except Exception: pass
        if self.show_bays.get():
            self._redraw_bays()
        self._on_form_change()   # debounced shelter recompute

    def _on_row_layout_change(self):
        """Dropdown changed — show/hide the custom-mask entry and refresh
        the mask preview label."""
        mode = self._row_layout_labels.get(self.row_layout_var.get(),"centered")
        try:
            if mode == "custom":
                self.custom_mask_entry.pack(fill="x",pady=(0,2))
            else:
                self.custom_mask_entry.pack_forget()
        except Exception: pass
        self._on_bay_change()   # schedule a recalc so the preview updates

    def _calc_bays(self):
        try:
            rs=float(self.fv["row_spacing_in"].get() or 22)
            total_rows=int(self.fv["total_rows"].get() or 20)
        except (ValueError,TypeError):
            self._status("Enter numeric values for row spacing and total rows."); return
        # Planter pass width — always shown, even in blanket-planted mode.
        planter_in = total_rows * rs
        planter_ft = planter_in / 12
        planter_m  = planter_ft * 0.3048
        use_m=self.unit_var.get()=="Metric"
        if use_m:
            self.planter_pass_lbl.configure(
                text=f'Planter pass: {total_rows} rows  ({planter_in:.1f}" = {planter_m:.3f} m)')
        else:
            self.planter_pass_lbl.configure(
                text=f'Planter pass: {total_rows} rows  ({planter_in:.1f}" = {planter_ft:.3f} ft)')

        # The rest only matters in bay mode — but we still resolve mask /
        # update the labels so the bay-only frame reads correctly when the
        # user toggles bays back on.
        try:
            nf=int(self.fv["num_female_rows"].get() or 8)
            nm=int(self.fv["num_male_rows"].get() or 2)
        except (ValueError,TypeError):
            nf, nm = 8, 2
        try: gap_in=float(self.fv["bay_gap_in"].get() or 0)
        except (ValueError,TypeError): gap_in=0.0
        if gap_in < 0: gap_in = 0.0
        unit = max(1, nf + nm)
        repeats = total_rows // unit if unit > 0 else 0
        leftover = total_rows - repeats * unit
        f_in=(nf+1)*rs; m_in=(nm+1)*rs
        f_ft=f_in/12; m_ft=m_in/12; f_m=f_ft*0.3048; m_m=m_ft*0.3048
        gap_ft=gap_in/12; gap_m=gap_ft*0.3048
        # Bay repeat period = female + male + a gap at EACH male/female edge
        # (two per period). Shown so the user can verify the spacing.
        period_in=f_in+m_in+2*gap_in; period_ft=period_in/12; period_m=period_ft*0.3048
        layout = self._row_layout_labels.get(self.row_layout_var.get(),"centered")
        mask = self._resolve_row_mask(nf, nm, layout, self.custom_mask_var.get(),
                                       total_rows=total_rows)
        # Repeat count + warning if total_rows isn't a clean multiple of unit.
        if leftover == 0:
            repeats_txt = f"Repeats: {repeats}  ({unit}-row unit × {repeats} = {total_rows})"
        else:
            repeats_txt = f"Repeats: {repeats} + {leftover} leftover rows (unit size {unit})"
        self.repeats_lbl.configure(text=repeats_txt)
        if use_m:
            self.female_bay_lbl.configure(text=f'Female bay: {f_in:.1f}" = {f_m:.3f} m')
            self.male_bay_lbl.configure(text=f'Male bay:   {m_in:.1f}" = {m_m:.3f} m')
            if gap_in > 0:
                self.bay_gap_lbl.configure(
                    text=f'Gap: {gap_in:.1f}" = {gap_m:.3f} m each edge  →  '
                         f'bay repeat {period_in:.1f}" = {period_m:.3f} m')
            else:
                self.bay_gap_lbl.configure(text="Gap: none")
        else:
            self.female_bay_lbl.configure(text=f'Female bay: {f_in:.1f}" = {f_ft:.3f} ft')
            self.male_bay_lbl.configure(text=f'Male bay:   {m_in:.1f}" = {m_ft:.3f} ft')
            if gap_in > 0:
                self.bay_gap_lbl.configure(
                    text=f'Gap: {gap_in:.1f}" = {gap_ft:.3f} ft each edge  →  '
                         f'bay repeat {period_in:.1f}" = {period_ft:.3f} ft')
            else:
                self.bay_gap_lbl.configure(text="Gap: none")
        self.row_mask_lbl.configure(text=f"Mask: {mask or '—'}")
        self._status(f"Bay layout: female {f_in:.0f}\" ({f_ft:.2f} ft), male {m_in:.0f}\" ({m_ft:.2f} ft); planter {total_rows} rows")
        if self.show_bays.get(): self._redraw_bays()
        if self.show_shelters.get(): self._redraw_shelters()

    def _toggle_bays(self):
        self._close_all_popups()
        self.show_bays.set(not self.show_bays.get())
        if self.show_bays.get():
            self._redraw_bays()
        else:
            self._clear_bays()

    def _clear_bays(self):
        for p in self.bay_polygons:
            try: p.delete()
            except Exception: pass
        self.bay_polygons=[]

    def _band_polygon_enu(self, x1, x2, tdx, tdy, ldx, ldy, poly_enu,
                           inner_polys_enu=None, off_e=0.0, off_n=0.0):
        """Clip a band (between lateral positions x1 and x2, travelling
        along (tdx, tdy)) to the outer polygon AND subtract every inner
        polygon. Returns a LIST of band polygons (one per inside-interval).

        Uses clip_line_to_polygon_intervals on both band edges and walks
        through the merged t-interval set, so a non-convex outer polygon
        (e.g. a field that wraps around a farmstead) gets multiple bay
        slices instead of one bounding rectangle that fills across the
        gap. Each interval is also subtracted by every inner polygon.

        (off_e, off_n) translates the band in ENU before clipping — used to
        apply the sprayer Shift so the pass overlay moves with the passes.
        The clip polygons stay fixed (tied to the boundary).
        """
        p1e, p1n = x1 * ldx + off_e, x1 * ldy + off_n
        p2e, p2n = x2 * ldx + off_e, x2 * ldy + off_n
        # Inside-intervals for each band edge.
        edge_a = clip_line_to_polygon_intervals(p1e, p1n, tdx, tdy, poly_enu)
        edge_b = clip_line_to_polygon_intervals(p2e, p2n, tdx, tdy, poly_enu)
        if not edge_a and not edge_b: return []
        # If one edge is entirely outside, fall back to the other (so we
        # don't drop a band just because its outer edge skims past).
        if not edge_a: edge_a = edge_b
        if not edge_b: edge_b = edge_a
        # No cutouts → build trapezoids whose END faces follow each band edge's
        # OWN entry/exit on the clip boundary. So a band that runs into the
        # boundary (or the outside-round tire that hugs it) stops AT THAT ANGLE
        # — a slanted cut matching the feature it meets — instead of a flat 90°
        # cut. (With cutouts we keep the simpler flat-interval path below.)
        if not inner_polys_enu:
            polys = []
            if len(edge_a) == len(edge_b):
                for (a0, a1), (b0, b1) in zip(edge_a, edge_b):
                    if min(a1, b1) - max(a0, b0) <= 1e-6: continue
                    polys.append([(p1e + a0*tdx, p1n + a0*tdy),
                                  (p2e + b0*tdx, p2n + b0*tdy),
                                  (p2e + b1*tdx, p2n + b1*tdy),
                                  (p1e + a1*tdx, p1n + a1*tdy)])
            else:
                # Edge interval counts disagree — happens when the clip polygon
                # self-intersects (a raw deep inset on a concave/finely-traced
                # boundary). Pairing the two edges then drops or garbles fill
                # pieces (the band shows its outline but no fill). Fall back to
                # clipping the band's CENTRELINE so the fill always renders
                # (flat ends instead of slanted, which is fine here).
                xm_e = (p1e + p2e) / 2.0; xm_n = (p1n + p2n) / 2.0
                for (t0, t1) in clip_line_to_polygon_intervals(xm_e, xm_n, tdx, tdy, poly_enu):
                    polys.append([(p1e + t0*tdx, p1n + t0*tdy),
                                  (p2e + t0*tdx, p2n + t0*tdy),
                                  (p2e + t1*tdx, p2n + t1*tdy),
                                  (p1e + t1*tdx, p1n + t1*tdy)])
            return polys
        # Pair up matching intervals between the two edges; intersect the i-th
        # interval of each edge to get the band's i-th inside-segment. If the
        # edge counts disagree (self-intersecting clip polygon), fall back to
        # the band centreline so the fill still renders.
        if len(edge_a) == len(edge_b):
            intervals = []
            for (a0, a1), (b0, b1) in zip(edge_a, edge_b):
                t0 = max(a0, b0); t1 = min(a1, b1)
                if t1 - t0 > 1e-6: intervals.append((t0, t1))
        else:
            xm_e = (p1e + p2e) / 2.0; xm_n = (p1n + p2n) / 2.0
            intervals = list(clip_line_to_polygon_intervals(xm_e, xm_n, tdx, tdy, poly_enu))
        if not intervals: return []
        # Subtract each inner polygon's intervals from the band's intervals.
        for inner_enu in (inner_polys_enu or []):
            sub_a = clip_line_to_polygon_intervals(p1e, p1n, tdx, tdy, inner_enu)
            sub_b = clip_line_to_polygon_intervals(p2e, p2n, tdx, tdy, inner_enu)
            inner_ts = []
            for (a0, a1), (b0, b1) in zip(sub_a, sub_b):
                # The cut is the UNION of the two edge's inner-intervals — be
                # conservative and remove anywhere either edge dips into the
                # cutout.
                inner_ts.append((min(a0, b0), max(a1, b1)))
            for (i0, i1) in inner_ts:
                new_intervals = []
                for (a, b) in intervals:
                    if i1 <= a or i0 >= b:
                        new_intervals.append((a, b))
                    else:
                        if i0 > a: new_intervals.append((a, i0))
                        if i1 < b: new_intervals.append((i1, b))
                intervals = new_intervals
        polys = []
        for (t0, t1) in intervals:
            polys.append([(p1e + t0*tdx, p1n + t0*tdy),
                          (p2e + t0*tdx, p2n + t0*tdy),
                          (p2e + t1*tdx, p2n + t1*tdy),
                          (p1e + t1*tdx, p1n + t1*tdy)])
        return polys

    def _clear_pass_buffer_overlay(self):
        for o in self.pass_buffer_overlays:
            try: o.delete()
            except Exception: pass
        self.pass_buffer_overlays = []

    def _redraw_pass_buffer_overlay(self):
        """Shelter-zone overlay:

          • SOLID RED 14 ft machine/tire band down the centre of every sprayer
            pass and the outside round (gated on the tire-zone toggle).
          • DIAGONAL GREEN STRIPES fill the shelter edge band near each pass
            edge — the good zone where shelters may sit. Drawn whenever a
            buffer is set, regardless of the tire-zone toggle. Bands are
            capped so they never overlap the tire zone or pivot track zones.
        """
        self._clear_pass_buffer_overlay()
        try:
            plat = float(self.fv["PP_Latitude"].get())
            plon = float(self.fv["PP_Longitude"].get())
            _spray = self.fv["Spray_angle"].get().strip()
            _plant = self.fv["Planting_angle"].get().strip()
            angle = float(_spray or _plant or 0)
            width_ft = float(self.fv["Sprayer_width"].get() or 133)
            width_m = width_ft * 0.3048
            buffer_ft = float(self.fv["pass_edge_buffer_ft"].get() or 0)
            buffer_m = buffer_ft * 0.3048
            tire_ft = float(self.fv["tire_width_ft"].get() or 14)
            tire_m = max(0.0, tire_ft) * 0.3048
            bp = self.current_field.get("boundary_polygon")
        except (ValueError, TypeError):
            return
        if not bp or len(bp) < 3 or width_m <= 0: return

        # Sprayer Shift (ENU metres) — the INTERIOR pass tires and their green
        # edge bands move with the passes. The outside round (its tire ring and
        # the continuous outer edge band) is tied to the boundary and does NOT
        # shift.
        sse, ssn = self._sprayer_shift()

        # Both the red tire stripes and the green good-zone bands are part of
        # the "Pass / Tire Zones" overlay and only render when it is toggled on
        # (off by default on field load). Previously the green band drew whenever
        # a buffer was set, which — now that every field defaults to a 25 ft edge
        # band — made it show on first open regardless of the toggle.
        overlay_on = self.show_pass_buffer_overlay.get()
        show_tire = overlay_on
        show_band = overlay_on and buffer_m > 0
        if not show_tire and not show_band: return

        poly_enu = [latlon_to_enu(lat, lon, plat, plon) for lat, lon in bp]
        max_r = max(math.sqrt(e*e + n*n) for e, n in poly_enu) * 1.1
        rot = math.radians((0 - angle + 180) % 360 - 180)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        tdx, tdy = -sin_r, cos_r
        ldx, ldy = cos_r, sin_r
        TIRE_HALF = tire_m / 2.0   # half the configurable machine/tire width
        RED   = "#FF2A2A"
        GREEN = "#22E048"
        max_rows = int(max_r / width_m) + 2
        half_width = width_m / 2.0
        # Maximum shelter band width before it would reach the tire zone
        max_band_m = max(0.0, half_width - TIRE_HALF)
        # Outside-round edge band (how far shelters may intrude into the
        # perimeter pass from its inner edge) and the no-go limit polygon.
        # Shelters must stay at distance ≥ (width − edge band) from the boundary,
        # so every green good-zone band is clipped to safe_poly and never bleeds
        # into the perimeter pass. Drawn regardless of the Shelters-in-Outside-
        # Pass toggle, so the rule is always visible.
        out_band = min(buffer_m, max_band_m) if show_band else 0.0
        safe_inset = max(0.0, width_m - out_band)
        # Clip-only polygons → raw inset (no spike cleanup) so deep insets on
        # finely-traced / concave boundaries don't collapse and leave gaps.
        safe_poly = (inset_polygon_enu(poly_enu, safe_inset, remove_spikes=False)
                     if out_band > 0 else poly_enu)
        if not safe_poly or len(safe_poly) < 3:
            safe_poly = poly_enu

        def _add(o):
            if o is not None: self.pass_buffer_overlays.append(o)

        outside_round_inner = inset_polygon_enu(poly_enu, width_m, remove_spikes=False)

        def _pass_covers_interior(x_lo, x_hi):
            """True if any part of the lateral pass span [x_lo, x_hi] reaches
            the field interior beyond the outside round — i.e. the pass still
            sprays ground the perimeter round does not cover, so it must be
            drawn. Sampling several lines across the span (not just the centre)
            keeps passes that only PARTLY poke out of the round."""
            if len(outside_round_inner) < 3:
                return True
            for i in range(7):
                cx = x_lo + (x_hi - x_lo) * i / 6.0
                if clip_line_to_polygon_intervals(cx * ldx + sse, cx * ldy + ssn,
                                                  tdx, tdy, outside_round_inner):
                    return True
            return False

        inner_polys_enu = []
        for inner in (self.current_field.get("boundary_inner") or []):
            if inner and len(inner) >= 3:
                inner_polys_enu.append([latlon_to_enu(p[0], p[1], plat, plon) for p in inner])

        # ── Solid red tire band (only when tire-zone toggle is on) ──────────
        if show_tire:
            for r in range(-max_rows, max_rows + 1):
                cx = (r + 0.5) * width_m
                if not _pass_covers_interior(r * width_m, (r + 1) * width_m): continue
                for band in self._band_polygon_enu(cx - TIRE_HALF, cx + TIRE_HALF,
                                                   tdx, tdy, ldx, ldy, poly_enu,
                                                   inner_polys_enu=inner_polys_enu,
                                                   off_e=sse, off_n=ssn):
                    lpts = [enu_to_latlon(e, n, plat, plon) for e, n in band]
                    try: _add(self.map_widget.set_polygon(lpts, fill_color=RED,
                                                           outline_color=RED, border_width=0))
                    except Exception: pass

        # ── Solid green fill + edge lines in shelter edge band ──────────────
        # Same fill approach as the red tire zone — just a solid green polygon
        # for each clipped band piece, capped so it never overlaps the tire zone.
        def _fill_band(x1, x2, clip_poly=None, cutouts=None, draw_edges=True,
                       off_e=0.0, off_n=0.0):
            if x2 - x1 <= 0: return
            cp = clip_poly if clip_poly is not None else safe_poly
            for band_poly in self._band_polygon_enu(x1, x2, tdx, tdy, ldx, ldy, cp,
                                                    inner_polys_enu=cutouts,
                                                    off_e=off_e, off_n=off_n):
                lpts = [enu_to_latlon(e, n, plat, plon) for e, n in band_poly]
                try: _add(self.map_widget.set_polygon(lpts, fill_color=GREEN,
                                                      outline_color=GREEN, border_width=0))
                except Exception: pass
            if not draw_edges: return
            for x_lat in (x1, x2):
                pe_e, pn_e = x_lat * ldx + off_e, x_lat * ldy + off_n
                segs = clip_line_to_polygon_intervals(pe_e, pn_e, tdx, tdy, cp)
                # Break the edge line out of any inner boundary too.
                for inner in (cutouts or []):
                    for (i0, i1) in clip_line_to_polygon_intervals(pe_e, pn_e, tdx, tdy, inner):
                        nseg = []
                        for (a, b) in segs:
                            if i1 <= a or i0 >= b:
                                nseg.append((a, b))
                            else:
                                if i0 > a: nseg.append((a, i0))
                                if i1 < b: nseg.append((i1, b))
                        segs = nseg
                for (t1, t2) in segs:
                    la1, lo1 = enu_to_latlon(pe_e + t1*tdx, pn_e + t1*tdy, plat, plon)
                    la2, lo2 = enu_to_latlon(pe_e + t2*tdx, pn_e + t2*tdy, plat, plon)
                    try: _add(self.map_widget.set_path([(la1,lo1),(la2,lo2)], color=GREEN, width=2))
                    except Exception: pass

        if show_band and out_band > 0:
            # Every green good-zone band is a LATERAL edge band (within out_band
            # of a pass edge). Because shelters only ever sit in these lateral
            # edge bands, the green automatically keeps the machine-to-edge
            # clearance from every interior tire and BREAKS at each row end
            # where a pass drives through — so green is never drawn too close to
            # a red tire zone.
            #   • Interior + inner-edge bands: clipped to safe_poly (≥ width −
            #     band in from the boundary) — fills the interior and the
            #     round's inner edge band without entering the driven middle.
            # Inner boundaries (cutouts) — keep the green edge bands OUT of
            # them, same as the tire pass. None when there are no inner
            # boundaries, so simple fields keep the slanted-end / centreline
            # path.
            _green_cuts = inner_polys_enu or None
            for r in range(-max_rows, max_rows + 1):
                if not _pass_covers_interior(r * width_m, (r + 1) * width_m): continue
                le = r * width_m; re_ = (r + 1) * width_m
                _fill_band(le, le + out_band, off_e=sse, off_n=ssn, cutouts=_green_cuts)
                _fill_band(re_ - out_band, re_, off_e=sse, off_n=ssn, cutouts=_green_cuts)
            #   • Outer edge band against the boundary: kept CONTINUOUS all the
            #     way around (override). The outside round is driven once and the
            #     operator turns before leaving the field, so a shelter on the
            #     very outer edge is safe even where an interior pass crosses —
            #     this band is NOT broken at row ends. Drawn PER BOUNDARY EDGE
            #     so it follows any field shape (a global inset would collapse on
            #     concave necks / finely-traced edges and leave gaps).
            _ob_inner = []
            for q in perimeter_band_quads(poly_enu, 0.0, out_band):
                lp = [enu_to_latlon(e, n, plat, plon) for e, n in q]
                try: _add(self.map_widget.set_polygon(lp, fill_color=GREEN,
                                                      outline_color=GREEN, border_width=0))
                except Exception: pass
                # Collect each quad's inner edge (q[3]→q[2], at depth out_band)
                # into ONE polyline so it stays connected around corners — a
                # per-segment line leaves wedge gaps at sharp corners (e.g. the
                # NW corner).
                _ob_inner.append(enu_to_latlon(q[3][0], q[3][1], plat, plon))
                _ob_inner.append(enu_to_latlon(q[2][0], q[2][1], plat, plon))
            if len(_ob_inner) >= 2:
                _ob_inner.append(_ob_inner[0])   # close the ring
                try: _add(self.map_widget.set_path(_ob_inner, color=GREEN, width=2))
                except Exception: pass

        # ── Outside round: red tire ring ────────────────────────────────────
        # The perimeter pass's machine/tire drive zone. Drawn PER BOUNDARY EDGE
        # (not from a global inset) so it renders all the way around any shape —
        # including concave necks narrower than the sprayer, where an inset
        # would self-intersect and drop sections.
        if show_tire:
            half = width_m / 2.0
            for q in perimeter_band_quads(poly_enu, max(0.0, half - TIRE_HALF),
                                          half + TIRE_HALF):
                lp = [enu_to_latlon(e, n, plat, plon) for e, n in q]
                try: _add(self.map_widget.set_polygon(lp, fill_color=RED,
                                                      outline_color=RED, border_width=0))
                except Exception: pass

    def _redraw_bays(self):
        self._clear_bays()
        if not self.show_bays.get(): return
        # No bay structure in blanket-planted mode → nothing to draw.
        if not self.current_field.get("use_bays", True): return
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            _plant = self.fv["Planting_angle"].get().strip()
            _spray = self.fv["Spray_angle"].get().strip()
            angle=float(_plant or _spray or 0)
            rs=float(self.fv["row_spacing_in"].get() or 22)
            nf=int(self.fv["num_female_rows"].get() or 8)
            nm=int(self.fv["num_male_rows"].get() or 2)
            total_rows=int(self.fv["total_rows"].get() or (nf + nm))
            gap_in=float(self.fv["bay_gap_in"].get() or 0)
            bp=self.current_field.get("boundary_polygon")
        except (ValueError,TypeError): return
        if not bp or len(bp)<3: return
        gap_m=max(0.0, gap_in)*0.0254

        bse, bsn = self._bay_shift()   # planter Shift offset (east, north metres)

        # When the user has imported planter passes AND the "use uploaded
        # planter data" toggle is on, derive the male-bay bands from the
        # actual pass polylines instead of the synthetic angle-grid below.
        # Each pass contributes one band per M block in the resolved row mask.
        planter_passes = self.current_field.get("planter_passes") or []
        if planter_passes and bool(self.current_field.get("use_imported_passes", True)):
            shifted = self._translate_latlon(planter_passes, bse, bsn)
            self._redraw_bays_from_passes(plat, plon, shifted,
                                          rs, nf, nm, total_rows)
            return
        row_m=rs*0.0254; female_m=(nf+1)*row_m; male_m=(nm+1)*row_m
        poly_enu=[latlon_to_enu(lat,lon,plat,plon) for lat,lon in bp]
        # Inner cutouts in ENU so bays don't render across building / slough
        # footprints either — UNLESS the user has opted into "bays through
        # inner" (some fields plant straight through small interior cutouts
        # like access lanes, and the bays should stay continuous).
        inner_polys_enu = []
        if not bool(self.current_field.get("bays_through_inner", False)):
            for inner in (self.current_field.get("boundary_inner") or []):
                if not inner or len(inner) < 3: continue
                inner_polys_enu.append(
                    [latlon_to_enu(pt[0], pt[1], plat, plon) for pt in inner])
        max_r=max(math.sqrt(e*e+n*n) for e,n in poly_enu)*1.1
        rot=math.radians((180-angle)%360-180)
        cos_r,sin_r=math.cos(rot),math.sin(rot)
        tdx=-sin_r; tdy=cos_r
        ldx=cos_r; ldy=sin_r
        # Repeat period grows by a gap at each male/female edge (2 per period);
        # the male band is pushed in by one gap so a gap sits on each side.
        unit=female_m+male_m+2*gap_m
        # Lateral component of the planter Shift offset (movement along the bay
        # travel direction is invisible, so only the perpendicular part counts).
        bay_sh = bse*ldx + bsn*ldy
        n_units=int(max_r/unit)+2
        for i in range(-n_units,n_units+1):
            cx=i*unit
            # Female bays hidden — only male bays shown
            bands = self._band_polygon_enu(
                cx + female_m/2 + gap_m + bay_sh,
                cx + female_m/2 + gap_m + male_m + bay_sh,
                tdx, tdy, ldx, ldy, poly_enu,
                inner_polys_enu=inner_polys_enu)
            for band in bands:
                lpts=[enu_to_latlon(e,n,plat,plon) for e,n in band]
                try:
                    p=self.map_widget.set_polygon(lpts,fill_color="#001F7A",outline_color="#001F7A",border_width=0)
                    self.bay_polygons.append(p)
                except Exception: pass

    def _redraw_bays_from_passes(self, plat, plon, planter_passes,
                                  row_spacing_in_v, nf, nm, total_rows):
        """Bay overlay derived from imported planter passes.

        For each pass and each M run in the resolved row mask, we offset the
        pass polyline by the M run's left edge and right edge (in metres,
        perpendicular to local heading) and draw the closed band as a male
        bay. Bands follow the actual planter path — curves and all — so the
        overlay matches what the user sees the planter actually did, not a
        synthetic angle-grid approximation.
        """
        layout = self._row_layout_labels.get(self.row_layout_var.get(), "centered")
        mask = self._resolve_row_mask(nf, nm, layout,
                                       self.custom_mask_var.get(),
                                       total_rows=total_rows)
        if not mask: return
        m_blocks = maketentgrid.mask_runs(mask, 'M')
        if not m_blocks: return
        row_spacing_m = row_spacing_in_v * 0.0254
        # M run (s, e) covers rows s..e-1. Its left edge is at lateral
        # offset (s - total_rows/2) × row_spacing from the pass centre,
        # right edge at (e - total_rows/2) × row_spacing. (Both signed —
        # negative = left of travel, positive = right.)
        edge_pairs = [((s - total_rows / 2.0) * row_spacing_m,
                       (e - total_rows / 2.0) * row_spacing_m)
                      for s, e in m_blocks]
        # Normalise polyline form: planter_passes is JSON [[lat,lon],...].
        passes_normed = [[(float(pt[0]), float(pt[1])) for pt in p]
                         for p in planter_passes if p and len(p) >= 2]
        for poly_ll in passes_normed:
            for left_m, right_m in edge_pairs:
                # Offset the pass twice and stitch the band as a closed loop.
                left_poly  = maketentgrid._offset_polyline_latlon(poly_ll, left_m)
                right_poly = maketentgrid._offset_polyline_latlon(poly_ll, right_m)
                if len(left_poly) < 2 or len(right_poly) < 2: continue
                band = list(left_poly) + list(reversed(right_poly))
                try:
                    p = self.map_widget.set_polygon(
                        band, fill_color="#001F7A",
                        outline_color="#001F7A", border_width=0)
                    self.bay_polygons.append(p)
                except Exception:
                    pass

    # ── Full overlay refresh ───────────────────────────────────────────────────
    def _redraw_all(self):
        self._redraw_pivot()
        self._redraw_boundary(); self._redraw_tracks(); self._redraw_passes(); self._redraw_bays(); self._redraw_corner_arms(); self._redraw_planter_passes(); self._redraw_sprayer_passes(); self._redraw_pass_buffer_overlay(); self._redraw_shelters()

    def _clear_all_overlays(self):
        if self.pivot_marker: self.pivot_marker.delete(); self.pivot_marker=None
        self._unregister_drag_prefix("pivot")
        self._clear_boundary_overlays(); self._clear_passes(); self._clear_bays()
        for o in self.track_circles:
            try: o.delete()
            except Exception: pass
        for h in self.track_handles:
            try: h.delete()
            except Exception: pass
        self.track_circles=[]; self.track_handles=[]
        self._clear_corner_arm(0); self._clear_corner_arm(1)
        for o in self.planter_path_overlays:
            try: o.delete()
            except Exception: pass
        self.planter_path_overlays=[]
        for o in self.sprayer_path_overlays:
            try: o.delete()
            except Exception: pass
        self.sprayer_path_overlays=[]
        self._clear_pass_buffer_overlay()
        self._clear_shelters()

    # ── Pivot drag handler ─────────────────────────────────────────────────────
    def _on_pivot_drag(self,lat,lon):
        self.fv["PP_Latitude"].set(f"{lat:.7f}"); self.fv["PP_Longitude"].set(f"{lon:.7f}")
        self._autofill_lld(lat, lon)
        if self.pivot_marker:
            try: self.pivot_marker.delete()
            except Exception: pass
        self.pivot_marker=self.map_widget.set_marker(lat,lon,text="Pivot",
                                                      marker_color_circle="red",
                                                      marker_color_outside="darkred")
        self._register_drag("pivot",lat,lon,"Pivot","red","darkred",self._on_pivot_drag,marker=self.pivot_marker)
        self._status(f"Pivot moved: {lat:.5f}, {lon:.5f}")
        self._redraw_boundary(); self._redraw_passes(); self._redraw_tracks()

    def _autofill_lld(self, lat, lon):
        """Compute the quarter-section LLD for (lat, lon) and write it to the
        LLD entry. Mirrors the acres-on-boundary-save flow: a fresh placement
        replaces whatever was there. The user can type a different format
        (half, section, or even LSD) afterwards and that value sticks until
        the pivot is moved again."""
        try:
            lld = reverse_geocode_lld(lat, lon, granularity='quarter')
        except Exception:
            lld = None
        if lld and self.fv.get("lld") is not None:
            try: self.fv["lld"].set(lld)
            except Exception: pass

    # ── Drag system ────────────────────────────────────────────────────────────
    def _register_drag(self,key,lat,lon,text,cc,oc,update_fn,marker=None):
        self._drag_registry[key]=dict(lat=lat,lon=lon,text=text,
                                       circle_color=cc,outside_color=oc,update_fn=update_fn,marker=marker)

    def _unregister_drag_prefix(self,prefix):
        for k in [k for k in self._drag_registry if k.startswith(prefix)]:
            del self._drag_registry[k]

    def _pixel_scale(self):
        try:
            mw=self.map_widget
            w,h=mw.winfo_width()//2,mw.winfo_height()//2
            la,lo=mw.convert_canvas_coords_to_decimal_coords(w,h)
            la2,lo2=mw.convert_canvas_coords_to_decimal_coords(w+1,h)
            return haversine_m(la,lo,la2,lo2)
        except Exception:
            return 5.0

    def _bind_drag_system(self):
        canvas=self.map_widget.canvas
        # Replace tkintermapview's handlers entirely; forward to them when not dragging a pin
        canvas.bind("<ButtonPress-1>",self._drag_press)
        canvas.bind("<ButtonRelease-1>",self._drag_release)
        canvas.bind("<B1-Motion>",self._b1_motion)

    def _drag_press(self,event):
        self._pan_start_xy=(event.x,event.y)
        self._drag_moved=False
        self._drag_track_idx=None
        self._pending_corner_idx=None
        self._pending_boundary_click=False
        # Always let tkintermapview record the press so panning works correctly
        try: self.map_widget.mouse_click(event)
        except Exception: pass
        # Find the pin under the cursor. Preferred: the click lands inside a
        # pin's drawn image (teardrop body + pointer) in canvas pixels, so any
        # part of the pin is grabbable. Fallback: nearest pin anchor within a
        # small radius (covers pins without a live marker).
        #
        # The marker is drawn with its pointer tip at the anchor (cx, cy):
        #   circle  bbox  = (cx-14, cy-45) .. (cx+14, cy-17)
        #   pointer triangle apex at (cx, cy)
        # so the whole image spans x∈[cx-16, cx+16], y∈[cy-48, cy+3] (padded).
        best_id=None; lat0=lon0=None
        try:
            lat0,lon0=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
            mpp=self._pixel_scale()
            ex,ey=event.x,event.y
            best_dist=max(12*mpp,8.0)   # fallback: nearest-anchor threshold (m)
            best_box=None               # (canvas-dist², id) for image-box hits
            for did,info in self._drag_registry.items():
                m=info.get('marker')
                if m is not None and not getattr(m,'deleted',False):
                    try: cx,cy=m.get_canvas_pos(m.position)
                    except Exception: cx=cy=None
                    if cx is not None and (cx-16)<=ex<=(cx+16) and (cy-48)<=ey<=(cy+3):
                        # Inside this pin's image — rank by distance to the
                        # circle centre so overlapping pins resolve sensibly.
                        dpx=(ex-cx)**2+(ey-(cy-31))**2
                        if best_box is None or dpx<best_box[0]:
                            best_box=(dpx,did)
                d=haversine_m(info['lat'],info['lon'],lat0,lon0)
                if d<best_dist:
                    best_dist=d; best_id=did
            if best_box is not None:
                best_id=best_box[1]   # an image-box hit always wins
        except Exception:
            best_id=None
        if best_id:
            self._drag_item=best_id
            self._drag_last_latlon=(lat0,lon0)
            self._drag_start_xy=(event.x,event.y)
        elif self.click_mode is None and lat0 is not None and self.show_tracks.get():
            # No pin hit — see if the click landed on a pivot track band
            idx=self._track_hit(lat0,lon0,mpp)
            if idx is not None:
                self._drag_track_idx=idx
                self._drag_last_latlon=(lat0,lon0)
                self._drag_start_xy=(event.x,event.y)
            elif self.show_corner_arms.get():
                # No track either — maybe a corner track was clicked. We only
                # remember it (no drag state) so a click without movement opens
                # the corner popup, while a drag still pans the map.
                cidx=self._corner_arm_hit(lat0,lon0,mpp)
                if cidx is not None:
                    self._pending_corner_idx=cidx
                    self._drag_start_xy=(event.x,event.y)
        elif self.click_mode is None and lat0 is not None and self.show_corner_arms.get():
            # Tracks hidden but corner arms shown — still allow corner clicks.
            cidx=self._corner_arm_hit(lat0,lon0,mpp)
            if cidx is not None:
                self._pending_corner_idx=cidx
                self._drag_start_xy=(event.x,event.y)
        # Outer-boundary edge click (when nothing else was grabbed) → remember
        # it so a click without movement opens the edit/delete popup, while a
        # drag still pans the map.
        if (self.click_mode is None and lat0 is not None
                and not self._drag_item and self._drag_track_idx is None
                and self._pending_corner_idx is None
                and self.show_boundary.get()
                and self._boundary_edge_hit(lat0, lon0, mpp)):
            self._pending_boundary_click=True
            self._drag_start_xy=(event.x,event.y)

    def _b1_motion(self,event):
        sx,sy=self._pan_start_xy if self._pan_start_xy else (event.x,event.y)
        if abs(event.x-sx)>4 or abs(event.y-sy)>4:
            self._drag_moved=True
        if self._drag_track_idx is not None:
            # Pivot-track band drag — resize the track to the cursor radius
            if self._drag_moved:
                try:
                    lat,lon=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
                    self._drag_last_latlon=(lat,lon)
                    self._on_track_drag(self._drag_track_idx,lat,lon,final=False)
                except Exception: pass
            return
        if self._drag_item:
            # Pin drag — move the actual marker's canvas items with the cursor
            try:
                lat,lon=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
            except Exception: return
            if self._drag_moved:
                info=self._drag_registry.get(self._drag_item)
                m=info.get('marker') if info else None
                if m and not getattr(m,'deleted',True):
                    try:
                        x,y=event.x,event.y
                        canvas=self.map_widget.canvas
                        if m.polygon:
                            canvas.coords(m.polygon,x-14,y-23,x,y,x+14,y-23)
                        if m.big_circle:
                            canvas.coords(m.big_circle,x-14,y-45,x+14,y-17)
                        if m.canvas_text:
                            canvas.coords(m.canvas_text,x,y+m.text_y_offset)
                    except Exception: pass
                self._drag_last_latlon=(lat,lon)
        else:
            # Pan the map via tkintermapview so tile positions stay accurate
            try: self.map_widget.mouse_move(event)
            except Exception: pass

    def _drag_release(self,event):
        # Pivot-track band: drag → resize, click (no drag) → options popup
        if self._drag_track_idx is not None:
            idx=self._drag_track_idx
            moved=self._drag_moved
            if moved and self._drag_last_latlon:
                lat,lon=self._drag_last_latlon
                self._on_track_drag(idx,lat,lon,final=True)
            self._drag_track_idx=None; self._drag_moved=False
            self._drag_start_xy=None; self._drag_last_latlon=None; self._pan_start_xy=None
            if not moved:
                self._show_track_popup(idx)
            return
        # Corner-track click (no drag) → options popup. A drag that began on a
        # corner arm just panned the map, so only act on a clean click.
        cidx=self._pending_corner_idx
        self._pending_corner_idx=None
        if cidx is not None and not self._drag_moved:
            self._drag_moved=False
            self._drag_start_xy=None; self._drag_last_latlon=None; self._pan_start_xy=None
            self._show_corner_track_popup(cidx)
            return
        # Outer-boundary click (no drag) → edit / delete popup.
        bnd_click=getattr(self,'_pending_boundary_click',False)
        self._pending_boundary_click=False
        if bnd_click and not self._drag_moved:
            self._drag_start_xy=None; self._drag_last_latlon=None; self._pan_start_xy=None
            self._show_boundary_popup()
            return
        was_pin_drag=bool(self._drag_item)
        if was_pin_drag and self._drag_moved and self._drag_last_latlon:
            # Real pin drag completed — apply new position
            lat,lon=self._drag_last_latlon
            info=self._drag_registry.get(self._drag_item)
            if info:
                info['lat']=lat; info['lon']=lon
                try: info['update_fn'](lat,lon)
                except Exception: pass
            self._just_dragged=True
        elif not self._drag_moved:
            # Click without drag.
            #   - Add-shelter mode AND a pin was tapped → delete that pin
            #     (so the user can remove their misplaced pin in the same
            #     flow as adding others, without leaving add mode).
            #   - Any other click_mode → defer to _on_map_click (so the user
            #     can place pivot / boundary / etc. points near existing pins).
            #   - No mode + pin tapped → offer delete.
            #   - No mode + no pin → plain map click.
            if self.click_mode == "add_shelter" and was_pin_drag and \
               self._drag_item and self._drag_item.startswith("manualpin_"):
                try: self._delete_manualpin(int(self._drag_item.split("_")[1]))
                except (ValueError, IndexError): pass
            elif self.click_mode == "add_shelter" and was_pin_drag and \
               self._drag_item and self._drag_item.startswith("shelter_"):
                try:
                    idx = int(self._drag_item.split("_")[1])
                    self._on_shelter_tap(idx)
                except (ValueError, IndexError): pass
            elif self.click_mode is not None:
                try:
                    lat,lon=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
                    self._on_map_click((lat,lon))
                except Exception: pass
            elif was_pin_drag and self._drag_item and self._drag_item.startswith("manualpin_"):
                # No mode active and an extra pin was tapped — offer delete
                try: self._on_manualpin_tap(int(self._drag_item.split("_")[1]))
                except (ValueError,IndexError): pass
            elif was_pin_drag and self._drag_item and self._drag_item.startswith("shelter_"):
                # No mode active and a shelter pin was tapped — offer delete
                try:
                    idx=int(self._drag_item.split("_")[1])
                    self._on_shelter_tap(idx)
                except (ValueError,IndexError): pass
            elif not was_pin_drag:
                # Plain map click (no pin nearby, no mode).
                # First check whether the click landed inside a non-active
                # field boundary — if so, activate that field.
                try:
                    lat,lon=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
                    hit=None
                    act_co=str(self.current_field.get("company",""))
                    act_yr=str(self.current_field.get("year",""))
                    act_nm=str(self.current_field.get("Name",""))
                    for (co,yr,name),bp in self._overview_field_bps.items():
                        if co==act_co and yr==act_yr and name==act_nm:
                            continue  # don't re-load the active field
                        if _pt_in_poly(lat,lon,bp):
                            hit=(co,yr,name); break
                    if hit:
                        self.after(1, lambda h=hit: self._activate_field(*h))
                    else:
                        self._on_map_click((lat,lon))
                except Exception: pass
        else:
            # Pan finished — let tkintermapview run its fading animation
            try: self.map_widget.mouse_release(event)
            except Exception: pass
        self._drag_item=None; self._drag_moved=False
        self._drag_start_xy=None; self._drag_last_latlon=None; self._pan_start_xy=None

    # ── Save / Load ────────────────────────────────────────────────────────────
    # ── Git auto-sync ──────────────────────────────────────────────────────────
    # Code files whose change signals a restart is needed.
    _CODE_FILES = {"beetent_app.py", "maketentgrid.py", "utmish.py"}

    def _git_pull(self):
        """Pull latest changes from GitHub on startup (background thread).
        If code files changed, show the restart button."""
        import subprocess
        repo = Path(__file__).parent
        def run():
            try:
                before = subprocess.run(["git","rev-parse","HEAD"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                subprocess.run(["git","pull","--rebase"],
                    cwd=repo, capture_output=True, timeout=30)
                after = subprocess.run(["git","rev-parse","HEAD"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                if before != after:
                    self.after(0, self._refresh_field_list)
                    self.after(0, self._refresh_preset_list)
                    self.after(0, self._refresh_bee_preset_list)
                    self.after(0, self._refresh_field_preset_list)
                    changed = subprocess.run(
                        ["git","diff","--name-only",
                         before.decode(), after.decode()],
                        cwd=repo, capture_output=True, timeout=5
                    ).stdout.decode()
                    if any(f in changed for f in self._CODE_FILES):
                        self.after(0, self._on_update_ready)
                    else:
                        self.after(0, lambda: self._status("☁ Pulled latest data"))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _manual_sync(self):
        """Manual refresh: pull from GitHub, then always refresh all lists and
        reload the current field from disk. Show restart button if code changed."""
        import subprocess
        self._sync_btn.configure(text="Syncing…", state="disabled")
        self._status("Syncing with GitHub…")
        repo = Path(__file__).parent
        co  = str(self.current_field.get("company",""))
        yr  = str(self.current_field.get("year",""))
        nm  = str(self.current_field.get("Name",""))
        def run():
            pulled_new = False
            code_changed = False
            try:
                before = subprocess.run(["git","rev-parse","HEAD"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                subprocess.run(["git","pull","--rebase"],
                    cwd=repo, capture_output=True, timeout=30)
                after = subprocess.run(["git","rev-parse","HEAD"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                if before != after:
                    pulled_new = True
                    changed = subprocess.run(
                        ["git","diff","--name-only", before.decode(), after.decode()],
                        cwd=repo, capture_output=True, timeout=5
                    ).stdout.decode()
                    code_changed = any(f in changed for f in self._CODE_FILES)
            except Exception:
                pass
            # Always refresh UI from disk regardless of git outcome
            self.after(0, self._refresh_field_list)
            self.after(0, self._refresh_preset_list)
            self.after(0, self._refresh_bee_preset_list)
            self.after(0, self._refresh_field_preset_list)
            if nm:
                self.after(0, lambda: self._activate_field(co, yr, nm))
            if code_changed:
                self.after(0, self._on_update_ready)
            elif pulled_new:
                self.after(0, lambda: self._status("☁ Refreshed — pulled latest data."))
            else:
                self.after(0, lambda: self._status("☁ Refreshed."))
            self.after(0, lambda: self._sync_btn.configure(text="☁ Refresh", state="normal"))
        threading.Thread(target=run, daemon=True).start()

    def _check_for_app_update(self):
        """Periodic update check (every 5 min). Fetches from GitHub; if the
        remote is ahead, pulls and shows the restart button if code changed."""
        import subprocess
        repo = Path(__file__).parent
        def run():
            try:
                subprocess.run(["git","fetch","github","master"],
                    cwd=repo, capture_output=True, timeout=15)
                local = subprocess.run(["git","rev-parse","HEAD"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                remote = subprocess.run(["git","rev-parse","github/master"],
                    cwd=repo, capture_output=True, timeout=5).stdout.strip()
                if local != remote:
                    subprocess.run(["git","pull","--rebase"],
                        cwd=repo, capture_output=True, timeout=30)
                    after = subprocess.run(["git","rev-parse","HEAD"],
                        cwd=repo, capture_output=True, timeout=5).stdout.strip()
                    if after != local:
                        self.after(0, self._refresh_field_list)
                        self.after(0, self._refresh_preset_list)
                        changed = subprocess.run(
                            ["git","diff","--name-only",
                             local.decode(), after.decode()],
                            cwd=repo, capture_output=True, timeout=5
                        ).stdout.decode()
                        if any(f in changed for f in self._CODE_FILES):
                            self.after(0, self._on_update_ready)
                        else:
                            self.after(0, lambda: self._status("☁ Pulled latest data"))
            except Exception:
                pass
            # Schedule the next check regardless of success/failure.
            self.after(300_000, self._check_for_app_update)
        threading.Thread(target=run, daemon=True).start()

    def _on_update_ready(self):
        """Show the restart button in the toolbar."""
        try:
            self._update_btn.pack(side="right", padx=(0,8), pady=6)
        except Exception:
            pass
        self._status("☁ App update downloaded — click 🔄 Restart to apply.")

    def _restart_app(self):
        """Restart the process in-place to apply a pulled update."""
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            tkinter.messagebox.showinfo("Restart Required",
                "Please close and reopen the app to apply the update.")

    def _git_push(self,message="auto-sync"):
        """Commit fields/ changes and push to GitHub (background thread)."""
        import subprocess
        repo=Path(__file__).parent
        def run():
            try:
                self.after(0,lambda:self._status("☁ Syncing…"))
                subprocess.run(["git","add","fields/"],cwd=repo,
                               capture_output=True,timeout=10)
                has_changes=subprocess.run(
                    ["git","diff","--cached","--quiet"],
                    cwd=repo,capture_output=True).returncode!=0
                if has_changes:
                    subprocess.run(["git","commit","-m",message],
                                   cwd=repo,capture_output=True,timeout=10)
                    subprocess.run(["git","push"],cwd=repo,
                                   capture_output=True,timeout=30)
                self.after(0,lambda:self._status("☁ Synced"))
            except Exception:
                self.after(0,lambda:self._status(""))
        threading.Thread(target=run,daemon=True).start()

    def _save_field(self):
        f=self._field_from_form()
        if not f.get("Name"):
            self._status("Enter a field name."); return
        # Field name validation: certain characters break the on-disk
        # folder structure AND get rejected by John Deere Operations
        # Center on upload. Block them here with a clear alert so the
        # user fixes the name before we write anything.
        bad = invalid_field_name_chars(f.get("Name") or "")
        if bad:
            tkinter.messagebox.showerror("Invalid field name",
                f"The field name contains \"{' '.join(bad)}\".\n\n"
                f"John Deere Operations Center rejects field names with "
                f"# or /, and these characters also break Windows file "
                f"paths:\n    {FIELD_NAME_BAD_CHARS_HUMAN}\n\n"
                f"Please change the name before saving.")
            return
        co=(f.get("company") or "").strip()
        if not co:
            self._status("Enter a company in Field Details before saving."); return
        # Company name has the same restriction since it becomes a folder.
        bad_co = invalid_field_name_chars(co)
        if bad_co:
            tkinter.messagebox.showerror("Invalid company name",
                f"The company name contains \"{' '.join(bad_co)}\".\n\n"
                f"These characters break Windows folders and JD uploads:\n"
                f"    {FIELD_NAME_BAD_CHARS_HUMAN}\n\n"
                f"Please change the company before saving.")
            return
        # Year falls back to the current calendar year if the dropdown is
        # on "All years" — saves the user the click in that common case.
        yr=(f.get("year") or "").strip()
        if not yr or yr==ALL_YEARS:
            yr=str(datetime.date.today().year)
            f["year"]=yr
        if not f.get("boundary_polygon"):
            self._status("⚠ No boundary drawn — field saved but cannot generate without one.")
        # save_field creates the company/year folder via _field_dir, so a
        # brand-new company name appears on disk for the first time here.
        new_company = co not in list_companies()
        save_field(f)
        if new_company:
            # Refresh the dropdown so the new company is selectable, and
            # switch to it (with the saved year) so the field-list filter
            # shows the field we just saved.
            self._refresh_company_list()
            self.company_var.set(co)
            self._on_company_change(co)
            self.year_var.set(yr)
        self._refresh_field_list()
        self._status(f"Saved: {f['Name']}" + (" (new company)" if new_company else ""))
        self._git_push(f"save field: {f['Name']}")

    def _load_csv(self):
        path=tkinter.filedialog.askopenfilename(filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path: return
        co=self.company_var.get(); yr=self.year_var.get(); loaded=0
        with open(path,newline="") as fh:
            for row in csv.DictReader(fh):
                f=blank_field(co,yr)
                for k in ("Name","PP_Latitude","PP_Longitude","Sprayer_width","directional_offset"):
                    if k in row: f[k]=row[k]
                f["Spray_angle"]=row.get("Seed_angle","0")
                f["num_structures"]=row.get("# of Structures","")
                f["spacing"]=row.get("spacing","")
                if f.get("Name"): save_field(f); loaded+=1
        self._refresh_field_list(); self._log(f"Loaded {loaded} field(s) from CSV.")

    # ── Generate ───────────────────────────────────────────────────────────────
    def _export_scope(self):
        """List of (company, year, field_name) matching the current dropdowns,
        expanding the All-companies / All-years sentinels."""
        co=self.company_var.get(); yr=self.year_var.get()
        companies=list_companies() if co==ALL_COMPANIES else [co]
        out=[]
        for c in companies:
            years=list_years(c) if yr==ALL_YEARS else [yr]
            for y in years:
                for name in list_fields(c,y):
                    out.append((c,y,name))
        return out

    def _final_shelter_positions(self, f, metric):
        """Shelter positions exactly as drawn on the map: get_tent_positions
        with the field's shelter_overrides (moved/deleted) applied, plus any
        additive manual pins (extra pins placed on top of the algorithm grid)."""
        positions=maketentgrid.get_tent_positions(f,use_metric=metric)
        # Match the map: shift the algorithm grid by the planter + sprayer Shift.
        sse, ssn = self._field_combined_shift(f)
        if (sse or ssn) and positions:
            positions=[self._shift_pt(la,lo,sse,ssn) for la,lo in positions]
        overrides=f.get("shelter_overrides") or {}
        merged=list(positions); deleted=set()
        for k,v in overrides.items():
            try:
                idx=int(k)
                if 0<=idx<len(merged):
                    if v is None: deleted.add(idx)
                    else: merged[idx]=tuple(v)
            except (ValueError,TypeError): pass
        out=[p for i,p in enumerate(merged) if i not in deleted]
        # Additive manual pins (skipped in "Manual pins only" mode where
        # get_tent_positions already returned them).
        mode_key=str(f.get("shelter_mode") or "").strip().lower()
        if mode_key!="manual":
            for pt in (f.get("manual_shelter_pins") or []):
                try: out.append((float(pt[0]),float(pt[1])))
                except (TypeError,ValueError,IndexError): pass
        return out

    def _prompt_zero_buffers(self, zero_fields, metric):
        """Modal dialog letting the user set a buffer size per zero-buffer field.

        zero_fields: list of (company, year, name, display_name).
        Returns None if the user cancels (abort export); otherwise a dict
        {(company,year,name): buffer_m} for fields given a positive size.
        Any positive size is also persisted to that field's JSON so the
        export reload picks it up.
        """
        unit = "m" if metric else "ft"
        win = ctk.CTkToplevel(self)
        win.title("Set Buffer Zones")
        win.grab_set()
        ctk.CTkLabel(win,
            text=("These field(s) have no buffer size set. Enter a size to\n"
                  f"include buffer zones ({unit}), or leave 0 to skip that field."),
            justify="left").pack(padx=14, pady=(12, 8), anchor="w")
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14)
        entries = {}
        for (c, y, name, disp) in zero_fields:
            row = ctk.CTkFrame(body, fg_color="transparent"); row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=disp, width=220, anchor="w").pack(side="left")
            var = tk.StringVar(value="0")
            ctk.CTkEntry(row, textvariable=var, width=70).pack(side="left", padx=(4, 2))
            ctk.CTkLabel(row, text=unit, width=24, anchor="w").pack(side="left")
            entries[(c, y, name)] = var
        state = {"ok": False}
        def do_ok(): state["ok"] = True; win.destroy()
        btns = ctk.CTkFrame(win, fg_color="transparent"); btns.pack(pady=(10, 12))
        ctk.CTkButton(btns, text="OK — Export", command=do_ok).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Cancel", fg_color="#555", command=win.destroy).pack(side="left", padx=4)
        _center_on_parent(win, self)
        self.wait_window(win)
        if not state["ok"]:
            return None
        result = {}
        for key, var in entries.items():
            try: v = float(var.get().strip())
            except ValueError: v = 0.0
            if v > 0:
                buf_m = v if metric else v * 0.3048
                result[key] = buf_m
                c, y, name = key
                f = load_field(c, y, name)
                if f:
                    f["shelter_buffer_m"] = str(buf_m)
                    save_field(f)
                    if (str(self.current_field.get("company")) == c and
                            str(self.current_field.get("year")) == y and
                            str(self.current_field.get("Name")) == name):
                        self.current_field["shelter_buffer_m"] = str(buf_m)
        return result

    def _generate(self):
        # ── Window 1: field picker ─────────────────────────────────────────
        dlg1 = _ExportFieldPicker(self)
        self.wait_window(dlg1)
        if not dlg1.result:
            return
        selected_fields = dlg1.result

        # ── Window 2: output type picker ───────────────────────────────────
        dlg2 = _ExportTypePicker(self)
        self.wait_window(dlg2)
        if not dlg2.result:
            return
        opts = dlg2.result

        # ── Zero-buffer handling (JD Buffer Zones requested) ───────────────
        # Any selected field with a 0 buffer is offered an editable size right
        # here; entering a value saves it to that field and includes its buffer
        # zone in the export. Leaving 0 simply skips buffer zones for that field.
        if opts["jd"]:
            metric_now=self.unit_var.get()=="Metric"
            zero_fields=[]
            for c,y,name in selected_fields:
                f=load_field(c,y,name)
                if not f: continue
                try: bm=float(f.get("shelter_buffer_m") or 0)
                except (ValueError,TypeError): bm=0.0
                if bm<=0: zero_fields.append((c,y,name,str(f.get("Name") or name)))
            if zero_fields:
                res=self._prompt_zero_buffers(zero_fields, metric_now)
                if res is None:
                    self._status("Export cancelled."); return

        # ── Output name: "Shelter Maps_<Company>_v<N>" ─────────────────────
        # Version N auto-increments: scan Downloads for existing exports of
        # this company and use the next free number. The export is delivered
        # as a single .zip so it shows a real size in Explorer's Size column
        # (folders never do) and sorts to the top by size or date.
        cos=set(c for c,y,n in selected_fields)
        co_tag=list(cos)[0] if len(cos)==1 else "MultiCompany"
        co_tag=re.sub(r"[^A-Za-z0-9 _-]+","_",co_tag).strip("_ ") or "export"
        dl_dir=Path.home()/"Downloads"
        _ver=1
        try:
            _pat=re.compile(re.escape("Shelter Maps_%s_v"%co_tag)+r"(\d+)$")
            _used=[]
            for _p in dl_dir.glob("Shelter Maps_%s_v*"%co_tag):
                _stem=_p.stem if _p.suffix.lower()==".zip" else _p.name
                _m=_pat.match(_stem)
                if _m:
                    try: _used.append(int(_m.group(1)))
                    except ValueError: pass
            if _used: _ver=max(_used)+1
        except Exception:
            _ver=1
        base_name="Shelter Maps_%s_v%d"%(co_tag,_ver)
        out_dir=dl_dir/base_name
        zip_path=dl_dir/(base_name+".zip")
        metric=self.unit_var.get()=="Metric"
        self._log("Generating %d field(s) → %s"%(len(selected_fields),out_dir)); self.update()

        # ── Export thread ──────────────────────────────────────────────────
        def run():
            try:
                ok=0
                for c,y,name in selected_fields:
                    f=load_field(c,y,name)
                    if not f: continue
                    try:
                        pivotpoint=(float(f["PP_Longitude"]),float(f["PP_Latitude"]))
                    except (KeyError,ValueError,TypeError):
                        self.after(0,lambda n=name:self._log("  skipped %s — no pivot point"%n)); continue
                    positions=self._final_shelter_positions(f,metric)
                    if not positions:
                        self.after(0,lambda n=name:self._log("  skipped %s — no shelters"%n)); continue
                    fname=str(f.get("Name") or name).strip()
                    try: buf_m=float(f.get("shelter_buffer_m") or 0)
                    except (ValueError,TypeError): buf_m=0.0
                    outer_boundary=f.get("boundary_polygon") or None
                    _spray_a = str(f.get("Spray_angle") or "").strip()
                    _plant_a = str(f.get("Planting_angle") or "").strip()
                    _ab_angle = float(_plant_a or _spray_a or 0)
                    maketentgrid.export_field_outputs(
                        positions, pivotpoint, str(out_dir), fname,
                        buffer_radius_m=buf_m,
                        outer_boundary=outer_boundary,
                        write_agps=opts["agps"],
                        write_jd=opts["jd"],
                        write_kml=opts["kml"],
                        write_geojson=opts["geojson"],
                        write_boundary=opts["boundary"],
                        angle=_ab_angle,
                    )
                    ok+=1
                    self.after(0,lambda n=fname,k=len(positions):self._log("  ✓ %s (%d shelters)"%(n,k)))
                self.after(0,lambda:self._log("Done. %d/%d fields exported."%(ok,len(selected_fields))))

                # ── Package as a single .zip so it shows a size in Explorer ──
                # Folders never report a size in the Size column; a .zip does,
                # so the latest export sorts to the top by size (or date).
                final_path=out_dir
                try:
                    if os.path.isdir(out_dir):
                        if zip_path.exists():
                            try: zip_path.unlink()
                            except Exception: pass
                        with zipfile.ZipFile(str(zip_path),"w",zipfile.ZIP_DEFLATED) as zf:
                            for root,_dirs,files in os.walk(out_dir):
                                for fn in files:
                                    fp=os.path.join(root,fn)
                                    zf.write(fp,os.path.relpath(fp,out_dir))
                        shutil.rmtree(out_dir,ignore_errors=True)
                        final_path=zip_path
                        _sz=zip_path.stat().st_size
                        _szmb=_sz/(1024*1024)
                        self.after(0,lambda s=_szmb:self._log("  Packaged → %s (%.1f MB)"%(zip_path.name,s)))
                except Exception:
                    import traceback as _tb
                    self.after(0,lambda m=_tb.format_exc():self._log("  (zip skipped: %s)"%m.splitlines()[-1]))
                    final_path=out_dir

                self.after(0,lambda fp=final_path:tkinter.messagebox.showinfo("Done",
                    "%d field(s) exported to:\n%s\n\n"
                    "Extract the .zip, then:\n"
                    "\n"
                    "Trimble: copy AgGPS\\ folder to USB root.\n"
                    "\n"
                    "John Deere (John Deere Shelter Buffer Zones\\ folder):\n"
                    "  Upload Files → Internal Boundaries → drop\n"
                    "    {field}_Shelter_Buffer_Zones_shp.zip\n"
                    "\n"
                    "Google Earth: open Shelter Pins KML\\{field}_Shelter_Pins.kml.\n"
                    "\n"
                    "Boundary Files\\ has the field boundary as shapefile and KML\n"
                    "(fields without a drawn boundary are skipped)."
                    %(ok,fp)))
                # Open Downloads with the new .zip selected so it's easy to find.
                try:
                    if final_path.suffix.lower()==".zip":
                        self.after(0,lambda fp=final_path:subprocess.Popen(
                            ["explorer","/select,",str(fp)]))
                    else:
                        self.after(0,lambda fp=final_path:os.startfile(str(fp)))
                except Exception: pass
            except Exception:
                import traceback as tb; msg=tb.format_exc()
                self.after(0,lambda:self._log("ERROR:\n"+msg))
                self.after(0,lambda:tkinter.messagebox.showerror("Error",msg[:600]))
        threading.Thread(target=run,daemon=True).start()

    # ── Field Summary PDF ──────────────────────────────────────────────────────
    def _export_field_pdf(self):
        """Step 1 — Field picker (same pattern as Generate Output Files)."""
        dlg1 = _ExportFieldPicker(self)
        self.wait_window(dlg1)
        if not dlg1.result: return
        selected = dlg1.result   # [(company, year, name), ...]

        # ── Step 2: Options dialog ─────────────────────────────────────────
        dlg = ctk.CTkToplevel(self)
        dlg.title("Field Summary PDF — Options")
        dlg.resizable(False, False)
        dlg.grab_set(); dlg.lift(); dlg.focus_force()
        pw = self.winfo_width(); ph = self.winfo_height()
        px = self.winfo_rootx(); py = self.winfo_rooty()
        is_single = len(selected) == 1
        dw, dh = 440, 225
        dlg.geometry(f"{dw}x{dh}+{px+(pw-dw)//2}+{py+(ph-dh)//2}")

        pad = ctk.CTkFrame(dlg, fg_color="transparent")
        pad.pack(fill="both", expand=True, padx=18, pady=14)

        if is_single:
            co0, yr0, nm0 = selected[0]
            ctk.CTkLabel(pad, text=f"{nm0}  ·  {co0}  ·  {yr0}",
                         text_color=UI_MUTED).pack(anchor="w", pady=(0,8))
        else:
            ctk.CTkLabel(pad, text=f"{len(selected)} fields selected",
                         text_color=UI_MUTED).pack(anchor="w", pady=(0,8))

        ctk.CTkLabel(pad, text="Pin Labels:").pack(anchor="w")
        label_var = tk.StringVar(value="Shelter Numbers")
        ctk.CTkSegmentedButton(pad,
                               values=["Shelter Numbers", "Tray Counts", "None"],
                               variable=label_var).pack(anchor="w", pady=(2,10))

        # Save path — file for single, folder for batch
        def _versioned_pdf_name(directory, base_stem):
            """Return first non-existing filename: base_stem.pdf, base_stem 2.pdf, ..."""
            p = Path(directory) / f"{base_stem}.pdf"
            if not p.exists():
                return str(p), f"{base_stem}.pdf"
            n = 2
            while True:
                p = Path(directory) / f"{base_stem} {n}.pdf"
                if not p.exists():
                    return str(p), f"{base_stem} {n}.pdf"
                n += 1

        if is_single:
            co0, yr0, nm0 = selected[0]
            safe = re.sub(r"[^A-Za-z0-9_\- ]+","",nm0).strip()
            base_stem = f"{safe} {yr0} Shelter Map" if safe else f"Field {yr0} Shelter Map"
            default_path, default_name = _versioned_pdf_name(Path.home()/"Downloads", base_stem)
            save_var = tk.StringVar(value=default_path)
            pr = ctk.CTkFrame(pad, fg_color="transparent"); pr.pack(fill="x")
            ctk.CTkEntry(pr, textvariable=save_var, width=310).pack(side="left", padx=(0,6))
            def _browse_file():
                p = filedialog.asksaveasfilename(
                    defaultextension=".pdf", filetypes=[("PDF files","*.pdf")],
                    initialfile=default_name,
                    initialdir=str(Path.home()/"Downloads"),
                    title="Save Field Summary PDF")
                if p: save_var.set(p)
            ctk.CTkButton(pr, text="Browse…", width=80,
                          command=_browse_file).pack(side="left")
        else:
            save_dir_var = tk.StringVar(value=str(Path.home()/"Downloads"))
            pr = ctk.CTkFrame(pad, fg_color="transparent"); pr.pack(fill="x")
            ctk.CTkEntry(pr, textvariable=save_dir_var, width=310).pack(side="left", padx=(0,6))
            def _browse_dir():
                d = filedialog.askdirectory(initialdir=str(Path.home()/"Downloads"),
                                            title="Save PDFs to folder")
                if d: save_dir_var.set(d)
            ctk.CTkButton(pr, text="Browse…", width=80,
                          command=_browse_dir).pack(side="left")

        result = {"go": False}
        def _ok(): result["go"] = True; dlg.destroy()
        btn_row = ctk.CTkFrame(pad, fg_color="transparent"); btn_row.pack(pady=(12,0))
        ctk.CTkButton(btn_row, text="Generate PDF" if is_single else "Generate PDFs",
                      fg_color="#1a5c8a", command=_ok).pack(side="left", padx=(0,8))
        ctk.CTkButton(btn_row, text="Cancel", fg_color="#555",
                      command=dlg.destroy).pack(side="left")

        self.wait_window(dlg)
        if not result["go"]: return

        label_mode = {"Shelter Numbers": "shelters",
                      "Tray Counts":     "trays",
                      "None":            "off"}[label_var.get()]

        if is_single:
            co0, yr0, nm0 = selected[0]
            pdf_paths = {(co0,yr0,nm0): save_var.get().strip()}
        else:
            sdir = Path(save_dir_var.get())
            pdf_paths = {}
            for co,yr,nm in selected:
                safe = re.sub(r"[^A-Za-z0-9_\- ]+","",nm).strip()
                base_stem = f"{safe} {yr} Shelter Map" if safe else f"Field {yr} Shelter Map"
                path, _ = _versioned_pdf_name(sdir, base_stem)
                pdf_paths[(co,yr,nm)] = path

        if not any(pdf_paths.values()):
            self._status("PDF export cancelled."); return

        # ── Step 3: Queue and process fields sequentially ──────────────────
        self._pdf_queue      = list(selected)
        self._pdf_paths      = pdf_paths
        self._pdf_label_mode = label_mode
        self._pdf_done       = []
        self._process_next_pdf()

    # ── Map-image cache helpers ────────────────────────────────────────────
    def _pdf_cache_paths(self, co, yr, name):
        base = Path(__file__).parent / "fields" / co / yr
        return base / f"{name}_map.jpg", base / f"{name}_map.json"

    def _pdf_load_cache(self, co, yr, name):
        """Return (PIL.Image, True) if a valid cached map image exists, else (None, False)."""
        try:
            from PIL import Image as _PI
            img_p, meta_p = self._pdf_cache_paths(co, yr, name)
            fld_p = Path(__file__).parent / "fields" / co / yr / f"{name}.json"
            if not img_p.exists() or not meta_p.exists():
                return None, False
            meta = json.loads(meta_p.read_text(encoding='utf-8'))
            if meta.get('label_mode') != self._pdf_label_mode:
                return None, False
            if img_p.stat().st_mtime < fld_p.stat().st_mtime:
                return None, False          # field updated since cache was saved
            return _PI.open(str(img_p)).copy(), True
        except Exception:
            return None, False

    def _pdf_save_cache(self, map_img, co, yr, name):
        """Persist map screenshot so future PDF runs for this field skip tile polling."""
        try:
            img_p, meta_p = self._pdf_cache_paths(co, yr, name)
            map_img.save(str(img_p), 'JPEG', quality=85)
            meta_p.write_text(
                json.dumps({'label_mode': self._pdf_label_mode}), encoding='utf-8')
        except Exception:
            pass

    def _process_next_pdf(self):
        """Load next field in queue, then schedule screenshot."""
        if not self._pdf_queue:
            n = len(self._pdf_done)
            self._status(f"PDF{'s' if n!=1 else ''} saved ({n} field{'s' if n!=1 else ''})")
            if self._pdf_done:
                try: os.startfile(str(Path(self._pdf_done[-1]).parent))
                except Exception: pass
            return

        co, yr, name = self._pdf_queue[0]
        is_cur = (str(self.current_field.get("company",""))==co and
                  str(self.current_field.get("year",""))==yr and
                  str(self.current_field.get("Name",""))==name)

        # ── Fast path: use cached map image ────────────────────────────────
        cached_img, cache_ok = self._pdf_load_cache(co, yr, name)
        if cache_ok:
            save_path = self._pdf_paths.get((co, yr, name), "")
            if save_path:
                try:
                    self._build_field_pdf(cached_img, self._pdf_label_mode, save_path)
                    self._pdf_done.append(save_path)
                except Exception:
                    import traceback as _tb
                    _s = _tb.format_exc()
                    tkinter.messagebox.showerror("PDF Error",
                        _s[-900:] if len(_s) > 900 else _s)
            self._pdf_queue.pop(0)
            self.after(50, self._process_next_pdf)
            return

        # ── Slow path: tile polling ─────────────────────────────────────────
        if not is_cur:
            self._activate_field_impl(co, yr, name)
        self.update()
        # Poll canvas stability instead of a fixed delay
        self._pdf_poll_start    = time.time()
        self._pdf_poll_prev     = None
        self._pdf_poll_stable   = 0
        self.after(400, self._pdf_poll_tiles)

    def _pdf_poll_tiles(self):
        """Fire every 300 ms; proceed once map tiles stop changing or time out."""
        STABLE_NEED = 3        # 3 identical frames = stable
        MAX_WAIT    = 8.0      # hard ceiling (seconds)
        INTERVAL    = 300      # ms between checks

        try:
            from PIL import ImageGrab
            cw  = self.map_widget
            try: sc = self.winfo_fpixels('1i') / 96.0
            except Exception: sc = 1.0
            bx = int(cw.winfo_rootx() * sc); by = int(cw.winfo_rooty() * sc)
            bw = int(cw.winfo_width()  * sc); bh = int(cw.winfo_height() * sc)
            th = ImageGrab.grab(bbox=(bx, by, bx+bw, by+bh)).resize((80, 60))
            h  = hash(th.tobytes())
        except Exception:
            h = None

        if h is not None and h == self._pdf_poll_prev:
            self._pdf_poll_stable += 1
        else:
            self._pdf_poll_stable = 0
        self._pdf_poll_prev = h

        if self._pdf_poll_stable >= STABLE_NEED or \
                (time.time() - self._pdf_poll_start) >= MAX_WAIT:
            self._pdf_pre_screenshot()
        else:
            self.after(INTERVAL, self._pdf_poll_tiles)

    def _pdf_pre_screenshot(self):
        """Enable shelters + set label mode, then wait for canvas to settle."""
        self.show_shelters.set(True)
        self.shelters_visible_var.set(True)
        self._pdf_old_mode  = self.pin_label_mode
        self.pin_label_mode = self._pdf_label_mode
        self._redraw_shelters()
        self.update_idletasks()
        self.after(500, self._pdf_do_screenshot)

    def _capture_canvas_gdi(self, canvas):
        """Capture a tkinter Canvas via Windows GDI PrintWindow.
        Works even when other windows (taskbar, etc.) overlap the widget.
        Falls back to PIL ImageGrab if GDI fails."""
        try:
            import ctypes, struct
            from PIL import Image as _PIL
            hwnd = canvas.winfo_id()
            try:
                scale = self.winfo_fpixels('1i') / 96.0
            except Exception:
                scale = 1.0
            w = max(1, int(canvas.winfo_width()  * scale))
            h = max(1, int(canvas.winfo_height() * scale))
            gdi32  = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            hdc_src = user32.GetDC(hwnd)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_src)
            hbm     = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
            old_bm  = gdi32.SelectObject(hdc_mem, hbm)
            # PW_CLIENTONLY=1 | PW_RENDERFULLCONTENT=2 (Win8+)
            user32.PrintWindow(hwnd, hdc_mem, 3)
            # DIB header: 32-bit top-down
            bmi = struct.pack('=IIIHHIIIIII', 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, bmi, 0)
            gdi32.SelectObject(hdc_mem, old_bm)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_src)
            return _PIL.frombuffer('RGBA', (w, h), buf.raw, 'raw', 'BGRA', 0, 1).convert('RGB')
        except Exception:
            # Fallback: plain screen grab
            from PIL import ImageGrab
            try: scale = self.winfo_fpixels('1i') / 96.0
            except Exception: scale = 1.0
            x = canvas.winfo_rootx(); y = canvas.winfo_rooty()
            w = canvas.winfo_width();  h = canvas.winfo_height()
            return ImageGrab.grab(bbox=(
                int(x*scale), int(y*scale),
                int((x+w)*scale), int((y+h)*scale)))

    def _pdf_do_screenshot(self):
        """Capture the map widget and build the PDF, then advance the queue."""
        co, yr, name = self._pdf_queue[0]
        save_path = self._pdf_paths.get((co,yr,name),"")

        try:
            cw = self.map_widget
            cw.canvas.update()
            map_img = self._capture_canvas_gdi(cw.canvas)
        except Exception as ex:
            tkinter.messagebox.showerror("Screenshot Failed", str(ex))
            map_img = None

        # Restore label mode immediately
        self.pin_label_mode = self._pdf_old_mode
        self._redraw_shelters()

        if map_img is not None and save_path:
            try:
                self._build_field_pdf(map_img, self._pdf_label_mode, save_path)
                self._pdf_done.append(save_path)
                # Save for future runs (next generation of this field is instant)
                self._pdf_save_cache(map_img, co, yr, name)
            except Exception:
                import traceback as _tb
                _tb_str = _tb.format_exc()
                tkinter.messagebox.showerror("PDF Error",
                    _tb_str[-900:] if len(_tb_str) > 900 else _tb_str)

        self._pdf_queue.pop(0)
        self._process_next_pdf()

    def _build_field_pdf(self, map_img, label_mode, save_path):
        """Construct and write the Field Summary PDF to *save_path*."""
        import fpdf as _fpdf
        import tempfile, math as _math

        f = self.current_field

        # ── Gather field data ──────────────────────────────────────────────
        field_name = str(f.get("Name","") or "").strip() or "Untitled"
        company    = str(f.get("company","") or "").strip() or "—"
        year       = str(f.get("year","") or "").strip() or "—"
        lld        = str(f.get("lld","") or "").strip() or "—"

        acres_manual = str(f.get("acres","") or "").strip()
        if acres_manual:
            try:    acres_disp = f"{float(acres_manual):.1f} ac"
            except  ValueError: acres_disp = acres_manual
            acres_f = float(acres_manual) if acres_manual else 0.0
        else:
            bp  = f.get("boundary_polygon") or []
            ac  = polygon_area_m2(bp) * ACRES_PER_M2 if len(bp) >= 3 else 0.0
            acres_disp = f"{ac:.1f} ac" if ac > 0 else "—"
            acres_f = ac

        plant_a  = str(f.get("Planting_angle","") or "").strip()
        spray_a  = str(f.get("Spray_angle","") or "").strip()
        plant_disp  = f"{plant_a}°" if plant_a else "—"
        spray_disp  = f"{spray_a}°" if spray_a else "—"

        sw = str(f.get("Sprayer_width","") or "").strip()
        sw_disp = f"{sw} ft" if sw else "—"

        rs = str(f.get("row_spacing_in","") or "").strip()
        rs_disp = f"{rs} in" if rs else "—"

        total_rows_s = str(f.get("total_rows","") or "").strip()
        nf_s = str(f.get("num_female_rows","") or "").strip()
        nm_s = str(f.get("num_male_rows","") or "").strip()
        gap_s = str(f.get("bay_gap_in","") or "").strip()
        gap_disp = f"{gap_s} in" if gap_s else "0 in"

        try:
            nf_i = int(nf_s or 8); nm_i = int(nm_s or 2)
            tr_i = int(total_rows_s or (nf_i + nm_i))
            layout_key   = f.get("row_layout", "centered")
            custom_mask  = str(f.get("custom_row_mask","") or "").strip()
            layout_disp  = self._row_layout_inverse.get(layout_key, layout_key).title()
            row_mask     = maketentgrid.resolve_row_mask(nf_i, nm_i, layout_key, custom_mask, tr_i)
        except Exception:
            layout_disp = "—"; row_mask = "—"

        has_planter = bool(f.get("planter_passes"))
        has_sprayer = bool(f.get("sprayer_passes"))
        upload_parts = []
        if has_planter: upload_parts.append("Planter GPS")
        if has_sprayer: upload_parts.append("Sprayer GPS")
        uploaded_disp = " + ".join(upload_parts) if upload_parts else None

        # ── Bee allocation ─────────────────────────────────────────────────
        n_shelters   = len(self.shelter_positions)
        shelters_disp = str(n_shelters) if n_shelters else "—"

        gpa_s = str(f.get("gals_per_acre","") or "").strip()
        gpt_s = str(f.get("gals_per_tray","") or "").strip()
        gpa_disp = f"{gpa_s} gal/ac" if gpa_s else "—"
        gpt_disp = f"{gpt_s} gal/tray" if gpt_s else "—"

        total_gals_disp = "—"; total_trays_disp = "—"; trays_per_disp = "—"
        if gpa_s and gpt_s and acres_f > 0:
            try:
                gpa_f = float(gpa_s); gpt_f = float(gpt_s)
                tot_gals = gpa_f * acres_f
                total_gals_disp = f"{tot_gals:.1f} gal"
                if self.shelter_tray_counts and n_shelters:
                    tot_tr = sum(self.shelter_tray_counts)
                    mn = min(self.shelter_tray_counts); mx = max(self.shelter_tray_counts)
                    total_trays_disp = str(tot_tr)
                    trays_per_disp   = str(mn) if mn == mx else f"{mn}–{mx}"
                elif gpt_f > 0 and n_shelters:
                    tot_tr = max(int(_math.ceil(tot_gals / gpt_f)), n_shelters)
                    total_trays_disp = str(tot_tr)
                    base = tot_tr // n_shelters; ext = tot_tr % n_shelters
                    trays_per_disp   = str(base) if ext == 0 else f"{base}–{base+1}"
            except (ValueError, ZeroDivisionError): pass

        dist_key  = f.get("tray_distribution","even")
        dist_disp = self._tray_dist_inverse.get(dist_key, dist_key).title()
        outside_disp = ("Yes" if str(f.get("shelters_in_outside_pass","Yes")
                                    ).strip().lower() == "yes" else "No")
        label_disp = {"shelters": "Shelter Numbers",
                      "trays":    "Tray Counts",
                      "off":      "None"}[label_mode]

        # ── Colour palette ─────────────────────────────────────────────────
        NAVY  = (30,  58,  95)
        GOLD  = (213, 160, 23)
        HBGR  = (245, 247, 250)   # header background
        MGRAY = (140, 152, 170)   # muted label text
        DBDR  = (200, 210, 222)   # divider / border
        ALTBG = (237, 242, 248)   # alternating table row
        WHITE = (255, 255, 255)

        # ── Page geometry (mm) ─────────────────────────────────────────────
        PW = 215.9; PH = 279.4
        ML = 15.0;  MR = 15.0
        CW = PW - ML - MR          # 185.9

        pdf = _fpdf.FPDF('P', 'mm', 'Letter')
        pdf.add_page()
        pdf.set_margins(0, 0, 0)
        pdf.set_auto_page_break(False)

        # ── Drawing helpers ────────────────────────────────────────────────
        def _fill(x, y, w, h, rgb):
            pdf.set_fill_color(*rgb)
            pdf.rect(x, y, w, h, 'F')

        def _hline(x, y, w, rgb, lw=0.3):
            pdf.set_draw_color(*rgb)
            pdf.set_line_width(lw)
            pdf.line(x, y, x + w, y)

        def _vline(x, y1, y2, rgb, lw=0.3):
            pdf.set_draw_color(*rgb)
            pdf.set_line_width(lw)
            pdf.line(x, y1, x, y2)

        def _txt(x, y, w, h, s, font='Helvetica', style='', size=9,
                 rgb=NAVY, align='L'):
            # fpdf v1 encodes to latin-1; strip anything outside that range
            safe = (str(s)
                    .replace('—', '--')   # em dash
                    .replace('–', '-')    # en dash
                    .replace('‒', '-')    # figure dash
                    .encode('latin-1', errors='replace')
                    .decode('latin-1'))
            pdf.set_font(font, style, size)
            pdf.set_text_color(*rgb)
            pdf.set_xy(x, y)
            pdf.cell(w, h, safe, align=align)

        # ── GOLD TOP BAR ───────────────────────────────────────────────────
        _fill(0, 0, PW, 3, GOLD)

        # ── HEADER BACKGROUND ─────────────────────────────────────────────
        _fill(0, 3, PW, 37, HBGR)

        # ── LOGO ──────────────────────────────────────────────────────────
        LQSEP = ML + 82           # x of vertical separator (97 mm)
        LQ_CX = LQSEP / 2        # horizontal centre of left quadrant
        HDR_TOP = 3; HDR_BOT = 38  # header content band
        logo_path = str(Path(__file__).parent / "assets" / "pdflogo.png")
        if os.path.exists(logo_path):
            try:
                from PIL import Image as _LI
                _logo_img = _LI.open(logo_path)
                _lw, _lh  = _logo_img.size
                aspect    = _lh / _lw
                # Composite RGBA onto header background so fpdf gets plain RGB
                if _logo_img.mode in ('RGBA', 'LA', 'P'):
                    _bg = _LI.new('RGB', (_lw, _lh), HBGR)
                    if _logo_img.mode == 'P':
                        _logo_img = _logo_img.convert('RGBA')
                    _bg.paste(_logo_img, mask=_logo_img.split()[-1]
                              if _logo_img.mode in ('RGBA', 'LA') else None)
                    _logo_img = _bg
                _logo_tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                _logo_tmp.close()
                _logo_img.save(_logo_tmp.name, 'PNG')
                _logo_embed = _logo_tmp.name
            except Exception:
                aspect = 1.446
                _logo_embed = logo_path
                _logo_tmp   = None
            logo_w = min(22, (HDR_BOT - HDR_TOP - 10) / aspect)  # 5 mm pad top+bottom
            logo_h = logo_w * aspect
            logo_x = LQ_CX - logo_w / 2
            logo_y = HDR_TOP + (HDR_BOT - HDR_TOP - logo_h) / 2
            pdf.image(_logo_embed, logo_x, logo_y, logo_w)
            try:
                if _logo_tmp: os.unlink(_logo_tmp.name)
            except Exception:
                pass

        # ── Vertical separator ─────────────────────────────────────────────
        _vline(ML + 82, 7, 37, DBDR, 0.4)

        # ── Field info (right of separator) ───────────────────────────────
        rx = ML + 85;  rw = PW - MR - rx
        _txt(rx, 13, rw, 9,  field_name, 'Helvetica', 'B', 18, NAVY, 'C')
        _txt(rx, 25, rw, 6,  f"{company}  ·  {year}", 'Helvetica', '', 11,
             (80, 100, 120), 'C')
        try:
            date_str = datetime.date.today().strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            date_str = str(datetime.date.today())

        # ── GOLD BOTTOM ACCENT ─────────────────────────────────────────────
        _fill(0, 38, PW, 2.5, GOLD)

        # ── MAP IMAGE ─────────────────────────────────────────────────────
        MAP_Y = 42.5;  MAP_H = 124.0
        # Border frame
        _fill(ML - 0.5, MAP_Y - 0.5, CW + 1, MAP_H + 1, DBDR)

        # Resize map image to fixed target (fills box, slight stretch is OK)
        from PIL import Image as _PILImage
        # Crop source image to match the PDF box aspect ratio before resizing
        # so the image is never stretched/warped.
        src_w, src_h = map_img.size
        pdf_aspect = CW / MAP_H          # target width-to-height ratio
        src_aspect = src_w / src_h
        if src_aspect > pdf_aspect + 0.01:
            new_w = int(src_h * pdf_aspect)
            x0 = (src_w - new_w) // 2
            map_img = map_img.crop((x0, 0, x0 + new_w, src_h))
        elif src_aspect < pdf_aspect - 0.01:
            new_h = int(src_w / pdf_aspect)
            y0 = (src_h - new_h) // 2
            map_img = map_img.crop((0, y0, src_w, y0 + new_h))
        target_px_w = 1600; target_px_h = int(target_px_w / pdf_aspect)
        map_resized = map_img.resize((target_px_w, target_px_h), _PILImage.LANCZOS)
        _tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        _tmp.close()
        try:
            map_resized.save(_tmp.name, 'JPEG', quality=90)
            pdf.image(_tmp.name, ML, MAP_Y, CW, MAP_H)
        finally:
            try: os.unlink(_tmp.name)
            except Exception: pass

        # Caption below map
        cap_y = MAP_Y + MAP_H + 2
        _txt(ML, cap_y, CW, 4,
             f"Yellow pins = shelter positions  ·  Labels: {label_disp}",
             'Helvetica', 'I', 7.5, MGRAY, 'C')

        # ── SECTION HEADER BAR ─────────────────────────────────────────────
        sec_y = cap_y + 6
        _fill(0, sec_y, PW, 8.5, NAVY)
        _txt(ML + 2, sec_y + 1.5, CW * 0.48, 5.5,
             "FIELD DETAILS", 'Helvetica', 'B', 8.5, WHITE)
        _txt(ML + CW * 0.51, sec_y + 1.5, CW * 0.48, 5.5,
             "BEE ALLOCATION", 'Helvetica', 'B', 8.5, WHITE, 'L')

        # ── DATA TABLES ───────────────────────────────────────────────────
        ROW_H  = 6.4
        tbl_y  = sec_y + 9.5
        HALF   = (CW - 5) / 2   # each panel ~90mm
        LGAP   = 3               # gap between panels (label side)

        def _data_row(px, py, pw, label, value, alt=False, small_val=False):
            _fill(px, py, pw, ROW_H, ALTBG if alt else WHITE)
            # Label
            _txt(px + 2.5, py + 1.3, pw * 0.50, ROW_H - 1.5,
                 label, 'Helvetica', '', 8, MGRAY, 'L')
            # Value
            vs = 7 if small_val else 8
            _txt(px + pw * 0.50, py + 1.3, pw * 0.48, ROW_H - 1.5,
                 value, 'Helvetica', 'B', vs, NAVY, 'R')

        # Left panel — Field Details
        left_rows = [
            ("Legal Description", lld),
            ("Acres",             acres_disp),
            ("Planting Angle",    plant_disp),
            ("Spraying Angle",    spray_disp),
            ("Sprayer Width",     sw_disp),
            ("Row Spacing",       rs_disp),
            ("Total Rows on Planter", total_rows_s or "—"),
            ("Female Rows (per unit)", nf_s or "—"),
            ("Male Rows (per unit)",   nm_s or "—"),
            ("Gap Between Bays",  gap_disp),
            ("Planter Layout",    row_mask or "—"),
        ]
        if uploaded_disp:
            left_rows.append(("Uploaded Data", uploaded_disp))

        for i, (lbl, val) in enumerate(left_rows):
            _data_row(ML, tbl_y + i * ROW_H, HALF,
                      lbl, val, alt=(i % 2 == 1),
                      small_val=(lbl == "Planter Layout" and len(val) > 18))

        # Right panel — Bee Allocation
        right_rows = [
            ("Total Shelters",         shelters_disp),
            ("Gals / Acre",            gpa_disp),
            ("Total Gals",             total_gals_disp),
            ("Gals / Tray",            gpt_disp),
            ("Total Trays",            total_trays_disp),
            ("Trays per Shelter",      trays_per_disp),
            ("Tray Distribution",      dist_disp),
            ("Shelters in Outside Pass", outside_disp),
        ]
        rx2 = ML + HALF + LGAP + 2
        pw2 = HALF - LGAP

        for i, (lbl, val) in enumerate(right_rows):
            _data_row(rx2, tbl_y + i * ROW_H, pw2,
                      lbl, val, alt=(i % 2 == 1))

        # Thin vertical line between panels
        mid_x = ML + HALF + (LGAP + 2) / 2
        _vline(mid_x, sec_y, tbl_y + len(left_rows) * ROW_H, DBDR, 0.3)

        # ── FOOTER ────────────────────────────────────────────────────────
        foot_y = PH - 9
        _hline(0, foot_y - 2.5, PW, GOLD, 1.5)
        _txt(ML, foot_y, CW * 0.5, 5,
             "Shelter Mapping App  ·  TNT Pollination",
             'Helvetica', '', 7, MGRAY, 'L')
        _txt(ML + CW * 0.5, foot_y, CW * 0.5, 5,
             f"Generated: {date_str}",
             'Helvetica', 'I', 7, MGRAY, 'R')

        # ── WRITE FILE ────────────────────────────────────────────────────
        pdf.output(save_path, 'F')

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _status(self,msg): self.status_lbl.configure(text=msg)

    def _log(self,text):
        # No log panel anymore — surface progress on the status line (last line).
        last=str(text).strip().splitlines()[-1] if str(text).strip() else ""
        self._status(last)


if __name__=="__main__":
    app=BeetentApp(); app.mainloop()
