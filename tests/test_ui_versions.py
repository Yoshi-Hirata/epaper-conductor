"""Firmware inventory: the FW VERSION menu row and ui/versions.py.

Boards are the fake OTA bus from the update tests, answering 0x29 the
way V1.0 (ACK_INVALID_CMD) and V1.1 (state + size + crc) firmware do,
so the scan, the labelling against the bundled images, and the screen
flow are covered without a wall.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import ota
from epaper.protocol import ACK_INVALID_CMD, ACK_SUCCESS, Frame
from ui import render
from ui.app import App, Screen
from ui.config import HEIGHT, WIDTH
from ui.display import NullDisplay
from ui.inputs import ScriptedInput
from ui.patterns import PATTERNS
from ui.versions import DONE, IDLE, SCANNING, BoardVersions
from tests.test_ui_app import FakeRunner
from tests.test_ui_runner import wait_until
from tests.test_ui_update import FakeOtaBus

NEW = b"\x01" * 300          # stands in for FW_260917
OLD = b"\x02" * 200          # stands in for FW_260903


def make_catalog(tmp_path: Path) -> dict:
    (tmp_path / "FW_260903").mkdir(exist_ok=True)
    (tmp_path / "FW_260903" / "OTA_16c.bin").write_bytes(OLD)
    (tmp_path / "FW_260917").mkdir(exist_ok=True)
    (tmp_path / "FW_260917" / "MCB_e16.bin").write_bytes(NEW)
    (tmp_path / "FW_260917" / "MCB_e16.hex").write_bytes(b":00")
    return ota.bundled_images(tmp_path)


def make_versions(tmp_path, bus=None, boards=(1, 2, 7, 20), **kwargs):
    kwargs.setdefault("open_bus", lambda port: bus or FakeOtaBus())
    kwargs.setdefault("locate", lambda: "/dev/fake")
    kwargs.setdefault("echo_log", False)
    kwargs.setdefault("catalog", make_catalog(tmp_path))
    kwargs.setdefault("firmware_dir", tmp_path)   # "bundled" from the same tree
    kwargs.setdefault("scan", lambda b, boards, progress=None: ota.scan(
        b, boards, timeout=0.02, progress=progress))
    return BoardVersions(boards=list(boards), **kwargs)


def make_app(versions):
    runner = FakeRunner()
    app = App(NullDisplay(), ScriptedInput(()), runner, port_label="/dev/fake",
              versions=versions, host="radxa-01")
    return app, runner


def wall(tmp_path):
    """Four boards on the 485, plus silence at every other address."""
    return FakeOtaBus(answers={1, 2, 7, 20})


def alone(addr, firmware=None, v10=False, v14=True):
    """One board on USB, 485 unplugged: the only case 0x29 (and 0x25) is
    sent. V1.4 boards answer 0x25; V1.1 boards refuse it."""
    special = ACK_INVALID_CMD if v10 else (
        ota.image_fingerprint(firmware) if firmware else None)
    return FakeOtaBus(answers={addr},
                      per_addr={addr: special} if special else {},
                      nak={} if v14 else {0x25: ACK_INVALID_CMD})


# ---- host/ota.py ----

def test_bundled_images_are_keyed_by_size_and_crc(tmp_path):
    catalog = make_catalog(tmp_path)
    assert catalog[ota.image_fingerprint(NEW)] == "FW_260917"
    assert catalog[ota.image_fingerprint(OLD)] == "FW_260903"
    assert len(catalog) == 2                 # the .hex is not an image


def test_repo_catalog_names_both_shipped_images():
    # The archived builds (FW/archive/, 2026-11-20) are still named.
    catalog = ota.bundled_images(Path(__file__).resolve().parents[1] / "FW")
    assert {"FW_260903", "FW_260917"} <= set(catalog.values())
    assert set(catalog.values()) == {"FW_260903", "FW_260917", "FW_260923", "FW_261109",
                                     "FW_261118", "FW_261118B", "FW_261119", "FW_261119D",
                                     "FW_261119F"}


def test_repo_catalog_names_subfolder_images_and_keeps_every_key_unique():
    # 2026-11-19: FW_260923's image sits in the vendor subfolder
    # fw2029.09.23/ and was missing; FW_261118B (home-made trial) has the
    # same size, so the two must still get their own (size, crc) keys.
    fw_dir = Path(__file__).resolve().parents[1] / "FW"
    catalog = ota.bundled_images(fw_dir)
    images = sorted(fw_dir.rglob("*.bin"))              # offered and archived
    assert len(images) == 10 and len(catalog) == len(images)   # no key collides
    arch = fw_dir / "archive"
    prod = ota.image_fingerprint(
        (fw_dir / "FW_260923" / "fw2029.09.23" / "MCB_16_0923.bin").read_bytes())
    trial = ota.image_fingerprint(
        (arch / "FW_261118B" / "mcb_0923_pinks_no13.bin").read_bytes())
    assert prod[0] == trial[0] and prod != trial
    assert catalog[prod] == "FW_260923"
    assert catalog[trial] == "FW_261118B"
    # Plan C (2026-11-20) has the same size as the FW_261118 program it keeps.
    prog = ota.image_fingerprint(
        (arch / "FW_261118" / "mcb_two_pinks(0a,0c).bin").read_bytes())
    plan_c = ota.image_fingerprint(
        (arch / "FW_261119" / "mcb_1118prog_seq0923_pinks_no13.bin").read_bytes())
    assert prog[0] == plan_c[0] and prog[1] != plan_c[1]
    assert (catalog[prog], catalog[plan_c]) == ("FW_261118", "FW_261119")
    # Plan D (2026-11-20): the third image of that size.
    plan_d = ota.image_fingerprint(
        (arch / "FW_261119D" / "mcb_1118prog_pinks_no13_endalign.bin").read_bytes())
    assert plan_d[0] == prog[0] and len({prog[1], plan_c[1], plan_d[1]}) == 3
    assert catalog[plan_d] == "FW_261119D"
    # The final image (2026-11-20): the fourth of that size.
    final = ota.image_fingerprint(
        (fw_dir / "FW_261119F" / "mcb_pinks_0a-purple_0c-red.bin").read_bytes())
    assert final[0] == prog[0] and len({prog[1], plan_c[1], plan_d[1], final[1]}) == 4
    assert catalog[final] == "FW_261119F"


def test_archived_builds_are_named_and_nested_archive_folders_too(tmp_path):
    (tmp_path / "FW_260923").mkdir()
    (tmp_path / "FW_260923" / "a.bin").write_bytes(b"\x0a" * 30)
    (tmp_path / "archive" / "FW_260917" / "sub").mkdir(parents=True)
    (tmp_path / "archive" / "FW_260917" / "sub" / "b.bin").write_bytes(b"\x0b" * 30)
    (tmp_path / "archive" / "loose.bin").write_bytes(b"\x0c" * 30)    # no FW_ folder
    assert ota.bundled_images(tmp_path) == {
        ota.image_fingerprint(b"\x0a" * 30): "FW_260923",
        ota.image_fingerprint(b"\x0b" * 30): "FW_260917"}


def test_nested_fw_folders_are_named_like_the_updater_labels_them(tmp_path):
    from ui import updater as updater_mod
    nested = tmp_path / "FW_261201" / "FW_261202" / "sub"
    nested.mkdir(parents=True)
    (nested / "x.bin").write_bytes(b"\x07" * 40)
    catalog = ota.bundled_images(tmp_path)
    assert catalog == {ota.image_fingerprint(b"\x07" * 40): "FW_261202"}
    label = updater_mod.firmware_label(nested / "x.bin")
    assert label.split("/")[0] == "FW_261202"


def test_subfolder_images_are_named_by_their_fw_folder(tmp_path):
    (tmp_path / "FW_260923" / "fw2029.09.23").mkdir(parents=True)
    (tmp_path / "FW_260923" / "fw2029.09.23" / "MCB_16_0923.bin").write_bytes(b"\x03" * 50)
    (tmp_path / "FW_260923" / "fw2029.09.23" / "MCB_16_0923.hex").write_bytes(b":00")
    catalog = make_catalog(tmp_path)
    assert catalog[ota.image_fingerprint(b"\x03" * 50)] == "FW_260923"
    assert len(catalog) == 3


def test_identify_names_the_firmware_from_the_0x29_answer(tmp_path):
    catalog = make_catalog(tmp_path)
    size, crc = ota.image_fingerprint(NEW)
    ack = Frame(dest=0, src=1, dev_type=0xFF, cmd=ACK_SUCCESS,
                data=bytes([0]) + size.to_bytes(4, "little")
                + crc.to_bytes(2, "little"))
    assert ota.identify(ack, catalog) == "FW_260917"
    blank = Frame(dest=0, src=1, dev_type=0xFF, cmd=ACK_SUCCESS,
                  data=bytes(7))
    assert ota.identify(blank, catalog) == "V1.1 16-color, build unknown"
    other = Frame(dest=0, src=1, dev_type=0xFF, cmd=ACK_SUCCESS,
                  data=bytes([1]) + (1234).to_bytes(4, "little")
                  + (0xBEEF).to_bytes(2, "little"))
    assert ota.identify(other, catalog) == "V1.1 size=1234 crc=0xBEEF RECEIVING"
    v10 = Frame(dest=0, src=1, dev_type=0xFF, cmd=ACK_INVALID_CMD)
    assert ota.identify(v10, catalog) == "V1.0 6-color (no OTA)"
    assert ota.identify(None, catalog) == "no reply"


def test_scan_reports_progress_per_address():
    bus = FakeOtaBus(answers={7})
    seen = []
    found = ota.scan(bus, [1, 7, 20], timeout=0.02,
                     progress=lambda addr, ack: seen.append((addr, ack is not None)))
    assert list(found) == [7]
    assert seen == [(1, False), (7, True), (20, False)]


# ---- the worker ----

def test_lone_usb_board_is_identified(tmp_path):
    for bus, label in ((alone(2, firmware=NEW), "FW_260917"),
                       (alone(2, firmware=OLD), "FW_260903"),
                       (alone(2), "V1.4 16-color (FW_260923+), no flash record here"),
                       (alone(2, v14=False), "V1.1 16-color, no flash record here"),
                       (alone(2, v10=True), "V1.0 6-color (no OTA)")):
        versions = make_versions(tmp_path, bus=bus)
        assert versions.phase == IDLE
        versions.scan()
        assert wait_until(lambda: versions.phase == DONE)
        assert versions.rows == [(2, label)]
        assert versions.status == "1/4 board answers"
        assert versions.asked == 4
        assert any(f"board 02: {label}" in line for line in versions.recent(10))
    assert versions.bundled == "FW_260917"


def test_a_shared_bus_is_listed_but_nobody_is_asked_0x29(tmp_path):
    bus = wall(tmp_path)
    versions = make_versions(tmp_path, bus=bus)
    versions.scan()
    assert wait_until(lambda: versions.phase == DONE)
    assert versions.rows == [(addr, BoardVersions.ON_BUS) for addr in (1, 2, 7, 20)]
    assert versions.status == "4/4 on bus: unplug 485 to identify"
    assert all(f.cmd != ota.CMD_OTA_QUERY for f in bus.sent)


def test_silent_wall_and_missing_port_are_said_on_screen(tmp_path):
    quiet = make_versions(tmp_path, bus=FakeOtaBus(answers=set()))
    quiet.scan()
    assert wait_until(lambda: quiet.phase == DONE)
    assert quiet.rows == [] and quiet.status == "no board answers"
    unplugged = make_versions(tmp_path, locate=lambda: None)
    unplugged.scan()
    assert wait_until(lambda: unplugged.phase == DONE)
    assert unplugged.status == "no serial port"


def test_scroll_is_clamped_to_the_rows(tmp_path):
    versions = make_versions(tmp_path)
    versions.rows = [(n, "x") for n in range(1, 16)]
    versions.phase = DONE
    versions.scroll(-1, 10)
    assert versions.offset == 0
    for _ in range(20):
        versions.scroll(+1, 10)
    assert versions.offset == 5


def test_menu_entry_names_the_address_range(tmp_path):
    versions = make_versions(tmp_path, boards=range(1, 21))
    assert versions.menu_entry.key == "versions"
    assert versions.menu_entry.label == "FW VERSION"
    assert "01-20" in versions.menu_entry.detail


# ---- the App ----

def test_versions_row_exists_only_with_a_worker(tmp_path):
    plain = App(NullDisplay(), ScriptedInput(()), FakeRunner())
    assert [p.key for p in plain.patterns] == [p.key for p in PATTERNS]
    app, _ = make_app(make_versions(tmp_path))
    assert app.patterns[-1].key == "versions"


def test_entering_stops_the_runner_scans_and_key2_leaves_without_repaint(tmp_path):
    versions = make_versions(tmp_path, bus=wall(tmp_path))
    app, runner = make_app(versions)
    app.select("versions")
    app.handle("key1")
    assert app.screen is Screen.VERSIONS
    assert runner.stops == 1
    assert wait_until(lambda: versions.phase == DONE)
    assert len(versions.rows) == 4
    app.handle("key2")
    assert app.screen is Screen.MENU
    assert runner.standbys == 0 and runner.starts == []


def test_buttons_wait_for_the_scan_then_key1_rescans(tmp_path):
    release = threading.Event()
    calls = {"scans": 0}

    def slow_scan(bus, boards, progress=None):
        calls["scans"] += 1
        release.wait(5.0)
        return {}

    versions = make_versions(tmp_path, scan=slow_scan)
    app, runner = make_app(versions)
    app.select("versions")
    app.handle("key1")
    assert wait_until(lambda: versions.phase == SCANNING)
    for event in ("key2", "key1", "down", "key1_hold"):
        app.handle(event)
    assert app.screen is Screen.VERSIONS
    assert runner.standbys == 0 and runner.starts == []
    release.set()
    assert wait_until(lambda: versions.phase == DONE)
    app.handle("key1")
    assert wait_until(lambda: calls["scans"] == 2)


def test_screen_redraws_as_boards_answer(tmp_path):
    versions = make_versions(tmp_path)
    versions.phase = DONE
    app, _ = make_app(versions)
    app.screen = Screen.VERSIONS
    app.draw()
    before = app.display.frames
    versions.rows.append((3, "FW_260917"))
    app.tick(wait=0.0)
    assert app.display.frames == before + 1
    app.tick(wait=0.0)
    assert app.display.frames == before + 1


# ---- rendering ----

def test_versions_screen_renders_every_phase_and_scrolls():
    rows = [(n, "FW_260917" if n % 2 else "FW_260903") for n in range(1, 21)]
    for phase in ("idle", "scanning", "done"):
        image = render.versions_screen(rows, "20/20 boards answer", phase,
                                       "FW_260917", host="radxa-01")
        assert image.size == (WIDTH, HEIGHT)
    top = render.versions_screen(rows, "", "done", "FW_260917", offset=0)
    down = render.versions_screen(rows, "", "done", "FW_260917", offset=10)
    assert top.tobytes() != down.tobytes()
    empty = render.versions_screen([], "no serial port", "done", "none")
    assert empty.size == (WIDTH, HEIGHT)


def test_a_result_landing_mid_paint_is_still_painted(tmp_path, monkeypatch):
    # The scan thread finished while the UI thread was rendering the
    # "01 answers..." frame; the key taken after the paint already held
    # the final rows, so nothing ever repainted (radxa-01, 2026-09-17).
    versions = make_versions(tmp_path)
    versions.phase = SCANNING
    versions.rows = [(1, "answers...")]
    versions.status = "scanning 3/20..."
    app, _ = make_app(versions)
    app.screen = Screen.VERSIONS

    real = render.versions_screen

    def slow_paint(rows, status, phase, *args, **kwargs):
        image = real(rows, status, phase, *args, **kwargs)
        if versions.phase == SCANNING:          # the worker lands now
            versions.rows = [(1, "V1.1 16-color, build unknown")]
            versions.status = "1/20 board answers"
            versions.phase = DONE
        return image

    monkeypatch.setattr(render, "versions_screen", slow_paint)
    app.draw()                                 # painted "answers..."
    before = app.display.frames
    app.tick(wait=0.0)
    assert app.display.frames == before + 1    # the result gets its paint
    app.tick(wait=0.0)
    assert app.display.frames == before + 1    # and then it is stable


def test_wrap_keeps_every_word_within_the_width():
    from ui.render import FONT_S, _wrap
    text = "V1.1, flashed FW_260917 09-17 08:19 on 485 bus - unplug 485 to identify"
    lines = _wrap(text, FONT_S, 198)
    assert len(lines) >= 2
    assert all(FONT_S.getlength(line) <= 198 for line in lines)
    assert " ".join(lines) == text                 # nothing dropped
    assert _wrap("", FONT_S, 198) == [""]
    long_word = "x" * 120
    pieces = _wrap(long_word, FONT_S, 60)
    assert "".join(pieces) == long_word            # cut, not lost
    assert all(FONT_S.getlength(piece) <= 60 for piece in pieces)


def test_long_labels_wrap_on_the_versions_screen_and_page():
    rows = [(n, "V1.1, flashed FW_260917 09-17 08:19") for n in range(1, 21)]
    image = render.versions_screen(rows, "USB 48EC7570324C", "done",
                                   "FW_260917", host="radxa-01")
    assert image.size == (WIDTH, HEIGHT)
    # Two-line rows fit fewer per page than one-liners, and the scroll
    # step floor is sized for that worst case.
    assert render.VERSION_ROWS <= 5
    later = render.versions_screen(rows, "USB 48EC7570324C", "done",
                                   "FW_260917", offset=render.VERSION_ROWS)
    assert later.tobytes() != image.tobytes()


def test_0x25_tells_v1_4_from_v1_1_and_goes_only_to_the_lone_usb_board(tmp_path):
    # V1.4: 0x25 answered; V1.1: refused; the 485 crowd is never asked.
    bus = alone(2)
    versions = make_versions(tmp_path, bus=bus)
    versions.scan()
    assert wait_until(lambda: versions.phase == DONE)
    clears = [f for f in bus.sent if f.cmd == 0x25]
    assert [f.dest for f in clears] == [2] and clears[0].data == bytes([19])
    assert versions.rows[0][1].startswith("V1.4")
    bus = wall(tmp_path)
    versions = make_versions(tmp_path, bus=bus)
    versions.scan()
    assert wait_until(lambda: versions.phase == DONE)
    assert [f for f in bus.sent if f.cmd in (0x25, ota.CMD_OTA_QUERY)] == []


def test_a_v1_4_board_whose_record_is_older_is_flagged(tmp_path):
    from ui import flashlog

    log = tmp_path / "flash.json"
    flashlog.record("48EA604B324C", 2, "FW_260917/MCB_e16_2029.09.17.bin", 1, 2, path=log)
    versions = make_versions(tmp_path, bus=alone(2), flash_log=log,
                             serial_of=lambda port: "48EA604B324C")
    versions.scan()
    assert wait_until(lambda: versions.phase == DONE)
    label = versions.rows[0][1]
    assert label.startswith("V1.4") and "FW_260917" in label and "older than the board" in label


# ---- "bundled" = the image UPDATE FW writes by default (2026-11-19) ----

def test_bundled_is_the_update_fw_default_not_the_newest_trial():
    # 2026-11-20: the released FW_261119F is the UPDATE FW default, so it
    # is "bundled" and its rows are green; FW_260923 (the way back) not.
    from ui import versions as versions_mod
    assert versions_mod.bundled_folder() == "FW_261119F"    # the real FW tree
    v = BoardVersions(catalog={}, echo_log=False)
    assert v.bundled == "FW_261119F"
    assert render.runs_bundled("FW_261119F", v.bundled)
    assert render.runs_bundled("V1.4 16-color (FW_260923+), flashed FW_261119F 11-20 10:00",
                               v.bundled)
    for other in ("FW_260923", "FW_261119", "FW_261119D",
                  "V1.4 16-color (FW_260923+), flashed FW_260923 09-24 10:00",
                  "V1.4 16-color (FW_260923+), no flash record here",
                  "V1.1 16-color, flashed FW_261118B 11-19 12:00"):
        assert not render.runs_bundled(other, v.bundled)
    assert render.runs_bundled("FW_261119F RECEIVING", v.bundled)


def test_bundled_matches_whole_folder_names_only(tmp_path):
    from ui import updater as updater_mod
    from ui import versions as versions_mod
    (tmp_path / "FW_261118").mkdir()
    (tmp_path / "FW_261118" / "a.bin").write_bytes(b"a")
    (tmp_path / "FW_261118B").mkdir()
    (tmp_path / "FW_261118B" / "b.bin").write_bytes(b"b")
    (tmp_path / "FW_261118B" / updater_mod.TRIAL_MARKER).write_text("")
    assert versions_mod.bundled_folder(tmp_path) == "FW_261118"
    assert render.runs_bundled("FW_261118", "FW_261118")
    assert render.runs_bundled("V1.1 16-color, flashed FW_261118 11-18 09:00", "FW_261118")
    assert not render.runs_bundled("FW_261118B", "FW_261118")
    assert not render.runs_bundled("V1.1 16-color, flashed FW_261118B 11-19 12:00",
                                   "FW_261118")


def test_bundled_falls_back_to_the_newest_trial_and_to_none(tmp_path):
    from ui import updater as updater_mod
    from ui import versions as versions_mod
    assert versions_mod.bundled_folder(tmp_path) == "none"
    for folder in ("FW_261109", "FW_261118B"):
        (tmp_path / folder / "sub").mkdir(parents=True)
        (tmp_path / folder / "sub" / "t.bin").write_bytes(b"t")
        (tmp_path / folder / updater_mod.TRIAL_MARKER).write_text("")
    assert versions_mod.bundled_folder(tmp_path) == "FW_261118B"
    v = make_versions(tmp_path)          # make_catalog adds FW_260903/260917
    assert v.bundled == "FW_260917"


def test_the_versions_screen_greens_only_the_bundled_rows():
    def green(label):
        image = render.versions_screen([(1, label)], "", "done", "FW_260923")
        colours = image.convert("RGB").getcolors(maxcolors=1 << 16)
        return sum(n for n, rgb in colours if rgb == render.OK)
    base = green("V1.1 16-color, no flash record here")
    assert green("FW_260923") > base
    assert green("FW_261118B") == base
    assert green("V1.4 16-color (FW_260923+), no flash record here") == base
