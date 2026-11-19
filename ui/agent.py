"""The unit's remote agent: a small HTTP server for the show PC.

Runs inside the UI process, because that is where the serial port lives
(ui/remote.py explains the two-step cue). Standard library only - the
units run Python 3.9 and nothing may be added to their requirements for
a show.

    GET  /status     who am I, what am I doing, which boards answer -
                     and the clock, for the PC's offset measurement.
                     "verify" is what the unit's own landing check made
                     of the last cue it fired (ui/runner.py's
                     _verify_landing()): null until one has, then
                     {"cue", "landed", "resent", "witness"} - see
                     ui/remote.py's RemoteSession.verify
    GET  /clock      the clock alone (smallest, fastest answer)
    POST /prepare    {"cue", "label", "dev_type", "boards": {"1": hex64, ...},
                      "delays": {"1": hex128, ...},   (delays optional)
                      "span_s", "refresh_s"}          (optional: how long
                      the cue takes to finish once fired - the sweep's
                      span and the panels' refresh. Only the guard STOP
                      uses them, ui/runner.py's _guard_for(); a body
                      without them keeps the old flat guard)
    POST /fire       {"cue", "at"}       at = this unit's monotonic seconds
    POST /cancel     forget the fire time
    POST /standby    white out the panels, keep the unit under remote
    POST /release    back to the unit's own menu
    POST /bus/recover  get a serial bus that is ACCEPTING frames and
                     EXECUTING NONE working again (ui/runner.py's
                     _recover_bus(); the 2026-09-28 LOOK28 state, where
                     every write blocked 359 ms for ever and the preset
                     refreshed no panel). The cure is a USB device
                     reset of the master, PROVEN by a live board's ACK
                     to a unicast STOP and a fast STOP after a 3 s
                     pause - padding and a
                     port reopen did not cure it on the unit. The only
                     answer on this server that is not /status:
                     {"recovered": bool, "by": "usb_reset" | null,
                     "before_ms", "after_ms"} - `by` null with recovered
                     true is "there was nothing wrong", which is what a
                     healthy unit answers. ~4.5 s of work, 15 s of
                     waiting. Refused (409) while a show is running or
                     holding, while a cue is armed within the minute,
                     while the last cue's picture is still drawing ("a
                     repaint is in progress - try again in N s"), or
                     during an OTA: it reopens the port, and there is no
                     giving a cue the port back in the middle of that.
                     The worker asks all of it again when it takes the
                     job, and answers the same 409 if a gate has closed
                     /status carries the last one as `bus_recovery`

    POST /show/load    the unit's whole show file (conductor/showfile.py);
                       starts writing every cue into its own slot right
                       away (ui/remote.py's RemoteSession.burn()) - the
                       reply's status.show.burn already says "burning"
                       with the total, and /show/run refuses until it
                       reads "burned" (or "failed" with only absent
                       boards involved). status.show.burn is never null
                       for a loaded show: "burning" / "burned" / "failed"
                       / "cancelled" (STOP during the burn) / "none"
                       (nothing burned since the unit restarted) - see
                       ui/showplay.py's module docstring
    POST /show/preset  {"force"?}  put the first cue's picture up, before
                       the start
    POST /show/run     {"t0", "show", "force"?}  second 0 of the show,
                       in this unit's monotonic clock - also RESUME and
                       NEXT, which are only a moved T0 (ui/showplay.py).
                       force=true (the operator's "START anyway") passes
                       a burn that failed on a live board, and nothing
                       else - never "burning", "cancelled" or "none"
    POST /show/hold    stop scheduling; POST /show/stop ends the run
                       (and gives up on a burn still in progress)
    POST /show/clear   {"show": <id>}  take this show's pictures back OUT
                       of slots 1-18 (acked 0x14 per board and slot,
                       never 0x15), so the garment's factory autoplay has
                       nothing of the show left to cycle through once the
                       Radxa is unplugged. NOTHING is repainted: the
                       garment keeps the last look it was shown for as
                       long as it has power. Refused while a run is
                       running or holding ("stop the show first"), and
                       for a demo stored on the unit. Progress is
                       status.clear ({"state": "none" | "clearing" |
                       "cleared" | "failed" | "partial", "done", "total",
                       "failed"}); a clear that deleted anything at all
                       turns status.show.burn into "cleared", so
                       /show/run and /show/preset refuse until the
                       pictures are uploaded again. Asking twice is free.
                       An agent older than this answers 404 - the
                       conductor reads that as "too old to clear"

    POST /demo/save    {"name", "loop", "show"} write a standalone show
                       into the unit's own menu (ui/demos.py); refused
                       while a show or demo is running or holding
    GET  /demo/list    the demos stored here
    POST /demo/delete  {"slug"}
    /status gains "demos": <count>; /show/load, /show/preset and
    /show/run are refused, the same way, while a demo (not a PC-driven
    show) is running or holding - /show/hold and /show/stop still work,
    since those are how the PC takes the unit back

    /status also carries "wifi": {"ssid", "ip", "signal", "mode",
    "profile", "pending"} - which network the unit is on (ui/wifi.py),
    nulls when unknown. It is a cache the unit's own reader thread
    fills every ~10 s; nothing is asked of nmcli on this thread.
    "pending" is {"profile", "in_s"} while a deferred switch (below)
    counts down, else null

    /status also carries "beacon": the one-way UDP listener's counters
    (ui/beacon.py, docs/BEACON.md section 3), null with --no-beacon. A
    beacon acts through show_command(), the same code as POST /show/*

    POST /wifi/select  {"profile": <NetworkManager connection name>,
                        "after_s": 0-120 (default 0)}
                       switch this unit's Wi-Fi - Wifi.switch() exactly
                       as a held KEY1 on the WIFI row would, `sudo -n
                       nmcli con up <profile>` on its own thread. With
                       after_s > 0 the switch is put on a timer and the
                       answer is immediate: {"scheduled": true,
                       "after_s", "profile"} - the deferral exists
                       because the Conductor that sends it is itself
                       about to leave the network to become the
                       AZ-Epaper hotspot, and would never see a later
                       answer. A pending deferral is replaced by a new
                       one; {"cancel": true} takes it back
                       ({"cancelled": bool}). Refused 409 with the WIFI
                       row's own words ("PC show running - WIFI locked",
                       "PC show loaded - WIFI locked") exactly when its
                       held KEY1 is (App._wifi_locked) - asked again
                       when the timer fires, and a switch refused then
                       is skipped and logged, never run; 409 while a
                       switch is in flight; 404 for a profile not in
                       the unit's list; 400 for a bad body

The PC polls; the unit never calls out. A unit that walks out of Wi-Fi
range simply stops answering for a while, and nothing here minds.

Trust: the agent only changes what the panels show - no reboot, no
pull, no firmware. On the show's own router that is enough; a shared
network can set a token (--remote-token), which every request must then
carry as X-Show-Token.

Clock: time.monotonic(), stamped FIRST in status(), before anything
with a variable cost (the demo listing, the log) - so that cost cannot
leak into the reading. The PC takes the round trip's midpoint as "when
the unit said so" (the NTP idea), and the smallest round trip of a few
tries bounds the error to a few milliseconds on a quiet WLAN.
"""

from __future__ import annotations

import hmac
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .beacon import gen_older
from .config import WIFI_FIELDS, WIFI_MAX_AFTER_S
from .remote import DEV_NUMBER_BRAND, RemoteError, RemoteSession
from .showplay import HOLDING, LOADED, RUNNING

DEFAULT_PORT = 8787
MAX_BODY = 4 * 1024 * 1024      # a show: cues x boards x 2 x 128 hex chars
API_VERSION = 1
# A /show/run carrying the serial of the run already applied, with a T0
# this close to it, is the same START delivered twice (beacon and HTTP),
# not a new one (review V1, 2026-11-20: by command identity - a T0 alone
# never decides it). conductor/fleet.py's T0_TOLERANCE_S, the
# supervision's own "in step".
SAME_T0_S = 0.05


def _clock() -> dict:
    return {"mono": time.monotonic(), "wall": time.time()}


def _header_serial(headers) -> "tuple[int, int] | None":
    """(cid, gen) from X-Beacon-Cid / X-Run-Gen when both are there and
    are numbers (docs/BEACON.md section 7 A1), else None."""
    try:
        cid = (headers or {}).get("X-Beacon-Cid")
        gen = (headers or {}).get("X-Run-Gen")
        if cid is not None and gen is not None:
            return int(cid), int(gen) & 0xFFFFFFFF
    except (TypeError, ValueError):
        pass
    return None


class _Handler(BaseHTTPRequestHandler):
    agent: "Agent" = None              # bound per server in Agent.start()
    server_version = "epaper-agent"
    protocol_version = "HTTP/1.1"      # keep-alive: the PC polls every 2 s
    # One TCP segment per answer, sent at once. Unbuffered (the default),
    # the headers and the body leave as two small writes, and Nagle on
    # this side waits for the PC's delayed ACK before sending the second:
    # measured 2026-09-21 as a constant ~60 ms round trip on a 3 ms WLAN
    # - all of it on the way back, which is exactly the lopsidedness the
    # clock measurement cannot see (it cost ~30 ms of offset error).
    wbufsize = 64 * 1024
    disable_nagle_algorithm = True
    # A PC that walks out of Wi-Fi range leaves its kept-alive connection
    # half open; without a timeout its handler thread would wait for the
    # next request for ever, one leaked thread per drop. The PC polls
    # every 2 s, so 20 s of silence is a dead peer.
    timeout = 20

    def log_message(self, fmt, *args):  # the journal is for the panels
        pass

    def _answer(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self) -> bool:
        token = self.agent.token
        if not token:
            return True
        given = self.headers.get("X-Show-Token", "")
        if hmac.compare_digest(given.encode(), token.encode()):
            return True
        self._answer(401, {"error": "bad or missing X-Show-Token"})
        return False

    def do_GET(self):
        if not self._allowed():
            return
        if self.path == "/clock":
            return self._answer(200, _clock())
        if self.path == "/status":
            return self._answer(200, self.agent.status())
        if self.path == "/demo/list":
            demos = self.agent.demos
            if demos is None:
                return self._answer(404, {"error": "not found"})
            return self._answer(200, {"demos": demos.list()})
        self._answer(404, {"error": "not found"})

    def do_POST(self):
        # Take the body off the wire before answering anything, refusals
        # included: bytes left unread on a kept-alive connection are read
        # as the next request, and closing over them resets the socket
        # before the client has seen the answer.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return self._answer(413, {"error": "request too large"})
        raw = self.rfile.read(length)
        if not self._allowed():
            return
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise RemoteError("the body must be a JSON object")
            session = self.agent.session
            player = self.agent.player
            demos = self.agent.demos
            if self.path == "/wifi/select":
                # Nothing of the serial side is involved: answered
                # before the show gates below are even looked at.
                return self._wifi_select(body)
            if self.path.startswith("/demo/"):
                if demos is None:
                    raise RemoteError("this unit has no demo store")
                if self.path == "/demo/save":
                    if player is not None and player.state in (RUNNING, HOLDING):
                        raise RemoteError("a show is running - stop it first")
                    slug = demos.save(body.get("name", ""), body.get("show"),
                                      bool(body.get("loop")))
                    return self._answer(200, {"ok": True, "slug": slug,
                                              "demos": demos.list()})
                if self.path == "/demo/delete":
                    demos.delete(body.get("slug", ""))
                    return self._answer(200, {"ok": True,
                                              "demos": demos.list()})
                return self._answer(404, {"error": "not found"})
            if self.path.startswith("/show/"):
                if not self.agent.show_command(self.path, body,
                                               headers=self.headers):
                    return self._answer(404, {"error": "not found"})
            elif (player is not None and player.running
                  and self.path in ("/prepare", "/fire", "/standby")):
                raise RemoteError("a show is running - stop it first")
            elif self.path == "/prepare":
                boards = {int(address): bytes.fromhex(array)
                          for address, array in body["boards"].items()}
                session.prepare(body["cue"], boards,
                                int(body.get("dev_type", DEV_NUMBER_BRAND)),
                                str(body.get("label", ""))[:40],
                                {int(a): bytes.fromhex(t) for a, t in
                                 (body.get("delays") or {}).items()},
                                span_s=body.get("span_s"),
                                refresh_s=body.get("refresh_s"))
            elif self.path == "/fire":
                session.fire(body["cue"], float(body["at"]))
            elif self.path == "/cancel":
                session.cancel()
            elif self.path == "/standby":
                session.standby()
            elif self.path == "/bus/recover":
                # The one answer on this server that is not /status: the
                # operator's "Recover bus" wants to read what it took.
                return self._answer(200, session.recover_bus())
            elif self.path == "/release":
                session.release()
            else:
                return self._answer(404, {"error": "not found"})
        except RemoteError as exc:
            return self._answer(409, {"error": str(exc)})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            return self._answer(400, {"error": f"bad request: {exc}"})
        except Exception as exc:        # noqa: BLE001 - answer, never die
            return self._answer(500, {"error": f"{exc.__class__.__name__}: "
                                               f"{exc}"})
        self._answer(200, self.agent.status())

    def _wifi_select(self, body: dict) -> None:
        """POST /wifi/select - see the module docstring. Every refusal
        is an answer with the reason; nothing here touches nmcli on this
        thread (the switch runs on Wifi's own thread, now or later)."""
        wifi = self.agent.wifi
        if wifi is None:
            return self._answer(404, {"error": "this unit has no wifi worker"})
        if body.get("cancel"):
            cancelled = wifi.cancel_pending()
            return self._answer(200, {"cancelled": cancelled,
                                      "wifi": self.agent._wifi()})
        profile = body.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            return self._answer(400, {"error": "bad request: profile - a "
                                               "NetworkManager connection name"})
        after = body.get("after_s", 0)
        if (isinstance(after, bool) or not isinstance(after, (int, float))
                or not 0 <= float(after) <= WIFI_MAX_AFTER_S):
            return self._answer(400, {"error": f"bad request: after_s - "
                                               f"0 to {WIFI_MAX_AFTER_S:g} seconds"})
        after = float(after)
        # The same lock as the WIFI row's held KEY1 (App._wifi_locked):
        # the PC would lose this unit. Its own words ("PC show running -
        # WIFI locked" / "PC show loaded - WIFI locked"), and asked
        # again at fire time.
        note = self.agent.wifi_refusal()
        if note is not None:
            return self._answer(409, {"error": note})
        if wifi.busy:
            return self._answer(409, {"error": "a switch is in flight"})
        if profile not in wifi.names():
            if wifi.read_state == "none":
                return self._answer(409, {"error": "the wifi profiles have "
                                                   "not been read yet"})
            return self._answer(404, {"error": f"unknown profile: {profile}"})
        if after > 0:
            wifi.schedule(profile, after, allowed=self.agent.wifi_refusal)
            return self._answer(200, {"scheduled": True, "after_s": after,
                                      "profile": profile})
        wifi.cancel_pending()
        if not wifi.switch_to(profile):
            # The name is known (checked above), so False is a switch
            # that started between the busy check and this claim.
            return self._answer(409, {"error": "a switch is in flight"})
        return self._answer(200, {"scheduled": False, "profile": profile,
                                  "phase": wifi.phase,
                                  "wifi": self.agent._wifi()})


class Agent:
    def __init__(self, session: RemoteSession, port: int = DEFAULT_PORT,
                 token: "str | None" = None, host: str = "0.0.0.0",
                 commit: str = "?", name: "str | None" = None, player=None,
                 demos=None, wifi=None):
        self.session = session
        self.player = player
        self.demos = demos             # ui.demos.DemoStore, or None
        self.wifi = wifi               # ui.wifi.Wifi (its cache), or None
        self.port = port
        self.token = token or None
        self.bind = host
        self.commit = commit
        self.name = name or socket.gethostname()
        self._server: "ThreadingHTTPServer | None" = None
        self._started = time.monotonic()
        # When the last HTTP /show/* command arrived (show_command()),
        # and the Conductor's (cid, run gen) it carried in X-Beacon-Cid /
        # X-Run-Gen, None without them: the beacon drops what is older
        # (docs/BEACON.md G5a, by serial; G5, by time, only while the
        # serial is None). show_lock makes stamp and beacon call atomic
        # against each other (section 7 A4): HTTP holds it to stamp only.
        self.show_cmd_at: "float | None" = None
        self.show_cmd_serial: "tuple[int, int] | None" = None
        self.show_lock = threading.Lock()
        self.beacon = None             # ui.beacon.Beacon, or None
        # The newest (cid, gen) the beacon accepted for this unit - an
        # applied run/hold/stop, or an entry that found the unit already
        # there (set under show_lock, newer only; V4) - and the cid it
        # has heard a valid entry for this unit from, this boot (V3c).
        # Together with show_cmd_serial they are the watermark an HTTP
        # run/hold of an older gen is late against (_late()).
        self.beacon_serial: "tuple[int, int] | None" = None
        self.beacon_seen_cid: "int | None" = None
        # (cid, gen, t0) of the last /show/run with a serial the player
        # actually ran, by either path: the identity a duplicate START
        # is recognised by (V1). None after a run without a serial.
        self.applied_run: "tuple[int, int, float] | None" = None

    def status(self) -> dict:
        # Stamped first, before any of the work below - the PC's offset
        # measurement takes this as "the instant the unit's clock read
        # this", and demos.list() (disk) or a busy runner.recent() can
        # cost real, variable time; letting that land between the
        # request and the stamp would bias every reading by it. Only
        # cheap in-memory reads follow.
        clock = _clock()
        payload = self.session.status()
        payload.update({"api": API_VERSION, "host": self.name,
                        "commit": self.commit,
                        "uptime_s": round(time.monotonic() - self._started),
                        "log": self.session.runner.recent(6),
                        "show": (self.player.status() if self.player
                                 else None),
                        "demos": len(self.demos.list()) if self.demos
                        else 0,
                        "wifi": self._wifi(),
                        "beacon": self._beacon(),
                        "clock": clock})
        return payload

    def show_command(self, path: str, body: dict, via: str = "http",
                     headers=None, serial=None) -> bool:
        """One /show/* command - the HTTP handler's and the beacon's
        (ui/beacon.py) one code path: the demo gate, then the player.
        False for a path that is not a show command (the handler's
        404); a refusal raises RemoteError, a bad body KeyError /
        TypeError / ValueError, as before.

        An HTTP command records its arrival first, whatever becomes of
        it, with the (cid, gen) of `headers` when it carries both: a
        beacon datagram of an older gen (or, without them, SENT before
        it) must not undo it (docs/BEACON.md G5a/G5 and section 7,
        2026-11-19). The beacon passes its datagram's (cid, gen) as
        `serial` and holds show_lock around the whole call.

        Review rulings V1-V3 (2026-11-20):
        - /show/run and /show/hold over HTTP hold show_lock from the
          stamp through the player call, so neither can interleave with
          a beacon call; stop/load/preset/clear stamp under it and run
          outside it, as before.
        - An HTTP run/hold of an OLDER gen than this unit has accepted
          from that Conductor is late and answered without acting
          (nothing stamped) - only once the beacon has been heard from
          that cid this boot. A /show/stop is always executed.
        - A /show/run with the serial of the run already applied and the
          same T0 (SAME_T0_S), on a unit running it, is that START
          delivered twice: answered without calling player.run(), which
          would take a T0 still ahead as a fresh start and fire the 0:00
          cue a second time."""
        if via != "http":
            return self._dispatch(path, body, serial)
        serial = _header_serial(headers)
        if path in ("/show/run", "/show/hold"):
            with self.show_lock:
                newest = self._late(serial)
                if newest is not None:
                    # The journal only (the LCD has the beacon's lines).
                    print(f"late {path} (gen {serial[1]} older than "
                          f"{newest}) - ignored", flush=True)
                    return True
                self._stamp(serial)
                return self._dispatch(path, body, serial)
        with self.show_lock:
            self._stamp(serial)
        return self._dispatch(path, body, serial)

    def _late(self, serial) -> "int | None":
        """The newest gen this unit accepted from that Conductor, by
        either path, when an HTTP run/hold's gen is older than it (V3),
        else None - asked only once the beacon has been heard from that
        cid: with the beacon off, the HTTP path is exactly what it was
        before it."""
        if serial is None or self.beacon_seen_cid != serial[0]:
            return None
        newest = None
        for seen in (self.beacon_serial, self.show_cmd_serial):
            if seen is not None and seen[0] == serial[0] and (
                    newest is None or gen_older(newest, seen[1])):
                newest = seen[1]
        if newest is not None and gen_older(serial[1], newest):
            return newest
        return None

    def _stamp(self, serial) -> None:
        self.show_cmd_at = time.monotonic()
        last = self.show_cmd_serial
        # Never back to an older gen of the same Conductor: a late
        # command must not reopen G5a for what it replaced.
        if not (serial is not None and last is not None
                and serial[0] == last[0] and gen_older(serial[1], last[1])):
            self.show_cmd_serial = serial

    def _dispatch(self, path: str, body: dict, serial) -> bool:
        player = self.player
        if player is None:
            raise RemoteError("this unit has no show player")
        # A demo (never a PC-driven show, is_demo is False for
        # those) that is RUNNING/HOLDING - or still writing its
        # pictures after KEY1 (LOADED, burn "burning": review
        # finding F7) - owns the unit until the operator stops
        # it locally or the PC sends /show/stop - load/preset/run
        # must not retime or replace it under someone's feet;
        # hold/stop still work, since those are exactly how the
        # PC takes the unit back.
        if (path in ("/show/load", "/show/preset", "/show/run",
                     "/show/clear")
                and player.is_demo):
            if player.state in (RUNNING, HOLDING):
                raise RemoteError("a show is running - stop it first")
            burn = (player.status() or {}).get("burn") or {}
            if player.state == LOADED and burn.get("state") == "burning":
                raise RemoteError("a demo is writing its pictures - "
                                  "stop it first")
        if path == "/show/load":
            player.load(body)
        elif path == "/show/preset":
            player.preset(bool(body.get("force", False)))
        elif path == "/show/run":
            t0, show_id = float(body["t0"]), body.get("show")
            if self._same_run(serial, t0, show_id):
                print("show/run: already running this command - "
                      "not restarted", flush=True)
                return True                  # V1: this START, delivered again
            player.run(t0, show_id, bool(body.get("force", False)))
            self.applied_run = (None if serial is None
                                else (serial[0], serial[1], t0))
        elif path == "/show/hold":
            player.hold()
        elif path == "/show/stop":
            player.stop()
        elif path == "/show/clear":
            player.clear_pictures(body.get("show"))
        else:
            return False
        return True

    def _same_run(self, serial, t0: float, show_id: "str | None") -> bool:
        """V1, by command identity: the serial of the run the player
        already ran, its T0 within SAME_T0_S, and the player still
        RUNNING that show on that T0 from the PC. Anything else - no
        serial, another gen (a SEEK 40 ms away is a new gen), a moved
        T0, a unit not running - goes to the player."""
        applied = self.applied_run
        if (serial is None or applied is None
                or (serial[0], serial[1]) != applied[:2]
                or abs(t0 - applied[2]) > SAME_T0_S):
            return False
        status = self.player.status() or {}
        return (status.get("state") == RUNNING and bool(status.get("synced"))
                and show_id in (None, status.get("id"))
                and status.get("t0") is not None
                and abs(status["t0"] - applied[2]) <= SAME_T0_S)

    def _beacon(self) -> "dict | None":
        # In-memory counters; never an exception out of /status either.
        if self.beacon is None:
            return None
        try:
            return self.beacon.status()
        except Exception:               # noqa: BLE001 - null is an answer
            return None

    def _wifi(self) -> dict:
        # The reader's cache, copied - never a question to nmcli here,
        # and never an exception out of /status.
        if self.wifi is None:
            return dict.fromkeys(WIFI_FIELDS + ("pending",))
        try:
            return dict(self.wifi.snapshot(), pending=self.wifi.pending_info())
        except Exception:               # noqa: BLE001 - nulls are an answer
            return dict.fromkeys(WIFI_FIELDS + ("pending",))

    def wifi_refusal(self) -> "str | None":
        """Why a Wi-Fi switch must not happen right now - the WIFI row's
        own rule (App.wifi_lock_note: the PC's show running or holding,
        restored onto the garment, being written, or uploaded and
        waiting for START) - or None. ui.app is imported here, when
        asked, not at module level: it draws (PIL), this server must
        stay standard-library-light and free of that import cycle."""
        if self.player is None:
            return None
        from .app import App
        try:
            return App.wifi_lock_note(self.player)
        except Exception as exc:        # noqa: BLE001 - a doubt is a no
            return str(exc) or exc.__class__.__name__

    def start(self) -> int:
        """Serve in a daemon thread; returns the port actually bound."""
        handler = type("BoundHandler", (_Handler,), {"agent": self})
        self._server = ThreadingHTTPServer((self.bind, self.port), handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever,
                         daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
