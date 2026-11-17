"""CLEAR BOARD: the LCD row that sends V1.4's 0x15 (clear all colours) or
0x05 (clear power-on autoplay) to one board (ui/boardclear.py, 2026-11-17).

Driven through a fake bus: the frames, the sweep, the two-hold
confirmation, the verdicts, and the App's refusals while a show holds
the unit - without a board on the bench.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import serial

import ota
from epaper.commands import clear_all_colors, clear_autoplay
from epaper.protocol import (ACK_FAIL, ACK_INVALID_CMD, ACK_SUCCESS,
                             CMD_CLEAR_ALL, CMD_CLEAR_AUTOPLAY,
                             CMD_FACTORY_RESET, CMD_PLAY_STOP, Frame,
                             crc16_modbus)
from ui import boardclear, render
from ui.app import CLEAR_LOCKED_LOADED, CLEAR_LOCKED_PICTURES, App, Screen
from ui.boardclear import (ARM_S, CLEAR_TRIES, DONE, FAILED, IDLE,
                           BoardClearer)
from ui.config import HEIGHT, WIDTH
from ui.display import NullDisplay
from ui.inputs import ScriptedInput
from ui.showplay import LOADED, RUNNING
from tests.test_ui_app import FakeRunner
from tests.test_ui_boardinfo import FakeRemote
from tests.test_ui_runner import wait_until
from tests.test_ui_update import FakeOtaBus, make_image, make_updater
from tests.test_ui_wifi import FakePlayer


# ---- fakes ----

class ClearBus(FakeOtaBus):
    """FakeOtaBus (PLAY_STOP answered ACK_SUCCESS by whoever is present)
    plus a chosen answer to the clearing commands: (ack code, data), or
    None for silence."""

    def __init__(self, reply=(ACK_SUCCESS, b""), **kwargs):
        super().__init__(**kwargs)
        self.reply = reply

    def send(self, frame: Frame) -> None:
        if frame.cmd not in (CMD_CLEAR_ALL, CMD_CLEAR_AUTOPLAY):
            super().send(frame)
            return
        self.sent.append(frame)
        if self.reply is not None and (self.answers is None
                                       or frame.dest in self.answers):
            code, data = self.reply
            self._pending.append(self._ack(frame, code, data))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_clearer(bus=None, boards=(1, 2, 20), answers=(1, 2), **kwargs):
    bus = bus or ClearBus(answers=answers)
    clock = kwargs.pop("clock", Clock())
    kwargs.setdefault("open_bus", lambda port: bus)
    kwargs.setdefault("locate", lambda: "/dev/fake")
    # The real sweep, with a short silence per absent address.
    kwargs.setdefault("find", lambda b, boards: ota.find_boards(
        b, boards, timeout=0.03))
    clearer = BoardClearer(boards=list(boards), clock=clock, echo_log=False,
                           **kwargs)
    return clearer, bus, clock


def settle(clearer):
    assert wait_until(lambda: not clearer.busy)


def scanned(clearer):
    clearer.scan()
    settle(clearer)
    return clearer


def cleared_frames(bus):
    return [f for f in bus.sent if f.cmd in (CMD_CLEAR_ALL, CMD_CLEAR_AUTOPLAY)]


def make_app(clearer, remote=None, player=None, updater=None):
    runner = FakeRunner()
    app = App(NullDisplay(), ScriptedInput(()), runner, port_label="/dev/fake",
              updater=updater, clearer=clearer, remote=remote, player=player,
              host="radxa-01")
    return app, runner


def enter(app):
    app.select("clear")
    app.handle("key1")
    if app.screen is Screen.CLEAR:
        settle(app.clearer)


# ---- the frames ----

def test_clear_all_colors_frame_follows_the_v1_4_example():
    frame = clear_all_colors(1)
    raw = frame.encode()
    assert frame.cmd == CMD_CLEAR_ALL == 0x15 and frame.data == b""
    assert raw[:2] == b"\xaa\x55" and raw[-2:] == b"\x55\xaa"
    # Dst Src | Cmd Len - GN is the board, as every _frame() builder has it.
    assert (raw[2], raw[3], raw[5], raw[6], raw[7]) == (0x01, 0x00, 0x15, 0x00, 0x01)
    assert len(raw) == 16
    crc = crc16_modbus(raw[2:12])
    assert raw[12] == crc & 0xFF and raw[13] == crc >> 8
    # The group fields are ours (H_WALL_BRICKS type, 2 groups, 1 chip);
    # with the spec example's own (type 00, GC 01, CC 02) it is the
    # example byte for byte: AA 55 01 00 00 15 00 01 01 02 00 00 XX XX 55 AA.
    spec = dataclasses.replace(frame, dev_type=0, group_count=1, chip_count=2)
    assert spec.encode()[:12] == bytes.fromhex("AA5501000015000101020000")


def test_clear_autoplay_frame_follows_the_v1_4_example():
    frame = clear_autoplay(1)
    assert frame.cmd == CMD_CLEAR_AUTOPLAY == CMD_FACTORY_RESET == 0x05
    assert frame.data == b""
    spec = dataclasses.replace(frame, dev_type=0, group_count=1, chip_count=2)
    raw = spec.encode()
    assert raw[:12] == bytes.fromhex("AA5501000005000101020000")
    crc = crc16_modbus(raw[2:12])
    assert raw[12:] == bytes([crc & 0xFF, crc >> 8, 0x55, 0xAA])
    # Unicast to whichever board, with the group count and type given.
    other = clear_autoplay(7, group_count=21, dev_type=3)
    assert (other.dest, other.group_no, other.group_count, other.dev_type) == (7, 7, 21, 3)


# ---- the sweep and the choice ----

def test_menu_entry_and_actions():
    clearer, _, _ = make_clearer()
    assert clearer.menu_entry.key == "clear"
    assert clearer.menu_entry.label == "CLEAR BOARD"
    assert [a.label for a in boardclear.ACTIONS] == [
        "CLEAR COLOURS (0x15)", "CLEAR AUTOPLAY (0x05)"]


def test_the_sweep_lists_who_answers_and_up_down_pick_only_among_them():
    clearer, bus, _ = make_clearer(answers=(2, 20))
    scanned(clearer)
    assert {f.cmd for f in bus.sent} == {CMD_PLAY_STOP}   # relay-safe, nothing else
    assert clearer.found == [2, 20] and clearer.addr == 2
    assert clearer.choice == "1/2"
    assert clearer.status == "boards 02,20 answer"
    clearer.select(+1)
    assert clearer.addr == 20
    clearer.select(+1)
    assert clearer.addr == 2                    # wraps; 1 never answered
    clearer.select(-1)
    assert clearer.addr == 20


def test_left_right_switch_the_command():
    clearer, _, _ = make_clearer()
    assert clearer.action.key == "colours"
    clearer.select_action(+1)
    assert clearer.action.key == "autoplay"
    clearer.select_action(+1)
    assert clearer.action.key == "colours"
    clearer.select_action(-1)
    assert clearer.action.key == "autoplay"


def test_no_board_no_port_and_a_wedged_port_leave_nothing_to_send():
    clearer, bus, _ = make_clearer(answers=())
    scanned(clearer)
    assert clearer.found == [] and clearer.addr is None
    assert clearer.status == "no board answers - KEY1 rescans"
    clearer.confirm()
    clearer.confirm()
    assert clearer.phase == IDLE and cleared_frames(bus) == []

    clearer, _, _ = make_clearer(locate=lambda: None)
    scanned(clearer)
    assert clearer.addr is None and clearer.status == "no serial port"

    class Wedged:
        def __enter__(self):
            raise serial.SerialTimeoutException("write timeout")

        def __exit__(self, *exc):
            pass
    clearer, _, _ = make_clearer(open_bus=lambda port: Wedged())
    scanned(clearer)
    assert clearer.status == "port wedged (write timeout)"


# ---- the confirmation ----

def test_one_hold_only_arms_and_the_second_sends_stop_then_the_command():
    clearer, bus, _ = make_clearer(answers=(1,))
    scanned(clearer)
    bus.sent.clear()
    clearer.confirm()
    assert clearer.armed_text() == "hold KEY1 again: CLEAR COLOURS board 01"
    assert bus.sent == [] and clearer.phase == IDLE
    clearer.confirm()
    settle(clearer)
    assert [f.cmd for f in bus.sent] == [CMD_PLAY_STOP, CMD_CLEAR_ALL]
    assert all(f.dest == 1 for f in bus.sent)
    assert bus.sent[1].group_count == 20       # the runner's count for 1,2,20
    assert clearer.phase == DONE
    assert clearer.status == "board 01: ACK_SUCCESS"
    assert clearer.armed_text() == ""
    log = " | ".join(clearer.recent(10))
    assert "board 01: CLEAR COLOURS (0x15)" in log
    assert "board 01 stop 0x17: ACK_SUCCESS" in log
    assert "board 01 CLEAR COLOURS (0x15): ACK_SUCCESS" in log
    assert "ERROR" not in log


def test_the_arming_lapses_and_any_change_of_choice_disarms():
    clearer, bus, clock = make_clearer()
    scanned(clearer)
    bus.sent.clear()
    clearer.confirm()
    clock.now += ARM_S + 0.1
    assert clearer.armed_text() == ""
    clearer.confirm()                           # re-arms, does not send
    assert clearer.armed_text() and cleared_frames(bus) == []
    clearer.select(+1)                          # another board
    assert clearer.armed_text() == ""
    clearer.confirm()                           # arms board 02
    clearer.select_action(+1)                   # another command
    assert clearer.armed_text() == ""
    clearer.confirm()
    assert clearer.armed_text() == "hold KEY1 again: CLEAR AUTOPLAY board 02"
    clearer.disarm()
    clearer.confirm()
    assert cleared_frames(bus) == []
    clearer.confirm()
    settle(clearer)
    assert [(f.cmd, f.dest) for f in cleared_frames(bus)] == [(CMD_CLEAR_AUTOPLAY, 2)]


# ---- the verdicts ----

def sent_and_settled(clearer, action_step=0):
    scanned(clearer)
    clearer.select_action(action_step)
    clearer.confirm()
    clearer.confirm()
    settle(clearer)


def test_ack_fail_is_the_hardware_error_and_is_logged_as_an_error():
    clearer, _, _ = make_clearer(bus=ClearBus(answers=(1,), reply=(ACK_FAIL, b"\x06")))
    sent_and_settled(clearer)
    assert clearer.phase == FAILED
    assert clearer.status.startswith("board 01: ACK_FAIL from 0x01")
    assert "hardware error" in clearer.status
    assert any("ERROR" in line and "ACK_FAIL" in line
               for line in clearer.recent(5))


def test_old_firmware_refusing_0x05_is_named():
    clearer, _, _ = make_clearer(bus=ClearBus(answers=(1,), reply=(ACK_FAIL, b"\x0a")))
    sent_and_settled(clearer, action_step=1)
    assert clearer.phase == FAILED
    assert clearer.status.endswith("firmware older than V1.3?")
    clearer, _, _ = make_clearer(bus=ClearBus(answers=(1,), reply=(ACK_INVALID_CMD, b"")))
    sent_and_settled(clearer)
    assert "ACK_INVALID_CMD" in clearer.status


def test_no_answer_is_a_failure_after_the_retries(monkeypatch):
    monkeypatch.setattr(boardclear, "CLEAR_ACK_S", 0.05)
    bus = ClearBus(answers=(1,), reply=None)
    clearer, _, _ = make_clearer(bus=bus)
    sent_and_settled(clearer)
    assert clearer.phase == FAILED
    assert clearer.status == "board 01: no answer"
    assert len(cleared_frames(bus)) == CLEAR_TRIES


def test_a_port_that_vanished_before_the_send_fails_cleanly():
    clearer, bus, _ = make_clearer(answers=(1,))
    scanned(clearer)
    clearer.port = None
    clearer._locate = lambda: None
    clearer.confirm()
    clearer.confirm()
    settle(clearer)
    assert clearer.phase == FAILED and clearer.status == "ERROR no serial port"


def test_a_verdict_stays_until_read_and_the_choice_is_kept():
    clearer, _, _ = make_clearer(answers=(1, 2))
    sent_and_settled(clearer)
    assert clearer.finished
    clearer.confirm()                           # a hold on the verdict: nothing
    assert clearer.phase == DONE and clearer.armed_text() == ""
    clearer.reset()
    assert clearer.phase == IDLE and clearer.status == ""
    assert clearer.found == [1, 2] and clearer.addr == 1


# ---- the App ----

def test_the_row_sits_right_after_update_fw_and_only_with_a_clearer(tmp_path):
    updater, _ = make_updater(make_image(tmp_path))
    clearer, _, _ = make_clearer()
    app, _ = make_app(clearer, updater=updater)
    keys = [p.key for p in app.patterns]
    assert keys[keys.index("update") + 1] == "clear"
    plain, _ = make_app(None)
    assert "clear" not in [p.key for p in plain.patterns]


def test_entering_stops_the_runner_and_sweeps():
    clearer, bus, _ = make_clearer(answers=(1,))
    app, runner = make_app(clearer)
    enter(app)
    assert app.screen is Screen.CLEAR
    assert runner.stops == 1
    assert clearer.found == [1] and clearer.addr == 1
    assert any(f.cmd == CMD_PLAY_STOP for f in bus.sent)


def test_plain_presses_never_send_and_two_holds_do():
    clearer, bus, _ = make_clearer(answers=(1, 2))
    app, runner = make_app(clearer)
    enter(app)
    for event in ("key1", "press", "up", "down", "left", "right"):
        app.handle(event)
        settle(clearer)
    assert cleared_frames(bus) == []
    assert (clearer.addr, clearer.action.key) == (1, "colours")
    app.handle("key1_hold")
    assert app.screen is Screen.CLEAR and cleared_frames(bus) == []
    app.handle("down")                          # disarms: board 02 now
    app.handle("key1_hold")
    assert cleared_frames(bus) == []
    app.handle("key1_hold")
    settle(clearer)
    assert [(f.cmd, f.dest) for f in cleared_frames(bus)] == [(CMD_CLEAR_ALL, 2)]
    assert clearer.phase == DONE
    # On the verdict a hold sends nothing more; KEY1 is back to the choice.
    app.handle("key1_hold")
    app.handle("key1_hold")
    settle(clearer)
    assert len(cleared_frames(bus)) == 1
    app.handle("key1")
    assert clearer.phase == IDLE and app.screen is Screen.CLEAR
    app.handle("key2")
    assert app.screen is Screen.MENU
    assert runner.starts == [] and runner.standbys == 0     # no repaint


def test_buttons_are_ignored_while_the_port_is_held():
    clearer, bus, _ = make_clearer(answers=(1,))
    app, _ = make_app(clearer)
    enter(app)
    clearer.phase = boardclear.SENDING          # as if a send were in flight
    for event in ("key2", "key1", "key1_hold", "up", "left"):
        app.handle(event)
    assert app.screen is Screen.CLEAR and clearer.action.key == "colours"
    clearer.phase = IDLE


def test_the_pc_session_cannot_take_the_port_while_clearing():
    clearer, _, _ = make_clearer()
    remote = FakeRemote()
    make_app(clearer, remote=remote)
    assert remote.busy() is False
    clearer.phase = boardclear.SCANNING
    assert remote.busy() is True
    clearer.phase = IDLE


def test_refused_on_the_menu_while_the_pc_holds_the_unit():
    clearer, bus, _ = make_clearer()
    app, runner = make_app(clearer, remote=FakeRemote(active=True))
    enter(app)
    assert app.screen is Screen.MENU
    assert app._standby_status() == "PC holds this unit - release it on the PC"
    assert runner.stops == 0 and bus.sent == []


def test_refused_while_a_pc_show_runs_or_is_loaded_and_waiting():
    clearer, bus, _ = make_clearer()
    app, runner = make_app(clearer, remote=FakeRemote(),
                           player=FakePlayer(state=RUNNING))
    enter(app)
    assert app.screen is Screen.MENU
    assert app._standby_status() == "PC show running - stop it on the PC"
    # Uploaded and waiting for START: UPDATE FW would go ahead, but 0x15
    # would erase exactly those burned pictures.
    app.player.state = LOADED
    enter(app)
    assert app.screen is Screen.MENU
    assert app._standby_status() == CLEAR_LOCKED_LOADED
    assert runner.stops == 0 and bus.sent == []
    # A show restore() brought back (not loaded since this boot) does not
    # lock the row for good - the WIFI row's same rule.
    app.player.loaded_here = False
    enter(app)
    assert app.screen is Screen.CLEAR


def test_the_sending_hold_checks_again():
    clearer, bus, _ = make_clearer(answers=(1,))
    remote = FakeRemote()
    app, _ = make_app(clearer, remote=remote)
    enter(app)
    app.handle("key1_hold")                     # armed
    remote.active = True                        # the PC took the unit meanwhile
    app.handle("key1_hold")
    settle(clearer)
    assert cleared_frames(bus) == []
    assert clearer.phase == IDLE and clearer.armed_text() == ""
    assert clearer.status == "PC holds this unit - release it on the PC"


def test_the_screen_renders_every_phase_and_the_app_draws_it():
    for phase in ("idle", "scanning", "sending", "done", "failed"):
        image = render.clear_screen(1, "1/2", "CLEAR AUTOPLAY (0x05)",
                                    "power-on autoplay back to defaults",
                                    phase, "board 01: ACK_SUCCESS",
                                    ["10:00:00 boards 01,02 answer"] * 8,
                                    host="radxa-01")
        assert image.size == (WIDTH, HEIGHT)
    armed = render.clear_screen(1, "", "CLEAR COLOURS (0x15)", "x", "idle",
                                "", [], armed="hold KEY1 again: CLEAR COLOURS board 01")
    plain = render.clear_screen(1, "", "CLEAR COLOURS (0x15)", "x", "idle",
                                "", [])
    assert armed.tobytes() != plain.tobytes()
    assert render.clear_screen(None, "", "CLEAR COLOURS (0x15)", "x", "idle",
                               "no board answers - KEY1 rescans", [],
                               locked=True).size == (WIDTH, HEIGHT)
    clearer, _, _ = make_clearer(answers=(1,))
    app, _ = make_app(clearer)
    enter(app)
    app.draw()
    key = app._drawn_key
    app.handle("key1_hold")
    assert app._display_key() != key            # the arming repaints


# ---- review of d08e126: the unit forgets what 0x15 erased ----

def test_every_0x15_sent_makes_the_runner_forget_the_board(monkeypatch):
    monkeypatch.setattr(boardclear, "CLEAR_ACK_S", 0.05)
    for reply in ((ACK_SUCCESS, b""), (ACK_FAIL, b"\x06"), None):
        forgotten = []
        clearer, _, _ = make_clearer(bus=ClearBus(answers=(1,), reply=reply),
                                     forget_board=forgotten.append)
        sent_and_settled(clearer)
        # A late or silent board may still have erased: forgotten anyway.
        assert forgotten == [1], reply
        assert any("next burn writes it again" in line
                   for line in clearer.recent(10))
    # CLEAR AUTOPLAY touches no picture; no port means nothing went out.
    forgotten = []
    clearer, _, _ = make_clearer(answers=(1,), forget_board=forgotten.append)
    sent_and_settled(clearer, action_step=1)
    assert clearer.phase == DONE and forgotten == []
    clearer, _, _ = make_clearer(answers=(1,), forget_board=forgotten.append)
    scanned(clearer)
    clearer._locate = lambda: None
    clearer.confirm()
    clearer.confirm()
    settle(clearer)
    assert clearer.phase == FAILED and forgotten == []


def test_after_forget_board_a_reburn_of_the_same_cue_writes_the_board_again():
    from tests.test_ui_remote import SAVE, array, make_session
    session, runner, bus = make_session()
    cues = [{"slot": 1, "boards": {1: array(1), 2: array(1)}, "delays": {}}]
    session.burn(cues, dev_type=3)
    assert wait_until(lambda: (session.burn_status() or {}).get("state")
                      == "burned")
    n = len([f for f in bus.requested if f.cmd == SAVE])
    runner.forget_board(1)                       # CLEAR COLOURS on board 01
    session.burn(cues, dev_type=3)               # the same show again
    assert wait_until(lambda: session.burn_status()["done"] == 2
                      and session.burn_status()["state"] == "burned")
    again = [f for f in bus.requested if f.cmd == SAVE][n:]
    assert [(f.dest, f.data[0]) for f in again] == [(1, 1)]   # 02 still skipped
    runner.stop()


class LateStopBus(ClearBus):
    """The stop's ACK arrives `late` seconds after it was sent; the
    clearing command is never answered."""

    def __init__(self, late: float, **kwargs):
        super().__init__(reply=None, **kwargs)
        self.late = late
        self._due: list = []

    def send(self, frame: Frame) -> None:
        if frame.cmd == CMD_PLAY_STOP:
            self.sent.append(frame)
            self._due.append((time.monotonic() + self.late,
                              self._ack(frame, ACK_SUCCESS)))
            return
        super().send(frame)

    def recv(self, timeout: float = 0.5):
        end = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            for item in self._due:
                if item[0] <= now:
                    self._due.remove(item)
                    return item[1]
            if self._pending:
                return self._pending.popleft()
            if now >= end:
                return None
            time.sleep(min(0.005, end - now))


def test_a_late_stop_ack_is_never_read_as_the_commands_verdict(monkeypatch):
    monkeypatch.setattr(boardclear, "STOP_ACK_S", 0.05)
    monkeypatch.setattr(boardclear, "CLEAR_ACK_S", 0.3)
    bus = LateStopBus(late=0.15, answers=(1,))
    clearer = BoardClearer(boards=[1], open_bus=lambda port: bus,
                           locate=lambda: "/dev/fake", clock=Clock(),
                           echo_log=False, find=lambda b, boards: {1: None})
    scanned(clearer)
    clearer.confirm()
    clearer.confirm()
    settle(clearer)
    assert clearer.phase == FAILED and clearer.status == "board 01: no answer"


def test_clear_colours_is_refused_while_a_restored_pc_show_is_on_the_boards():
    # restore() brought the last PC show back LOADED with "burned": the
    # row opens (it must not lock for good), but 0x15 would leave START
    # passing its gate onto a blank board - so COLOURS is refused, and
    # AUTOPLAY (no picture touched) still goes.
    clearer, bus, _ = make_clearer(answers=(1,))
    player = FakePlayer(state=LOADED, burn="burned", loaded_here=False)
    app, _ = make_app(clearer, remote=FakeRemote(), player=player)
    enter(app)
    assert app.screen is Screen.CLEAR
    app.handle("key1_hold")
    app.handle("key1_hold")
    settle(clearer)
    assert cleared_frames(bus) == []
    assert clearer.status == CLEAR_LOCKED_PICTURES
    app.handle("right")                          # CLEAR AUTOPLAY
    app.handle("key1_hold")
    app.handle("key1_hold")
    settle(clearer)
    assert [f.cmd for f in cleared_frames(bus)] == [CMD_CLEAR_AUTOPLAY]
    # Once the PC has cleared its pictures, COLOURS goes too.
    player.burn = "cleared"
    app.handle("key1")                           # off the verdict
    app.handle("left")
    app.handle("key1_hold")
    app.handle("key1_hold")
    settle(clearer)
    assert [f.cmd for f in cleared_frames(bus)] == [CMD_CLEAR_AUTOPLAY,
                                                     CMD_CLEAR_ALL]


def test_a_plain_key1_rescan_is_refused_while_the_pc_holds_the_unit():
    clearer, bus, _ = make_clearer(answers=(1,))
    remote = FakeRemote()
    app, _ = make_app(clearer, remote=remote)
    enter(app)
    sweeps = len(bus.sent)
    remote.active = True
    app.handle("key1")
    assert not clearer.busy and len(bus.sent) == sweeps
    assert clearer.status == "PC holds this unit - release it on the PC"


def test_blanking_the_screen_disarms():
    clearer, bus, _ = make_clearer(answers=(1,))
    app, _ = make_app(clearer)
    enter(app)
    app.handle("key1_hold")
    assert clearer.armed_text()
    app.handle("key3")                           # blank
    assert clearer.armed_text() == ""
    app.handle("key1")                           # wakes only
    app.handle("key1_hold")                      # arms again, sends nothing
    settle(clearer)
    assert cleared_frames(bus) == []
