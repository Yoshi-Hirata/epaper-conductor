# Beacon hardware check, 2026-11-20 (raw record, PM)

Status: DONE except the open items below (2026-11-20). Raw record of the check; the summary is in `docs/BEACON.md`
section 10, the START switch (rows 30-36, F1) in section 9.
Open: a COLD power cycle (battery pulled); more than one deaf
unit, a weak link and a full-length run with the beacon on. Observations O1 and O2 are at the end of the third round.
Decision (Codex as overseer): the beacon stays OFF on 2026-11-22 unless these are closed and a rehearsal shows a real need.
(The setup below is as it was in round 1; the Wavlink went from WISP to router mode before round 3.)

## Setup
- Unit: radxa-06 (192.168.51.106, commit 1aa943d, `ui.main` without `--remote-token`), board 03 (DIP 3) on /dev/ttyACM0.
- Network: Wavlink `R2.net` (WISP, uplink a phone hotspot), PC on 5 GHz ch 40 with 192.168.51.40/24.
- Conductor: test instance on the PC, `python -m conductor serve --workspace <scratch>/beacon06 --port 8770 --label BEACON-TEST`
  (PC-show mode, i.e. NOT `--adopt`). fleet.json maps radxa-06 to 192.168.51.106:8787 and every other unit to the closed
  port 127.0.0.1:9 so this Conductor never talks to them. Loop is on (workspace default), countdown 5 s.
- Show: a copy of the soak test show, one garment TEST06 -> radxa-06, duration 120 s, 9 cues at 0, 10, ..., 80 s
  (white/A/B/C), show id 5b56260216.
- "Replies cut" = on the unit `iptables -I OUTPUT -p tcp --sport 8787 ! -d 127.0.0.1 -j DROP` (the unit still RECEIVES
  everything; its HTTP answers never leave; SSH stays up and the unit's own /status is read over SSH on 127.0.0.1).
  UDP is untouched.
- Unit state is read every ~3 s: /status `show.state`, `show.now`, `beacon{}` and the log lines.

## Results, first round (PC time JST; the unit's log clock is unrelated)

| # | Step | Evidence | Verdict |
|---|------|----------|---------|
| 1 | Beacon ON, replies OK, START from 0:00 | `cue ...:2:q00 fired slot 1 +3011 ms` exactly once (logged 5 s before T0, at the START), then q01 `+1 ms` at 10 s, q02 `+2 ms`; unit `beacon.ignored = "seen already"`, `applied = null`, `dropped.seq` grows by about one per datagram; Conductor `sent=220 errors=0 routes=3` (192.168.51.255 broadcast via 192.168.51.40, unicast 192.168.51.106, plus 127.0.0.1 which is an artefact of the test fleet.json) | pass: 0:00 picture once |
| 1c | Control: beacon OFF, same START | `cue ...:3:q00 fired slot 1 +3027 ms` once - identical to #1; unit `heard_s` grows 3.2 -> 24.9 s (no datagrams); HTTP START/STOP work | pass: switch OFF = plain HTTP |
| 2 | STOP with both paths | state=stopped at the first read (1 s later), still stopped at 4, 8, 11 s | pass |
| 3 | Replies cut BEFORE START, then START | Conductor answers `400 {"error": "radxa-06: not answering"}`; with `"force": true` the same. Nothing is beaconed, the unit stays stopped | FINDING F1 |
| 4 | START normally, cut replies about 4 s into the run, HOLD | Conductor: `hold -> radxa-06 timed out`; unit log `beacon hold`, state=holding, `applied={"what":"hold","gen":6,"ago_s":3.6}` at a read 4 s after the HOLD | pass |
| 5 | RESUME (cut) | unit log `beacon run, joined 15s`; next cue `q02 fired slot 3 +2 ms` | pass |
| 6 | SEEK to 55 s while running (cut, manual) | unit log `beacon run, joined 52s`, `q05 fired slot 6 +2177 ms` (the current picture, on joining), then q06 `+1 ms`, q07 `+1 ms` | pass |
| 7 | STOP (cut) | unit log `beacon stop`, `applied={"what":"stop","gen":9,"ago_s":3.6}` at a read 4 s after the STOP; state=stopped at 7 reads over 20 s while the STOP tail is still heard (`heard_s` < 1 s) | pass |
| 8 | NEXT (cut), pressed about 4 s into a run | unit log `beacon run, joined 7s`, `q01 fired slot 2 +2 ms` 3 s later | pass (two earlier presses returned "No cue ahead to jump to (or it is already due)": the next cue was inside the 3 s lead) |
| 9 | `systemctl restart epaper-ui` on the unit mid-run, replies cut | 21 s later the unit is `state=running now=40.98`, `applied={"what":"run","gen":10,"ago_s":15.2}`; cues q03, q04, q05 at +1..2 ms | pass (a UI restart only - NOT a reboot, see open items) |
| 10 | The run reaches its end (120 s) with replies cut, then STOP | unit state=ended; the beacon STOP (gen 11) is heard, `ignored="seen already"`, state stays `ended` | by design? (section 3: STOP acts only on RUNNING/HOLDING) |
| 11 | Conductor restarted mid-run, replies cut | new Conductor: `run None, shows [], sent=0`; the unit keeps running on its own (`ignored="in step"`, `heard_s` grows) | pass |
| 12 | ...replies restored | Conductor adopts (`run running adopted`), an HTTP HOLD works; `skipped` says "its show is not confirmed by this Conductor since it restarted - the beacon skips it until then (Upload after STOP)" - for ALL ten links, including the nine this fleet never reaches | pass (the count is cosmetic) |
| 13 | STOP, Upload, START, cut replies, HOLD, STOP from the restarted Conductor (new cid 1836987485) | the unit follows: `beacon hold`, `beacon stop`; `beacon.cid` is the new one | pass |

| 14 | Set-up B (HTTP through an SSH tunnel to the unit, `"beacon_to": ["192.168.51.255"]`, `"beacon_unicast": false`, so one route: broadcast via 192.168.51.40). Upload, kill the tunnel, START within the 6 s online window | Conductor: `start -> radxa-06: connection refused`, run created, `sent=104 routes=1`; unit log `beacon run, starts in 5s`, `q00 fired slot 1 +3082 ms` once, q01 `+1 ms`, q02 `+2 ms`, q03 `+2 ms`; `applied={"what":"run","gen":1}`; `dropped.seq` does NOT grow (491 throughout: no duplicate copies, i.e. broadcast alone) | pass: beacon-only START, broadcast-only delivery |
| 15 | STOP in set-up B (HTTP refused) | unit log `beacon stop` 4 s after q03; `applied={"what":"stop","gen":2}` | pass |

Cue lateness under the beacon is the same +1..2 ms as under HTTP (this is the unit's own trigger scheduling, not
PC-to-panel sync). The `+30xx ms` of q00 is the same with the beacon off: the 0:00 cue is scheduled `refresh_s` (8 s)
before T0 and the countdown is 5 s (PM's reading, not verified against ui/runner.py).

Overseer (Codex, first pass, 2026-11-20): rows 1-13 are "covered" only for post-START control on one unit; not covered
or only partly: beacon-only START (now row 14), broadcast-only (row 14), the full 60 s STOP tail, STOP -> quick START,
a real reboot, two Conductors, Loop with a unit that is not heard, the switch toggled mid-run, SEEK while holding, the
LCD. Verdict then: leave the switch OFF for 2026-11-22 until those pass.

## Second round (after the overseer's first pass), set-up B unless said otherwise

Set-up B: the test Conductor reaches the unit's HTTP through an SSH tunnel (127.0.0.1:18787); killing the tunnel is the
"HTTP cut" (on the PC side, so it survives a unit reboot); the beacon goes by broadcast only (`"beacon_to":
["192.168.51.255"]`, `"beacon_unicast": false`). One sample line every 1-4 s with the unit's `show.state`, `show.now`,
`beacon.gen / applied / ignored / heard_s / dropped` and the Conductor's `run.state`, `sent` (with the delta) and
`announcing`. The full sample log is kept by the PM (`hw2.log`, about 300 lines).

| # | Step | Evidence | Verdict |
|---|------|----------|---------|
| 16 | The whole STOP tail: START, cut, STOP at 12:06:20, 19 samples to 12:07:35 | `applied=stop/4` once and never anything else; state=stopped in all 19 samples; no cue line after the STOP; Conductor `sent` +13 in the first 4 s, then +3..4 per 4 s, `announcing` false and `sent` frozen at 364 from 12:07:23 (62 s after the STOP); unit `heard_s` then grows 3.6 -> 15.5 s | pass |
| 17 | HTTP working + beacon on: START, 6 s, STOP, START at once | unit `running`, `gen=10`, position -4.2 -> 9.9 s over 8 samples, never stopped by the older STOP | pass (nominal; no proof that the race branch ran). A first attempt with HTTP cut is void: the second START was refused by F1 |
| 18 | Cut, HOLD, SEEK to 62 s while holding, 9 s wait, RESUME | `applied=hold/11` 3 s after the HOLD; the SEEK raises `gen` to 12 and nothing is applied (5 samples, state=holding); RESUME: `applied=run/13`, position 64.7 s 3 s later, `q07 fired slot 8 +1 ms` at 70 s, `q08 +2 ms` at 80 s | pass |
| 19 | REAL reboot mid-run (`sudo reboot`, boot id changed), HTTP cut throughout | 25 s after SSH is back: state=loaded, `ignored='clock out of step'`, `dropped.now` 3 -> 118, log `beacon skip: clock off` (once), `applied=None`; a HOLD from the PC changes nothing on the unit | pass: a rebooted unit is not steered |
| 20 | ...tunnel re-opened (run HOLDING on the PC) | within 2 s `gen=16` is accepted, `ignored='hold: not running'`, `dropped.now` stops at 118; the unit stays `loaded` while the PC holds | by design (a HOLD acts only on a running unit) |
| 21 | ...cut again, RESUME | `applied=run/17`, position 87.2 s 3 s later; log: `port /dev/ttyACM0`, `q08 fired slot 9 +4756 ms`, `panels online: 1/1`, `q08 fired slot 9 +5184 ms` (observation O1), then `beacon stop` on STOP | pass, with O1 |
| 22 | Switch toggled mid-run (cut): beacon OFF, HOLD, 8 s, beacon ON | OFF: `sent` frozen, `announcing` false, unit keeps running (4 samples, `heard_s` 1.1 -> 13.9 s); ON: `applied=hold/20` 0.5 s later, counters restart from 0; STOP: `applied=stop/21` | pass. Note: the API accepts the toggle mid-run (the docs say "only while stopped") |
| 23 | Two Conductors A (8770) and B (8771, same workspace content and folder name so the show id is the same), both cut; both START inside the online window; A beacon on. Then B HOLD + B beacon ON | unit follows A (`applied=run/22`); with B on: `ignored='another Conductor'`, `dropped.cid` 3 -> 82, log `beacon skip: other PC` (once); the unit keeps running A's show (8 samples) | pass |
| 24 | ...A beacon OFF at 12:20:38 | B's HOLD is applied at 12:20:43 (`applied=hold/2`, log `beacon hold`, `beacon.cid` = B); B STOP -> `beacon stop` | pass: takeover 5 s after the first sender falls silent |
| 25 | Wrong show: the unit holds show b8489a6430 (uploaded by another workspace) while A's STOP tail names 5b56260216 | `ignored='not the loaded show'`, `dropped.show=1`, nothing applied | pass |
| 26 | CONTROL for O1, beacon OFF: START, real reboot, tunnel re-opened after boot | the unit comes up `running` on its restored run with `now=-534.6` (observation O2); 4 s after the first good poll `now=65.1`; log `panels online: 1/1`, `q06 fired slot 7 +4628 ms` ONCE, `q07 +4 ms` | reference |

| 27 | Loop on; START from 1:30 by HTTP, cut 6 s later; the run ends at 2:00 | unit `ended` at 120 s and still `ended` 80 s later, nothing applied; Conductor `sent` +3..4 per 4 s until about 150 s (duration + 30 s), then `announcing` false and `sent` frozen, `skipped` 1; Loop: "cannot start again yet (radxa-06: not answering) - trying every 5 s", 62 s later "(no unit is ready)" | pass: no lap is started on a unit that is not heard |

Third round, 13:00-13:15, after the venue Wi-Fi was switched to router mode and after the unit's clock had been set
(the unit reached NTP once around 12:34; `timedatectl`: synchronized yes, an `rtc0` exists; before that every boot
today came up at the same stale wall time):

| # | Step | Evidence | Verdict |
|---|------|----------|---------|
| 28 | Warm reboot mid-run (boot id changed; unit wall clock right before and after: 04:08:20 / 04:09:08 UTC against the PC's 04:08:21 / 04:09:07), HTTP cut throughout | 20 s after SSH is back the unit is `running now=62.75` on its own (T0 was 13:08:26; the sample is at 13:09:28), `synced=False`; log `q06 fired slot 7 +8304 ms` (the current picture), `q07 +1 ms` 2 s later, `q08 +1 ms`; beacon `ignored='clock out of step'`, `dropped.now` growing, log `beacon skip: clock off`, nothing applied | the restore from disk lands on the right position when the wall clock is right; the beacon still refuses (monotonic offset is stale) |
| 29 | Warm reboot mid-run, HTTP path re-opened 14 s after SSH is back, PC run still RUNNING (no HOLD, no RESUME) | first good poll at 13:12:48: one sample `running now=-142.85` (`clock out of step`), Conductor correction `13:12:48 radxa-06: T0 corrected`; 2 s later `running now=59.28`, `applied=run/31`, log `beacon run, joined 58s`, `q05 fired slot 6 +7559 ms`, `q06 +1 ms` 3 s later, `q07 +1 ms`, `q08 +1 ms`; `synced=True` | pass: back in step 7 s after HTTP returns, by itself; the wrong T0 in between is in the future (nothing fires) |

Plan B ("START without units that do not answer", the fix for F1), 13:29-13:32, on the round-3 build from the review
worktree (merged to main afterwards as c2b08f1): test Conductor on 8772, two garments - TEST06 on radxa-06 (board 3, HTTP through the tunnel)
and TEST08 on radxa-08 (board 20, direct) - beacon broadcast-only, `"start_without_silent": true`:

| # | Step | Evidence | Verdict |
|---|------|----------|---------|
| 30 | Beacon ON. Upload both; kill radxa-06's tunnel; wait until it is "not answering"; START | before the press `/api/fleet` `start_without=['radxa-06']`; START answers `units: {radxa-08: ok}`, note "Started without radxa-06 (not answering)."; `run.left_out={'radxa-06': 'not answering'}`, `run.silent=['radxa-06']`; correction "START without radxa-06 (not answering)"; radxa-06 `applied=run/1` (beacon), radxa-08 started by HTTP; 11 paired samples, positions within 0.01-0.06 s of each other (sampling skew included); `q01 fired +1 ms` on both | pass |
| 31 | ...HOLD, RESUME | HOLD: radxa-08 ok by HTTP, radxa-06 "connection refused" and `applied=hold/2` by beacon, both `holding`; RESUME: both `running`, 21.05 / 21.27 s then within 0.02-0.04 s | pass |
| 32 | ...radxa-06's HTTP comes back mid-run | at the first sample `run.left_out={}` (the "Without ... in this run" line goes), no "started late" correction (it was already in step), `q05 +1 ms / +2 ms` on the two units; STOP -> both stopped | pass |
| 33 | Beacon OFF. Cut, wait, START; HTTP back 18 s later | START without radxa-06 as above; radxa-06 stays `stopped` (6 samples) while radxa-08 runs; 1 s after the tunnel returns: correction "radxa-06: started late", radxa-06 `running now=13.77` against radxa-08's 13.85; `q02 +2 ms / +1 ms` | pass |

The same two checks repeated at 14:39-14:42 on the FINAL build (after review round 5: the `_starting` guard instead of
the revision retry, the left-out unit observed once more before the commit, no HTTP "kick"):

| # | Step | Evidence | Verdict |
|---|------|----------|---------|
| 34 | Beacon ON, radxa-06 cut, the page before the press | THE SHOW panel: "Not answering: radxa-06 - if it is still not answering and its other checks pass, START can go without it; it may follow the backup broadcast, and joins fully when it answers again, if it is still ready."; checkbox ticked and enabled | pass |
| 35 | ...START; a toggle of the switch during the run; HTTP back | note "Started without radxa-06 (not answering)."; radxa-06 `applied=run/1` by beacon, 6 paired samples within 0.02-0.08 s; the toggle is refused: "Stop the show first (or let it reach its end) - this can't be changed during a show, a HOLD or the preset countdown."; `left_out` empties on the first sample after the tunnel returns | pass |
| 36 | Beacon OFF, radxa-06 cut, START, HTTP back 10 s later | radxa-06 `stopped` for 4 samples while radxa-08 runs; correction "radxa-06: started late" 1 s after the tunnel returns; then 6.12 / 6.21 s and `q01 +1 ms` on both | pass |

LCD, 14:24-14:31: the operator watched radxa-06 (and filmed the second pass) while START, HOLD, RESUME and STOP were
sent with HTTP cut; the unit's log shows `beacon run, starts in ...`, `beacon hold`, `beacon run, joined 22s` / `23s`,
`beacon stop` and the cues at +1..2 ms.

LCD read from the operator's video (PXL_20261120_052806101.mp4, 98 s, the second pass; frames taken every few seconds):

| # | On the LCD (REMOTE screen of radxa-06) | Verdict |
|---|---|---|
| 37 | START: header `ARMED`, `fires in 14 s`, `SHOW -00:03  next in 14 s`; log line `05:29:03 beacon run, starts in 5s` in full, then `05:29:03 cue 5b56260216:6:q00 fi...` | pass: the line is complete and readable |
| 38 | HOLD: header `READY`, `no fire time yet`, `SHOW HOLDING  9 cues`; log line `05:29:31 beacon hold` | pass |
| 39 | RESUME: header `ARMED`, `fires in 4 s`, `SHOW 00:26  next in 4 s`; log line `05:29:54 beacon run, joined 23s` in full | pass |
| 40 | STOP: header `READY`, `SHOW STOPPED  9 cues`; log line `05:30:16 beacon stop` | pass |

LCD observations: (L1) the NEWEST log line is clipped at its bottom by the footer bar (`KEY2 local menu  KEY3 off`)
until the next line pushes it up - ui/render.py `remote_screen` draws 6 lines of 15 px from y=134 (the sixth ends at
about y=223) and the footer starts at y=220; not beacon-specific, but the line that has just arrived is the one the
operator wants. (L2) the log's clock is the unit's UTC (05:29 for 14:29 JST). (L3) in every frame a thin dark vertical
line runs through the second character column of this unit's LCD (`f|res`, `S|HOW`, `0|5:29`) - the panel or its
cover on radxa-06, not the software. The cue lines are ellipsized (`...q00 fi...`) as designed.

Still open after the third round: the PICTURE on the glass and the LCD have not been watched by a person in any of
these; a COLD power cycle (battery pulled - the RTC may not keep time, and then the restore position is not known) has
not been done; rows 28/29 again show two triggers 2-3 s apart (the current picture on joining, then the next cue).

Observations (not beacon defects as far as the PM can tell; for the overseer):
- **O1** In row 21 the current picture was triggered twice 0.4 s apart when the run arrived on a unit whose serial port was
  not open yet (`loaded` after the reboot): fired, `panels online`, fired again. In the HTTP control (row 26) the port was
  already open and it fired once.
- **O2** After a reboot the unit restores its run from disk with a wrong position (`now=-534 s` in row 26; in row 19 it
  came up `loaded` instead). The units have no RTC and no NTP on this network, so the wall clock restarts from a saved
  value. HTTP supervision corrects it on the first good polls; a unit that HTTP cannot reach stays wrong, and the beacon
  refuses it (`clock out of step`) by design. (Correction after round 3: the units DO have an RTC; there is no NTP on the show Wi-Fi.
  Every boot that morning came up at the same stale time; after the unit reached NTP once, about 12:34 JST, a warm reboot
  restored the right position - rows 28/29. Rule: battery swaps between shows; a garment that lost power goes back on
  stage only after its picture is confirmed.)
- The unit's LCD: read from the operator's video of 2026-11-20 14:29 (rows 37-40). The lines quoted elsewhere are from the unit's log (the LCD shows the same
  lines). `--frames` writes PNGs only with the png display, so they cannot be fetched from a unit with the real LCD.

Still not tested after the third round (Loop with a unit that is not heard was done in row 27): crafted
reorder/duplicate/bad-MAC packets on hardware (unit tests and tests/test_beacon_e2e.py cover them over real UDP on the
PC); `own demo`, `beacon NG`, `STOP wins`; a unit beaconed while its pictures are being written; more than one deaf unit;
a weak link; a full-length run (36:34) with the beacon on; a cold power cycle.

## Finding F1 (operational, not a beacon bug)
In PC-show mode (the Kunitachi Conductor) START is refused when any garment of the timeline is "not answering", forced
or not (`Fleet._burn_problems`, conductor/fleet.py about line 1446; `start_present` is only set by `--adopt`,
conductor/server.py about line 4825). So a unit whose replies do not reach the router AT START TIME blocks the START for
every garment, and the beacon never announces that run. The beacon as built covers a unit that stops being heard AFTER
the START (HOLD/RESUME/SEEK/NEXT/STOP and rejoin), not the START itself.

## Open items at the end of the check
(The list written at the start of the day was mostly closed: real reboot rows 19/26/28/29, broadcast alone and
`starts in Ns` row 14, `other PC` / `clock off` rows 19/23, Loop row 27; F1 is fixed by rows 30-36.)
- Not exercised on hardware: the skip lines `no show` and `own demo`; `beacon NG: ...`; `beacon: STOP wins`.
- More than one deaf unit; a weak-signal link rather than a clean cut; a full-length (36:34) run with the beacon ON; a
  COLD power cycle (battery pulled).
- The LCD itself: done for START / HOLD / RESUME / STOP (rows 37-40); the skip and refusal lines (`beacon skip: ...`, `beacon NG: ...`) have not been filmed. The THE SHOW / START-switch texts are recorded in rows 34/35 and were read on the PC's page by the PM.
