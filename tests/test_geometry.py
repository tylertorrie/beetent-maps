"""Regression + property tests for the placement engine (maketentgrid.py).

The engine is pure (no GUI), so it's the right place to lock behaviour down.
Every geometry bug the field hit — the gap-collapse, one-sided bays, the
alignment mesh drift — traces back to functions exercised here. Run with:

    python -m pytest tests/ -q

The golden test (test_positions_match_baseline) compares live output to
tests/baseline_positions.json; regenerate that ONLY on an intentional geometry
change via `python tests/_gen_baseline.py`.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maketentgrid as m
from tests._gen_baseline import field_files, positions_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS = 22 * 0.0254   # a common row spacing in metres, for the pure-fn tests

# Load fields once; parametrize by relative path so failures name the field.
FIELDS = dict(field_files())
BAY_FIELDS = {k: f for k, f in FIELDS.items()
              if str(f.get("use_bays", True)).lower() not in ("false", "0", "no")
              and f.get("boundary_polygon")}


# ── resolve_row_mask ────────────────────────────────────────────────────────
def test_mask_centered_repeats_to_total_rows():
    # centered 6F/3M unit = FFFMMMFFF, tiled to 20 rows.
    mask = m.resolve_row_mask(6, 3, "centered", "", total_rows=20)
    assert mask == "FFFMMMFFFFFFMMMFFFFF"
    assert len(mask) == 20


def test_mask_outer_puts_male_on_edges():
    mask = m.resolve_row_mask(8, 2, "outer", "", total_rows=10)
    assert mask == "MFFFFFFFFM"


def test_mask_custom_is_verbatim():
    assert m.resolve_row_mask(6, 3, "custom", "mmff", total_rows=99) == "MMFF"


def test_mask_length_equals_total_rows():
    for nf, nm, tr in [(6, 3, 20), (8, 2, 10), (14, 4, 36), (12, 4, 32)]:
        assert len(m.resolve_row_mask(nf, nm, "centered", "", tr)) == tr


# ── mask_runs ───────────────────────────────────────────────────────────────
def test_mask_runs_basic():
    assert m.mask_runs("FFFMMMFFF", "M") == [(3, 6)]
    assert m.mask_runs("MFFM", "M") == [(0, 1), (3, 4)]
    assert m.mask_runs("FFFF", "M") == []


# ── bay_slot_lefts (the gap-aware tiling; regression for the gap-collapse bug) ─
def test_gap0_reproduces_uniform_tiling_exactly():
    # gap=0 MUST be byte-for-byte the old uniform layout, so gap-free fields
    # never move. (Guards the gap-aware change from regressing the 13 gap=0 fields.)
    mask = m.resolve_row_mask(6, 3, "centered", "", 20)
    lefts, pass_w = m.bay_slot_lefts(mask, RS, 0.0)
    # Cumulative addition differs from k*rs only by float epsilon (~1e-15 m,
    # sub-nanometre) — physically the same uniform layout, absorbed by the 1e-7
    # deg rounding downstream (the golden baseline confirms gap-free fields hold).
    assert lefts == pytest.approx([k * RS for k in range(len(mask))])
    assert pass_w == pytest.approx(len(mask) * RS)


def test_gap_inserts_between_bays_not_inside_male_run():
    # The Wordmans regression: a 33" gap must NOT shrink the male band. Each M
    # run keeps its own (e-s)*rs width; the gaps land between bays.
    gap = 33 * 0.0254
    mask = m.resolve_row_mask(6, 3, "centered", "", 20)   # FFFMMMFFFFFFMMMFFFFF
    lefts, pass_w = m.bay_slot_lefts(mask, RS, gap)
    for (s, e) in m.mask_runs(mask, "M"):
        band_w = (lefts[e - 1] + RS) - lefts[s]           # west edge .. east edge
        assert band_w == pytest.approx((e - s) * RS)      # 3 rows = 66", uncollapsed
        assert band_w > 0
    # pass width grew by one gap per male/female transition (4 here).
    transitions = sum(1 for i in range(1, len(mask)) if mask[i] != mask[i - 1])
    assert pass_w == pytest.approx(len(mask) * RS + transitions * gap)


def test_bay_slot_lefts_monotonic():
    gap = 33 * 0.0254
    mask = m.resolve_row_mask(6, 3, "centered", "", 20)
    lefts, _ = m.bay_slot_lefts(mask, RS, gap)
    assert all(lefts[i] < lefts[i + 1] for i in range(len(lefts) - 1))


# ── male_bay_shelter_laterals ───────────────────────────────────────────────
def test_laterals_sorted_unique():
    xs = m.male_bay_shelter_laterals(6, 3, "centered", "", 20, RS, 1.5, 400.0)
    assert xs == sorted(xs)
    assert len(xs) == len(set(xs))


def test_laterals_empty_when_no_male_rows():
    # all-female mask has no male bays → engine falls back (returns []).
    assert m.male_bay_shelter_laterals(6, 0, "centered", "", 6, RS, 1.5, 400.0) == []


def test_laterals_gap0_matches_default_arg():
    a = m.male_bay_shelter_laterals(6, 3, "centered", "", 20, RS, 1.5, 400.0)
    b = m.male_bay_shelter_laterals(6, 3, "centered", "", 20, RS, 1.5, 400.0, gap_m=0.0)
    assert a == b


# ── get_tent_positions over every real field ────────────────────────────────
@pytest.mark.parametrize("rel", sorted(FIELDS), ids=lambda r: os.path.basename(r))
def test_positions_finite_and_deterministic(rel):
    f = FIELDS[rel]
    p1 = list(m.get_tent_positions(dict(f), use_metric=True))
    p2 = list(m.get_tent_positions(dict(f), use_metric=True))
    assert p1 == p2, "engine must be deterministic for the same input"
    for la, lo in p1:
        assert -90 <= la <= 90 and -180 <= lo <= 180
        assert la == la and lo == lo   # not NaN


@pytest.mark.parametrize("rel", sorted(BAY_FIELDS), ids=lambda r: os.path.basename(r))
def test_bay_fields_place_shelters(rel):
    pos = m.get_tent_positions(dict(BAY_FIELDS[rel]), use_metric=True)
    assert len(pos) > 0, "a bay field with a boundary should place shelters"


@pytest.mark.parametrize("rel", sorted(FIELDS), ids=lambda r: os.path.basename(r))
def test_metric_and_imperial_both_run(rel):
    f = FIELDS[rel]
    assert isinstance(m.get_tent_positions(dict(f), use_metric=True), list)
    assert isinstance(m.get_tent_positions(dict(f), use_metric=False), list)


# ── golden regression: live output matches the committed baseline ───────────
with open(os.path.join(ROOT, "tests", "baseline_positions.json"), encoding="utf-8") as _fh:
    BASELINE = json.load(_fh)


@pytest.mark.parametrize("rel", sorted(BASELINE), ids=lambda r: os.path.basename(r))
def test_positions_match_baseline(rel):
    exp = BASELINE[rel]
    if "error" in exp:
        pytest.skip(f"baseline records an engine error: {exp['error']}")
    f = FIELDS.get(rel)
    assert f is not None, f"field {rel} in baseline but missing from repo"
    pos = list(m.get_tent_positions(dict(f), use_metric=True))
    assert len(pos) == exp["count"], (
        f"shelter count changed for {rel}: {len(pos)} vs baseline {exp['count']}. "
        f"If intentional, rerun tests/_gen_baseline.py.")
    assert positions_hash(pos) == exp["hash"], (
        f"planned positions moved for {rel}. If intentional, rerun _gen_baseline.py.")


# ── field_warnings (save-time validation) ──────────────────────────────────
def _base_field():
    return {"use_bays": True, "num_female_rows": 6, "num_male_rows": 3,
            "row_spacing_in": 22, "total_rows": 20, "bay_gap_in": 0,
            "boundary_polygon": [[0, 0], [0, 1], [1, 1], [1, 0]]}


def test_warnings_clean_field_is_silent():
    assert m.field_warnings(_base_field()) == []


def test_warnings_no_male_rows():
    f = _base_field(); f["num_male_rows"] = 0
    assert any("male rows" in w.lower() for w in m.field_warnings(f))


def test_warnings_total_rows_too_small():
    f = _base_field(); f["total_rows"] = 5    # < 6+3
    assert any("total rows" in w.lower() for w in m.field_warnings(f))


def test_warnings_custom_mask_length_mismatch():
    f = _base_field(); f["row_layout"] = "custom"; f["custom_row_mask"] = "MMFF"
    assert any("mask" in w.lower() for w in m.field_warnings(f))


def test_warnings_huge_gap_flagged():
    # Wordmans-style: gap each side as wide as the female bay.
    f = _base_field(); f["bay_gap_in"] = 66   # >= 6*22/2 female width
    assert any("gap" in w.lower() for w in m.field_warnings(f))


def test_warnings_boundary_too_few_points():
    f = _base_field(); f["boundary_polygon"] = [[0, 0], [0, 1]]
    assert any("boundary" in w.lower() for w in m.field_warnings(f))


def test_warnings_blanket_planted_skips_bay_checks():
    f = _base_field(); f["use_bays"] = False; f["num_male_rows"] = 0
    assert m.field_warnings(f) == []   # no bays → no male-bay warning


def test_warnings_all_real_fields_clean():
    # None of the shipped fields should trip the validator (Wordmans/Carrots use
    # the gap-aware geometry now, so gap=33 no longer collapses; 33 < 66 female).
    for rel, f in FIELDS.items():
        assert m.field_warnings(f) == [], f"{os.path.basename(rel)}: {m.field_warnings(f)}"


# ── crew_route ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rel", sorted(BAY_FIELDS), ids=lambda r: os.path.basename(r))
def test_crew_route_nonnegative_length(rel):
    route, total_m = m.crew_route(dict(BAY_FIELDS[rel]), use_metric=True)
    assert total_m >= 0
    assert isinstance(route, list)
    if route:
        assert total_m > 0
