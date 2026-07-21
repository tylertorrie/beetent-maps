"""Drift guard: the tablet's JavaScript geometry math vs the desktop's Python.

The tablet re-implements two numeric cores by hand in JS:
  * `_UTM.enu(...)`   — a port of utmish / beetent_app.latlon_to_enu
  * `shiftLatLon(...)` — mirrors beetent_app._shift_pt

Nothing links them, so changing one silently leaves the other behind (the crew
would calibrate against different numbers than the office planned with). These
tests extract the REAL JS out of tablet/app.js, run it in Node, and assert it
agrees with the Python to sub-millimetre precision.

Skipped automatically if Node isn't installed.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utmish

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, "tablet", "app.js")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

# Points spread across the real operating area (southern Alberta) plus a couple
# of stress cases, plus the pivot they're measured against.
PIVOT = (49.7827835, -112.2011202)
SAMPLES = [
    (49.7827835, -112.2011202),      # exactly on the pivot
    (49.7830000, -112.2000000),
    (49.7800000, -112.2050000),
    (49.7900000, -112.1900000),
    (53.5461000, -113.4912000),      # far north (other field area)
    (49.7827835, -112.1000000),      # far east of the pivot
]
SHIFTS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (-12.5, 7.25), (250.0, -180.0)]


def _extract(pattern, src, what):
    m = re.search(pattern, src, re.S)
    assert m, f"could not find {what} in tablet/app.js — did it get renamed?"
    return m.group(0)


def _run_node(script):
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"node failed:\n{out.stderr}"
        return json.loads(out.stdout)
    finally:
        os.unlink(path)


def _js_source():
    """Pull the two math blocks verbatim out of the shipped tablet app."""
    src = open(APP_JS, encoding="utf-8").read()
    utm = _extract(r"const _UTM = \(function \(\) \{.*?\n\}\)\(\);", src, "_UTM block")
    shift = _extract(r"function shiftLatLon\(lat, lon, de, dn\) \{.*?\n\}", src,
                     "shiftLatLon")
    return utm + "\n" + shift


def test_utm_enu_matches_python():
    """_UTM.enu (JS) must equal latlon_to_enu (Python) — this is the frame the
    crew's Calibrate projects into."""
    js = _js_source() + """
const out = [];
for (const [lat, lon] of %s) {
  out.push(_UTM.enu(lat, lon, %r, %r));
}
console.log(JSON.stringify(out));
""" % (json.dumps(SAMPLES), PIVOT[0], PIVOT[1])
    got = _run_node(js)

    def py_enu(lat, lon, plat, plon):
        pe, pn = utmish.from_lonlat(plon, plat, plon)
        e, n = utmish.from_lonlat(lon, lat, plon)
        return (e - pe, n - pn)

    for (lat, lon), (je, jn) in zip(SAMPLES, got):
        pe, pn = py_enu(lat, lon, *PIVOT)
        assert je == pytest.approx(pe, abs=1e-6), f"east drift at {lat},{lon}"
        assert jn == pytest.approx(pn, abs=1e-6), f"north drift at {lat},{lon}"


def test_shift_latlon_matches_python():
    """shiftLatLon (JS) must equal _shift_pt (Python) — used to move the grid."""
    cases = [(lat, lon, de, dn) for (lat, lon) in SAMPLES for (de, dn) in SHIFTS]
    js = _js_source() + """
const out = [];
for (const [lat, lon, de, dn] of %s) { out.push(shiftLatLon(lat, lon, de, dn)); }
console.log(JSON.stringify(out));
""" % json.dumps(cases)
    got = _run_node(js)

    def py_shift(lat, lon, de, dn):          # mirrors _shift_pt
        return (lat + dn / 111111.0,
                lon + de / (111111.0 * math.cos(math.radians(lat))))

    for (lat, lon, de, dn), (jlat, jlon) in zip(cases, got):
        plat, plon = py_shift(lat, lon, de, dn)
        assert jlat == pytest.approx(plat, abs=1e-12)
        assert jlon == pytest.approx(plon, abs=1e-12)


def test_enu_roundtrip_is_stable_in_js():
    """Sanity: projecting the pivot itself must land at the origin in JS too."""
    js = _js_source() + """
console.log(JSON.stringify(_UTM.enu(%r, %r, %r, %r)));
""" % (PIVOT[0], PIVOT[1], PIVOT[0], PIVOT[1])
    e, n = _run_node(js)
    assert e == pytest.approx(0.0, abs=1e-6)
    assert n == pytest.approx(0.0, abs=1e-6)
