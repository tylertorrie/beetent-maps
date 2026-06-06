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
import math, os, sys, threading, json, re, csv, datetime, zipfile, struct, glob
import subprocess
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

def inset_polygon_enu(poly_enu, dist):
    """Offset every edge of poly_enu inward by dist metres.

    Corner join strategy:
      * Concave corners (miter intersection lies beyond the edge, t >= 1):
        always use miter — the two offset edges converge naturally and the
        bevel would cut across the wrong side, creating a self-intersection.
      * Convex corners where the miter stays within miter_threshold of the
        original vertex (t < 1, d <= threshold): use miter — clean corner.
      * Convex corners with a deep miter spike (t < 1, d > threshold):
        use bevel — straight cut from one offset endpoint to the next,
        shortest possible path, never backtracks.
    """
    n=len(poly_enu)
    if n<3: return []
    cx=sum(e for e,_ in poly_enu)/n; cn=sum(nn for _,nn in poly_enu)/n
    edges=[]; src_vertex=[]
    for i in range(n):
        e1,n1=poly_enu[i]; e2,n2=poly_enu[(i+1)%n]
        dx2,dy2=e2-e1,n2-n1; L=math.sqrt(dx2*dx2+dy2*dy2)
        if L<1e-9: continue
        nx,ny=-dy2/L,dx2/L
        me,mn=(e1+e2)/2,(n1+n2)/2
        if (cx-me)*nx+(cn-mn)*ny<0: nx,ny=-nx,-ny
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
        # Concave corner OR miter not too deep: use miter.
        if t>=1.0 or d<=miter_threshold:
            result.append((ix,iy))
            continue
        # Sharp convex corner: bevel — straight cut, no spike.
        result.append(a[1])
        result.append(b[0])
    return result

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
                Spray_angle="0",Sprayer_width="133",
                shelter_mode="total",num_structures="",shelters_per_acre="",
                acres_per_shelter="",
                spacing="",shelter_spacing="",directional_offset="",
                row_spacing_in="22",num_female_rows="8",num_male_rows="2",
                total_rows="20",          # total rows on the planter (may > num_female + num_male if unit repeats)
                row_layout="centered",   # "outer" | "centered" | "custom"
                custom_row_mask="",       # only used when row_layout == "custom"
                use_bays=True,            # False = blanket-planted crop, no female-bay constraint
                outside_sprayer_pass="No",track_exclusion_ft="10",
                pass_edge_buffer_ft="0",    # 0 = no sprayer-pass kill zone (opt in via Sprayer → Set Edge Buffer)
                shelter_buffer_m="1.524",
                planter_passes=None,           # [[(lat,lon), ...], ...]  imported from JD
                use_imported_passes=True,      # when False or no data, use synthetic grid
                sprayer_passes=None,           # [[(lat,lon), ...], ...]  uploaded GPS sprayer tracks
                gals_per_acre="3",acres="",gals_per_tray="2",tray_distribution="even",
                boundary_polygon=None,pivot_tracks=[],corner_arms=[],
                boundary_inner=[],            # list of inner-exclusion polygons (JD-style "interior boundaries")
                sprayer_routes_around_inner=True,   # sprayer pass lines break around inner boundaries when True
                bays_through_inner=False,     # when True, planter bays draw through inner boundaries instead of clipping
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
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


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
        self.minsize(1000,650)
        self._set_window_icon()

        self.current_field = blank_field()
        self.click_mode    = None

        # Map overlays
        self.pivot_marker     = None
        self.field_circle     = None
        self.boundary_poly    = None
        self.boundary_pts     = []
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
        self.pin_label_mode     = "off"   # "off" | "trays" | "shelters" — what each pin shows
        self._shelter_undo      = []   # stack of (override_key, prev_value) for Reset Move
        self.shelter_tray_counts= []  # parallel to shelter_positions; per-shelter int
        self.moving_shelter_idx = None
        self._shelter_refresh_id= None
        self._all_popups        = []
        self.shelter_circle_var = tk.BooleanVar(value=False)
        self.field_labels       = {}

        # Drag system
        self._drag_registry = {}
        self._drag_item = None
        self._drag_track_idx = None   # index of pivot track being resized by band-drag
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
        self.lld_entry=ctk.CTkEntry(bar,width=230,placeholder_text="e.g. NW-32-14-22-W4")
        self.lld_entry.pack(side="left",pady=8)
        self.lld_entry.bind("<Return>",lambda e:self._search_lld())
        ctk.CTkButton(bar,text="Go",width=48,command=self._search_lld).pack(side="left",padx=(4,4),pady=8)
        # LLD highlight box toggle — the yellow rectangle around the searched
        # quarter section can get in the way once you're zoomed in working on
        # the field, so we let users hide/show it without re-searching.
        ctk.CTkSwitch(bar,text="LLD box",variable=self.show_lld_box,
                      command=self._toggle_lld_box,
                      font=ctk.CTkFont(family=FONT_LABEL,size=11)
                      ).pack(side="left",padx=(0,20),pady=8)
        self.status_lbl=ctk.CTkLabel(bar,text="",text_color=UI_MUTED,width=340,anchor="w")
        self.status_lbl.pack(side="left",padx=16)
        # Update-ready button — hidden until a code update is pulled.
        # _on_update_ready() packs it; clicking it restarts the process.
        self._update_btn=ctk.CTkButton(bar,text="🔄 Restart to update",
                                        fg_color="#1a6b3a",width=160,
                                        command=self._restart_app)
        # intentionally NOT packed here — shown on demand
        ctk.CTkLabel(bar,text="Units:").pack(side="right",padx=(0,4))
        self.unit_var=tk.StringVar(value="Feet")
        ctk.CTkComboBox(bar,variable=self.unit_var,values=["Feet","Metres"],
                        width=90,command=self._on_unit_change).pack(side="right",padx=(0,12))
        ctk.CTkButton(bar,text="⚙ Generate Output Files",fg_color="#1a5c8a",
                      font=ctk.CTkFont(family=FONT_LABEL,size=12),
                      command=self._generate).pack(side="right",padx=(0,12),pady=6)

    # ── Popup menu helpers ─────────────────────────────────────────────────────
    def _make_menu_btn(self, bar, label, items, color="#2b2b2b"):
        popup = ctk.CTkFrame(self, fg_color=UI_CARD, border_width=1, border_color=UI_BORDER, corner_radius=4)
        for item_label, item_cmd in items:
            ctk.CTkButton(popup, text=item_label, anchor="w", height=30,
                          fg_color="transparent", hover_color=UI_HOVER, text_color=UI_TEXT,
                          command=lambda p=popup, c=item_cmd: (p.place_forget(), c())
                          ).pack(fill="x", padx=2, pady=1)
        btn_ref = [None]
        btn = ctk.CTkButton(bar, text=label+" ▾", fg_color=color,
                            command=lambda p=popup, r=btn_ref: self._toggle_popup(p, r[0]))
        btn_ref[0] = btn
        self._all_popups.append(popup)
        return btn

    def _toggle_popup(self, popup, btn):
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
        val=self._ask_string("Track Exclusion",
                             f"Clear zone each side of pivot tracks (ft).  Current: {cur}")
        if val is None: return
        val=val.strip()
        if val:
            self.fv["track_exclusion_ft"].set(val)   # write-trace → _redraw_tracks
            self._status(f"Track exclusion set to {val} ft.")

    def _edit_pass_edge_buffer(self):
        """Adjust how far in from the edge of any sprayer pass shelters can
        sit. Middle of each pass becomes a kill zone of width
        max(0, sprayer_width − 2 × buffer); applies to BOTH the outside pass
        and every main pass through the field."""
        self._close_all_popups()
        cur = self.fv["pass_edge_buffer_ft"].get() or "30"
        val = self._ask_string("Sprayer Edge Buffer",
                                f"How far in from the edge of any sprayer pass shelters can sit (ft).\n"
                                f"Applies to the outside pass AND every main pass.\n"
                                f"Current: {cur}")
        if val is None: return
        val = val.strip()
        if val:
            self.fv["pass_edge_buffer_ft"].set(val)   # write-trace → _on_form_change
            self._status(f"Sprayer edge buffer set to {val} ft.")
            # Refresh the overlay if it's currently being shown.
            if self.show_pass_buffer_overlay.get():
                self._redraw_pass_buffer_overlay()

    def _toggle_pass_buffer_overlay(self):
        """Show/hide a red translucent overlay marking the sprayer kill zones
        (the middle of every sprayer pass + the middle of the outside pass).
        Lets the user visually verify where shelters can and cannot land."""
        self._close_all_popups()
        self.show_pass_buffer_overlay.set(not self.show_pass_buffer_overlay.get())
        self._redraw_pass_buffer_overlay()
        self._status("Sprayer buffer overlay " +
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
        self._pivot_btn = self._make_menu_btn(bb, "📍 Pivot", [
            ("Toggle on/off",           self._toggle_pivot),
            ("Set Pivot Point",         self._mode_pivot),
            ("Draw Track Circle",       self._mode_track),
            ("Edit Track Measurements", self._mode_edit_track_measurements),
            ("Set Track Exclusion (ft)",self._edit_track_exclusion),
            ("Add Corner Path",         self._mode_add_corner_path),
            ("Delete Corner Path",      self._mode_delete_corner_ui),
        ], color="#1a6b3a")
        self._pivot_btn.pack(side="left", padx=(0,4))

        self._bnd_btn = self._make_menu_btn(bb, "✏️ Boundary", [
            ("Draw Outer",          self._mode_boundary),
            ("Edit Outer",          self._mode_edit_boundary),
            ("Upload File",         self._upload_boundary),
            ("Delete Outer",        self._clear_boundary),
            ("Add Inner Boundary",  self._mode_add_inner_boundary),
            ("Delete Inner",        self._mode_delete_inner_boundary),
        ], color="#5a3a8a")
        self._bnd_btn.pack(side="left", padx=(0,4))

        self._sp_btn = self._make_menu_btn(bb, "🌊 Sprayer", [
            ("Toggle on/off",                   self._toggle_passes),
            ("Edit",                            self._mode_edit_passes),
            ("Import Sprayer Data (.shp/.geojson)", self._import_sprayer_data),
            ("Toggle Uploaded Paths on/off",    self._toggle_sprayer_passes),
            ("Clear Uploaded Paths",            self._clear_sprayer_data),
            ("Set Edge Buffer (ft)",            self._edit_pass_edge_buffer),
            ("Toggle Edge Buffer Overlay",      self._toggle_pass_buffer_overlay),
            ("Toggle Route Around Inner",       self._toggle_route_around_inner),
        ], color="#2a5a4a")
        self._sp_btn.pack(side="left", padx=(0,4))

        # Planter menu: synthetic bay overlay (from bay-calculator inputs) PLUS
        # imported planter passes from a John Deere Operations Center Seeding
        # shapefile (the actual path the planter took on this field).
        self._pl_btn = self._make_menu_btn(bb, "🌾 Planter", [
            ("Toggle Bays on/off",     self._toggle_bays),
            ("Edit",                   self._mode_edit_bays),
            ("Import Planter Data (.shp)", self._import_planter_data),
            ("Toggle Paths on/off",    self._toggle_planter_passes),
            ("Clear Planter Data",     self._clear_planter_passes),
            ("Toggle Bays Through Inner", self._toggle_bays_through_inner),
        ], color="#3a5a1a")
        self._pl_btn.pack(side="left", padx=(0,4))

        self._shelter_btn = self._make_menu_btn(bb, "🏠 Shelters", [
            ("Toggle Pins",          self._toggle_shelters),
            ("Add Shelter Pin",      self._mode_add_shelter),
            ("Numbers: Tray count",  lambda: self._set_pin_mode("trays")),
            ("Numbers: Shelter #",   lambda: self._set_pin_mode("shelters")),
            ("Numbers: Off",         lambda: self._set_pin_mode("off")),
            ("Toggle Buffer Zone",   self._toggle_shelter_buffers),
            ("Set Buffer Size",      self._edit_shelter_buffer),
        ], color="#5a3000")
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
        self.field_tree=ttk.Treeview(lf,columns=("field","company","year"),show="headings",
                                     height=7,style="Fields.Treeview",selectmode="browse")
        for col,label,w,anchor in (("field","Field",130,"w"),("company","Company",110,"w"),("year","Year",55,"center")):
            self.field_tree.heading(col,text=label,command=lambda c=col:self._sort_fields(c))
            self.field_tree.column(col,width=w,anchor=anchor,stretch=(col=="field"))
        self.field_tree.pack(fill="x")
        self.field_tree.bind("<<TreeviewSelect>>",self._on_field_select)
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
        fpr=ctk.CTkFrame(fd,fg_color="transparent"); fpr.pack(fill="x",pady=(4,2))
        ctk.CTkLabel(fpr,text="Preset:",width=55,anchor="w").pack(side="left")
        self.field_preset_var=tk.StringVar()
        self.field_preset_cb=ctk.CTkComboBox(fpr,variable=self.field_preset_var,values=[""],width=150,
                                              command=self._on_field_preset_selected)
        self.field_preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(fpr,text="+",width=30,command=self._save_new_field_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(fpr,text="💾",width=30,command=self._update_field_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(fpr,text="🗑",width=30,command=self._delete_field_preset).pack(side="left")

        fs=ctk.CTkFrame(fd,fg_color="transparent"); fs.pack(fill="x",pady=(4,0))
        self.fv={}; self.hint_labels={}; self.field_labels={}
        form_rows=[
            ("Name",               "Name",                  "Field name — used as folder/file name", False),
            ("company",            "Company",                "Type any name. New companies are created automatically on save.", False),
            ("PP_Latitude",        "Pivot Latitude",         "Decimal degrees — or click 📍 on map",  False),
            ("PP_Longitude",       "Pivot Longitude",        "Decimal degrees",                        False),
            ("lld",                "Legal Land Description", "Auto-filled to NE/NW/SE/SW when pivot is placed. Editable — type a section (32-14-22-W4), half (N-32-14-22-W4), or quarter (NE-32-14-22-W4).", False),
            ("Spray_angle",        "Spray Angle (°)",        "0=N↑  90=E→  180=S↓  270=W←",           False),
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
        # Track exclusion lives in the Pivot menu now, but keep its backing var
        # (used by _redraw_tracks / get_tent_positions) and its write-trace.
        self.fv["track_exclusion_ft"]=tk.StringVar(value="10")
        # Sprayer-pass edge buffer (Sprayer menu). How far in from the edge of
        # any sprayer pass shelters can sit; the middle of each pass becomes
        # a kill zone of width max(0, sprayer_width − 2 × buffer). Defaults
        # to 0 (no kill zone) — opt in via Sprayer → Set Edge Buffer (ft).
        self.fv["pass_edge_buffer_ft"]=tk.StringVar(value="0")
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

        # Outside Sprayer Pass
        ctk.CTkLabel(fs,text="Outside Sprayer Pass",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        ctk.CTkLabel(fs,text="If yes, shelters are excluded from one pass-width inside the boundary",
                     anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10)).pack(fill="x")
        self.outside_pass_var=tk.StringVar(value="No")
        ctk.CTkSegmentedButton(fs,values=["Yes","No"],variable=self.outside_pass_var,
                               command=lambda v: self._on_outside_pass_toggle()
                               ).pack(fill="x",pady=(2,8))

        self.fv["Spray_angle"].set("0"); self.fv["Sprayer_width"].set("133")

        # Bay calculator (collapsible)
        bc=self._collapsible(right,"Bay Calculator",expanded=False)

        # Preset row
        preset_row=ctk.CTkFrame(bc,fg_color="transparent")
        preset_row.pack(fill="x",pady=(2,2))
        ctk.CTkLabel(preset_row,text="Preset:",width=55,anchor="w").pack(side="left")
        self.preset_var=tk.StringVar()
        self.preset_cb=ctk.CTkComboBox(preset_row,variable=self.preset_var,values=[""],width=160,
                                        command=self._on_preset_selected)
        self.preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(preset_row,text="+",width=30,command=self._save_new_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(preset_row,text="💾",width=30,command=self._update_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(preset_row,text="🗑",width=30,command=self._delete_preset).pack(side="left")

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
        self.use_imported_passes_var=tk.BooleanVar(value=True)
        ctk.CTkLabel(bc,text="Planter pass source",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x",pady=(8,0))
        ctk.CTkCheckBox(bc,text="Use uploaded planter data (if any)",
                        variable=self.use_imported_passes_var,
                        command=self._on_form_change).pack(anchor="w",pady=(2,4))
        # No "Recalculate Bays" button — the bay widths and map redraw
        # automatically whenever any bay-calculator field changes.

        # ── Bee Allocation (collapsible) ──────────────────────────────────
        ba=self._collapsible(right,"Bee Allocation",expanded=False)

        bp_row=ctk.CTkFrame(ba,fg_color="transparent")
        bp_row.pack(fill="x",pady=(2,2))
        ctk.CTkLabel(bp_row,text="Preset:",width=55,anchor="w").pack(side="left")
        self.bee_preset_var=tk.StringVar()
        self.bee_preset_cb=ctk.CTkComboBox(bp_row,variable=self.bee_preset_var,values=[""],width=160,
                                            command=self._on_bee_preset_selected)
        self.bee_preset_cb.pack(side="left",padx=(2,2))
        ctk.CTkButton(bp_row,text="+",width=30,command=self._save_new_bee_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(bp_row,text="💾",width=30,command=self._update_bee_preset).pack(side="left",padx=(0,2))
        ctk.CTkButton(bp_row,text="🗑",width=30,command=self._delete_bee_preset).pack(side="left")

        ctk.CTkFrame(ba,height=1,fg_color=UI_BORDER).pack(fill="x",pady=(2,4))

        # Shelter count drives the bee math, so the mode + value live here at
        # the top of Bee Allocation (was previously under Field Details).
        ctk.CTkLabel(ba,text="Shelters",anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        ctk.CTkComboBox(ba,variable=self.shelter_mode_var,values=list(self._shelter_mode_labels.keys()),
                        command=self._on_shelter_mode_change).pack(fill="x",pady=(0,2))
        self._shelter_entry=ctk.CTkEntry(ba,textvariable=self.shelter_value_var)
        self._shelter_entry.pack(fill="x",pady=(0,2))
        self.shelter_hint_lbl=ctk.CTkLabel(ba,text="",anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10))
        self.shelter_hint_lbl.pack(fill="x",pady=(0,8))

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
        for k in ("row_spacing_in","total_rows","num_female_rows","num_male_rows"):
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
        # Bee summary recomputes immediately so the user sees the math update.
        try: self._refresh_bee_summary()
        except Exception: pass
        if not self.show_shelters.get(): return
        if self._shelter_refresh_id:
            self.after_cancel(self._shelter_refresh_id)
        self._shelter_refresh_id = self.after(600, self._redraw_shelters)

    def _on_outside_pass_toggle(self):
        """Outside-sprayer-pass toggle changed. Beyond the usual form-change
        side effects (shelter recompute), redraw the sprayer passes so the
        red outer-pass inset line appears/disappears with the toggle."""
        self._on_form_change()
        if self.show_passes.get():
            self._redraw_passes()

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
        names=[""]+[p["name"] for p in presets]
        self.preset_cb.configure(values=names)

    def _on_preset_selected(self, name):
        if not name: return
        presets=self._load_bay_presets()
        for p in presets:
            if p["name"]==name:
                for k in ("row_spacing_in","total_rows","num_female_rows","num_male_rows"):
                    if k in p and k in self.fv: self.fv[k].set(str(p[k]))
                # Row layout & custom mask are new — older presets that lack
                # them default to "centered" (the historical implicit shape).
                rl = p.get("row_layout","centered")
                self.row_layout_var.set(self._row_layout_inverse.get(rl,"Centered male"))
                self.custom_mask_var.set(str(p.get("custom_row_mask","")))
                self._on_row_layout_change()
                break

    def _bay_preset_entry(self, name):
        """Build the dict written to bay_presets.json for the current bay-calc
        state. One spot so save-new and update stay in sync."""
        return {"name":name,
                "row_spacing_in":self.fv["row_spacing_in"].get(),
                "total_rows":self.fv["total_rows"].get(),
                "num_female_rows":self.fv["num_female_rows"].get(),
                "num_male_rows":self.fv["num_male_rows"].get(),
                "row_layout":self._row_layout_labels.get(self.row_layout_var.get(),"centered"),
                "custom_row_mask":self.custom_mask_var.get()}

    def _save_new_preset(self):
        name=self._ask_string("Save Preset","Preset name:")
        if not name: return
        presets=self._load_bay_presets()
        entry=self._bay_preset_entry(name)
        presets=[p for p in presets if p["name"]!=name]
        presets.append(entry)
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set(name)

    def _update_preset(self):
        name=self.preset_var.get()
        if not name:
            self._status("Select a bay preset to update (or use + to save a new one)."); return
        entry=self._bay_preset_entry(name)
        presets=[p for p in self._load_bay_presets() if p["name"]!=name]
        presets.append(entry)
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set(name)
        self._status(f"Updated bay preset: {name}")

    def _delete_preset(self):
        name=self.preset_var.get()
        if not name: return
        presets=[p for p in self._load_bay_presets() if p["name"]!=name]
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set("")

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
        names=[""]+[p["name"] for p in self._load_bee_presets()]
        self.bee_preset_cb.configure(values=names)

    def _on_bee_preset_selected(self, name):
        if not name: return
        for p in self._load_bee_presets():
            if p["name"]==name:
                for k in ("gals_per_acre","gals_per_tray"):
                    if k in p and k in self.fv: self.fv[k].set(str(p[k]))
                break

    def _save_new_bee_preset(self):
        name=self._ask_string("Save Bee Preset","Preset name:")
        if not name: return
        presets=self._load_bee_presets()
        entry={"name":name,
               "gals_per_acre":self.fv["gals_per_acre"].get(),
               "gals_per_tray":self.fv["gals_per_tray"].get()}
        presets=[p for p in presets if p["name"]!=name]
        presets.append(entry)
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set(name)

    def _update_bee_preset(self):
        name=self.bee_preset_var.get()
        if not name:
            self._status("Select a bee preset to update (or use + to save a new one)."); return
        entry={"name":name,
               "gals_per_acre":self.fv["gals_per_acre"].get(),
               "gals_per_tray":self.fv["gals_per_tray"].get()}
        presets=[p for p in self._load_bee_presets() if p["name"]!=name]
        presets.append(entry)
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set(name)
        self._status(f"Updated bee preset: {name}")

    def _delete_bee_preset(self):
        name=self.bee_preset_var.get()
        if not name: return
        presets=[p for p in self._load_bee_presets() if p["name"]!=name]
        self._save_bee_presets(presets)
        self._refresh_bee_preset_list()
        self.bee_preset_var.set("")

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
        names=[""]+[p["name"] for p in self._load_field_presets()]
        self.field_preset_cb.configure(values=names)

    def _save_new_field_preset(self):
        name=self._ask_string("Save Field Preset",
            "Preset name (saves field name, pivot, tracks, acres, boundary, corners):")
        if not name: return
        f=self._field_from_form()
        bp=f.get("boundary_polygon")
        entry={"name":name,
               "pivot_tracks":list(f.get("pivot_tracks") or []),
               "boundary_polygon":[list(pt) for pt in bp] if bp else None,
               "corner_arms":f.get("corner_arms") or []}
        for k in self._FIELD_PRESET_SCALARS:
            entry[k]=f.get(k,"")
        presets=[p for p in self._load_field_presets() if p["name"]!=name]
        presets.append(entry)
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set(name)
        self._status(f"Saved field preset: {name}")

    def _on_field_preset_selected(self, name):
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
        self._status(f"Applied field preset: {name} — set name, angle & bees for this year.")

    def _update_field_preset(self):
        name=self.field_preset_var.get()
        if not name:
            self._status("Select a field preset to update (or use + to save a new one)."); return
        f=self._field_from_form()
        bp=f.get("boundary_polygon")
        entry={"name":name,
               "pivot_tracks":list(f.get("pivot_tracks") or []),
               "boundary_polygon":[list(pt) for pt in bp] if bp else None,
               "corner_arms":f.get("corner_arms") or []}
        for k in self._FIELD_PRESET_SCALARS:
            entry[k]=f.get(k,"")
        presets=[p for p in self._load_field_presets() if p["name"]!=name]
        presets.append(entry)
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set(name)
        self._status(f"Updated field preset: {name}")

    def _delete_field_preset(self):
        name=self.field_preset_var.get()
        if not name: return
        presets=[p for p in self._load_field_presets() if p["name"]!=name]
        self._save_field_presets(presets)
        self._refresh_field_preset_list()
        self.field_preset_var.set("")

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

    def _on_shelter_mode_change(self, _=None):
        mode=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
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

        # Per-shelter + warning lines need the actual shelter count.
        if n <= 0 or total_trays is None:
            self.bee_per_shelter_lbl.configure(text="Per shelter:  —")
            self.bee_short_lbl.configure(text="")
            return

        total_trays_d, per, short, _tg = self._compute_bee_distribution(n)
        if per:
            lo, hi = min(per), max(per)
            ps_txt = f"{lo} trays" if lo == hi else f"{lo}–{hi} trays"
        else:
            ps_txt = "—"
        self.bee_per_shelter_lbl.configure(text=f"Per shelter:  {ps_txt}")
        if short > 0:
            self.bee_short_lbl.configure(text=f"⚠ {n} shelters but bee math gives only {n-short} trays — bumped up to 1 each")
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
    def _is_all_scope(self):
        return self.company_var.get()==ALL_COMPANIES or self.year_var.get()==ALL_YEARS

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

    def _on_field_select(self,_=None):
        sel=self.field_tree.selection()
        if not sel: return
        row=self._field_rows.get(sel[0])
        if not row: return
        co,yr,name=row
        f=load_field(co,yr,name)
        if not f: return
        self.current_field=f
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
        self._form_from_field()
        self._redraw_all()
        self._zoom_to_field()

    def _zoom_to_field(self):
        """Zoom the map so the field's outer boundary just fits in the frame.
        Falls back to the pivot point when no boundary is set."""
        try:
            bp = self.current_field.get("boundary_polygon") or []
            if bp and len(bp) >= 3:
                lats=[p[0] for p in bp]; lons=[p[1] for p in bp]
                cy=(max(lats)+min(lats))/2.0; cx=(max(lons)+min(lons))/2.0
                self.map_widget.update_idletasks()
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

    # ── Form helpers ───────────────────────────────────────────────────────────
    def _form_from_field(self):
        f=self.current_field
        bf=blank_field()
        self._shelter_undo=[]   # undo history is per-field, reset on load/new
        for k,v in self.fv.items():
            val=f.get(k)
            v.set(str(bf.get(k,"")) if val is None else str(val))
        self.outside_pass_var.set(f.get("outside_sprayer_pass","No"))
        # Row layout: dropdown + custom mask + use-imported-passes toggle.
        rl = f.get("row_layout","centered")
        self.row_layout_var.set(self._row_layout_inverse.get(rl,"Centered male"))
        self.custom_mask_var.set(str(f.get("custom_row_mask","")))
        self.use_imported_passes_var.set(bool(f.get("use_imported_passes",True)))
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
        f["outside_sprayer_pass"]=self.outside_pass_var.get()
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

    def _on_track_select(self,_=None): pass

    # ── Map ────────────────────────────────────────────────────────────────────
    def _search_lld(self):
        res=geocode_lld(self.lld_entry.get())
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

    def _delete_pivot(self):
        self._close_all_popups()
        if self.pivot_marker:
            self.pivot_marker.delete(); self.pivot_marker=None
        self._unregister_drag_prefix("pivot")
        self.fv["PP_Latitude"].set(""); self.fv["PP_Longitude"].set("")
        self._status("Pivot deleted.")

    def _mode_boundary(self):
        self._close_all_popups()
        self.click_mode="boundary"; self.boundary_pts=[]; self._clear_boundary_overlays()
        self._show_context_btn("✔ Save Boundary", self._close_boundary)
        self._status("Click map to add boundary vertices. ✔ Save when done.")

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
        self._status("Click a vertex to select it (drag to move, 🗑 Delete to remove). Esc to deselect. ✔ Save when finished.")

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
        self._status("Click a vertex to select it (drag to move, 🗑 Delete to remove). ✔ Save when finished.")

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
            outer = polys[0]
            inners = polys[1:]
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

    def _parse_kml_coords(self,path):
        with open(path,encoding="utf-8") as fh: text=fh.read()
        return self._parse_kml_coords_text(text)

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
        self.click_mode="track"; self._status("Click on map to set track radius from pivot…")

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
            # Append a manual shelter pin and redraw. Stays in this mode
            # until the user clicks ✔ Done so multiple pins can be placed
            # in one session.
            pins = self.current_field.setdefault("manual_shelter_pins", [])
            pins.append([lat, lon])
            # If the user is in "Manual pins only" mode, the engine returns
            # these pins as the shelter set; otherwise the pin is stored
            # but only takes effect after the user switches to manual mode.
            self.show_shelters.set(True)
            self._redraw_shelters()
            self._status(f"Added shelter pin #{len(pins)} — keep clicking, ✔ Done when finished.")

        elif mode=="inner_boundary":
            if not hasattr(self, "inner_pts"): self.inner_pts = []
            self.inner_pts.append((lat,lon))
            # Distinct orange-red marker so it doesn't get confused with the
            # yellow outer-boundary in-progress marker.
            m = self.map_widget.set_marker(lat, lon, text=str(len(self.inner_pts)),
                                            marker_color_circle="#FF6600",
                                            marker_color_outside="#993300")
            self.boundary_markers.append(m)

        elif mode=="boundary_edit":
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
            self.click_mode=None; self._status(f"Track added: {r_m:.1f} m ({r_m/0.3048:.1f} ft)")
            # Auto-enable the tracks layer so the newly-added circle is visible
            # without the user having to remember to toggle it on. (Field-select
            # turns this off by default.)
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
    def _add_track_manual(self):
        use_m=self.unit_var.get()=="Metres"
        unit_label="metres" if use_m else "feet"
        val=tkinter.simpledialog.askfloat("Pivot Track",f"Radius from pivot ({unit_label}):")
        if val is None: return
        r_m=val if use_m else val*0.3048
        self.current_field.setdefault("pivot_tracks",[]).append(round(r_m,2))
        self.show_tracks.set(True)   # ensure the newly-added circle is visible
        self._refresh_track_list(); self._redraw_tracks()

    def _remove_track(self):
        sel=self.track_lb.curselection()
        if not sel: return
        del self.current_field["pivot_tracks"][sel[0]]
        self._refresh_track_list(); self._redraw_tracks()
        if self.show_shelters.get(): self._redraw_shelters()

    def _toggle_pivot(self):
        """Toggle the pivot point marker, pivot tracks, AND corner tracks
        together — they're all part of the same conceptual layer (pivot +
        anything anchored relative to it / around its kill zone)."""
        self._close_all_popups()
        on = not self.show_pivot.get()
        self.show_pivot.set(on)
        self.show_tracks.set(on)
        self.show_corner_arms.set(on)
        self._redraw_pivot()
        self._redraw_tracks()
        self._redraw_corner_arms()
        self._status("Pivot " + ("shown." if on else "hidden."))

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

    def _mode_edit_track(self):
        self._close_all_popups()
        tracks=self.current_field.get("pivot_tracks") or []
        if not tracks:
            self._status("No pivot tracks — use Draw Circle to add one."); return
        self._status("Click and drag the ↔ handle to resize a track.")

    def _mode_edit_track_measurements(self):
        """Dialog to type the length of each pivot SPAN (the segment from the
        previous tower, or the pivot for span 1, out to that tower). Internally
        the tracks are still stored as cumulative distance-from-pivot in metres;
        spans are just a convenient input that maps how pivots are actually
        measured (e.g. eight 179 ft spans with a short 66 ft final span). Lets
        the rings be corrected against real measurements when the satellite
        imagery is slightly off."""
        self._close_all_popups()
        use_m=self.unit_var.get()=="Metres"
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

    def _mode_delete_track_ui(self):
        self._close_all_popups()
        tracks=self.current_field.get("pivot_tracks") or []
        if not tracks:
            self._status("No pivot tracks to delete."); return
        win=ctk.CTkToplevel(self)
        win.title("Delete Pivot Track")
        win.geometry("320x220")
        win.grab_set()
        ctk.CTkLabel(win,text="Select track to delete:").pack(pady=(12,4))
        lb=tk.Listbox(win,bg=UI_CARD,fg=UI_TEXT,selectbackground=UI_SELECT,selectforeground=UI_TEXT,
                      relief="flat",font=(FONT_BODY,11),height=5,
                      activestyle="none",highlightthickness=1,highlightbackground=UI_BORDER)
        for i,r in enumerate(tracks):
            lb.insert(tk.END,f"Track {i+1}: {r:.1f} m  ({r/0.3048:.1f} ft)")
        lb.pack(fill="x",padx=10,pady=4)
        def do_delete():
            sel=lb.curselection()
            if not sel: return
            del self.current_field["pivot_tracks"][sel[0]]
            self._refresh_track_list(); self._redraw_tracks()
            if self.show_shelters.get(): self._redraw_shelters()
            win.destroy(); self._status("Track deleted.")
        ctk.CTkButton(win,text="Delete Selected",fg_color="#6b1a1a",command=do_delete).pack(pady=(4,2))
        ctk.CTkButton(win,text="Cancel",command=win.destroy).pack()

    def _make_resize_cb(self,idx):
        pass  # replaced by drag system

    def _on_track_resize_motion(self,event):
        pass  # replaced by drag system

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
        for i,r_m in enumerate(self.current_field.get("pivot_tracks") or []):
            for r,col,w in [(r_m+excl_m,"#32CD32",2),(max(1,r_m-excl_m),"#32CD32",1)]:
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

    # ── Corner zones (paths and circles — unlimited) ──────────────────────────
    def _mode_add_corner_path(self):
        self._close_all_popups()
        self._cancel_corner_arm_drawing()
        self.corner_arm_pts=[]
        self.click_mode="corner_arm_path"
        self._show_context_btn("✔ Done Path", self._finish_corner_path)
        self._status("Corner path — click map to place points, ✔ Done when finished")

    def _mode_add_corner_circle(self):
        self._close_all_popups()
        self._cancel_corner_arm_drawing()
        self.corner_arm_circle_center=None
        self.click_mode="corner_arm_circle_center"
        self._show_context_btn("✖ Cancel", self._cancel_corner_arm_drawing)
        self._status("Corner circle — click map to place center")

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

    def _toggle_corner_arms(self):
        """Show/hide corner tracks. Mirrors _toggle_tracks / _toggle_passes."""
        self._close_all_popups()
        self.show_corner_arms.set(not self.show_corner_arms.get())
        self._redraw_corner_arms()
        # Shelters depend on corner-arm exclusion zones; refresh them too.
        if self.show_shelters.get(): self._redraw_shelters()
        self._status("Corner tracks " +
                     ("shown." if self.show_corner_arms.get() else "hidden."))

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
        # Match pivot tracks: lime-green ±excl_m offset lines, no centerline.
        # Color is the same as pivot tracks so they read as "the same kind of
        # zone" — the only difference is that corner tracks are anchored to
        # absolute lat/lon and don't move when the pivot does.
        col = "#32CD32"
        try:
            excl_m = float(self.fv.get("track_exclusion_ft", self.excl_var).get() or "10") * 0.3048
        except (ValueError, AttributeError):
            excl_m = 10 * 0.3048
        for arm in arms:
            try:
                if arm.get("type") == "circle":
                    # Legacy data: render as a single outline polygon. New
                    # corner tracks are paths only (Add Circle removed).
                    o = self.map_widget.set_polygon(
                        circle_pts(arm["lat"], arm["lon"], arm["radius_m"]),
                        fill_color=None, outline_color=col, border_width=2)
                    self.corner_arm_overlays.append(o)
                else:
                    pts = arm.get("pts") or []
                    if len(pts) < 2: continue
                    pts_ll = [(p[0], p[1]) for p in pts]
                    left, right = self._offset_path_latlon(pts_ll, excl_m)
                    if len(left) >= 2:
                        self.corner_arm_overlays.append(
                            self.map_widget.set_path(left, color=col, width=2))
                    if len(right) >= 2:
                        self.corner_arm_overlays.append(
                            self.map_widget.set_path(right, color=col, width=1))
            except Exception:
                pass

    # ── Sprayer passes extras ──────────────────────────────────────────────────
    def _mode_edit_passes(self):
        self._close_all_popups()
        self._status("Sprayer pass editing: adjust Spray Angle or Sprayer Width in Field Details, then Toggle on/off to refresh.")

    def _import_jd_passes(self):
        self._close_all_popups()
        path=tkinter.filedialog.askopenfilename(
            title="Open John Deere Operation GeoJSON",
            filetypes=[("GeoJSON","*.geojson *.json"),("All","*.*")])
        if not path: return
        try:
            with open(path,encoding="utf-8") as fh:
                gj=json.load(fh)
            self._clear_passes()
            count=0
            features=gj.get("features",[]) if isinstance(gj,dict) else []
            for feat in features:
                geom=feat.get("geometry") or {}
                gtype=geom.get("type","")
                coords=geom.get("coordinates",[])
                lines=[]
                if gtype=="LineString": lines=[coords]
                elif gtype=="MultiLineString": lines=coords
                for line in lines:
                    pts=[(c[1],c[0]) for c in line if len(c)>=2]
                    if len(pts)>=2:
                        try:
                            p=self.map_widget.set_path(pts,color="#FF3333",width=1)
                            self.pass_paths.append(p); count+=1
                        except Exception: pass
            self.show_passes.set(True)
            self._status(f"Loaded {count} pass lines from {Path(path).name}")
        except Exception as ex:
            tkinter.messagebox.showerror("Import Error",str(ex))

    # ── Planter passes extras ──────────────────────────────────────────────────
    def _mode_edit_bays(self):
        self._close_all_popups()
        self._status("Bay editing: adjust Row Spacing / Female Rows / Male Rows in Bay Calculator, then recalculate.")

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
        self.show_planter_passes.set(True)
        self._redraw_planter_passes()
        n_pts = sum(len(p) for p in passes)
        self._status(f"Imported {len(passes)} passes ({n_pts:,} samples) "
                     f"from {Path(path).name}.")

    def _clear_planter_passes(self):
        """Remove the imported planter data from this field."""
        self._close_all_popups()
        self.current_field["planter_passes"] = None
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
        """Click-to-add-pins mode. Each click drops a manual shelter pin at
        the click point; pins survive until the user clicks ✔ Done or picks
        a different click mode. Manual pins are stored on the field as
        manual_shelter_pins; they take effect immediately when the shelter
        mode is "Manual pins only", and they're preserved (but inactive)
        when the user switches to a different shelter mode."""
        self._close_all_popups()
        self.click_mode = "add_shelter"
        self.show_shelters.set(True)
        self._show_context_btn("✔ Done Adding Pins", self._close_add_shelter)
        n = len(self.current_field.get("manual_shelter_pins") or [])
        if n:
            self._status(f"Click map to add shelter pins ({n} already placed). "
                         "Drag a pin to move it, click a pin to delete it. ✔ Done when finished.")
        else:
            self._status("Click map to add shelter pins. Drag a pin to move it, "
                         "click a pin to delete it. ✔ Done when finished.")

    def _close_add_shelter(self):
        self.click_mode = None
        self._hide_context_btn()
        n = len(self.current_field.get("manual_shelter_pins") or [])
        mode = self._shelter_mode_labels.get(self.shelter_mode_var.get(), "total")
        if n and mode != "manual":
            self._status(f"{n} manual pins saved. Switch shelter mode to "
                         "\"Manual pins only\" to use them.")
        else:
            self._status(f"{n} manual pins saved.")

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
        for m in self.shelter_markers:
            try: m.delete()
            except Exception: pass
        self.shelter_markers=[]
        for p in self.shelter_circle_polys:
            try: p.delete()
            except Exception: pass
        self.shelter_circle_polys=[]

    def _toggle_shelter_buffers(self):
        self.shelter_circle_var.set(not self.shelter_circle_var.get())
        if self.show_shelters.get(): self._redraw_shelters()
        self._status("Buffer zone " + ("shown." if self.shelter_circle_var.get() else "hidden."))

    def _edit_shelter_buffer(self):
        self._close_all_popups()
        use_m=self.unit_var.get()=="Metres"
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
        self._shelter_undo.append((key,prev))

    def _undo_shelter_move(self):
        """Revert just the most recent move/delete; repeat to step further back."""
        if not self._shelter_undo:
            self._status("Nothing to undo."); return
        key,prev=self._shelter_undo.pop()
        overrides=self.current_field.setdefault("shelter_overrides",{})
        if prev is _UNDO_MISSING:
            overrides.pop(key,None)
        else:
            overrides[key]=prev
        if self.show_shelters.get(): self._redraw_shelters()
        n=len(self._shelter_undo)
        self._status("Reverted last change." + (f" {n} earlier change(s) remain." if n else " No more to undo."))

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

    def _delete_shelter(self,idx):
        self._record_shelter_change(idx)
        overrides=self.current_field.setdefault("shelter_overrides",{})
        overrides[str(idx)]=None
        self._redraw_shelters()
        self._status(f"Shelter #{idx+1} deleted — ↶ Reset Move to undo.")

    def _make_shelter_move_cb(self,idx):
        def cb(marker):
            self.moving_shelter_idx=idx
            self.click_mode=("move_shelter",idx)
            self._status(f"Click new location for shelter #{idx+1} (or click elsewhere to cancel)")
        return cb

    def _on_shelter_drag(self,idx,lat,lon):
        self._record_shelter_change(idx)
        overrides=self.current_field.setdefault("shelter_overrides",{})
        overrides[str(idx)]=[lat,lon]
        self._status(f"Shelter #{idx+1} moved — ↶ Reset Move to undo.")
        self._redraw_shelters()

    def _redraw_shelters(self):
        self._clear_shelters()
        if not self.show_shelters.get(): return
        f=self._field_from_form()
        use_m=self.unit_var.get()=="Metres"
        positions, row_idxs = maketentgrid.get_tent_positions(f,use_metric=use_m,return_rows=True)
        if not positions:
            self._status("⚠ No shelter positions — check field details and boundary.")
            self.shelter_positions=[]; self.shelter_tray_counts=[]
            self._refresh_bee_summary()
            return
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
        self.shelter_positions=list(merged)
        # Compute tray distribution across only the *visible* shelters (skipping deleted)
        kept_indices=[i for i in range(len(merged)) if i not in deleted]
        kept_rows = [row_idxs[i] for i in kept_indices] if row_idxs else None
        kept_positions = [merged[i] for i in kept_indices]
        n_visible=len(kept_indices)
        total_trays, per, short, _ = self._compute_bee_distribution(
            n_visible, kept_rows, shelter_positions_latlon=kept_positions)
        tray_count_at={}
        if per:
            for k_pos, tc in zip(kept_indices, per):
                tray_count_at[k_pos] = tc
        self.shelter_tray_counts=[tray_count_at.get(i,0) for i in range(len(merged))]
        mode=self.pin_label_mode
        try: BUFFER_M=float(self.current_field.get("shelter_buffer_m") or 0)
        except (ValueError,TypeError): BUFFER_M=0.0
        show_circles=self.shelter_circle_var.get() and BUFFER_M>0   # 0 size = no buffer
        shelter_num=0   # sequential 1..N among VISIBLE shelters (matches export numbering)
        for i,(lat,lon) in enumerate(merged):
            if i in deleted: continue
            shelter_num+=1
            cc="#FFD700"; oc="#B8860B"
            if mode=="shelters":
                lbl=str(shelter_num)
            elif mode=="trays" and per:
                lbl=str(tray_count_at[i])
            else:
                lbl=""
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
                self._register_drag(f"shelter_{i}",lat,lon,lbl,cc,oc,
                                    lambda la,lo,i=i: self._on_shelter_drag(i,la,lo),marker=m)
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
            angle=float(self.fv["Spray_angle"].get() or 0)
            width_ft=float(self.fv["Sprayer_width"].get() or 133)
            width_m=width_ft*0.3048
            bp=self.current_field.get("boundary_polygon")
        except (ValueError,TypeError): return
        if not bp or len(bp)<3: return

        poly_enu=[(latlon_to_enu(lat,lon,plat,plon)) for lat,lon in bp]
        max_r=max(math.sqrt(e*e+n*n) for e,n in poly_enu)*1.1

        if not self.show_passes.get(): return

        # Outer sprayer limit (one sprayer-width inset from boundary) — only
        # drawn when the user is actually running an outside pass; with the
        # toggle off there is no outside pass, so no inner-limit line either.
        outside_pass_on = (self.outside_pass_var.get() or "No").strip().lower() == "yes"
        if outside_pass_on:
            inset=inset_polygon_enu(poly_enu,width_m)
            if len(inset)>=3:
                lpts=[enu_to_latlon(e,n,plat,plon) for e,n in inset]
                try:
                    self.outer_sprayer_poly=self.map_widget.set_polygon(
                        lpts,fill_color=None,outline_color="#FF3333",border_width=1)
                except Exception: pass

        rot=math.radians((0-angle+180)%360-180)
        cos_r,sin_r=math.cos(rot),math.sin(rot)
        tdx=-sin_r; tdy=cos_r

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

        max_rows=int(max_r/width_m)+2
        for r in range(-max_rows,max_rows+1):
            lat_e=r*width_m; lat_n=0
            pe=lat_e*cos_r-lat_n*sin_r; pn=lat_n*cos_r+lat_e*sin_r

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
                        [(lat1, lon1), (lat2, lon2)], color="#FF3333", width=1)
                    self.pass_paths.append(path)
                except Exception:
                    pass

    # ── Unit label refresh ─────────────────────────────────────────────────────
    def _on_unit_change(self,val=None):
        self._refresh_unit_labels()

    def _refresh_unit_labels(self):
        u=self.unit_var.get(); m=u=="Metres"; abb="m" if m else "ft"
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
        use_m=self.unit_var.get()=="Metres"
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
        unit = max(1, nf + nm)
        repeats = total_rows // unit if unit > 0 else 0
        leftover = total_rows - repeats * unit
        f_in=(nf+1)*rs; m_in=(nm+1)*rs
        f_ft=f_in/12; m_ft=m_in/12; f_m=f_ft*0.3048; m_m=m_ft*0.3048
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
        else:
            self.female_bay_lbl.configure(text=f'Female bay: {f_in:.1f}" = {f_ft:.3f} ft')
            self.male_bay_lbl.configure(text=f'Male bay:   {m_in:.1f}" = {m_ft:.3f} ft')
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
                           inner_polys_enu=None):
        """Clip a band (between lateral positions x1 and x2, travelling
        along (tdx, tdy)) to the outer polygon AND subtract every inner
        polygon. Returns a LIST of band polygons (one per inside-interval).

        Uses clip_line_to_polygon_intervals on both band edges and walks
        through the merged t-interval set, so a non-convex outer polygon
        (e.g. a field that wraps around a farmstead) gets multiple bay
        slices instead of one bounding rectangle that fills across the
        gap. Each interval is also subtracted by every inner polygon.
        """
        p1e, p1n = x1 * ldx, x1 * ldy
        p2e, p2n = x2 * ldx, x2 * ldy
        # Inside-intervals for each band edge.
        edge_a = clip_line_to_polygon_intervals(p1e, p1n, tdx, tdy, poly_enu)
        edge_b = clip_line_to_polygon_intervals(p2e, p2n, tdx, tdy, poly_enu)
        if not edge_a and not edge_b: return []
        # If one edge is entirely outside, fall back to the other (so we
        # don't drop a band just because its outer edge skims past).
        if not edge_a: edge_a = edge_b
        if not edge_b: edge_b = edge_a
        # Pair up matching intervals between the two edges. They should
        # always have the same count for sane geometry; intersect the i-th
        # interval of each edge to get the band's i-th inside-segment.
        intervals = []
        for (a0, a1), (b0, b1) in zip(edge_a, edge_b):
            t0 = max(a0, b0); t1 = min(a1, b1)
            if t1 - t0 > 1e-6: intervals.append((t0, t1))
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
        """Translucent red bands showing the sprayer-pass kill zones — the
        middle of every main sprayer pass + the middle of the outside pass.
        Helps the user visually verify where shelters can / cannot land."""
        self._clear_pass_buffer_overlay()
        if not self.show_pass_buffer_overlay.get(): return
        try:
            plat = float(self.fv["PP_Latitude"].get())
            plon = float(self.fv["PP_Longitude"].get())
            angle = float(self.fv["Spray_angle"].get() or 0)
            width_ft = float(self.fv["Sprayer_width"].get() or 133)
            width_m = width_ft * 0.3048
            buffer_ft = float(self.fv["pass_edge_buffer_ft"].get() or 30)
            buffer_m = buffer_ft * 0.3048
            bp = self.current_field.get("boundary_polygon")
        except (ValueError, TypeError):
            return
        if not bp or len(bp) < 3 or width_m <= 0: return
        dead_half = max(0.0, width_m / 2.0 - buffer_m)
        if dead_half <= 0:   # buffer ≥ half-width → no kill zone at all
            self._status("Edge buffer ≥ half pass width — no kill zone to draw.")
            return
        poly_enu = [latlon_to_enu(lat, lon, plat, plon) for lat, lon in bp]
        max_r = max(math.sqrt(e*e + n*n) for e, n in poly_enu) * 1.1
        rot = math.radians((0 - angle + 180) % 360 - 180)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        tdx, tdy = -sin_r, cos_r
        ldx, ldy = cos_r, sin_r
        KILL_FILL = "#FF2233"   # translucent-looking red (no real alpha in tkintermapview)
        # Main-pass kill zones: a band of width 2 × dead_half centred on each
        # sprayer pass at r * sprayer_width.
        max_rows = int(max_r / width_m) + 2
        # Inner boundaries as ENU rings (so the kill-zone bands also stop at
        # interior cutouts when present).
        inner_polys_enu = []
        for inner in (self.current_field.get("boundary_inner") or []):
            if not inner or len(inner) < 3: continue
            inner_polys_enu.append(
                [latlon_to_enu(pt[0], pt[1], plat, plon) for pt in inner])
        for r in range(-max_rows, max_rows + 1):
            cx = r * width_m
            bands = self._band_polygon_enu(cx - dead_half, cx + dead_half,
                                            tdx, tdy, ldx, ldy, poly_enu,
                                            inner_polys_enu=inner_polys_enu)
            for band in bands:
                lpts = [enu_to_latlon(e, n, plat, plon) for e, n in band]
                try:
                    o = self.map_widget.set_polygon(
                        lpts, fill_color=KILL_FILL,
                        outline_color=KILL_FILL, border_width=0)
                    self.pass_buffer_overlays.append(o)
                except Exception:
                    pass
        # Outside-pass kill zone (only when running an outside pass) — drawn
        # as the area between the boundary inset by buffer_m (outer edge of
        # kill zone) and the boundary inset by (sprayer_width − buffer_m)
        # (inner edge of kill zone). Since tkintermapview doesn't do
        # polygons-with-holes, show it as two outline rings instead.
        outside_pass_on = (self.outside_pass_var.get() or "No").strip().lower() == "yes"
        if outside_pass_on:
            for inset_dist in (buffer_m, width_m - buffer_m):
                inset = inset_polygon_enu(poly_enu, inset_dist)
                if len(inset) >= 3:
                    lpts = [enu_to_latlon(e, n, plat, plon) for e, n in inset]
                    try:
                        o = self.map_widget.set_polygon(
                            lpts, fill_color=None,
                            outline_color=KILL_FILL, border_width=2)
                        self.pass_buffer_overlays.append(o)
                    except Exception:
                        pass

    def _redraw_bays(self):
        self._clear_bays()
        if not self.show_bays.get(): return
        # No bay structure in blanket-planted mode → nothing to draw.
        if not self.current_field.get("use_bays", True): return
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            angle=float(self.fv["Spray_angle"].get() or 0)
            rs=float(self.fv["row_spacing_in"].get() or 22)
            nf=int(self.fv["num_female_rows"].get() or 8)
            nm=int(self.fv["num_male_rows"].get() or 2)
            total_rows=int(self.fv["total_rows"].get() or (nf + nm))
            bp=self.current_field.get("boundary_polygon")
        except (ValueError,TypeError): return
        if not bp or len(bp)<3: return

        # When the user has imported planter passes AND the "use uploaded
        # planter data" toggle is on, derive the male-bay bands from the
        # actual pass polylines instead of the synthetic angle-grid below.
        # Each pass contributes one band per M block in the resolved row mask.
        planter_passes = self.current_field.get("planter_passes") or []
        if planter_passes and bool(self.current_field.get("use_imported_passes", True)):
            self._redraw_bays_from_passes(plat, plon, planter_passes,
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
        unit=female_m+male_m
        n_units=int(max_r/unit)+2
        for i in range(-n_units,n_units+1):
            cx=i*unit
            # Female bays hidden — only male bays shown
            bands = self._band_polygon_enu(
                cx + female_m/2, cx + female_m/2 + male_m,
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
        f=self.current_field
        try:
            plat=float(f.get("PP_Latitude",0) or 0); plon=float(f.get("PP_Longitude",0) or 0)
            if plat and plon:
                self.map_widget.set_position(plat,plon); self.map_widget.set_zoom(14)
        except (ValueError,TypeError): pass
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
        # Always let tkintermapview record the press so panning works correctly
        try: self.map_widget.mouse_click(event)
        except Exception: pass
        # Find nearest registered pin
        best_id=None; lat0=lon0=None
        try:
            lat0,lon0=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
            mpp=self._pixel_scale()
            threshold_m=max(50*mpp,30.0)
            best_dist=threshold_m
            for did,info in self._drag_registry.items():
                d=haversine_m(info['lat'],info['lon'],lat0,lon0)
                if d<best_dist:
                    best_dist=d; best_id=did
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
        # Pivot-track band drag — commit the new radius
        if self._drag_track_idx is not None:
            if self._drag_moved and self._drag_last_latlon:
                lat,lon=self._drag_last_latlon
                self._on_track_drag(self._drag_track_idx,lat,lon,final=True)
            self._drag_track_idx=None; self._drag_moved=False
            self._drag_start_xy=None; self._drag_last_latlon=None; self._pan_start_xy=None
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
            elif was_pin_drag and self._drag_item and self._drag_item.startswith("shelter_"):
                # No mode active and a shelter pin was tapped — offer delete
                try:
                    idx=int(self._drag_item.split("_")[1])
                    self._on_shelter_tap(idx)
                except (ValueError,IndexError): pass
            elif not was_pin_drag:
                # Plain map click (no pin nearby, no mode)
                try:
                    lat,lon=self.map_widget.convert_canvas_coords_to_decimal_coords(event.x,event.y)
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
        with the field's shelter_overrides (moved/deleted) applied."""
        positions=maketentgrid.get_tent_positions(f,use_metric=metric)
        overrides=f.get("shelter_overrides") or {}
        merged=list(positions); deleted=set()
        for k,v in overrides.items():
            try:
                idx=int(k)
                if 0<=idx<len(merged):
                    if v is None: deleted.add(idx)
                    else: merged[idx]=tuple(v)
            except (ValueError,TypeError): pass
        return [p for i,p in enumerate(merged) if i not in deleted]

    def _generate(self):
        scope=self._export_scope()
        if not scope:
            tkinter.messagebox.showwarning("No fields",
                "No saved fields match the current Company / Year selection."); return
        include_buffers=tkinter.messagebox.askyesno("Buffer zones",
            "Include each shelter's buffer zone as a passable interior boundary\n"
            "in the John Deere Operations Center file?\n\n"
            "(Uses each field's buffer size; fields with a 0 buffer add none.\n"
            "The field's outer boundary is never included.)")
        co=self.company_var.get(); yr=self.year_var.get()
        tag="%s_%s" % ("AllCompanies" if co==ALL_COMPANIES else co,
                       "AllYears" if yr==ALL_YEARS else yr)
        tag=re.sub(r"[^A-Za-z0-9_-]+","_",tag).strip("_") or "export"
        stamp=datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir=Path.home()/"Downloads"/("BeeTents_%s_%s" % (tag,stamp))
        metric=self.unit_var.get()=="Metres"
        self._log("Generating %d field(s) → %s" % (len(scope),out_dir)); self.update()
        def run():
            try:
                ok=0
                for c,y,name in scope:
                    f=load_field(c,y,name)
                    if not f: continue
                    try:
                        pivotpoint=(float(f["PP_Longitude"]),float(f["PP_Latitude"]))
                    except (KeyError,ValueError,TypeError):
                        self.after(0,lambda n=name:self._log("  skipped %s — no pivot point" % n)); continue
                    positions=self._final_shelter_positions(f,metric)
                    if not positions:
                        self.after(0,lambda n=name:self._log("  skipped %s — no shelters" % n)); continue
                    fname=str(f.get("Name") or name).strip()
                    try: buf_m=float(f.get("shelter_buffer_m") or 0)
                    except (ValueError,TypeError): buf_m=0.0
                    maketentgrid.export_field_outputs(positions,pivotpoint,str(out_dir),fname,
                                                      include_buffers=include_buffers,
                                                      buffer_radius_m=buf_m)
                    ok+=1
                    self.after(0,lambda n=fname,k=len(positions):self._log("  ✓ %s (%d shelters)" % (n,k)))
                self.after(0,lambda:self._log("Done. %d/%d fields exported." % (ok,len(scope))))
                self.after(0,lambda:tkinter.messagebox.showinfo("Done",
                    "%d field(s) written to:\n%s\n\n"
                    "For John Deere Operations Center:\n"
                    "  Files → Upload Files → Flags → drop\n"
                    "    {field}_Shelter_Pins.zip\n"
                    "  Files → Upload Files → Internal Boundaries → drop\n"
                    "    {field}_Shelter_Buffer_Zones.zip  (if buffers are enabled)\n"
                    "\n"
                    "Each zip has a README.txt with the same instructions.\n"
                    "\n"
                    "Trimble import: copy AgGPS/ folder to USB root.\n"
                    "Google Earth: open {field}_Shelter_Pins.kml."
                    % (ok,out_dir)))
                try: self.after(0,lambda:os.startfile(str(out_dir)))
                except Exception: pass
            except Exception:
                import traceback as tb; msg=tb.format_exc()
                self.after(0,lambda:self._log("ERROR:\n"+msg))
                self.after(0,lambda:tkinter.messagebox.showerror("Error",msg[:600]))
        threading.Thread(target=run,daemon=True).start()

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _status(self,msg): self.status_lbl.configure(text=msg)

    def _log(self,text):
        # No log panel anymore — surface progress on the status line (last line).
        last=str(text).strip().splitlines()[-1] if str(text).strip() else ""
        self._status(last)


if __name__=="__main__":
    app=BeetentApp(); app.mainloop()
