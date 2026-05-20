"""
One-time conversion: read the Alberta Township System V4.1 section shapefile and
write a compact lookup file used by `geocode_lld()` in beetent_app.py.

For each section record where RA == '' (the actual surveyed section polygon,
not road allowances or rights-of-way), this stores the axis-aligned bounding
box (lat_min, lat_max, lon_min, lon_max) keyed by (mer, twp, rng, sec).

Quarter and half sections are synthesized at lookup time by subdividing the
section bbox.

Usage:
    python build_ats_lookup.py <path_to_V4-1_SEC>  [<output_file>]

Default output: fields/ats_sections.bin (next to this script)
"""
import sys, os, struct
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import shapefile  # vendored


def build(shp_base, out_path):
    print(f"Reading {shp_base}.shp ...")
    r = shapefile.Reader(shp_base)
    total = len(r)
    print(f"  {total} records total")

    rows = []
    skipped = 0
    for i, rec in enumerate(r.iterRecords()):
        if rec['RA'] != '':
            skipped += 1
            continue
        m, twp, rng, sec = rec['M'], rec['TWP'], rec['RGE'], rec['SEC']
        if not (1 <= m <= 6 and 1 <= twp <= 127 and 1 <= rng <= 35 and 1 <= sec <= 36):
            skipped += 1
            continue
        s = r.shape(i)
        lon_min, lat_min, lon_max, lat_max = s.bbox
        rows.append((m, twp, rng, sec, lat_min, lat_max, lon_min, lon_max))
        if (i + 1) % 50000 == 0:
            print(f"  read {i+1}/{total} ...")

    print(f"  kept {len(rows)} sections, skipped {skipped} (road allowances / out-of-range)")

    # Packed binary format:
    #   header: 4-byte magic 'ATS1' + 4-byte little-endian count
    #   record: M(u8) TWP(u8) RGE(u8) SEC(u8) lat_min(f32) lat_max(f32) lon_min(f32) lon_max(f32)  = 20 bytes
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(b'ATS1')
        f.write(struct.pack('<I', len(rows)))
        rec_fmt = struct.Struct('<BBBBffff')
        for row in rows:
            f.write(rec_fmt.pack(*row))

    size = out_path.stat().st_size
    print(f"  wrote {out_path} ({size:,} bytes = {size/1024/1024:.2f} MB)")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    shp_base = sys.argv[1]
    if shp_base.lower().endswith('.shp'):
        shp_base = shp_base[:-4]
    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else HERE / 'fields' / 'ats_sections.bin'
    build(shp_base, out_path)
