"""The panel firmware profiles (conductor/web/sim/panelfw.js, conductor/panelfw.py,
2026-11-19): what a refresh looks like on screen under FW_261119F (the pinks,
every colour finishing together) and FW_260923 (production), played from the
reference video of the real panel (tools/make_panelfw.py).

* The data: both profiles, all 16 codes, the tick tables, the palette.
* The curves: the simulator shows the normalised measurement (FW_261119F),
  FW_260923 is the same measurement re-timed in ticks, the previous-colour
  rule of the lead-in.
* The selector: FW_261119F by default on both screens, the way back to
  FW_260923 (today's orange / olive grey) and that the choice is kept.
* Nothing the units receive moves: the compiled show is the same whatever
  the profile.

The accuracy checks run the Python twin (conductor/export3d.phase_color),
which tests/test_export3d.py holds frame for frame to flicker.js under Node,
and flicker.js itself under Node where Node is installed.
"""
from __future__ import annotations

import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
from html import unescape
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from conductor import export3d, panelfw  # noqa: E402
from conductor.look import PALETTE  # noqa: E402
from conductor.server import Workspace  # noqa: E402
import tools.make_panelfw as make_panelfw  # noqa: E402

SIM = REPO / "conductor" / "web" / "sim"
INDEX_HTML = REPO / "conductor" / "web" / "index.html"
DESIGNER_HTML = REPO / "conductor" / "web" / "designer.html"
CAPTURE = json.loads(panelfw.CAPTURE.read_text(encoding="utf-8"))
BASE = [{"name": n, "rgb": list(rgb)} for n, rgb in PALETTE]

F, P = "FW_261119F", "FW_260923"
# The PM's tables (2026-11-19), as read off the waveform tables. FW_260923's
# codes 10, 12 and 15 end at their final 1-2 tick pulse (660 / 659 / 660):
# the video shows code 15 yellow until that pulse.
TABLE = {
    F: [(354, 390, 662), (364, 448, 662), (354, 426, 662), (354, 354, 652),
        (354, 368, 662), (426, 429, 654), (426, 426, 659), (426, 429, 651),
        (354, 510, 662), (354, 492, 661), (354, 566, 662), (354, 414, 662),
        (354, 438, 662), (354, 354, 661), (354, 354, 655), (354, 362, 660)],
    P: [(354, 358, 630), (364, 364, 578), (354, 354, 590), (354, 354, 652),
        (354, 354, 648), (426, 429, 654), (426, 426, 659), (426, 429, 651),
        (354, 358, 510), (354, 426, 595), (354, 362, 660), (354, 354, 602),
        (354, 362, 659), (354, 354, 661), (354, 354, 655), (354, 362, 660)],
}


def _rgb(hex_color):
    return [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]


def _diff(a, b):
    return max(abs(x - y) for x, y in zip(_rgb(a), _rgb(b)))


def _measured(code_f):
    """FW_261119F's normalised recording of code_f, as make_panelfw makes
    it (recorded curve swapped for the pinks)."""
    fw = panelfw.profile(F)
    key = fw["recorded"].get(str(code_f), str(code_f))
    target = panelfw.palette_of(BASE, F)[code_f]["rgb"]
    return make_panelfw.normalised(CAPTURE, key, fw["timeline"][code_f], target)


# ---------------------------------------------------------------- the data

def test_both_profiles_all_16_codes_and_the_default():
    assert panelfw.IDS == [F, P] and panelfw.DEFAULT == F
    for fw in panelfw.IDS:
        p = panelfw.profile(fw)
        assert len(p["timeline"]) == 16 and len(p["curves"]) == 16
        for code, (shake_end, drive_start, drive_end) in enumerate(p["timeline"]):
            assert 122 <= shake_end <= drive_start <= drive_end <= 662, (fw, code)
        assert [tuple(t) for t in p["timeline"]] == TABLE[fw]
    assert all(651 <= t[2] <= 662 for t in panelfw.profile(F)["timeline"])
    # An unknown or absent choice is the default, never an error.
    assert panelfw.profile(None)["id"] == F and panelfw.profile("FW_000000")["id"] == F
    assert panelfw.DATA["tick_s"] == CAPTURE["tick_s"] == 0.011
    assert panelfw.DATA["wave_ticks"] == 662 and panelfw.DATA["lead_in_ticks"] == 122


def test_the_pinks_belong_to_fw_261119f_and_the_way_back_is_todays_palette():
    pinks = panelfw.palette_of(BASE, F)
    assert pinks[10] == {"name": "Pink Purple", "rgb": [204, 162, 163], "estimate": True}
    assert pinks[12] == {"name": "Pink Red", "rgb": [170, 125, 128], "estimate": True}
    assert [p for c, p in enumerate(pinks) if c not in (10, 12)] == \
        [p for c, p in enumerate(BASE) if c not in (10, 12)]
    back = panelfw.palette_of(BASE, P)
    assert back == BASE
    assert (back[10]["name"], back[12]["name"]) == ("Orange", "Olive Gray")
    assert panelfw.palette_of(BASE) == pinks           # the default
    assert BASE[10]["name"] == "Orange"                # base untouched


def test_the_generated_curves_are_current_and_well_formed():
    assert make_panelfw.main(["--check"]) == 0
    for fw in panelfw.IDS:
        p = panelfw.profile(fw)
        for code in range(16):
            curve = p["curves"][str(code)]
            assert curve["end"] == p["timeline"][code][2]
            ticks = [k[0] for k in curve["keys"]]
            assert ticks[0] == 0 and ticks == sorted(set(ticks)) and ticks[-1] < curve["end"]
            assert all(re.fullmatch(r"#[0-9a-f]{6}", k[1]) for k in curve["keys"])
    assert panelfw.DATA["white"] == "#89adc3"          # look.py's white


def test_the_page_and_the_simulator_load_the_one_file():
    data_js = (SIM / "panelfw.js").read_text(encoding="utf-8")
    # One source: nobody else holds a copy of the tick tables or the curves.
    for name in ("index.html", "designer.html"):
        text = (REPO / "conductor" / "web" / name).read_text(encoding="utf-8")
        assert "[354, 390, 662]" not in text and '"#89adc3"],' not in text, name
    for js in SIM.glob("*.js"):
        if js.name != "panelfw.js":
            assert "[354, 390, 662]" not in js.read_text(encoding="utf-8"), js.name
    designer = DESIGNER_HTML.read_text(encoding="utf-8")
    assert designer.index('src="sim/panelfw.js"') < designer.index('src="sim/flicker.js"')
    assert "PANEL_FW_JSON_BEGIN" in data_js and "PANEL_FW_CURVES_BEGIN" in data_js
    # The Conductor's page gets it in its state, next to the palette.
    state = Workspace(Path(REPO / "tests" / "fixtures" / "sim")).state() \
        if False else None
    assert state is None


def test_the_conductor_sends_the_profiles_in_its_state(tmp_path):
    state = Workspace(tmp_path / "ws").state()
    assert state["panel_fw"]["default"] == F
    assert [p["id"] for p in state["panel_fw"]["profiles"]] == [F, P]
    assert state["palette"][10]["name"] == "Orange"    # the base, FW_260923's chart


# --------------------------------------------------------------- the curves

def _sim(fw_id, tick, code, prev=None):
    fw = panelfw.profile(fw_id)
    return export3d.phase_color(tick, code, prev,
                                export3d._Palette(panelfw.palette_of(BASE, fw_id)), fw)


def test_fw_261119f_plays_the_measurement_every_quarter_second():
    """White -> code: at every 0.25 s of the waveform (11 ms ticks) the
    simulator shows the white-balanced measured colour in force then, within
    1 per channel (rounding), and the palette colour from drive_end on."""
    palette = panelfw.palette_of(BASE, F)
    for code in range(16):
        drive_end = TABLE[F][code][2]
        measured = _measured(code)
        for k in range(1, 31):
            tick = k * 0.25 / 0.011
            shown = _sim(F, tick, code)
            if tick >= drive_end:
                assert shown == export3d._hex(palette[code]["rgb"]), (code, k)
                continue
            expect = make_panelfw._hex(make_panelfw.colour_at(measured, tick))
            assert _diff(shown, expect) <= 1, (code, k, shown, expect)


def test_each_curve_starts_white_and_ends_on_its_palette_colour():
    for fw_id in panelfw.IDS:
        palette = panelfw.palette_of(BASE, fw_id)
        # Codes whose step 0 starts at level 0 are still white at tick 0 (a
        # level-6 one is already navy in the frame the refresh starts in).
        for code in (1, 3, 4, 7, 8, 10, 12, 14):
            assert _diff(_sim(fw_id, 0, code), "#89adc3") <= 3, (fw_id, code)
        for code in range(16):
            end = TABLE[fw_id][code][2]
            assert _sim(fw_id, end, code) == export3d._hex(palette[code]["rgb"])
    # The recordings are white-balanced on their own pre-refresh white, so
    # the measured lead-in's level-6 navy is really dark and its level-1
    # yellow really yellow (the user's reading of the video).
    navy = _rgb(_sim(F, 60, 11))               # code 11: level 6 for 92 ticks
    assert sum(navy) < 250, navy
    yellow = _rgb(_sim(F, 118, 7))             # code 7: level 1 from tick 42
    assert yellow[2] < 120, yellow              # the blue channel gone: yellow


def test_fw_261119f_finishes_every_colour_together():
    ends = [_first_settled(F, code) for code in range(16)]
    assert max(ends) - min(ends) <= 11, ends   # 651..662 ticks
    old = [_first_settled(P, code) for code in range(16)]
    assert max(old) - min(old) > 100, old      # FW_260923: 510..661


def _first_settled(fw_id, code):
    palette = panelfw.palette_of(BASE, fw_id)
    target = export3d._hex(palette[code]["rgb"])
    return next(t for t in range(663) if all(_sim(fw_id, u, code) == target
                                             for u in range(t, 663)))


def test_fw_260923_is_the_measurement_retimed_in_ticks():
    for code in range(16):
        if code in (10, 12):
            continue
        _, f_start, f_end = TABLE[F][code]
        shake_end, p_start, p_end = TABLE[P][code]
        assert p_end - p_start == f_end - f_start
        for k in range(0, 662 * 4):
            tick = k / 4 + 0.13
            if tick < p_start:
                assert _sim(P, tick, code) == _sim(F, tick, code), (code, tick)
            elif tick < p_end:
                assert _sim(P, tick, code) == _sim(F, tick - p_start + f_start, code), (code, tick)
            else:
                assert _sim(P, tick, code) == _sim(P, p_end, code)


def test_fw_260923s_orange_and_olive_grey_are_synthesised():
    fw = panelfw.profile(P)
    assert fw["synth"] == {"10": {"from": 4, "onset_from": 67, "onset": 112, "drive": 15, "hold": 602},
                           "12": {"from": 15, "drive": 15, "hold": 602}}
    # 0x0A: white until its own step 0 turns level 1 at tick 112, yellow
    # after; 0x0C: level 6 from tick 0, navy all through the lead-in.
    assert _diff(_sim(P, 100, 10), "#89adc3") <= 12
    assert _rgb(_sim(P, 121, 10))[2] < 100
    assert sum(_rgb(_sim(P, 60, 12))) < 200
    # The shake is the borrowed code's; the drive is code 15's measured one
    # pulled towards the target: unchanged at its start, shifted by the
    # whole (target - smoke blue) from 602, held through the rest, and the
    # palette colour only at the final pulse.
    assert _sim(P, 200, 10) == _sim(F, 200, 4) and _sim(P, 200, 12) == _sim(F, 200, 15)
    for code, end in ((10, 660), (12, 659)):
        pull = [t - s for t, s in zip(BASE[code]["rgb"], BASE[15]["rgb"])]
        assert _sim(P, 362.5, code) == _sim(F, 362.5, 15)
        held = _rgb(_sim(P, 602, code))
        expect = [max(0, min(255, a + b)) for a, b in zip(_rgb(_sim(F, 602, 15)), pull)]
        assert max(abs(x - y) for x, y in zip(held, expect)) <= 1, (code, held, expect)
        assert all(_sim(P, t, code) == _sim(P, 602, code) for t in range(602, end))
        assert _sim(P, end - 0.01, code) != export3d._hex(BASE[code]["rgb"])
        assert _sim(P, end, code) == export3d._hex(BASE[code]["rgb"])


def test_a_scale_that_was_not_white_keeps_its_colour_through_the_lead_in():
    """The video only has refreshes from white: the lead-in is the previous
    colour plus the measured change from white, crossing into the measured
    curve over the first 10 ticks of the shake (assumption, 2026-11-19)."""
    red = export3d._hex(BASE[3]["rgb"])        # 0x03 Red #72473b
    white = _rgb("#89adc3")
    for tick, code in ((50, 0), (50, 11), (110, 7), (5, 3)):
        measured = _rgb(_sim(F, tick, code))
        expect = [max(0, min(255, r + m - w)) for r, m, w in zip(_rgb(red), measured, white)]
        assert _rgb(_sim(F, tick, code, red)) == expect, (tick, code)
    # A white previous colour IS the measurement.
    assert _sim(F, 50, 0, "#89adc3") == _sim(F, 50, 0)
    # Halfway through the cross: half of each.
    tick = 127.5                               # step 5 of 10
    measured = _rgb(_sim(F, tick, 0))
    lead = [max(0, min(255, r + m - w)) for r, m, w in zip(_rgb(red), measured, white)]
    half = [export3d._js_round(a * 0.5 + b * 0.5) for a, b in zip(lead, measured)]
    assert _rgb(_sim(F, tick, 0, red)) == half
    # From the end of the cross the shake has reset the panel.
    for tick in (132, 200, 400, 600):
        assert _sim(F, tick, 0, red) == _sim(F, tick, 0)


# ------------------------------------------------ flicker.js itself (Node)

NODE_PROBE = r"""
require(%(model)s);
require(%(panelfw)s);
require(%(flicker)s);
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = input.cases.map(([fw, tick, code, prev]) => {
  const p = SIM.panelFw.profile(fw);
  const palette = SIM.panelFw.paletteOf(input.base, fw);
  const c = SIM.flicker.refreshPhaseColor(tick, code, prev === null ? undefined : prev, palette, p);
  return typeof c === "number"
    ? "#" + palette[c].rgb.map(v => Math.round(v).toString(16).padStart(2, "0")).join("") : c;
});
process.stdout.write(JSON.stringify({ out, default: SIM.panelFw.profile("").id,
  back: SIM.panelFw.paletteOf(input.base, "FW_260923")[10].name,
  pinks: SIM.panelFw.paletteOf(input.base, "")[10].name }));
"""


def test_flicker_js_shows_what_the_python_twin_shows(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    cases = []
    for fw in panelfw.IDS:
        for code in range(16):
            for tick in (0, 30.5, 99, 121.9, 122, 125.2, 131.99, 200, 360, 450, 600, 650, 661.9, 662):
                for prev in (None, "#72473b"):
                    cases.append([fw, tick, code, prev])
    script = tmp_path / "probe.js"
    script.write_text(NODE_PROBE % {k: json.dumps(str(SIM / f"{k}.js"))
                                    for k in ("model", "panelfw", "flicker")}, encoding="utf-8")
    run = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8",
                         input=json.dumps({"cases": cases, "base": BASE}), check=True)
    got = json.loads(run.stdout)
    assert got["default"] == F and got["back"] == "Orange" and got["pinks"] == "Pink Purple"
    for (fw, tick, code, prev), shown in zip(cases, got["out"]):
        assert shown == _sim(fw, tick, code, prev), (fw, tick, code, prev)


# ---------------------------------------- nothing the units receive moves

def test_the_compiled_show_is_the_same_whatever_the_profile(tmp_path):
    """The profile is a view setting: nothing on the way to the units reads
    it. No compiler module (showfile, timeline, sequence, fleet, look)
    imports conductor/panelfw, and a compiled show is the same before and
    after 3D exports under both profiles."""
    from tests.test_look import GRID, MAP
    for module in ("showfile", "timeline", "sequence", "fleet", "look"):
        text = (REPO / "conductor" / f"{module}.py").read_text(encoding="utf-8")
        assert "panelfw" not in text, module
    ws = Workspace(tmp_path / "ws")
    ws.save("Look20-Top_map.csv", MAP)
    ws.save("Look20-Top_color_pattern01_grid.csv", GRID)
    ws.assign("Look20-Top", "radxa-02")
    ws.set_timeline(60, [{"id": "a", "item": "Look20-Top", "at": 0,
                          "design": "Look20-Top_color_pattern01_grid.csv"}])
    shows, problems = ws.compile_show()
    assert problems == [] and shows["radxa-02"]["cues"]
    before = json.dumps(shows, sort_keys=True)
    export3d.resolve(ws.state(), fw=P)
    export3d.resolve(ws.state(), fw=F)
    assert json.dumps(ws.compile_show()[0], sort_keys=True) == before


# ------------------------------------------------- the selector, in a browser

def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_PAGE_PROBE = """
<script>
(function () {
  var out = { error: null };
  var wait = ms => new Promise(r => setTimeout(r, ms));
  (async function () {
    try {
      await wait(600);
      var sel = document.querySelector("#panel-fw");
      out.first = { value: sel.value, options: Array.from(sel.options).map(o => o.value),
                    disabled: sel.disabled, fw: panelFw().id, name10: state.palette[10].name,
                    stored: localStorage.getItem("sim.panelFw") };
      if (sel.value !== "FW_260923") {
        sel.value = "FW_260923";
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        await wait(200);
        out.switched = { fw: panelFw().id, name10: state.palette[10].name,
                         name12: state.palette[12].name, end0: panelFw().curves[0].end,
                         stored: localStorage.getItem("sim.panelFw"), reread: loadPanelFwPref() };
      }
    } catch (e) { out.error = String((e && e.stack) || e); }
    var pre = document.createElement("pre");
    pre.id = "fw-out";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  })();
})();
</script>
"""


def _page_run(tmp_path, ws):
    from tests.test_designer_build import _dump_dom
    page = INDEX_HTML.read_text(encoding="utf-8").replace("</body>", _PAGE_PROBE + "</body>", 1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                body, kind = page.encode("utf-8"), "text/html; charset=utf-8"
            else:
                obj = ws.state() if path == "/api/state" else (
                    {"units": [], "last_fire": None, "run": None, "shows": {}, "corrections": [],
                     "prepared": {}, "start_at": 0.0, "show_duration": 600.0, "timeline": None}
                    if path == "/api/fleet" else {})
                body, kind = json.dumps(obj).encode("utf-8"), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        dom = _dump_dom(f"http://127.0.0.1:{port}/", tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()
    match = re.search(r'<pre id="fw-out">(.*?)</pre>', dom or "", re.S)
    assert match, f"no #fw-out in the dumped DOM:\n{(dom or '')[:2000]}"
    data = json.loads(unescape(match.group(1)))
    assert data["error"] is None, data["error"]
    return data


def test_the_conductor_page_defaults_to_fw_261119f_and_keeps_the_way_back(tmp_path):
    from tests.test_designer_build import _require_browser
    _require_browser(tmp_path)
    from tests.test_look import GRID, MAP
    ws = Workspace(tmp_path / "ws")
    ws.save("Look22_map.csv", MAP)
    ws.save("Look22_color_pattern01_grid.csv", GRID)
    first = _page_run(tmp_path, ws)
    assert first["first"] == {"value": F, "options": [F, P], "disabled": False, "fw": F,
                              "name10": "Pink Purple", "stored": None}
    # Kept in localStorage, where the page reads its choice back at load
    # (a second headless load does not see it: --dump-dom quits before the
    # profile's storage is flushed).
    assert first["switched"] == {"fw": P, "name10": "Orange", "name12": "Olive Gray",
                                 "end0": 630, "stored": P, "reread": P}


_DESIGNER_PROBE = """
<script>
window.addEventListener("load", function () {
  setTimeout(function () {
    var out = { error: null };
    try {
      var sel = document.querySelector("#panel-fw");
      var st = SIM.app.getState();
      out.first = { value: sel.value, options: Array.from(sel.options).map(o => o.value),
                    name10: st.palette[10].name };
      sel.value = "FW_260923";
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      st = SIM.app.getState();
      out.switched = { name10: st.palette[10].name, name12: st.palette[12].name,
                       stored: JSON.parse(localStorage.getItem("az27ss.ui.v1")).panelFw };
    } catch (e) { out.error = String((e && e.stack) || e); }
    var pre = document.createElement("pre");
    pre.id = "fw-out";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }, 300);
});
</script>
"""


def test_the_designer_simulator_defaults_to_fw_261119f_and_goes_back(tmp_path):
    from tests.test_designer_build import _dump_dom, _require_browser
    _require_browser(tmp_path)
    base = (REPO / "conductor" / "web").resolve().as_uri() + "/"
    html = DESIGNER_HTML.read_text(encoding="utf-8")
    html = html.replace('src="sim/', f'src="{base}sim/').replace('href="sim/', f'href="{base}sim/')
    page = tmp_path / "designer.html"
    page.write_text(html.replace("</body>", _DESIGNER_PROBE + "</body>", 1), encoding="utf-8")
    dom = _dump_dom(page.resolve().as_uri(), tmp_path)
    match = re.search(r'<pre id="fw-out">(.*?)</pre>', dom or "", re.S)
    assert match, f"no #fw-out in the dumped DOM:\n{(dom or '')[:2000]}"
    data = json.loads(unescape(match.group(1)))
    assert data["error"] is None, data["error"]
    assert data["first"] == {"value": F, "options": [F, P], "name10": "Pink Purple"}
    assert data["switched"] == {"name10": "Orange", "name12": "Olive Gray", "stored": P}


_DIST_PROBE = """
<script>
window.addEventListener("load", function () {
  setTimeout(function () {
    var out = { error: null };
    try {
      var sel = document.querySelector("#panel-fw");
      var st = SIM.app.getState();
      var item = st.items.find(i => st.show.cues.some(c => c.item === i.item));
      var cue = st.show.cues.filter(c => c.item === item.item).sort((a, b) => a.sent - b.sent)[0];
      function worn(fw) {
        var w = SIM.flicker.wornAt(item, cue.sent + 3.0, { flicker: true, palette: SIM.panelFw.paletteOf(st.basePalette, fw),
          refreshS: st.show.refresh_s, fw: fw, cuesOf: () => st.show.cues.filter(c => c.item === item.item) });
        return Object.values(w.colors).filter(v => typeof v === "string");
      }
      var known = {};
      ["FW_261119F", "FW_260923"].forEach(fw => {
        known[fw] = {};
        Object.values(SIM.panelFw.curves[fw]).forEach(c => c.keys.forEach(k => { known[fw][k[1]] = 1; }));
      });
      var during = worn("");
      out.first = { value: sel.value, options: Array.from(sel.options).map(o => o.value),
                    name10: st.palette[10].name, drawing: during.length,
                    measured: during.filter(h => known.FW_261119F[h]).length };
      sel.value = "FW_260923";
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      st = SIM.app.getState();
      var old = worn("FW_260923");
      out.switched = { name10: st.palette[10].name, drawing: old.length,
                       measured: old.filter(h => known.FW_260923[h]).length,
                       label: sel.options[sel.selectedIndex].textContent };
    } catch (e) { out.error = String((e && e.stack) || e); }
    var pre = document.createElement("pre");
    pre.id = "fw-out";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }, 500);
});
</script>
"""


def test_the_shipped_single_file_simulator_has_the_selector_and_the_curves(tmp_path):
    """dist/az27ss-simulator.html, the designers' double-click: the
    selector, FW_261119F by default, a refresh 3 s in drawn from the
    measured curves, and the way back to FW_260923."""
    from tests.test_designer_build import DIST, _dump_dom, _require_browser
    _require_browser(tmp_path)
    page = tmp_path / "dist.html"
    page.write_text(DIST.read_text(encoding="utf-8").replace("</body>", _DIST_PROBE + "</body>", 1),
                    encoding="utf-8")
    dom = _dump_dom(page.resolve().as_uri(), tmp_path)
    match = re.search(r'<pre id="fw-out">(.*?)</pre>', dom or "", re.S)
    assert match, f"no #fw-out in the dumped DOM:\n{(dom or '')[:2000]}"
    data = json.loads(unescape(match.group(1)))
    assert data["error"] is None, data["error"]
    first, back = data["first"], data["switched"]
    assert first["value"] == F and first["options"] == [F, P] and first["name10"] == "Pink Purple"
    assert first["drawing"] > 0 and first["measured"] == first["drawing"]
    assert back["name10"] == "Orange" and back["label"] == "FW_260923 (production)"
    assert back["drawing"] > 0 and back["measured"] == back["drawing"]


def test_a_show_spaced_for_the_old_7_s_repaint_gets_the_standard_refusal(tmp_path):
    """PANEL_REPAINT_S went 7.0 -> 7.3 (2026-11-19). A show whose two 20 s
    sweeps were spaced exactly 7.0 + 20 + 1 = 28.0 s apart (showdata's 2:08
    and 3:40 cues) is now short by 0.3 s: it is refused with the ordinary
    message naming the cue, the unit and the interval it needs, both on the
    cue (the Timeline row) and from the compile (Upload)."""
    from tests.test_look import GRID, MAP
    ws = Workspace(tmp_path / "ws")
    ws.save("Look20-Top_map.csv", MAP)
    ws.save("Look20-Top_color_pattern01_grid.csv", GRID)
    ws.assign("Look20-Top", "radxa-09")
    sweep = {"transition": "custom", "sequence": "top_down", "span_s": 20}
    ws.set_timeline(300, [
        dict({"id": "a", "item": "Look20-Top", "at": 100,
              "design": "Look20-Top_color_pattern01_grid.csv"}, **sweep),
        dict({"id": "b", "item": "Look20-Top", "at": 128,
              "design": "Look20-Top_color_pattern01_grid.csv"}, **sweep)])
    text = ("only 28.0 s after the previous send on radxa-09; at least 28.3 s is "
            "needed (7.3 s panel repaint + 20.0 s sweep + 1.0 s gap)")
    cue = next(c for c in ws.state()["show"]["cues"] if c["id"] == "b")
    assert cue["problems"] == [text]
    shows, problems = ws.compile_show()
    assert problems == ["2:08 Look20-Top: " + text] and shows == {}


@pytest.mark.parametrize("second, refused", [(124.4, False), (124.3, True)])
def test_the_compiler_takes_a_cue_exactly_at_the_shown_minimum(tmp_path, second, refused):
    """Upload's own check (Workspace.compile_show -> showfile.build, which
    reports timeline.validate's problems): a 16.1 s sweep at 100.0 needs
    24.4 s, and a cue at 124.4 is accepted - 7.3 + 16.1 + 1.0 is
    24.400000000000002 in floating point, which used to refuse it."""
    from tests.test_look import GRID, MAP
    ws = Workspace(tmp_path / "ws")
    ws.save("Look20-Top_map.csv", MAP)
    ws.save("Look20-Top_color_pattern01_grid.csv", GRID)
    ws.assign("Look20-Top", "radxa-09")
    grid = "Look20-Top_color_pattern01_grid.csv"
    ws.set_timeline(300, [
        {"id": "p", "item": "Look20-Top", "at": 0, "design": grid},
        {"id": "a", "item": "Look20-Top", "at": 100, "design": grid,
         "transition": "custom", "sequence": "top_down", "span_s": 16.1},
        {"id": "b", "item": "Look20-Top", "at": second, "design": grid}])
    assert ws.state()["show"]["min_interval"]["radxa-09"] == 24.4
    shows, problems = ws.compile_show()
    if refused:
        assert problems == ["2:04 Look20-Top: only 24.3 s after the previous send on "
                            "radxa-09; at least 24.4 s is needed (7.3 s panel repaint "
                            "+ 16.1 s sweep + 1.0 s gap)"]
    else:
        assert problems == [] and shows["radxa-09"]["cues"]


# ------------------------------------------- the generator and a broken file

def test_the_generator_follows_the_hand_block_it_is_given():
    """build() is a pure function of (capture, hand block, look.PALETTE): a
    pink edited in the hand block moves that code's curve towards it, in
    the same process (it used to read the palette from conductor/panelfw's
    import-time copy)."""
    text = panelfw.SOURCE.read_text(encoding="utf-8")
    hand = panelfw.read_block(text, panelfw._HAND)
    before = make_panelfw.build(CAPTURE, hand)
    fast = next(p for p in hand["profiles"] if p["id"] == F)
    fast["palette"]["10"]["rgb"] = [250, 20, 200]
    after = make_panelfw.build(CAPTURE, hand)
    old_end, new_end = before[F]["10"]["keys"][-1][1], after[F]["10"]["keys"][-1][1]
    assert old_end != new_end
    assert _diff(new_end, "#fa14c8") < _diff(old_end, "#fa14c8")
    assert before[F]["12"] == after[F]["12"] and before[P] == after[P]


def test_the_generator_rebuilds_a_broken_generated_block(tmp_path):
    text = panelfw.SOURCE.read_text(encoding="utf-8")
    broken = panelfw.block_pattern(panelfw._CURVES).sub(
        "/* PANEL_FW_CURVES_BEGIN */\n{ not json\n/* PANEL_FW_CURVES_END */", text, count=1)
    with pytest.raises(ValueError):
        panelfw.read_block(broken, panelfw._CURVES)
    _, rebuilt = make_panelfw.updated_source(broken)
    assert rebuilt == text
    no_markers = broken.replace("PANEL_FW_CURVES_BEGIN", "X")
    with pytest.raises(SystemExit):
        make_panelfw.updated_source(no_markers)


def test_a_missing_or_broken_profile_file_does_not_stop_the_conductor(tmp_path, monkeypatch, capsys):
    """conductor/panelfw.py is read when the Conductor starts: a missing or
    broken panelfw.js costs one warning, /api/state carries no panel_fw
    (the page's own fallback), and the 3D export still exports."""
    monkeypatch.setattr(panelfw, "SOURCE", tmp_path / "missing.js")
    assert panelfw._load_soft() is None
    assert "panel firmware profiles not loaded" in capsys.readouterr().err
    monkeypatch.setattr(panelfw, "DATA", None)
    assert panelfw.profile(F) is panelfw.FALLBACK and panelfw.palette_of(BASE, F) == BASE
    from tests.test_look import GRID, MAP
    ws = Workspace(tmp_path / "ws")
    ws.save("Look22_map.csv", MAP)
    ws.save("Look22_color_pattern01_grid.csv", GRID)
    ws.set_timeline(60, [{"id": "a", "item": "Look22", "at": 10,
                          "design": "Look22_color_pattern01_grid.csv"}])
    state = ws.state()
    assert "panel_fw" not in state
    out = export3d.resolve(state, fw=F)
    assert out["panel_firmware"]["id"] == "" and out["items"]["Look22"]["keys"]


def test_the_page_falls_back_when_the_conductor_sends_no_profiles(tmp_path, monkeypatch):
    from tests.test_designer_build import _require_browser
    _require_browser(tmp_path)
    from tests.test_look import GRID, MAP
    monkeypatch.setattr(panelfw, "DATA", None)
    ws = Workspace(tmp_path / "ws")
    ws.save("Look22_map.csv", MAP)
    ws.save("Look22_color_pattern01_grid.csv", GRID)
    first = _page_run(tmp_path, ws)["first"]
    assert first["disabled"] and first["fw"] == "" and first["options"] == [""]
    assert first["name10"] == "Orange"
