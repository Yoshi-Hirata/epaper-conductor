# One-way beacon (spec v1, 2026-11-19)

The garment units' Wi-Fi antennas are weak: the router reaches them, their
replies often do not reach the router. Every command today is an HTTP request
per unit (TCP, both directions). The beacon is a ONE-WAY backup path: the
Conductor broadcasts a small signed UDP datagram several times a second that
states the run it wants; a unit that hears it brings itself to that state as if
the matching HTTP command had arrived. No reply is needed. Upload stays HTTP
(done beforehand near the router). HTTP stays primary; whichever arrives first
wins; neither may undo the other.

This file is the contract between the unit side (`ui/beacon.py`) and the
Conductor side (`conductor/beacon.py`). The byte-exact examples are in
`tests/goldens/beacon_v1.json`; both test suites must use them.

## 0. Decisions

- **Key.** `key = SHA-256(b"epaper-beacon-v1\0" + token_utf8)`; the token is
  the fleet's existing one (fleet.json `"token"` / the unit's `--remote-token`),
  the empty string when none is set (as today: the HTTP API is then open too).
- **Ordering against HTTP** uses the `now` field: the unit records the
  monotonic arrival time of every HTTP `/show/*` command and ignores beacon
  entries sent before it. HTTP bodies are not changed.
- **Units are matched by their self-reported `host`** from `/status`
  (`ui/agent.py`), not by the fleet.json key.
- **"Stopped" is sent for 60 s.** Stop only acts on a unit that is running or
  holding the same show id.
- **PRESET, load, clear are never beaconed.**
- **Netmask is assumed /24**; fleet.json `"beacon_to": ["a.b.c.255"]` overrides.
- **Unicast copy (PM: yes, on by default).** The same datagram (same seq) is
  also sent unicast to each unit's IP; the unit drops the duplicate by seq. It
  needs only the 802.11 ACK, not an IP reply, and closes AP broadcast filtering
  and DTIM delay. fleet.json `"beacon_unicast": false` turns it off.
- **Switches.** Conductor: fleet.json `"beacon"`, default **false**, live toggle
  `POST /api/fleet/beacon {"on": bool}` (persisted). Unit: listener **on** by
  default, `--no-beacon` turns it off, `--beacon-port` moves it.

## 1. Today's flow (what the beacon mirrors)

- PC clock `pc_clock = time.perf_counter` (conductor/fleet.py); unit clock
  `time.monotonic()`. `UnitLink._learn`: `offset = unit_mono - (sent+received)/2`,
  best of the last 8 samples by round trip; a jump over 0.5 s seen twice is a
  reboot and resets the samples. Samples never age.
- START: `fleet.start_show` creates the run with `t0 = clock() + lead - at`
  (PC clock) and `_send_run` posts per unit
  `/show/run {"t0": run.t0 + offset, "show": id, "force": ...}`. T0 is second 0
  of the show in that unit's monotonic clock.
- HOLD posts `/show/hold {}`; RESUME / NEXT / MOVE change `run.t0` and re-send
  run; STOP sets `run = None`, `_stopped = True`, posts `/show/stop {}`.
- Supervision (`_supervise`, after each answered poll, one correction per unit
  per 3 s): missed STOP -> stop; demo left alone; other show id -> load;
  holding with unit running -> hold; running -> run when the unit is not
  running/ended, `t0` is None, `synced` False, or `|t0 - expected| > 0.05`; not
  when more than duration + 30 s past. `_run_gen` is bumped by `_t0_moved()` on
  every run change.
- Unit entry points (ui/agent.py do_POST `/show/*`): demo gate, then
  `ShowPlayer.run(t0, show_id=None, force=False)`, `hold()`, `stop()`;
  `ShowPlayer.status()` gives `id, state, t0, synced, demo, burn, duration`.

## 2. Wire format v1

UDP, port **8788**, big-endian, datagram at most 1200 bytes.

```
header  struct "!4sBBBBIII" (20 B)
  magic  4s  b"EPBC"
  ver    u8  1
  flags  u8  0 (reserved)
  count  u8  entries in this datagram (>= 1)
  rsv    u8  0
  cid    u32 Conductor instance id (random per process, non-zero)
  gen    u32 command serial = Fleet._run_gen & 0xFFFFFFFF
  seq    u32 datagram counter per cid, starts at 1, +1 per datagram
entry   (count times)
  host_len u8 (1..32), host  UTF-8   the unit's own /status "host"
  show_len u8 (1..32), show  ASCII   show id
  struct "!BBddd" (26 B)
    state  u8  1 running, 2 holding, 3 stopped
    eflags u8  bit0 = force; other bits 0
    now    f64 s  Conductor's estimate of this unit's time.monotonic() at send
    t0     f64 s  second 0 in the unit's monotonic clock (run.t0 + offset); 0.0 when stopped
    pos    f64 s  show position at send (negative in the countdown; the held
                  position; 0.0 stopped). Display only, never acted on
mac     32 B  HMAC-SHA256(key, every preceding byte of the datagram)
```

- Canonical form is the bytes on the wire: verify the MAC over `data[:-32]`
  before parsing entries.
- Receive order: length >= 52 and <= 1200, magic, `ver == 1` (otherwise drop
  silently), MAC, then entries.
- Reject a length that does not consume exactly `count` entries, a non-finite
  double, an unknown state.
- Overflow: pack greedily; start another self-contained datagram at 1200 B
  (same gen, next seq). A unit only needs the datagram holding its own entry.

## 3. Unit side (`ui/beacon.py`)

**Thread.** One daemon thread; `SO_REUSEADDR`, `bind(("", 8788))`,
`settimeout(1.0)`, `recvfrom(2048)`. Every exception is caught; an `OSError`
sleeps 1 s (no busy loop). It calls the player as an HTTP handler thread does.

**Guards, in order** (each counted in `/status.beacon.dropped`):
- G1 bad length, magic, version or MAC.
- G2 cid latch: another cid is ignored unless the latched one has been silent
  for 5 s (`CID_HOLD_S`). Only datagrams carrying this unit's entry count.
- G3 `seq <= last seq` for that cid (replay, reorder, the unicast duplicate).
- G4 `|entry.now - monotonic()| > 1.0 s` (`NOW_TOLERANCE_S`): a reboot on
  either side, a junk offset, an old capture.
- G5 `entry.now <= agent.show_cmd_at + 0.10 s`: sent before the newest HTTP
  `/show/*` command.
- G6 the unit plays its own demo (status.demo and running/holding, or loaded
  with burn "burning"): never touched, not even by stop.
- G7 no show loaded, or `entry.show != status.id`.

**decide(entry, status, mono) -> (path or None, reason)**

| Beacon | Unit state | Action |
|---|---|---|
| run | running or ended, `synced`, `abs(t0 - entry.t0) <= 0.10` | none (in step) |
| run | `mono - entry.t0 > duration + 30` | none (run is over) |
| run | burn "burning" | none (wait) |
| run | anything else | `/show/run {"t0", "show", "force"}` |
| hold | running | `/show/hold {}` |
| hold | other | none |
| stop | running or holding | `/show/stop {}` |
| stop | other | none |

- All calls go through `agent.show_command(path, body, via="beacon")` - the
  same code the HTTP handler runs (demo gate, id check, busy check, burn gate).
- A `RemoteError` is logged once per reason and retried no sooner than 3 s.
- Level-triggered: a repeated beacon makes no call.
- Shown: `runner.emit("beacon: run (show at -8.2 s)")`, `"beacon: hold"`,
  `"beacon: stop"`, `"beacon: run refused: <reason>"`; `/status["beacon"] =
  {"on", "port", "heard_s", "cid", "gen", "applied": {"what", "gen", "ago_s"},
  "ignored": <last reason>, "dropped": {...}}`.

## 4. Conductor side (`conductor/beacon.py`)

- `Fleet.beacon_view()` (read-only): stamps `now = self._clock()` FIRST, then
  copies `run`, `_run_gen`, `_stopped`, `_staging`, `shows` under `_run_lock`.
  State: none while staging or idle; `"stopped"` when `run is None and
  _stopped`; otherwise `run["state"]`. One entry per unit in `shows` with an
  offset and a status `host`: `now = now + offset`, `t0 = run.t0 + offset`,
  `pos = (held_at or now) - run.t0`, `force = run.force or name in run.forced`.
  Skipped with a reason: offset None, host unknown, running more than that
  show's duration + 30 s past.
- Cadence: the sender ticks every 0.2 s. 5 Hz during the countdown (`pos < 0`)
  and for 20 s after any change (new gen, state or show-id set); 1 Hz otherwise
  while running or holding. Stopped: 5 Hz for 5 s, 1 Hz until 60 s, then
  silence. Silence when idle, staging, over, or switched off.
- Destinations: every 10 s, for each distinct unit /24: the local address that
  routes to it (`socket.connect((unit_ip, 8788)); getsockname()` on a UDP
  socket - sends nothing) plus `gethostbyname_ex(gethostname())[2]`. For every
  local address in that /24: one socket bound to `(local, 0)` with
  `SO_BROADCAST`, sending to `a.b.c.255:8788`; plus the unicast copy to each
  unit IP. A prefix with no local address is skipped and reported. A unit at
  `127.x` gets unicast to `127.0.0.1:8788` (radxa-05's own agent, the tests).
- The thread never writes fleet state. Every tick is wrapped; a send error is
  counted, the socket rebuilt at the next refresh, nothing propagates.

## 5. Risks

| Risk | Closed by |
|---|---|
| Two Conductors | idle ones are silent; default off; cid latch G2 |
| Yesterday's beacon / replay | G4, G3, G7, the "over" row |
| Loop restarts | each lap is a new gen and T0; staging is silent |
| Unit rebooted | offset wrong until two polls agree -> G4 drops; the unit runs from `restore()` as today. If no poll ever returns, the beacon cannot steer that unit |
| Offset drift | beacon T0 equals HTTP T0; error = offset error at the last good poll + drift (~3 ms/min); the 0.10 s tolerance stops re-timing on jitter |
| DTIM delay | T0 is absolute; a late beacon is "started late" and catches up |
| AP isolation / broadcast filter | the unicast copy; hardware check |
| A beacon stop arms the unit's picture clear | stop needs G2-G7 and a running/holding unit |

## 6. Tests

- `tests/test_ui_beacon.py`: decode the three goldens; flipped byte, wrong key,
  truncated, ver=2 rejected; `decide` row by row; G2-G7; with a real
  ShowPlayer: run applied once, hold, resume on a moved T0, stop, demo
  untouched, burn "none" refused once and retried after 3 s; HTTP `/show/hold`
  followed by an older run datagram stays holding; socket on port 0; `stop()`
  joins.
- `tests/test_conductor_beacon.py`: `pack(golden fields) == hex`; both keys; a
  split at 1200 B; `beacon_view` for running, holding, stopped, staging, no
  offset, over, force; cadence with a fake clock and socket (off = zero sends);
  a raising socket is survived; destination selection; the server toggle.
- `tests/test_beacon_e2e.py` (after both sides land): two real Agents and
  players as in `tests/test_show_e2e.py`; Upload over HTTP, then unit B's
  `link.post` and `poll` raise; START, HOLD, RESUME, SEEK, STOP: B follows by
  UDP on 127.0.0.1 with `|B.t0 - (run.t0 + offset)| < 1 ms`.
- Hardware, one spare unit: Upload; block TCP 8787 to it; START with
  countdown, HOLD, RESUME, NEXT, MOVE, STOP - the LCD log shows `beacon:` lines;
  reboot mid-run (beacons ignored until a poll returns); switch off.
