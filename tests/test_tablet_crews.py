"""The tablet's "other crews" feature, exercised in Node against the real files.

Each tablet already publishes its position to crews/<id> for the office Monitor;
these cover the consumer side added so crews can also see EACH OTHER:
  * beePublish.onCrews — excludes self, drops stale nodes and bad coordinates
  * refreshCrewLayer   — Work mode shows only crews on the same field, Map mode
                         shows every active crew, label carries placed/total

Runs tests/tablet_crews_harness.mjs, which pulls both pieces out of the shipped
tablet/publish.js and tablet/app.js (so it can't drift from the app).
Skipped if Node isn't installed.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "tablet_crews_harness.mjs")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


@pytest.fixture(scope="module")
def res():
    out = subprocess.run([NODE, HARNESS], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


def test_relay_initialises(res):
    assert res["relayEnabled"] is True
    assert res["onCrewsReturnsUnsub"] is True


def test_own_crew_is_excluded(res):
    assert "crew-me" not in res["deliveredIds"]


def test_stale_and_bad_coord_crews_dropped(res):
    # crew-c is 10 min old, crew-d has lat=null — neither should reach the map.
    assert "crew-c" not in res["deliveredIds"]
    assert "crew-d" not in res["deliveredIds"]
    assert res["deliveredIds"] == ["crew-b", "crew-e"]


def test_work_mode_shows_only_same_field(res):
    # Field A has one other crew (Bravo); Echo is on field B and must not show.
    assert res["workModeLabels"] == ["Bravo 12/40"]


def test_work_mode_other_field_shows_none(res):
    assert res["workModeOtherField"] == 0


def test_map_mode_shows_every_crew(res):
    assert res["mapModeCount"] == 2
    assert [-112.201, 49.781] in res["mapModeCoords"]


def test_unsubscribe_is_safe(res):
    assert res["unsubOk"] is True
