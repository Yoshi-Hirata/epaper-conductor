"""Export for 3D simulator (conductor/export3d.py, GET /api/show/export3d).

The keys must be the frames the Conductor's own Timeline preview shows,
flicker included: the page's model (conductor/web/sim/flicker.js) is run
under Node on the same show and every scale is compared, sampled every
50 ms through a preset, a centre-outward sweep, a top-down sweep with a
short refresh and a partial cue. Skipped where Node is not installed.
"""

from __future__ import annotations

import bisect
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from conductor import export3d
from conductor.server import Workspace, make_server

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "sim"
ITEM = "AZ271SD1301"
DESIGN = f"{ITEM}_1_HW.csv"
HALF = f"{ITEM}_2_HW.csv"           # the top half of DESIGN's rows only

NODE_WORN_AT = r"""
// model.js first: flicker.js takes its hash32 from SIM.sequence (2026-10-22,
// one implementation), then the stubbed timeline over model.js's own.
require(%(model)s);
globalThis.SIM.timeline = { PANEL_REPAINT_S: %(repaint)s };
require(%(flicker)s);
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const hex = c => typeof c === "number"
  ? "#" + input.palette[c].rgb.map(v => Math.round(v).toString(16).padStart(2, "0")).join("")
  : c;
const out = input.times.map(t => {
  const worn = SIM.flicker.wornAt(input.item, t, {
    cuesOf: () => input.cues, palette: input.palette, refreshS: input.refreshS });
  const colors = {};
  for (const [k, v] of Object.entries(worn.colors)) colors[k] = hex(v);
  return colors;
});
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.save(f"{ITEM}_map.csv", (FIX / f"{ITEM}_map.csv").read_text(encoding="utf-8"))
    full = (FIX / DESIGN).read_text(encoding="utf-8")
    ws.save(DESIGN, full)
    lines = full.splitlines()
    ws.save(HALF, "\n".join(lines[:1 + (len(lines) - 1) // 2]) + "\n")
    ws.set_timeline(120, [
        {"id": "p", "item": ITEM, "at": 0, "design": DESIGN},
        {"id": "c", "item": ITEM, "at": 20, "design": DESIGN,
         "transition": "custom", "sequence": "center", "span_s": 3},
        {"id": "t", "item": ITEM, "at": 40, "design": DESIGN, "refresh_s": 5,
         "transition": "custom", "sequence": "top_down", "span_s": 2},
        # A partial overlay, a full design lacking half the scales sent
        # while the overlay is still repainting, then the whole design back.
        {"id": "x", "item": ITEM, "at": 60, "design": HALF, "partial": True},
        {"id": "o", "item": ITEM, "at": 63, "design": HALF},
        {"id": "z", "item": ITEM, "at": 80, "design": DESIGN,
         "transition": "custom", "sequence": "bottom_up", "span_s": 2}])
    return ws


def color_at(track, t):
    """The exported key in force at t (constant interpolation)."""
    i = bisect.bisect_right([k[0] for k in track], t) - 1
    return track[i][1] if i >= 0 else None


def test_keys_match_the_timeline_preview_frame_for_frame(workspace, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    state = workspace.state()
    out = export3d.resolve(state)
    item = next(i for i in state["items"] if i["item"] == ITEM)
    cues = sorted((c for c in state["show"]["cues"] if c["item"] == ITEM),
                  key=lambda c: c["sent"])
    times = [round(-12 + k * 0.05, 4) + 0.0123 for k in range(int(110 / 0.05))]
    script = tmp_path / "worn.js"
    script.write_text(NODE_WORN_AT % {
        "repaint": json.dumps(export3d.timeline.PANEL_REPAINT_S),
        "model": json.dumps(str(ROOT / "conductor" / "web" / "sim" / "model.js")),
        "flicker": json.dumps(str(ROOT / "conductor" / "web" / "sim" / "flicker.js"))},
        encoding="utf-8")
    run = subprocess.run([node, str(script)], capture_output=True, text=True,
                         input=json.dumps({"item": item, "cues": cues,
                                           "palette": state["palette"],
                                           "refreshS": state["show"]["refresh_s"],
                                           "times": times}),
                         encoding="utf-8", check=True)
    page = json.loads(run.stdout)
    exported = out["items"][ITEM]
    table = out["colors"]
    changing = 0
    for scale in exported["scales"]:
        key = f"{scale['side']}|{scale['row']}|{scale['col']}"
        track = exported["keys"][scale["label"]]
        instants = [k[0] for k in track]
        for t, frame in zip(times, page):
            # Key times are written to the millisecond; a sample within that
            # rounding of a key may fall either side of it.
            i = bisect.bisect_left(instants, t)
            if any(abs(instants[j] - t) <= 0.0005 for j in (i - 1, i)
                   if 0 <= j < len(instants)):
                continue
            index = color_at(track, t)
            assert (table[index] if index is not None else None) == frame.get(key), (
                f"{scale['label']} at {t}")
        changing += len(track)
    assert changing > 20 * len(exported["scales"])     # the flicker is in there
    # Half the scales lose their colour at "o" and get it back at "z".
    blank = [label for label, track in exported["keys"].items()
             if any(index is None for _, index in track)]
    assert 0 < len(blank) < len(exported["scales"])
    for track in exported["keys"].values():
        times_ = [k[0] for k in track]
        assert times_ == sorted(set(times_))               # strictly increasing


def test_the_file_names_scales_by_their_map_label_and_lists_the_cues(workspace):
    out = export3d.resolve(workspace.state(), now=0)
    assert out["format"] == "epaper-3d-timeline" and out["version"] == 1
    assert out["interpolation"] == "constant"
    item = out["items"][ITEM]
    labels = [s["label"] for s in item["scales"]]
    assert labels[0] == "092-14" and set(item["keys"]) == set(labels)
    assert [c["id"] for c in item["cues"]] == ["p", "c", "t", "x", "o", "z"]
    assert item["cues"][3] == {"id": "x", "design": HALF, "label": "2",
                               "at": 60.0, "partial": True}
    assert len(out["palette"]) == 16 and all(
        c.startswith("#") and len(c) == 7 for c in out["colors"])
    # Results only: nothing that names or times the rules travels - no
    # ranks, delays, sweep names, spans, refreshes or send/complete times.
    text = json.dumps(out)
    for word in ("sequence", "delay", "rank", "span", "refresh", "sweep",
                 "center", "top_down", "complete", "sent", "jitter"):
        assert word not in text, word


def test_the_route_downloads_the_file(tmp_path):
    server = make_server(tmp_path, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/api/show/export3d", timeout=10) as r:
            assert "attachment" in r.headers["Content-Disposition"]
            assert "-3d-" in r.headers["Content-Disposition"]
            body = json.loads(r.read().decode("utf-8"))
        assert body["format"] == "epaper-3d-timeline" and body["items"] == {}
    finally:
        server.shutdown()
        server.server_close()
