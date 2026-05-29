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
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maketentgrid
import utmish

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

def latlon_to_enu(lat,lon,pivot_lat,pivot_lon):
    pe,pn=utmish.from_lonlat(pivot_lon,pivot_lat,pivot_lon)
    e,n=utmish.from_lonlat(lon,lat,pivot_lon)
    return e-pe, n-pn

def enu_to_latlon(e,n,pivot_lat,pivot_lon):
    pe,pn=utmish.from_lonlat(pivot_lon,pivot_lat,pivot_lon)
    lon2,lat2=utmish.to_lonlat(pe+e,pn+n,pivot_lon)
    return lat2,lon2

def inset_polygon_enu(poly_enu, dist):
    """Offset every edge of poly_enu inward by dist metres. Works for convex polygons."""
    n=len(poly_enu)
    if n<3: return []
    cx=sum(e for e,_ in poly_enu)/n; cn=sum(nn for _,nn in poly_enu)/n
    edges=[]
    for i in range(n):
        e1,n1=poly_enu[i]; e2,n2=poly_enu[(i+1)%n]
        dx2,dy2=e2-e1,n2-n1; L=math.sqrt(dx2*dx2+dy2*dy2)
        if L<1e-9: continue
        nx,ny=-dy2/L,dx2/L
        me,mn=(e1+e2)/2,(n1+n2)/2
        if (cx-me)*nx+(cn-mn)*ny<0: nx,ny=-nx,-ny
        edges.append(((e1+dist*nx,n1+dist*ny),(e2+dist*nx,n2+dist*ny)))
    if len(edges)<3: return []
    result=[]
    for i in range(len(edges)):
        a=edges[i]; b=edges[(i+1)%len(edges)]
        ax,ay=a[1][0]-a[0][0],a[1][1]-a[0][1]
        bx,by=b[1][0]-b[0][0],b[1][1]-b[0][1]
        det=ax*(-by)-(-bx)*ay
        if abs(det)<1e-9:
            result.append(((a[1][0]+b[0][0])/2,(a[1][1]+b[0][1])/2))
        else:
            ddx=b[0][0]-a[0][0]; ddy=b[0][1]-a[0][1]
            t=(ddx*(-by)-ddy*(-bx))/det
            result.append((a[0][0]+t*ax,a[0][1]+t*ay))
    return result

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
                PP_Latitude="",PP_Longitude="",
                Spray_angle="0",Sprayer_width="133",
                shelter_mode="total",num_structures="",shelters_per_acre="",
                spacing="",shelter_spacing="",directional_offset="",
                row_spacing_in="22",num_female_rows="8",num_male_rows="2",planter_width_ft="",
                outside_sprayer_pass="No",track_exclusion_ft="10",
                shelter_buffer_m="1.524",
                boundary_edge_shelters=True,
                gals_per_acre="3",acres="",gals_per_tray="2",tray_distribution="even",
                boundary_polygon=None,pivot_tracks=[],corner_arms=[],
                shelter_overrides={})

def _field_dir(company,year):
    d=DATA_DIR/company/str(year); d.mkdir(parents=True,exist_ok=True); return d

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
        self.corner_arm_overlays    = []   # list of map path/polygon objects
        self.corner_arm_pts         = []   # points being drawn for in-progress path
        self.corner_arm_circle_center = None  # (lat,lon) for in-progress circle
        self.corner_arm_temp_markers = []
        self.show_passes      = tk.BooleanVar(value=False)
        self.show_bays        = tk.BooleanVar(value=False)
        self.show_pivot       = tk.BooleanVar(value=True)   # pivot marker + tracks together
        self.show_tracks      = tk.BooleanVar(value=True)
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
        self.after(1000, self._git_pull)  # pull latest on startup

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
        ctk.CTkButton(bar,text="Go",width=48,command=self._search_lld).pack(side="left",padx=(4,20),pady=8)
        self.status_lbl=ctk.CTkLabel(bar,text="",text_color=UI_MUTED,width=340,anchor="w")
        self.status_lbl.pack(side="left",padx=16)
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

        self._pivot_btn = self._make_menu_btn(bb, "📍 Pivot", [
            ("Toggle on/off",           self._toggle_pivot),
            ("Set Pivot Point",         self._mode_pivot),
            ("Draw Track Circle",       self._mode_track),
            ("Edit Track Measurements", self._mode_edit_track_measurements),
            ("Set Track Exclusion (ft)",self._edit_track_exclusion),
        ], color="#1a6b3a")
        self._pivot_btn.pack(side="left", padx=(0,4))

        self._bnd_btn = self._make_menu_btn(bb, "✏️ Boundary", [
            ("Draw",         self._mode_boundary),
            ("Edit",         self._mode_edit_boundary),
            ("Upload File",  self._upload_boundary),
            ("Delete",       self._clear_boundary),
        ], color="#5a3a8a")
        self._bnd_btn.pack(side="left", padx=(0,4))

        self._sp_btn = self._make_menu_btn(bb, "🌊 Sprayer", [
            ("Toggle on/off", self._toggle_passes),
            ("Edit",          self._mode_edit_passes),
            ("Add File",      self._import_jd_passes),
        ], color="#2a5a4a")
        self._sp_btn.pack(side="left", padx=(0,4))

        self._pl_btn = self._make_menu_btn(bb, "🌾 Planter", [
            ("Toggle on/off", self._toggle_bays),
            ("Edit",          self._mode_edit_bays),
        ], color="#3a5a1a")
        self._pl_btn.pack(side="left", padx=(0,4))

        self._shelter_btn = self._make_menu_btn(bb, "🏠 Shelters", [
            ("Toggle Pins",          self._toggle_shelters),
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
        # All right-side panels start collapsed; user opens what they need and
        # the choice persists for the rest of the session (no on-disk state).
        lf=self._collapsible(right,"Fields",expanded=False)
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
            ("PP_Latitude",        "Pivot Latitude",         "Decimal degrees — or click 📍 on map",  False),
            ("PP_Longitude",       "Pivot Longitude",        "Decimal degrees",                        False),
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
        for k in ("num_structures","spacing","shelters_per_acre"):
            self.fv[k]=tk.StringVar()
        # Track exclusion lives in the Pivot menu now, but keep its backing var
        # (used by _redraw_tracks / get_tent_positions) and its write-trace.
        self.fv["track_exclusion_ft"]=tk.StringVar(value="10")
        self._shelter_mode_labels={
            "Total shelters":           "total",
            "Shelters per acre":        "per_acre",
            "Spacing between shelters": "spacing",
        }
        self._shelter_mode_inverse={v:k for k,v in self._shelter_mode_labels.items()}
        self._shelter_mode_key={"total":"num_structures","per_acre":"shelters_per_acre","spacing":"spacing"}
        ctk.CTkLabel(fs,text="Shelters",anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        self.shelter_mode_var=tk.StringVar(value="Total shelters")
        ctk.CTkComboBox(fs,variable=self.shelter_mode_var,values=list(self._shelter_mode_labels.keys()),
                        command=self._on_shelter_mode_change).pack(fill="x",pady=(0,2))
        self.shelter_value_var=tk.StringVar()
        ctk.CTkEntry(fs,textvariable=self.shelter_value_var).pack(fill="x",pady=(0,2))
        self.shelter_hint_lbl=ctk.CTkLabel(fs,text="",anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10))
        self.shelter_hint_lbl.pack(fill="x",pady=(0,5))
        self.shelter_value_var.trace_add("write", self._on_shelter_value_change)

        # Outside Sprayer Pass
        ctk.CTkLabel(fs,text="Outside Sprayer Pass",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        ctk.CTkLabel(fs,text="If yes, shelters are excluded from one pass-width inside the boundary",
                     anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10)).pack(fill="x")
        self.outside_pass_var=tk.StringVar(value="No")
        ctk.CTkSegmentedButton(fs,values=["Yes","No"],variable=self.outside_pass_var,
                               command=lambda v: self._on_form_change()).pack(fill="x",pady=(2,8))

        # Boundary-edge shelters — on the SHORTER stagger class, add one shelter
        # past each end of the row just outside the field boundary, so the
        # perimeter reads as a clean wrap instead of zig-zagging where alternating
        # rows end at slightly different N-S coordinates. Counts toward the
        # requested total.
        ctk.CTkLabel(fs,text="Boundary edge shelters",anchor="w",
                     font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
        ctk.CTkLabel(fs,text="Adds a wing shelter past each end of every other row",
                     anchor="w",text_color=UI_MUTED,font=ctk.CTkFont(size=10)).pack(fill="x")
        self.boundary_edge_var=tk.BooleanVar(value=True)
        ctk.CTkCheckBox(fs,text="Enable wing shelters",variable=self.boundary_edge_var,
                        command=self._on_form_change).pack(anchor="w",pady=(2,8))

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

        bay_rows=[
            ("row_spacing_in",   "Row Spacing (inches)"),
            ("planter_width_ft", "Planter Width (ft)"),
            ("num_female_rows",  "Female Rows"),
            ("num_male_rows",    "Male Rows"),
        ]
        for key,label in bay_rows:
            ctk.CTkLabel(bc,text=label,anchor="w",font=ctk.CTkFont(family=FONT_LABEL,size=11)).pack(fill="x")
            v=tk.StringVar(); ctk.CTkEntry(bc,textvariable=v).pack(fill="x",pady=(0,4))
            self.fv[key]=v
        self.female_bay_lbl=ctk.CTkLabel(bc,text="Female bay width: —",anchor="w",text_color=UI_ACCENT)
        self.female_bay_lbl.pack(fill="x")
        self.male_bay_lbl=ctk.CTkLabel(bc,text="Male bay width: —",anchor="w",text_color=UI_ACCENT)
        self.male_bay_lbl.pack(fill="x")
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
        for k in ("row_spacing_in","planter_width_ft","num_female_rows","num_male_rows"):
            if k in self.fv:
                self.fv[k].trace_add("write", self._on_bay_change)

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

    def _on_track_excl_change(self, *_):
        if getattr(self, "_track_excl_refresh_id", None):
            self.after_cancel(self._track_excl_refresh_id)
        self._track_excl_refresh_id = self.after(600, self._redraw_tracks)

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
                for k in ("row_spacing_in","planter_width_ft","num_female_rows","num_male_rows"):
                    if k in p and k in self.fv: self.fv[k].set(str(p[k]))
                break

    def _save_new_preset(self):
        name=self._ask_string("Save Preset","Preset name:")
        if not name: return
        presets=self._load_bay_presets()
        entry={"name":name,
               "row_spacing_in":self.fv["row_spacing_in"].get(),
               "planter_width_ft":self.fv["planter_width_ft"].get(),
               "num_female_rows":self.fv["num_female_rows"].get(),
               "num_male_rows":self.fv["num_male_rows"].get()}
        presets=[p for p in presets if p["name"]!=name]
        presets.append(entry)
        self._save_bay_presets(presets)
        self._refresh_preset_list()
        self.preset_var.set(name)

    def _update_preset(self):
        name=self.preset_var.get()
        if not name:
            self._status("Select a bay preset to update (or use + to save a new one)."); return
        entry={"name":name,
               "row_spacing_in":self.fv["row_spacing_in"].get(),
               "planter_width_ft":self.fv["planter_width_ft"].get(),
               "num_female_rows":self.fv["num_female_rows"].get(),
               "num_male_rows":self.fv["num_male_rows"].get()}
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

    # ── Shelter count mode (per acre / total / spacing) ───────────────────────
    def _shelter_hint(self, mode):
        if mode=="per_acre": return "Shelters per acre × Acres = exact count placed."
        if mode=="spacing":  return "Distance between shelters. Fills the field at that spacing."
        return "Exact number of shelters to place (e.g. 135)."

    def _on_shelter_mode_change(self, _=None):
        mode=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        self.current_field["shelter_mode"]=mode
        key=self._shelter_mode_key[mode]
        self._loading_shelter_value=True
        self.shelter_value_var.set(self.fv[key].get())
        self._loading_shelter_value=False
        self.shelter_hint_lbl.configure(text=self._shelter_hint(mode))
        if self.show_shelters.get(): self._redraw_shelters()

    def _on_shelter_value_change(self, *_):
        if getattr(self,"_loading_shelter_value",False): return
        mode=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        key=self._shelter_mode_key[mode]
        self.fv[key].set(self.shelter_value_var.get())   # fv trace → _on_form_change → redraw

    def _refresh_bee_summary(self):
        """Update the three computed lines under the Bee Allocation block."""
        n = len(self.shelter_positions or [])
        total_trays, per, short, total_gals = self._compute_bee_distribution(n)
        if total_trays is None:
            self.bee_total_gals_lbl.configure(text="Total gals:   —")
            self.bee_total_trays_lbl.configure(text="Total trays:  —")
            self.bee_per_shelter_lbl.configure(text="Per shelter:  —")
            self.bee_short_lbl.configure(text="")
            return
        if per:
            lo, hi = min(per), max(per)
            ps_txt = f"{lo} trays" if lo == hi else f"{lo}–{hi} trays"
        else:
            ps_txt = "—"
        self.bee_total_gals_lbl.configure(text=f"Total gals:   {total_gals:g}")
        self.bee_total_trays_lbl.configure(text=f"Total trays:  {total_trays}")
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
        self.company_var.set(real[0]); self._on_company_change(real[0])

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
        if n: (DATA_DIR/n).mkdir(parents=True,exist_ok=True); self._refresh_company_list(); self.company_var.set(n); self._on_company_change()

    def _new_year(self):
        y=self._ask_string("New Year",f"Year (e.g. {datetime.date.today().year}):")
        if y: (DATA_DIR/self.company_var.get()/y).mkdir(parents=True,exist_ok=True); self._on_company_change(); self.year_var.set(y); self._refresh_field_list()

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
        if self._is_all_scope():
            self._status("Pick a specific company and year before creating a field."); return
        self.current_field=blank_field(self.company_var.get(),self.year_var.get())
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
        # Default ON for fields that predate the boundary_edge_shelters flag.
        self.boundary_edge_var.set(bool(f.get("boundary_edge_shelters",True)))
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
        f["boundary_edge_shelters"]=bool(self.boundary_edge_var.get())
        f["tray_distribution"]=self._tray_dist_labels.get(self.tray_dist_var.get(),"even")
        f["shelter_mode"]=self._shelter_mode_labels.get(self.shelter_mode_var.get(),"total")
        # Use the dropdown company/year when specific; otherwise keep the loaded
        # field's own (so a field opened from an All/All list still saves home).
        co=self.company_var.get(); yr=self.year_var.get()
        if co!=ALL_COMPANIES: f["company"]=co
        if yr!=ALL_YEARS:     f["year"]=yr
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
        if self.lld_boundary_poly:
            try: self.lld_boundary_poly.delete()
            except Exception: pass
        self.lld_boundary_poly=self.map_widget.set_polygon(
            corners,fill_color=None,outline_color="#FFFF88",border_width=2)
        self._status(f"→ {label}")

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
        self._show_context_btn("✔ Close Boundary", self._close_boundary)
        self._status("Click map to add boundary vertices. ✔ Close when done.")

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
            self._register_drag(f"bnd_{i}",lat,lon,str(i+1),"#FFD700","#B8860B",
                                lambda la,lo,i=i: self._on_bnd_vertex_drag(i,la,lo))
        self._update_bnd_preview()
        self.click_mode="boundary_edit"
        self._selected_bnd_vertex=None
        self._show_context_btn("✔ Done Editing", self._close_boundary)
        self._status("Click a vertex to select it (drag to move, 🗑 Delete to remove). Esc to deselect. ✔ Done when finished.")

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
        self._show_context_btn("✔ Done Editing",self._close_boundary)
        self._status("Click a vertex to select it (drag to move, 🗑 Delete to remove). ✔ Done when finished.")

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
                            lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo))

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
            pts=[]
            if ext==".shp":
                import shapefile as sf_mod
                r=sf_mod.Reader(path)
                shape=r.shape(0)
                pts=[(lat,lon) for lon,lat in shape.points]
            elif ext==".kml":
                pts=self._parse_kml_coords(path)
            elif ext==".kmz":
                with zipfile.ZipFile(path) as zf:
                    kml_name=next(n for n in zf.namelist() if n.endswith(".kml"))
                    kml_text=zf.read(kml_name).decode("utf-8")
                pts=self._parse_kml_coords_text(kml_text)
            if len(pts)<3:
                tkinter.messagebox.showerror("Upload Error","Boundary must have at least 3 points."); return
            self.current_field["boundary_polygon"]=[list(p) for p in pts]
            self.boundary_pts=pts
            self._redraw_boundary()
            self._status(f"Boundary loaded: {len(pts)} vertices from {Path(path).name}")
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

    def _mode_track(self):
        self._close_all_popups()
        if not self.fv["PP_Latitude"].get(): self._status("Set pivot first."); return
        self.click_mode="track"; self._status("Click on map to set track radius from pivot…")

    def _on_map_click(self,coords):
        lat,lon=coords
        mode=self.click_mode

        if mode=="pivot":
            self.fv["PP_Latitude"].set(f"{lat:.7f}"); self.fv["PP_Longitude"].set(f"{lon:.7f}")
            self.show_pivot.set(True)
            self._redraw_pivot()
            self.click_mode=None; self._status(f"Pivot: {lat:.5f}, {lon:.5f}")
            self._redraw_boundary(); self._redraw_passes(); self._redraw_tracks()

        elif mode=="boundary":
            self.boundary_pts.append((lat,lon))
            m=self.map_widget.set_marker(lat,lon,text=str(len(self.boundary_pts)),
                                          marker_color_circle="#FFD700",marker_color_outside="#B8860B")
            self.boundary_markers.append(m); self._update_bnd_preview()

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
                                lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo))
            self._update_bnd_preview()
            self.click_mode="boundary_edit"
            self._status("Vertex moved. Click another vertex or ✔ Done Editing.")

        elif mode=="track":
            try:
                plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            except ValueError: self._status("Set pivot first."); return
            r_m=haversine_m(plat,plon,lat,lon)
            self.current_field.setdefault("pivot_tracks",[]).append(round(r_m,2))
            self.click_mode=None; self._status(f"Track added: {r_m:.1f} m ({r_m/0.3048:.1f} ft)")
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
                self._status(f"Corner circle added: r={r_m:.1f} m ({r_m/0.3048:.1f} ft)")
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
        self._status(f"Boundary set ({len(self.boundary_pts)} vertices).")
        self.boundary_pts=[]; self._redraw_passes()
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

    def _update_bnd_preview(self):
        if self.boundary_poly: self.boundary_poly.delete()
        if len(self.boundary_pts)>=2:
            self.boundary_poly=self.map_widget.set_polygon(
                self.boundary_pts,fill_color=None,outline_color="#FFD700",border_width=2)

    def _redraw_boundary(self):
        if self.boundary_poly: self.boundary_poly.delete(); self.boundary_poly=None
        bp=self.current_field.get("boundary_polygon")
        if bp and len(bp)>=3:
            self.boundary_poly=self.map_widget.set_polygon(
                [tuple(p) for p in bp],fill_color=None,outline_color="#00CED1",border_width=2)

    def _on_bnd_vertex_drag(self,idx,lat,lon):
        if self._selected_bnd_vertex==idx:
            self._selected_bnd_vertex=None
            self._show_context_btn("✔ Done Editing",self._close_boundary)
        self.boundary_pts[idx]=(lat,lon)
        try: self.boundary_markers[idx].delete()
        except Exception: pass
        m=self.map_widget.set_marker(lat,lon,text=str(idx+1),
                                      marker_color_circle="#FFD700",marker_color_outside="#B8860B",
                                      command=self._make_bnd_vertex_cb(idx))
        self.boundary_markers[idx]=m
        self._register_drag(f"bnd_{idx}",lat,lon,str(idx+1),"#FFD700","#B8860B",
                            lambda la,lo,i=idx: self._on_bnd_vertex_drag(i,la,lo))
        self._update_bnd_preview()

    # ── Pivot tracks ───────────────────────────────────────────────────────────
    def _add_track_manual(self):
        use_m=self.unit_var.get()=="Metres"
        unit_label="metres" if use_m else "feet"
        val=tkinter.simpledialog.askfloat("Pivot Track",f"Radius from pivot ({unit_label}):")
        if val is None: return
        r_m=val if use_m else val*0.3048
        self.current_field.setdefault("pivot_tracks",[]).append(round(r_m,2))
        self._refresh_track_list(); self._redraw_tracks()

    def _remove_track(self):
        sel=self.track_lb.curselection()
        if not sel: return
        del self.current_field["pivot_tracks"][sel[0]]
        self._refresh_track_list(); self._redraw_tracks()
        if self.show_shelters.get(): self._redraw_shelters()

    def _toggle_pivot(self):
        """Toggle the pivot point marker AND its tracks together."""
        self._close_all_popups()
        on=not self.show_pivot.get()
        self.show_pivot.set(on)
        self.show_tracks.set(on)
        self._redraw_pivot()
        self._redraw_tracks()
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
        for i,r_m in enumerate(self.current_field.get("pivot_tracks") or []):
            for r,col,w in [(r_m+excl_m,"#32CD32",2),(max(1,r_m-excl_m),"#32CD32",1)]:
                self.track_circles.append(self.map_widget.set_polygon(
                    circle_pts(plat,plon,r),fill_color=None,outline_color=col,border_width=w))
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
        self._status(f"Corner path #{n} saved ({len(self.corner_arm_pts)} pts).")
        self.corner_arm_pts=[]
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

    def _redraw_corner_arms(self):
        for o in self.corner_arm_overlays:
            if not getattr(o,"_is_preview",False):
                try: o.delete()
                except Exception: pass
        self.corner_arm_overlays=[o for o in self.corner_arm_overlays if getattr(o,"_is_preview",False)]
        arms=self.current_field.get("corner_arms") or []
        colors=["#CC44FF","#44CCFF","#FF44AA","#44FFCC","#FF9944","#44AAFF"]
        for i,arm in enumerate(arms):
            col=colors[i % len(colors)]
            try:
                if arm.get("type")=="circle":
                    o=self.map_widget.set_polygon(
                        circle_pts(arm["lat"],arm["lon"],arm["radius_m"]),
                        fill_color=None,outline_color=col,border_width=2)
                else:
                    pts=arm.get("pts") or []
                    if len(pts)>=2:
                        o=self.map_widget.set_path([(p[0],p[1]) for p in pts],color=col,width=3)
                    else:
                        continue
                self.corner_arm_overlays.append(o)
            except Exception: pass

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

    # ── Shelter preview ────────────────────────────────────────────────────────
    def _toggle_shelters(self):
        self.show_shelters.set(not self.show_shelters.get())
        if self.show_shelters.get():
            self._redraw_shelters(); self._status("Shelter pins shown.")
        else:
            self._clear_shelters(); self._status("Shelter pins hidden.")

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

        # Outer sprayer limit — one sprayer-width inside the boundary
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

        max_rows=int(max_r/width_m)+2
        for r in range(-max_rows,max_rows+1):
            lat_e=r*width_m; lat_n=0
            pe=lat_e*cos_r-lat_n*sin_r; pn=lat_n*cos_r+lat_e*sin_r

            res=clip_line_to_polygon(pe,pn,tdx,tdy,poly_enu)
            if res is None: continue
            t1,t2=res
            e1,n1=pe+t1*tdx,pn+t1*tdy
            e2,n2=pe+t2*tdx,pn+t2*tdy
            lat1,lon1=enu_to_latlon(e1,n1,plat,plon)
            lat2,lon2=enu_to_latlon(e2,n2,plat,plon)
            try:
                path=self.map_widget.set_path([(lat1,lon1),(lat2,lon2)],color="#FF3333",width=1)
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
    def _calc_bays(self):
        try:
            rs=float(self.fv["row_spacing_in"].get() or 22)
            nf=int(self.fv["num_female_rows"].get() or 8)
            nm=int(self.fv["num_male_rows"].get() or 2)
        except (ValueError,TypeError):
            self._status("Enter numeric values for row spacing and row counts."); return
        f_in=(nf+1)*rs; m_in=(nm+1)*rs
        f_ft=f_in/12; m_ft=m_in/12; f_m=f_ft*0.3048; m_m=m_ft*0.3048
        use_m=self.unit_var.get()=="Metres"
        if use_m:
            self.female_bay_lbl.configure(text=f'Female bay: {f_in:.1f}" = {f_m:.3f} m')
            self.male_bay_lbl.configure(text=f'Male bay:   {m_in:.1f}" = {m_m:.3f} m')
        else:
            self.female_bay_lbl.configure(text=f'Female bay: {f_in:.1f}" = {f_ft:.3f} ft')
            self.male_bay_lbl.configure(text=f'Male bay:   {m_in:.1f}" = {m_ft:.3f} ft')
        self._status(f"Bay layout: female {f_in:.0f}\" ({f_ft:.2f} ft), male {m_in:.0f}\" ({m_ft:.2f} ft)")
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

    def _band_polygon_enu(self,x1,x2,tdx,tdy,ldx,ldy,poly_enu):
        p1e,p1n=x1*ldx,x1*ldy; p2e,p2n=x2*ldx,x2*ldy
        r1=clip_line_to_polygon(p1e,p1n,tdx,tdy,poly_enu)
        r2=clip_line_to_polygon(p2e,p2n,tdx,tdy,poly_enu)
        if r1 is None and r2 is None: return None
        if r1 is None: r1=r2
        if r2 is None: r2=r1
        t0=min(r1[0],r2[0]); t1=max(r1[1],r2[1])
        return [(p1e+t0*tdx,p1n+t0*tdy),(p2e+t0*tdx,p2n+t0*tdy),
                (p2e+t1*tdx,p2n+t1*tdy),(p1e+t1*tdx,p1n+t1*tdy)]

    def _redraw_bays(self):
        self._clear_bays()
        if not self.show_bays.get(): return
        try:
            plat=float(self.fv["PP_Latitude"].get()); plon=float(self.fv["PP_Longitude"].get())
            angle=float(self.fv["Spray_angle"].get() or 0)
            rs=float(self.fv["row_spacing_in"].get() or 22)
            nf=int(self.fv["num_female_rows"].get() or 8)
            nm=int(self.fv["num_male_rows"].get() or 2)
            bp=self.current_field.get("boundary_polygon")
        except (ValueError,TypeError): return
        if not bp or len(bp)<3: return
        row_m=rs*0.0254; female_m=(nf+1)*row_m; male_m=(nm+1)*row_m
        poly_enu=[latlon_to_enu(lat,lon,plat,plon) for lat,lon in bp]
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
            band=self._band_polygon_enu(cx+female_m/2,cx+female_m/2+male_m,tdx,tdy,ldx,ldy,poly_enu)
            if band:
                lpts=[enu_to_latlon(e,n,plat,plon) for e,n in band]
                try:
                    p=self.map_widget.set_polygon(lpts,fill_color="#001F7A",outline_color="#001F7A",border_width=0)
                    self.bay_polygons.append(p)
                except Exception: pass

    # ── Full overlay refresh ───────────────────────────────────────────────────
    def _redraw_all(self):
        f=self.current_field
        try:
            plat=float(f.get("PP_Latitude",0) or 0); plon=float(f.get("PP_Longitude",0) or 0)
            if plat and plon:
                self.map_widget.set_position(plat,plon); self.map_widget.set_zoom(14)
        except (ValueError,TypeError): pass
        self._redraw_pivot()
        self._redraw_boundary(); self._redraw_tracks(); self._redraw_passes(); self._redraw_bays(); self._redraw_corner_arms(); self._redraw_shelters()

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
        self._clear_shelters()

    # ── Pivot drag handler ─────────────────────────────────────────────────────
    def _on_pivot_drag(self,lat,lon):
        self.fv["PP_Latitude"].set(f"{lat:.7f}"); self.fv["PP_Longitude"].set(f"{lon:.7f}")
        if self.pivot_marker:
            try: self.pivot_marker.delete()
            except Exception: pass
        self.pivot_marker=self.map_widget.set_marker(lat,lon,text="Pivot",
                                                      marker_color_circle="red",
                                                      marker_color_outside="darkred")
        self._register_drag("pivot",lat,lon,"Pivot","red","darkred",self._on_pivot_drag,marker=self.pivot_marker)
        self._status(f"Pivot moved: {lat:.5f}, {lon:.5f}")
        self._redraw_boundary(); self._redraw_passes(); self._redraw_tracks()

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
            # Click without drag — pivot/boundary/track modes win over pin-tap so
            # the user can place points near existing pins.
            if self.click_mode is not None:
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
    def _git_pull(self):
        """Pull latest changes from GitHub on startup (background thread)."""
        import subprocess
        repo=Path(__file__).parent
        def run():
            try:
                r=subprocess.run(["git","pull","--rebase"],cwd=repo,
                                 capture_output=True,timeout=30)
                if b"Already up to date" not in r.stdout:
                    self.after(0,self._refresh_field_list)
                    self.after(0,self._refresh_preset_list)
                    self.after(0,self._refresh_bee_preset_list)
                    self.after(0,self._refresh_field_preset_list)
                    self.after(0,lambda:self._status("☁ Pulled latest changes"))
            except Exception: pass
        threading.Thread(target=run,daemon=True).start()

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
        co=f.get("company"); yr=f.get("year")
        if not co or co==ALL_COMPANIES or not yr or yr==ALL_YEARS:
            self._status("Pick a specific company and year before saving."); return
        if not f.get("Name"): self._status("Enter a field name."); return
        if not f.get("boundary_polygon"): self._status("⚠ No boundary drawn — field saved but cannot generate without one.");
        save_field(f); self._refresh_field_list(); self._status(f"Saved: {f['Name']}")
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
                    "%d field(s) written to:\n%s" % (ok,out_dir)))
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
