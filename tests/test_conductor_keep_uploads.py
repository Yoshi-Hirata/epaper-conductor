"""The show PC's "keep_uploads_on_restart" (fleet.json, 2026-11-20).

Kunitachi (2026-11-22): the show data is Uploaded in the morning near the
router, the garments are dressed and power-cycled, and at the venue only
START is sent. The units keep the show and its burn record across power
cuts; the PC Conductor knew who holds what only in memory, so a restart of
the show PC cost an Upload at the venue. With the option on, serve() makes
EXHIBITION's startup offer (offer_startup_shows) on the show PC too - but
start_present stays off, and every gate an ordinary Upload + edit meets
still applies.

Most of it runs through serve() itself, with a Fleet that never polls (the
test plays the polls by calling _supervise) and units that are StubLinks -
no network beyond localhost, fleet.json pointed at 127.0.0.1:9. Review
round 1 (Codex + adversarial, 2026-11-20) added: no adoption into a run
recovered from the units, the offer's generation against a withdrawal, the
clear-after-show flag, START's per-unit reasons, the Loop's revision check.
"""
from __future__ import annotations

import io
import json
import threading

import pytest

import conductor.server as srv
from conductor.fleet import OFFER_STALE, Fleet
from conductor.server import Workspace, kept_uploads_waiting
from tests.test_conductor_exhibition import Clock, _post
from tests.test_fleet import StubLink
from tests.test_look import GRID, MAP

UNITS = ("radxa-01", "radxa-02")
ADOPTED = "holds this show already (adopted after a restart of the conductor)"


def _workspace(root, **options):
    """Two garments, one per unit, and a fleet.json that reaches nothing."""
    ws = Workspace(root)
    cues = []
    for look, unit in (("Look23", "radxa-01"), ("Look24", "radxa-02")):
        ws.save(f"{look}_map.csv", MAP)
        ws.save(f"{look}_color_ivory_grid.csv", GRID)
        ws.save(f"{look}_color_scarlet_grid.csv", GRID)
        ws.assign(look, unit)
        cues += [{"id": f"{look}p", "item": look, "at": 0,
                  "design": f"{look}_color_ivory_grid.csv"},
                 {"id": f"{look}c", "item": look, "at": 60,
                  "design": f"{look}_color_scarlet_grid.csv"}]
    ws.set_timeline(120, cues)
    fleet_json = {"units": {f"radxa-{n:02d}": "127.0.0.1:9" for n in range(1, 11)}}
    fleet_json.update(options)
    (ws.root / "fleet.json").write_text(json.dumps(fleet_json), encoding="utf-8")
    return ws


def _set_options(ws, **options):
    data = json.loads((ws.root / "fleet.json").read_text(encoding="utf-8"))
    data.update(options)
    (ws.root / "fleet.json").write_text(json.dumps(data), encoding="utf-8")


def _holding(compiled, burn="burned", ids=None):
    """Stub units reporting `compiled`'s ids (or `ids`), pictures `burn`."""
    links = {}
    for name in UNITS:
        link = StubLink(name, "loaded")
        show_id = (ids or {}).get(name, compiled[name]["id"])
        link.status["show"].update(id=show_id, burn={"state": burn})
        links[name] = link
    return links


def _restart(monkeypatch, ws, links, scenario):
    """serve() on `ws` as after a restart of the show PC: the units are
    `links`, nobody polls, and `scenario(port, fleet, workspace)` runs
    while the server answers."""
    seen = {}
    base = srv._Server

    class QuietFleet(Fleet):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.links = links

        def start(self):                # the test plays the polls
            pass

    class Once(base):
        def serve_forever(self, poll_interval=0.5):
            handler = self.RequestHandlerClass
            thread = threading.Thread(target=base.serve_forever,
                                      args=(self, 0.05), daemon=True)
            thread.start()
            try:
                seen["result"] = scenario(self.server_address[1], handler.fleet,
                                          handler.workspace)
            finally:
                self.shutdown()
                thread.join(5)

    monkeypatch.setattr(srv, "Fleet", QuietFleet)
    monkeypatch.setattr(srv, "_Server", Once)
    monkeypatch.setattr(srv, "already_serving", lambda port: False)
    monkeypatch.setattr(srv, "other_conductor_port", lambda port, label: None)
    assert srv.serve(ws.root, port=0) == 0
    return seen.get("result")


def _poll(fleet):
    for link in fleet.links.values():
        if link.online:
            fleet._supervise(link)


def _notes(fleet):
    return "\n".join(fleet.corrections)


# ---- off (the default): a restart costs an Upload, exactly as before ----

@pytest.mark.parametrize("options", [{}, {"keep_uploads_on_restart": False}])
def test_off_a_restart_still_says_upload_first(tmp_path, monkeypatch, options):
    ws = _workspace(tmp_path / "ws", **options)
    compiled, _ = ws.compile_show()

    def scenario(port, fleet, workspace):
        assert fleet._offered == {} and fleet.start_present is False
        assert fleet.keep_uploads is False
        _poll(fleet)
        assert fleet.shows == {} and ADOPTED not in _notes(fleet)
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 200
        assert answer == {"units": {}, "note": "Nothing uploaded yet - Upload first."}
        assert fleet.run is None and workspace.unit_marks.get("upload") is None
        return True

    assert _restart(monkeypatch, ws, _holding(compiled), scenario)


# ---- on: adopted, START runs with no Upload, PC semantics kept ----

def test_on_the_units_holding_the_show_are_adopted_and_start_runs(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)

    def scenario(port, fleet, workspace):
        assert set(fleet._offered) == set(UNITS)
        assert fleet.start_present is False         # the show PC's START
        assert fleet.keep_uploads is True           # ...and its offer's rules
        _poll(fleet)
        assert fleet.shows == compiled
        for name in UNITS:
            assert f"{name}: {ADOPTED}" in _notes(fleet)
        # Marked as holding the startup compile's revision, as an Upload
        # marks them: the page's chips say "up to date", the tiles ✓.
        rev = workspace.revision()
        assert workspace.unit_marks["upload"] == dict.fromkeys(UNITS, rev)
        written = workspace.written_state()
        assert written["uploaded"] == rev == written["revision"]
        snap = fleet.snapshot()
        assert {u: s["id"] for u, s in snap["shows"].items()} == {
            u: s["id"] for u, s in compiled.items()}
        # The beacon announces them (their ids are this Conductor's own
        # compile, confirmed by the units) - as on EXHIBITION --adopt.
        assert fleet.beacon_view()["shows"] == {u: s["id"] for u, s in compiled.items()}
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 200 and answer.get("from_s") == 0.0, answer
        assert fleet.run is not None
        runs = {name: body for name, link in links.items()
                for path, body in link.posted if path == "/show/run"}
        assert {u: b["show"] for u, b in runs.items()} == {
            u: s["id"] for u, s in compiled.items()}
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_a_garment_that_does_not_answer_still_refuses_start(tmp_path, monkeypatch):
    """start_present stays off: no EXHIBITION "start with who is there" -
    and the refusal names the garment as waiting, never "Upload for All
    LOOKs" (review round 1, point 4 / A)."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    held = links["radxa-02"].status
    links["radxa-02"].online = False                # never heard since the restart
    links["radxa-02"].status = None

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-01"}
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 400, answer
        assert answer["error"] == kept_uploads_waiting(
            {"radxa-02": "waiting for its first reply"}), answer
        assert "Upload" not in answer["error"]
        assert fleet.run is None
        # Adopted, then gone quiet: the burn gate refuses it as after an Upload.
        links["radxa-02"].status = held
        links["radxa-02"].online = True
        fleet._supervise(links["radxa-02"])
        assert set(fleet.shows) == set(UNITS)
        links["radxa-02"].online = False
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 400 and "radxa-02: not answering" in answer["error"], answer
        assert fleet.run is None and fleet.start_present is False
        return True

    assert _restart(monkeypatch, ws, links, scenario)


@pytest.mark.parametrize("burn, other, reason", [
    ("burned", True, "holds another show - Upload"),
    ("none", False, "pictures not written - Upload"),
    ("cleared", False, "pictures cleared - Upload"),
    ("burning", False, "still writing its pictures - wait"),
])
def test_on_a_unit_not_holding_exactly_this_show_is_not_adopted(
        tmp_path, monkeypatch, burn, other, reason):
    """The poll's correction and START's answer give the same advice
    (Fleet._offer_reason) - never "Upload" for a unit still writing."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    links["radxa-02"].status["show"].update(
        id="0ldsh0w000" if other else compiled["radxa-02"]["id"], burn={"state": burn})

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-01"}
        assert f"radxa-02: {reason}" in _notes(fleet)
        assert "Upload before START" not in _notes(fleet)
        assert "radxa-02" not in workspace.unit_marks["upload"]
        # START's own answer carries the reason, from the unit's state now
        # (the corrections are capped at 20 lines).
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 400, answer
        assert answer["error"] == kept_uploads_waiting({"radxa-02": reason})
        assert fleet.run is None
        assert not [p for p, _ in links["radxa-02"].posted if p == "/show/run"]
        return True

    assert _restart(monkeypatch, ws, links, scenario)


@pytest.mark.parametrize("workspace_clears", [True, False])
def test_on_a_different_clear_after_show_is_not_adopted(tmp_path, monkeypatch,
                                                        workspace_clears):
    """Left out of the show id, so the id cannot tell: the unit's own flag
    is compared with what this workspace compiles (review round 1, point 3)."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    ws.set_clear_after_show(workspace_clears)
    compiled, _ = ws.compile_show()
    assert bool(compiled["radxa-01"].get("clear_after_show")) is workspace_clears
    links = _holding(compiled)
    links["radxa-01"].status["show"]["clear_after_show"] = workspace_clears
    links["radxa-02"].status["show"]["clear_after_show"] = not workspace_clears

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-01"}
        assert ("radxa-02: 'Clear pictures after the show' differs - Upload"
                in _notes(fleet))
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 400 and answer["error"] == kept_uploads_waiting(
            {"radxa-02": "'Clear pictures after the show' differs - Upload"}), answer
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_nothing_adopted_start_does_not_say_nothing_uploaded(tmp_path, monkeypatch):
    """Every unit on another show (or not answered yet): START refuses with
    a note that does not send the operator to Upload at the venue for a
    unit that simply has not answered since the restart."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled, ids=dict.fromkeys(UNITS, "0ldsh0w000"))

    def scenario(port, fleet, workspace):
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        expected = kept_uploads_waiting(dict.fromkeys(UNITS, "holds another show - Upload"))
        assert status == 200 and answer == {"units": {}, "note": expected}
        assert expected == ("Not confirmed since the Conductor restarted: "
                            "radxa-01, radxa-02 (holds another show - Upload).")
        _poll(fleet)
        assert fleet.shows == {}
        assert _notes(fleet).count("holds another show - Upload") == 2
        _poll(fleet)                                # said once, not every poll
        assert _notes(fleet).count("holds another show - Upload") == 2
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert answer["note"] == expected and fleet.run is None
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_before_any_reply_start_says_wait_not_upload(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    links["radxa-01"].status["show"]["burn"] = {"state": "burning", "done": 3, "total": 9}
    links["radxa-02"].status, links["radxa-02"].online = None, False

    def scenario(port, fleet, workspace):
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert answer == {"units": {}, "note": (
            "Not confirmed since the Conductor restarted: radxa-01 (still writing "
            "its pictures - wait); radxa-02 (waiting for its first reply).")}, answer
        assert fleet.run is None
        return True

    assert _restart(monkeypatch, ws, links, scenario)


# ---- edits: after the restart refused, before the restart never adopted ----

def test_on_an_edit_after_the_restart_reads_changed_since_and_refuses_start(
        tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert fleet.shows == compiled
        before = workspace.revision()
        workspace.set_timeline(90, [{"id": "p", "item": "Look23", "at": 0,
                                     "design": "Look23_color_scarlet_grid.csv"},
                                    {"id": "q", "item": "Look24", "at": 0,
                                     "design": "Look24_color_scarlet_grid.csv"}])
        written = workspace.written_state()
        assert written["revision"] != before
        # "changed since": the per-unit marks are the startup revision.
        assert set(written["uploaded_units"].values()) == {before}
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 400 and "Upload again" in answer["error"], answer
        assert fleet.run is None
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_an_edit_before_the_restart_adopts_nothing(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    uploaded, _ = ws.compile_show()                 # what the units hold
    ws.set_timeline(100, ws._load_show()["cues"])   # edited, then the restart
    links = _holding(uploaded)

    def scenario(port, fleet, workspace):
        assert all(fleet._offered[u]["id"] != uploaded[u]["id"] for u in UNITS)
        _poll(fleet)
        assert fleet.shows == {} and workspace.unit_marks.get("upload") is None
        assert _notes(fleet).count("holds another show - Upload") == 2
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert answer == {"units": {}, "note": kept_uploads_waiting(
            dict.fromkeys(UNITS, "holds another show - Upload"))} and fleet.run is None
        return True

    assert _restart(monkeypatch, ws, links, scenario)


# ---- an Upload or an import withdraws the offer ----

def test_on_an_upload_after_the_restart_withdraws_the_offer(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled, ids={"radxa-02": "0ldsh0w000"})

    def scenario(port, fleet, workspace):
        assert set(fleet._offered) == set(UNITS)
        status, answer = _post(port, "/api/fleet/upload", {"units": ["radxa-01"]})
        assert status == 200, answer
        assert fleet._offered == {}
        # radxa-02 now reports this show burned (someone else's Upload, a
        # late burn): no longer adopted - the Upload is the truth.
        links["radxa-02"].status["show"]["id"] = compiled["radxa-02"]["id"]
        _poll(fleet)
        assert "radxa-02" not in fleet.shows
        assert ADOPTED not in _notes(fleet)
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_an_import_after_the_restart_withdraws_the_offer(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    links["radxa-02"].online = False                # not adopted before the import
    other = _workspace(tmp_path / "other")
    packed = io.BytesIO()
    other.export_tar(packed)

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-01"} and "radxa-02" in fleet._offered
        status, answer = _post(port, "/api/workspace/import", packed.getvalue())
        assert status == 200, answer
        assert fleet._offered == {} and fleet.shows == {}
        links["radxa-02"].online = True
        _poll(fleet)
        assert fleet.shows == {}
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert answer == {"units": {}, "note": "Nothing uploaded yet - Upload first."}
        return True

    assert _restart(monkeypatch, ws, links, scenario)


# ---- fleet.json: strictly true or false; EXHIBITION's adopt unchanged ----

@pytest.mark.parametrize("value", ["yes", 1, "true", None, [True]])
def test_a_bad_value_warns_and_stays_off(tmp_path, monkeypatch, capsys, value):
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=value)
    compiled, _ = ws.compile_show()

    def scenario(port, fleet, workspace):
        return dict(fleet._offered)

    assert _restart(monkeypatch, ws, _holding(compiled), scenario) == {}
    assert ('Warning: fleet.json: "keep_uploads_on_restart" must be true or false '
            f"(got {value!r}) - it stays off; a restart of this Conductor then "
            "needs an Upload") in capsys.readouterr().out


def test_exhibition_adopt_is_unchanged(tmp_path, monkeypatch):
    ws = _workspace(tmp_path / "ws", adopt=True)
    compiled, _ = ws.compile_show()

    def scenario(port, fleet, workspace):
        return set(fleet._offered), fleet.start_present

    assert _restart(monkeypatch, ws, _holding(compiled), scenario) == (set(UNITS), True)
    # Both on: EXHIBITION wins (adopt sets start_present).
    _set_options(ws, keep_uploads_on_restart=True)
    assert _restart(monkeypatch, ws, _holding(compiled), scenario) == (set(UNITS), True)
    # The option alone: offered, start_present off.
    _set_options(ws, adopt=False)
    assert _restart(monkeypatch, ws, _holding(compiled), scenario) == (set(UNITS), False)


def test_a_malformed_show_json_still_serves(tmp_path, monkeypatch):
    """The startup revision is read inside the guard (review round 1, F)."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    (ws.root / "show.json").write_text("[1, 2]", encoding="utf-8")

    def scenario(port, fleet, workspace):
        return dict(fleet._offered)

    assert _restart(monkeypatch, ws, _holding(compiled), scenario) == {}


# ---- a run recovered from the units is fed nothing until STOP (rounds 1-2) ----

def test_on_a_restart_mid_run_stays_passive_until_stop(tmp_path, monkeypatch):
    """radxa-01 still runs YESTERDAY's show when the show PC comes back;
    radxa-02 sits idle holding today's. Nothing of the offer reaches that
    run: radxa-02 is not adopted (set aside if a poll took it before the
    run was found), no supervision post, no beacon entry (the units report
    hostnames, so an empty beacon proves the suppression), the run's
    clear-after-show is main's - and after STOP the offer is taken; once
    both hold today's show START runs and the beacon announces both."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    ws.set_clear_after_show(True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    for name, link in links.items():
        link.status["host"] = name
        link.status["show"]["clear_after_show"] = True
    links["radxa-01"].status["show"].update(id="yesterd4y0", state="running")

    def scenario(port, fleet, workspace):
        # radxa-02's first poll lands before anything asked for the run...
        fleet._supervise(links["radxa-02"])
        assert set(fleet.shows) == {"radxa-02"}
        assert set(workspace.unit_marks["upload"]) == {"radxa-02"}
        # ...the page's first /api/fleet finds the run on the units.
        run = fleet.snapshot()["run"]
        assert run and run["adopted"]
        assert fleet.shows == {} and workspace.unit_marks["upload"] == {}
        assert ("radxa-02: set aside - a show was found running on the units; "
                "confirmed after STOP") in _notes(fleet)
        assert fleet.run["clear_after_show"] is False   # nothing uploaded: main's
        before = len(fleet.corrections)
        for _ in range(3):
            _poll(fleet)
        assert fleet.shows == {}
        assert all(link.posted == [] for link in links.values())
        assert len(fleet.corrections) == before + 1     # radxa-01's advice, once
        view = fleet.beacon_view()
        assert view["state"] == "running"
        assert view["entries"] == {} and view["shows"] == {}
        assert view["skipped"]["radxa-02"] == (
            "its show is not confirmed by this Conductor since it restarted - "
            "confirmed after STOP")
        assert sorted(fleet._targets()) == sorted(UNITS)    # main's widening, no more
        assert fleet.offer_waiting() == {"radxa-01": "holds another show - Upload",
                                         "radxa-02": "confirmed after STOP"}
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1, "force": True})
        assert answer["note"] == kept_uploads_waiting(fleet.offer_waiting()), answer
        status, answer = _post(port, "/api/fleet/stop", {})
        assert status == 200, answer
        assert fleet.run is None
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-02"}
        assert workspace.unit_marks["upload"] == {"radxa-02": workspace.revision()}
        # radxa-01 is given today's show (its own Upload, say): both held.
        links["radxa-01"].status["show"].update(id=compiled["radxa-01"]["id"],
                                                state="loaded")
        _poll(fleet)
        assert set(fleet.shows) == set(UNITS)
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 200 and answer.get("from_s") == 0.0, answer
        assert set(fleet.beacon_view()["entries"]) == set(UNITS)
        return True

    assert _restart(monkeypatch, ws, links, scenario)


def test_on_a_recovered_run_never_reaches_an_offered_unit_that_never_answered():
    """_targets() during a run recovered from the units: exactly what it is
    with no offer at all - an offered unit never heard from is not added
    (HOLD / RESUME / NEXT / STOP reach the same units as without the
    option)."""
    names = ("radxa-01", "radxa-02", "radxa-03")
    compiled = {u: {"id": f"id-{u}", "cues": [], "duration": 100.0} for u in names}

    def fleet_of(**kw):
        fleet = Fleet({}, clock=Clock(1050.0), **kw)
        fleet.links = {u: StubLink(u, "loaded") for u in names}
        fleet.links["radxa-01"].status["show"].update(id="yesterd4y0", state="running",
                                                      burn={"state": "burned"})
        fleet.links["radxa-02"].status["show"].update(id="id-radxa-02",
                                                      burn={"state": "burned"})
        fleet.links["radxa-03"].status = None           # never answered
        fleet.links["radxa-03"].online = False
        return fleet

    plain = fleet_of()                                  # no option, no offer
    plain._adopt()
    kept = fleet_of(keep_uploads=True)
    kept.offer_shows(compiled)
    kept._adopt()
    assert kept.run["adopted"] and plain.run["adopted"]
    for link in kept.links.values():
        if link.online:
            kept._supervise(link)
    assert kept.shows == {}
    assert sorted(kept._targets()) == sorted(plain._targets()) == ["radxa-01", "radxa-02"]
    kept.hold()
    assert sorted(n for n, link in kept.links.items()
                  for p, _ in link.posted if p == "/show/hold") == ["radxa-01", "radxa-02"]
    assert kept.offer_waiting()["radxa-03"] == "waiting for its first reply"


def test_exhibition_offers_exactly_as_before():
    """EXHIBITION (start_present) - and any Fleet not told keep_uploads -
    adopts as it always did: an idle unit holding the offered show is taken
    even while a run recovered from the units plays, offered names stay in
    _targets(), the corrections keep their words, no Loop revision check."""
    compiled = {u: {"id": f"id-{u}", "cues": [], "duration": 100.0} for u in UNITS}
    for kw in ({"start_present": True}, {}, {"start_present": True, "keep_uploads": True}):
        fleet = Fleet({}, clock=Clock(1050.0), **kw)
        assert fleet.keep_uploads is False
        fleet.links = {u: StubLink(u, "loaded") for u in UNITS}
        fleet.links["radxa-01"].status["show"].update(id="yesterd4y0", state="running",
                                                      burn={"state": "burned"})
        fleet.links["radxa-02"].status["show"].update(id="id-radxa-02",
                                                      burn={"state": "burned"})
        fleet.offer_shows(compiled, current=lambda: False)
        fleet._adopt()
        assert fleet.run["adopted"]
        for link in fleet.links.values():
            fleet._supervise(link)
        assert set(fleet.shows) == {"radxa-02"}
        assert "radxa-01: another show - Upload before START" in _notes(fleet)
        assert sorted(fleet._targets()) == sorted(UNITS)
        assert not fleet._from_offer


def test_the_advice_is_one_function():
    """Fleet._offer_reason: what START answers, the poll's correction and the
    beacon's skip all say (PM, round 2)."""
    clock = Clock(1050.0)
    offered = {"id": "today00000", "cues": [], "duration": 100.0}
    fleet = Fleet({}, clock=clock, keep_uploads=True)
    link = StubLink("radxa-01", "loaded")
    fleet.links = {"radxa-01": link}
    fleet.offer_shows({"radxa-01": offered})

    def advice(**show):
        link.status = {"show": dict({"id": "today00000", "state": "loaded"}, **show)}
        return fleet._offer_reason("radxa-01", offered)

    link.status, link.online = None, False
    assert fleet._offer_reason("radxa-01", offered) == "waiting for its first reply"
    link.status, link.online = {"show": None}, False
    assert fleet._offer_reason("radxa-01", offered) == "not answering"
    link.online = True
    link.status = {"show": None}
    assert fleet._offer_reason("radxa-01", offered) == "holds no show - Upload"
    assert advice(id="other00000", burn={"state": "burned"}) == "holds another show - Upload"
    assert advice(burn={"state": "burned"}, clear_after_show=True) == \
        "'Clear pictures after the show' differs - Upload"
    assert advice(burn={"state": "burning", "done": 2, "total": 9}) == \
        "still writing its pictures - wait"
    assert advice(burn={"state": "cleared"}) == "pictures cleared - Upload"
    assert advice(burn={"state": "none"}) == "pictures not written - Upload"
    assert advice(burn={"state": "burned"}) == "being confirmed - wait"
    fleet.run = {"t0": 1000.0, "state": "running", "adopted": True}
    for state in ("burned", "failed"):                   # whatever the burn
        assert advice(burn={"state": state}) == "confirmed after STOP"


def test_on_one_snapshot_decides_start_and_its_advice(tmp_path, monkeypatch):
    """A poll adopting radxa-02 lands after START read its state, before the
    advice: the gate and the advice both come from the one snapshot, so
    radxa-02 is never called "not on this upload - Upload for All LOOKs"
    (Codex, round 2). The next START runs."""
    ws = _workspace(tmp_path / "ws", keep_uploads_on_restart=True)
    compiled, _ = ws.compile_show()
    links = _holding(compiled)
    links["radxa-02"].status["show"]["burn"] = {"state": "burning"}

    def scenario(port, fleet, workspace):
        _poll(fleet)
        assert set(fleet.shows) == {"radxa-01"}
        links["radxa-02"].status["show"]["burn"] = {"state": "burned"}
        real = fleet.offer_waiting

        def poll_lands_between(pending=None):
            fleet._supervise(links["radxa-02"])         # adopted right now
            return real(pending)

        fleet.offer_waiting = poll_lands_between
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert set(fleet.shows) == set(UNITS)
        assert status == 400, answer
        assert answer["error"] == kept_uploads_waiting(
            {"radxa-02": "being confirmed - wait"}), answer
        assert "Upload for All LOOKs" not in answer["error"]
        fleet.offer_waiting = real
        status, answer = _post(port, "/api/fleet/start", {"lead_s": 1})
        assert status == 200 and answer.get("from_s") == 0.0, answer
        return True

    assert _restart(monkeypatch, ws, links, scenario)


# ---- round 2: no adoption while a START is being decided or staged ----

def test_no_adoption_while_a_start_is_being_decided():
    compiled = {"radxa-01": {"id": "id-1", "cues": [], "duration": 100.0}}
    fleet = Fleet({}, keep_uploads=True)
    fleet.links = {"radxa-01": StubLink("radxa-01", "loaded")}
    fleet.links["radxa-01"].status["show"].update(id="id-1", burn={"state": "burned"})
    fleet.offer_shows(compiled)
    fleet._starting = 1
    fleet._supervise(fleet.links["radxa-01"])
    assert fleet.shows == {}
    assert "radxa-01: being confirmed - wait" in _notes(fleet)
    fleet._starting = 0
    fleet._supervise(fleet.links["radxa-01"])
    assert fleet.shows == compiled


def test_no_adoption_during_a_preset_stage_or_its_handoff(tmp_path):
    """Through the real PRESET handoff: a unit that becomes ready during the
    stage is not taken - not during the stage, and not in the window
    between the end of the stage and the final START's targets (a poll
    landing there, injected as start_show is entered) - and is taken by the
    first poll after."""
    from conductor.fleet import PRESET_WAIT_MAX_S

    ws = _workspace(tmp_path / "ws")
    compiled, _ = ws.compile_show()
    clock = Clock(1000.0)
    fleet = Fleet({}, clock=clock, keep_uploads=True)
    fleet.links = _holding(compiled)
    fleet.links["radxa-02"].status["show"]["burn"] = {"state": "burning"}
    fleet.offer_shows(compiled)
    for link in fleet.links.values():
        fleet._supervise(link)
    assert set(fleet.shows) == {"radxa-01"}
    results = fleet.start_show(1.0, preset_first=True, skip={"radxa-02"})
    assert results and fleet._staging is not None
    fleet.links["radxa-02"].status["show"]["burn"] = {"state": "burned"}
    fleet._supervise(fleet.links["radxa-02"])
    assert "radxa-02" not in fleet.shows                # the stage decided
    seen = {}
    real_start = fleet.start_show

    def poll_lands_first(*args, **kwargs):
        fleet._supervise(fleet.links["radxa-02"])       # a poll in the handoff
        seen["adopted"] = "radxa-02" in fleet.shows
        seen["starting"] = fleet._starting
        return real_start(*args, **kwargs)

    fleet.start_show = poll_lands_first
    clock.now += PRESET_WAIT_MAX_S + 1.0
    fleet._staging_tick()
    assert seen == {"adopted": False, "starting": 1}
    assert fleet.run is not None and fleet._staging is None and fleet._starting == 0
    fleet._supervise(fleet.links["radxa-02"])
    assert "radxa-02" in fleet.shows


# ---- review round 1: the offer's generation against a withdrawal ----

class PausedLink(StubLink):
    """A StubLink whose next status read waits for the test - a poll
    suspended between reading the offer and committing the adoption."""

    def __init__(self, *args, **kwargs):
        self.pause = None
        super().__init__(*args, **kwargs)

    @property
    def status(self):
        if self.pause is not None:
            reading, go = self.pause
            self.pause = None
            reading.set()
            go.wait(5)
        return self._status

    @status.setter
    def status(self, value):
        self._status = value


@pytest.mark.parametrize("withdraw", ["upload", "import"])
def test_a_withdrawal_during_a_poll_leaves_no_stale_adoption(withdraw):
    compiled = {u: {"id": f"id-{u}", "cues": [], "duration": 100.0} for u in UNITS}
    fleet = Fleet({}, keep_uploads=True)
    fleet.links = {u: PausedLink(u, "loaded") for u in UNITS}
    for u in UNITS:
        fleet.links[u].status["show"].update(id=compiled[u]["id"], burn={"state": "burned"})
    marked = []
    fleet.offer_shows(compiled, on_adopt=marked.append)
    link = fleet.links["radxa-01"]
    reading, go = threading.Event(), threading.Event()
    link.pause = (reading, go)
    poll = threading.Thread(target=fleet._adopt_show, args=(link,))
    poll.start()
    assert reading.wait(5)
    if withdraw == "upload":
        fleet.upload(compiled, only=["radxa-02"])       # a one-LOOK Upload
    else:
        fleet.forget_shows()                            # what an import calls
    go.set()
    poll.join(5)
    assert "radxa-01" not in fleet.shows and marked == []
    fleet._supervise(link)
    assert "radxa-01" not in fleet.shows and marked == []


def test_a_failed_mark_keeps_the_unit_out_until_the_next_poll():
    compiled = {"radxa-01": {"id": "id-1", "cues": [], "duration": 100.0}}
    fleet = Fleet({}, keep_uploads=True)
    fleet.links = {"radxa-01": StubLink("radxa-01", "loaded")}
    fleet.links["radxa-01"].status["show"].update(id="id-1", burn={"state": "burned"})
    calls = []

    def flaky(unit):
        calls.append(unit)
        if len(calls) == 1:
            raise RuntimeError("disk full")

    fleet.offer_shows(compiled, on_adopt=flaky)
    fleet._supervise(fleet.links["radxa-01"])
    assert fleet.shows == {}
    assert ("radxa-01: could not mark the adoption (disk full) - tried again on "
            "its next reply") in _notes(fleet)
    fleet._supervise(fleet.links["radxa-01"])
    assert fleet.shows == compiled and calls == ["radxa-01", "radxa-01"]


def test_two_first_polls_never_mark_at_once():
    compiled = {u: {"id": f"id-{u}", "cues": [], "duration": 100.0} for u in UNITS}
    fleet = Fleet({}, keep_uploads=True)
    fleet.links = {u: StubLink(u, "loaded") for u in UNITS}
    for u in UNITS:
        fleet.links[u].status["show"].update(id=compiled[u]["id"], burn={"state": "burned"})
    active, most = [0], [0]
    gate = threading.Lock()

    def slow_mark(unit):
        with gate:
            active[0] += 1
            most[0] = max(most[0], active[0])
        threading.Event().wait(0.05)
        with gate:
            active[0] -= 1

    fleet.offer_shows(compiled, on_adopt=slow_mark)
    polls = [threading.Thread(target=fleet._supervise, args=(link,))
             for link in fleet.links.values()]
    for poll in polls:
        poll.start()
    for poll in polls:
        poll.join(5)
    assert set(fleet.shows) == set(UNITS) and most[0] == 1


# ---- review round 1: a Loop restart checks the workspace again ----

def test_a_loop_restart_over_offered_shows_checks_the_workspace():
    clock = Clock()
    compiled = {u: {"id": f"id-{u}", "cues": [], "duration": 100.0} for u in UNITS}
    fresh = [True]
    fleet = Fleet({}, clock=clock, loop_settings=lambda: (40.0, 2.0), keep_uploads=True)
    fleet.links = {u: StubLink(u, "loaded") for u in UNITS}
    for u in UNITS:
        fleet.links[u].status["show"].update(id=compiled[u]["id"], burn={"state": "burned"})
    fleet.offer_shows(compiled, current=lambda: fresh[0])
    for link in fleet.links.values():
        fleet._supervise(link)
    assert set(fleet.shows) == set(UNITS)
    fleet.start_show(lead_s=1.0)                    # the operator's START
    first = fleet.run["t0"]
    clock.now = first + 100.0
    fleet._loop_tick()                              # the end: the wait is armed
    fresh[0] = False                                # ...and the timeline edited
    clock.now += 40.0
    fleet._loop_tick()
    assert fleet.run["t0"] == first and fleet.run["loops"] == 0
    assert f"Loop: cannot start again yet ({OFFER_STALE})" in _notes(fleet)
    # The end of a staged START asks the same (expect_gen set).
    with pytest.raises(ValueError, match="edited since the Conductor restarted"):
        fleet.start_show(1.0, expect_gen=fleet._run_gen)
    # An Upload makes the shows the Upload's: no longer checked.
    fleet.upload(compiled)
    clock.now += fleet.loop_retry_s + 1.0
    fleet._loop_tick()
    assert fleet.run["loops"] == 1 and fleet.run["t0"] != first
