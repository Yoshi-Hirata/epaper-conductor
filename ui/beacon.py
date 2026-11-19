"""The one-way beacon, unit side: a signed UDP datagram that says which
run the Conductor wants (docs/BEACON.md, spec v1, 2026-11-19).

The garment units' Wi-Fi is weak in one direction: the router reaches
them, their replies often do not reach the router - and every HTTP
command needs both. The Conductor therefore also broadcasts a small
datagram several times a second; a unit that hears one brings itself to
the state it names as if the matching HTTP /show/* command had arrived.
Nothing is ever sent back. HTTP stays primary; whichever arrives first
wins, and neither may undo the other (G5 below).

docs/BEACON.md is the contract - wire format (section 2), guards and the
decide table (section 3) - and tests/goldens/beacon_v1.json holds the
byte-exact datagrams both sides are tested against. Section 7 (the
design review's amendments, same day) overrides the text above it:
ordering against HTTP by the (cid, gen) headers (G5a), stop once per
(cid, gen), the sequence per cid, and agent.show_lock. Every action goes
through Agent.show_command(..., via="beacon"): the very code an HTTP
handler runs, demo gate, id check, busy check and burn gate included.

Standard library only, Python 3.9. One daemon thread, blocking in
recvfrom() with a 1 s timeout; nothing here holds a lock the cue-firing
thread needs (ui/runner.py's _fire_at) - the player's own lock is taken
only inside ShowPlayer.status()/run()/hold()/stop(), exactly as an HTTP
handler thread takes it.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import socket
import struct
import threading
import time

from .remote import RemoteError
from .showplay import ENDED, HOLDING, LOADED, RUNNING

PORT = 8788
NOW_TOLERANCE_S = 1.0      # G4: |entry.now - our clock| beyond this is junk
CID_HOLD_S = 5.0           # G2: another Conductor waits until ours is silent
T0_TOLERANCE_S = 0.10      # "in step": the PC's own jitter, never re-timed
HTTP_MARGIN_S = 0.10       # G5: sent before the newest HTTP /show/* command
RETRY_S = 3.0              # a refused command is asked again no sooner
END_SLACK = 30             # a run more than duration + this past is over
SEQ_CIDS = 8               # G3 remembers the last seq of this many cids

MAGIC = b"EPBC"
VERSION = 1
HEADER = struct.Struct("!4sBBBBIII")    # 20 B
ENTRY = struct.Struct("!BBddd")         # 26 B, after host and show
MAC_LEN = 32
MIN_LEN = HEADER.size + MAC_LEN         # 52
MAX_LEN = 1200
NAME_MAX = 32
STATES = {1: "run", 2: "hold", 3: "stop"}

# The dropped counters, one per guard (docs/BEACON.md section 3; "gen"
# is section 7's G5a, "http" the time-based G5), plus
# "host": a good datagram with no entry for this unit - normal for a
# split datagram, but a unit whose /status "host" the Conductor has
# wrong shows it climbing while nothing is ever applied.
DROP_KEYS = ("bad", "cid", "seq", "now", "gen", "http", "demo", "show",
             "host")


def beacon_key(token: "str | None") -> bytes:
    """SHA-256(b"epaper-beacon-v1\\0" + token) - the fleet's token, the
    empty string when none is set, None included (section 7 A5; the
    HTTP API is open then, too)."""
    return hashlib.sha256(b"epaper-beacon-v1\0"
                          + (token or "").encode("utf-8")).digest()


def unpack(data: bytes, key: bytes):
    """(header, [entries]) for a datagram that passes every check of
    docs/BEACON.md section 2, else None. The MAC is checked over the
    bytes as received, before a single entry is parsed."""
    if not MIN_LEN <= len(data) <= MAX_LEN:
        return None
    magic, ver, flags, count, _rsv, cid, gen, seq = HEADER.unpack_from(data)
    if magic != MAGIC or ver != VERSION:
        return None
    body, mac = data[:-MAC_LEN], data[-MAC_LEN:]
    if not hmac.compare_digest(hmac.new(key, body, hashlib.sha256).digest(),
                               mac):
        return None
    if count < 1:
        return None
    entries = []
    at = HEADER.size
    try:
        for _ in range(count):
            names = []
            for _name in ("host", "show"):
                size = body[at]
                if not 1 <= size <= NAME_MAX or at + 1 + size > len(body):
                    return None
                names.append(body[at + 1:at + 1 + size])
                at += 1 + size
            if at + ENTRY.size > len(body):
                return None
            state, eflags, now, t0, pos = ENTRY.unpack_from(body, at)
            at += ENTRY.size
            if state not in STATES or not all(map(math.isfinite,
                                                  (now, t0, pos))):
                return None
            entries.append({"host": names[0].decode("utf-8"),
                            "show": names[1].decode("ascii"),
                            "state": state, "flags": eflags,
                            "force": bool(eflags & 1),
                            "now": now, "t0": t0, "pos": pos})
    except (IndexError, UnicodeDecodeError, struct.error):
        return None
    if at != len(body):
        return None
    return ({"ver": ver, "flags": flags, "count": count, "cid": cid,
             "gen": gen, "seq": seq}, entries)


def decide(entry: dict, status: dict, mono: float):
    """(path or None, reason): the table of docs/BEACON.md section 3.
    `status` is ShowPlayer.status() for the show the entry names (G6 and
    G7 already passed); `mono` is this unit's monotonic clock now."""
    what = STATES[entry["state"]]
    state = status.get("state")
    if what == "run":
        t0 = status.get("t0")
        if (state in (RUNNING, ENDED) and status.get("synced")
                and t0 is not None
                and abs(t0 - entry["t0"]) <= T0_TOLERANCE_S):
            return None, "in step"
        if mono - entry["t0"] > float(status.get("duration") or 0) + END_SLACK:
            return None, "run is over"
        if (status.get("burn") or {}).get("state") == "burning":
            return None, "still writing the pictures"
        return "/show/run", "run"
    if what == "hold":
        if state == RUNNING:
            return "/show/hold", "hold"
        return None, "hold: not running"
    if state in (RUNNING, HOLDING):
        return "/show/stop", "stop"
    return None, "stop: not running or holding"


class Beacon:
    def __init__(self, agent, port: int = PORT, host: str = "",
                 clock=time.monotonic):
        self.agent = agent
        self.port = port
        self.bind = host
        self._clock = clock
        self._key = beacon_key(agent.token)
        self._sock: "socket.socket | None" = None
        self._thread: "threading.Thread | None" = None
        self._quit = threading.Event()
        # Bookkeeping only - never held across a call into the player
        # (stop() waits for the card), so /status never waits on it.
        # The call itself is under agent.show_lock (section 7 A4).
        self._lock = threading.Lock()
        self._heard = None             # clock() of the last datagram past G1
        self._cid = None               # the latched Conductor (G2)
        self._cid_heard = None         # ...when it last carried our entry
        self._seqs = {}                # cid -> last seq with our entry (G3, A3)
        self._gen = None
        self._applied = None           # {"what", "gen", "at"}
        self._ignored = None           # the last reason nothing was done
        self._dropped = dict.fromkeys(DROP_KEYS, 0)
        self._retry_at = 0.0           # no command before this (RETRY_S)
        self._refusal = None           # the refusal already logged
        self._stop_told = None         # (cid, gen) a stop was applied for (A2)

    # ---- the per-datagram path (socket-free: the tests drive it) ----

    def handle(self, data: bytes) -> str:
        """Everything one datagram can cause; returns what became of it
        (a drop key, a decide reason, or the action taken)."""
        parsed = unpack(data, self._key)
        mono = self._clock()
        if parsed is None:
            return self._drop("bad", "bad datagram")
        header, entries = parsed
        with self._lock:
            self._heard = mono
        name = self.agent.name
        entry = next((e for e in entries if e["host"] == name), None)
        if entry is None:
            return self._drop("host", f"no entry for {name}")
        cid = header["cid"]
        with self._lock:
            # G2: only datagrams carrying our entry latch or refresh.
            drop = None
            if (cid != self._cid and self._cid is not None
                    and self._cid_heard is not None
                    and mono - self._cid_heard <= CID_HOLD_S):
                drop = "cid"
            # G3 (A3): per cid, and only datagrams with our entry count -
            # another unit's half of a split datagram, arriving first
            # with seq + 1, does not hide ours.
            last = self._seqs.get(cid)
            if drop is None and last is not None and header["seq"] <= last:
                drop = "seq"
            if drop is None:
                self._cid, self._cid_heard = cid, mono
                self._seqs.pop(cid, None)
                self._seqs[cid] = header["seq"]
                while len(self._seqs) > SEQ_CIDS:
                    self._seqs.pop(next(iter(self._seqs)))
                self._gen = header["gen"]
        if drop == "cid":
            return self._drop("cid", "another Conductor")
        if drop == "seq":
            return self._drop("seq", "seen already")
        # G4: a reboot on either side, a junk offset, an old capture.
        if abs(entry["now"] - mono) > NOW_TOLERANCE_S:
            return self._drop("now", "clock out of step")
        # A4: from G5 to the end of the call into the player, an HTTP
        # /show/* command cannot stamp itself in between - one that
        # arrives meanwhile lands after this call; one stamped first
        # makes G5a/G5 drop the entry here.
        with self.agent.show_lock:
            return self._act(header, entry, mono)

    def _act(self, header: dict, entry: dict, mono: float) -> str:
        cid, gen = header["cid"], header["gen"]
        serial = self.agent.show_cmd_serial
        if serial is not None:
            # G5a: the last HTTP command named this Conductor's run gen;
            # an older gen of the same cid is what that command replaced.
            if serial[0] == cid and (gen - serial[1]) & 0xFFFFFFFF >= 0x80000000:
                return self._drop("gen", "older gen than the last HTTP command")
        else:
            # G5, the fallback for an HTTP command without the headers:
            # anything sent before it loses.
            since = self.agent.show_cmd_at
            if since is not None and entry["now"] <= since + HTTP_MARGIN_S:
                return self._drop("http", "older than the last HTTP command")
        player = self.agent.player
        status = player.status() if player is not None else None
        # G6: the unit's own demo is never touched, not even by stop.
        if status is not None and status.get("demo"):
            burn = (status.get("burn") or {}).get("state")
            if (status.get("state") in (RUNNING, HOLDING)
                    or (status.get("state") == LOADED and burn == "burning")):
                return self._drop("demo", "the unit plays its own demo")
        # G7: not the show this unit holds.
        if status is None or entry["show"] != status.get("id"):
            return self._drop("show", "not the loaded show")
        path, reason = decide(entry, status, mono)
        if path == "/show/stop" and self._stop_told == (cid, gen):
            # A2: once per (cid, gen) - a unit started again by someone
            # else is not stopped again by the same stop.
            path, reason = None, "stop: told already"
        if path is None:
            with self._lock:
                self._ignored = reason
            return reason
        if mono < self._retry_at:
            with self._lock:
                self._ignored = f"{reason}: retry wait"
            return "retry wait"
        body = ({"t0": entry["t0"], "show": entry["show"],
                 "force": entry["force"]} if path == "/show/run" else {})
        try:
            self.agent.show_command(path, body, via="beacon")
        except Exception as exc:        # noqa: BLE001 - never out of the thread
            # A RemoteError is the refusal an HTTP caller would get as a
            # 409 (burn "none", busy, ...): logged once per reason, asked
            # again no sooner than RETRY_S.
            text = str(exc) if isinstance(exc, RemoteError) else \
                f"{exc.__class__.__name__}: {exc}"
            self._retry_at = mono + RETRY_S
            with self._lock:
                self._ignored = f"{reason} refused: {text}"
            if text != self._refusal:
                self._refusal = text
                self._emit(f"beacon: {reason} refused: {text}")
            return "refused"
        self._retry_at, self._refusal = 0.0, None
        if path == "/show/stop":
            self._stop_told = (cid, gen)
        with self._lock:
            self._applied = {"what": reason, "gen": gen, "at": mono}
        if reason == "run":
            self._emit(f"beacon: run (show at {mono - entry['t0']:.1f} s)")
        else:
            self._emit(f"beacon: {reason}")
        return reason

    def _drop(self, key: str, reason: str) -> str:
        with self._lock:
            self._dropped[key] += 1
            self._ignored = reason
        return key

    def _emit(self, line: str) -> None:
        try:
            self.agent.session.runner.emit(line)
        except Exception:               # noqa: BLE001 - the log is not the show
            pass

    def status(self) -> dict:
        mono = self._clock()
        with self._lock:
            applied = self._applied
            return {"on": self._thread is not None and not self._quit.is_set(),
                    "port": self.port,
                    "heard_s": (None if self._heard is None
                                else round(mono - self._heard, 1)),
                    "cid": self._cid, "gen": self._gen,
                    "applied": (None if applied is None else
                                {"what": applied["what"],
                                 "gen": applied["gen"],
                                 "ago_s": round(mono - applied["at"], 1)}),
                    "ignored": self._ignored,
                    "dropped": dict(self._dropped)}

    # ---- the socket ----

    def start(self) -> int:
        """Bind and listen in a daemon thread; returns the port bound.
        An OSError (port taken) is the caller's: ui/main.py says so and
        the unit carries on without the beacon."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind, self.port))
            sock.settimeout(1.0)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._quit.clear()
        self._thread = threading.Thread(target=self._loop, name="beacon",
                                        daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._quit.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3)     # recvfrom wakes within its 1 s timeout
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _loop(self) -> None:
        sock = self._sock
        while not self._quit.is_set():
            try:
                data, _peer = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                # A network that went away under us: wait, never spin.
                self._quit.wait(1.0)
                continue
            except Exception:           # noqa: BLE001 - the thread never dies
                self._quit.wait(1.0)
                continue
            try:
                self.handle(data)
            except Exception as exc:    # noqa: BLE001 - nor on one datagram
                with self._lock:
                    self._ignored = f"{exc.__class__.__name__}: {exc}"
