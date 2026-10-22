"""The designers' simulator: the sweep's randomised edge (2026-10-22) - the
same Edge / Width / New pattern controls as the Conductor, carried through
the project, the Save project... bundle and the preview.

One headless run of the committed dist/az27ss-simulator.html with a probe
appended (never shipped), like tests/test_designer_music_end.py. The ranks
themselves are checked bit for bit against conductor/sequence.py by the
"edge_ranks" golden cases (tests/test_sim_goldens.py's browser self-test);
this file is about the page: the controls, what they store, and that the
preview plays the edge ranks. Behind CONDUCTOR_BROWSER_TESTS=1 like every
browser test here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_designer_build import DIST, _probe_page, _require_browser  # noqa: E402

_PROBE = """
<script>
(function () {
  var out = { error: null };
  function publish() {
    var pre = document.createElement("pre");
    pre.id = "simedge-out";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function q(sel) { return document.querySelector(sel); }
  function ready() {
    try { var st = SIM.app.getState(); return !!(st && st.items && st.items.length); }
    catch (e) { return false; }
  }
  function projectCue(id) { return SIM.app.getProject().show.cues.find(function (c) { return String(c.id) === String(id); }); }
  function stateCue(id) { return SIM.app.getState().show.cues.find(function (c) { return String(c.id) === String(id); }); }
  function edgeFields(c) {
    return { edge: c.edge, edge_rows: c.edge_rows, edge_random: c.edge_random,
             edge_min_rows: c.edge_min_rows, edge_seed: c.edge_seed };
  }
  function visible(el) { return !!el && el.offsetParent !== null; }
  function cueCtl() { return q("#cue-editor-body .edge-ctl[data-cue-edge]"); }
  function change(el, value) { el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); }
  (async function () {
    try {
      for (var i = 0; i < 200 && !ready(); i++) await wait(50);
      var st = SIM.app.getState();
      var item = st.items.find(function (it) { return it.map && it.map.scales.length > 20 && it.designs.length; });
      var design = item.designs[0].name;
      out.scales = item.map.scales.length;

      // ---- a cue's own transition ----
      var id = SIM.app.addCue({ item: item.item, at: 590, design: design });
      out.newCueKeys = Object.keys(projectCue(id));
      out.newCue = edgeFields(stateCue(id));
      // a second, plain cue: no edge keys in the project or the bundle, and an
      // edge set and taken back again leaves none behind (2026-10-23)
      var plainId = SIM.app.addCue({ item: item.item, at: 600, design: design });
      SIM.app.updateCue(plainId, { transition: "custom", sequence: "top_down", span_s: 2, edge: "wave" });
      out.plainWaveKeys = Object.keys(projectCue(plainId));
      SIM.app.updateCue(plainId, { edge: "straight" });
      out.plainBackKeys = Object.keys(projectCue(plainId));
      SIM.app.updateCue(plainId, { transition: "design", sequence: "natural", span_s: 0 });
      out.plainBundleKeys = Object.keys(SIM.app.exportBundle().show.cues.find(function (c) { return String(c.id) === String(plainId); }));
      SIM.app.updateCue(id, { transition: "custom", sequence: "natural", span_s: 0 });
      q('[data-tab="timeline"]').click();
      await wait(200);
      q('tr[data-cue="' + id + '"]').click();
      await wait(200);
      out.naturalShown = visible(cueCtl());
      SIM.app.updateCue(id, { sequence: "top_down", span_s: 2 });
      q('tr[data-cue="' + id + '"]').click();
      await wait(200);
      out.straight = { shown: visible(cueCtl()), widthShown: visible(cueCtl().querySelector(".edge-more")),
                       options: Array.prototype.map.call(cueCtl().querySelectorAll("select option"), function (o) { return o.textContent; }),
                       rankSet: stateCue(id).sweep.rank_set };
      change(cueCtl().querySelector('select[data-ef="edge"]'), "wave");
      await wait(200);
      var sweep = stateCue(id).sweep;
      var ranks = SIM.app.getState().items.find(function (it) { return it.item === item.item; }).sweep_ranks[sweep.rank_set];
      out.wave = { stored: edgeFields(projectCue(id)), rankSet: sweep.rank_set,
                   ranks: ranks ? ranks.length : null,
                   fractional: ranks ? ranks.some(function (r) { return r !== Math.floor(r); }) : null,
                   widthShown: visible(cueCtl().querySelector(".edge-more")),
                   text: cueCtl().textContent.replace(/\\s+/g, " ").trim() };
      // the preview plays the edge ranks: the last scale still lands on the span
      var stItem = SIM.app.getState().items.find(function (it) { return it.item === item.item; });
      SIM.flicker.clearCaches();
      var model = SIM.flicker.refreshModelFor(stItem, stateCue(id), stateCue(id).sweep);
      var straightModel = SIM.flicker.refreshModelFor(stItem, { id: "probe-straight" },
        { sequence: "top_down", span_s: 2, rank_set: "" });
      out.preview = { maxDelay: Math.max.apply(null, Array.from(model.delay)),
                      differs: Array.from(model.delay).some(function (d, n) { return d !== straightModel.delay[n]; }) };
      cueCtl().querySelector('button[data-v="random"]').click();
      await wait(200);
      out.random = { stored: edgeFields(projectCue(id)),
                     text: cueCtl().querySelector(".edge-rows").textContent.replace(/\\s+/g, " ").trim() };
      document.querySelector("#toast").textContent = "";
      change(cueCtl().querySelector('input[data-ef="edge_min_rows"]'), "30");
      await wait(200);
      out.tooWide = { toast: q("#toast").textContent, stored: edgeFields(projectCue(id)) };
      change(cueCtl().querySelector('input[data-ef="edge_rows"]'), "10");
      await wait(200);
      change(cueCtl().querySelector('input[data-ef="edge_min_rows"]'), "7");
      await wait(200);
      document.querySelector("#toast").textContent = "";
      change(cueCtl().querySelector('input[data-ef="edge_min_rows"]'), "11");
      await wait(200);
      out.minOverMax = { toast: q("#toast").textContent, stored: edgeFields(projectCue(id)) };
      document.querySelector("#toast").textContent = "";
      change(cueCtl().querySelector('input[data-ef="edge_rows"]'), "4");
      await wait(200);
      out.maxUnderMin = edgeFields(projectCue(id));
      out.maxUnderMinToast = q("#toast").textContent;
      cueCtl().querySelector('button[data-ef="new"]').click();
      await wait(200);
      out.newPattern = edgeFields(projectCue(id));
      out.cueTable = q('tr[data-cue="' + id + '"]').textContent.replace(/\\s+/g, " ");

      // ---- the bundle round trip ----
      var bundle = SIM.app.exportBundle();
      var exported = bundle.show.cues.find(function (c) { return String(c.id) === String(id); });
      out.exported = edgeFields(exported);
      out.plainCueExported = Object.keys(bundle.show.cues.find(function (c) { return c.transition !== "custom"; }) || {});
      SIM.app.newProject();
      SIM.app.openBundle(JSON.parse(JSON.stringify(bundle)));
      await wait(200);
      out.reopened = edgeFields(stateCue(id));
      out.reopenedSweep = stateCue(id).sweep.rank_set;

      // ---- a design's transition ----
      SIM.app.show({ tab: "designs", item: item.item, design: design });
      SIM.app.setDesignTransition(design, "natural", 0);
      await wait(100);
      out.designNatural = visible(q('.edge-ctl[data-tr-edge]'));
      SIM.app.setDesignTransition(design, "left_right", 1.5);
      await wait(100);
      out.designTouched = Object.keys(SIM.app.getProject().show.transitions[design]);
      var dctl = q('.edge-ctl[data-tr-edge]');
      out.designShown = visible(dctl);
      change(dctl.querySelector('select[data-ef="edge"]'), "fray");
      await wait(200);
      out.designFray = Object.assign({}, SIM.app.getProject().show.transitions[design]);
      q('.edge-ctl[data-tr-edge] button[data-ef="new"]').click();
      await wait(200);
      out.designSeed = SIM.app.getProject().show.transitions[design].edge_seed;
      SIM.app.setDesignTransition(design, "bottom_up", 2);
      await wait(100);
      out.designKept = Object.assign({}, SIM.app.getProject().show.transitions[design]);
      out.designRow = ((q('.dsg[data-design="' + CSS.escape(design) + '"] .dsg-edge') || {}).textContent || "");
      var dtr = SIM.app.getState().items.find(function (it) { return it.item === item.item; })
        .designs.find(function (d) { return d.name === design; }).transition;
      out.designState = { rankSet: dtr.rank_set,
        ranked: !!SIM.app.getState().items.find(function (it) { return it.item === item.item; }).sweep_ranks[dtr.rank_set] };
      // an edge set back to all defaults is stored as no edge at all, and a
      // later touch of the sequence or span adds none (2026-10-23)
      var trKeys = function () { return Object.keys(SIM.app.getProject().show.transitions[design]); };
      SIM.app.setDesignTransition(design, "top_down", 2, SIM.sequence.EDGE_DEFAULTS);
      out.designDefaults = trKeys();
      SIM.app.setDesignTransition(design, "top_down", 3);
      out.designDefaultsTouched = trKeys();
      SIM.app.show({ tab: "designs", item: item.item, design: design });
      await wait(100);
      change(q('.edge-ctl[data-tr-edge] select[data-ef="edge"]'), "wave");
      await wait(200);
      out.designWaveKeys = trKeys();
      change(q('.edge-ctl[data-tr-edge] select[data-ef="edge"]'), "straight");
      await wait(200);
      out.designStraightAgain = trKeys();
      // all-default keys an older build wrote: dropped on the next touch,
      // and left out of Save project... meanwhile
      var legacy = Object.assign({ sequence: "top_down", span_s: 3 }, SIM.sequence.EDGE_DEFAULTS);
      SIM.app.getProject().show.transitions[design] = legacy;
      out.legacyExported = Object.keys(SIM.app.exportBundle().show.transitions[design]);
      SIM.app.setDesignTransition(design, "top_down", 4);
      out.legacyTouched = trKeys();

      // a typed Max that lowers Min says so under Fixed too, naming the
      // stored Min; quiet when Min did not move (2026-10-23)
      SIM.app.show({ tab: "designs", item: item.item, design: design });
      await wait(100);
      change(q('.edge-ctl[data-tr-edge] select[data-ef="edge"]'), "wave");
      await wait(200);
      var maxToast = async function (value) {
        document.querySelector("#toast").textContent = "";
        change(q('.edge-ctl[data-tr-edge] input[data-ef="edge_rows"]'), value);
        await wait(200);
        return [q("#toast").textContent, SIM.app.getProject().show.transitions[design].edge_min_rows];
      };
      out.fixedMaxToasts = [await maxToast("3"), await maxToast("0.5"), await maxToast("0.46")];

      // ---- the Conductor's import of what Save project... writes ----
      // one straight design, one edged, one legacy all-default entry; the
      // bundle is trimmed to this garment's files (test_the_conductor_imports_...)
      var three = item.designs.slice(0, 3).map(function (d) { return d.name; });
      var trs = SIM.app.getProject().show.transitions;
      Object.keys(trs).forEach(function (k) { delete trs[k]; });
      SIM.app.setDesignTransition(three[0], "top_down", 2);
      SIM.app.setDesignTransition(three[1], "left_right", 1.5,
        { edge: "wave_fray", edge_rows: 3, edge_random: true, edge_min_rows: 1.5, edge_seed: 7 });
      trs[three[2]] = Object.assign({ sequence: "center", span_s: 2.5 }, SIM.sequence.EDGE_DEFAULTS);
      var rt = SIM.app.exportBundle();
      var keep = three.concat([item.map.name]);
      Object.keys(rt.files).forEach(function (k) { if (keep.indexOf(k) === -1) delete rt.files[k]; });
      rt.show.cues = [];
      out.roundTrip = rt;
    } catch (e) { out.error = String((e && e.stack) || e); }
    publish();
  })();
})();
</script>
"""

EDGE_KEYS = {"edge", "edge_rows", "edge_random", "edge_min_rows", "edge_seed"}


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("simedge")
    _require_browser(tmp)
    assert DIST.exists(), "dist/az27ss-simulator.html has not been built yet"
    return _probe_page(tmp, _PROBE, "simedge-out")


def test_a_new_cue_starts_straight(sim):
    # straight by the keys' absence in the project (2026-10-23), as in the
    # Conductor's show.json; the page state fills the defaults in
    assert not EDGE_KEYS & set(sim["newCueKeys"]), sim["newCueKeys"]
    assert sim["newCue"] == {"edge": "straight", "edge_rows": 6, "edge_random": False,
                             "edge_min_rows": 1, "edge_seed": 1}


def test_a_cue_stores_no_default_edge(sim):
    assert EDGE_KEYS <= set(sim["plainWaveKeys"]), sim["plainWaveKeys"]
    assert not EDGE_KEYS & set(sim["plainBackKeys"]), sim["plainBackKeys"]
    assert sim["plainBundleKeys"] and not EDGE_KEYS & set(sim["plainBundleKeys"])


def test_the_edge_is_offered_only_for_a_sweep(sim):
    assert sim["naturalShown"] is False
    straight = sim["straight"]
    assert straight["shown"] is True and straight["widthShown"] is False, straight
    assert straight["options"] == ["Straight", "Wave", "Fray", "Wave + fray"]
    assert straight["rankSet"] == ""
    assert sim["designNatural"] is False and sim["designShown"] is True


def test_a_wave_is_stored_and_ranked(sim):
    wave = sim["wave"]
    assert wave["stored"]["edge"] == "wave", wave
    assert wave["rankSet"] == "top_down/wave/6/fixed/1/1", wave
    assert wave["ranks"] == sim["scales"] and wave["fractional"] is True, wave
    assert wave["widthShown"] is True
    assert "Width" in wave["text"] and "Fixed" in wave["text"] and "Random" in wave["text"]
    assert "New pattern" in wave["text"]


def test_the_preview_plays_the_edge_and_still_ends_on_the_span(sim):
    assert sim["preview"]["maxDelay"] == 2, sim["preview"]
    assert sim["preview"]["differs"] is True


def test_random_width_min_and_max(sim):
    random = sim["random"]
    assert random["stored"]["edge_random"] is True, random
    assert random["text"].startswith("Min") and random["text"].endswith("rows"), random
    assert "Max" in random["text"]
    assert sim["tooWide"]["toast"].startswith("Width: 0 to 24 rows"), sim["tooWide"]
    assert sim["tooWide"]["stored"]["edge_min_rows"] == 1
    over = sim["minOverMax"]
    assert over["toast"] == "Min cannot be wider than Max", over
    assert over["stored"]["edge_rows"] == 10 and over["stored"]["edge_min_rows"] == 7, over
    # a Max brought below the Min takes the Min down with it, and says so
    assert sim["maxUnderMin"]["edge_rows"] == 4 and sim["maxUnderMin"]["edge_min_rows"] == 4
    assert sim["maxUnderMinToast"] == "Max is narrower than Min - Min lowered to 4 rows"
    # under Fixed too; quiet when Min stays (Max 3 over Min 1, or 0.46
    # rounding back to the Min of 0.5 already stored)
    assert sim["fixedMaxToasts"] == [["", 1], ["Max is narrower than Min - Min lowered to 0.5 rows", 0.5],
                                     ["", 0.5]], sim["fixedMaxToasts"]


def test_new_pattern_moves_the_seed_on(sim):
    assert sim["newPattern"]["edge_seed"] == 2
    assert "Wave edge, 4-4 rows" in sim["cueTable"], sim["cueTable"]


def test_the_bundle_carries_the_edge_and_opens_with_it(sim):
    want = {"edge": "wave", "edge_rows": 4, "edge_random": True, "edge_min_rows": 4, "edge_seed": 2}
    assert sim["exported"] == want
    assert sim["reopened"] == want
    assert sim["reopenedSweep"] == "top_down/wave/4/random/4/2"
    # a cue without an edge exports exactly as it did before the edge
    assert sim["plainCueExported"] and not EDGE_KEYS & set(sim["plainCueExported"])


def test_a_design_transition_keeps_its_edge(sim):
    fray = sim["designFray"]
    assert fray["sequence"] == "left_right" and fray["span_s"] == 1.5 and fray["edge"] == "fray", fray
    assert sim["designSeed"] == 2
    kept = sim["designKept"]
    assert kept["sequence"] == "bottom_up" and kept["edge"] == "fray" and kept["edge_seed"] == 2, kept
    assert sim["designRow"] == "Fray edge, 6 rows"
    assert sim["designState"] == {"rankSet": "bottom_up/fray/6/fixed/1/2", "ranked": True}


def test_a_design_transition_stores_no_default_edge(sim):
    assert sim["designTouched"] == ["sequence", "span_s"]
    assert sim["designDefaults"] == ["sequence", "span_s"]
    assert sim["designDefaultsTouched"] == ["sequence", "span_s"]
    assert EDGE_KEYS <= set(sim["designWaveKeys"]), sim["designWaveKeys"]
    assert sim["designStraightAgain"] == ["sequence", "span_s"]
    assert sim["legacyExported"] == ["sequence", "span_s"]
    assert sim["legacyTouched"] == ["sequence", "span_s"]


def test_the_conductor_imports_the_exported_transitions_as_they_are(sim, tmp_path):
    """Save project... -> the Conductor's Workspace.import_bundle(): the
    design transitions land in show.json exactly as the simulator wrote
    them - a straight one and a legacy all-default one with no edge keys,
    an edged one with all five (2026-10-23)."""
    from conductor.server import Workspace
    bundle = sim["roundTrip"]
    exported = bundle["show"]["transitions"]
    assert len(exported) == 3, exported
    edged = [v for v in exported.values() if EDGE_KEYS & set(v)]
    assert edged == [{"sequence": "left_right", "span_s": 1.5, "edge": "wave_fray", "edge_rows": 3,
                      "edge_random": True, "edge_min_rows": 1.5, "edge_seed": 7}], exported
    ws = Workspace(tmp_path / "ws")
    result = ws.import_bundle(bundle)
    assert result["refused"] == [] and result["renamed"] == {}, result
    show = json.loads((ws.root / "show.json").read_text(encoding="utf-8"))
    assert show["transitions"] == exported
