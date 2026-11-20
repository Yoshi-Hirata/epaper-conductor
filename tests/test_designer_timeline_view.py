"""The designers' simulator: the Timeline's zoom/pan window (2026-11-20).

The Kunitachi show is 36:34; drawn as a share of the whole show a 7 s cue
was ~5 px wide. The Timeline now draws a window {v0, v1} of the show, and
every time<->x conversion on it goes through SIM.transport.view (pct /
timeAt). These tests drive that maths in Node (no browser), check that the
whole-show window is exactly the old mapping (so nothing changes for a
designer who never zooms), and check statically that designer-app.js has no
whole-show conversion left and keeps the window out of the project.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "conductor" / "web" / "sim"

_NODE = r"""
const path = require("path");
require(path.join(process.argv[2], "conductor", "web", "sim", "transport.js"));
const V = SIM.transport.view;
const out = {};
const D = 2194;                                   // 36:34
const all = { v0: 0, v1: D };

// the whole show: the old 100 * t / D and (x / width) * D, to the bit
out.identityPct = [0, 1, 7, 480.5, 1234.567, D].every(t =>
  V.pct(V.clamp(all, D), t).toFixed(3) === (100 * t / D).toFixed(3));
out.identityTime = [0, 0.1, 0.25, 0.5, 0.999, 1].every(f => V.timeAt(all, D, f) === f * D);
out.clampAll = V.clamp(all, D);
out.isAll = V.isAll(V.clamp(all, D), D);

// clamping
out.garbage = [V.clamp(null, D), V.clamp({ v0: "x", v1: 3 }, D), V.clamp({ v0: 50, v1: 40 }, D)];
out.minWidth = V.clamp({ v0: 100, v1: 110 }, D);          // 10 s -> 30 s
out.pastEnd = V.clamp({ v0: 2100, v1: 2300 }, D);         // slid back inside
out.beforeStart = V.clamp({ v0: -50, v1: 70 }, D);
out.tooWide = V.clamp({ v0: -10, v1: 5000 }, D);
out.shortShow = V.clamp({ v0: 0, v1: 10 }, 20);           // a 20 s show: whole show
out.noShow = V.clamp(all, 0);

// a window from 8:00 to 10:00 (the designer's screenshot)
const w = { v0: 480, v1: 600 };
out.pctIn = [V.pct(w, 480), V.pct(w, 540), V.pct(w, 600)];
out.pctOut = [V.pct(w, 420), V.pct(w, 660)];              // clipped, not squashed
out.pctW = V.pctW(w, 6);
out.timeAt = [V.timeAt(w, D, 0), V.timeAt(w, D, 0.5), V.timeAt(w, D, 1), V.timeAt(w, D, -0.2), V.timeAt(w, D, 1.3)];
// round trip: x of t, back to t
out.roundTrip = [480, 497.25, 555, 600].every(t => Math.abs(V.timeAt(w, D, V.pct(w, t) / 100) - t) < 1e-9);

// zoom keeps the anchor where it is on screen; never under 30 s or over D
const z = V.zoom(w, D, 60, 510);                          // 510 is 25% in
out.zoomed = z;
out.zoomAnchorFrac = (510 - z.v0) / (z.v1 - z.v0);
out.zoomMin = V.zoom(w, D, 5, 540);
out.zoomMax = V.zoom(w, D, 99999, 540);
out.zoomAtStart = V.zoom(all, D, 120, 0);
out.zoomAtEnd = V.zoom(all, D, 120, D);

// pan and centre clamp at the ends, keeping the width
out.panRight = V.pan(w, D, 96);
out.panPastEnd = V.pan(w, D, 5000);
out.panPastStart = V.pan(w, D, -5000);
out.centre = V.centre(w, D, 1000);
out.centreAtEnd = V.centre(w, D, D);

// following the playhead: no change on screen, a page (quarter in) off it
out.followInside = V.follow(w, D, 599);
out.followPast = V.follow(w, D, 600.02);
out.followBefore = V.follow(w, D, 100);
out.followEnd = V.follow(w, D, D);

// ruler ticks: the whole show keeps the old tiers and the old loop's ticks
function oldTicks(D) {
  const step = Math.max(D > 1800 ? 120 : D > 900 ? 60 : D > 300 ? 30 : 10, D / 200);
  const ts = []; for (let t = 0; t <= D; t += step) ts.push(t); return ts;
}
out.oldTiers = [D, 2128, 1800, 900, 600, 301, 300, 45, 2400].every(d =>
  JSON.stringify(V.ticks({ v0: 0, v1: d }, d)) === JSON.stringify(oldTicks(d)));
out.steps = [60, 120, 300, 600, 900, 1800].map(width => V.tickStep({ v0: 100, v1: 100 + width }, D));
out.ticks2min = V.ticks(w, D);
out.ticksOdd = V.ticks({ v0: 487.5, v1: 547.5 }, D);
out.MIN_S = V.MIN_S;

// transport's ruler scrub maps through the same window
const fakeRuler = { getBoundingClientRect: () => ({ left: 100, width: 1000 }) };
global.Audio = function () { return { addEventListener() {}, pause() {}, play() {}, removeAttribute() {}, load() {} }; };
let viewNow = w;
const tp = SIM.transport.create({ duration: () => D, isActive: () => true, onPlayingChange() {},
  onPlayhead() {}, onThumbTick() {}, toast() {}, view: () => viewNow });
const ph = [];
tp.beginDrag(fakeRuler, 600, false); ph.push(tp.playhead);    // the middle: 9:00
tp.dragMove(100); ph.push(tp.playhead);                        // left edge: 8:00
tp.dragMove(5000); ph.push(tp.playhead);                       // past the right edge: 10:00
tp.endDrag();
viewNow = null;                                                // no window: the whole show
tp.beginDrag(fakeRuler, 350, false); ph.push(tp.playhead); tp.endDrag();
const legacy = SIM.transport.create({ duration: () => D, isActive: () => true, onPlayingChange() {},
  onPlayhead() {}, onThumbTick() {}, toast() {} });             // a cfg with no view at all
legacy.beginDrag(fakeRuler, 350, false); ph.push(legacy.playhead); legacy.endDrag();
out.scrub = ph;
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def view(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    script = tmp_path_factory.mktemp("timeline_view") / "timeline_view.js"
    script.write_text(_NODE, encoding="utf-8")
    run = subprocess.run([node, str(script), str(ROOT)], capture_output=True, text=True,
                         encoding="utf-8")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_whole_show_is_the_old_mapping_exactly(view):
    assert view["identityPct"] is True and view["identityTime"] is True
    assert view["clampAll"] == {"v0": 0, "v1": 2194} and view["isAll"] is True
    assert view["oldTiers"] is True, "the unzoomed ruler must keep its old ticks"


def test_the_window_is_clamped_into_the_show_and_at_least_30_s(view):
    assert view["MIN_S"] == 30
    assert view["garbage"] == [{"v0": 0, "v1": 2194}] * 3
    assert view["minWidth"] == {"v0": 100, "v1": 130}
    assert view["pastEnd"] == {"v0": 1994, "v1": 2194}
    assert view["beforeStart"] == {"v0": 0, "v1": 120}
    assert view["tooWide"] == {"v0": 0, "v1": 2194}
    assert view["shortShow"] == {"v0": 0, "v1": 20}
    assert view["noShow"] == {"v0": 0, "v1": 0}


def test_time_and_x_map_through_the_window(view):
    assert view["pctIn"] == [0, 50, 100]
    assert view["pctOut"] == [-50, 150]           # off screen, clipped by the track
    assert view["pctW"] == pytest.approx(5)       # 6 s of a 2 min window
    # a click/scrub only ever lands on a moment that is on screen
    assert view["timeAt"] == [480, 540, 600, 480, 600]
    assert view["roundTrip"] is True


def test_zoom_keeps_the_anchor_and_respects_the_limits(view):
    assert view["zoomed"] == {"v0": 495, "v1": 555}
    assert view["zoomAnchorFrac"] == pytest.approx(0.25)
    assert view["zoomMin"]["v1"] - view["zoomMin"]["v0"] == pytest.approx(30)
    assert view["zoomMax"] == {"v0": 0, "v1": 2194}
    assert view["zoomAtStart"] == {"v0": 0, "v1": 120}
    assert view["zoomAtEnd"] == {"v0": 2074, "v1": 2194}


def test_pan_and_centre_keep_the_width_inside_the_show(view):
    assert view["panRight"] == {"v0": 576, "v1": 696}
    assert view["panPastEnd"] == {"v0": 2074, "v1": 2194}
    assert view["panPastStart"] == {"v0": 0, "v1": 120}
    assert view["centre"] == {"v0": 940, "v1": 1060}
    assert view["centreAtEnd"] == {"v0": 2074, "v1": 2194}


def test_the_playhead_pages_the_window_once_not_every_frame(view):
    assert view["followInside"] == {"v0": 480, "v1": 600}
    assert view["followPast"]["v0"] == pytest.approx(570.02)     # a quarter in
    assert view["followBefore"] == {"v0": 70, "v1": 190}
    assert view["followEnd"] == {"v0": 2074, "v1": 2194}


def test_ruler_ticks_follow_the_window(view):
    assert view["steps"] == [5, 10, 30, 60, 60, 60]
    assert view["ticks2min"] == [480, 490, 500, 510, 520, 530, 540, 550, 560, 570, 580, 590, 600]
    assert view["ticksOdd"] == [490, 495, 500, 505, 510, 515, 520, 525, 530, 535, 540, 545]


def test_the_ruler_scrub_uses_the_window(view):
    # 9:00 / 8:00 / 10:00 in the 8:00-10:00 window, then the old whole-show
    # mapping both without a window and for a cfg that has no view at all
    assert view["scrub"] == [540, 480, 600, 548.5, 548.5]


def _code(path: Path) -> str:
    """The file without its // comments, so prose about the old mapping does
    not count as a use of it."""
    return "\n".join(re.sub(r"^\s*//.*$", "", line) for line in path.read_text(encoding="utf-8").splitlines())


def test_no_timeline_conversion_is_left_on_the_whole_show_mapping():
    app = _code(SIM / "designer-app.js")
    transport = _code(SIM / "transport.js")
    # the forms the old code used: 100 * t / D, (x - left) / width * D
    for pattern in (r"\(100 \* [\w.]+ / (D|state\.show\.duration)\)", r"/ \w*[bB]ox(Width|\.width) \* D\b",
                    r"/ D\)\.toFixed", r"\(t / D\)"):
        assert not re.search(pattern, app), f"designer-app.js still maps by the whole show: {pattern}"
        assert not re.search(pattern, transport), f"transport.js still maps by the whole show: {pattern}"


def test_the_window_lives_in_the_ui_state_never_in_the_project():
    app = (SIM / "designer-app.js").read_text(encoding="utf-8")
    save = re.search(r"function saveUiPrefs\(\) \{(.*?)\n  \}", app, re.S).group(1)
    assert "tlView: ui.tlView" in save and "UI_KEY" in save
    # the project is written in exactly one place, and the window is not in it
    assert app.count("localStorage.setItem(PROJECT_KEY") == 1
    for name in ("model.js", "state.js"):
        assert "tlView" not in (SIM / name).read_text(encoding="utf-8")
