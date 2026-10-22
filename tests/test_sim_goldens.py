"""The JS/Python cross-check for the designer simulator (plan section 2).

tools/make_goldens.py reads tests/fixtures/sim/*.csv and writes
tests/goldens/model.json and conductor/web/sim/goldens.js from the SAME
data, computed by the real conductor.look/sequence/timeline. This file
checks that those generated artefacts are current, and (the one test
that actually runs JavaScript) that a browser's SIM implementation
scores 0 failures against them.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conductor.look import Design, LookError, LookMap  # noqa: E402
from conductor import sequence  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import make_goldens as mg  # noqa: E402

SIM_DIR = ROOT / "conductor" / "web" / "sim"
GOLDEN_JSON = ROOT / "tests" / "goldens" / "model.json"


@pytest.fixture(scope="module")
def golden():
    return json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))


def test_goldens_are_current():
    """python tools/make_goldens.py --check must see no drift - the
    generator recomputes goldens.js/model.json bit-for-bit deterministic
    (sorted keys, no timestamps, no absolute paths)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_goldens.py"), "--check"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, (
        f"tests/goldens/model.json or conductor/web/sim/goldens.js is stale - "
        f"run python tools/make_goldens.py\nstdout={result.stdout}\nstderr={result.stderr}")


def test_generator_ignores_showdata(tmp_path, monkeypatch):
    """The generator must depend ONLY on committed inputs (tests/fixtures/
    sim/*.csv and conductor/web/starter/*.csv) - a populated, git-ignored
    showdata/files/ (real client data that only exists on the show PC)
    must not change a single golden case, or `--check` fails on any
    machine that happens to have it. Regression test for the bug Coder Q
    found: an extra showdata-derived digest case the committed goldens
    (built on a machine without showdata/) did not have.

    Fully sandboxed under tmp_path (adversarial review round 2 - F8): the
    old version of this test wrote its probe files straight into the real
    repository's showdata/files/ - on a machine that (like the one it was
    caught on) has real, gitignored client data sitting there already, and
    whose `finally` block's rmdir() calls could raise and mask the real
    assertion if that directory somehow ended up non-empty. This copies the
    committed inputs build_goldens() actually reads into a throwaway
    directory, monkeypatches the module's own path constants to it, and
    only ever writes probes under tmp_path - if some future edit ever adds
    a real ROOT/showdata/files glob back into the generator, ROOT is
    monkeypatched too, so that edit would still see these probes and this
    test would still catch it."""
    fake_root = tmp_path / "repo"
    fake_starter = fake_root / "conductor" / "web" / "starter"
    fake_fixtures = fake_root / "tests" / "fixtures" / "sim"
    shutil.copytree(mg.STARTER_DIR, fake_starter)
    shutil.copytree(mg.FIXTURES_DIR, fake_fixtures)
    monkeypatch.setattr(mg, "ROOT", fake_root)
    monkeypatch.setattr(mg, "STARTER_DIR", fake_starter)
    monkeypatch.setattr(mg, "FIXTURES_DIR", fake_fixtures)

    before = mg.dumps_sorted(mg.build_goldens())

    showdata_files = fake_root / "showdata" / "files"
    showdata_files.mkdir(parents=True)
    (showdata_files / "ZZZ_generator_ignores_showdata_probe_map.csv").write_text(
        "side,row,col,board_no,socket,label\nfront,0,1,1,1,\n", encoding="utf-8")
    (showdata_files / "ZZZ_generator_ignores_showdata_probe_color_pattern01_grid.csv"
    ).write_text("side,row,shift,1\nfront,0,0,0x01\n", encoding="utf-8")
    after = mg.dumps_sorted(mg.build_goldens())

    assert before == after, "a populated showdata/files/ changed the generated goldens"

    # And the same, run out-of-process (the actual `--check` entry
    # point), so a stray CWD-relative glob elsewhere in main() would
    # still be caught even if build_goldens() itself looks clean.
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_goldens.py"), "--check"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_refresh_constants_match_conductor_timeline():
    """The golden cases always pass a refresh time explicitly, so a drifted
    DEFAULT in model.js would never show up there - and it is the number the
    simulator gives a brand-new project (designer-app.js's freshProject()).
    Both sides are checked here instead."""
    from conductor import timeline                                # noqa: PLC0415

    text = (SIM_DIR / "model.js").read_text(encoding="utf-8")
    for name, expected in (("REFRESH_S", timeline.REFRESH_S),
                           ("LEGACY_REFRESH_S", timeline.LEGACY_REFRESH_S),
                           ("PANEL_REPAINT_S", timeline.PANEL_REPAINT_S),
                           ("GAP_AFTER_REFRESH_S", timeline.GAP_AFTER_REFRESH_S)):
        found = re.findall(rf"^  const {name} = ([0-9.]+);$", text, re.M)
        assert found == [f"{expected}"], (
            f"conductor/web/sim/model.js defines {name} as {found}, "
            f"conductor/timeline.py has {expected}")


def test_goldens_js_is_well_formed(golden):
    # read_bytes(), not read_text() (adversarial review round 2 - F8):
    # Path.read_text() does universal-newline translation, so "\r\n" not in
    # text can never fire regardless of what is actually on disk - the CRLF
    # check needs the raw bytes to mean anything.
    raw = (SIM_DIR / "goldens.js").read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert str(ROOT).replace("\\", "/") not in text.replace("\\\\", "/")
    assert "globalThis.SIM = Object.assign" in text and "GOLDENS:" in text
    assert golden["format"] == "epaper-sim-goldens"


def test_fixture_csvs_parse_the_same_in_python(golden):
    """Every fixture CSV parses (or fails) the same way when read fresh
    through conductor.look, independently of tools/make_goldens.py's own
    caching - guards against a fixture edited without regenerating."""
    fixtures_dir = ROOT / "tests" / "fixtures" / "sim"
    for case in golden["cases"]:
        if case["kind"] == "map":
            text = fixtures_dir.joinpath(case["fixture"]).read_text(encoding="utf-8")
            opts = case["opts"]
            try:
                m = LookMap.parse(io.StringIO(text), name=opts["name"], item=opts["item"])
                ok, problems = True, []
            except LookError as exc:
                m, ok, problems = None, False, list(exc.problems)
            assert ok == case["expect"]["ok"], case["fixture"]
            if not ok:
                assert problems == case["expect"]["problems"], case["fixture"]
        elif case["kind"] == "design":
            text = fixtures_dir.joinpath(case["fixture"]).read_text(encoding="utf-8")
            opts = case["opts"]
            try:
                Design.parse(io.StringIO(text), name=opts["name"], item=opts["item"],
                             pattern=opts["pattern"])
                ok = True
            except LookError:
                ok = False
            assert ok == case["expect"]["ok"], case["fixture"]


def test_goldens_cover_every_sequence_and_rule(golden):
    seq_covered = {c["sequence"] for c in golden["cases"] if c["kind"] == "ranks"}
    assert seq_covered == set(sequence.SEQUENCES)

    kinds = {c["kind"] for c in golden["cases"]}
    for expected_kind in ("fmt", "canonical", "clock", "mmss", "names", "intake",
                          "map", "design", "check",
                          "ranks", "timeline", "state"):
        assert expected_kind in kinds, f"no golden cases of kind {expected_kind!r}"

    # The geometry sentence (2026-09-26) is the one check() problem a
    # designer is meant to act on rather than read past, so it must be in
    # the goldens - the browser self-test is what proves model.js words it
    # identically, and it can only do that if the case is here. It must
    # also LEAD its result: buried under a thousand "no colour for ..."
    # lines it would be exactly as invisible as the messages it replaces.
    results = [result for c in golden["cases"] if c["kind"] == "check"
               for result in c["expect"]["results"]]
    geometry = [p for result in results for p in result
                if "made for another layout of" in p]
    assert geometry, "no golden case for a design made for another layout"
    assert all("配線ナビ" in p for p in geometry)   # 配線ナビ
    for result in results:
        hits = [i for i, p in enumerate(result) if "made for another layout of" in p]
        assert hits in ([], [0]), \
            "the geometry sentence must lead, not trail the per-scale problems"

    # Taking a picked file in (2026-09-27): every one of the five rules
    # that decides what a dropped CSV is SAVED as has to be cross-checked,
    # not just the ones that were easy to write cases for - a rule only
    # the simulator applies is a rule the show PC will disagree with.
    intake_ops = {c["op"] for c in golden["cases"] if c["kind"] == "intake"}
    assert intake_ops == {"macSafeName", "isMacMetadata", "uniqueSaveName",
                          "renameOntoItem", "sniffCsvKind", "conventionalName",
                          "foldName", "csvExtension", "parseTimeField"}
    # Case folding is what stands between the workspace and a Windows
    # filesystem that cannot tell two names apart, so a uniqueSaveName
    # case that differs only in case has to be in the goldens.
    assert any(c["name"].lower() in {t.lower() for t in c["taken"]}
               and c["name"] not in c["taken"]
               for c in golden["cases"]
               if c["kind"] == "intake" and c["op"] == "uniqueSaveName")
    sniffed = [c["expect"] for c in golden["cases"]
               if c["kind"] == "intake" and c["op"] == "sniffCsvKind"]
    assert set(sniffed) == {"map", "grid", None}
    # A garment has one wiring file: uniqueSaveName has nowhere to put a
    # number on a *_map.csv and must say so rather than invent a name.
    assert any(c["expect"] is None for c in golden["cases"]
               if c["kind"] == "intake" and c["op"] == "uniqueSaveName")

    # The file-name grammar covers both spellings of a design file, the
    # site's own <model>_<配色案名>_HW.csv included.
    name_cases = [c for c in golden["cases"] if c["kind"] == "names"]
    assert {c["expect"]["kind"] for c in name_cases} == {"map", "grid", None}
    assert any(c["filename"].lower().endswith("_hw.csv")
               and c["expect"]["kind"] == "grid" for c in name_cases)

    # state-digest cases come from conductor/web/starter/*.csv (Q's
    # committed real maps), not showdata/ - they must always be present
    # and reproducible on any machine, one per starter item.
    digest_cases = [c for c in golden["cases"] if c["kind"] == "state-digest"]
    # The exact 10 starter items, pinned by name - not "no name contains the
    # literal word showdata" (adversarial review round 2 - F8), which can
    # never fail: these cases are built from conductor/web/starter/*.csv
    # (Q's committed real maps), and nothing in that path ever contains the
    # word "showdata" regardless of whether the check that follows it is
    # doing anything. A pinned set catches an item silently going missing
    # (or an unexpected one appearing) the way the vacuous check could not.
    EXPECTED_STARTER_ITEMS = {
        "starter-AZ271SB2303", "starter-AZ271SC6302", "starter-AZ271SD1301",
        "starter-AZ271SD1305", "starter-AZ271SD1305_B", "starter-AZ271SD1306",
        "starter-AZ271SD1307", "starter-AZ271SG1035", "starter-AZ271SG1036",
        "starter-AZ271SG3037",
    }
    assert {c["name"] for c in digest_cases} == EXPECTED_STARTER_ITEMS

    # The exact half-to-even landmark: Python rounds 2.25 to one decimal
    # as "2.2" (even), not the "2.3" a naive JS Math.round-based toFixed
    # would give - this is the whole reason SIM.fmt exists.
    tie = [c for c in golden["cases"]
          if c["kind"] == "fmt" and c["op"] == "fixed" and c["x"] == 2.25 and c["n"] == 1]
    assert tie and tie[0]["expect"] == "2.2"

    # Every validate() spacing "binding" (write/refresh/rejoin) and the
    # design-kind/no-preset/sweep rules appear somewhere in the timeline
    # goldens, not just as Python unit tests nobody ported.
    all_problems = " ".join(
        p for c in golden["cases"] if c["kind"] == "timeline"
        for plist in c["expect"]["problems"].values() for p in plist)
    all_warnings = " ".join(
        w for c in golden["cases"] if c["kind"] == "timeline" for w in c["expect"]["warnings"])
    for phrase in ("refresh + ", "a board holds", "merge or remove cues",
                  "is not loaded", "make this a partial cue", "after the end of the show",
                  "already has a cue sent at the same moment",
                  "previous picture is complete", "sweep is at most 30 s",
                  "needs the item's map to be timed"):
        assert phrase in all_problems, phrase
    assert "no preset at 0:00" in all_warnings


def test_goldens_cover_every_edge_fixed_and_random_front_and_back(golden):
    """The sweep's edge (2026-10-22): the simulator's port of
    sequence.edge_ranks() is held to every edge style, a fixed and a
    random width, a vertical and a horizontal sequence, on garments with
    a back - and every case still matches the Python it came from."""
    cases = [c for c in golden["cases"] if c["kind"] == "edge_ranks"]
    covered = {(c["sweep"]["sequence"], c["sweep"]["edge"], c["sweep"]["edge_random"])
               for c in cases}
    for seq in ("top_down", "left_right"):
        for edge in sequence.EDGES:
            for random in (False, True):
                assert (seq, edge, random) in covered
    assert any(key.startswith("back|") for c in cases for key in c["expect"])
    fixtures = ROOT / "tests" / "fixtures" / "sim"
    for c in cases:
        m = LookMap.parse(io.StringIO(fixtures.joinpath(c["map"]).read_text(
            encoding="utf-8")), name=c["opts"]["name"], item=c["opts"]["item"])
        got = sequence.edge_ranks(m, c["sweep"])
        assert {f"{s}|{r}|{col}": v for (s, r, col), v in got.items()} == c["expect"]
        assert sequence.rank_set(c["sweep"]) == c["rankSet"]


def test_canonical_and_digest_match_a_recorded_table():
    """A pinned table (computed once, by hand-inspecting the output;
    mg.CANONICAL_TABLE is the same list make_goldens.py turns into the
    golden "canonical" cases, so there is exactly one list of inputs)
    that both tools/make_goldens.py's canonical()/digest64() and
    model.js's SIM.fmt.canonical()/digest64() must reproduce - the
    browser self-test is the real cross-check; this catches a
    regression in the Python half even without a browser."""
    expect = [
        ("0", "af63ad4c86019caf"), ("5", "af63a84c86019430"), ("-5", "07d00f07b497d7f7"),
        ("2.500", "ac5b7dc41134559c"), ("0.500", "d45564b0f53aea22"),
        ('"a"', "d4272417d7c77eea"), ('"a\\"b"', "aea405f405787fda"),
        ("true", "5b5c98ef514dbfa5"), ("false", "b5fae2c14238b978"),
        ("null", "5b9bc4ba528108e4"), ('[1,2,"x"]', "6893ac5ba04fa4f2"),
        ('{"a":2,"b":1}', "f85f5878cbf2dc03"), ('"\\u65e5\\u672c\\u8a9e"', "9a5893e1cdc4beb6"),
    ]
    assert len(expect) == len(mg.CANONICAL_TABLE)
    for value, (expect_canonical, expect_digest) in zip(mg.CANONICAL_TABLE, expect):
        got_canonical = mg.canonical(value)
        assert got_canonical == expect_canonical, value
        assert mg.digest64(got_canonical) == expect_digest, value


def _find_browser() -> "str | None":
    env = os.environ.get("CONDUCTOR_BROWSER")
    candidates = [env] if env else []
    candidates += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("msedge"),
        shutil.which("microsoft-edge"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _standalone_harness() -> str:
    """A tiny self-contained page (no designer.html, no UI) that inlines
    model.js + state.js + goldens.js + selftest.js in dependency order -
    until Q's dist/az27ss-simulator.html exists, this is what proves
    Coder P's branch is testable standalone (plan section 6)."""
    parts = []
    for name in ("model.js", "state.js", "goldens.js", "selftest.js"):
        text = (SIM_DIR / name).read_text(encoding="utf-8")
        assert "</script" not in text.lower(), f"{name} contains a literal </script"
        parts.append(f"<script>\n{text}\n</script>")
    body = "\n".join(parts)
    return f"<!doctype html>\n<html><head><meta charset=\"utf-8\"></head><body>\n{body}\n</body></html>\n"


def test_browser_selftest_passes(tmp_path):
    browser = _find_browser()
    forced = os.environ.get("CONDUCTOR_BROWSER_TESTS") == "1"
    if not browser:
        if forced:
            pytest.fail("CONDUCTOR_BROWSER_TESTS=1 but no browser was found "
                       "(set $CONDUCTOR_BROWSER to its path)")
        pytest.skip("no Edge/Chrome found - set CONDUCTOR_BROWSER_TESTS=1 to force")

    html_path = tmp_path / "selftest_harness.html"
    html_path.write_text(_standalone_harness(), encoding="utf-8")
    url = "file:///" + str(html_path.resolve()).replace("\\", "/") + "#selftest"
    user_data_dir = tmp_path / "user-data"
    args = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
           f"--user-data-dir={user_data_dir}", "--virtual-time-budget=20000",
           "--dump-dom", url]
    result = subprocess.run(args, capture_output=True, timeout=60)
    dom = result.stdout.decode("utf-8", errors="replace")
    match = re.search(r'data-ok="(true|false)" data-total="(\d+)" data-failed="(\d+)"', dom)
    assert match, f"no #selftest-out found in the dumped DOM (rc={result.returncode}):\n{dom[:3000]}\n" \
                  f"stderr: {result.stderr.decode('utf-8', errors='replace')[:2000]}"
    ok, total, failed = match.group(1), int(match.group(2)), int(match.group(3))
    if ok != "true":
        detail = re.search(r'<pre id="selftest-out"[^>]*>(.*?)</pre>', dom, re.S)
        pytest.fail(f"browser self-test failed ({failed}/{total}):\n"
                   f"{detail.group(1) if detail else dom[:3000]}")
    assert total > 900          # sanity: the goldens really did load and run


# The edge rank cache (2026-10-23, STATUS 3.0 item 2): state.js used to rank
# every edged sweep twice per rebuild (spanS(), then item.sweep_ranks) and
# again on the next rebuild. Now once per map file and rank_set. Node, not a
# browser: model.js and state.js are classic scripts that only need
# globalThis, the way tests/test_export3d.py loads model.js.
_NODE_EDGE_CACHE = r"""
const root = process.argv[2];
require(root + "/conductor/web/sim/model.js");
require(root + "/conductor/web/sim/state.js");
const fs = require("fs");
const fix = root + "/tests/fixtures/sim/";
const mapText = fs.readFileSync(fix + "AZ271SD1301_map.csv", "utf8");
const design = fs.readFileSync(fix + "AZ271SD1301_1_HW.csv", "utf8");
function project(map, seed, boards) {
  return { files: { "AZ271SD1301_map.csv": map, "AZ271SD1301_1_HW.csv": design },
    show: { duration: 120, refresh_s: 9, labels: {}, boards: boards || {}, cues: [
      { id: "1", item: "AZ271SD1301", at: 0, design: "AZ271SD1301_1_HW.csv",
        transition: "custom", sequence: "top_down", span_s: 2, edge: "wave", edge_seed: seed },
      { id: "2", item: "AZ271SD1301", at: 30, design: "AZ271SD1301_1_HW.csv" }],
    transitions: { "AZ271SD1301_1_HW.csv": { sequence: "left_right", span_s: 1, edge: "fray" } } } };
}
const stats = SIM.sequence.edgeRankStats;
const count = (fn) => { const before = stats.computed; const st = fn(); return [stats.computed - before, st]; };
const ranksOf = (st) => st.items[0].sweep_ranks;
const [first, a] = count(() => SIM.buildState(project(mapText, 1)));
const [again, b] = count(() => SIM.buildState(project(mapText, 1)));
const [reseeded, c] = count(() => SIM.buildState(project(mapText, 2)));
// seed 1 was not asked for by that build, so its ranks were let go
const [unseeded] = count(() => SIM.buildState(project(mapText, 1)));
// the board renumbering is part of what makes the map: a new store
const renumbering = { AZ271SD1301: { "92": 200 } };
const [renumbered, r] = count(() => SIM.buildState(project(mapText, 1, renumbering)));
// one scale moved up a row: a different map, so nothing may be reused
const lines = mapText.split("\n");
const parts = lines[1].split(","); parts[1] = String(Number(parts[1]) + 40); lines[1] = parts.join(",");
const [edited, d] = count(() => SIM.buildState(project(lines.join("\n"), 1)));
const [back, e] = count(() => SIM.buildState(project(mapText, 1)));
// the uncached ranks, straight from edgeRanksByKey(), in map.scales order
const parsed = SIM.look.parseMap(mapText, { name: "AZ271SD1301_map.csv", item: "AZ271SD1301" }).map;
const plainOn = (m, sweep) => { const byKey = SIM.sequence.edgeRanksByKey(m, sweep); return m.scales.map(s => byKey[s.key]); };
const plain = (sweep) => plainOn(parsed, sweep);
const renumberedMap = SIM.look.renumber(parsed, { 92: 200 });
const sweepOf = (st, id) => st.show.cues.find(cu => cu.id === id).sweep;
process.stdout.write(JSON.stringify({
  first, again, reseeded, unseeded, renumbered, edited, back,
  renumberedBoard: r.items[0].map.scales.some(sc => sc[3] === 200),
  renumberedUncached: JSON.stringify(ranksOf(r)[sweepOf(r, "1").rank_set]) === JSON.stringify(plainOn(renumberedMap, sweepOf(r, "1")))
            && JSON.stringify(ranksOf(r)[sweepOf(r, "2").rank_set]) === JSON.stringify(plainOn(renumberedMap, sweepOf(r, "2"))),
  sets: Object.keys(ranksOf(a)).sort(),
  same: JSON.stringify(ranksOf(a)) === JSON.stringify(ranksOf(b))
        && JSON.stringify(ranksOf(a)) === JSON.stringify(ranksOf(e)),
  editedDiffers: JSON.stringify(ranksOf(a)) !== JSON.stringify(ranksOf(d)),
  spans: [a, b, c, d, e].map(st => st.show.cues.map(cu => cu.span)),
  uncached: JSON.stringify(ranksOf(a)[sweepOf(a, "1").rank_set]) === JSON.stringify(plain(sweepOf(a, "1")))
            && JSON.stringify(ranksOf(a)[sweepOf(a, "2").rank_set]) === JSON.stringify(plain(sweepOf(a, "2"))),
  // the span plays no part in the key: a sweep of span 0 is timed from the same ranks
  identity: SIM.sequence.cachedEdgeRanksByKey(parsed, { sequence: "center", edge: "wave_fray", span_s: 0 })
            === SIM.sequence.cachedEdgeRanksByKey(parsed, { sequence: "center", edge: "wave_fray", span_s: 3 }),
}));
"""


def test_edge_ranks_are_computed_once_per_map_and_rank_set(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    script = tmp_path / "edge_cache.js"
    script.write_text(_NODE_EDGE_CACHE, encoding="utf-8")
    run = subprocess.run([node, str(script), str(ROOT)], capture_output=True, text=True,
                         encoding="utf-8", check=True)
    out = json.loads(run.stdout)
    assert out["sets"] == ["left_right/fray/6/fixed/1/1", "top_down/wave/6/fixed/1/1"], out
    # the cue's sweep and the design's own: two rankings, each done once
    # (spanS() and item.sweep_ranks share it), none on an unchanged rebuild
    assert out["first"] == 2 and out["again"] == 0, out
    assert out["reseeded"] == 1, out             # only the cue's new seed is new
    assert out["unseeded"] == 1, out             # the old seed's ranks were pruned
    # a show.boards renumbering is in the map's signature (ownBoards)
    assert out["renumbered"] > 0 and out["renumberedBoard"] is True, out
    assert out["renumberedUncached"] is True, out
    assert out["edited"] == 2 and out["editedDiffers"] is True, out
    assert out["back"] == 2, out                 # the old map's store went with its map
    assert out["same"] is True and out["uncached"] is True and out["identity"] is True, out
    assert out["spans"][0] == out["spans"][1] == out["spans"][4] == [2, 1], out
