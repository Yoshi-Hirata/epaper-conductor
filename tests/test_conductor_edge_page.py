"""The sweep's edge on the Conductor page (2026-10-22): a swept transition's
border can be Straight / Wave / Fray / Wave + fray, Fixed or Random width,
with a seed that "New pattern" moves on. The ranks are the server's; what
conductor/web/index.html owns is editing the five fields, posting them with
the show (cues) and /api/transition (designs), and drawing the Timeline
preview from item.sweep_ranks[sweep.rank_set].

* Plain text checks over index.html - they run everywhere.
* One headless run of the REAL page against a stand-in that wraps a real
  Workspace and adds what the edge-aware server sends (the five fields,
  `rank_set` on each cue's sweep, `sweep_ranks` on each item), so the page
  is driven exactly as the operator would drive it. Behind
  CONDUCTOR_BROWSER_TESTS=1 like the other browser tests - skipped, not
  failed, where no browser is installed.
"""
from __future__ import annotations

import http.server
import json
import re
import socket
import sys
import threading
from html import unescape
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO / "conductor" / "web" / "index.html"

sys.path.insert(0, str(REPO))

from conductor.server import Workspace  # noqa: E402
from tests.test_designer_build import _dump_dom, _require_browser  # noqa: E402
from tests.test_look import GRID, MAP  # noqa: E402

PAGE = INDEX_HTML.read_text(encoding="utf-8")
EDGE_FIELDS = ("edge", "edge_rows", "edge_random", "edge_min_rows", "edge_seed")
EDGE_DEFAULTS = {"edge": "straight", "edge_rows": 6, "edge_random": False,
                 "edge_min_rows": 1, "edge_seed": 1}


def _function(name: str) -> str:
    start = PAGE.index(f"function {name}(")
    depth = 0
    for i in range(PAGE.index("{", start), len(PAGE)):
        depth += (PAGE[i] == "{") - (PAGE[i] == "}")
        if depth == 0:
            return PAGE[start:i + 1]
    raise AssertionError(f"function {name} is never closed")


def _without_comments(source: str) -> str:
    return "\n".join(re.sub(r"//.*", "", line) for line in source.splitlines())


# ------------------------------------------------------- the page as text

def test_every_save_of_the_show_carries_each_cues_own_edge():
    # An unrelated edit (a Start dragged, a design swapped) posts the whole
    # timeline; a cue whose edge were left out would come back straight.
    body = _without_comments(_function("saveShow"))
    assert "...cueEdge(cue)" in body, body


def test_a_designs_span_change_keeps_its_edge():
    # /api/transition sets the whole transition: the sequence/span handler
    # has to send the design's current edge along or a new span straightens it.
    handler = PAGE[PAGE.index("e.target.dataset.trSeq !== undefined || e.target.dataset.trSpan"):]
    handler = _without_comments(handler[:handler.index("\n  }\n")])
    assert "...edgeOf(designTransition(design))" in handler, handler


def test_the_preview_draws_from_the_rank_set_and_keys_its_cache_by_it():
    body = _without_comments(_function("refreshModelFor"))
    assert "item.sweep_ranks?.[rankSet]" in body
    assert 'const key = item.item + "|" + cue.id + "|" + rankSet;' in body
    # Straight (rank_set "") and a server with no sweep_ranks: today's ranks.
    assert "edged || item.sequences?.[sweep.sequence]" in body


def test_no_edge_row_at_a_span_of_zero():
    # The spec ignores the edge when the span is 0: nothing to offer then.
    body = _without_comments(_function("renderCueEditor"))
    assert '(cue.sequence || "natural") !== "natural" && (cue.span_s ?? 0) > 0' in body
    assert 'designTr.sequence !== "natural" && designTr.span_s > 0' in body


def test_the_controls_use_the_agreed_words():
    for words in ('"Straight"', '"Wave"', '"Fray"', '"Wave + fray"', ">Fixed<", ">Random<",
                  ">New pattern<", " rows</span>", "Min ${", " Max ${"):
        assert words in PAGE, words


# ------------------------------------------------------- the page, running

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _fake_ranks(base, seed):
    """Fractional ranks standing in for conductor/sequence.py's edge_ranks():
    the page only has to draw what it is given, so any deterministic,
    non-integer set that differs per seed will do."""
    return [r + 0.5 * ((i + seed) % 2) for i, r in enumerate(base)]


class _Stand:
    """index.html, a real Workspace, and the edge fields the edge-aware
    server adds - kept here, since this tree's Workspace drops them."""

    def __init__(self, tmp_path, probe):
        self.ws = Workspace(tmp_path / "ws")
        self.ws.save("Look22_map.csv", MAP)
        self.ws.save("Look22_color_pattern01_grid.csv", GRID)
        self.ws.save("Look22_color_pattern02_grid.csv", GRID)
        self.ws.set_transition("Look22_color_pattern02_grid.csv", "top_down", 2.0)
        self.ws.set_timeline(600, [
            {"id": "c1", "item": "Look22", "at": 30, "design": "Look22_color_pattern01_grid.csv",
             "transition": "custom", "sequence": "top_down", "span_s": 2.0},
            {"id": "c2", "item": "Look22", "at": 60, "design": "Look22_color_pattern02_grid.csv"},
            {"id": "c3", "item": "Look22", "at": 90, "design": "Look22_color_pattern01_grid.csv",
             "transition": "custom", "sequence": "natural", "span_s": 0},
        ])
        self.cue_edges, self.design_edges = {}, {}
        page = PAGE.replace("</body>", probe + "</body>", 1)
        stand = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, body, kind):
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj):
                self._send(json.dumps(obj).encode("utf-8"), "application/json")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                try:
                    if self.path == "/api/show":
                        for cue in body.get("cues", []):
                            stand.cue_edges[cue["id"]] = {f: cue[f] for f in EDGE_FIELDS if f in cue}
                        stand.ws.set_timeline(body.get("duration", 600), body.get("cues", []),
                                              body.get("refresh_s"))
                        return self._json({"ok": True})
                    if self.path == "/api/transition":
                        stand.design_edges[body["design"]] = {f: body[f] for f in EDGE_FIELDS if f in body}
                        stand.ws.set_transition(body["design"], body["sequence"], body["span_s"])
                        return self._json({"ok": True})
                except ValueError as exc:
                    return self._json({"error": str(exc)})
                return self._json({})

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    return self._send(page.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/api/state":
                    return self._json(stand.state())
                if path == "/api/fleet":
                    return self._json({"units": [], "last_fire": None, "run": None, "shows": {},
                                       "corrections": [], "prepared": {}, "start_at": 0.0,
                                       "show_duration": 600.0, "timeline": None})
                if path == "/api/fleet/demos":
                    return self._json({"units": {}, "offline": [], "failed": {}})
                return self._json({})

        self.port = _free_port()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def state(self):
        """The Workspace's state plus the wire format of the shared spec:
        the five fields on each design transition and stored cue, each cue's
        resolved sweep with them and a `rank_set`, and the item's
        `sweep_ranks` for every non-straight sweep."""
        st = self.ws.state()
        for item in st["items"]:
            for d in item["designs"]:
                d["transition"] = {**d["transition"], **EDGE_DEFAULTS,
                                   **self.design_edges.get(d["name"], {})}
            item["sweep_ranks"] = {}
            for cue in st["show"]["cues"]:
                if cue["item"] != item["item"]:
                    continue
                own = {**EDGE_DEFAULTS, **self.cue_edges.get(cue["id"], {})}
                cue.update(own)
                sweep = cue["sweep"]
                src = own if sweep["source"] == "cue" else \
                    {**EDGE_DEFAULTS, **self.design_edges.get(cue["design"], {})}
                sweep.update(src)
                if sweep["sequence"] != "natural" and sweep["edge"] != "straight":
                    key = "{sequence}|{edge}|{edge_rows}|{edge_random}|{edge_min_rows}|{edge_seed}".format(**sweep)
                    sweep["rank_set"] = key
                    item["sweep_ranks"][key] = _fake_ranks(item["sequences"][sweep["sequence"]],
                                                           sweep["edge_seed"])
                else:
                    sweep["rank_set"] = ""
        return st

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# One pass over the page as the operator would make it, writing what it saw
# into #edge-out. POST bodies are caught on their way out (fetch wrapped),
# so each step can say exactly what that one control sent.
_PROBE = """
<script>
(function () {
  var out = { error: null };
  function publish() {
    var pre = document.createElement("pre");
    pre.id = "edge-out";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }
  var wait = ms => new Promise(r => setTimeout(r, ms));
  var posts = [];
  var realFetch = window.fetch;
  window.fetch = function (path, init) {
    if (init && init.method === "POST") posts.push({ path: path, body: JSON.parse(init.body) });
    return realFetch.apply(this, arguments);
  };
  function lastPost(path) { for (var i = posts.length - 1; i >= 0; i--) if (posts[i].path === path) return posts[i].body; return null; }
  function sentCue(id) { var b = lastPost("/api/show"); return b ? b.cues.find(c => c.id === id) : null; }
  function edgeOfCue(c) { return c ? { edge: c.edge, edge_rows: c.edge_rows, edge_random: c.edge_random,
                                       edge_min_rows: c.edge_min_rows, edge_seed: c.edge_seed } : null; }
  function openCue(id) { ui.tab = "timeline"; ui.cue = id; ui.editorOpen = true; render(); }
  function box(scope) { return document.querySelector("#tl-editor-card [" + scope + "]") || document.querySelector("[" + scope + "]"); }
  function field(scope, f) { var b = box(scope); return b ? b.querySelector('[data-edge-field="' + f + '"]') : null; }
  async function change(el, value) { el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); await wait(350); }
  async function press(el) { el.click(); await wait(350); }
  function toastNow() { return (document.querySelector("#toast") || {}).textContent || ""; }
  function item() { return state.items.find(i => i.item === "Look22"); }
  function cueOf(id) { return state.show.cues.find(c => c.id === id); }
  function rowWhy(scope) { var b = box(scope); var row = b && b.closest(".cue-row"); return row ? row.querySelector(".why").textContent : null; }
  (async function () {
    try {
      await wait(500);
      out.booted = !!(state && state.items && state.items.length);

      // 1. A custom top-to-bottom cue: the Edge row is there, Straight, and
      //    Straight draws from the sequence's own ranks (rank_set "").
      openCue("c1");
      var sel = field('data-edge-cue="c1"', "edge");
      out.straight = { select: sel ? sel.value : null,
                       options: sel ? Array.from(sel.options).map(o => o.textContent) : null,
                       widthShown: !!field('data-edge-cue="c1"', "edge_random"),
                       why: rowWhy('data-edge-cue="c1"'),
                       delay: Array.from(refreshModelFor(item(), cueOf("c1"), sweepOf(cueOf("c1"))).delay) };

      // 2. Wave: one /api/show, the cue's five fields in it; the preview now
      //    draws from sweep_ranks.
      await change(sel, "wave");
      var c1 = cueOf("c1"), sw = sweepOf(c1);
      out.wave = { sent: edgeOfCue(sentCue("c1")), otherCue: edgeOfCue(sentCue("c2")),
                   rankSet: sw.rank_set, ranks: item().sweep_ranks[sw.rank_set],
                   delay: Array.from(refreshModelFor(item(), c1, sw).delay),
                   fixedOn: (field('data-edge-cue="c1"', "edge_random") || {}).className,
                   rowsField: (field('data-edge-cue="c1"', "edge_rows") || {}).value,
                   why: rowWhy('data-edge-cue="c1"') };

      // 3. Random width, then Min and Max, then New pattern.
      var random = box('data-edge-cue="c1"').querySelector('[data-edge-field="edge_random"][data-value="1"]');
      await press(random);
      out.random = { sent: edgeOfCue(sentCue("c1")),
                     min: !!field('data-edge-cue="c1"', "edge_min_rows") };
      await change(field('data-edge-cue="c1"', "edge_min_rows"), "2");
      await change(field('data-edge-cue="c1"', "edge_rows"), "8.25");
      out.widths = edgeOfCue(sentCue("c1"));
      var before = posts.length;
      await change(field('data-edge-cue="c1"', "edge_min_rows"), "9");
      out.minOverMax = { posted: posts.length - before, toast: toastNow(),
                         kept: (field('data-edge-cue="c1"', "edge_min_rows") || {}).value };
      before = posts.length;
      await change(field('data-edge-cue="c1"', "edge_rows"), "3,5");
      out.comma = { posted: posts.length - before, toast: toastNow() };
      var oldSet = sweepOf(cueOf("c1")).rank_set;
      await press(field('data-edge-cue="c1"', "edge_seed"));
      out.newPattern = { sent: edgeOfCue(sentCue("c1")), oldSet: oldSet,
                         newSet: sweepOf(cueOf("c1")).rank_set };
      // Pressing the one already lit sends nothing.
      before = posts.length;
      await press(box('data-edge-cue="c1"').querySelector('[data-edge-field="edge_random"][data-value="1"]'));
      out.pressLit = posts.length - before;
      // A Max typed and New pattern pressed straight after it (the field's
      // change on blur, then the click, back to back): the second save waits
      // for the first, so neither the width nor the press is lost.
      openCue("c1");
      var maxField = field('data-edge-cue="c1"', "edge_rows"), seedBtn = field('data-edge-cue="c1"', "edge_seed");
      maxField.value = "10";
      maxField.dispatchEvent(new Event("change", { bubbles: true }));
      seedBtn.click();
      await wait(700);
      out.typedThenPressed = edgeOfCue(sentCue("c1"));

      // 4. A Start moved: the cue's edge rides along untouched.
      openCue("c1");
      await change(document.querySelector("#cue-start"), "0:40");
      out.startMoved = edgeOfCue(sentCue("c1"));

      // 5. A cue on its design's transition: the Edge row edits the DESIGN,
      //    through /api/transition, with its sequence and span.
      openCue("c2");
      out.designRow = { why: rowWhy('data-edge-design="Look22_color_pattern02_grid.csv"') };
      await change(field('data-edge-design="Look22_color_pattern02_grid.csv"', "edge"), "fray");
      out.designFray = { sent: lastPost("/api/transition"),
                         rankSet: sweepOf(cueOf("c2")).rank_set };
      // ...and a new span for that design keeps the fray.
      openCue("c2");
      await change(document.querySelector('#tl-editor-card [data-tr-span="Look22_color_pattern02_grid.csv"]'), "3");
      out.designSpan = lastPost("/api/transition");

      // 6. A natural custom cue has no border to shape: no Edge row.
      openCue("c3");
      out.natural = { row: !!document.querySelector('[data-edge-cue="c3"]'),
                      edgeRows: document.querySelectorAll("#tl-editor-card .edge-ctl").length };

      // 7. The Designs tab: the design row and the toolbar both offer it.
      ui.tab = "items"; ui.item = "Look22"; ui.design = "Look22_color_pattern02_grid.csv"; render();
      var boxesWithToolbar = document.querySelectorAll('[data-edge-design="Look22_color_pattern02_grid.csv"]').length;
      // pattern01 picked, pattern02's row pressed: the press must not pick it.
      ui.design = "Look22_color_pattern01_grid.csv"; render();
      out.designsTab = { boxes: boxesWithToolbar,
                         rowOnly: document.querySelectorAll('[data-edge-design="Look22_color_pattern02_grid.csv"]').length,
                         rowSelect: (document.querySelector('.dsg-edge [data-edge-field="edge"]') || {}).value,
                         natural01: !!document.querySelector('.dsg[data-design="Look22_color_pattern01_grid.csv"] .dsg-edge') };
      await press(document.querySelector('.dsg[data-design="Look22_color_pattern02_grid.csv"] .dsg-edge [data-edge-field="edge_seed"]'));
      out.designsTab.seed = (lastPost("/api/transition") || {}).edge_seed;
      out.designsTab.stillThisDesign = ui.design;

      // 8. An older server: no rank_set, no sweep_ranks - today's ranks; and
      //    one cue under two rank sets is two models, not one stale one.
      var it = item(), plain = { sequence: "top_down", span_s: 2.0, source: "cue" };
      var cue = { id: "probe" };
      out.oldServer = Array.from(refreshModelFor({ item: it.item, map: it.map, sequences: it.sequences }, cue, plain).delay);
      var a = refreshModelFor(it, { id: "probe2" }, Object.assign({}, plain, { rank_set: "" }));
      var fake = Object.assign({}, it, { sweep_ranks: { k: [0, 0.5, 1.5, 3] } });
      var b = refreshModelFor(fake, { id: "probe2" }, Object.assign({}, plain, { rank_set: "k" }));
      var missing = refreshModelFor(it, { id: "probe3" }, Object.assign({}, plain, { rank_set: "nowhere" }));
      out.cacheKey = { straight: Array.from(a.delay), edged: Array.from(b.delay), missing: Array.from(missing.delay) };
    } catch (e) { out.error = String((e && e.stack) || e); }
    publish();
  })();
})();
</script>
"""


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("edge")
    _require_browser(tmp)
    stand = _Stand(tmp, _PROBE)
    try:
        dom = _dump_dom(stand.url, tmp)
    finally:
        stand.close()
    match = re.search(r'<pre id="edge-out">(.*?)</pre>', dom or "", re.S)
    assert match, f"no #edge-out in the dumped DOM:\n{(dom or '')[:3000]}"
    data = json.loads(unescape(match.group(1)))
    assert data.get("error") is None, data["error"]
    assert data.get("booted"), "the page never loaded its state"
    return data


def test_a_straight_edge_is_todays_sweep(edge):
    s = edge["straight"]
    assert s["select"] == "straight"
    assert s["options"] == ["Straight", "Wave", "Fray", "Wave + fray"]
    assert not s["widthShown"]                         # nothing to set on a straight line
    assert s["why"].startswith("Straight: the border moves as a clean line")
    # top_down over this map is [0, 0, 1, 1]: the plain ranks, 2 s span.
    assert s["delay"] == [0, 0, 2, 2]


def test_choosing_wave_posts_the_cues_edge_and_redraws_from_its_ranks(edge):
    w = edge["wave"]
    assert w["sent"] == {"edge": "wave", "edge_rows": 6, "edge_random": False,
                         "edge_min_rows": 1, "edge_seed": 1}
    # The other cues ride along with their own (default) edge.
    assert w["otherCue"]["edge"] == "straight"
    assert w["rankSet"] and w["ranks"] == [0.5, 0, 1.5, 1]        # _fake_ranks(seed 1)
    top = max(w["ranks"])
    assert w["delay"] == [r * 2.0 / top for r in w["ranks"]]
    assert w["fixedOn"] == "on" and w["rowsField"] == "6"
    assert w["why"].startswith("Wave: the border moves as a wavy line")
    assert "Applies to this cue only." in w["why"]


def test_random_width_min_max_and_new_pattern(edge):
    assert edge["random"]["sent"]["edge_random"] is True and edge["random"]["min"]
    assert edge["widths"] == {"edge": "wave", "edge_rows": 8.3, "edge_random": True,
                              "edge_min_rows": 2, "edge_seed": 1}
    # Refused, not clamped: nothing sent, said why, the field shows the stored value.
    assert edge["minOverMax"]["posted"] == 0
    assert "Min cannot be wider than Max" in edge["minOverMax"]["toast"]
    assert edge["minOverMax"]["kept"] == "2"
    assert edge["comma"]["posted"] == 0 and "use a period" in edge["comma"]["toast"]
    np = edge["newPattern"]
    assert np["sent"]["edge_seed"] == 2 and np["sent"]["edge_rows"] == 8.3
    assert np["newSet"] != np["oldSet"]
    assert edge["pressLit"] == 0


def test_a_typed_width_then_new_pattern_keeps_both(edge):
    # Review of ebc8888: the click used to post from the state before the
    # typed Max had landed, and put the old width back.
    assert edge["typedThenPressed"] == {"edge": "wave", "edge_rows": 10, "edge_random": True,
                                        "edge_min_rows": 2, "edge_seed": 3}


def test_an_unrelated_edit_keeps_the_cues_edge(edge):
    assert edge["startMoved"] == {"edge": "wave", "edge_rows": 10, "edge_random": True,
                                  "edge_min_rows": 2, "edge_seed": 3}


def test_a_design_cues_edge_row_edits_the_design(edge):
    assert "Applies to" in edge["designRow"]["why"] and "everywhere it is used" in edge["designRow"]["why"]
    sent = edge["designFray"]["sent"]
    assert sent == {"design": "Look22_color_pattern02_grid.csv", "sequence": "top_down",
                    "span_s": 2.0, "edge": "fray", "edge_rows": 6, "edge_random": False,
                    "edge_min_rows": 1, "edge_seed": 1}
    assert edge["designFray"]["rankSet"]
    span = edge["designSpan"]
    assert span["span_s"] == 3 and span["edge"] == "fray"


def test_a_natural_sequence_has_no_edge_row(edge):
    assert edge["natural"] == {"row": False, "edgeRows": 0}


def test_the_designs_tab_offers_the_edge_on_the_row_and_the_toolbar(edge):
    tab = edge["designsTab"]
    assert tab["boxes"] == 2                    # the design's row + the toolbar
    assert tab["rowOnly"] == 1                  # pattern01 picked: no toolbar edge (natural)
    assert tab["rowSelect"] == "fray"
    assert not tab["natural01"]                 # a natural design: nothing to shape
    assert tab["seed"] == 2
    # A press inside pattern02's row is not taken for "pick this design" as
    # well: pattern01 stays the one picked.
    assert tab["stillThisDesign"] == "Look22_color_pattern01_grid.csv"


def test_an_older_server_and_the_cache_key(edge):
    assert edge["oldServer"] == [0, 0, 2, 2]
    keys = edge["cacheKey"]
    assert keys["straight"] == [0, 0, 2, 2]
    assert keys["edged"] == [0, 2 * 0.5 / 3, 2 * 1.5 / 3, 2.0]
    # A rank_set the server did not send ranks for: the plain ranks.
    assert keys["missing"] == [0, 0, 2, 2]
