"""Hardware pin map and UI constants for the Waveshare 1.3inch LCD HAT.

Pin numbers are BCM, taken from the Waveshare 1.3inch LCD HAT wiki. The
HAT drives an ST7789 over SPI0 and exposes a 5-way joystick plus three
push buttons, all active-low with pull-ups.
"""

# --- ST7789 display (SPI0, CE0) ---
SPI_BUS = 0
SPI_DEVICE = 0
SPI_HZ = 40_000_000
PIN_DC = 25
PIN_RST = 27
PIN_BL = 24

WIDTH = 240
HEIGHT = 240

# --- inputs (active low, internal pull-up) ---
PIN_KEY1 = 21
PIN_KEY2 = 20
PIN_KEY3 = 16
PIN_UP = 6
PIN_DOWN = 19
PIN_LEFT = 5
PIN_RIGHT = 26
PIN_PRESS = 13

BUTTON_PINS = {
    "up": PIN_UP,
    "down": PIN_DOWN,
    "left": PIN_LEFT,
    "right": PIN_RIGHT,
    "press": PIN_PRESS,
    "key1": PIN_KEY1,
    "key2": PIN_KEY2,
    "key3": PIN_KEY3,
}

# Holding KEY1 this long resets the demo instead of pausing it. The hold
# is reported as its own event and suppresses the short press, so one
# press never does both. KEY3 reports a hold the same way (EXHIBITION's
# LOOP toggle, 2026-09-30); its short press - blank the screen - is
# therefore reported on release rather than on the press.
KEY1_HOLD_S = 1.0
HOLD_EVENTS = {"key1": KEY1_HOLD_S, "key3": KEY1_HOLD_S}
# After ANY wake of a dark screen - a press, an alert, a result or an
# error lighting it - every SHORT press within this long only counts as
# the wake (ui/app.py, App._wake_guard). A little longer than a hold,
# because people double-tap "did it wake?" within about a second
# (2026-11-20 review: KEY2 0.3 s after a press-wake stopped a running
# show, once every garment unit went dark after 30 s).
WAKE_GUARD_S = 1.5
# A HOLD (key1_hold / key3_hold) is only the wake if it went down before
# the screen was lit, or within this reaction allowance after: a full
# 1 s hold begun on a visibly lit screen is deliberate - radxa-05's
# EXHIBITION STOP is "wake, then hold KEY1 straight away" (re-review,
# 2026-11-20: 1.5 s for holds too made that STOP take 3.5-4 s).
WAKE_HOLD_GRACE_S = 0.3
# A fault seen again (the same text, digits aside) lights a dark screen
# at most once per this long - a flapping "no serial port" must not
# undo a deliberate KEY3 blank during a show over and over.
ATTENTION_REWAKE_S = 60.0
EVENTS = tuple(BUTTON_PINS) + ("key1_hold", "key3_hold")

# --- UI behaviour ---
# Input poll interval. Redraws are separate and only happen when the
# screen content actually changes (see App._display_key), so this can be
# short enough to feel responsive without wasting frames.
FRAME_INTERVAL_S = 0.1
LOG_LINES = 6            # log rows visible on the running screen
LOG_HISTORY = 200        # lines kept in memory

# Blank the backlight after this long without a button press, on every
# unit (2026-11-20, the user: 30 s everywhere, without touching each
# unit's /etc/default/epaper-ui; it used to be 0 = never on a garment
# unit). `--blank-after SEC` overrides it, `--blank-after 0` disables it.
# Worth roughly 20-40 mA (docs/POWER.md). The demo keeps running either
# way, any press only wakes the screen, and the screen is never blanked
# while a flash / clear / pull / scan runs or before a result or an
# error has been seen (ui/app.py, _keep_lit).
BLANK_AFTER_S = 30.0
# The unit the exhibition Conductor runs on (radxa-05: a Conductor answers
# on 127.0.0.1, Exhibition.present()) uses this one - the same 30 s since
# 2026-11-20, kept apart because an alert there (speaker LOST, a unit
# newly missing, a verdict) wakes the screen without a press and keeps it
# lit for another BLANK_AFTER_CONDUCTOR_S (ui/app.py, _watch_alerts).
BLANK_AFTER_CONDUCTOR_S = 30.0

# Show lock. Locked, the buttons do nothing, so a knock during a show
# cannot stop the demo. This sequence within UNLOCK_WINDOW_S unlocks;
# the lock comes back on its own after RELOCK_AFTER_S of no input, so an
# operator never has to remember to re-arm it.
UNLOCK_SEQUENCE = ("key2", "key3", "key2")
UNLOCK_WINDOW_S = 5.0
RELOCK_AFTER_S = 60.0

# How often to pet systemd's watchdog (WatchdogSec must be well above).
WATCHDOG_PERIOD_S = 5.0

# /status's "wifi" (ui/wifi.py fills it, ui/agent.py serves it): the
# keys, so the agent can answer all-null without importing the worker.
WIFI_FIELDS = ("ssid", "ip", "signal", "mode", "profile")
# POST /wifi/select's longest deferral (seconds) - the same reason: the
# agent validates the body without importing ui/wifi.py.
WIFI_MAX_AFTER_S = 120.0
