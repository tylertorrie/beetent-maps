"""Map rotation for tkintermapview (which has NO native rotation support).

tkintermapview 1.29 renders satellite tiles strictly north-up via
canvas.create_image (tkinter can't rotate a canvas image), and has no bearing
concept anywhere. This module adds map rotation by monkeypatching the few
choke points every on-screen position flows through:

  * CanvasTile.draw                         — rotate the tile imagery
  * CanvasPositionMarker/Polygon/Path
        .get_canvas_pos                     — rotate every overlay + the app's
                                              own pins (which read get_canvas_pos)
  * TkinterMapView.convert_canvas_coords_to_decimal_coords
                                            — un-rotate mouse events so clicks
                                              and pin-drags still hit the right
                                              lat/lon
  * request_image                           — keep a PIL copy of each tile so it
                                              can be rotated (the lib only keeps
                                              the Tk PhotoImage)

DESIGN — SAFE BY DEFAULT: every patch short-circuits to the original behaviour
when the map bearing is 0, so a north-up map is byte-for-byte identical to the
unpatched library. Rotation only engages once the user turns the compass, so a
rotation bug can never break the normal view.

Pins/markers stay SCREEN-UPRIGHT automatically: we only rotate each overlay's
ANCHOR POSITION, never the pin/teardrop/text shape itself — so as the map turns,
pins appear to swing around but stay drawn upright, exactly as asked.

KNOWN LIMITATION (v1): the four screen corners can show the map background when
rotated (the library only loads tiles for the north-up viewport). The centred
field stays covered. Corner fill needs extra tile loading — a follow-up.

Pinned to tkintermapview 1.29 (request_image is mirrored below).
"""
import io
import math
import sqlite3

import tkintermapview.map_widget as _mw_mod
from tkintermapview.canvas_tile import CanvasTile
from tkintermapview.canvas_position_marker import CanvasPositionMarker
from tkintermapview.canvas_polygon import CanvasPolygon
from tkintermapview.canvas_path import CanvasPath

import PIL
from PIL import Image, ImageTk
import requests

_TkinterMapView = _mw_mod.TkinterMapView

# Rotated-tile PhotoImage cache: keyed by (id(pil), tile_px, bearing) so panning
# at a fixed bearing reuses rotated tiles (only turning the compass rebuilds).
_rot_tile_cache = {}
_ROT_CACHE_MAX = 4000

_installed = False


# ── bearing accessors ───────────────────────────────────────────────────────
def get_bearing(mw):
    return float(getattr(mw, "_bearing_deg", 0.0) or 0.0)


def set_bearing(mw, deg, on_redraw=None):
    """Set the map bearing (degrees, the heading shown at the TOP of the screen)
    and re-render. on_redraw (optional) is called after the library redraw so the
    host app can refresh its own canvas overlays (shelter pins, drag handles)."""
    mw._bearing_deg = float(deg) % 360.0
    # Bearing changed → previously cached rotated tiles are at the old angle.
    if abs(mw._bearing_deg) < 1e-9:
        mw._bearing_deg = 0.0
    try:
        mw.draw_zoom()          # re-place + re-image every tile and lib overlay
    except Exception:
        pass
    if on_redraw is not None:
        try: on_redraw()
        except Exception: pass


# ── rotation maths (about the canvas centre) ────────────────────────────────
def _rotate(mw, x, y):
    """Rotate a north-up canvas point about the canvas centre so that the
    bearing heading ends up at the TOP of the screen (bearing 90 = East up).
    The tile image rotation in `_tile_draw` uses the matched opposite sign so
    imagery and overlays always align; if the WHOLE map ever turns the wrong
    way, flip the sign of `a` here AND the image rotate in `_tile_draw`
    together (they must stay matched)."""
    b = get_bearing(mw)
    if not b:
        return x, y
    cx, cy = mw.width / 2.0, mw.height / 2.0
    a = math.radians(-b)
    c, s = math.cos(a), math.sin(a)
    dx, dy = x - cx, y - cy
    return cx + dx * c - dy * s, cy + dx * s + dy * c


def _unrotate(mw, x, y):
    """Inverse of _rotate — maps a screen point back to its north-up position so
    convert_canvas_coords_to_decimal_coords yields the true lat/lon under the
    cursor."""
    b = get_bearing(mw)
    if not b:
        return x, y
    cx, cy = mw.width / 2.0, mw.height / 2.0
    a = math.radians(b)
    c, s = math.cos(a), math.sin(a)
    dx, dy = x - cx, y - cy
    return cx + dx * c - dy * s, cy + dx * s + dy * c


# ── PIL tile cache (so tiles can be rotated) ────────────────────────────────
_PIL_CACHE_MAX = 600     # ~visible tiles × several viewports; bounds memory


def _pil_cache(mw):
    c = getattr(mw, "_pil_tile_cache", None)
    if c is None:
        c = {}
        mw._pil_tile_cache = c
    return c


def _pil_store(mw, key, image):
    """Cache a PIL tile, evicting oldest entries past the cap so always-on
    caching (needed so tiles are ready the instant the user rotates) stays
    memory-bounded even at bearing 0."""
    c = _pil_cache(mw)
    if key not in c and len(c) >= _PIL_CACHE_MAX:
        for k in list(c.keys())[:len(c) - _PIL_CACHE_MAX + 1]:
            del c[k]
    c[key] = image


def _request_image(self, zoom, x, y, db_cursor=None):
    """Mirror of TkinterMapView.request_image (v1.29) that ALSO stashes the PIL
    image (keyed "{zoom}{x}{y}") so _tile_draw can rotate it. Behaviour is
    otherwise identical to the original."""
    if db_cursor is not None:
        try:
            db_cursor.execute(
                "SELECT t.tile_image FROM tiles t WHERE t.zoom=? AND t.x=? AND t.y=? AND t.server=?;",
                (zoom, x, y, self.tile_server))
            result = db_cursor.fetchone()
            if result is not None:
                image = Image.open(io.BytesIO(result[0]))
                _pil_store(self, f"{zoom}{x}{y}", image)
                image_tk = ImageTk.PhotoImage(image)
                self.tile_image_cache[f"{zoom}{x}{y}"] = image_tk
                return image_tk
            elif self.use_database_only:
                return self.empty_tile_image
        except sqlite3.OperationalError:
            if self.use_database_only:
                return self.empty_tile_image
        except Exception:
            return self.empty_tile_image

    try:
        url = self.tile_server.replace("{x}", str(x)).replace("{y}", str(y)).replace("{z}", str(zoom))
        image = Image.open(requests.get(url, stream=True, headers={"User-Agent": "TkinterMapView"}).raw)

        if self.overlay_tile_server is not None:
            url = self.overlay_tile_server.replace("{x}", str(x)).replace("{y}", str(y)).replace("{z}", str(zoom))
            image_overlay = Image.open(requests.get(url, stream=True, headers={"User-Agent": "TkinterMapView"}).raw)
            image = image.convert("RGBA")
            image_overlay = image_overlay.convert("RGBA")
            if image_overlay.size is not (self.tile_size, self.tile_size):
                image_overlay = image_overlay.resize((self.tile_size, self.tile_size), Image.LANCZOS)
            image.paste(image_overlay, (0, 0), image_overlay)

        if self.running:
            _pil_store(self, f"{zoom}{x}{y}", image)
            image_tk = ImageTk.PhotoImage(image)
        else:
            return self.empty_tile_image

        self.tile_image_cache[f"{zoom}{x}{y}"] = image_tk
        return image_tk
    except PIL.UnidentifiedImageError:
        self.tile_image_cache[f"{zoom}{x}{y}"] = self.empty_tile_image
        return self.empty_tile_image
    except requests.exceptions.ConnectionError:
        return self.empty_tile_image
    except Exception:
        return self.empty_tile_image


def _pil_for_tile(mw, tile_name_position):
    z = round(mw.zoom)
    return _pil_cache(mw).get(f"{z}{tile_name_position[0]}{tile_name_position[1]}")


# ── patched tile draw ───────────────────────────────────────────────────────
def _tile_draw(self, image_update=False):
    mw = self.map_widget
    b = get_bearing(mw)
    if not b:
        return _orig_tile_draw(self, image_update=image_update)

    # North-up NW corner + on-canvas tile size.
    cx, cy = self.get_canvas_pos()
    wtw = mw.lower_right_tile_pos[0] - mw.upper_left_tile_pos[0]
    wth = mw.lower_right_tile_pos[1] - mw.upper_left_tile_pos[1]
    if wtw == 0 or wth == 0:
        return
    tile_w = mw.width / wtw
    tile_h = mw.height / wth
    # Rotate the tile CENTRE about the screen centre.
    rcx, rcy = _rotate(mw, cx + tile_w / 2.0, cy + tile_h / 2.0)

    pil = _pil_for_tile(mw, self.tile_name_position)
    if pil is None:
        # PIL not cached (tile still loading) — skip; redraws once it lands.
        if self.canvas_object is not None:
            try: mw.canvas.delete(self.canvas_object)
            except Exception: pass
            self.canvas_object = None
        return

    key = (id(pil), round(tile_w), round(b, 1))
    rot_tk = _rot_tile_cache.get(key)
    if rot_tk is None:
        img = pil.resize((max(1, round(tile_w)) + 1, max(1, round(tile_h)) + 1), Image.BILINEAR)
        # Matched to _rotate's sign (which now uses -b): image rotates by +b so
        # imagery turns the same visual direction as the overlay positions.
        img = img.rotate(b, resample=Image.BILINEAR, expand=True)
        rot_tk = ImageTk.PhotoImage(img)
        if len(_rot_tile_cache) > _ROT_CACHE_MAX:
            _rot_tile_cache.clear()
        _rot_tile_cache[key] = rot_tk
    self._rot_tk = rot_tk    # keep a ref so Tk doesn't GC the image

    if self.canvas_object is None:
        self.canvas_object = mw.canvas.create_image(rcx, rcy, image=rot_tk,
                                                     anchor="center", tags="tile")
    else:
        mw.canvas.coords(self.canvas_object, rcx, rcy)
        mw.canvas.itemconfig(self.canvas_object, image=rot_tk)
    mw.manage_z_order()


# ── patched overlay get_canvas_pos (rotate the anchor position) ─────────────
def _marker_get_canvas_pos(self, position):
    x, y = _orig_marker_gcp(self, position)
    return _rotate(self.map_widget, x, y)


def _polygon_get_canvas_pos(self, position, wtw, wth):
    x, y = _orig_polygon_gcp(self, position, wtw, wth)
    return _rotate(self.map_widget, x, y)


def _path_get_canvas_pos(self, position, wtw, wth):
    x, y = _orig_path_gcp(self, position, wtw, wth)
    return _rotate(self.map_widget, x, y)


def _polygon_draw(self, move=False):
    # The pan "move" fast-path just TRANSLATES cached points, which is wrong once
    # rotated — force a full recompute through the (rotating) get_canvas_pos.
    if get_bearing(self.map_widget):
        move = False
    return _orig_polygon_draw(self, move=move)


def _path_draw(self, move=False):
    if get_bearing(self.map_widget):
        move = False
    return _orig_path_draw(self, move=move)


# ── patched inverse conversion (mouse events) ───────────────────────────────
def _convert_canvas_to_decimal(self, canvas_x, canvas_y):
    ux, uy = _unrotate(self, canvas_x, canvas_y)
    return _orig_convert(self, ux, uy)


# ── install ─────────────────────────────────────────────────────────────────
_orig_tile_draw = CanvasTile.draw
_orig_marker_gcp = CanvasPositionMarker.get_canvas_pos
_orig_polygon_gcp = CanvasPolygon.get_canvas_pos
_orig_path_gcp = CanvasPath.get_canvas_pos
_orig_polygon_draw = CanvasPolygon.draw
_orig_path_draw = CanvasPath.draw
_orig_convert = _TkinterMapView.convert_canvas_coords_to_decimal_coords
_orig_request = _TkinterMapView.request_image


def enable(mw):
    """Install the rotation patches (class-level, done once) and initialise the
    bearing on this map instance to 0 (north-up = unchanged behaviour)."""
    global _installed
    if not getattr(mw, "_bearing_deg", None):
        mw._bearing_deg = 0.0
    if _installed:
        return
    CanvasTile.draw = _tile_draw
    CanvasPositionMarker.get_canvas_pos = _marker_get_canvas_pos
    CanvasPolygon.get_canvas_pos = _polygon_get_canvas_pos
    CanvasPath.get_canvas_pos = _path_get_canvas_pos
    CanvasPolygon.draw = _polygon_draw
    CanvasPath.draw = _path_draw
    _TkinterMapView.convert_canvas_coords_to_decimal_coords = _convert_canvas_to_decimal
    _TkinterMapView.request_image = _request_image
    _installed = True
