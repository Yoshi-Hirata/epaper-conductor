"""WIFI's reconnect watchdog (ui/wifi.py, 2026-11-20).

radxa-08 at the venue: back in range of R2.net after the edge of
coverage, the WPA handshake timed out, NetworkManager took it for a
wrong password (`no-secrets`) and never tried that profile again on its
own. The watchdog runs `con up` after 30 s of `disconnected`. The fake
nmcli is tests/test_ui_wifi.py's, with a clock of our own, so nothing
here waits 30 real seconds or touches the network of this machine.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import render
from ui.agent import Agent
from ui.app import Screen
from ui.config import WIDTH
from ui.wifi import (CONNECTING, DONE, FAILED, IDLE, LOCKED, NM_DISCONNECTED,
                     RECONNECT_AFTER_S, RECONNECT_RETRY_S, Wifi, parse_state,
                     up_command)
from tests.test_ui_wifi import HOTSPOT, REFUSAL, ROUTER, FakeNmcli, make_app
from tests.test_ui_runner import wait_until

VENUE = "R2.net"
NO_SECRETS = ("Error: Connection activation failed: Secrets were required, "
              "but not provided.")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def venue_fake(active=VENUE, **kwargs):
    """The venue's unit: R2.net (autoconnect, priority 10), the office
    router (autoconnect, priority 0), AZ-Epaper as a client profile
    (autoconnect=no, like every unit but radxa-05)."""
    fake = FakeNmcli(active=active, **kwargs)
    fake.profiles = {
        VENUE: {"mode": "infrastructure", "ip": "192.168.0.108",
                "priority": 10},
        ROUTER: {"mode": "infrastructure", "ip": "192.168.51.108"},
        HOTSPOT: {"mode": "infrastructure", "ip": "192.168.4.18",
                  "autoconnect": "no"},
    }
    return fake


def make_dog(fake=None, **kwargs):
    fake = fake if fake is not None else venue_fake()
    clock = Clock()
    lines = []
    kwargs.setdefault("watchdog", True)
    wifi = Wifi(run=fake, poll_s=60.0, echo_log=False, clock=clock,
                notify=lines.append, **kwargs)
    return wifi, fake, clock, lines


def look(wifi, clock=None, at=None):
    """One turn of the reader's loop: poll, then the watchdog - and,
    when it started a `con up`, wait for that worker."""
    if at is not None:
        clock.t = at
    started = wifi.watch(wifi.poll())
    if started:
        wifi.join_reconnect(5.0)
    return started


def lose_link(wifi, fake, clock):
    """On the venue network, then out of range: the device sits at
    `disconnected` from clock.t on."""
    assert not look(wifi)
    fake.active = None
    assert not look(wifi)               # the 30 s start here
    return clock.t


# ---- the rule ----

def test_thirty_seconds_disconnected_brings_the_last_network_back():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    assert not look(wifi, clock, t0 + RECONNECT_AFTER_S - 0.1)
    assert fake.ups == [] and lines == []
    assert look(wifi, clock, t0 + RECONNECT_AFTER_S)
    assert fake.ups == [VENUE]
    assert up_command(VENUE) in fake.calls               # sudo -n nmcli --wait 45
    assert lines == [f"wifi: reconnect {VENUE}", f"wifi: on {VENUE}"]
    assert wifi.snapshot()["profile"] == VENUE           # the cache moved
    assert not wifi.busy
    # Back on: nothing more to do, however long it stays up.
    for step in range(1, 10):
        assert not look(wifi, clock, t0 + 30 + 60 * step)
    assert fake.ups == [VENUE]


def test_a_failed_try_waits_thirty_seconds_then_tries_again():
    wifi, fake, clock, lines = make_dog()
    fake.fail = {VENUE}                 # the weak AP: handshake times out
    t0 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [VENUE]
    assert lines == [f"wifi: reconnect {VENUE}",
                     "wifi: up failed: No suitable device found for this "
                     "connection (device wlan0 not available because "
                     "profile is not compatible with device)."]
    info = wifi.reconnect_info()
    assert info["attempts"] == 1 and info["last"] == VENUE
    assert info["last_error"] == REFUSAL                 # nmcli's whole line
    # The next 30 s: nothing, however often the reader looks.
    for dt in (0.0, 10.0, 20.0, 29.9):
        assert not look(wifi, clock, t0 + 30 + dt)
    assert fake.ups == [VENUE]
    assert look(wifi, clock, t0 + 60)
    assert fake.ups == [VENUE, VENUE]
    assert wifi.reconnect_info()["attempts"] == 2
    # The same failure again: the journal hears it, the LCD does not.
    assert len(lines) == 2
    assert sum("wifi: reconnect" in line for line in wifi.recent(20)) == 2
    # Back in range: the third try takes.
    fake.fail = set()
    assert look(wifi, clock, t0 + 90)
    assert fake.ups == [VENUE] * 3 and lines[-1] == f"wifi: on {VENUE}"
    assert wifi.reconnect_info()["last_error"] is None


def test_the_retry_counts_from_the_end_of_a_slow_try():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.fail = {VENUE}

    def slow(args, timeout):
        if args[:2] == ["sudo", "-n"]:
            clock.t += 45.0             # nmcli's --wait 45, used up
        return FakeNmcli.__call__(fake, args, timeout)
    wifi._run = slow
    assert look(wifi, clock, t0 + 30)   # ends at t0 + 75
    assert not look(wifi, clock, t0 + 100)
    assert look(wifi, clock, t0 + 105)
    assert fake.ups == [VENUE, VENUE]


def test_no_secrets_is_said_by_its_reason_on_the_lcd():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    wifi._run = lambda args, timeout: (
        (4, NO_SECRETS) if args[:2] == ["sudo", "-n"]
        else FakeNmcli.__call__(fake, args, timeout))
    assert look(wifi, clock, t0 + 30)
    assert lines[-1] == ("wifi: up failed: Secrets were required, but not "
                         "provided.")
    assert wifi.reconnect_info()["last_error"] == NO_SECRETS


# ---- which profile ----

def test_the_operators_choice_this_boot_wins_over_priority():
    # Moved onto AZ-Epaper (autoconnect=no, no priority) from the LCD or
    # /wifi/select: that is where the watchdog brings it back to.
    wifi, fake, clock, lines = make_dog(fake=venue_fake(active=HOTSPOT))
    t0 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [HOTSPOT]


def test_never_connected_this_boot_takes_the_highest_priority_autoconnect():
    fake = venue_fake(active=None)
    fake.profiles[HOTSPOT]["priority"] = 50     # autoconnect=no: not a pick
    wifi, fake, clock, lines = make_dog(fake=fake)
    assert not look(wifi, clock, 0.0)
    assert look(wifi, clock, 30.0)
    assert fake.ups == [VENUE]
    # A tie goes to the first one nmcli lists.
    fake2 = venue_fake(active=None)
    fake2.profiles[VENUE]["priority"] = 0
    wifi2, fake2, clock2, _ = make_dog(fake=fake2)
    assert not look(wifi2, clock2, 0.0)
    assert look(wifi2, clock2, 30.0)
    assert fake2.ups == [VENUE]


def test_only_an_activated_profile_counts_as_the_last_one():
    # The exhibition's switch to AZ-Epaper seen half-way (CONNECTION
    # names it while it is still trying) is not where the unit lives.
    fake = venue_fake(active=None)
    wifi, fake, clock, lines = make_dog(fake=fake)
    fake.active, fake.nm_state = HOTSPOT, "50 (connecting (configuring))"
    assert not look(wifi, clock, 0.0)
    fake.active, fake.nm_state = None, None
    assert not look(wifi, clock, 1.0)
    assert look(wifi, clock, 31.0)
    assert fake.ups == [VENUE]


def test_a_vanished_last_profile_falls_back_to_priority():
    wifi, fake, clock, lines = make_dog(fake=venue_fake(active=ROUTER))
    t0 = lose_link(wifi, fake, clock)
    del fake.profiles[ROUTER]           # deleted by hand meanwhile
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [VENUE]


def radxa05_fake(active):
    """radxa-05: AZ-Epaper IS the hotspot (mode ap). Even made
    autoconnect=yes with the top priority here, it is never a pick."""
    fake = venue_fake(active=active)
    fake.profiles[HOTSPOT] = {"mode": "ap", "ip": "10.42.0.1",
                              "autoconnect": "yes", "priority": 99}
    return fake


def test_a_hotspot_unit_is_left_alone():
    # EXHIBITION mode: radxa-05 up as the hotspot - activated, so the
    # watchdog does nothing at all.
    wifi, fake, clock, lines = make_dog(fake=radxa05_fake(active=HOTSPOT))
    for at in (0.0, 30.0, 300.0):
        assert not look(wifi, clock, at)
    assert wifi.device_state != NM_DISCONNECTED
    # ...and if the hotspot itself drops, it is not raised from here,
    # nor is radxa-05 moved onto a client network.
    fake.active = None
    for at in (301.0, 340.0, 1000.0):
        assert not look(wifi, clock, at)
    assert fake.ups == [] and lines == []


def test_radxa05_before_any_network_is_exhibition_net_shs():
    # At boot radxa/exhibition-net.sh waits 90 s (longer while a known
    # router is in sight) and then raises the hotspot: a `con up` of the
    # router from here could race it off the radio.
    wifi, fake, clock, lines = make_dog(fake=radxa05_fake(active=None))
    for at in (0.0, 30.0, 120.0, 600.0):
        assert not look(wifi, clock, at)
    assert fake.ups == []


def test_radxa05_as_a_client_gets_its_router_back_never_the_hotspot():
    wifi, fake, clock, lines = make_dog(fake=radxa05_fake(active=VENUE))
    t0 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [VENUE]


# ---- the states it leaves alone ----

@pytest.mark.parametrize("nm_state", [
    "20 (unavailable)", "10 (unmanaged)", "40 (connecting (prepare))",
    "50 (connecting (configuring))", "60 (connecting (need authentication))",
    "70 (connecting (getting IP configuration))", "100 (connected)",
    "120 (failed)"])
def test_no_try_unless_the_device_is_disconnected(nm_state):
    wifi, fake, clock, lines = make_dog(fake=venue_fake(active=None))
    fake.nm_state = nm_state
    for at in (0.0, 30.0, 60.0, 600.0):
        assert not look(wifi, clock, at)
    assert fake.ups == [] and lines == []


def test_the_thirty_seconds_restart_when_the_state_leaves_disconnected():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.nm_state = "60 (connecting (need authentication))"
    assert not look(wifi, clock, t0 + 20)
    fake.nm_state = None                    # disconnected again at +25
    assert not look(wifi, clock, t0 + 25)
    assert not look(wifi, clock, t0 + 54.9)
    assert fake.ups == []
    assert look(wifi, clock, t0 + 55)
    assert fake.ups == [VENUE]


# ---- what it waits for ----

def test_never_while_a_switch_is_pending_or_in_flight():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    # The exhibition's timed move (/wifi/select with after_s) counting
    # down: a deliberate move, not to be pulled back from under.
    wifi.schedule(HOTSPOT, 100.0)
    try:
        assert not look(wifi, clock, t0 + 30)
        assert not look(wifi, clock, t0 + 90)
    finally:
        wifi.cancel_pending()
    # The row's (or /wifi/select's) own `con up` running.
    fake.release.clear()
    fake.nm_state = "30 (disconnected)"     # even if nmcli reads it so
    assert wifi.switch_to(HOTSPOT)
    assert wifi.phase == CONNECTING
    try:
        assert not look(wifi, clock, t0 + 120)
        assert fake.ups == [HOTSPOT]
    finally:
        fake.release.set()
        wifi.join(5.0)
    assert wifi.phase == DONE and fake.ups == [HOTSPOT]
    assert lines == []


def test_the_exhibition_move_lands_and_then_stands():
    # The timer fires, AZ-Epaper comes up; later it drops: the watchdog
    # brings back AZ-Epaper, not the venue's higher-priority router.
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    wifi.schedule(HOTSPOT, 0.05)
    assert wait_until(lambda: wifi.phase == DONE)
    wifi.join(5.0)
    t0 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [HOTSPOT, HOTSPOT]


def test_while_it_runs_the_row_and_select_wait():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.release.clear()
    clock.t = t0 + 30
    assert wifi.watch(wifi.poll())
    try:
        assert wifi.busy and wifi.reconnecting == VENUE
        assert wifi.status_text() == f"no network - reconnecting {VENUE}…"
        assert not wifi.switch_to(HOTSPOT)          # /wifi/select: 409
        assert not wifi.switch(wifi.profiles[2])    # nothing claimed
        assert wifi.phase != CONNECTING
        # Nor a second watchdog try, however long it takes.
        assert not wifi.watch(wifi.poll())
        clock.t += 100
        assert not wifi.watch(wifi.poll())
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
    assert fake.ups == [VENUE] and not wifi.busy


def test_the_lcd_loop_and_status_never_wait_for_its_nmcli():
    from tests.test_ui_remote import call, make_session

    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.release.clear()                    # `con up` hangs in nmcli
    app, _ = make_app(wifi)
    session, runner, _ = make_session()
    agent = Agent(session, port=0, host="127.0.0.1", wifi=wifi)
    agent.start()
    try:
        clock.t = t0 + 30
        began = time.monotonic()
        assert wifi.watch(wifi.poll())      # the reader is not held either
        assert wait_until(lambda: fake.ups == [VENUE])
        app.screen = Screen.WIFI
        app.handle("key1_hold")             # busy: refused, not run
        assert wifi.status_text() == f"reconnecting {VENUE} - KEY2 back"
        app.draw()
        app.screen = Screen.MENU
        app.draw()
        code, status = call(agent, "/status")
        assert time.monotonic() - began < 2.0
        assert code == 200
        # A try is booked when nmcli answers (nothing may run between
        # the last state read and `con up`, review of 3f43a10).
        assert status["wifi"]["reconnect"]["attempts"] == 0
        assert fake.ups == [VENUE]          # the held KEY1 started nothing
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
    try:
        code, status = call(agent, "/status")
        assert status["wifi"]["reconnect"] == {
            "attempts": 1, "last": VENUE, "last_ago_s": 0.0,
            "last_error": None}
    finally:
        agent.stop()
        runner.stop()


# ---- no nmcli, no wifi, no spam ----

def test_without_nmcli_or_a_wifi_device_nothing_is_tried_or_said():
    lines = []
    clock = Clock()
    absent = Wifi(run=lambda args, t: (127, "nmcli not installed"),
                  echo_log=False, clock=clock, notify=lines.append)
    no_radio = Wifi(run=lambda args, t: (0, "GENERAL.DEVICE:eth0\n"
                                            "GENERAL.TYPE:ethernet\n"
                                            "GENERAL.STATE:30 (disconnected)\n"
                                            "GENERAL.CONNECTION:--"),
                    echo_log=False, clock=clock, notify=lines.append)
    for wifi in (absent, no_radio):
        for at in (0.0, 30.0, 60.0, 3600.0):
            assert not look(wifi, clock, at)
        assert wifi.recent(10) == [] and wifi.device_state is None
    assert lines == []


def test_a_failing_read_is_not_disconnected_and_restarts_the_clock():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.broken = "Error: NetworkManager is not running."
    for dt in (30.0, 60.0, 90.0):
        assert not look(wifi, clock, t0 + dt)
    fake.broken = None
    assert not look(wifi, clock, t0 + 100)
    assert look(wifi, clock, t0 + 130)
    assert fake.ups == [VENUE] and len(lines) == 2


def test_switched_off_it_never_acts():
    wifi, fake, clock, lines = make_dog(watchdog=False)
    t0 = lose_link(wifi, fake, clock)
    for dt in (30.0, 60.0, 600.0):
        assert not look(wifi, clock, t0 + dt)
    assert fake.ups == [] and wifi.reconnect_info() is None


def test_the_reader_thread_runs_the_watchdog():
    fake = venue_fake(active=None)
    lines = []
    wifi = Wifi(run=fake, poll_s=0.02, echo_log=False, notify=lines.append,
                reconnect_after=0.1, reconnect_retry=0.1)
    try:
        wifi.start_reader()
        assert wait_until(lambda: wifi.snapshot()["profile"] == VENUE)
        assert fake.ups == [VENUE]
        assert lines == [f"wifi: reconnect {VENUE}", f"wifi: on {VENUE}"]
    finally:
        wifi.close()


# ---- the log lines ----

def test_its_lcd_lines_fit_the_log_row():
    # The REMOTE screen draws each runner log line in DejaVuSans 12 px,
    # ellipsized to WIDTH - 16 px with "HH:MM:SS " in front (as
    # tests/test_ui_beacon.py measures the beacon's). The attempt and
    # the success line fit whole with the venue's and the exhibition's
    # names; a refusal shows as much of nmcli's reason as fits.
    lines = []
    for name in (VENUE, HOTSPOT):
        fake = venue_fake(active=name)
        wifi, fake, clock, said = make_dog(fake=fake)
        t0 = lose_link(wifi, fake, clock)
        assert look(wifi, clock, t0 + 30)
        lines += said
    assert lines == [f"wifi: reconnect {VENUE}", f"wifi: on {VENUE}",
                     f"wifi: reconnect {HOTSPOT}", f"wifi: on {HOTSPOT}"]
    lines.append("wifi: still down, try 100")      # _failed()'s summary
    for line in lines:                  # a backstop wherever the font is
        assert len(line) <= 25, line
    if "DejaVuSans" not in str(getattr(render.FONT_S, "path", "")):
        pytest.skip("the unit's DejaVuSans font is not on this machine")
    for line in lines:
        text = "12:34:56 " + line
        assert render._ellipsize(text, render.FONT_S, WIDTH - 16) == text


# ---- review of 9858e7a, 1: incomplete reads never drive a decision ----

def flaky_reads(wifi, fake, name):
    """`con show <name>` times out while flaky["on"]."""
    flaky = {"on": False}

    def run(args, timeout):
        if (flaky["on"] and args[-3:-1] == ["con", "show"]
                and args[-1] == name):
            return 124, "Error: Timeout expired"
        return FakeNmcli.__call__(fake, args, timeout)
    wifi._run = run
    return flaky


def test_a_flaky_hotspot_read_on_radxa05_never_brings_the_router_up():
    # The reviewer's repro: radxa-05 at boot, no network yet, one poll
    # where `con show AZ-Epaper` times out - the AP profile then looked
    # like a client and the guard let the router's `con up` through.
    fake = radxa05_fake(active=None)
    fake.profiles[HOTSPOT]["autoconnect"] = "no"    # as in EXHIBITION.md
    wifi, fake, clock, lines = make_dog(fake=fake)
    flaky = flaky_reads(wifi, fake, HOTSPOT)
    assert not look(wifi, clock, 0.0)
    flaky["on"] = True
    for at in (30.0, 60.0, 600.0):
        assert not look(wifi, clock, at)
    assert dict((p.name, p.mode) for p in wifi.profiles)[HOTSPOT] == "unknown"
    assert wifi.device_state is None
    assert fake.ups == [] and lines == []


def test_an_unread_mode_is_unknown_and_restarts_the_clock():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    flaky = flaky_reads(wifi, fake, ROUTER)
    flaky["on"] = True
    assert not look(wifi, clock, t0 + 30)
    assert wifi.rows()[1] == (ROUTER, "unknown", False)   # not "client"
    flaky["on"] = False
    assert not look(wifi, clock, t0 + 31)               # 30 s from here
    assert not look(wifi, clock, t0 + 60.9)
    assert look(wifi, clock, t0 + 61)
    assert fake.ups == [VENUE]


def test_the_hotspot_guard_sees_past_the_lists_eight():
    # Codex: an AP profile beyond the eight rows the screen lists was
    # invisible to the guard.
    fake = venue_fake(active=None)
    for i in range(8):
        fake.profiles[f"spare-{i}"] = {"mode": "infrastructure",
                                       "ip": "10.0.0.1"}
    fake.profiles["late-AP"] = {"mode": "ap", "ip": "10.42.0.1",
                                "autoconnect": "no"}
    wifi, fake, clock, lines = make_dog(fake=fake)
    for at in (0.0, 30.0, 300.0):
        assert not look(wifi, clock, at)
    assert len(wifi.profiles) == 8 and len(wifi.all_profiles) == 12
    assert wifi.all_profiles[-1].mode == "hotspot"
    assert fake.ups == []


def test_a_translated_state_still_reads_as_disconnected():
    assert parse_state("30 (切断されました)") == 30
    assert parse_state("100 (接続済み)") == 100
    assert parse_state("") is None and parse_state("--") is None
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    fake.active, fake.nm_state = None, "30 (切断されました)"
    t0 = clock.t
    assert not look(wifi)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [VENUE]


def test_a_wifi_block_without_a_state_line_is_left_alone():
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    fake.active = None

    def no_state(args, timeout):
        code, output = FakeNmcli.__call__(fake, args, timeout)
        if args[-2:] == ["dev", "show"]:
            output = "\n".join(line for line in output.splitlines()
                               if not line.startswith("GENERAL.STATE"))
        return code, output
    wifi._run = no_state
    for dt in (0.0, 30.0, 60.0, 600.0):
        assert not look(wifi, clock, clock.t + dt)
    assert wifi.device_state is None and fake.ups == [] and lines == []


def test_nmcli_runs_with_its_words_in_english(monkeypatch):
    import subprocess as sp
    from ui import wifi as wifi_mod

    seen = {}
    asked = []

    def fake_run(args, **kwargs):
        if args == ["locale", "-a"]:
            asked.append(1)
            return SimpleNamespace(returncode=0, stdout="C\nC.utf8\nPOSIX\n",
                                   stderr="")
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="out", stderr="")
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ja")
    monkeypatch.setattr(sp, "run", fake_run)
    monkeypatch.setattr(wifi_mod, "_nmcli_locale", None)
    assert wifi_mod.nmcli_command(["nmcli", "-t"], 5.0) == (0, "out")
    assert wifi_mod.nmcli_command(["nmcli", "-t"], 5.0) == (0, "out")
    assert asked == [1]                     # `locale -a` asked once
    env = seen["env"]
    assert env["LC_ALL"] == "C.UTF-8" and env["LANG"] == "C.UTF-8"
    assert "LANGUAGE" not in env
    assert seen["stdin"] is sp.DEVNULL and seen["timeout"] == 5.0
    # Decoded as UTF-8 whatever this process's locale (an SSID in kana).
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"

    def missing(args, **kwargs):
        raise FileNotFoundError(args[0])
    monkeypatch.setattr(sp, "run", missing)
    assert wifi_mod.nmcli_command(["nmcli"], 5.0) == (127, "nmcli not installed")
    assert Wifi()._run is wifi_mod.nmcli_command


@pytest.mark.parametrize("listing, expected", [
    ("C\nPOSIX\nen_US.utf8\n", "C"),        # no C.UTF-8 on this unit
    ("C\nC.UTF-8\nPOSIX\n", "C.UTF-8"),
    (None, "C"),                            # no `locale` tool at all
])
def test_the_locale_falls_back_to_plain_c(monkeypatch, listing, expected):
    import subprocess as sp
    from ui import wifi as wifi_mod

    seen = {}

    def fake_run(args, **kwargs):
        if args == ["locale", "-a"]:
            if listing is None:
                raise FileNotFoundError("locale")
            return SimpleNamespace(returncode=0, stdout=listing, stderr="")
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(sp, "run", fake_run)
    monkeypatch.setattr(wifi_mod, "_nmcli_locale", None)
    wifi_mod.nmcli_command(["nmcli", "-t"], 5.0)
    assert seen["env"]["LC_ALL"] == expected == seen["env"]["LANG"]


# ---- 2: a fresh read right before acting ----

def test_networkmanager_starting_its_own_activation_wins():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)

    def nm_gets_there_first(args, timeout):
        if args[-3:] == ["dev", "show", "wlan0"]:
            fake.nm_state = "40 (connecting (prepare))"
        return FakeNmcli.__call__(fake, args, timeout)
    wifi._run = nm_gets_there_first
    assert look(wifi, clock, t0 + 30)          # claimed, then dropped
    assert fake.ups == [] and lines == [] and wifi.recent(10) == []
    assert wifi.reconnect_info()["attempts"] == 0
    assert not wifi.busy
    # The reader sees NetworkManager's own try; it fails: disconnected
    # again, the usual 30 s from there.
    wifi._run = fake
    assert not look(wifi, clock, t0 + 35)
    fake.nm_state = None
    assert not look(wifi, clock, t0 + 40)
    assert not look(wifi, clock, t0 + 69.9)
    assert look(wifi, clock, t0 + 70)
    assert fake.ups == [VENUE]
    # The last look is the state only, right before `con up`.
    sudo = next(i for i, c in enumerate(fake.calls) if c[:2] == ["sudo", "-n"])
    assert fake.calls[sudo - 1] == ["nmcli", "-t", "-f", "GENERAL.STATE",
                                    "dev", "show", "wlan0"]


def test_a_failing_last_look_is_a_no():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)

    def last_look_fails(args, timeout):
        if args[-3:] == ["dev", "show", "wlan0"]:
            return 10, "Error: Device 'wlan0' not found."
        return FakeNmcli.__call__(fake, args, timeout)
    wifi._run = last_look_fails
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [] and lines == [] and not wifi.busy


# ---- 3: claims are atomic ----

def test_a_timed_switch_asked_for_during_a_try_is_refused_at_once():
    # schedule() checks and reserves in one step (review of 3f43a10): no
    # deferral is accepted that its timer would later have to skip.
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.release.clear()
    clock.t = t0 + 30
    assert wifi.watch(wifi.poll())
    assert wait_until(lambda: fake.ups == [VENUE])
    try:
        scheduled = wifi.schedule(HOTSPOT, 0.05)
        assert not scheduled and scheduled.busy
        assert scheduled.reason == (f"reconnecting {VENUE} - try again "
                                    "in a moment")
        assert wifi.pending is None
        assert any(f"switch to {HOTSPOT} not scheduled - reconnecting {VENUE}"
                   in line for line in wifi.recent(10))
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
    time.sleep(0.2)
    assert fake.ups == [VENUE]


def test_only_one_of_two_racing_switches_claims_the_radio():
    import threading

    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    fake.release.clear()
    gate = threading.Barrier(2)
    results = []

    def go(call):
        gate.wait()
        results.append(call())
    threads = [threading.Thread(target=go, args=(lambda: wifi.switch_to(HOTSPOT),)),
               threading.Thread(target=go, args=(lambda: wifi.switch(wifi.profiles[1]),))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)
    try:
        assert sorted(results) == [False, True]
        assert wait_until(lambda: len(fake.ups) == 1)
    finally:
        fake.release.set()
        wifi.join(5.0)
    assert len(fake.ups) == 1


def test_select_during_a_try_says_so_and_never_answers_success():
    from tests.test_ui_remote import call, make_session

    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    session, runner, _ = make_session()
    agent = Agent(session, port=0, host="127.0.0.1", wifi=wifi)
    agent.start()
    fake.release.clear()
    try:
        clock.t = t0 + 30
        assert wifi.watch(wifi.poll())
        assert wait_until(lambda: fake.ups == [VENUE])
        for body in ({"profile": HOTSPOT}, {"profile": HOTSPOT, "after_s": 20}):
            code, answer = call(agent, "/wifi/select", body)
            assert code == 409
            assert answer["error"] == (f"reconnecting {VENUE} - try again "
                                       "in a moment")
        assert wifi.pending is None
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
        agent.stop()
        runner.stop()
    assert fake.ups == [VENUE]


def test_the_watchdog_never_claims_during_a_timed_switchs_handoff():
    import threading

    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    asking = threading.Event()
    go_on = threading.Event()

    def allowed():
        # The fire is between its timer and its claim: the PC lock is
        # being asked again.
        asking.set()
        go_on.wait(5.0)
        return None
    wifi.schedule(HOTSPOT, 0.01, allowed=allowed)
    try:
        assert asking.wait(5.0)
        assert wifi.pending is not None            # still reserved
        clock.t = t0 + 60
        assert not wifi.watch(wifi.poll())
        assert not wifi.busy
    finally:
        go_on.set()
    assert wait_until(lambda: wifi.phase == DONE)
    wifi.join(5.0)
    assert fake.ups == [HOTSPOT] and wifi.pending is None


def test_a_timed_switch_cancelled_in_its_handoff_starts_nothing():
    import threading

    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    asking = threading.Event()
    go_on = threading.Event()

    def allowed():
        asking.set()
        go_on.wait(5.0)
        return None
    wifi.schedule(HOTSPOT, 0.01, allowed=allowed)
    assert asking.wait(5.0)
    assert wifi.cancel_pending()
    go_on.set()
    time.sleep(0.2)
    assert fake.ups == [] and wifi.phase != CONNECTING
    assert not any("skipped" in line for line in wifi.recent(10))


# ---- 4: hardening ----

def test_a_notify_that_raises_never_leaves_the_radio_claimed():
    wifi, fake, clock, lines = make_dog()

    def broken(line):
        raise RuntimeError("runner log gone")
    wifi._notify = broken
    t0 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t0 + 30)
    assert fake.ups == [VENUE] and wifi.reconnecting is None
    assert wifi.snapshot()["profile"] == VENUE


def test_a_hung_con_up_ends_the_try_with_the_timeout():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    fake.hang = {VENUE}
    assert look(wifi, clock, t0 + 30)
    assert wifi.reconnecting is None and not wifi.busy
    assert wifi.reconnect_info()["last_error"] == "nmcli timed out after 60 s"
    assert lines[-1] == "wifi: up failed: nmcli timed out after 60 s"


def test_the_reader_survives_an_exception_and_says_it_once():
    fake = venue_fake()
    wifi = Wifi(run=fake, poll_s=0.01, echo_log=False)
    turns = []

    def broken(read_ok=True):
        turns.append(1)
        raise RuntimeError("watchdog bug")
    wifi.watch = broken
    wifi._down_since = 5.0                          # a disconnect timer running
    try:
        wifi.start_reader()
        assert wait_until(lambda: len(turns) >= 5)
        assert wifi._reader.is_alive()
        assert wifi._down_since is None             # a failed turn clears it
        fake.active = HOTSPOT                       # the cache still moves
        assert wait_until(lambda: wifi.snapshot()["profile"] == HOTSPOT)
    finally:
        wifi.close()
    said = [line for line in wifi.recent(50) if "reader error" in line]
    assert len(said) == 1 and said[0].endswith("watchdog bug")


# ---- 5: the LCD's log during one outage ----

def test_one_outage_puts_few_lines_on_the_lcd():
    wifi, fake, clock, lines = make_dog()
    fake.fail = {VENUE}
    t0 = lose_link(wifi, fake, clock)
    for n in range(12):
        assert look(wifi, clock, t0 + 30 + 30 * n)
    assert len(fake.ups) == 12
    failed = ("wifi: up failed: No suitable device found for this connection "
              "(device wlan0 not available because profile is not "
              "compatible with device).")
    assert lines == [f"wifi: reconnect {VENUE}", failed,
                     "wifi: still down, try 5", "wifi: still down, try 10"]
    journal = wifi.recent(100)
    assert sum(line.endswith(f"wifi: reconnect {VENUE}") for line in journal) == 12
    assert sum(line.endswith(failed) for line in journal) == 12
    # nmcli's line changes: said once, and the outage's count goes on.
    wifi._run = lambda args, timeout: (
        (4, NO_SECRETS) if args[:2] == ["sudo", "-n"]
        else FakeNmcli.__call__(fake, args, timeout))
    for n in range(12, 17):
        assert look(wifi, clock, t0 + 30 + 30 * n)
    no_secrets = "wifi: up failed: Secrets were required, but not provided."
    assert lines[4:] == [no_secrets, "wifi: still down, try 15"]
    # Lines that keep changing back and forth are each said only once
    # per outage (review of 3f43a10).
    flip = {"n": 0}

    def alternating(args, timeout):
        if args[:2] == ["sudo", "-n"]:
            flip["n"] += 1
            return (4, NO_SECRETS) if flip["n"] % 2 else (4, REFUSAL)
        return FakeNmcli.__call__(fake, args, timeout)
    wifi._run = alternating
    for n in range(17, 23):
        assert look(wifi, clock, t0 + 30 + 30 * n)
    assert lines[6:] == ["wifi: still down, try 20"]
    # Back: said; the next outage starts its own count.
    wifi._run = fake
    fake.fail = set()
    assert look(wifi, clock, t0 + 30 + 30 * 23)
    assert lines[-1] == f"wifi: on {VENUE}"
    fake.fail = {VENUE}
    t1 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t1 + 30)
    assert lines[-2:] == [f"wifi: reconnect {VENUE}", failed]


def test_networkmanager_reconnecting_by_itself_ends_the_outage():
    wifi, fake, clock, lines = make_dog()
    fake.fail = {VENUE}
    t0 = lose_link(wifi, fake, clock)
    for n in range(3):
        assert look(wifi, clock, t0 + 30 + 30 * n)
    assert len(lines) == 2
    fake.active = VENUE                     # NetworkManager got it back
    assert not look(wifi, clock, t0 + 200)
    t1 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t1 + 30)
    assert lines[2:] == [f"wifi: reconnect {VENUE}", lines[1]]


# ---- 6: the WIFI screen while a try runs ----

def test_the_wifi_screen_says_reconnecting_and_refuses_a_held_key1(monkeypatch):
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    app, _ = make_app(wifi)
    hints = []
    real_hint = render._hint
    monkeypatch.setattr(render, "_hint",
                        lambda draw, text: (hints.append(text),
                                            real_hint(draw, text)))
    app.screen = Screen.WIFI
    app.draw()
    assert hints[-1] == "hold KEY1 = switch  KEY2 back"
    fake.release.clear()
    clock.t = t0 + 30
    assert wifi.watch(wifi.poll())
    try:
        assert wait_until(lambda: fake.ups == [VENUE])
        app.draw()
        assert hints[-1] == f"reconnecting {VENUE} - KEY2 back"
        app.handle("key1_hold")
        assert wifi.phase == LOCKED
        assert wifi.status_text() == f"reconnecting {VENUE} - KEY2 back"
        app.handle("up")                    # nothing else is heard
        assert wifi.choice == 0
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
    # The try is over: the refusal goes with it, the usual hint is back.
    assert wifi.phase == IDLE
    app.draw()
    assert hints[-1] == "hold KEY1 = switch  KEY2 back"
    app.handle("key2")
    assert app.screen is not Screen.WIFI


def test_a_switch_verdict_stays_on_the_screen_during_a_try():
    wifi, fake, clock, lines = make_dog(fake=venue_fake(active=VENUE))
    assert not look(wifi)
    fake.fail = {HOTSPOT}                   # the operator's switch fails...
    assert wifi.switch_to(HOTSPOT)
    wifi.join(5.0)
    assert wifi.phase == FAILED
    verdict = wifi.status_text()
    assert verdict.startswith("ERROR back on")
    app, _ = make_app(wifi)
    app.screen = Screen.WIFI
    fake.active = None                      # ...and later the link drops
    t0 = clock.t
    assert not look(wifi)
    fake.release.clear()
    clock.t = t0 + 30
    assert wifi.watch(wifi.poll())
    try:
        assert wait_until(lambda: fake.ups[-1:] == [VENUE] and len(fake.ups) == 3)
        app.handle("key1_hold")
        assert wifi.phase == FAILED and wifi.status_text() == verdict
        assert wifi.hint() == f"reconnecting {VENUE} - KEY2 back"
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)


# ---- review of 3f43a10 ----

def test_the_watchdog_claiming_inside_selects_admission_is_a_409():
    # 1: /wifi/select's delayed path checked busy, let go, then called
    # schedule(): a watchdog claim in between got a 200 and a timer that
    # later skipped the move. The claim is made right there, inside the
    # handler between its busy check and schedule() (names() is the call
    # in between).
    from tests.test_ui_remote import call, make_session

    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    clock.t = t0 + 30
    fake.release.clear()
    real_names = wifi.names
    claimed = []

    def names_then_claim():
        found = real_names()
        if not claimed:
            claimed.append(wifi.watch(wifi.poll()))
        return found
    wifi.names = names_then_claim
    session, runner, _ = make_session()
    agent = Agent(session, port=0, host="127.0.0.1", wifi=wifi)
    agent.start()
    try:
        code, answer = call(agent, "/wifi/select",
                            {"profile": HOTSPOT, "after_s": 20})
        assert claimed == [True]
        assert code == 409
        assert answer["error"] == (f"reconnecting {VENUE} - try again in "
                                   "a moment")
        assert wifi.pending is None
    finally:
        fake.release.set()
        wifi.join_reconnect(5.0)
        agent.stop()
        runner.stop()
    assert fake.ups == [VENUE]


def test_a_timer_that_will_not_start_is_no_200(monkeypatch):
    from tests.test_ui_remote import call, make_session
    from ui import wifi as wifi_mod

    class DeadTimer:
        daemon = True

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

        def cancel(self):
            pass
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    monkeypatch.setattr(wifi_mod.threading, "Timer", DeadTimer)
    session, runner, _ = make_session()
    agent = Agent(session, port=0, host="127.0.0.1", wifi=wifi)
    agent.start()
    try:
        code, answer = call(agent, "/wifi/select",
                            {"profile": HOTSPOT, "after_s": 20})
        assert code == 500
        assert answer["error"] == ("the switch could not be scheduled on "
                                   "this unit")
        assert wifi.pending is None
    finally:
        agent.stop()
        runner.stop()
    assert fake.ups == []


def test_the_hat_says_so_when_its_switch_does_not_start(monkeypatch):
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    app, _ = make_app(wifi)
    app.screen = Screen.WIFI
    wifi.select(+1)
    monkeypatch.setattr(wifi, "switch", lambda target=None: False)
    app.handle("key1_hold")
    assert wifi.phase == LOCKED
    assert wifi.status_text() == "the switch did not start"


def test_a_timer_whose_worker_will_not_start_is_not_called_busy(monkeypatch):
    from ui import wifi as wifi_mod

    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)

    class NoThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("can't start new thread")
    assert wifi.schedule(HOTSPOT, 0.05)
    monkeypatch.setattr(wifi_mod.threading, "Thread", NoThread)
    assert wait_until(lambda: wifi.pending is None and wifi.phase == FAILED)
    journal = wifi.recent(10)
    assert any(f"switch to {HOTSPOT} skipped - the switch did not start"
               in line for line in journal)
    assert not any("busy" in line or "in flight" in line for line in journal)
    assert wifi.error.startswith("could not start: ")
    assert not wifi.busy and fake.ups == []


def test_an_old_switch_worker_never_touches_a_newer_claim(monkeypatch):
    # 2: A published its verdict, B claimed CONNECTING - A's cleanup
    # must not mark B FAILED.
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    fake.release.clear()
    assert wifi.switch_to(HOTSPOT)                  # B, held in nmcli
    assert wifi.phase == CONNECTING
    b_gen = wifi._switch_gen

    def boom(target, previous):
        raise RuntimeError("A's late exception")
    monkeypatch.setattr(wifi, "_switch_body", boom)
    wifi._switch(wifi.profiles[1], VENUE, b_gen - 1)   # A's worker, late
    assert wifi.phase == CONNECTING                 # B untouched...
    assert wifi.error is None                       # ...its error too
    assert any("A's late exception" in line for line in wifi.recent(5))
    monkeypatch.undo()
    fake.release.set()
    assert wait_until(lambda: wifi.phase == DONE)
    wifi.join(5.0)
    assert fake.ups == [HOTSPOT]


def test_a_failed_worker_start_shows_failed_before_its_log_line(monkeypatch):
    from ui import wifi as wifi_mod

    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    seen = []
    real_emit = wifi.emit

    def emit(message, error=False, echo=True):
        seen.append((wifi.phase, message))
        real_emit(message, error, echo)
    wifi.emit = emit

    class NoThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(wifi_mod.threading, "Thread", NoThread)
    assert not wifi.switch_to(HOTSPOT)
    assert seen[-1][0] == FAILED and seen[-1][1].startswith("could not start")


def test_nothing_runs_between_the_last_state_read_and_con_up():
    # 3: no log line, no lock between them: the try is said after nmcli
    # answers.
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)
    up = {"returned": False, "read": False}

    def runner(args, timeout):
        if args[-3:] == ["dev", "show", "wlan0"]:
            up["read"] = True
        answer = FakeNmcli.__call__(fake, args, timeout)
        if args[:2] == ["sudo", "-n"]:
            up["returned"] = True
        return answer
    wifi._run = runner
    # Spies on both logs - the journal (emit) and the LCD's (notify):
    # every line of the try comes after _up() has returned.
    journal, lcd = [], []
    real_emit = wifi.emit

    def emit(message, error=False, echo=True):
        if up["read"]:
            journal.append((up["returned"], message))
        real_emit(message, error, echo)
    wifi.emit = emit
    wifi._notify = lambda line: lcd.append((up["returned"], line))
    assert look(wifi, clock, t0 + 30)
    sudo = next(i for i, c in enumerate(fake.calls) if c[:2] == ["sudo", "-n"])
    assert fake.calls[sudo - 1][-3:] == ["dev", "show", "wlan0"]
    assert journal and lcd
    assert all(returned for returned, _ in journal + lcd)
    assert journal[0][1] == f"wifi: reconnect {VENUE}"
    assert [line for _, line in lcd] == [f"wifi: reconnect {VENUE}",
                                         f"wifi: on {VENUE}"]


def test_a_partial_read_still_records_a_move_onto_the_ap():
    # 4: radxa-05 on the router, moved onto its own AP while one
    # profile read was failing: the move counts (mode unknown until a
    # full read names it a hotspot), and when the AP then drops it is
    # never pulled back to the router.
    fake = radxa05_fake(active=VENUE)
    wifi, fake, clock, lines = make_dog(fake=fake)
    assert not look(wifi)                           # full: on the router
    flaky = flaky_reads(wifi, fake, ROUTER)
    flaky["on"] = True
    fake.active = HOTSPOT                           # up as the hotspot
    assert not look(wifi)                           # partial read
    assert wifi._last_active == (HOTSPOT, "unknown")
    flaky["on"] = False
    fake.active = None                              # the AP drops
    for dt in (1.0, 31.0, 61.0, 600.0):
        assert not look(wifi, clock, clock.t + dt)
    assert fake.ups == [] and lines == []


def test_a_malformed_inventory_row_makes_the_read_incomplete():
    wifi, fake, clock, lines = make_dog()
    t0 = lose_link(wifi, fake, clock)

    def garbled(args, timeout):
        code, output = FakeNmcli.__call__(fake, args, timeout)
        if args[-2:] == ["con", "show"]:
            output += "\nhalf a row:802-11-wireless"
        return code, output
    wifi._run = garbled
    for dt in (30.0, 60.0, 600.0):
        assert not look(wifi, clock, t0 + dt)
    assert wifi.device_state is None and fake.ups == []


def test_any_activated_read_ends_the_outage_even_a_partial_one():
    wifi, fake, clock, lines = make_dog()
    fake.fail = {VENUE}
    t0 = lose_link(wifi, fake, clock)
    for n in range(3):
        assert look(wifi, clock, t0 + 30 + 30 * n)
    assert len(lines) == 2
    flaky = flaky_reads(wifi, fake, ROUTER)
    flaky["on"] = True
    fake.active = VENUE                             # back, read partially
    assert not look(wifi, clock, t0 + 200)
    flaky["on"] = False
    t1 = lose_link(wifi, fake, clock)
    assert look(wifi, clock, t1 + 30)
    assert lines[2:] == [f"wifi: reconnect {VENUE}", lines[1]]


# ---- review of 8c01839 ----

def test_two_concurrent_schedules_keep_their_own_reasons(monkeypatch):
    # 1: each call's refusal reason is its own answer, never a shared
    # field the other call may have rewritten.
    import threading
    from ui import wifi as wifi_mod

    real_timer = threading.Timer

    class FlakyTimer(real_timer):
        def start(self):
            if threading.current_thread().name == "B":
                raise RuntimeError("no thread for B")
            super().start()
    monkeypatch.setattr(wifi_mod.threading, "Timer", FlakyTimer)
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    for _ in range(20):
        gate = threading.Barrier(2)
        results = {}

        def go(name):
            gate.wait()
            results[name] = wifi.schedule(HOTSPOT, 60.0)
        threads = [threading.Thread(target=go, args=(n,), name=n)
                   for n in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        assert results["A"].ok and results["A"].reason is None
        assert not results["B"].ok and not results["B"].busy
        assert results["B"].reason == "could not schedule: no thread for B"
        wifi.cancel_pending()
    assert fake.ups == []


def test_inventory_rows_need_exactly_four_fields_and_a_name():
    for bad in ("Extra:802-11-wireless:no:yes:surplus",
                ":802-11-wireless:no:yes"):
        wifi, fake, clock, lines = make_dog()
        t0 = lose_link(wifi, fake, clock)

        def garbled(args, timeout, bad=bad, fake=fake):
            code, output = FakeNmcli.__call__(fake, args, timeout)
            if args[-2:] == ["con", "show"]:
                output += "\n" + bad
            return code, output
        wifi._run = garbled
        for dt in (30.0, 60.0):
            assert not look(wifi, clock, t0 + dt), bad
        assert wifi.device_state is None and fake.ups == []


def test_the_locale_is_probed_once_and_only_a_clean_exit_counts(monkeypatch):
    import subprocess as sp
    import threading
    from ui import wifi as wifi_mod

    asked = []

    def slow_locale(args, **kwargs):
        if args == ["locale", "-a"]:
            asked.append(1)
            time.sleep(0.1)                 # first callers pile up here
            return SimpleNamespace(returncode=0, stdout="C\nC.utf8\n",
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(sp, "run", slow_locale)
    monkeypatch.setattr(wifi_mod, "_nmcli_locale", None)
    got = []
    threads = [threading.Thread(target=lambda: got.append(
        wifi_mod.nmcli_locale())) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)
    assert asked == [1] and got == ["C.UTF-8"] * 5

    def failing_locale(args, **kwargs):
        # A non-zero exit that still printed something: not believed.
        return SimpleNamespace(returncode=1, stdout="C.utf8\n",
                               stderr="locale: error")
    monkeypatch.setattr(sp, "run", failing_locale)
    monkeypatch.setattr(wifi_mod, "_nmcli_locale", None)
    assert wifi_mod.nmcli_locale() == "C"


def test_a_failing_announcement_never_strands_the_timer(monkeypatch):
    # 5: said after timer.start(), best-effort.
    wifi, fake, clock, lines = make_dog()
    assert not look(wifi)
    real_emit = wifi.emit

    def emit(message, error=False, echo=True):
        if " in " in message and "switch to" in message:
            raise RuntimeError("journal gone")
        real_emit(message, error, echo)
    wifi.emit = emit
    scheduled = wifi.schedule(HOTSPOT, 0.05)
    assert scheduled.ok
    assert wait_until(lambda: wifi.phase == DONE)
    wifi.join(5.0)
    assert wifi.pending is None and fake.ups == [HOTSPOT]


# ---- ui/main.py ----

class _Built(Exception):
    pass


def _run_main(monkeypatch, *argv):
    """ui.main.main() up to the Wifi it builds - everything before it
    stubbed, so no port, board, git or nmcli is touched."""
    import ui.main as main_mod

    seen = {}

    def stub(*args, **kwargs):
        return SimpleNamespace(forget_board=None, note_usb_board=None,
                               emit=lambda line: None,
                               before=SimpleNamespace(commit="x"))

    def fake_wifi(*args, **kwargs):
        seen.update(kwargs)
        raise _Built()

    for name in ("DemoRunner", "FirmwareUpdater", "RepoPuller", "Rebooter",
                 "BoardVersions", "BoardClearer", "BoardInfo"):
        monkeypatch.setattr(main_mod, name, stub)
    monkeypatch.setattr(main_mod, "find_port", lambda: "/dev/fake")
    monkeypatch.setattr(main_mod, "find_firmware", lambda: None)
    monkeypatch.setattr(main_mod, "find_firmware_images", lambda: [])
    monkeypatch.setattr(main_mod, "Wifi", fake_wifi)
    monkeypatch.setattr(sys, "argv", ["ui.main", *argv])
    with pytest.raises(_Built):
        main_mod.main()
    return seen


def test_main_turns_the_watchdog_on_by_default_and_off_with_the_flag(
        monkeypatch):
    on = _run_main(monkeypatch)
    assert on["watchdog"] is True and callable(on["notify"])
    off = _run_main(monkeypatch, "--no-wifi-watchdog")
    assert off["watchdog"] is False


def test_the_class_default_is_on_with_the_constants():
    wifi = Wifi(run=lambda args, t: (127, ""), echo_log=False)
    assert wifi.watchdog is True
    assert wifi.reconnect_after == RECONNECT_AFTER_S == 30.0
    assert wifi.reconnect_retry == RECONNECT_RETRY_S == 30.0
