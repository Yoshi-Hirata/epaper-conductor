"""The one-way beacon end to end (docs/BEACON.md sections 6 and 7).

The bench of tests/test_show_e2e.py - a real Fleet and two real unit
Agents with real ShowPlayers on the FakeBus, HTTP on 127.0.0.1 - plus a
real conductor.beacon.BeaconSender and a ui.beacon.Beacon listener per
unit (port 0). radxa-01 is unit A, radxa-02 unit B.

"Cut" is the garment the beacon exists for: the router reaches it, its
replies never reach the router. Every HTTP exchange with it times out
(poll and post alike), while the clock offset the Conductor measured
before the cut stays - samples never age (section 1).
"""

from __future__ import annotations

import socket
import sys
import time
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conductor.beacon import BeaconSender, beacon_key, pack
from tests.test_show_e2e import burned, stage, timeline  # noqa: F401 - fixture
from tests.test_ui_remote import wait_until
from ui import beacon as unit_beacon

A, B = "radxa-01", "radxa-02"
MS = 0.001
# The beacon's run, the beacon's T0: the unit takes entry.t0 as it comes,
# and entry.t0 IS run.t0 + offset - the same float the Conductor holds.
T0_EXACT = 1 * MS
# A unit fed by HTTP got run.t0 + offset with the offset of the moment of
# the post; a poll since may have improved the best sample a little.
T0_HTTP = 5 * MS


class Air:
    """The test's radio: a socket factory for the BeaconSender that sends
    every datagram, as the Conductor packed it, to the listening units
    that can hear right now - and keeps a copy of everything that went
    on air (scenario 3 replays one)."""

    def __init__(self):
        self.ports = {}                 # unit -> its listener's port
        self.deaf = set()               # units that hear nothing for now
        self.heard = []                 # every datagram sent, in order
        self._out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def factory(self, family=socket.AF_INET, kind=socket.SOCK_DGRAM, *_):
        air = self

        class _Socket:
            def setsockopt(self, *_):
                pass

            def bind(self, *_):
                pass

            def sendto(self, data, _address):
                air.heard.append(bytes(data))
                for name, port in list(air.ports.items()):
                    if name not in air.deaf:
                        air.deliver(data, name)
                return len(data)

            def close(self):
                pass
        return _Socket()

    def deliver(self, data, name):
        self._out.sendto(data, ("127.0.0.1", self.ports[name]))

    def close(self):
        self._out.close()


def decoded(data):
    return unit_beacon.unpack(data, beacon_key(None))


class Bench:
    def __init__(self, ws, fleet, units):
        self.ws, self.fleet, self.units = ws, fleet, units
        self.senders = []
        self.air = Air()
        for name, unit in units.items():
            unit.beacon = unit_beacon.Beacon(unit.agent, port=0,
                                             host="127.0.0.1")
            unit.agent.beacon = unit.beacon
            unit.beacon.start()
            self.air.ports[name] = unit.beacon.port
            # Every /show/* command the unit ran, and by which path: the
            # HTTP handler and the beacon both go through show_command.
            unit.calls = []
            command = unit.agent.show_command

            def spy(path, body, via="http", headers=None, serial=None,
                    unit=unit, command=command):
                unit.calls.append((path, via))
                return command(path, body, via, headers=headers,
                               serial=serial)
            unit.agent.show_command = spy

    def upload(self, second_at=20, duration=60):
        results = self.fleet.upload(timeline(self.ws, second_at=second_at,
                                             duration=duration))
        assert all(r["ok"] for r in results.values()), results
        assert burned(self.fleet, self.units)
        for unit in self.units.values():
            unit.calls.clear()

    def cut(self, name):
        """Its replies never arrive: every exchange times out, the
        offset measured so far stays."""
        link = self.fleet.links[name]
        before = (len(link._samples), link.offset)

        def lost(*_args, **_kwargs):
            raise socket.timeout("timed out")
        link._exchange = lost
        assert before[1] is not None
        return before

    def sender(self, port=None, enabled=True, air=False):
        sender = BeaconSender(
            self.fleet, token=None, enabled=enabled,
            port=port if port is not None else 9,
            sock_factory=self.air.factory if air else None)
        sender.start()
        self.senders.append(sender)
        return sender

    def want_t0(self, name):
        return self.fleet.run["t0"] + self.fleet.links[name].offset

    def beacon_calls(self, name):
        return [path for path, via in self.units[name].calls
                if via == "beacon"]

    def dropped(self, name):
        return self.units[name].beacon.status()["dropped"]

    def close(self):
        for sender in self.senders:
            sender.stop()
        for unit in self.units.values():
            unit.beacon.stop()
        self.air.close()


@pytest.fixture
def bench(stage):  # noqa: F811 - the imported fixture
    ws, fleet, units, _ = stage
    bench = Bench(ws, fleet, units)
    yield bench
    bench.close()


def stamped_lines(unit, text):
    return [line for line in unit.runner.recent(200) if text in line]


# ---- 1. a unit whose replies never arrive follows the whole show by UDP ----

def test_a_cut_unit_follows_start_hold_resume_seek_next_and_stop(bench):
    fleet, units = bench.fleet, bench.units
    a, b = units[A], units[B]
    bench.upload(second_at=20)
    samples, offset_b = bench.cut(B)
    # Unicast to 127.0.0.1:<B's port>, a real socket: only B hears it.
    sender = bench.sender(port=b.beacon.port)

    results = fleet.start_show(lead_s=2.0)
    assert results[A]["ok"] and not results[B]["ok"]
    assert "timed out" in results[B]["error"]
    t0 = fleet.run["t0"]
    # B starts from the beacon alone, inside the countdown...
    assert wait_until(lambda: b.player.state == "running", timeout=2)
    assert fleet._clock() < t0
    # ...on exactly the T0 HTTP would have given it.
    assert abs(b.player.t0 - (t0 + offset_b)) < T0_EXACT
    assert fleet.links[B].offset == offset_b      # nothing new measured
    assert len(fleet.links[B]._samples) == samples
    assert a.player.state == "running"
    assert abs(a.player.t0 - bench.want_t0(A)) < T0_HTTP
    assert b.beacon.status()["applied"]["what"] == "run"
    assert stamped_lines(b, "beacon run, starts in ")
    # The 0:00 cue fires on both garments together.
    assert wait_until(lambda: all(u.player.applied == "q00"
                                  for u in units.values()), timeout=6)
    assert abs(a.shows[-1] - b.shows[-1]) < 0.05

    fleet.hold()
    assert a.player.state == "holding"
    assert wait_until(lambda: b.player.state == "holding", timeout=2)

    fleet.resume()
    assert wait_until(lambda: b.player.state == "running"
                      and abs(b.player.t0 - bench.want_t0(B)) < T0_EXACT,
                      timeout=2)
    assert abs(a.player.t0 - bench.want_t0(A)) < T0_HTTP

    mode, _ = fleet.seek(10.0, lead_s=0.5)
    assert mode == "running"
    assert wait_until(lambda: abs(b.player.t0 - bench.want_t0(B)) < T0_EXACT,
                      timeout=2)
    assert abs(a.player.t0 - bench.want_t0(A)) < T0_HTTP

    results = fleet.next_cue(lead_s=1.2)          # 0:20's cue, now
    assert results[A]["ok"] and not results[B]["ok"]
    assert wait_until(lambda: abs(b.player.t0 - bench.want_t0(B)) < T0_EXACT,
                      timeout=2)
    assert wait_until(lambda: all(u.player.applied == "q01"
                                  for u in units.values()), timeout=6)
    assert abs(a.shows[-1] - b.shows[-1]) < 0.05

    fleet.stop_show()
    assert a.player.state == "stopped"
    assert wait_until(lambda: b.player.state == "stopped", timeout=2)
    # One beacon call per command, nothing repeated by the 5 Hz datagrams
    # (level-triggered), and not one HTTP command reached B after the cut.
    assert bench.beacon_calls(B) == ["/show/run", "/show/hold", "/show/run",
                                     "/show/run", "/show/run", "/show/stop"]
    assert [via for _, via in b.calls] == ["beacon"] * 6
    assert bench.beacon_calls(A) == []           # A never heard a datagram

    # B is started again by someone else (KEY1, say) while the STOP is
    # still being sent: the later ticks of the same stop leave it alone.
    b.player.run(time.monotonic() - 1.0, fleet.shows[B]["id"])
    stops = sender.snapshot()["sent"]
    assert wait_until(lambda: sender.snapshot()["sent"] >= stops + 3,
                      timeout=3)
    time.sleep(0.3)
    assert b.player.state == "running"
    assert bench.beacon_calls(B).count("/show/stop") == 1
    assert b.beacon.status()["ignored"] == "stop: told already"
    assert len(stamped_lines(b, "beacon stop")) == 1
    assert sender.snapshot()["errors"] == 0


# ---- 2. both paths deliver START: the 0:00 cue fires once ----

def both_paths_start(bench, http_first):
    """START with a 1 s countdown to A over HTTP and the beacon; which
    lands first is forced: the beacon is held off the air until HTTP has
    answered, or the HTTP post is slowed (the weak link the beacon is
    for) until the beacon has run the unit. The 0:00 cue is sent at
    -1.0 s, so it fires inside the countdown, before the second path
    lands."""
    fleet, units, air = bench.fleet, bench.units, bench.air
    a = units[A]
    bench.upload(second_at=20)
    air.deaf = {B} | ({A} if http_first else set())
    bench.sender(air=True)
    if not http_first:
        link = fleet.links[A]
        post = link.post

        def slow(path, body, *args, **kwargs):
            if path == "/show/run":
                # Landing just after the beacon's run fired the 0:00 cue,
                # before the unit's landing check (+0.1 s) has called it
                # applied - the window the race hit on the bench.
                assert wait_until(lambda: len(a.shows) >= 1, timeout=2)
            return post(path, body, *args, **kwargs)
        link.post = slow

    results = fleet.start_show(lead_s=1.0)
    assert results[A]["ok"]
    air.deaf = {B}
    assert wait_until(lambda: a.player.applied == "q00", timeout=6)
    t0 = a.player.t0
    assert abs(t0 - bench.want_t0(A)) < T0_HTTP
    time.sleep(1.5)                           # ~7 more datagrams at 5 Hz
    assert a.beacon.status()["gen"] == fleet._run_gen
    assert a.beacon.status()["ignored"] == "in step"
    assert [p for p, via in a.calls if via == "http"] == ["/show/run"]
    assert bench.beacon_calls(A) == ([] if http_first else ["/show/run"])
    assert a.player.state == "running"
    assert a.player.t0 == t0                  # the run never restarted
    assert len(a.shows) == 1                  # the 0:00 cue, exactly once
    assert fleet.snapshot()["corrections"] == []


def test_http_then_beacon_start_fires_the_first_cue_once(bench):
    both_paths_start(bench, http_first=True)


def test_beacon_then_http_start_fires_the_first_cue_once(bench):
    # The HTTP /show/run of the SAME T0, landing after the beacon's run
    # fired the 0:00 cue, is answered without a call into the player
    # (ui/agent.py's SAME_T0_S, review U1, 2026-11-20) - before, it
    # restarted the run and the cue fired a second time.
    both_paths_start(bench, http_first=False)


# ---- 3. ordering against HTTP: an older gen never undoes a HOLD ----

def test_an_older_beacon_after_an_http_hold_leaves_the_unit_holding(bench):
    fleet, units, air = bench.fleet, bench.units, bench.air
    a = units[A]
    bench.upload(second_at=20)
    air.deaf = {A, B}                             # on air, heard by nobody
    bench.sender(air=True)

    fleet.start_show(lead_s=0.5)
    gen_run = fleet._run_gen
    assert a.player.state == "running"
    assert wait_until(lambda: any(decoded(d)[0]["gen"] == gen_run
                                  for d in air.heard), timeout=2)
    captured = [d for d in air.heard if decoded(d)[0]["gen"] == gen_run][-1]
    header, entries = decoded(captured)
    entry = next(e for e in entries if e["host"] == A)
    assert entry["state"] == 1

    fleet.hold()                                  # by HTTP, gen_run + 1
    gen_hold = fleet._run_gen
    assert gen_hold == gen_run + 1
    assert a.agent.show_cmd_serial == (fleet.beacon_cid, gen_hold)
    assert a.player.state == "holding"
    held_t0 = a.player.t0

    # The run datagram, sent before the HOLD, arrives after it.
    air.deliver(captured, A)
    assert wait_until(lambda: bench.dropped(A)["gen"] == 1, timeout=2)
    assert a.player.state == "holding"
    # ...and even with a `now` later than the HOLD (the clock-based G5
    # alone would let this one through): the serial says older.
    later = time.monotonic() + 0.5
    stale = pack(fleet.beacon_cid, gen_run, header["seq"] + 1,
                 [dict(entry, now=later, pos=later - entry["t0"])],
                 beacon_key(None))
    air.deliver(stale, A)
    assert wait_until(lambda: bench.dropped(A)["gen"] == 2, timeout=2)
    assert a.player.state == "holding"
    assert bench.beacon_calls(A) == []

    # The HOLD's own gen, heard from now on: accepted, and nothing to do.
    air.deaf = set()
    assert wait_until(lambda: a.beacon.status()["gen"] == gen_hold
                      and a.beacon.status()["ignored"] == "hold: not running",
                      timeout=2)
    time.sleep(0.5)
    assert a.player.state == "holding" and a.player.t0 == held_t0
    assert bench.dropped(A)["gen"] == 2
    assert bench.beacon_calls(A) == []
    assert a.beacon.status()["applied"] is None


# ---- 4. switched off: nothing on air, START exactly as before ----

def test_with_the_switch_off_nothing_is_sent_and_start_is_unchanged(bench):
    fleet, units, air = bench.fleet, bench.units, bench.air
    a, b = units[A], units[B]
    bench.upload(second_at=20)
    bench.cut(B)
    sender = bench.sender(air=True, enabled=False)

    results = fleet.start_show(lead_s=0.5)
    assert results[A]["ok"] and not results[B]["ok"]
    assert wait_until(lambda: a.player.applied == "q00", timeout=6)
    time.sleep(1.0)
    assert air.heard == []
    assert sender.snapshot()["sent"] == 0 and sender.snapshot()["enabled"] is False
    for unit in units.values():
        status = unit.beacon.status()
        assert status["heard_s"] is None and status["applied"] is None
        assert sum(status["dropped"].values()) == 0
    assert len(a.shows) == 1
    assert abs(a.player.t0 - bench.want_t0(A)) < T0_HTTP
    assert [p for p, via in a.calls] == ["/show/run"]
    assert b.player.state == "loaded" and b.calls == []

    # The live toggle: on, and the unit HTTP cannot reach joins late.
    sender.set_enabled(True)
    assert wait_until(lambda: b.player.state == "running"
                      and abs(b.player.t0 - bench.want_t0(B)) < T0_EXACT,
                      timeout=2)
    assert bench.beacon_calls(B) == ["/show/run"]
    assert wait_until(lambda: a.beacon.status()["ignored"] == "in step",
                      timeout=2)
    assert bench.beacon_calls(A) == []


# ---- 5. a unit that rebooted since its last poll is not steered ----

def test_a_unit_whose_clock_moved_since_the_last_poll_ignores_the_beacon(bench):
    fleet, units = bench.fleet, bench.units
    b = units[B]
    bench.upload(second_at=20)
    bench.cut(B)
    # B restarted: its monotonic clock now reads 100 s off what the
    # Conductor measured, and no poll has come back to say so.
    link = fleet.links[B]
    with link._lock:
        link._samples = deque(((rtt, offset - 100.0)
                               for rtt, offset in link._samples),
                              maxlen=link._samples.maxlen)
    bench.sender(port=b.beacon.port)

    fleet.start_show(lead_s=0.5)
    assert wait_until(lambda: bench.dropped(B)["now"] >= 3, timeout=3)
    assert b.player.state == "loaded"
    assert bench.beacon_calls(B) == []
    status = b.beacon.status()
    assert status["applied"] is None
    assert status["ignored"] == "clock out of step"
    assert status["heard_s"] is not None          # heard, and dropped


# ---- 6. a unit playing its own demo is never touched ----

def test_a_unit_playing_its_own_demo_ignores_run_hold_and_stop(bench):
    fleet, units = bench.fleet, bench.units
    a, b = units[A], units[B]
    bench.upload(second_at=20)
    bench.cut(B)
    # The very show the PC gave it, stored and started as B's own demo:
    # the ids match, so only G6 stands between it and the beacon.
    b.player.load(dict(fleet.shows[B]), demo=True, name="demo", slug="demo")
    assert wait_until(lambda: b.player.status()["burn"]["state"] == "burned",
                      timeout=5)
    b.player.run(time.monotonic() + 0.2)
    demo_t0 = b.player.t0
    assert b.player.status()["demo"] is True
    bench.sender(port=b.beacon.port)

    seen = 0
    for command in (lambda: fleet.start_show(lead_s=0.5), fleet.hold,
                    fleet.stop_show):
        command()
        assert wait_until(lambda: bench.dropped(B)["demo"] >= seen + 2,
                          timeout=3)
        seen = bench.dropped(B)["demo"]
        assert b.player.state == "running" and b.player.t0 == demo_t0
    assert a.player.state == "stopped"            # A did follow, by HTTP
    assert bench.beacon_calls(B) == []
    assert b.beacon.status()["applied"] is None
    assert b.beacon.status()["ignored"] == "the unit plays its own demo"
