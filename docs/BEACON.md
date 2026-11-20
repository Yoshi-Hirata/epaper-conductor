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

*Sections 3 and 4 are the first design; section 8 (as built) overrides them
where they differ.*

**Thread.** One daemon thread; `SO_REUSEADDR`, `bind(("", 8788))`,
`settimeout(1.0)`, `recvfrom(2048)`. Every exception is caught; an `OSError`
sleeps 1 s (no busy loop). It calls the player as an HTTP handler thread does.

**Guards, in order** (each counted in `/status.beacon.dropped`):
- G1 bad length, magic, version or MAC.
- G2 cid latch: another cid is ignored unless the latched one has been silent
  for 5 s (`CID_HOLD_S`). Only datagrams carrying this unit's entry count.
  (As built: silent for 5 s or more takes over, and the latch moves only after
  G4 - section 8.)
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
- Shown (first design; the strings built are in section 8):
  `runner.emit("beacon: run (show at -8.2 s)")`, `"beacon: hold"`,
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

## 7. Amendments after the design review (2026-11-19) - these override the text above

- **A1 Ordering against HTTP by serial, not by clocks.** The Conductor sends two
  headers on every `/show/*` POST: `X-Beacon-Cid: <cid, decimal>` and
  `X-Run-Gen: <Fleet._run_gen & 0xFFFFFFFF at post time, decimal>`. The cid is
  owned by the Fleet (random, non-zero, per process) and is the one the beacon
  uses. HTTP bodies do not change. The unit records the pair of the last HTTP
  `/show/*` command that carried both headers (`agent.show_cmd_serial`).
  **G5a:** drop a beacon entry whose datagram cid equals that cid and whose gen
  is older than that gen (32-bit serial arithmetic: `(gen - http_gen) & 0xFFFFFFFF
  >= 0x80000000` means older). The time-based G5 remains only as the fallback
  while the last HTTP `/show/*` command carried no headers. A `via="beacon"`
  call never stamps `show_cmd_at` or `show_cmd_serial`.
- **A2 Stop once.** A beacon "stop" is applied at most once per (cid, gen), as
  supervision's `_stop_told` does.
- **A3 Sequence.** G3 counts only datagrams that carry this unit's entry; the
  last seq is kept per cid (a small bounded dict); the unicast copy sent to a
  unit is the datagram that holds its entry. A new cid starting at seq 1 is
  accepted once the latched cid has been silent for `CID_HOLD_S`.
- **A4 Atomic against HTTP arrivals.** One `agent.show_lock`: the HTTP handler
  takes it only to stamp `show_cmd_at` / `show_cmd_serial` (then runs the
  command outside it, as today); the beacon takes it to re-check G5/G5a and
  holds it through its call into the player. So an HTTP command that arrives
  during a beacon call lands after it, and one that was stamped first makes
  the beacon drop its entry.
- **A5 Token.** A missing token (`None`) is the empty string on both sides.
- **A6 Force.** `beacon_view` mirrors supervision's `_rejoin_force` only through
  a side-effect-free read; where that is not possible the entry carries
  `run.force or name in run.forced` and the exhibition's failed-garment rejoin
  stays an HTTP-only path (documented limit).
- **A7 One beaconing Conductor per network** (v1). Two Conductors with the
  beacon on fight through the cid latch; the default is off and only the show's
  Conductor turns it on.
- **A8 Notes.** Outside EXHIBITION mode START still refuses units that do not
  answer unless the operator leaves them out; the beacon then starts them as
  supervision's "started late" would. A beacon "stop" leaves an ENDED unit
  ENDED (as supervision does). A unit that rebooted and has not been polled
  since cannot be steered (G4) - a v1.1 item (PC-clock fields + one-way offset
  estimate on the unit).
- **Tests added:** a stale beacon (older gen) after an HTTP hold stays holding
  even when its `now` is later; a beacon of the same gen is accepted; stop is
  applied once per (cid, gen); a split datagram received out of order; a new
  cid after 5 s; the e2e 0:00 cue fires exactly once when both paths deliver
  START.

## 8. As built (2026-11-20) - this section overrides the text above

The code went further than section 7 in the review rounds (a friendly review and
two adversarial reviewers, several rounds). This is what it does now. Merged to
main as e330963 (listener on the nine Kunitachi units since 1aa943d, 2026-11-20; the
Conductor's START switch of section 9 came later, c2b08f1). Not pushed.

**Wire format: unchanged.** Header, entries, MAC, the key and the three goldens
in `tests/goldens/beacon_v1.json` are still valid on both sides.

### 8.1 Unit side (`ui/beacon.py`, `Agent.show_command`)

- **Order and commit point.** G1 (bad datagram) -> no entry for this unit
  (counted as `host`) -> G2 (cid latch) -> G3 (seq, per cid) -> G4 (`now` within
  1.0 s). Only after G4 are the cid latch, its "last heard" time, the per-cid seq
  and `gen` committed: a datagram whose clock is unusable captures nothing and
  moves no sequence. G2 lets another cid in once the latched one has been silent
  for 5 s or more (`CID_HOLD_S`), counted from the last datagram that carried
  this unit's entry. The seq memory is the last 8 cids (`SEQ_CIDS`).
- **G5a / G5.** From G4 on the unit records `agent.beacon_seen_cid`. If the last
  HTTP `/show/*` command carried a serial of the SAME cid, G5a decides by
  `(cid, gen)`: an older gen is dropped (`gen`), an equal or newer one passes. The
  time-based G5 (`entry.now <= show_cmd_at + 0.10 s`, dropped as `http`) is used
  only when that command carried no serial, or the serial of ANOTHER cid.
  A beacon call never stamps `show_cmd_at` / `show_cmd_serial`.
- **G6, G7** as in section 3 (own demo untouched, even by stop; the entry must
  name the loaded show - the LCD says "no show").
- **`decide()`.** run: in step (running or ended, `synced`, T0 within 0.10 s) =
  none; past its end = none, where the allowance past `duration` is 30 s
  (`END_SLACK`) ONLY for a unit that is running or holding - a unit that is
  neither (ended, loaded, a fresh preset) gets none, so a late datagram of a
  finished lap cannot start it again; burn "burning" = none; otherwise
  `/show/run`. hold: `/show/hold` only if running. stop: `/show/stop` only if
  running or holding (an ENDED unit stays ENDED).
- **Watermark.** An entry that needs no action still advances
  `agent.beacon_serial` (the newest `(cid, gen)` accepted, never back to an older
  gen of the same cid) when the unit already is where the entry says: "in step",
  "stop: told already", "hold" on a holding unit, "stop" on a unit that is
  neither running nor holding. An older HTTP run/hold is then late (8.2).
- **Stop once per `(cid, gen)`**, remembered for the last 8 cids, with ONE
  exception: if, after that stop was applied, an OLDER run of the same Conductor
  reached the player (an HTTP `/show/run` that was already past the agent's
  checks when the unit heard its first beacon), the stop is applied again. A unit
  started again by anything else is not stopped again by the same stop. The
  snapshot of `agent.applied_run` that decides this is taken BEFORE the player
  call, because `player.stop()` releases the player's lock during its settle wait.
- **Retry** of a refused command (`RemoteError`, e.g. burn "none", busy) is keyed
  to `(cid, gen, path)`: that command of that serial waits `RETRY_S` = 3 s; a
  refused RUN never holds a STOP back, and a new gen is asked at once.
- **STOP overtaken.** `Beacon.handle` holds `agent.show_lock` from G5 through the
  player call. If an HTTP `/show/stop` reached the player meanwhile
  (`agent.stops_seen` changed) and the beacon's own run/hold landed, the beacon
  stops the unit again.
- **LCD strings** (`runner.emit`, about 17 characters survive on the 1.3" row;
  `tests/test_ui_beacon.py` measures them against `ui/render.py`):
  applied - `beacon run, starts in 8s`, `beacon run, joined 31s`, `beacon hold`,
  `beacon stop`; skipped (once per change of reason) - `beacon skip: other PC`
  (G2), `beacon skip: clock off` (G4), `beacon skip: no show` (G7),
  `beacon skip: own demo` (G6); refused - `beacon NG: <the refusal>` (once per
  text); `beacon: STOP wins` (the stop-again above). Other drops are counted in
  `/status.beacon.dropped` (`bad cid seq now gen http demo show host`) and nothing
  is written to the LCD.

### 8.2 HTTP path (`Agent.show_command`, `via="http"`)

The Conductor sends `X-Beacon-Cid` and `X-Run-Gen` on every `/show/*` POST
(`UnitLink.post(..., serial=)`, also for supervision's and the stop tail's
corrections). The serial is bound to the command's own decision: START/HOLD/STOP
take `(beacon_cid, _run_gen)` where they decide; supervision's posts carry the
gen they decided under, not whatever `_run_gen` is when the post goes out.

- **UNTIL a beacon entry for this unit has been heard this boot**
  (`beacon_seen_cid is None`): the HTTP path is exactly the path before the
  beacon - no lock, no same-START no-op, nothing dropped. Only the stamps
  (`show_cmd_at`, `show_cmd_serial`) and the STOP count are kept, so that the
  first datagram is ordered against what HTTP already did. With the Conductor's
  switch off, or a unit out of its reach, nothing else ever changes.
- **AFTER that:**
  - `/show/run` and `/show/hold` hold `show_lock` from the stamp through the
    player call, so they cannot interleave with a beacon call.
  - A run/hold whose gen is OLDER than the newest accepted from that cid (by
    either path; only for a cid the beacon has been heard from) is answered
    without acting and without stamping; the journal says "late ...".
  - The same START - same serial, T0 within `SAME_T0_S`, the unit running that
    show from the PC - is not run twice (it would fire the 0:00 cue again).
    Another gen (a SEEK 40 ms away is one), a moved T0 or a unit that is not
    running goes to the player.
  - A run/hold that a `/show/stop` overtook while it waited for the lock is
    dropped (the journal says so).
  - `/show/stop` NEVER takes `show_lock` and always executes (straight to the
    player, stamp first, then the count). `/show/load`, `/show/preset`,
    `/show/clear` stamp and run as before.
  - The beacon stops again a unit its own run/hold landed on after a STOP (8.1).

### 8.3 Conductor side (`conductor/beacon.py`, `Fleet.beacon_view`, `server.py`)

- **Entries.** `beacon_view()` stamps `now` first and copies run/gen/state/shows
  under `_run_lock`. No entries while staging or idle. One entry per unit in
  `shows` that has an offset and a valid `/status` host (1-32 bytes UTF-8) and,
  while running, is not more than that show's duration + 30 s past. Each entry is
  validated by `_encode` before it is sent; a unit that fails is left out with the
  reason, the others still go. Two units that report the SAME `host` are both left
  out ("two units report the hostname ..."), since the unit matches by host.
  `flags` bit0 uses `_beacon_force`, a side-effect-free read of supervision's
  force decision (this closes A6 for the common case).
- **An adopted run** (the Conductor restarted while the units played) beacons only
  units whose show id is in `self.shows`; the others are left out with "its show is
  not confirmed by this Conductor since it restarted ... (Upload after STOP)".
- **Cadence** as section 4: 5 Hz in the countdown and for 20 s after a change
  (new gen, state or show-id set), else 1 Hz. The sender ticks every 0.2 s.
- **STOP tail by observed transition.** A STOP is beaconed only to units that were
  told the run: a unit is "announced" when a datagram carrying its entry was
  actually sent (a broadcast, or the unicast copy to it). So a stale STOP is never
  broadcast to a unit never told a run. The tail is 5 Hz for 5 s, then 1 Hz until
  60 s, then silence (`_STOP_FAST_S`, `_STOP_TAIL_S`); nothing is sent for a tail
  with no announced unit. A unit without a measured offset is left out
  ("clock not measured yet - needs one good /status reply first").
- **Routes** are refreshed every 10 s (`_ROUTE_REFRESH_S`) in a separate thread,
  outside the sender's lock (a DNS name that hangs never stalls the ticks). A
  refresh that finds NO route while the last one had some is believed only after
  3 in a row (`_EMPTY_REFRESHES`, about 30 s); until then the last routes keep
  sending and the error "route refresh found no route - keeping the last ones"
  is counted. A refresh that finds none and had none is the zero-route state:
  `routes: 0`, which the Units tab shows as "no route". A prefix with no local
  address is skipped and reported per unit ("this computer is not on a.b.c.0/24
  - join the show Wi-Fi"). A failed socket is rebuilt at the next refresh; send
  errors are counted (`errors`, `last_error`, `last_error_at`) and never
  propagate. Switching on resets those counters.
- **fleet.json options** (read at start; invalid values warn on the console and
  fall back safely):
  - `"beacon"` (bool, default false): the stored state of the switch. Not a
    bool -> `Warning: fleet.json: "beacon" must be true or false ...` and the
    beacon stays off.
  - `"beacon_unicast"` (bool, default true): not a bool -> warning, the unicast
    copy stays on.
  - `"beacon_to"` (list of IPv4 strings, or one string): broadcast addresses used
    instead of the units' /24 broadcasts. An entry that is not an IPv4 address is
    ignored with a warning; if none is valid (and the list was not empty) the
    /24 broadcasts are used instead, with the warning saying so. An empty list
    means no broadcast, unicast only.
- **Switch.** `POST /api/fleet/beacon {"on": bool}` (non-bool -> error; 400 when
  no sender is configured); saved to fleet.json under `workspace.set_fleet_option`
  and applied under one lock; the answer is the sender's snapshot.
- **Units tab** (`index.html`, the "Backup broadcast (beacon)" checkbox - since 2026-11-20 in THE SHOW card, in the row "If a unit's Wi-Fi is weak:" under the START buttons, no longer in MANUAL CUE): after it,
  "(on, idle)", "(sending)" or "(no route - check the Wi-Fi)"; then
  ", N send errors, last HH:MM:SS" and ", N units left out" (the reasons, and the
  last error, on hover). Nothing is shown while it is off. The checkbox is disabled
  while a switch request is in flight or no sender exists.
- **What is proven** (`tests/test_beacon_e2e.py`, two real Agents and players,
  real `BeaconSender` and listeners on 127.0.0.1): a cut unit (every HTTP
  exchange times out) follows START, HOLD, RESUME, SEEK, NEXT and STOP from the
  beacon alone, T0 within 1 ms of the Conductor's; HTTP-then-beacon and
  beacon-then-HTTP START fire the first cue once; an older beacon after an HTTP
  hold leaves the unit holding; with the switch off nothing is sent and START is
  unchanged; a unit whose clock moved since the last poll ignores the beacon; a
  unit playing its own demo ignores run, hold and stop. Real radios, the access
  point's behaviour with broadcast and a real unit's LCD were NOT proven by those
  tests; the hardware check of 2026-11-20 (section 10) covers the radio and the
  broadcast, and the LCD was read from the operator's video (rows 37-40).

### 8.4 Accepted limits

- **One beaconing Conductor per network.** Two with the switch on fight through
  the G2 latch (the unit follows whichever it heard first, and takes the other
  over only after 5 s of silence).
- **A unit that rebooted cannot be steered by the beacon** until HTTP polls have
  relearned its clock: its monotonic clock restarted, G4 drops the datagrams
  (`beacon skip: clock off`), and the Conductor replaces its stored offset only
  after TWO agreeing `/status` samples (not one poll; measured 2026-11-20, section
  10). On its own the unit restores its run from disk using its wall clock and, if
  that clock is right, lands on the right position (row 28); it is back in step
  about 7 s after HTTP returns (row 29). After a COLD power cut the unit's clock
  is lost (it came up 2 h 10 min behind, rows 41-42): it judges the saved run out
  of range, stays `loaded` and plays nothing on its own, so no wrong picture; the
  beacon cannot bring it back, HTTP does - the right position 2 s after HTTP
  returns, about 40 s after power returns. Not tested: a saved clock that happens
  to fall inside the show (O2, section 10). v1.1: PC-clock fields in the datagram and
  a one-way offset estimate on the unit.
- **First datagram after boot vs an HTTP command already in flight.** HTTP
  commands that passed the agent's `beacon_seen_cid is None` check before the
  first datagram was heard run without the lock; the window is a few statements.
  The stop-again exception (8.1) and the stamps cover the cases that matter; the
  rest is that a run racing the very first beacon may land once after it.
- **Stop-again residual (accepted by the PM, not fixed; Codex's last
  confirmation).** The stop-once exception reads `agent.applied_run` before the
  player's stop. If, on a unit's FIRST contact with the beacon, an older HTTP run
  already in flight lands between that read and the player's stop, the stop still
  stops it (correct) - but if someone then restarts that show from the unit
  itself within the 60 s STOP tail, the repeated stop stops it again, because
  that older run looks as if it came after the stop. It needs three things at
  once: the first datagram after boot, an HTTP run in flight in a window of a few
  statements, and a manual restart inside the tail; the result is a unit stopped
  while the Conductor says STOP. Closing it would need the run identity and the
  stop recorded under the player's own lock (`ui/showplay.py`), which was not
  touched before the show.
- **In step.** A new-gen move of T0 under 0.10 s (`T0_TOLERANCE_S`) is "in step"
  for the beacon: the unit is not re-timed. HTTP still applies it.
- **PRESET, load and clear are never beaconed**; Upload stays HTTP.
- **Outside EXHIBITION mode START refuses units that do not answer** (hardware
  finding F1) unless the operator has ticked "START without units that do not
  answer" (section 9, default off); the left-out unit then follows the beacon, or
  is started late by supervision when the beacon is off.
- **After a Conductor restart mid-show** (adopted run) nothing is beaconed to a
  unit until STOP and a new Upload (8.3).

## 9. START without units that do not answer (2026-11-20, c2b08f1) - Conductor side only

**Why (finding F1).** In PC-show mode START was refused for EVERY garment when
one unit was "not answering", forced or not (`Fleet._burn_problems`;
`start_present` is only set by `--adopt`). A unit whose replies do not reach the
router at START time therefore blocked the run, and the beacon never announced
it: it covered a unit that goes quiet AFTER START, not the START itself. Units
need nothing new for this.

**The switch.** Units tab, THE SHOW card, the row "If a unit's Wi-Fi is weak:" under the START buttons (next to the beacon checkbox): "START without units that do not answer",
default OFF. Saved in the workspace's fleet.json as `"start_without_silent":
true|false` (strict bool; anything else prints a warning and stays off). API
`POST /api/fleet/start_without_silent {"on": bool}`. `/api/fleet` carries
`start_without_silent`, `start_without` (the units START would leave out now)
and `start_without_why` (the reason line when it cannot help). It can be changed
only with no live run (an ENDED show is fine), not during HOLD or the preset
countdown, and not while a START is being decided ("START is being decided - try
again in a moment."). Refused on an EXHIBITION Conductor, which already starts
with the units that answer (`start_present`).

**The rule (ON).** The operator's plain START leaves out a unit whose ONLY
problem is "not answering" AND whose last known status was fully ready (same show
id, pictures burned, no force waiver). At least one garment must answer. START
still refuses exactly as before for any other fault (needs Upload, another show,
still writing, cleared, failed boards), for a unit not heard from since this
Conductor started, and when nobody answers. Not used with "Preset before START"
(a START from 0:00), not by Loop restarts (the Loop keeps its own 60 s grace).
If a left-out garment comes back twice while START is deciding, the press is
refused with "A garment came back while START was deciding - press START again."
(the `_starting` guard; the left-out unit is observed once more before the commit).

**Interaction with the beacon.**
- A left-out unit STAYS in `shows` (the run's show map), so `beacon_view()` still
  makes an entry for it and it is announced like any other unit. The run records
  it as `run.left_out` / `run.silent`; the corrections line and START note name
  it.
- With the beacon ON the unit starts by the broadcast alone (hardware: in step
  within 0.08 s). With it OFF the unit stays as it is and supervision starts it
  "late" about 1 s after its HTTP answers again.
- `flags` bit0 (the run-wide force, `_beacon_force`) is NEVER carried for a
  left-out unit: it never inherits the run-wide "start anyway". If it comes back
  with failed boards it stays out until a forced restart or a rescue Upload.
- **No HTTP "kick".** The left-out unit gets no HTTP START of its own (a build
  under review had a best-effort one; review round 3 removed it). When a unit's
  replies do not reach the Conductor the TCP handshake never completes, so the
  post cannot land in the very case the switch exists for (hardware check row 4:
  `hold -> radxa-06 timed out`, applied by the beacon). It could only reach a
  unit whose link is merely flaky - which supervision starts on its next good
  poll anyway - and with a 3 s connect plus 3 s read timeout such a post could
  arrive after the operator's STOP carrying the old T0, which a unit that has
  not heard this Conductor's beacon would execute. The beacon (on) and
  supervision's "started late" (off) cover the unit.
- While the unit is left out its entry is not dropped from the datagram, and when
  its HTTP returns the left-out line disappears without any correction (it was
  already in step - row 32).

Reviews: friendly x2, adversarial Claude x5, Codex x6. Final verdicts: merge with
the switch OFF by default - yes (all); the operator ticks it on 2026-11-22 - yes
as a fallback (Codex), ship (Claude). The operator's rule stands: make every unit
online before START; the switch is the backup.

## 10. Hardware check, 2026-11-20 (summary)

Raw record, with 42 rows, findings and observations:
`docs/BEACON_HWCHECK_20261120.md` (raw logs in `docs/hwcheck-20261120-logs/`).
Unit radxa-06 with board 3; radxa-08 with board 20 for the two-unit rows; test
Conductors on the PC at 192.168.51.40; commit 1aa943d on the units for rows 1-36.
All nine units (radxa-01..04, 06..10) are now on ab674dd (the LCD log-row fix
below); origin/main = ab674dd, pushed 2026-11-20 15:15.

**Proven on hardware.** The 0:00 picture is shown once with both paths;
START / HOLD / RESUME / SEEK / NEXT / STOP by broadcast alone (unicast off); the
full 60 s STOP tail; STOP then START at once; SEEK while holding; a real reboot
(the beacon refuses the rebooted unit - `beacon skip: clock off` - until HTTP
polls have relearned its clock; with a correct wall clock the unit restores its
run itself at the right position and is back in step 7 s after HTTP returns); a
Conductor restart mid-show; the switch toggled mid-run; two Conductors (the unit
stays with the first, `beacon skip: other PC`; takeover 5 s after the first falls
silent); a wrong show id is refused; Loop with a unit that is not heard (no lap
is started on it); START without a silent unit (section 9) with the beacon on and
off, final build included; the LCD (rows 37-40: on radxa-06's REMOTE screen all
four lines `beacon run, starts in 5s`, `beacon hold`, `beacon run, joined 23s`,
`beacon stop` appear in full, with the header ARMED / READY and `SHOW HOLDING` /
`SHOW STOPPED`); a COLD power cut mid-run, twice (rows 41-42, below).

**LCD observations.** The newest log line was clipped by the hint strip: fixed in
ab674dd (`ui/render.py`: the REMOTE screen's log rows are 14 px from y=133), on
all nine units since 15:12. The log clock is UTC. radxa-06's LCD has a dead
column (hardware).

**Observations.**
- **O1** When a run reaches a unit whose serial port is not open yet (just
  rebooted), the current picture is triggered twice about 0.4 s apart (the
  player's late-board heal; not beacon-specific). On any rejoin the current picture
  is shown and the next cue may follow 2-3 s later (a repaint takes about 7.3 s).
- **O2** The unit restores its run from disk using its wall clock. Units have an
  RTC but there is no NTP on the show Wi-Fi: on the morning of 2026-11-20 every
  boot came up at the same stale time and a rebooted unit stayed `loaded`; after
  the units reached NTP once (about 12:34 JST) a warm reboot restored the right
  position. Operating rule: battery swaps between shows, not during; a garment that
  lost power mid-show goes back on stage only after its picture is confirmed.
  **Measured answer (COLD power cut, rows 41-42, 15:17 and 15:26):** the unit's
  clock is NOT kept through a power cut (it came up 2 h 10 min behind). With its
  clock lost it judges the saved run out of range, stays `loaded` and plays
  nothing on its own - the feared wrong picture after a power cut did not happen.
  The beacon alone cannot bring it back (clock out of step, by design). With HTTP
  it is on the right position 2 s after HTTP returns, about 40 s after power
  returns (boot 35 s). A warm reboot keeps the clock (RTC powered) and the unit
  restores its run itself. Residual, not tested: a saved clock that happens to
  fall inside the show (a boot or clock save during the same show, then a power
  cut, and no HTTP).

**Operating rule from the power cut.** A garment that loses power needs about 40 s
after power returns and a working Wi-Fi link to the PC to rejoin; if its link does
not reach the PC it stays as it is until it does.

**Still open.** More than one deaf unit; a weak link (not a clean cut); a
full-length 36:34 run with the beacon on; the skip and refusal lines on the LCD
(`beacon skip: ...`, `beacon NG: ...` were not filmed). Not exercised on
hardware: `beacon skip: no show` / `own demo`, `beacon NG`, `beacon: STOP wins`.
Codex's two conditions "someone must read the LCD" and "a power cycle must be
watched through to the right picture" are met by rows 37-42.

**Decision for 2026-11-22 (Codex as test overseer).** Keep "Backup broadcast
(beacon)" OFF unless the open items are closed and a rehearsal shows a real need.
If it is ticked: one Conductor only; change it only while stopped; no Conductor
restart or battery swap mid-show; after any unit reboot verify the picture before
the garment goes back on stage.
