"""Regenerate the golden geometry baseline (tests/baseline_positions.json).

Run this ONLY when you have INTENTIONALLY changed the placement geometry and
have eyeballed that the new output is correct:

    python tests/_gen_baseline.py

It records, per field, the planned-shelter count and a stable hash of the
rounded positions. test_geometry.py asserts the live engine still matches, so an
accidental geometry regression fails loudly.
"""
import json
import glob
import os
import hashlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maketentgrid as m

SKIP = {"bay_presets.json", "bee_presets.json", "field_presets.json",
        "cost_prefs.json", "overview_prefs.json"}


def field_files():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in sorted(glob.glob(os.path.join(root, "fields", "**", "*.json"), recursive=True)):
        b = os.path.basename(p)
        if b in SKIP or p.endswith("_map.json"):
            continue
        try:
            f = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if "PP_Latitude" in f:
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            yield rel, f


def positions_hash(pos):
    rounded = [(round(la, 7), round(lo, 7)) for la, lo in pos]
    return hashlib.sha256(json.dumps(rounded).encode()).hexdigest()[:16]


def build():
    baseline = {}
    for rel, f in field_files():
        try:
            pos = list(m.get_tent_positions(dict(f), use_metric=True))
        except Exception as e:
            baseline[rel] = {"error": str(e)}
            continue
        baseline[rel] = {"count": len(pos), "hash": positions_hash(pos)}
    return baseline


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    baseline = build()
    with open(os.path.join(here, "baseline_positions.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(baseline, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"baseline written: {len(baseline)} fields")
