"""The one-way beacon, unit side: ui/beacon.py (docs/BEACON.md, spec v1).

The goldens (tests/goldens/beacon_v1.json) are the byte-exact contract
with conductor/beacon.py; the packer below is this file's own and is
checked against them first, so every datagram built from it afterwards
is one the Conductor could have sent. The per-datagram path is driven
through Beacon.handle() (no socket); a real ShowPlayer on the FakeBus of
tests/test_ui_runner.py stands behind the agent, as in
tests/test_ui_remote.py, and one test goes over a real UDP socket.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import struct
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_ui_remote import array, call, make_session, wait_until
from ui import beacon as bc
from ui.agent import Agent
from ui.showplay import ShowPlayer

GOLDEN = json.loads((Path(__file__).parent / "goldens" / "beacon_v1.json")
                    .read_text(encoding="utf-8"))
KEY = bc.beacon_key(GOLDEN["token"])
RUN, HOLD, STOP = 1, 2, 3


def pack(cid, gen, seq, entries, key=KEY, ver=1, count=None):
    """A datagram exactly as docs/BEACON.md section 2 lays it out."""
    out = struct.pack("!4sBBBBIII", b"EPBC", ver, 0,
                      len(entries) if count is None else count, 0,
                      cid, gen, seq)
    for e in entries:
        host, show = e["host"].encode(), e["show"].encode()
        out += bytes([len(host)]) + host + bytes([len(show)]) + show
        out += struct.pack("!BBddd", e["state"], e.get("flags", 0),
                           e["now"], e["t0"], e["pos"])
    return out + hmac.new(key, out, hashlib.sha256).digest()


# ---- the wire ----

def test_the_keys_match_the_goldens():
    assert KEY.hex() == GOLDEN["key_hex"]
    assert bc.beacon_key("").hex() == GOLDEN["key_hex_no_token"]
    assert bc.beacon_key(None).hex() == GOLDEN["key_hex_no_token"]


@pytest.mark.parametrize("golden", GOLDEN["datagrams"],
                         ids=[g["name"] for g in GOLDEN["datagrams"]])
def test_the_goldens_decode_and_this_packer_rebuilds_them(golden):
    data = bytes.fromhex(golden["hex"])
    assert pack(golden["cid"], golden["gen"], golden["seq"],
                golden["entries"]) == data
    header, entries = bc.unpack(data, KEY)
    assert (header["cid"], header["gen"], header["seq"], header["ver"]) == (
        golden["cid"], golden["gen"], golden["seq"], 1)
    assert header["count"] == len(golden["entries"])
    for got, want in zip(entries, golden["entries"]):
        for key in ("host", "show", "state", "flags", "now", "t0", "pos"):
            assert got[key] == want[key]
        assert got["force"] == bool(want["flags"] & 1)


def test_every_flipped_byte_is_rejected():
    data = bytes.fromhex(GOLDEN["datagrams"][0]["hex"])
    for at in range(len(data)):
        bad = bytearray(data)
        bad[at] ^= 0x01
        assert bc.unpack(bytes(bad), KEY) is None, at


def test_a_wrong_key_a_truncation_and_another_version_are_rejected():
    data = bytes.fromhex(GOLDEN["datagrams"][0]["hex"])
    assert bc.unpack(data, bc.beacon_key("")) is None
    assert bc.unpack(data, bc.beacon_key("other")) is None
    for size in (0, 20, 51, 52, len(data) - 1):
        assert bc.unpack(data[:size], KEY) is None
    entries = GOLDEN["datagrams"][1]["entries"]
    assert bc.unpack(pack(1, 1, 1, entries, ver=2), KEY) is None
    assert bc.unpack(pack(1, 1, 1, entries), KEY) is not None


def test_a_wrong_count_a_nan_an_unknown_state_and_oversize_are_rejected():
    one = dict(GOLDEN["datagrams"][1]["entries"][0])
    assert bc.unpack(pack(1, 1, 1, [one], count=2), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [one], count=0), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [one])[:-32] + b"\0" * 32, KEY) is None
    assert bc.unpack(pack(1, 1, 1, [dict(one, now=float("nan"))]), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [dict(one, t0=float("inf"))]), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [dict(one, state=4)]), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [one] * 40), KEY) is None      # > 1200 B
    # A count that leaves bytes over: one entry declared, two present.
    assert bc.unpack(pack(1, 1, 1, [one, one], count=1), KEY) is None


# ---- decide, row by row ----

def entry(state=RUN, t0=100.0, show="abc1234567", force=False, now=None):
    return {"host": "radxa-01", "show": show, "state": state,
            "flags": int(force), "force": force,
            "now": t0 if now is None else now, "t0": t0, "pos": 0.0}


def status(state="loaded", t0=None, synced=False, burn="burned",
           duration=60.0, demo=False, id_="abc1234567"):
    return {"id": id_, "state": state, "t0": t0, "synced": synced,
            "burn": {"state": burn}, "duration": duration, "demo": demo}


def test_decide_run_in_step_is_no_call():
    for state in ("running", "ended"):
        assert bc.decide(entry(t0=100.0), status(state, 100.08, True),
                         101.0) == (None, "in step")


def test_decide_run_re_times_a_t0_off_by_more_than_the_tolerance():
    assert bc.decide(entry(t0=100.0), status("running", 100.2, True),
                     101.0)[0] == "/show/run"
    # ...and a T0 that did not come from the PC (restored from disk).
    assert bc.decide(entry(t0=100.0), status("running", 100.0, False),
                     101.0)[0] == "/show/run"


def test_decide_run_over_is_no_call():
    # A unit still in that run gets END_SLACK past the end...
    for state in ("running", "holding"):
        path, reason = bc.decide(entry(t0=100.0), status(state, 90.0, True),
                                 100.0 + 60 + 30.5)
        assert path is None and "over" in reason
        assert bc.decide(entry(t0=100.0), status(state, 90.0, True),
                         100.0 + 60 + 29.5)[0] == "/show/run"
    # ...one that is not (loaded after a preset, stopped, ended) none:
    # a late datagram of a finished lap never starts it again (V5).
    for state in ("loaded", "stopped", "ended"):
        path, reason = bc.decide(entry(t0=100.0), status(state),
                                 100.0 + 60 + 0.1)
        assert path is None and reason == "run is over"
        assert bc.decide(entry(t0=100.0), status(state),
                         100.0 + 59.9)[0] == "/show/run"


def test_decide_run_waits_for_a_burn_in_progress():
    path, reason = bc.decide(entry(), status(burn="burning"), 101.0)
    assert path is None and "writing" in reason


def test_decide_run_otherwise_runs():
    for unit in (status("loaded"), status("stopped"), status("holding", 90.0),
                 status("loaded", burn="none")):
        assert bc.decide(entry(), unit, 101.0) == ("/show/run", "run")


def test_decide_hold_and_stop():
    assert bc.decide(entry(HOLD), status("running", 100.0, True),
                     101.0) == ("/show/hold", "hold")
    for state in ("holding", "loaded", "stopped", "ended"):
        assert bc.decide(entry(HOLD), status(state), 101.0)[0] is None
    for state in ("running", "holding"):
        assert bc.decide(entry(STOP), status(state), 101.0) == (
            "/show/stop", "stop")
    for state in ("loaded", "stopped", "ended"):
        assert bc.decide(entry(STOP), status(state), 101.0)[0] is None


# ---- the guards, on a stub agent ----

class StubPlayer:
    def __init__(self, st):
        self.st = st

    def status(self):
        return self.st


class StubRunner:
    def __init__(self):
        self.lines = []

    def emit(self, line):
        self.lines.append(line)


class StubAgent:
    def __init__(self, st, name="radxa-01", token=GOLDEN["token"]):
        self.name, self.token = name, token
        self.player = StubPlayer(st)
        self.show_cmd_at = None
        self.show_cmd_serial = None
        self.show_lock = threading.Lock()
        self.beacon_serial = None
        self.beacon_seen_cid = None
        self.session = type("S", (), {})()
        self.session.runner = StubRunner()
        self.calls = []
        self.refuse = None

    def show_command(self, path, body, via="http", serial=None):
        self.calls.append((path, body, via))
        if self.refuse:
            raise bc.RemoteError(self.refuse)
        return True


class Clock:
    def __init__(self, at=1000.0):
        self.at = at

    def __call__(self):
        return self.at


def stub(st=None, **kwargs):
    clock = Clock()
    agent = StubAgent(st if st is not None else status(), **kwargs)
    return bc.Beacon(agent, port=0, clock=clock), agent, clock


def run_dgram(clock, seq, cid=7, gen=1, t0=None, state=RUN, host="radxa-01",
              show="abc1234567", now=None, key=KEY, extra=()):
    e = {"host": host, "show": show, "state": state, "flags": 0,
         "now": clock.at if now is None else now,
         "t0": clock.at + 5 if t0 is None else t0, "pos": -5.0}
    return pack(cid, gen, seq, list(extra) + [e], key=key)


def test_a_good_run_calls_show_command_via_the_beacon():
    beacon, agent, clock = stub()
    assert beacon.handle(run_dgram(clock, 1, t0=1005.0)) == "run"
    assert agent.calls == [("/show/run", {"t0": 1005.0, "show": "abc1234567",
                                          "force": False}, "beacon")]
    assert agent.session.runner.lines == ["beacon run, starts in 5s"]
    st = beacon.status()
    assert st["applied"] == {"what": "run", "gen": 1, "ago_s": 0.0}
    assert st["cid"] == 7 and st["gen"] == 1 and st["heard_s"] == 0.0


def test_g1_garbage_is_counted_and_ignored():
    beacon, agent, clock = stub()
    assert beacon.handle(b"junk") == "bad"
    assert beacon.handle(run_dgram(clock, 1, key=bc.beacon_key(""))) == "bad"
    assert agent.calls == [] and beacon.status()["dropped"]["bad"] == 2
    assert beacon.status()["heard_s"] is None


def test_an_entry_for_another_unit_only_is_not_ours():
    beacon, agent, clock = stub()
    assert beacon.handle(run_dgram(clock, 1, host="radxa-02")) == "host"
    assert agent.calls == [] and beacon.status()["heard_s"] == 0.0
    # ...and our entry anywhere in the datagram is found.
    other = {"host": "radxa-02", "show": "zzz", "state": STOP, "now": 0.0,
             "t0": 0.0, "pos": 0.0}
    assert beacon.handle(run_dgram(clock, 2, extra=[other])) == "run"


def test_g2_another_conductor_waits_until_ours_is_silent():
    beacon, agent, clock = stub(status("holding", 1.0, True))
    beacon.handle(run_dgram(clock, 1, cid=7, state=STOP))
    assert agent.calls[-1][0] == "/show/stop"
    clock.at += 4.0
    assert beacon.handle(run_dgram(clock, 1, cid=8, state=STOP)) == "cid"
    # A datagram from the latched cid WITHOUT our entry does not keep it.
    beacon.handle(run_dgram(clock, 2, cid=7, host="radxa-02"))
    clock.at += 1.5                # 5.5 s since cid 7 last carried our entry
    assert beacon.handle(run_dgram(clock, 2, cid=8, state=STOP)) == "stop"
    assert beacon.status()["cid"] == 8
    assert beacon.status()["dropped"]["cid"] == 1


def test_g3_a_replay_or_the_unicast_duplicate_is_dropped():
    beacon, agent, clock = stub(status("running", 1.0, True))
    data = run_dgram(clock, 5, state=HOLD)
    assert beacon.handle(data) == "hold"
    assert beacon.handle(data) == "seq"
    assert beacon.handle(run_dgram(clock, 4, state=HOLD)) == "seq"
    assert len(agent.calls) == 1
    assert beacon.status()["dropped"]["seq"] == 2


def test_g4_a_now_out_of_step_is_dropped():
    beacon, agent, clock = stub()
    assert beacon.handle(run_dgram(clock, 1, now=clock.at - 1.2)) == "now"
    assert beacon.handle(run_dgram(clock, 2, now=clock.at + 1.2)) == "now"
    assert beacon.handle(run_dgram(clock, 3, now=clock.at + 0.9)) == "run"
    assert beacon.status()["dropped"]["now"] == 2


def test_g5_sent_before_the_newest_http_command_is_dropped():
    beacon, agent, clock = stub()
    agent.show_cmd_at = clock.at - 0.05
    assert beacon.handle(run_dgram(clock, 1)) == "http"
    assert agent.calls == [] and beacon.status()["dropped"]["http"] == 1
    clock.at += 0.2
    assert beacon.handle(run_dgram(clock, 2)) == "run"


def test_g5a_an_older_gen_of_the_same_cid_is_dropped_whatever_its_now():
    # Section 7 A1: with the HTTP serial known, clocks do not order.
    beacon, agent, clock = stub()
    agent.show_cmd_at = clock.at - 5.0          # long before: G5 would pass
    agent.show_cmd_serial = (7, 10)
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=9)) == "gen"
    assert beacon.status()["dropped"]["gen"] == 1
    # The same gen is accepted...
    assert beacon.handle(run_dgram(clock, 2, cid=7, gen=10)) == "run"
    # ...and with the serial known, the time-based G5 is off.
    beacon, agent, clock = stub()
    agent.show_cmd_at = clock.at + 0.5
    agent.show_cmd_serial = (7, 10)
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=11)) == "run"


def test_g5a_uses_32_bit_serial_arithmetic_and_ignores_another_cid():
    beacon, agent, clock = stub()
    agent.show_cmd_serial = (7, 0xFFFFFFFE)
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=1)) == "run"   # wrapped
    beacon, agent, clock = stub()
    agent.show_cmd_serial = (7, 1)
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=0xFFFFFFFE)) == "gen"
    beacon, agent, clock = stub()
    agent.show_cmd_serial = (8, 50)
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=3)) == "run"


def test_a_stop_is_applied_once_per_cid_and_gen():
    beacon, agent, clock = stub(status("running", 1.0, True))
    assert beacon.handle(run_dgram(clock, 1, gen=4, state=STOP)) == "stop"
    # Something else starts the unit again; the same stop leaves it be.
    agent.player.st = status("running", 1.0, True)
    assert beacon.handle(run_dgram(clock, 2, gen=4, state=STOP)) == \
        "stop: told already"
    assert beacon.handle(run_dgram(clock, 3, gen=5, state=STOP)) == "stop"
    assert [c[0] for c in agent.calls] == ["/show/stop", "/show/stop"]


def test_a_split_datagram_received_out_of_order_still_applies():
    beacon, agent, clock = stub()
    # Another unit's half (seq 6) first, then ours (seq 5): G3 counts
    # only datagrams that carry this unit's entry (section 7 A3).
    assert beacon.handle(run_dgram(clock, 6, host="radxa-02")) == "host"
    assert beacon.handle(run_dgram(clock, 5)) == "run"


def test_a_new_cid_from_seq_1_after_5_s_of_silence():
    beacon, agent, clock = stub(status("running", 1.0, True))
    assert beacon.handle(run_dgram(clock, 900, cid=7, state=HOLD)) == "hold"
    clock.at += 5.1                             # the Conductor restarted
    agent.player.st = status("running", 1.0, True)
    assert beacon.handle(run_dgram(clock, 1, cid=8, state=HOLD)) == "hold"
    assert beacon.status()["cid"] == 8
    # ...and the old one, back within 5 s of the new, is the other one now.
    assert beacon.handle(run_dgram(clock, 901, cid=7, state=HOLD)) == "cid"


def test_the_beacon_call_holds_show_lock_through_the_player_call():
    beacon, agent, clock = stub()
    seen = []

    def command(path, body, via="http", serial=None):
        seen.append(agent.show_lock.locked())
        return True
    agent.show_command = command
    assert beacon.handle(run_dgram(clock, 1)) == "run"
    assert seen == [True] and not agent.show_lock.locked()


def test_g6_the_units_own_demo_is_never_touched():
    for st in (status("running", 1.0, True, demo=True),
               status("holding", 1.0, True, demo=True),
               status("loaded", burn="burning", demo=True)):
        for state in (RUN, HOLD, STOP):
            beacon, agent, clock = stub(st)
            assert beacon.handle(run_dgram(clock, 1, state=state)) == "demo"
            assert agent.calls == []
    # A demo merely loaded is not playing: G7 (another id) decides.
    beacon, agent, clock = stub(status("loaded", demo=True, id_="demo000001"))
    assert beacon.handle(run_dgram(clock, 1)) == "show"


def test_g7_no_show_or_another_show_is_dropped():
    beacon, agent, clock = stub()
    agent.player.st = None
    assert beacon.handle(run_dgram(clock, 1)) == "show"
    agent.player.st = status(id_="other00001")
    assert beacon.handle(run_dgram(clock, 2)) == "show"
    agent.player = None
    assert beacon.handle(run_dgram(clock, 3)) == "show"
    assert agent.calls == [] and beacon.status()["dropped"]["show"] == 3


def test_a_refusal_is_logged_once_and_retried_no_sooner_than_3_s():
    beacon, agent, clock = stub()
    agent.refuse = "pictures not written (the burn never started) - Upload again"
    assert beacon.handle(run_dgram(clock, 1)) == "refused"
    clock.at += 1.0
    assert beacon.handle(run_dgram(clock, 2)) == "retry wait"
    clock.at += 2.1
    assert beacon.handle(run_dgram(clock, 3)) == "refused"
    assert len(agent.calls) == 2
    assert agent.session.runner.lines == [
        "beacon NG: pictures not written (the burn never "
        "started) - Upload again"]
    assert "refused" in beacon.status()["ignored"]


def test_the_retry_wait_holds_back_only_the_refused_command_of_that_gen():
    # U3: a refused RUN never delays a STOP, nor the run of a newer gen.
    beacon, agent, clock = stub(status("running", 1.0, False))
    agent.refuse = "unit is busy (firmware update, scan or reboot)"
    assert beacon.handle(run_dgram(clock, 1, gen=3)) == "refused"
    assert beacon.handle(run_dgram(clock, 2, gen=3)) == "retry wait"
    assert beacon.handle(run_dgram(clock, 3, gen=4)) == "refused"
    agent.refuse = None
    assert beacon.handle(run_dgram(clock, 4, gen=4)) == "retry wait"
    assert beacon.handle(run_dgram(clock, 5, gen=4, state=STOP)) == "stop"
    assert [c[0] for c in agent.calls] == ["/show/run", "/show/run",
                                           "/show/stop"]


def test_the_retry_timer_starts_when_the_command_fails():
    beacon, agent, clock = stub()

    def slow_refusal(path, body, via="http", serial=None):
        clock.at += 2.0                  # the player took 2 s to say no
        raise bc.RemoteError("no")
    agent.show_command = slow_refusal
    assert beacon.handle(run_dgram(clock, 1)) == "refused"
    clock.at += 2.5                      # 4.5 s after the datagram, 2.5 s after
    assert beacon.handle(run_dgram(clock, 2)) == "retry wait"
    clock.at += 0.6
    assert beacon.handle(run_dgram(clock, 3)) == "refused"


def test_stops_are_remembered_per_cid():
    # U4: cid 8's stop does not make cid 7's stop of the same gen new again.
    beacon, agent, clock = stub(status("running", 1.0, True))
    assert beacon.handle(run_dgram(clock, 1, cid=7, gen=4, state=STOP)) == "stop"
    clock.at += 5.0
    agent.player.st = status("running", 1.0, True)
    assert beacon.handle(run_dgram(clock, 1, cid=8, gen=4, state=STOP)) == "stop"
    clock.at += 5.0
    assert beacon.handle(run_dgram(clock, 2, cid=7, gen=4, state=STOP)) == \
        "stop: told already"
    assert len(agent.calls) == 2


def test_an_unusable_clock_neither_latches_nor_moves_the_sequence():
    # U5: G4 comes before the latch and the watermark are committed.
    beacon, agent, clock = stub()
    assert beacon.handle(run_dgram(clock, 1, cid=7)) == "run"
    clock.at += 5.0
    agent.player.st = status()
    # cid 8 with a junk clock: not latched...
    assert beacon.handle(run_dgram(clock, 50, cid=8, now=clock.at - 9)) == "now"
    assert beacon.status()["cid"] == 7
    # ...and its seq 50 never became the watermark.
    assert beacon.handle(run_dgram(clock, 10, cid=8)) == "run"
    assert beacon.status()["cid"] == 8
    # Our own cid with a junk clock does not refresh the latch either.
    beacon, agent, clock = stub()
    assert beacon.handle(run_dgram(clock, 1, cid=7)) == "run"
    clock.at += 4.0
    assert beacon.handle(run_dgram(clock, 2, cid=7, now=0.0)) == "now"
    clock.at += 1.0                      # 5 s since cid 7's last good one
    agent.player.st = status()
    assert beacon.handle(run_dgram(clock, 1, cid=9)) == "run"


def test_the_latch_is_taken_over_at_exactly_cid_hold_s():
    beacon, agent, clock = stub()
    start = clock.at
    assert beacon.handle(run_dgram(clock, 1, cid=7)) == "run"
    clock.at = start + bc.CID_HOLD_S - 0.01
    assert beacon.handle(run_dgram(clock, 1, cid=8)) == "cid"
    clock.at = start + bc.CID_HOLD_S
    agent.player.st = status()
    assert beacon.handle(run_dgram(clock, 2, cid=8)) == "run"


def test_cid_0_is_rejected():
    one = dict(GOLDEN["datagrams"][1]["entries"][0])
    assert bc.unpack(pack(0, 1, 1, [one]), KEY) is None
    assert bc.unpack(pack(1, 1, 1, [one]), KEY) is not None


def test_a_serial_of_another_cid_leaves_the_time_based_g5_on():
    # U6: the last HTTP command came from another Conductor.
    beacon, agent, clock = stub()
    agent.show_cmd_at = clock.at - 0.05
    agent.show_cmd_serial = (8, 100)
    assert beacon.handle(run_dgram(clock, 1, cid=7)) == "http"
    clock.at += 0.2
    assert beacon.handle(run_dgram(clock, 2, cid=7)) == "run"


def test_an_applied_command_records_the_beacon_serial():
    beacon, agent, clock = stub()
    beacon.handle(run_dgram(clock, 1, cid=7, gen=12))
    assert agent.beacon_serial == (7, 12)
    agent.player.st = status("running", 1.0, True)
    agent.refuse = "no"
    beacon.handle(run_dgram(clock, 2, cid=7, gen=13, state=HOLD))
    assert agent.beacon_serial == (7, 12)        # a refusal applies nothing


def ignored_lines(agent):
    return [line for line in agent.session.runner.lines
            if line.startswith("beacon skip")]


def test_each_ignored_reason_is_logged_once_and_again_after_an_action():
    beacon, agent, clock = stub()
    agent.player.st = None
    for seq in (1, 2, 3):
        assert beacon.handle(run_dgram(clock, seq)) == "show"
    assert ignored_lines(agent) == ["beacon skip: no show"]
    for seq in (4, 5):
        assert beacon.handle(run_dgram(clock, seq, now=clock.at + 3)) == "now"
    assert beacon.handle(run_dgram(clock, 6)) == "show"
    assert ignored_lines(agent) == ["beacon skip: no show",
                                    "beacon skip: clock off",
                                    "beacon skip: no show"]
    # An applied action resets it: the same reason is said again.
    agent.player.st = status()
    assert beacon.handle(run_dgram(clock, 7)) == "run"
    agent.player.st = None
    beacon.handle(run_dgram(clock, 8))
    beacon.handle(run_dgram(clock, 9))
    assert ignored_lines(agent)[-1] == "beacon skip: no show"
    assert len(ignored_lines(agent)) == 4


def test_other_conductor_and_own_demo_are_logged_but_routine_drops_are_not():
    beacon, agent, clock = stub(status("running", 1.0, True, demo=True))
    beacon.handle(run_dgram(clock, 1, cid=7))
    beacon.handle(run_dgram(clock, 2, cid=7))
    beacon.handle(run_dgram(clock, 1, cid=8))
    beacon.handle(run_dgram(clock, 2, cid=8))
    assert ignored_lines(agent) == ["beacon skip: own demo",
                                    "beacon skip: other PC"]
    # bad / host / seq / gen / http / in step: never on the LCD.
    beacon, agent, clock = stub(status("running", 1005.0, True))
    beacon.handle(b"junk")
    beacon.handle(run_dgram(clock, 1, host="radxa-02"))
    beacon.handle(run_dgram(clock, 2, t0=1005.0))           # in step
    beacon.handle(run_dgram(clock, 2, t0=1005.0))           # seq
    agent.show_cmd_serial = (7, 5)
    beacon.handle(run_dgram(clock, 3, gen=4))               # gen
    agent.show_cmd_serial, agent.show_cmd_at = None, clock.at + 1
    beacon.handle(run_dgram(clock, 4))                      # http
    assert agent.session.runner.lines == []
    assert beacon.status()["dropped"]["http"] == 1


def test_an_unexpected_exception_from_the_player_is_survived():
    beacon, agent, clock = stub()

    def boom(path, body, via="http", serial=None):
        raise KeyError("t0")
    agent.show_command = boom
    assert beacon.handle(run_dgram(clock, 1)) == "refused"
    assert "KeyError" in beacon.status()["ignored"]


# ---- with a real ShowPlayer ----

SHOW_ID = "abc1234567"


def a_show(duration=60.0):
    return {"id": SHOW_ID, "name": "t", "unit": "radxa-01", "dev_type": 3,
            "refresh_s": 0.3, "duration": duration, "boards": [1],
            "slot_capacity": 20,
            "cues": [{"id": "q00", "at": 0.0, "sent": -0.3, "slot": 1,
                      "label": "", "boards": {"1": array(1).hex()},
                      "state": {"1": array(1).hex()}}]}


class Real:
    """A real agent and player on the FakeBus, and a Beacon whose clock
    is the unit's monotonic clock plus `shift` (to step over RETRY_S
    without sleeping; the player keeps the true clock)."""

    def __init__(self, tmp_path, burn=True, serve=False):
        self.session, self.runner, self.bus = make_session(
            boards=[1], verify_fire=False)
        self.player = ShowPlayer(self.session, store=tmp_path, tick_s=0.02,
                                 grace_s=0.1)
        self.agent = Agent(self.session, port=0, host="127.0.0.1",
                           name="radxa-01", token=GOLDEN["token"],
                           player=self.player)
        if serve:
            self.agent.start()
        self.shift = 0.0
        self.beacon = bc.Beacon(self.agent, port=0,
                                clock=lambda: time.monotonic() + self.shift)
        self.agent.beacon = self.beacon
        self.calls = []
        command = self.agent.show_command

        def spy(path, body, via="http", headers=None, serial=None):
            self.calls.append((path, via))
            return command(path, body, via, headers=headers, serial=serial)
        self.agent.show_command = spy
        self.seq = 0
        if burn:
            self.load()

    def load(self):
        self.player.load(a_show())
        assert wait_until(lambda: self.player.status()["burn"]["state"]
                          == "burned", timeout=5)

    def send(self, state, t0, cid=99, gen=1, force=False):
        self.seq += 1
        now = time.monotonic() + self.shift
        return self.beacon.handle(pack(cid, gen, self.seq, [
            {"host": "radxa-01", "show": SHOW_ID, "state": state,
             "flags": int(force), "now": now, "t0": t0,
             "pos": now - t0}]))

    def close(self):
        self.agent.stop()
        self.player.close()
        self.runner.stop()


@pytest.fixture
def real(tmp_path):
    unit = Real(tmp_path)
    yield unit
    unit.close()


def test_run_is_applied_once_and_a_second_identical_beacon_makes_no_call(real):
    t0 = time.monotonic() + 2.0
    assert real.send(RUN, t0) == "run"
    st = real.player.status()
    assert st["state"] == "running" and st["t0"] == t0 and st["synced"]
    assert real.send(RUN, t0) == "in step"
    assert real.send(RUN, t0 + 0.05) == "in step"
    assert real.calls == [("/show/run", "beacon")]
    assert any("beacon run, starts in 2s" in line
               for line in real.runner.recent(10))
    # ...and /status carries the block.
    block = real.agent.status()["beacon"]
    assert block["applied"]["what"] == "run" and block["ignored"] == "in step"


def test_hold_then_resume_on_a_moved_t0_then_stop(real):
    t0 = time.monotonic() - 1.0
    assert real.send(RUN, t0) == "run"
    assert real.send(HOLD, t0, gen=2) == "hold"
    assert real.player.status()["state"] == "holding"
    assert real.send(HOLD, t0, gen=2) == "hold: not running"
    moved = t0 + 4.0                     # RESUME: T0 later by the time held
    assert real.send(RUN, moved, gen=3) == "run"
    st = real.player.status()
    assert st["state"] == "running" and st["t0"] == moved
    assert real.send(STOP, 0.0, gen=4) == "stop"
    assert real.player.status()["state"] == "stopped"
    assert real.send(STOP, 0.0, gen=4).startswith("stop: not")
    assert real.calls == [("/show/run", "beacon"), ("/show/hold", "beacon"),
                          ("/show/run", "beacon"), ("/show/stop", "beacon")]
    lines = real.runner.recent(20)
    assert any(line.endswith("beacon hold") for line in lines)
    assert any(line.endswith("beacon stop") for line in lines)


def test_a_demo_playing_on_the_unit_is_untouched(real):
    real.player.load(a_show(), demo=True, name="loop", slug="loop")
    assert wait_until(lambda: real.player.status()["burn"]["state"]
                      == "burned", timeout=5)
    t0 = time.monotonic() - 1.0
    real.player.run(t0)
    for state in (RUN, HOLD, STOP):
        assert real.send(state, t0 + 3.0) == "demo"
    st = real.player.status()
    assert st["state"] == "running" and st["t0"] == t0 and st["demo"]
    assert real.calls == []


def test_burn_none_is_refused_once_and_retried_after_3_s(tmp_path):
    unit = Real(tmp_path, burn=False)
    try:
        real_burn = unit.session.burn
        unit.session.burn = lambda *a, **k: None       # the burn never starts
        unit.player.load(a_show())
        assert unit.player.status()["burn"]["state"] == "none"
        t0 = time.monotonic() + 5.0
        assert unit.send(RUN, t0) == "refused"
        assert unit.send(RUN, t0) == "retry wait"
        unit.shift = 1.0
        assert unit.send(RUN, t0) == "retry wait"
        assert unit.calls == [("/show/run", "beacon")]
        refusals = [line for line in unit.runner.recent(20)
                    if "beacon NG: pictures not written" in line]
        assert len(refusals) == 1
        # The pictures go in; the next beacon past 3 s runs the show.
        real_burn(unit.player._burn_items(a_show()), 3)
        assert wait_until(lambda: unit.player.status()["burn"]["state"]
                          == "burned", timeout=5)
        unit.shift = 3.1
        assert unit.send(RUN, t0) == "run"
        assert unit.player.status()["state"] == "running"
        assert len(unit.calls) == 2
    finally:
        unit.close()


def test_an_http_hold_wins_over_an_older_run_datagram(tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() - 1.0
        assert unit.send(RUN, t0) == "run"
        # The datagram is stamped (sent) BEFORE the HTTP hold arrives...
        unit.seq += 1
        older = pack(99, 1, unit.seq, [
            {"host": "radxa-01", "show": SHOW_ID, "state": RUN,
             "now": time.monotonic(), "t0": t0 + 2.0, "pos": 0.0}])
        code, answer = call(unit.agent, "/show/hold", {},
                            token=GOLDEN["token"])
        assert code == 200 and answer["show"]["state"] == "holding"
        assert unit.agent.show_cmd_at is not None
        # ...and arrives after it: it must not undo the hold.
        assert unit.beacon.handle(older) == "http"
        assert unit.player.status()["state"] == "holding"
        assert answer["beacon"]["applied"]["what"] == "run"
    finally:
        unit.close()


def post(agent, path, body, headers):
    request = urllib.request.Request(f"http://127.0.0.1:{agent.port}{path}",
                                     data=json.dumps(body).encode())
    request.add_header("X-Show-Token", GOLDEN["token"])
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_a_stale_gen_after_an_http_hold_stays_holding_even_if_sent_later(
        tmp_path):
    # Section 7 A1: the hold carries the Conductor's (cid, gen); a run
    # datagram of the gen before it - stamped AFTER the hold arrived, so
    # the time-based G5 would have let it through - stays dropped.
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() - 1.0
        assert unit.send(RUN, t0, cid=99, gen=1) == "run"
        code, answer = post(unit.agent, "/show/hold", {},
                            {"X-Beacon-Cid": "99", "X-Run-Gen": "2"})
        assert code == 200 and answer["show"]["state"] == "holding"
        assert unit.agent.show_cmd_serial == (99, 2)
        unit.shift = 0.5                 # `now` well after the hold
        assert unit.send(RUN, t0, cid=99, gen=1) == "gen"
        assert unit.player.status()["state"] == "holding"
        # The same gen is accepted (and is a hold: nothing to do).
        assert unit.send(HOLD, t0, cid=99, gen=2) == "hold: not running"
        # A RESUME the beacon brings first (gen 3) is applied.
        assert unit.send(RUN, t0 + 3.0, cid=99, gen=3) == "run"
        assert unit.player.status()["state"] == "running"
        # A POST without the headers (or with junk) falls back to G5.
        post(unit.agent, "/show/hold", {}, {"X-Beacon-Cid": "x",
                                            "X-Run-Gen": "3"})
        assert unit.agent.show_cmd_serial is None
    finally:
        unit.close()


def count_runs(unit):
    """Every call into ShowPlayer.run(), whoever made it."""
    runs = []
    run = unit.player.run

    def spy(*args, **kwargs):
        runs.append(args)
        return run(*args, **kwargs)
    unit.player.run = spy
    return runs


def serial(cid, gen):
    return {"X-Beacon-Cid": str(cid), "X-Run-Gen": str(gen)}


def test_a_start_by_beacon_then_by_http_with_its_serial_runs_once(tmp_path):
    # V1: the beacon's START landed first; the HTTP one (same serial,
    # same T0) after - recognised by command identity.
    unit = Real(tmp_path, serve=True)
    try:
        runs = count_runs(unit)
        t0 = time.monotonic() + 1.0
        assert unit.send(RUN, t0, cid=99, gen=5) == "run"
        assert unit.agent.applied_run == (99, 5, t0)
        code, answer = post(unit.agent, "/show/run",
                            {"t0": t0 + 0.004, "show": SHOW_ID},
                            serial(99, 5))
        assert code == 200 and answer["show"]["state"] == "running"
        assert answer["show"]["t0"] == t0
        assert len(runs) == 1                     # the player was not asked
        assert unit.agent.show_cmd_serial == (99, 5)   # ...HTTP stamped
    finally:
        unit.close()


def test_a_forced_start_delivered_twice_with_one_serial_runs_once(tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        runs = count_runs(unit)
        t0 = time.monotonic() + 1.0
        body = {"t0": t0, "show": SHOW_ID, "force": True}
        assert post(unit.agent, "/show/run", body, serial(99, 5))[0] == 200
        assert post(unit.agent, "/show/run", body, serial(99, 5))[0] == 200
        assert len(runs) == 1
        # The beacon's copy of it (force bit set) is in step anyway.
        assert unit.send(RUN, t0, cid=99, gen=5, force=True) == "in step"
        assert len(runs) == 1
    finally:
        unit.close()


def test_a_new_gen_a_moved_t0_or_no_serial_always_reaches_the_player(
        tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        runs = count_runs(unit)
        t0 = time.monotonic() + 1.0
        assert post(unit.agent, "/show/run", {"t0": t0, "show": SHOW_ID},
                    serial(99, 5))[0] == 200
        # A SEEK 40 ms away is a new gen: executed.
        post(unit.agent, "/show/run", {"t0": t0 + 0.04, "show": SHOW_ID},
             serial(99, 6))
        assert len(runs) == 2 and unit.player.t0 == t0 + 0.04
        # The same serial with a moved T0: executed.
        post(unit.agent, "/show/run", {"t0": t0 + 0.2, "show": SHOW_ID},
             serial(99, 6))
        assert len(runs) == 3
        # No serial at all, on a running unit, the same T0: executed -
        # the HTTP-only path is what it always was.
        assert call(unit.agent, "/show/run",
                    {"t0": t0 + 0.2, "show": SHOW_ID},
                    token=GOLDEN["token"])[0] == 200
        assert len(runs) == 4 and unit.agent.applied_run is None
    finally:
        unit.close()


def test_the_same_serial_on_a_unit_not_running_or_not_synced_runs(tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        runs = count_runs(unit)
        t0 = time.monotonic() + 1.0
        body = {"t0": t0, "show": SHOW_ID}
        post(unit.agent, "/show/run", body, serial(99, 5))
        unit.player.hold()
        post(unit.agent, "/show/run", body, serial(99, 5))
        assert len(runs) == 2 and unit.player.state == "running"
        unit.player.synced = False                # a T0 restored from disk
        post(unit.agent, "/show/run", body, serial(99, 5))
        assert len(runs) == 3 and unit.player.synced
    finally:
        unit.close()


def test_an_http_stop_never_waits_for_show_lock(tmp_path):
    # Final review: show_lock is held through player calls (an HTTP
    # run/hold, the beacon - whose stop can wait 2 s in the settle). An
    # HTTP /show/stop never takes it: straight to the player, as before.
    unit = Real(tmp_path, serve=True)
    try:
        stops = []
        stop = unit.player.stop
        unit.player.stop = lambda *a, **k: (stops.append(1), stop(*a, **k))[1]
        assert unit.agent.show_lock.acquire(timeout=1)     # someone else has it
        try:
            status, _ = post(unit.agent, "/show/stop", {}, serial(99, 5))
            assert status == 200 and stops == [1]          # ...and it did not wait
        finally:
            unit.agent.show_lock.release()
        assert unit.agent.show_cmd_serial == (99, 5)       # stamped all the same
        assert unit.agent.stops_seen == 1
    finally:
        unit.close()


def test_an_http_run_overtaken_by_a_stop_while_it_queued_is_dropped(tmp_path):
    # The lock must not let an EARLIER run land AFTER a later STOP: without
    # the lock that run would have reached the player first and the STOP
    # would have had the last word.
    unit = Real(tmp_path, serve=True)
    try:
        runs = []
        run = unit.player.run
        unit.player.run = lambda *a, **k: (runs.append(a), run(*a, **k))[1]
        t0 = time.monotonic() + 2.0
        assert unit.agent.show_lock.acquire(timeout=1)     # e.g. a hold in the player
        try:
            answer = []
            queued = threading.Thread(target=lambda: answer.append(post(
                unit.agent, "/show/run", {"t0": t0, "show": SHOW_ID},
                serial(99, 5))))
            queued.start()
            time.sleep(0.3)
            assert answer == []                            # waiting on show_lock
            status, _ = post(unit.agent, "/show/stop", {}, serial(99, 6))
            assert status == 200
        finally:
            unit.agent.show_lock.release()
        queued.join(5)
        assert answer and answer[0][0] == 200              # answered, not an error
        assert runs == []                                  # ...but never run
        assert unit.player.status()["state"] != "running"
    finally:
        unit.close()


def test_a_beacon_run_that_lands_after_an_http_stop_is_stopped_again(tmp_path):
    # A STOP does not wait for the beacon's call: if the beacon's run is in
    # the player when the STOP goes through, the beacon stops the unit
    # again when its call returns.
    unit = Real(tmp_path, serve=True)
    try:
        inside, release = threading.Event(), threading.Event()
        run = unit.player.run

        def gated(*args, **kwargs):
            inside.set()
            release.wait(5)
            return run(*args, **kwargs)
        unit.player.run = gated
        t0 = time.monotonic() + 2.0
        result = []
        beacon = threading.Thread(
            target=lambda: result.append(unit.send(RUN, t0, cid=99, gen=5)))
        beacon.start()
        assert inside.wait(5)
        status, _ = post(unit.agent, "/show/stop", {}, serial(99, 6))
        assert status == 200                               # did not wait
        release.set()
        beacon.join(5)
        assert result == ["stop"]
        assert unit.player.status()["state"] != "running"
        assert any("STOP wins" in line for line in unit.runner.recent(20))
    finally:
        unit.close()


def test_http_run_and_beacon_cannot_both_run_one_serial(tmp_path):
    # V2: the HTTP run holds show_lock from the stamp through the player
    # call. Gate the player inside one path's call and deliver the other
    # path's copy meanwhile: it waits, then finds the run applied.
    unit = Real(tmp_path, serve=True)
    try:
        runs = []
        inside, release = threading.Event(), threading.Event()
        run = unit.player.run

        def gated(*args, **kwargs):
            runs.append(args)
            if len(runs) == 1:
                inside.set()
                release.wait(5)
            return run(*args, **kwargs)
        unit.player.run = gated
        t0 = time.monotonic() + 2.0
        http = threading.Thread(target=post, args=(
            unit.agent, "/show/run", {"t0": t0, "show": SHOW_ID},
            serial(99, 5)))
        http.start()
        assert inside.wait(5)
        result = []
        beacon = threading.Thread(
            target=lambda: result.append(unit.send(RUN, t0, cid=99, gen=5)))
        beacon.start()
        time.sleep(0.2)
        assert result == []                       # waiting on show_lock
        release.set()
        http.join(5)
        beacon.join(5)
        assert result == ["in step"] and len(runs) == 1
        # ...and the other way round: the beacon first, HTTP waits.
        inside.clear()
        release.clear()
        runs.clear()
        t1 = t0 + 5.0
        beacon = threading.Thread(
            target=lambda: result.append(unit.send(RUN, t1, cid=99, gen=6)))
        beacon.start()
        assert inside.wait(5)
        answers = []
        http = threading.Thread(target=lambda: answers.append(post(
            unit.agent, "/show/run", {"t0": t1, "show": SHOW_ID},
            serial(99, 6))))
        http.start()
        time.sleep(0.2)
        assert answers == []
        release.set()
        beacon.join(5)
        http.join(5)
        assert answers[0][0] == 200 and len(runs) == 1
        assert unit.player.t0 == t1
    finally:
        unit.close()


def test_a_late_http_run_of_an_older_gen_leaves_the_beacons_seek(tmp_path):
    # U2: SEEK A (gen 5) and SEEK B (gen 6) came by beacon; the HTTP run
    # for A arrives after both.
    unit = Real(tmp_path, serve=True)
    try:
        now = time.monotonic()
        t0_a, t0_b = now - 3.0, now - 10.0
        assert unit.send(RUN, t0_a, cid=99, gen=5) == "run"
        assert unit.send(RUN, t0_b, cid=99, gen=6) == "run"
        assert unit.agent.beacon_serial == (99, 6)
        runs = count_runs(unit)
        code, answer = post(unit.agent, "/show/run",
                            {"t0": t0_a, "show": SHOW_ID}, serial(99, 5))
        assert code == 200 and answer["show"]["t0"] == t0_b
        assert runs == []
        # Not stamped either: the late command changes nothing at all.
        assert unit.agent.show_cmd_at is None
        assert unit.agent.show_cmd_serial is None
    finally:
        unit.close()


def test_a_late_http_run_after_a_beacon_hold_stays_holding(tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() - 1.0
        assert unit.send(RUN, t0, cid=99, gen=5) == "run"
        assert unit.send(HOLD, t0, cid=99, gen=6) == "hold"
        code, answer = post(unit.agent, "/show/run",
                            {"t0": t0, "show": SHOW_ID}, serial(99, 5))
        assert code == 200 and answer["show"]["state"] == "holding"
        assert unit.player.state == "holding"
    finally:
        unit.close()


def test_http_of_the_same_or_a_newer_gen_or_another_cid_or_a_load_acts(
        tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() - 1.0
        assert unit.send(RUN, t0, cid=99, gen=5) == "run"
        assert unit.send(HOLD, t0, cid=99, gen=6) == "hold"
        # The same gen: executed (the hold again - nothing to change).
        runs = count_runs(unit)
        post(unit.agent, "/show/hold", {}, serial(99, 6))
        assert unit.agent.show_cmd_serial == (99, 6)
        # A newer gen: executed.
        post(unit.agent, "/show/run", {"t0": t0 + 2.0, "show": SHOW_ID},
             serial(99, 7))
        assert len(runs) == 1 and unit.player.state == "running"
        # Another Conductor's older-looking gen: executed.
        post(unit.agent, "/show/hold", {}, serial(42, 1))
        assert unit.player.state == "holding"
        # Without headers: executed, exactly as before.
        call(unit.agent, "/show/stop", {}, token=GOLDEN["token"])
        assert unit.player.state == "stopped"
        # /show/load is never skipped, whatever its gen.
        loads = []
        load = unit.player.load
        unit.player.load = lambda show: (loads.append(show), load(show))[1]
        code, _ = post(unit.agent, "/show/load", a_show(), serial(99, 1))
        assert code == 200 and len(loads) == 1
    finally:
        unit.close()


def test_the_http_serial_never_goes_back_for_the_same_cid(tmp_path):
    unit = Real(tmp_path, serve=True)
    try:
        post(unit.agent, "/show/hold", {}, serial(99, 8))
        post(unit.agent, "/show/load", a_show(), serial(99, 7))
        assert unit.agent.show_cmd_serial == (99, 8)
        post(unit.agent, "/show/hold", {}, serial(42, 3))
        assert unit.agent.show_cmd_serial == (42, 3)
    finally:
        unit.close()


def test_an_http_stop_of_an_older_gen_is_always_executed(tmp_path):
    # V3a: a swallowed STOP is the one failure not risked.
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() - 1.0
        assert unit.send(RUN, t0, cid=99, gen=7) == "run"
        code, answer = post(unit.agent, "/show/stop", {}, serial(99, 3))
        assert code == 200 and answer["show"]["state"] == "stopped"
    finally:
        unit.close()


def test_older_gen_http_is_skipped_only_once_the_beacon_was_heard(tmp_path):
    # V3c: with the beacon never heard (switched off on the Conductor),
    # the HTTP path is what it was before the beacon existed.
    unit = Real(tmp_path, serve=True)
    try:
        t0 = time.monotonic() + 1.0
        post(unit.agent, "/show/run", {"t0": t0, "show": SHOW_ID},
             serial(99, 8))
        post(unit.agent, "/show/hold", {}, serial(99, 6))      # older
        assert unit.player.state == "holding"
        post(unit.agent, "/show/run", {"t0": t0 + 1, "show": SHOW_ID},
             serial(99, 5))                                     # older
        assert unit.player.state == "running"
        assert unit.player.t0 == t0 + 1
        assert unit.agent.beacon_seen_cid is None
        # Now one valid entry for this unit is heard from cid 99...
        assert unit.send(RUN, t0 + 1, cid=99, gen=8) == "in step"
        assert unit.agent.beacon_seen_cid == 99
        # ...and an older-gen hold is late from then on.
        code, answer = post(unit.agent, "/show/hold", {}, serial(99, 7))
        assert code == 200 and answer["show"]["state"] == "running"
        # Another cid has never been heard: executed.
        post(unit.agent, "/show/hold", {}, serial(42, 1))
        assert unit.player.state == "holding"
    finally:
        unit.close()


def test_an_in_step_beacon_advances_the_watermark(tmp_path):
    # V4: the beacon had nothing to do, but gen 9 is accepted - an HTTP
    # run of gen 8 arriving afterwards is late.
    unit = Real(tmp_path, serve=True)
    try:
        runs = count_runs(unit)
        t0 = time.monotonic() + 1.0
        post(unit.agent, "/show/run", {"t0": t0, "show": SHOW_ID},
             serial(99, 9))
        assert unit.agent.beacon_serial is None
        assert unit.send(RUN, t0, cid=99, gen=9) == "in step"
        assert unit.agent.beacon_serial == (99, 9)
        unit.agent.show_cmd_serial = None         # the beacon's word alone
        post(unit.agent, "/show/run", {"t0": t0 + 3, "show": SHOW_ID},
             serial(99, 8))
        assert len(runs) == 1 and unit.player.t0 == t0
        # Already holding / already stopped advance it too.
        unit.player.hold()
        unit.shift = 1.0                 # sent well after the last HTTP stamp
        assert unit.send(HOLD, t0, cid=99, gen=10) == "hold: not running"
        assert unit.agent.beacon_serial == (99, 10)
        unit.player.stop()
        assert unit.send(STOP, 0.0, cid=99, gen=11).startswith("stop: not")
        assert unit.agent.beacon_serial == (99, 11)
    finally:
        unit.close()


def test_a_run_datagram_past_the_shows_end_does_not_start_a_loaded_unit(
        real):
    # V5: the show is 60 s; this lap began 61 s ago and the unit is
    # loaded (preset for the next one) - not even the old +30 s slack.
    runs = count_runs(real)
    t0 = time.monotonic() - 61.0
    assert real.send(RUN, t0) == "run is over"
    assert runs == [] and real.player.status()["state"] == "loaded"


def test_every_beacon_lcd_line_fits_the_log_row():
    # V6: the 1.3" LCD draws each log line in DejaVuSans 12 px, ellipsized
    # to WIDTH - 16 px with "HH:MM:SS " in front (ui/render.py). Realistic
    # worst cases: four-digit seconds.
    from ui import render
    from ui.config import WIDTH

    if "DejaVuSans" not in str(getattr(render.FONT_S, "path", "")):
        pytest.skip("the unit's DejaVuSans font is not on this machine")
    beacon, agent, clock = stub()
    lines = []
    agent.session.runner.emit = lines.append
    beacon.handle(run_dgram(clock, 1, t0=clock.at + 1234))         # starts in
    agent.player.st = status(duration=7200.0)
    beacon.handle(run_dgram(clock, 2, gen=2, t0=clock.at - 3599))  # joined
    agent.player.st = status("running", 1.0, True)
    beacon.handle(run_dgram(clock, 3, gen=3, state=HOLD))
    agent.player.st = status("holding", 1.0, True)
    beacon.handle(run_dgram(clock, 4, gen=4, state=STOP))
    beacon._drop("cid", "", say="other PC")
    beacon._drop("cid", "", say="clock off")
    beacon._drop("cid", "", say="no show")
    beacon._drop("cid", "", say="own demo")
    assert len(lines) == 8, lines
    for line in lines:
        text = "12:34:56 " + line
        assert render._ellipsize(text, render.FONT_S, WIDTH - 16) == text


def test_the_http_path_still_answers_404_for_an_unknown_show_path(tmp_path):
    unit = Real(tmp_path, burn=False, serve=True)
    try:
        code, _ = call(unit.agent, "/show/nope", {}, token=GOLDEN["token"])
        assert code == 404
        assert unit.agent.show_cmd_at is not None
        # The beacon's own calls never move the HTTP stamp.
        stamp = unit.agent.show_cmd_at
        unit.agent.show_command("/show/hold", {}, via="beacon")
        assert unit.agent.show_cmd_at == stamp
    finally:
        unit.close()


# ---- the socket ----

def test_a_real_socket_on_port_0_hears_a_datagram_and_stop_joins(real):
    port = real.beacon.start()
    assert port and real.beacon.port == port
    assert real.beacon.status()["on"] is True
    t0 = time.monotonic() + 3.0
    data = pack(5, 1, 1, [{"host": "radxa-01", "show": SHOW_ID,
                           "state": RUN, "now": time.monotonic(), "t0": t0,
                           "pos": -3.0}])
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(b"noise", ("127.0.0.1", port))
        sender.sendto(data, ("127.0.0.1", port))
    finally:
        sender.close()
    assert wait_until(lambda: real.player.status()["state"] == "running",
                      timeout=3)
    assert real.beacon.status()["dropped"]["bad"] == 1
    thread = real.beacon._thread
    started = time.monotonic()
    real.beacon.stop()
    assert not thread.is_alive()
    assert time.monotonic() - started < 2.5
    assert real.beacon.status()["on"] is False
    real.beacon.stop()                   # twice is harmless

