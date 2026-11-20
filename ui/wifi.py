"""WIFI: which Wi-Fi network this unit is on, and switching it (2026-09-30).

At the venue the units sit on the router's SSID (yoshihirock.net_5G). An
exhibition without the router or a PC runs off radxa-05 instead: it
becomes a hotspot, SSID AZ-Epaper - on radxa-05 the NetworkManager
profile of that name IS the hotspot, on every other unit it is an
ordinary client profile with a static address. Moving a unit between
the two used to mean ssh, which from the wrong network is exactly what
one cannot do. The WIFI menu row does it from the LCD HAT.

The screen shows what the unit is on now (SSID, IPv4 address, signal,
client or hotspot) and lists the unit's wireless NetworkManager
profiles, the active one marked. UP/DOWN pick one; KEY1 *held* (the
REBOOT gesture - a plain press does nothing here, a knock must never
move a unit off its network) runs `sudo -n nmcli --wait 45 con up
<name>` on a thread of its own. Success shows the new SSID and address;
a refusal shows nmcli's first line and brings the previous profile back
up, so a choice nmcli itself rejects (no such network in reach, a
profile that does not fit the device) leaves the unit where it was. A
choice nmcli accepts is kept, reachable or not: a unit moved onto a
hotspot that is then switched off is only reached from its own LCD (or
by a reboot) - so at an exhibition radxa-05 is switched first, the
others after it. Profiles are never created, modified or deleted here,
autoconnect is never touched, nothing is written under /etc: the row
only chooses among what the setup left on the unit (radxa/README.md),
and the choice lasts until the next reboot - then autoconnect decides
again, which on every unit means the router's profile (AZ-Epaper is
autoconnect=no; radxa-05's hotspot in particular has to be raised again
by hand after a power cycle).

Everything shown comes from a cache. A daemon thread asks nmcli every
POLL_S - cheap D-Bus questions, no USB, and no scan (`dev wifi list
--rescan no`), so a unit mid-show never hops channels for this - and
/status's "wifi" is that same cache, never a read on the HTTP thread
or the LCD loop. Every nmcli call goes through one injectable runner,
nmcli_command (REBOOT's run_command with nmcli's words kept in
English), so the tests run against a fake and never touch the network
of the machine they run on.

The switch can also be asked for over the agent (ui/agent.py's POST
/wifi/select), with a delay: the Conductor that sends the command is
about to leave the network itself to become the hotspot, so it says
"AZ-Epaper, in 30 s" and goes. schedule() keeps that on a timer thread;
while it is pending the screen counts it down and refuses UP/DOWN and a
held KEY1 ("switch pending"), KEY2 leaves it alone, and only a cancel
over the same endpoint takes it back. When the timer fires the lock is
asked AGAIN (the PC may have taken the unit meanwhile): a refused
switch is logged and skipped, never run.

The reconnect watchdog (2026-11-20, radxa-08 at the venue): a garment
walked to the edge of R2.net lost the link, came back, and the WPA
handshake timed out on the weak signal. NetworkManager takes that for
a wrong password, asks a secret agent for new secrets, finds none on a
headless unit and fails with `no-secrets` - after which it does not
autoconnect that profile again until someone runs `con up` (the day
before, the same unit sat 194 s untouched until the operator opened
this row). So the reader also watches the wifi device: NetworkManager
state `disconnected` (not connecting, not activated - and not
unavailable/unmanaged, which is the radio off or gone) for
RECONNECT_AFTER_S on end runs `con up` for ONE profile, on a worker of
its own, at most every RECONNECT_RETRY_S after the last try ended:
the client profile last ACTIVATED this boot (an operator's choice - a
unit moved onto AZ-Epaper - stands), else, only on a unit with no
hotspot profile, the autoconnect client profile of highest priority.
Never a hotspot: a unit whose last network was its own hotspot
(radxa-05) is left alone, and radxa-05's never-connected-yet boot is
radxa/exhibition-net.sh's. It waits while a switch of the row or of
/wifi/select runs or counts down, and the row waits while it runs.
It does not ask the PC show's lock: that keeps a unit ON the network
the PC reaches it on, and this only brings a unit with no network at
all back to the one it was on. Still `con up` only - autoconnect and
the profiles stay untouched. Off with ui/main.py's --no-wifi-watchdog.

After the review of 9858e7a (2026-11-20): only a FULL read decides -
every profile's mode read (one that is not is UNKNOWN, never taken for
a client), all profiles, not just the list's eight, and the device's
state given; anything less restarts the 30 s. The worker looks at the
device once more right before `con up` and drops the try, unsaid and
uncounted, if NetworkManager has started its own. A deferral keeps its
reservation until switch() claims the radio with it. The LCD hears an
outage's first try and first failure, one "still down" per five failed
tries, and the "on"; the journal hears every try.

After the review of 3f43a10: schedule() refuses while the radio is
claimed, in the same step as it reserves (/wifi/select answers 409, or
500 when the timer will not start - never a 200 for nothing); a switch
worker publishes its verdict only while the claim is still its own;
nothing runs between the last state read and `con up` (the try is said
after); an activation seen on a partial read still counts, its mode
unknown until a full read names it; nmcli's output is decoded as UTF-8
and it runs in C where C.UTF-8 is missing; each different failure line
reaches the LCD once per outage.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

from .config import LOG_HISTORY, WIFI_FIELDS
from .updater import MenuEntry

IDLE = "idle"                # the list: hold KEY1 switches to the chosen row
CONNECTING = "connecting"    # nmcli con up in flight
DONE = "done"                # switched (or already there)
FAILED = "failed"            # refused; the previous profile brought back
LOCKED = "locked"            # the PC drives the unit: nothing was done

WIRELESS = "802-11-wireless"
LOCKED_NOTE = "PC show running - WIFI locked"
PENDING_NOTE = "switch pending"      # the list is refused while one counts down
MAX_AFTER_S = 120.0                  # /wifi/select's longest deferral

LIST_COMMAND = ["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE,AUTOCONNECT",
                "con", "show"]
# --rescan no: the cached scan results are enough for the signal figure,
# and a scan would take the radio off-channel while the PC drives the unit.
SCAN_COMMAND = ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi",
                "list", "--rescan", "no"]
# Every device, not `dev show wlan0`: the block whose TYPE is wifi is the
# one, whatever the kernel named it. GENERAL.STATE is the watchdog's
# ("30 (disconnected)").
DEVICE_COMMAND = ["nmcli", "-t", "-f",
                  "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE,"
                  "GENERAL.CONNECTION,IP4.ADDRESS",
                  "dev", "show"]
PROFILE_FIELDS = ("802-11-wireless.mode,802-11-wireless.ssid,"
                  "connection.autoconnect-priority")

UP_WAIT_S = 45               # nmcli's own wait (its default is 90)
UP_TIMEOUT_S = 60.0          # ours, around it
READ_TIMEOUT_S = 10.0
POLL_S = 10.0
MAX_PROFILES = 8             # rows on the list; the screen fits 5
# Profiles read per poll. All of them, not just the list's eight: the
# watchdog's hotspot guard must see an AP profile wherever nmcli lists
# it (review of 9858e7a). A unit with more than this is "incomplete".
MAX_READ = 32

# The reconnect watchdog (module docstring). NetworkManager's device
# states: 30 is `disconnected` - nothing up, nothing being tried; 100 is
# `activated`. 10/20 (unmanaged/unavailable) are the radio gone or off,
# 40-90 an activation under way: the watchdog leaves all of those alone.
RECONNECT_AFTER_S = 30.0     # disconnected this long on end -> con up
RECONNECT_RETRY_S = 30.0     # ...and this long after the last try ended
NM_DISCONNECTED = 30
NM_ACTIVATED = 100
SUMMARY_EVERY = 5            # failed tries in a row per "still down" LCD line

# What /status serves when nothing has been read (or nmcli is missing).
# The keys live in ui/config.py so the agent needs nothing from here.
WIFI_BLANK = dict.fromkeys(WIFI_FIELDS)

_MODES = {"infrastructure": "client", "": "client", "ap": "hotspot"}
# A profile whose mode could not be read: neither a client the watchdog
# may bring up nor provably not a hotspot - a poll with one in it is not
# acted on (review of 9858e7a).
UNKNOWN = "unknown"


def profile_command(name: str) -> list[str]:
    return ["nmcli", "-t", "-f", PROFILE_FIELDS, "con", "show", name]


def up_command(name: str) -> list[str]:
    return ["sudo", "-n", "nmcli", "--wait", str(UP_WAIT_S), "con", "up", name]


def device_state_command(device: str) -> list[str]:
    """The watchdog's last look before `con up`: the state, nothing else."""
    return ["nmcli", "-t", "-f", "GENERAL.STATE", "dev", "show", device]


def parse_state(value: str) -> "int | None":
    """GENERAL.STATE's number - "30 (disconnected)", or a translated
    "30 (切断されました)" - or None."""
    try:
        return int(str(value).split()[0])
    except (IndexError, ValueError):
        return None


_nmcli_locale: "str | None" = None
_nmcli_locale_lock = threading.Lock()


def nmcli_locale() -> str:
    """C.UTF-8 where the unit has it (`locale -a`, asked once - under a
    lock, so concurrent first callers share one answer - and believed
    only from a zero exit), else plain C (reviews of 3f43a10 and
    8c01839): untranslated either way, UTF-8 when it can."""
    global _nmcli_locale
    with _nmcli_locale_lock:
        if _nmcli_locale is None:
            try:
                result = subprocess.run(["locale", "-a"], capture_output=True,
                                        text=True, encoding="utf-8",
                                        errors="replace",
                                        timeout=READ_TIMEOUT_S,
                                        stdin=subprocess.DEVNULL)
                names = {line.strip().lower().replace("-", "")
                         for line in result.stdout.splitlines()}
                found = result.returncode == 0 and "c.utf8" in names
            except Exception:               # noqa: BLE001 - no locale tool: C
                found = False
            _nmcli_locale = "C.UTF-8" if found else "C"
        return _nmcli_locale


def nmcli_command(args: list[str], timeout: float) -> "tuple[int, str]":
    """REBOOT's run_command, with nmcli's words in English whatever the
    unit's locale: "yes", "ap" and the error lines are what this module
    reads. C.UTF-8, not plain C, where it exists: glib would turn a
    non-ASCII SSID into escapes in an ASCII locale; LANGUAGE goes, since
    gettext would let it translate anyway (review of 9858e7a). sudo keeps
    LC_ALL/LANG for the `con up` (its default env_keep). The output is
    decoded as UTF-8 whatever this process's own locale says."""
    name = nmcli_locale()
    env = dict(os.environ, LC_ALL=name, LANG=name)
    env.pop("LANGUAGE", None)
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout, stdin=subprocess.DEVNULL,
                                env=env)
    except FileNotFoundError:
        return 127, f"{args[0]} not installed"
    return result.returncode, (result.stdout + result.stderr).strip()


def split_terse(line: str) -> list[str]:
    """One `nmcli -t` line into its fields: ':' separates, '\\' escapes
    (an SSID may carry either)."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def _first_line(text: str) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()
    return ""


# nmcli's boilerplate in front of the reason, which the watchdog's log
# line drops so the LCD row reaches the reason ("Secrets were required,
# but not provided"); /status keeps nmcli's whole first line.
_ERROR_PREFIXES = ("Error: ", "Connection activation failed: ")


def _short_error(text: str) -> str:
    for prefix in _ERROR_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text or "?"


class WifiError(Exception):
    """nmcli refused or is not there; the message is its first line."""


@dataclass(frozen=True)
class Scheduled:
    """schedule()'s answer, its own per call (review of 8c01839: no
    shared field another caller could overwrite): truthy when the timer
    runs; else `reason`, and `busy` - the radio was claimed (409), not a
    timer that would not start (500)."""
    ok: bool
    reason: "str | None" = None
    busy: bool = False

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class Profile:
    """One wireless NetworkManager profile, as the list shows it."""
    name: str
    ssid: str
    mode: str            # "client" / "hotspot" / UNKNOWN (or nmcli's word)
    active: bool
    autoconnect: bool
    priority: int = 0    # connection.autoconnect-priority (the watchdog's)


class Wifi:
    """State behind the WIFI screen and /status's "wifi".

    `run` is injectable (args, timeout) -> (code, output), like the
    Rebooter's, so the flow is testable without nmcli or sudo. So is
    the watchdog's `clock`; `notify` gets its lines too (main: the
    runner's log, which the REMOTE screen shows), `watchdog=False`
    turns it off.
    """

    def __init__(self, run=nmcli_command, poll_s: float = POLL_S,
                 read_timeout: float = READ_TIMEOUT_S,
                 up_timeout: float = UP_TIMEOUT_S, echo_log: bool = True,
                 watchdog: bool = True,
                 reconnect_after: float = RECONNECT_AFTER_S,
                 reconnect_retry: float = RECONNECT_RETRY_S,
                 notify=None, clock=time.monotonic):
        self._run = run
        self.poll_s = poll_s
        self.read_timeout = read_timeout
        self.up_timeout = up_timeout
        self._echo_log = echo_log
        self.watchdog = watchdog
        self.reconnect_after = reconnect_after
        self.reconnect_retry = reconnect_retry
        self._notify = notify
        self._clock = clock

        # The cache: what the reader last saw. `state` is exactly what
        # /status serves; `profiles` is the list on the screen.
        self.state: dict = dict(WIFI_BLANK)
        self.profiles: "list[Profile]" = []
        self.read_state = "none"          # none / read / error
        self.read_error: "str | None" = None
        self.choice = 0

        self.phase = IDLE
        self.target: "Profile | None" = None
        self.previous: "str | None" = None
        self.error: "str | None" = None
        self.restored: "bool | None" = None   # FAILED: previous back up?
        # A switch asked for over /wifi/select with a delay: (profile
        # name, due time in time.monotonic()) while it counts down.
        self.pending: "tuple[str, float] | None" = None
        self._timer: "threading.Timer | None" = None
        self._pending_gen = 0
        self._pending_args: tuple = (0, "", None)   # (gen, name, allowed)
        self.log: deque = deque(maxlen=LOG_HISTORY)
        self._lock = threading.Lock()         # cache, log, pending
        self._poll_lock = threading.Lock()    # one poll at a time
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._reader: "threading.Thread | None" = None
        self._thread: "threading.Thread | None" = None

        # The watchdog. `device_state` is the wifi device's NetworkManager
        # state from the last full read (None: unread, no wifi device, or
        # the read failed); `reconnecting` names the profile its `con up`
        # is bringing up while that runs - an in-flight switch, as far as
        # `busy` and switch() are concerned.
        # `all_profiles` is every wireless profile read (the list shows
        # the first MAX_PROFILES of them), `device` the wifi device's
        # name, both from the last poll.
        self.device_state: "int | None" = None
        self.reconnecting: "str | None" = None
        self.all_profiles: "list[Profile]" = []
        self.device: "str | None" = None
        self._last_active: "tuple[str, str | None] | None" = None
        self._down_since: "float | None" = None
        self._reconnect_attempts = 0
        self._reconnect_name: "str | None" = None
        self._reconnect_at: "float | None" = None      # the last try began
        self._reconnect_done: "float | None" = None    # ...and ended
        self._reconnect_error: "str | None" = None
        self._reconnect_thread: "threading.Thread | None" = None
        # One outage's failed tries, and the nmcli lines the LCD has heard
        # in it: the LCD hears the first try, each different line once, a
        # "still down" every SUMMARY_EVERY-th failure and the "on"; the
        # journal every one (reviews of 9858e7a and 3f43a10). An outage
        # ends at any read of the device activated.
        self._outage_fails = 0
        self._outage_said: "set[str]" = set()
        self._loop_error: "str | None" = None
        # Each switch() claim's number: a switch worker publishes its
        # verdict only while the claim is still its own (review of
        # 3f43a10).
        self._switch_gen = 0

    PENDING_NOTE = PENDING_NOTE

    # ---- facts for the screen ----

    @property
    def menu_entry(self) -> MenuEntry:
        return MenuEntry("wifi", "WIFI", "choose this unit's Wi-Fi network")

    @property
    def busy(self) -> bool:
        """True while the switch - or the watchdog's reconnect - runs.
        KEY2 still leaves the screen then; the switch keeps going."""
        return self.phase == CONNECTING or self.reconnecting is not None

    def recent(self, count: int) -> list[str]:
        with self._lock:
            return list(self.log)[-count:]

    def emit(self, message: str, error: bool = False,
             echo: bool = True) -> None:
        first = _first_line(message)
        if error and not first.startswith("ERROR"):
            first = f"ERROR {first}"
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.log.append(f"{stamp} {first}")
        if self._echo_log and echo:
            print(f"{stamp} {message.rstrip()}", flush=True)

    def _say(self, line: str, lcd: bool = True) -> None:
        """A watchdog line: this log, and - `lcd` - `notify` (the runner's
        log the REMOTE screen shows, which prints it to the journal
        itself - so it is printed once). Not `lcd`: the journal only."""
        notify = self._notify if lcd else None
        self.emit(line, echo=notify is None)
        if notify is not None:
            try:
                notify(line)
            except Exception:           # noqa: BLE001 - the log is not the link
                pass

    def hint(self) -> "str | None":
        """The WIFI screen's hint while the watchdog's `con up` runs (and
        a held KEY1's refusal then), else None for the usual one."""
        name = self.reconnecting
        return None if name is None else f"reconnecting {name} - KEY2 back"

    def refuse_reconnecting(self) -> None:
        """A held KEY1 while the watchdog's `con up` runs: refused with
        the hint's words. A switch's DONE/FAILED verdict on the screen
        stays - the hint already says why KEY1 does nothing."""
        note = self.hint()
        if note is not None and self.phase not in (DONE, FAILED):
            self.refuse(note)

    def refuse_unstarted(self) -> None:
        """switch() said False to the HAT's held KEY1: said, not silently
        dropped (review of 3f43a10). A worker that would not start has
        its own FAILED verdict already; that stays."""
        if self.phase != FAILED:
            self.refuse(self.busy_note() or "the switch did not start")

    def busy_note(self) -> "str | None":
        """Why a switch cannot start now - /wifi/select's 409 - or None."""
        name = self.reconnecting
        if self.phase == CONNECTING:
            return "a switch is in flight"
        if name is not None:
            return f"reconnecting {name} - try again in a moment"
        return None

    def snapshot(self) -> dict:
        """/status's "wifi": the cache, never a read."""
        with self._lock:
            return dict(self.state)

    def chosen(self) -> "Profile | None":
        with self._lock:
            if not self.profiles:
                return None
            return self.profiles[self.choice % len(self.profiles)]

    def rows(self) -> "list[tuple[str, str, bool]]":
        """(name, mode, active) per profile, for the screen."""
        with self._lock:
            return [(p.name, p.mode, p.active) for p in self.profiles]

    def current(self) -> "tuple[str, str]":
        """The two lines about now: the SSID (or why there is none) and
        'IP 192.168.51.103  72%  client', whichever parts are known."""
        state = self.snapshot()
        if state["ssid"] is None and state["profile"] is None:
            if self.read_state == "none":
                return "reading...", ""
            if self.read_state == "error":
                return "not connected", f"nmcli: {self.read_error}"
            return "not connected", ""
        parts = []
        if state["ip"]:
            parts.append(f"IP {state['ip']}")
        if state["signal"] is not None:
            parts.append(f"{state['signal']}%")
        if state["mode"]:
            parts.append(state["mode"])
        return state["ssid"] or state["profile"], "  ".join(parts)

    def status_text(self) -> str:
        """The line under the list: what the switch is doing or did."""
        target = self.target
        if self.phase == CONNECTING:
            return f"connecting to {target.ssid if target else '?'}…"
        pending = self.pending_info()
        if pending is not None:
            # A deferred /wifi/select counting down. A refused key on
            # the list says why first.
            text = f"switching to {pending['profile']} in {pending['in_s']} s"
            if self.phase == LOCKED and self.error:
                return f"{self.error} - {text}"
            return text
        if self.phase == DONE:
            return f"on {target.ssid}" if target else ""
        if self.phase == FAILED:
            # The restore's outcome first: it is the one thing the
            # operator must not miss, and nmcli's own sentence behind it
            # can run past the three lines the screen gives this.
            error = self.error or "nmcli refused"
            if self.previous is None:
                return f"ERROR no previous network to go back to: {error}"
            if self.restored is None:       # no restore was even tried
                return f"ERROR {self.previous} not restored: {error}"
            if self.restored:
                return f"ERROR back on {self.previous}: {error}"
            return f"ERROR {self.previous} NOT restored: {error}"
        if self.phase == LOCKED:
            return self.error or LOCKED_NOTE
        # The watchdog's try, below any verdict: a switch's DONE/FAILED
        # stays readable while it runs (the hint says it runs).
        reconnecting = self.reconnecting
        if reconnecting is not None:
            return f"no network - reconnecting {reconnecting}…"
        if self.read_state == "error" and self.state["profile"] is not None:
            return f"nmcli: {self.read_error}"
        return ""

    def key(self) -> tuple:
        """Everything the screen shows, for the App's redraw check."""
        with self._lock:
            state = tuple(self.state[k] for k in WIFI_BLANK)
            profiles = tuple(self.profiles)
        pending = self.pending_info()
        return (self.phase, self.choice, profiles, state, self.error,
                self.restored, self.read_state, self.read_error,
                None if self.target is None else self.target.name,
                None if pending is None else (pending["profile"],
                                              pending["in_s"]),
                self.reconnecting)

    # ---- a switch asked for over the agent, later ----

    def pending_info(self) -> "dict | None":
        """/status.wifi's "pending": {"profile", "in_s"} while a deferred
        switch counts down, else None."""
        with self._lock:
            pending = self.pending
        if pending is None:
            return None
        name, due = pending
        return {"profile": name,
                "in_s": max(0, int(round(due - time.monotonic())))}

    def names(self) -> "list[str]":
        with self._lock:
            return [p.name for p in self.profiles]

    def switch_to(self, name: str, reservation: "int | None" = None) -> bool:
        """Bring the named profile up (the agent's path into switch()):
        the profile itself is handed over, and the cursor follows it so
        the screen shows what is happening. switch()'s answer: False when
        no such profile is in the list, a switch or the watchdog's try is
        in flight, or `reservation` was cancelled (nothing is moved then
        either) - so /wifi/select never says yes to a switch that did not
        start (review of 9858e7a)."""
        with self._lock:
            if self.phase == CONNECTING or self.reconnecting is not None:
                return False
            for index, profile in enumerate(self.profiles):
                if profile.name == name:
                    self.choice = index
                    target = profile
                    break
            else:
                return False
        return self.switch(target, reservation=reservation)

    def schedule(self, name: str, after_s: float, allowed=None) -> Scheduled:
        """Switch to `name` in `after_s` seconds, on a timer thread. A
        pending deferral is replaced. `allowed` (-> refusal note or
        None) is asked again when the timer fires - the PC may have
        taken the unit meanwhile - and a refused switch is skipped and
        logged, never run.

        Returns this call's own Scheduled: truthy when the timer runs.
        Refused - nothing pending - while a switch or the watchdog's try
        holds the radio (busy, the reason says which; checked and
        reserved in one step under the lock, so the watchdog cannot
        claim between /wifi/select's check and this, review of
        3f43a10), or when the timer would not start (not busy)."""
        with self._lock:
            other = self.reconnecting
            note = None
            if self.phase == CONNECTING:
                note = "a switch is in flight"
            elif other is not None:
                note = f"reconnecting {other} - try again in a moment"
            if note is None:
                old = self._timer
                self._pending_gen += 1
                gen = self._pending_gen
                # The timer carries ITS OWN generation: an older timer
                # that expires after a replacement must find itself
                # stale, not run the replacement's switch ahead of time
                # (review of 951e0b7, MED-3).
                timer = threading.Timer(after_s, self._fire_scheduled,
                                        args=(gen,))
                timer.daemon = True
                self.pending = (name, time.monotonic() + after_s)
                self._timer = timer
                self._pending_args = (gen, name, allowed)
        if note is not None:
            self.emit(f"wifi: switch to {name} not scheduled - {note}",
                      error=True)
            return Scheduled(False, note, busy=True)
        if old is not None:
            old.cancel()
        try:
            timer.start()
        except Exception as exc:            # noqa: BLE001 - never stuck pending
            with self._lock:
                if self._timer is timer:
                    self.pending = None
                    self._timer = None
            reason = f"could not schedule: {_first_line(str(exc))}"
            try:
                self.emit(f"wifi: switch to {name} not scheduled: {exc}",
                          error=True)
                self.refuse(reason)
            except Exception:               # noqa: BLE001 - the answer stands
                pass
            return Scheduled(False, reason)
        # Said once the timer runs, and only best-effort: the log line
        # can never leave `pending` stranded (review of 8c01839).
        try:
            self.emit(f"wifi: switch to {name} in {after_s:g} s")
        except Exception:                   # noqa: BLE001 - the timer runs
            pass
        return Scheduled(True)

    def cancel_pending(self) -> bool:
        """Take a deferred switch back; True when there was one."""
        with self._lock:
            timer, self._timer = self._timer, None
            pending, self.pending = self.pending, None
            self._pending_gen += 1          # a fire already racing is void
        if timer is not None:
            timer.cancel()
        if pending is not None:
            self.emit(f"wifi: switch to {pending[0]} cancelled")
            if self.phase == LOCKED and self.error == PENDING_NOTE:
                self.reset()
        return pending is not None

    def _fire_scheduled(self, fired_gen: int) -> None:
        with self._lock:
            gen, name, allowed = self._pending_args
            if (fired_gen != gen or gen != self._pending_gen
                    or self.pending is None):
                return                      # cancelled or replaced meanwhile
            self._timer = None
        # `pending` stays set until switch() claims the radio with this
        # reservation - one step under the lock - or the fire gives up:
        # no gap for the watchdog to slip into (review of 9858e7a).
        try:
            self._fire(gen, name, allowed)
        finally:
            with self._lock:
                if self._pending_gen == gen:
                    self.pending = None

    def _fire(self, gen: int, name: str, allowed) -> None:
        if self.phase == LOCKED and self.error == PENDING_NOTE:
            self.reset()                    # the countdown's own refusals
        note = None
        if allowed is not None:
            try:
                note = allowed()
            except Exception as exc:        # noqa: BLE001 - a doubt is a no
                note = str(exc) or exc.__class__.__name__
        if note:
            self.emit(f"wifi: switch to {name} skipped - {note}", error=True)
            self.refuse(f"{note} - switch to {name} skipped")
            return
        if name not in self.names():
            # Said, not done - like a lock: nothing was tried, so there
            # is no restore outcome for FAILED's wording to report.
            self.emit(f"wifi: switch to {name} skipped - no such profile",
                      error=True)
            self.refuse(f"no such profile: {name} - switch skipped")
            return
        if not self.switch_to(name, reservation=gen):
            with self._lock:
                cancelled = self._pending_gen != gen
            if cancelled:
                return                      # cancel_pending() said so
            # False with the name known: a switch or the watchdog's try
            # is in flight (the check and the claim are one step in
            # switch()) - or the claim was made and its worker would not
            # start, which is no "busy" (review of 3f43a10): its FAILED
            # verdict stays on the screen.
            other = self.reconnecting
            if self.phase == CONNECTING:
                busy = "a switch is in flight"
            elif other is not None:
                busy = f"reconnecting {other}"
            else:
                self.emit(f"wifi: switch to {name} skipped - the switch did "
                          "not start", error=True)
                if self.phase != FAILED:
                    self.refuse(f"the switch did not start - switch to "
                                f"{name} skipped")
                return
            self.emit(f"wifi: switch to {name} skipped - {busy}", error=True)
            self.refuse(f"busy - switch to {name} skipped")

    # ---- the list ----

    def select(self, step: int) -> None:
        if self.busy:
            return
        with self._lock:
            if self.profiles:
                self.choice = (self.choice + step) % len(self.profiles)
        if self.phase != IDLE:
            self.reset()                # a verdict is read; moving on

    def select_active(self) -> None:
        """The cursor on the profile in use, so a hold does nothing rash."""
        with self._lock:
            for index, profile in enumerate(self.profiles):
                if profile.active:
                    self.choice = index
                    return

    def _keep_choice(self, before: "list[Profile]") -> None:
        # Called with the lock held, after `profiles` was replaced: the
        # cursor stays on the same NAME, or clamps.
        if before and 0 <= self.choice < len(before):
            name = before[self.choice].name
            for index, profile in enumerate(self.profiles):
                if profile.name == name:
                    self.choice = index
                    return
        self.choice = min(self.choice, max(0, len(self.profiles) - 1))

    # ---- the reader ----

    def start_reader(self) -> None:
        """The daemon that fills the cache every poll_s (main wires it)."""
        if self._reader is not None:
            return
        self._reader = threading.Thread(target=self._loop, daemon=True,
                                        name="wifi-reader")
        self._reader.start()

    def refresh(self) -> None:
        """Read again soon (the screen was just opened)."""
        if self._reader is None:
            self.start_reader()         # its first poll is right away
        else:
            self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        # The reader must never die: the WIFI screen and /status's cache
        # live on it. An exception nobody foresaw is logged once (until a
        # clean turn, or a different one) and the loop goes on.
        while not self._stop.is_set():
            try:
                self.watch(self.poll())
            except Exception as exc:        # noqa: BLE001 - logged, not fatal
                note = _first_line(str(exc)) or exc.__class__.__name__
                with self._lock:
                    self._down_since = None     # a failed turn saw nothing
                if note != self._loop_error:
                    self._loop_error = note
                    try:
                        self.emit(f"wifi: reader error: {note}", error=True)
                    except Exception:       # noqa: BLE001
                        pass
            else:
                self._loop_error = None
            self._wake.wait(self.poll_s)
            self._wake.clear()

    def _ask(self, args: list[str]) -> str:
        try:
            code, output = self._run(args, self.read_timeout)
        except subprocess.TimeoutExpired:
            raise WifiError(f"{args[0]} timed out")
        if code != 0:
            raise WifiError(_first_line(output) or f"{args[0]} exit {code}")
        return output

    def poll(self) -> bool:
        """One refresh of the cache, on the caller's thread (the reader's,
        normally; a test's). Never raises. A read nmcli refuses (a busy
        D-Bus, a restart of NetworkManager) keeps the last good answer
        and notes the error for the screen; only a read that went
        through replaces the cache - so /status does not flicker to
        nulls on a hiccup, and a first read that fails leaves nulls.
        Returns whether everything was read - a caller about to act on
        the cache (the switch's restore) must not act on a stale one."""
        with self._poll_lock:
            error = None
            every = state = device = name = None
            complete = False
            try:
                every, complete = self._read_profiles()
            except Exception as exc:        # noqa: BLE001 - shown, not raised
                error = _first_line(str(exc)) or exc.__class__.__name__
            try:
                state, device, name = self._read_state(
                    every if every is not None else self.all_profiles)
            except Exception as exc:        # noqa: BLE001
                error = error or _first_line(str(exc)) or exc.__class__.__name__
            with self._lock:
                if every is not None:
                    before = self.profiles
                    self.all_profiles = every
                    self.profiles = every[:MAX_PROFILES]
                    self._keep_choice(before)
                if state is not None:
                    self.state = state
                self.read_error = error
                self.read_state = "error" if error else "read"
                # The watchdog acts on a full read only - every profile's
                # mode read, the device's state given: anything less is
                # "unknown", never "disconnected" (review of 9858e7a).
                full = error is None and complete
                self.device_state = device if full else None
                self.device = name if error is None else self.device
                if state is not None and device == NM_ACTIVATED:
                    # The device read went through and says activated:
                    # whatever the profile reads did, this outage is over
                    # and this is where the unit lives - ACTIVATED, not
                    # merely named (a switch still trying may fail). On a
                    # partial read its mode stays UNKNOWN until a full
                    # one resolves it by name (review of 3f43a10: a
                    # radxa-05 that went onto its AP then must never be
                    # pulled back to the router).
                    self._outage_fails = 0
                    self._outage_said = set()
                    if state["profile"]:
                        self._last_active = (
                            state["profile"],
                            state["mode"] if full else UNKNOWN)
            return error is None

    def _read_profiles(self) -> "tuple[list[Profile], bool]":
        """Every wireless profile (up to MAX_READ), and whether each one's
        mode was read: a `con show <name>` that fails or leaves the mode
        out makes that profile UNKNOWN and the read incomplete."""
        found = []
        malformed = False
        for line in self._ask(LIST_COMMAND).splitlines():
            if not line.strip():
                continue
            fields = split_terse(line)
            if len(fields) != 4 or not fields[0]:
                # Exactly NAME:TYPE:ACTIVE:AUTOCONNECT with a name, else
                # not dropped unsaid: the read is incomplete (reviews of
                # 3f43a10 and 8c01839).
                malformed = True
                continue
            if fields[1] != WIRELESS:
                continue
            if fields[2] not in ("yes", "no") or fields[3] not in ("yes", "no"):
                malformed = True
            found.append((fields[0], fields[2] == "yes", fields[3] == "yes"))
        complete = len(found) <= MAX_READ and not malformed
        profiles = []
        for name, active, autoconnect in found[:MAX_READ]:
            ssid, mode, priority = name, None, 0
            try:
                for line in self._ask(profile_command(name)).splitlines():
                    fields = split_terse(line)
                    if len(fields) < 2:
                        continue
                    key, value = fields[0], ":".join(fields[1:])
                    if key == "802-11-wireless.mode":
                        mode = _MODES.get(value, value)
                    elif key == "802-11-wireless.ssid" and value:
                        ssid = value
                    elif key == "connection.autoconnect-priority":
                        try:
                            priority = int(value)
                        except ValueError:
                            pass
            except WifiError:
                pass                        # the name is still a row
            if mode is None:
                mode, complete = UNKNOWN, False
            profiles.append(Profile(name, ssid, mode, active, autoconnect,
                                    priority))
        return profiles, complete

    def _read_state(self, profiles: "list[Profile]"
                    ) -> "tuple[dict, int | None, str | None]":
        """(/status's dict, the wifi device's NetworkManager state - None
        when there is no wifi device or no state was given - and its
        name)."""
        state = dict(WIFI_BLANK)
        device = name = None
        # The wifi device: its connection and IPv4 address. Blocks start
        # at each GENERAL.DEVICE, whether or not nmcli blank-lines them.
        blocks: "list[dict]" = []
        for line in self._ask(DEVICE_COMMAND).splitlines():
            fields = split_terse(line)
            if len(fields) < 2:
                continue
            key, value = fields[0], ":".join(fields[1:])
            if key == "GENERAL.DEVICE" or not blocks:
                blocks.append({})
            blocks[-1][key] = value
        for block in blocks:
            if block.get("GENERAL.TYPE") != "wifi":
                continue
            profile = block.get("GENERAL.CONNECTION") or ""
            state["profile"] = None if profile in ("", "--") else profile
            device = parse_state(block.get("GENERAL.STATE") or "")
            name = block.get("GENERAL.DEVICE") or None
            for key, value in block.items():
                if key.startswith("IP4.ADDRESS") and value:
                    state["ip"] = value.split("/")[0]
                    break
            break
        if state["profile"] is not None:
            for profile in profiles:
                if profile.name == state["profile"]:
                    state["mode"] = profile.mode
                    state["ssid"] = profile.ssid
                    break
        # What the radio says it is on, and how well - the hotspot has
        # no such row (and some builds refuse the question in AP mode),
        # which is why the SSID above came from the profile first.
        try:
            rows = self._ask(SCAN_COMMAND).splitlines()
        except WifiError:
            rows = []
        for line in rows:
            fields = split_terse(line)
            if len(fields) >= 3 and fields[0] == "yes":
                if fields[1]:
                    state["ssid"] = fields[1]
                try:
                    state["signal"] = int(fields[2])
                except ValueError:
                    pass
                break
        if state["mode"] == "hotspot":
            # A hotspot has no signal of its own; whatever the list said
            # (some builds put the AP itself there at 100) is not one.
            state["signal"] = None
        return state, device, name

    # ---- the reconnect watchdog ----

    def reconnect_info(self) -> "dict | None":
        """/status.wifi's "reconnect": the watchdog's tries this boot
        (null with --no-wifi-watchdog)."""
        if not self.watchdog:
            return None
        with self._lock:
            at = self._reconnect_at
            return {"attempts": self._reconnect_attempts,
                    "last": self._reconnect_name,
                    "last_ago_s": None if at is None
                    else round(self._clock() - at, 1),
                    "last_error": self._reconnect_error}

    def watch(self, read_ok: bool = True) -> bool:
        """One look of the watchdog, after a poll (the reader's loop; a
        test's). True when it claimed the radio for a try - which runs on
        a worker, so the reader, the LCD loop and /status never wait for
        nmcli. Never raises."""
        if not self.watchdog:
            return False
        now = self._clock()
        with self._lock:
            if not read_ok or self.device_state != NM_DISCONNECTED:
                # Connecting, connected, radio off or gone, or unknown:
                # the 30 s start again from the next `disconnected`.
                self._down_since = None
                return False
            if self._down_since is None:
                self._down_since = now
            if now - self._down_since < self.reconnect_after:
                return False
            done = self._reconnect_done
            if done is not None and now - done < self.reconnect_retry:
                return False
            # The row's switch (held KEY1, /wifi/select) running or
            # counting down - the exhibition's timed move among them, up
            # to the moment switch() claims the radio with it - is a
            # deliberate move: never pulled back from under it.
            if (self.phase == CONNECTING or self.pending is not None
                    or self.reconnecting is not None):
                return False
            target = self._reconnect_target()
            if target is None:
                return False
            # The claim. From here every path ends in _reconnected(),
            # which lets it go (review of 9858e7a).
            self.reconnecting = target.name
        try:
            thread = threading.Thread(target=self._reconnect, args=(target,),
                                      daemon=True, name="wifi-watchdog")
            self._reconnect_thread = thread
            thread.start()
        except Exception:                   # noqa: BLE001 - never stuck busy
            self._reconnected(target.name, counted=False)
            return False
        return True

    def _reconnect_target(self) -> "Profile | None":
        # Called with the lock held, after a full read. The client
        # profile last activated this boot; else - only on a unit without
        # a hotspot profile among ALL its profiles, not just the list's
        # eight - the autoconnect client profile of highest priority (the
        # first listed on a tie). Never a hotspot, never an UNKNOWN.
        every = self.all_profiles
        by_name = {p.name: p for p in every}
        if self._last_active is not None:
            name, mode = self._last_active
            profile = by_name.get(name)
            if profile is not None:
                mode = profile.mode
            if mode == "hotspot":
                # The unit WAS the hotspot (radxa-05 at an exhibition):
                # not raised again from here, and not moved to a client
                # network either - the operator decides that.
                return None
            if mode != "client":
                return None                 # gone, and what it was is unsure
            if profile is not None:
                return profile
        if any(p.mode != "client" for p in every):
            # radxa-05 (an AP profile) before it was on anything this
            # boot: that is radxa/exhibition-net.sh's to settle (the
            # router, or the hotspot), and a `con up` racing its own
            # could take the radio off the hotspot it is raising. Any
            # other mode (ad-hoc, mesh) counts the same: not a guess.
            return None
        candidates = [p for p in every if p.autoconnect]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.priority)

    def _still_disconnected(self) -> bool:
        """The worker's last look, right before `con up`: the device's
        state once more, now. NetworkManager may have started its own
        activation since the poll (review of 9858e7a)."""
        device = self.device
        if not device:
            return False
        try:
            output = self._ask(device_state_command(device))
        except Exception:                   # noqa: BLE001 - a doubt is a no
            return False
        for line in output.splitlines():
            fields = split_terse(line)
            if len(fields) >= 2 and fields[0] == "GENERAL.STATE":
                return parse_state(":".join(fields[1:])) == NM_DISCONNECTED
        return False

    def _reconnect(self, target: Profile) -> None:
        counted = False
        error = None
        try:
            # The fresh read and `con up` back to back: nothing between
            # them - no log line, no lock (review of 3f43a10); the try's
            # lines and bookkeeping come after nmcli answers. Not
            # disconnected any more: dropped silently, not counted, the
            # usual retry wait.
            if not self._still_disconnected():
                return
            began = self._clock()
            code, output = self._up(target.name)
            with self._lock:
                self._reconnect_attempts += 1
                self._reconnect_name = target.name
                self._reconnect_at = began
                self._reconnect_error = None
                first = self._outage_fails == 0
            counted = True
            # Short, for the REMOTE screen's log row (DejaVuSans 12 px,
            # 224 px with "HH:MM:SS " in front): "wifi: no network 30 s -
            # con up R2.net" ran to 284 px; this fits R2.net and AZ-Epaper.
            # Only an outage's first try reaches the LCD.
            self._say(f"wifi: reconnect {target.name}", lcd=first)
            if code == 0:
                self.poll()                 # the new SSID/IP for /status
                with self._lock:            # (the poll ends the outage too)
                    self._outage_fails = 0
                    self._outage_said = set()
                self._say(f"wifi: on {target.ssid}")
            else:
                error = _first_line(output) or f"nmcli exit {code}"
                self._failed(error)
        except Exception as exc:            # noqa: BLE001 - never stuck busy
            error = error or _first_line(str(exc)) or exc.__class__.__name__
        finally:
            self._reconnected(target.name, counted, error)

    def _failed(self, error: str) -> None:
        """One failed try's lines. The LCD hears each different nmcli
        line once per outage and one "still down" per SUMMARY_EVERY
        failures of it, whatever the lines said (review of 3f43a10: a
        line that keeps changing must not flood it); the journal hears
        every one."""
        with self._lock:
            self._outage_fails += 1
            lcd = error not in self._outage_said
            self._outage_said.add(error)
            fails = self._outage_fails
        self._say(f"wifi: up failed: {_short_error(error)}", lcd=lcd)
        if fails % SUMMARY_EVERY == 0:
            self._say(f"wifi: still down, try {fails}")

    def _reconnected(self, name: str, counted: bool,
                     error: "str | None" = None) -> None:
        with self._lock:
            self.reconnecting = None
            self._reconnect_done = self._clock()
            if counted:
                self._reconnect_error = error
        # A held KEY1's refusal during the try is over with it.
        if self.phase == LOCKED and self.error == \
                f"reconnecting {name} - KEY2 back":
            self.reset()

    def join_reconnect(self, timeout: "float | None" = None) -> None:
        thread = self._reconnect_thread
        if thread is not None:
            thread.join(timeout)

    # ---- the switch ----

    def reset(self) -> None:
        """Back to the plain list (a verdict was read, or KEY2 left)."""
        # Check-then-set under the lock: switch() claims CONNECTING
        # there, and a reset racing it must not undo the claim.
        with self._lock:
            if self.phase == CONNECTING:
                return
            self.phase = IDLE
            self.error = None
            self.target = None
            self.restored = None

    def reopen(self) -> None:
        """The screen is opened again (App._enter_wifi). A FAILED verdict
        stays until it is read - the operator may have left during the
        switch (KEY2 is allowed then) and must find out what became of
        it; DONE, LOCKED and the plain list start clean."""
        if self.phase != FAILED:
            self.reset()

    def refuse(self, note: str = LOCKED_NOTE) -> None:
        """The App's answer while the PC drives the unit: said, not done."""
        with self._lock:
            if self.phase == CONNECTING:
                return
            self.phase = LOCKED
            self.error = note
            self.target = None
            self.restored = None

    def switch(self, target: "Profile | None" = None,
               reservation: "int | None" = None) -> bool:
        """Bring `target` - by default the chosen profile - up on a thread
        of its own. The busy check and the claim (phase CONNECTING) are
        one step under the lock: the HAT's held KEY1, the agent's
        /wifi/select and a deferral's timer can all arrive at once, and
        only one of them may start nmcli (review of 951e0b7, LOW-7) - and
        not while the watchdog's own `con up` runs. A deferral's timer
        hands its `reservation` (its generation): still current, its
        `pending` is cleared in that same step, so the watchdog never
        finds the radio unclaimed between the two; cancelled, nothing is
        done. True when the switch started or was not needed (already
        on that profile); False when nothing was started."""
        with self._lock:
            if self.phase == CONNECTING or self.reconnecting is not None:
                return False
            if reservation is not None:
                if reservation != self._pending_gen:
                    return False            # cancelled or replaced
                self.pending = None
                self._timer = None
            if target is None and self.profiles:
                target = self.profiles[self.choice % len(self.profiles)]
            if target is None:
                return False
            self.target = target
            self.error = None
            self.restored = None
            # This claim's number: only its own worker may publish its
            # verdict (review of 3f43a10).
            self._switch_gen += 1
            gen = self._switch_gen
            previous = self.state["profile"]
            if target.active:
                # `con up` on the active profile would drop and re-raise
                # it for nothing - say so instead.
                self.phase = DONE
            else:
                self.previous = previous
                self.phase = CONNECTING
        if target.active:
            self.emit(f"wifi: already on {target.name}")
            return True
        try:
            self._thread = threading.Thread(target=self._switch,
                                            args=(target, previous, gen),
                                            daemon=True, name="wifi-switch")
            self._thread.start()
        except Exception as exc:            # noqa: BLE001 - never stuck CONNECTING
            # FAILED first, then the log line: nothing may see CONNECTING
            # with no worker behind it (review of 3f43a10).
            error = f"could not start: {exc}"
            with self._lock:
                if self._switch_gen == gen:
                    self.error = error
                    self.phase = FAILED
            self.emit(error, error=True)
            return False
        return True

    def join(self, timeout: "float | None" = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _up(self, name: str) -> "tuple[int, str]":
        try:
            return self._run(up_command(name), self.up_timeout)
        except subprocess.TimeoutExpired:
            return 124, f"nmcli timed out after {self.up_timeout:.0f} s"
        except Exception as exc:            # noqa: BLE001 - a verdict, not a crash
            return 1, str(exc) or exc.__class__.__name__

    def _switch(self, target: Profile, previous: "str | None",
                gen: int) -> None:
        # Whatever happens on this thread, the screen must not stay on
        # CONNECTING: an exception nobody foresaw becomes a FAILED verdict.
        # The verdict is published here, as the worker's last step, under
        # the lock and only while the claim is still its own: once DONE
        # or FAILED shows, another switch may claim, and an old worker's
        # cleanup must never mark that one FAILED (review of 3f43a10).
        # An exception's detail stays local until that same guarded step:
        # a stale worker never writes a newer claim's error (review of
        # 8c01839).
        verdict = FAILED
        detail = None
        try:
            verdict = self._switch_body(target, previous)
        except Exception as exc:            # noqa: BLE001 - shown, not raised
            detail = _first_line(str(exc)) or exc.__class__.__name__
            self.emit(detail, error=True)
        finally:
            with self._lock:
                if self._switch_gen == gen and self.phase == CONNECTING:
                    if detail is not None:
                        self.error = self.error or detail
                    self.phase = verdict if verdict in (DONE, FAILED) else FAILED

    def _switch_body(self, target: Profile, previous: "str | None") -> str:
        """The switch itself; returns its verdict (DONE / FAILED) for
        _switch() to publish."""
        self.emit(f"wifi: con up {target.name}")
        code, output = self._up(target.name)
        if code == 0:
            self.poll()                     # the new SSID/IP before DONE shows
            self.emit(f"wifi: on {target.ssid}")
            return DONE
        self.error = _first_line(output) or f"nmcli exit {code}"
        self.emit(self.error, error=True)
        fresh = self.poll()
        if previous and previous != target.name:
            # A wrong choice must not strand the unit: the profile it
            # was on comes back. NetworkManager's own autoconnect may
            # have done that already - then `con up` again would only
            # drop and re-raise a link that is fine. Only a poll that
            # actually read says so: a failed read keeps the cache from
            # before the switch, which names `previous` while the device
            # may sit disconnected (review round 2, 2026-09-30).
            if fresh and self.snapshot()["profile"] == previous:
                self.restored = True
                self.emit(f"wifi: still on {previous}")
            else:
                self.emit(f"wifi: back to {previous}")
                code, output = self._up(previous)
                self.restored = code == 0
                if self.restored:
                    self.emit(f"wifi: back on {previous}")
                else:
                    self.emit(f"{previous}: {_first_line(output) or code}",
                              error=True)
                self.poll()
        return FAILED
