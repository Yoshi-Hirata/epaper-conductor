"""Backlight auto-blank and the show lock.

Both exist so the device can be left alone: the screen should not burn
power all night, and a knock during a show must not stop the demo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.app import App, Screen
from ui.config import UNLOCK_SEQUENCE
from ui.display import NullDisplay
from ui.inputs import ScriptedInput
from ui.patterns import PATTERNS


class FakeRunner:
    def __init__(self):
        self.pattern = None
        self.cycle = 0
        self.elapsed = 0.0
        self.error = None
        self.running = False
        self.starts = []
        self.stops = 0
        self.standby_ready = False
        self.live = [1, 20]
        self.boards = [1, 20]

    def start(self, pattern):
        self.pattern = pattern
        self.running = True
        self.starts.append(pattern.key)

    def standby(self):
        self.standby_ready = True

    def stop(self, timeout=5.0):
        self.running = False
        self.stops += 1

    def recent(self, count):
        return ["09:00:00 start"]


class Clock:
    """Hand-cranked clock so idle timeouts are exact, not slept through."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_app(events=(), **kwargs):
    clock = Clock()
    runner = FakeRunner()
    app = App(NullDisplay(), ScriptedInput(events), runner,
              port_label="/dev/ttyACM0", clock=clock, **kwargs)
    return app, runner, clock


# ---- auto-blank ----

def test_screen_blanks_after_the_idle_timeout():
    app, _, clock = make_app(blank_after=10.0)
    app.tick(wait=0)
    assert not app.blanked
    clock.advance(9.9)
    app.tick(wait=0)
    assert not app.blanked          # not yet
    clock.advance(0.2)
    app.tick(wait=0)
    assert app.blanked
    assert app.display.asleep


def test_input_postpones_blanking():
    app, _, clock = make_app(blank_after=10.0)
    clock.advance(9.0)
    app.handle("down")              # a press resets the idle timer
    clock.advance(9.0)
    app.tick(wait=0)
    assert not app.blanked


def test_waking_press_changes_nothing_else():
    # The whole point: in a dark room you press something to see the
    # screen, and that must not start, stop or re-select anything.
    app, runner, clock = make_app(blank_after=10.0)
    app.handle("down")
    app.handle("key1")              # demo running, menu -> running
    before = (app.screen, app.selected, list(runner.starts), runner.stops)
    clock.advance(11)
    app.tick(wait=0)
    assert app.blanked

    app.handle("key1")              # blind press: wakes only
    assert not app.blanked
    assert not app.display.asleep
    assert (app.screen, app.selected, list(runner.starts), runner.stops) == before


def test_every_button_wakes_the_screen():
    for event in ("up", "down", "left", "right", "press", "key1", "key2", "key3"):
        app, _, clock = make_app(blank_after=10.0)
        clock.advance(11)
        app.tick(wait=0)
        assert app.blanked
        app.handle(event)
        assert not app.blanked, f"{event} did not wake the screen"


def test_blanking_can_be_disabled():
    app, _, clock = make_app(blank_after=0)
    clock.advance(3600)
    app.tick(wait=0)
    assert not app.blanked


def test_blanking_is_30_s_by_default_and_0_disables_it():
    # 2026-11-20, the user: every unit blanks after 30 s idle, without a
    # change to each unit's /etc/default/epaper-ui (it was off on garment
    # units). --blank-after 0 still disables it, a given value still wins.
    from ui.config import BLANK_AFTER_S

    assert BLANK_AFTER_S == 30.0
    app, _, clock = make_app()          # no blank_after given
    assert app.blank_timeout() == 30.0
    app.tick(wait=0)
    clock.advance(29.9)
    app.tick(wait=0)
    assert not app.blanked
    clock.advance(0.2)
    app.tick(wait=0)
    assert app.blanked
    app, _, clock = make_app(blank_after=0)
    clock.advance(3600)
    app.tick(wait=0)
    assert not app.blanked
    # KEY3 still blanks on demand.
    app.handle("key3")
    assert app.blanked


def test_demo_keeps_running_while_blanked():
    app, runner, clock = make_app(blank_after=10.0)
    app.handle("down")
    app.handle("key1")
    clock.advance(11)
    app.tick(wait=0)
    assert app.blanked
    assert runner.running           # only the backlight sleeps


# ---- show lock ----

def test_locked_buttons_do_nothing():
    app, runner, _ = make_app(locked=True)
    for event in ("down", "key1", "press", "key2"):
        app.handle(event)
    assert app.selected == 0
    assert runner.starts == []
    assert app.screen is Screen.MENU


def test_unlock_sequence_frees_the_buttons():
    app, runner, _ = make_app(locked=True)
    for event in UNLOCK_SEQUENCE:
        app.handle(event)
    assert not app.locked
    app.handle("down")
    app.handle("key1")
    assert runner.starts == [PATTERNS[1].key]


def test_wrong_sequence_does_not_unlock():
    app, _, _ = make_app(locked=True)
    for event in ("key2", "key1", "key2"):
        app.handle(event)
    assert app.locked


def test_unlock_sequence_expires():
    app, _, clock = make_app(locked=True)
    app.handle(UNLOCK_SEQUENCE[0])
    clock.advance(10)               # too slow
    for event in UNLOCK_SEQUENCE[1:]:
        app.handle(event)
    assert app.locked


def test_lock_returns_on_its_own():
    # An operator who unlocks mid-show must not have to remember to re-arm it.
    app, _, clock = make_app(locked=True, relock_after=60.0, blank_after=0)
    for event in UNLOCK_SEQUENCE:
        app.handle(event)
    assert not app.locked
    clock.advance(61)
    app.tick(wait=0)
    assert app.locked


def test_a_device_that_started_unlocked_never_locks_itself():
    app, _, clock = make_app(locked=False, relock_after=60.0, blank_after=0)
    clock.advance(600)
    app.tick(wait=0)
    assert not app.locked


def test_locked_screen_still_wakes():
    # Seeing the status must not require unlocking first.
    app, _, clock = make_app(locked=True, blank_after=10.0)
    clock.advance(11)
    app.tick(wait=0)
    assert app.blanked
    app.handle("key1")
    assert not app.blanked
    assert app.locked               # ...but it stays locked


# ---- what never goes dark (2026-11-20, 30 s blanking on every unit) ----

from ui.updater import MenuEntry  # noqa: E402


class FakeWorker:
    """Enough of an updater / clearer / puller / scanner for _keep_lit:
    a menu row, `busy`, `finished` and `phase`."""

    def __init__(self, key):
        self.menu_entry = MenuEntry(key, key.upper(), "")
        self.phase = "idle"

    @property
    def busy(self):
        return self.phase == "busy"

    @property
    def finished(self):
        return self.phase in ("done", "failed")

    def disarm(self):           # CLEAR BOARD: blanking drops an armed send
        pass

    def __getattr__(self, name):    # any other call a press makes: a no-op
        return lambda *args, **kwargs: None


def idle(app, clock, seconds):
    clock.advance(seconds)
    app._idle_tasks()           # the blanking decision, without a repaint


def test_a_running_worker_keeps_the_screen_lit_and_the_timer_restarts_after():
    for name in ("updater", "clearer", "puller", "versions", "boardinfo",
                 "rebooter", "wifi"):
        worker = FakeWorker(name)
        app, _, clock = make_app(**{name: worker})
        worker.phase = "busy"
        idle(app, clock, 600)               # a ten-minute flash / scan
        assert not app.blanked, name
        worker.phase = "idle"               # ended with nothing to read
        idle(app, clock, 29)
        assert not app.blanked, name        # 30 s from the end, not the press
        idle(app, clock, 2)
        assert app.blanked, name


def test_a_finished_flash_clear_or_pull_stays_lit_until_a_press():
    from ui.app import Screen
    for name, screen in (("updater", Screen.UPDATE), ("clearer", Screen.CLEAR),
                         ("puller", Screen.PULL)):
        for result in ("done", "failed"):
            worker = FakeWorker(name)
            app, _, clock = make_app(**{name: worker})
            app.screen = screen
            worker.phase = "busy"
            idle(app, clock, 120)
            worker.phase = result
            idle(app, clock, 3600)
            assert not app.blanked, (name, result)
            app.handle("down")              # seen: the press acknowledges it
            idle(app, clock, 31)
            assert app.blanked, (name, result)


def test_a_result_off_its_screen_does_not_hold_the_screen():
    worker = FakeWorker("updater")
    app, _, clock = make_app(updater=worker)
    worker.phase = "done"                   # left the UPDATE screen (KEY2)
    idle(app, clock, 31)
    assert app.blanked


def test_a_new_runner_error_lights_a_dark_screen_until_a_press():
    app, runner, clock = make_app()
    idle(app, clock, 31)
    assert app.blanked
    runner.error = "no serial port"
    idle(app, clock, 0.1)
    assert not app.blanked and not app.display.asleep
    idle(app, clock, 3600)
    assert not app.blanked                  # not seen yet
    clock.advance(5.0)                      # past a hold's length
    app.handle("down")                      # seen
    idle(app, clock, 31)
    assert app.blanked                      # the same error does not re-light
    runner.error = None
    idle(app, clock, 1)
    runner.error = "no boards answering"    # a new one does
    idle(app, clock, 0.1)
    assert not app.blanked


def test_a_blind_press_right_after_an_error_wake_only_wakes():
    app, runner, clock = make_app()
    idle(app, clock, 31)
    runner.error = "no serial port"
    idle(app, clock, 0.1)
    before = (app.screen, app.selected, list(runner.starts), runner.stops)
    app.handle("key1")                      # made in the dark
    assert (app.screen, app.selected, list(runner.starts), runner.stops) == before


def test_key3_still_blanks_while_something_keeps_the_screen_lit():
    worker = FakeWorker("updater")
    app, _, clock = make_app(updater=worker)
    worker.phase = "busy"
    app.handle("key3")
    assert app.blanked
    idle(app, clock, 60)
    assert app.blanked                      # stays dark until a press


def test_blanking_never_touches_what_runs():
    app, runner, clock = make_app()
    app.handle("down")
    app.handle("key1")
    before = (app.screen, list(runner.starts), runner.stops, runner.running)
    idle(app, clock, 31)
    assert app.blanked
    assert (app.screen, list(runner.starts), runner.stops, runner.running) == before


# ---- the wake window (2026-11-20 review): a second press shortly after
# ANY wake must not act - every garment unit is now dark after 30 s ----

SECOND_PRESSES = ("key1", "key2", "key1_hold", "key3_hold", "down", "up",
                  "press", "left", "right")


def dark_double_press(app, clock, wake, second, error_runner=None):
    """Blank, wake (a press, or an error lighting the screen), then
    `second` 0.3 s and again 0.9 s after the wake."""
    clock.advance(5.0)                  # clear of any earlier window
    app.handle("key3")
    assert app.blanked
    if wake == "press":
        app.handle("down")
    else:
        error_runner.error = f"no serial port {clock.now}"
        app._idle_tasks()
    assert not app.blanked
    clock.advance(0.3)
    app.handle(second)
    clock.advance(0.6)
    app.handle(second)


def test_two_presses_after_a_wake_do_nothing_on_running():
    from ui.app import Screen
    for wake in ("press", "attention"):
        for second in SECOND_PRESSES:
            app, runner, clock = make_app()
            app.handle("down")
            app.handle("key1")                  # a pattern running
            assert app.screen is Screen.RUNNING
            before = (app.screen, app.selected, list(runner.starts), runner.stops)
            dark_double_press(app, clock, wake, second, runner)
            after = (app.screen, app.selected, list(runner.starts), runner.stops)
            assert after == before, (wake, second)
            assert not app.blanked, (wake, second)   # KEY3 in it did not blank


def test_two_presses_after_a_wake_do_nothing_on_the_menu():
    from ui.app import Screen
    for wake in ("press", "attention"):
        for second in SECOND_PRESSES:
            app, runner, clock = make_app()
            before = (app.screen, app.selected, list(runner.starts), runner.stops)
            dark_double_press(app, clock, wake, second, runner)
            after = (app.screen, app.selected, list(runner.starts), runner.stops)
            assert after == before and app.screen is Screen.MENU, (wake, second)


def test_a_press_after_the_window_acts():
    from ui.config import WAKE_GUARD_S
    app, runner, clock = make_app()
    clock.advance(5.0)
    app.handle("key3")
    app.handle("down")                          # wake
    clock.advance(WAKE_GUARD_S + 0.01)
    app.handle("down")                          # acts
    assert app.selected == 1
    app.handle("down")                          # and so does the next
    assert app.selected == 2
    app.handle("key3")                          # KEY3 blanks as ever
    assert app.blanked


def test_a_hold_counts_by_when_it_went_down():
    # Re-review 2026-11-20: a hold is reported KEY1_HOLD_S after its
    # press. Down before the wake, or within WAKE_HOLD_GRACE_S (0.3 s)
    # after it: only the wake. Down later, on a lit screen: deliberate.
    for wake in ("press", "attention"):
        app, runner, clock = make_app()
        app.handle("down")
        app.handle("key1")                      # RUNNING
        clock.advance(5.0)
        app.handle("key3")
        if wake == "press":
            app.handle("down")                  # wake at t
        else:
            runner.error = "no serial port"
            app._idle_tasks()
        starts = len(runner.starts)
        clock.advance(0.8)                      # down at t-0.2
        app.handle("key1_hold")
        clock.advance(0.4)                      # down at t+0.2
        app.handle("key1_hold")
        assert len(runner.starts) == starts, wake
        clock.advance(0.3)                      # down at t+0.5: RESET acts
        app.handle("key1_hold")
        assert len(runner.starts) == starts + 1, wake
        app.handle("key2")                      # the window closed with it
        assert app.screen.value == "menu", wake


def test_short_presses_stay_guarded_for_the_whole_window():
    from ui.app import Screen
    for wake in ("press", "attention"):
        app, runner, clock = make_app()
        app.handle("down")
        app.handle("key1")                      # RUNNING
        clock.advance(5.0)
        app.handle("key3")
        if wake == "press":
            app.handle("down")
        else:
            runner.error = "no serial port"
            app._idle_tasks()
        t0 = clock.now
        clock.now = t0 + 0.3
        app.handle("key2")
        clock.now = t0 + 1.4
        app.handle("key2")
        assert app.screen is Screen.RUNNING and runner.stops == 0, wake
        clock.now = t0 + 1.6
        app.handle("key2")                      # acts
        assert app.screen is Screen.MENU and runner.stops == 1, wake


def test_a_hold_begun_in_the_dark_is_only_the_wake():
    app, runner, clock = make_app()
    app.handle("down")
    app.handle("key1")
    clock.advance(31)
    app._idle_tasks()
    assert app.blanked
    starts = list(runner.starts)
    app.handle("key1_hold")                     # held down while dark
    assert not app.blanked and runner.starts == starts and runner.stops == 0


def test_an_error_wake_under_a_held_key_does_not_turn_it_into_a_hold():
    # KEY1 goes down in the dark at t; an error lights the screen at
    # t+0.5; the hold reports at t+1.0 - its press was before the wake.
    app, runner, clock = make_app()
    app.handle("down")
    app.handle("key1")
    clock.advance(31)
    app._idle_tasks()
    clock.advance(0.5)
    runner.error = "no serial port"
    app._idle_tasks()
    assert not app.blanked
    clock.advance(0.5)
    app.handle("key1_hold")
    assert runner.stops == 0


def test_locked_presses_in_the_window_are_only_the_wake():
    from ui.config import UNLOCK_SEQUENCE, WAKE_GUARD_S
    app, runner, clock = make_app(locked=True)
    clock.advance(31)
    app._idle_tasks()
    assert app.blanked
    app.handle("key1")                          # wakes; still locked
    assert app.locked and not app.blanked
    clock.advance(0.3)
    for event in UNLOCK_SEQUENCE:               # inside the window: ignored
        app.handle(event)
    assert app.locked
    clock.advance(WAKE_GUARD_S)
    for event in UNLOCK_SEQUENCE:               # after it: unlocks
        app.handle(event)
    assert not app.locked


# ---- attention re-lighting (2026-11-20 review) ----

def test_a_flapping_fault_relights_a_dark_screen_once_a_minute():
    from ui.config import ATTENTION_REWAKE_S
    app, runner, clock = make_app()
    for label in ("q05", "q06", "q07"):         # same family, new label
        clock.advance(5.0)
        if not app.blanked:
            app.handle("key3")                  # a deliberate blank
        runner.error = None
        app._idle_tasks()
        runner.error = f"{label}: gave up after 3"
        app._idle_tasks()
        if label == "q05":
            assert not app.blanked              # first time: lit
        else:
            assert app.blanked                  # not again within a minute
    clock.advance(ATTENTION_REWAKE_S)
    assert app.blanked                          # still dark from q07
    runner.error = None
    app._idle_tasks()
    runner.error = "q08: gave up after 4"
    app._idle_tasks()
    assert not app.blanked                      # a minute on: lit again
    # A different fault is news at once.
    clock.advance(5.0)
    app.handle("key3")
    assert app.blanked
    runner.error = "no serial port"
    app._idle_tasks()
    assert not app.blanked


def test_a_seen_result_is_not_raised_again_on_reentry():
    from ui.app import Screen
    worker = FakeWorker("updater")
    app, _, clock = make_app(updater=worker)
    app.screen = Screen.UPDATE
    worker.phase = "busy"
    idle(app, clock, 10)
    worker.phase = "done"
    idle(app, clock, 1)
    clock.advance(5.0)
    app.handle("down")                          # seen
    idle(app, clock, 1)
    app.screen = Screen.MENU                    # left ...
    idle(app, clock, 1)
    app.screen = Screen.UPDATE                  # ... and back, same result
    idle(app, clock, 31)
    assert app.blanked
    worker.phase = "busy"                       # a new flash
    idle(app, clock, 10)
    worker.phase = "failed"
    idle(app, clock, 3600)
    assert not app.blanked                      # its result is news
