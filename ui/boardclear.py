"""CLEAR BOARD: send one of the protocol's two clearing commands to ONE
board from the LCD (2026-11-17).

The FW_261109 trial firmware's engineer said "maybe need to clear the
wave first"; V1.4 has exactly two commands that clear anything stored
on a board, and this screen sends either of them to the board the
operator picks:

    CLEAR COLOURS  (0x15)  all 20 slots' pictures, the factory demo in
                           17/18 included (V1.4 7.3)
    CLEAR AUTOPLAY (0x05)  the power-on auto-play configuration back to
                           its defaults, persisted (V1.4 6.7) - the
                           master still auto-plays slot 0 every 15 s at
                           power-up afterwards, that is firmware default

The board list is a relay-safe presence sweep (ota.find_boards: PLAY_STOP
to every configured address, the same sweep UPDATE FW and FW VERSION
start with), so a board behind the 485 can be chosen too - neither
command is OTA, the master relays them like any other. UP/DOWN pick among
the boards that answered, LEFT/RIGHT the command, a plain KEY1 sweeps
again. Sending takes KEY1 *held twice*: the first hold only arms it for
ARM_S ("hold KEY1 again: CLEAR COLOURS board 03"), any other button
disarms, a second hold on the same board and command within that sends -
the BACKUPS restore gesture, because an accidental press must not erase
a board. The send is the order of V1.4 10.3: 0x17 stop to that board,
then the command; ACK_SUCCESS / ACK_FAIL (with its error code) / no
answer is the verdict, on the screen and in the journal. Nothing is
recorded on the unit - but after every 0x15 sent the runner is told to
forget that board's burn cache (forget_board), whatever the answer, so
the next burn writes it again instead of skipping "unchanged" pictures
that are gone (review of d08e126).

Like UPDATE FW it needs the port to itself: the App stops the runner on
the way in and refuses the row while a show holds the unit (_remote_holds,
plus a PC show uploaded and waiting - its burned pictures would be the
ones erased). While a sweep or a send is in flight `busy` is true, which
also keeps the PC's session off the port (App wires it into remote.busy).
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import serial

import ota
from epaper.commands import clear_all_colors, clear_autoplay, stop
from epaper.protocol import ACK_FAIL, ACK_INVALID_CMD, ACK_NAMES, ACK_SUCCESS
from epaper.transport import Bus, find_port

from .config import LOG_HISTORY
from .updater import MenuEntry

IDLE = "idle"              # choosing board and command
SCANNING = "scanning"      # the presence sweep is on the port
SENDING = "sending"        # 0x17 then the command is on the port
DONE = "done"              # ACK_SUCCESS
FAILED = "failed"          # ACK_FAIL, another ACK, no answer, or no port

# The first KEY1 hold arms the send for this long; the second sends.
# 5 s like the BACKUPS restore (ui/exhibition.py RESTORE_ARM_S).
ARM_S = 5.0
# A board erasing 20 slots of flash may take a while before it answers;
# a lost frame is sent once more (both commands are idempotent).
CLEAR_ACK_S = 4.0
CLEAR_TRIES = 2
STOP_ACK_S = 0.8
# Before the command: the line quiet this long, so a late 0x17 ACK is
# not taken for the command's (ACKs do not name the command).
DRAIN_QUIET_S = 0.3
DRAIN_LIMIT_S = 2.0


@dataclass(frozen=True)
class ClearAction:
    key: str
    label: str        # what the screen and the journal call it
    detail: str       # one short line: what it erases
    build: object     # commands.clear_all_colors / clear_autoplay


ACTIONS = (
    ClearAction("colours", "CLEAR COLOURS (0x15)",
                "erases all 20 slots' pictures (17/18 too)",
                clear_all_colors),
    ClearAction("autoplay", "CLEAR AUTOPLAY (0x05)",
                "power-on autoplay back to defaults",
                clear_autoplay),
)


def describe_answer(action: ClearAction, ack) -> "tuple[bool, str]":
    """(cleared?, the verdict line) for the command's answer."""
    if ack is None:
        return False, "no answer"
    if ack.cmd == ACK_SUCCESS:
        return True, "ACK_SUCCESS"
    text = ack.describe()
    if action.key == "autoplay" and ack.cmd == ACK_FAIL and ack.data[:1] == b"\x0a":
        # V1.4 6.7: firmware before V1.3 does not know 0x05.
        text += " - firmware older than V1.3?"
    elif ack.cmd == ACK_INVALID_CMD:
        text += " - firmware does not know this command"
    return False, text


class BoardClearer:
    """State behind the CLEAR BOARD screen; the port work runs in a thread.

    `open_bus`, `locate`, `find` and `clock` are injectable so the whole
    flow is testable without a board.
    """

    def __init__(self, boards: "list[int] | None" = None,
                 port: "str | None" = None, open_bus=None, locate=find_port,
                 find=ota.find_boards, clock=time.monotonic,
                 echo_log: bool = True, forget_board=None):
        self.boards = list(boards) if boards else list(ota.SCAN_BOARDS)
        # The runner's group count (DemoRunner._group_count): the list's
        # length, or its highest address when it has gaps.
        self.groups = max(len(self.boards), max(self.boards))
        self.port = port
        self._open_bus = open_bus or (lambda p: Bus(p, verbose=False))
        self._locate = locate
        self._find = find
        self._clock = clock
        self._echo_log = echo_log
        # The runner's forget_board (ui/main.py wires it): called after
        # every 0x15 sent, see _forget().
        self.forget_board = forget_board

        self.found: list[int] = []      # last sweep's answering addresses
        self.addr: "int | None" = None
        self.action_index = 0
        self.phase = IDLE
        self.status = ""                # the line under the choice
        self.log: deque[str] = deque(maxlen=LOG_HISTORY)
        self._armed: "tuple[int, str, float] | None" = None
        self._lock = threading.Lock()
        self._thread: "threading.Thread | None" = None

    # ---- facts for the screen ----

    @property
    def menu_entry(self) -> MenuEntry:
        return MenuEntry("clear", "CLEAR BOARD", "0x15 colours / 0x05 autoplay")

    @property
    def action(self) -> ClearAction:
        return ACTIONS[self.action_index]

    @property
    def busy(self) -> bool:
        """True while the sweep or a send holds the port."""
        return self.phase in (SCANNING, SENDING)

    @property
    def finished(self) -> bool:
        return self.phase in (DONE, FAILED)

    @property
    def choice(self) -> str:
        """'2/3' among the boards found, '' with one or none."""
        if len(self.found) < 2 or self.addr not in self.found:
            return ""
        return f"{self.found.index(self.addr) + 1}/{len(self.found)}"

    def armed_text(self) -> str:
        """`hold KEY1 again: CLEAR COLOURS board 03` while the first
        hold's arming stands, else ""."""
        armed = self._armed
        if armed is None or self._clock() - armed[2] > ARM_S:
            return ""
        action = next(a for a in ACTIONS if a.key == armed[1])
        label = action.label.split(" (")[0]
        return f"hold KEY1 again: {label} board {armed[0]:02d}"

    def recent(self, count: int) -> "list[str]":
        with self._lock:
            return list(self.log)[-count:]

    def key(self) -> tuple:
        """Everything the screen shows, for the App's redraw check."""
        return (self.phase, self.addr, tuple(self.found), self.action_index,
                self.status, self.armed_text(), tuple(self.recent(6)))

    def emit(self, message: str, error: bool = False) -> None:
        """One line on the LCD log, the whole text on stdout (the journal
        under systemd) - the updater's sink, same shape."""
        first = message.strip().splitlines()[0] if message.strip() else ""
        if error and not first.startswith("ERROR"):
            first = f"ERROR {first}"
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.log.append(f"{stamp} {first}")
        if self._echo_log:
            print(f"{stamp} clear: {message.rstrip()}", flush=True)

    # ---- choosing ----

    def disarm(self) -> None:
        self._armed = None

    def select(self, step: int) -> None:
        """UP/DOWN: another board among those the sweep found."""
        if self.busy:
            return
        self._back_to_choice()
        if not self.found:
            return
        index = self.found.index(self.addr) if self.addr in self.found else 0
        self.addr = self.found[(index + step) % len(self.found)]

    def select_action(self, step: int) -> None:
        """LEFT/RIGHT: the other command."""
        if self.busy:
            return
        self._back_to_choice()
        self.action_index = (self.action_index + step) % len(ACTIONS)

    def reset(self) -> None:
        """Back to the choice (on entry, or after a verdict was read)."""
        if self.busy:
            return
        self._back_to_choice()
        self.status = ""

    def refuse(self, note: str) -> None:
        """A send the App will not allow now (a show took the unit)."""
        self._armed = None
        self.phase = IDLE
        self.status = note
        self.emit(f"refused: {note}", error=True)

    def _back_to_choice(self) -> None:
        self._armed = None
        if self.finished:
            self.phase = IDLE
            self.status = ""

    # ---- the sweep ----

    def scan(self) -> None:
        """Find who answers (PLAY_STOP to every configured address), in
        the background. A plain KEY1, and the way in."""
        if self.busy:
            return
        self._armed = None
        self.phase = SCANNING
        self.status = "scanning..."
        self._start(self._scan)

    def _scan(self) -> None:
        result = self._with_bus(lambda bus: self._find(bus, self.boards))
        if isinstance(result, str):            # no port / wedged / error
            self.found = []
            self.addr = None
            self.status = result
            self.emit(result, error=True)
            self.phase = IDLE
            return
        self.found = sorted(result)
        if self.addr not in self.found:
            # The lowest address first; the screen names it large, and
            # the arming line names it again before anything is sent.
            self.addr = self.found[0] if self.found else None
        if not self.found:
            self.status = "no board answers - KEY1 rescans"
        elif len(self.found) == 1:
            self.status = f"board {self.found[0]:02d} found"
        else:
            listed = ",".join(f"{a:02d}" for a in self.found)
            self.status = f"boards {listed} answer"
        self.emit(self.status)
        self.phase = IDLE

    # ---- the send ----

    def confirm(self) -> None:
        """KEY1 held. The FIRST hold only arms the send of the chosen
        command to the chosen board (ARM_S); a second hold on the same
        pair, within that, sends it."""
        if self.busy or self.finished or self.addr is None:
            return
        armed = self._armed
        pair = (self.addr, self.action.key)
        if not (self.armed_text() and armed is not None
                and (armed[0], armed[1]) == pair):
            self._armed = (self.addr, self.action.key, self._clock())
            return
        self._armed = None
        addr, action = self.addr, self.action
        self.phase = SENDING
        self.status = f"{action.label} -> board {addr:02d}..."
        self.emit(f"board {addr:02d}: {action.label}")
        self._start(lambda: self._send(addr, action))

    def _send(self, addr: int, action: ClearAction) -> None:
        tried = False                     # the command itself went out
        phase = FAILED
        try:
            port = self.port or self._locate()
            if not port:
                raise RuntimeError("no serial port")
            with self._open_bus(port) as bus:
                # V1.4 10.3: stop playback first. Its answer is only
                # logged - the command's own answer is the verdict.
                stopped = self._request(bus, stop(addr, self.groups),
                                        STOP_ACK_S, 1)
                said = ("no answer" if stopped is None else
                        ACK_NAMES.get(stopped.cmd, f"0x{stopped.cmd:02X}"))
                self.emit(f"board {addr:02d} stop 0x17: {said}")
                # An ACK does not say which command it answers: a 0x17
                # ACK arriving after STOP_ACK_S would read as the
                # command's verdict ("CLEARED" with the 0x15 dropped).
                # So the line has to be quiet before the command goes.
                self._drain(bus)
                tried = True
                ack = self._request(bus, action.build(addr, self.groups),
                                    CLEAR_ACK_S, CLEAR_TRIES)
            cleared, verdict = describe_answer(action, ack)
            self.status = f"board {addr:02d}: {verdict}"
            self.emit(f"board {addr:02d} {action.label}: {verdict}",
                      error=not cleared)
            phase = DONE if cleared else FAILED
        except serial.SerialTimeoutException:
            self.status = "port wedged: replug USB / power-cycle board"
            self.emit(ota.WEDGE_MSG, error=True)
        except Exception as exc:          # noqa: BLE001 - shown, not raised
            self.status = f"ERROR {exc}"
            self.emit(f"{exc}", error=True)
        if tried and action.key == "colours":
            self._forget(addr)
        self.phase = phase

    def _forget(self, addr: int) -> None:
        """0x15 went out to `addr`: whatever the answer (a board that
        answered late, or not at all, may still have erased), the unit
        must stop believing it holds pictures there. The runner's burn
        cache outlives runner.stop() on purpose and a burn skips every
        (board, slot) it remembers - a re-Upload of the same show, or a
        demo burned again, would write nothing to this board and play
        its empty slots (review of d08e126, BLOCKER 1)."""
        if self.forget_board is None:
            return
        try:
            self.forget_board(addr)
        except Exception as exc:          # noqa: BLE001 - shown, not raised
            self.emit(f"burn cache for board {addr:02d} not forgotten: "
                      f"{exc}", error=True)
            return
        self.emit(f"board {addr:02d}: next burn writes it again")

    @staticmethod
    def _drain(bus, quiet: "float | None" = None,
               limit: "float | None" = None) -> None:
        """Read and drop frames until the line has been quiet for
        `quiet` seconds (or `limit` has passed)."""
        quiet = DRAIN_QUIET_S if quiet is None else quiet
        deadline = time.monotonic() + (DRAIN_LIMIT_S if limit is None
                                       else limit)
        while time.monotonic() < deadline:
            if bus.recv(timeout=quiet) is None:
                return

    @staticmethod
    def _request(bus, frame, timeout: float, tries: int):
        """Send `frame` and wait for an ACK from its board; other traffic
        (a late answer from someone else) is skipped. Plain send/recv, so
        it works on any bus the sweep works on."""
        for _ in range(tries):
            bus.send(frame)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                got = bus.recv(timeout=left)
                if got is None:
                    break
                if got.is_ack and got.src == frame.dest:
                    return got
        return None

    # ---- plumbing ----

    def _start(self, work) -> None:
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def join(self, timeout: "float | None" = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _with_bus(self, action):
        """Run `action(bus)` on a freshly opened port; a string on failure."""
        port = self.port or self._locate()
        if not port:
            return "no serial port"
        try:
            with self._open_bus(port) as bus:
                return action(bus)
        except serial.SerialTimeoutException:
            return "port wedged (write timeout)"
        except Exception as exc:          # noqa: BLE001 - shown, not raised
            return f"ERROR {exc}"
