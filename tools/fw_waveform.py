"""Read the colour waveform table of a board firmware image, and build a
trial image by transplanting single colour entries from another build.

Why (2026-11-19): the vendor's 11/9 trial builds each changed several
colours at once, and their 11/18 build (0x0A/0x0C -> pinks) also moved
the other colours' timing. "Plan B" is an image that is FW_260923 byte for
byte except the drive data of the colours we chose, so a board flashed
with it can only differ from production in those colours.

The table, as decoded from the shipped images (FW_260903 .. FW_261118):

  header   a5, BCD date yy mm dd hh mm ss, 01 00 01, 4 unknown bytes, ...,
           u16 LE total duration, 4-byte key, 0x10, then the 16-byte name
           "PDName1SMP000001"
  entries  16 of them, colour 0..15, right after the name: one count byte
           N (not masked), then N steps of 7 bytes, each XOR-masked with
           key[0:4] + key[1:4]
  step     [repeat, levelA, timeA, levelB, timeB, levelC, timeC], lasting
           repeat * (timeA + timeB + timeC)

In every shipped image the 16 entries last exactly the header's total, and
step 0 is a padding step ([1, 0, a, x, b, 0, 0] in the donors) that makes
them so. A transplanted entry therefore keeps the donor's steps, grows the
donor's step 0 level-0 time to the target total (an entry that already
lasts it is copied untouched), and is padded to the base entry's step
count by splitting a plain hold in two (same drive, same bytes count), so
nothing after it moves. The header is never touched, except its total
with --total N; then every colour must last N, so all 16 whose base entry
does not must be given by --set.

Plan C (2026-11-20): the FW_260923 program prefers a waveform saved in the
board's SPI flash to its built-in table, so a table patched into that
program may never be used (FW_261118B showed six colours on board 03, and
so did plain FW_260923); the vendor's later trial programs ignore the saved
wave. FW_261119 therefore keeps the vendor's FW_261118 program and carries
FW_260923's table (total 662) with the two 11/9 pinks.

Plan D (2026-11-20): on plan C the colours finish switching up to 2 s
apart, because each entry ends with its own level-0 rest. `retime
--end-align` moves each colour's trailing pure-rest steps to just after
the shake, so the shake stays synchronous and every colour then waits its
rest first and finishes driving at the end. Same steps, same order of the
drive, only when the rest happens.

    python tools/fw_waveform.py dump IMAGE
    python tools/fw_waveform.py transplant --base IMAGE \
        --set 10=DONOR:12 --set 12=DONOR:12 [--total N] --out OUT [--force]
    python tools/fw_waveform.py retime --base IMAGE --end-align \
        [--after-step 9] [--shake-end N] --out OUT [--force]

transplant also takes --end-align (and --after-step / --shake-end): the
retime runs on the transplanted image in the same command, both self-checks
pass before anything is written, and no intermediate file is left
(2026-11-20, the final FW_261119F image: 0x0A = pink purple, 0x0C = pink
red, as in the user's colour chart).

Since 2026-11-20 only FW/FW_260923 and FW/FW_261119F are where UPDATE FW
looks; the other builds (vendor and home-made) are kept under FW/archive/
for this tool, the tests and history. FW_261119F is the released default
(no TRIAL file), so the output guard below no longer writes there: its
NOTES reproduce it outside FW/ and compare the bytes.

Pure stdlib; no board or serial port involved.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

TABLE_NAME = b"PDName1"      # + 9 bytes: "PDName1SMP000001", "PDName1SPS005001" (FW_260903)
NAME_LEN = 16
HEADER_LEN = 34              # the a5 byte sits 34 bytes before PDName1 in every image
HEADER_BYTE = 0xA5
KEY_LEN = 4
FW_ROOT = Path(__file__).resolve().parents[1] / "FW"   # what UPDATE FW offers
TRIAL_MARKER = "TRIAL"       # ui/updater.py is_trial
COLOURS = 16
STEP_LEN = 7

Step = Tuple[int, int, int, int, int, int, int]


class WaveformError(ValueError):
    """The image does not hold a table this tool understands, or a
    transplant cannot be done without changing more than asked."""


@dataclass(frozen=True)
class Entry:
    colour: int
    start: int               # file offset of the count byte
    end: int                 # file offset just past the last step
    steps: Tuple[Step, ...]

    @property
    def duration(self) -> int:
        return sum(step_duration(s) for s in self.steps)


@dataclass(frozen=True)
class Table:
    header: int              # file offset of the a5 26 header
    name: int                # file offset of "PDName1..."
    total: int
    key: bytes
    date: str
    entries: Tuple[Entry, ...]

    @property
    def mask(self) -> bytes:
        return self.key + self.key[1:]


def step_duration(step: Sequence[int]) -> int:
    return step[0] * (step[2] + step[4] + step[6])


def parse(image: bytes) -> Table:
    """The table, its 16 entries read within the image's bounds. The
    header is found by the fixed layout (a5 34 bytes before PDName1), not
    by searching for a5 26, which would tie it to the year 2026."""
    k = image.find(TABLE_NAME)
    if k < 0 or image.find(TABLE_NAME, k + 1) != -1:
        raise WaveformError("no single PDName1 waveform table in the image")
    if k < HEADER_LEN or k + NAME_LEN > len(image):
        raise WaveformError("PDName1 table header or name cut off by the image")
    if image[k - 1] != 0x10:
        raise WaveformError(f"byte before PDName1 is 0x{image[k - 1]:02X}, not 0x10")
    header = k - HEADER_LEN
    if image[header] != HEADER_BYTE:
        raise WaveformError(f"byte 0x{header:X} is 0x{image[header]:02X}, not the a5 "
                            f"that starts the table header")
    key = image[k - 5:k - 1]
    total = int.from_bytes(image[k - 7:k - 5], "little")
    d = image[header + 1:header + 7].hex()
    date = f"20{d[0:2]}-{d[2:4]}-{d[4:6]} {d[6:8]}:{d[8:10]}:{d[10:12]}"
    mask = key + key[1:]
    entries = []
    i = k + NAME_LEN
    for colour in range(COLOURS):
        if i >= len(image):
            raise WaveformError(f"table runs off the image at colour {colour}")
        start, count = i, image[i]
        i += 1
        if i + STEP_LEN * count > len(image):
            raise WaveformError(f"colour {colour} runs off the image")
        steps = tuple(
            tuple(b ^ m for b, m in zip(image[i + STEP_LEN * n:i + STEP_LEN * (n + 1)], mask))
            for n in range(count))
        i += STEP_LEN * count
        entries.append(Entry(colour, start, i, steps))
    return Table(header, k, total, key, date, tuple(entries))


def encode(steps: Sequence[Step], key: bytes) -> bytes:
    if len(key) != KEY_LEN:
        raise WaveformError(f"key {bytes(key).hex()} is not {KEY_LEN} bytes")
    if not 0 < len(steps) <= 255:
        raise WaveformError(f"{len(steps)} steps do not fit the count byte")
    mask = key + key[1:]
    out = bytearray([len(steps)])
    for step in steps:
        if len(step) != STEP_LEN or not all(0 <= v <= 255 for v in step):
            raise WaveformError(f"step {list(step)} is not 7 bytes")
        out += bytes(v ^ m for v, m in zip(step, mask))
    return bytes(out)


def timeline(steps: Sequence[Step]) -> List[Tuple[int, int]]:
    """The drive as (level, time) runs: repeats expanded, zero-time runs
    dropped, adjacent equal levels merged - two step lists with the same
    timeline drive the panel the same way."""
    runs: List[List[int]] = []
    for step in steps:
        for _ in range(step[0]):
            for level, time in ((step[1], step[2]), (step[3], step[4]), (step[5], step[6])):
                if time == 0:
                    continue
                if runs and runs[-1][0] == level:
                    runs[-1][1] += time
                else:
                    runs.append([level, time])
    return [(level, time) for level, time in runs]


def fit_step0(steps: Sequence[Step], total: int) -> List[Step]:
    """Grow the level-0 time of the padding step 0 so the entry lasts
    `total`; refuses a step 0 that is not [1, 0, a, x, b, 0, 0], and a
    donor that lasts longer than `total` (the pad is only ever made
    longer, never eaten into). An entry that already lasts `total` is
    returned untouched, whatever its step 0 (FW_260923 has forms like
    [1, 6, 2, 0, 120, 0, 0] and [1, 0, 0, 0, 0, 6, 122])."""
    if not steps:
        raise WaveformError("donor entry has no steps")
    donor_total = sum(step_duration(s) for s in steps)
    if donor_total == total:
        return list(steps)
    if donor_total > total:
        raise WaveformError(f"donor entry lasts {donor_total}, longer than the target "
                            f"total {total}")
    s0 = steps[0]
    if not (s0[0] == 1 and s0[1] == 0 and s0[5] == 0 and s0[6] == 0):
        raise WaveformError(f"donor step 0 {list(s0)} is not of the form [1, 0, a, x, b, 0, 0]")
    pad = s0[2] + total - donor_total
    if pad > 255:
        raise WaveformError(f"step 0 level-0 time {pad} does not fit a byte")
    return [(s0[0], s0[1], pad) + tuple(s0[3:])] + list(steps[1:])


def _splittable(step: Step) -> bool:
    return step[0] == 1 and step[3:] == (0, 0, 0, 0) and step[2] >= 2 and step[2] % 2 == 0


def split_to(steps: Sequence[Step], count: int) -> List[Step]:
    """Reach `count` steps by splitting plain holds [1, L, t, 0, 0, 0, 0]
    (t even, >= 2; never step 0) into two [1, L, t/2, ...] steps - the
    same drive, one step more. Level-0 holds are split first (a rest, not
    a drive), then the latest one; refuses when the counts cannot meet."""
    out = list(steps)
    if len(out) > count:
        raise WaveformError(f"donor entry has {len(out)} steps, more than the "
                            f"base entry's {count}")
    while len(out) < count:
        candidates = [i for i in range(1, len(out)) if _splittable(out[i])]
        if not candidates:
            raise WaveformError(f"cannot reach {count} steps: no [1, L, t, 0, 0, 0, 0] "
                                f"step with an even t >= 2 left to split")
        i = max(candidates, key=lambda n: (out[n][1] == 0, n))
        half = (1, out[i][1], out[i][2] // 2, 0, 0, 0, 0)
        out[i:i + 1] = [half, half]
    return out


@dataclass(frozen=True)
class Change:
    colour: int
    donor: str
    donor_colour: int
    before: Tuple[Step, ...]     # the base entry
    donor_steps: Tuple[Step, ...]
    after: Tuple[Step, ...]
    start: int
    end: int


MAX_LEVEL = 6                # levels 0..6 in every vendor table (FW_260903 .. FW_261118)


def validate(image: bytes, label: str) -> Table:
    """parse() plus what every shipped image keeps: no step with repeat 0
    or a zero duration, no level outside 0..6, and all 16 entries lasting
    the header's total. Checked on the base and on every donor before
    fitting, so a damaged donor time cannot be absorbed by the step-0
    padding and still pass the self-check, and a crafted count cannot
    swallow the bytes after the table as zero-duration steps. A level
    changed to another valid level with the same times cannot be
    detected: nothing in the table checks it."""
    try:
        table = parse(image)
    except WaveformError as exc:
        raise WaveformError(f"{label}: {exc}") from None
    for e in table.entries:
        for n, step in enumerate(e.steps):
            if step[0] == 0 or step_duration(step) == 0:
                raise WaveformError(f"{label}: colour {e.colour} step {n} {list(step)} "
                                    f"has repeat 0 or lasts 0")
            if max(step[1], step[3], step[5]) > MAX_LEVEL:
                raise WaveformError(f"{label}: colour {e.colour} step {n} {list(step)} "
                                    f"has a level outside 0..{MAX_LEVEL}")
    for e in table.entries:
        if e.duration != table.total:
            raise WaveformError(f"{label}: colour {e.colour} lasts {e.duration}, "
                                f"its table total is {table.total}")
    return table


def total_range(table: Table) -> Tuple[int, int]:
    """File offsets [start, end) of the header's u16 total."""
    return table.name - 7, table.name - 5


def transplant(base: bytes, sets: Sequence[Tuple[int, bytes, int, str]],
               total: int | None = None) -> Tuple[bytes, List[Change]]:
    """`sets` = (base colour, donor image, donor colour, donor name).
    `total` rewrites the header total; every colour of the result must
    then last it, so the colours whose base entry does not must all be
    in `sets`. Returns the new image and what changed, after check()."""
    table = validate(base, "base")
    target_total = table.total if total is None else total
    if not 0 < target_total <= 0xFFFF:
        raise WaveformError(f"--total {target_total} does not fit the header's u16")
    out = bytearray(base)
    if total is not None:
        missing = [e.colour for e in table.entries
                   if e.duration != total and e.colour not in {c for c, *_ in sets}]
        if missing:
            raise WaveformError(f"--total {total}: colours {missing} last {table.total} in "
                                f"the base and are not given by --set")
        start, end = total_range(table)
        out[start:end] = total.to_bytes(2, "little")
    changes = []
    seen = set()
    for colour, donor, donor_colour, donor_name in sets:
        if not (0 <= colour < COLOURS and 0 <= donor_colour < COLOURS):
            raise WaveformError(f"colour {colour}/{donor_colour} is not 0..15")
        if colour in seen:
            raise WaveformError(f"colour {colour} is set twice")
        seen.add(colour)
        target = table.entries[colour]
        donor_steps = validate(donor, donor_name).entries[donor_colour].steps
        steps = split_to(fit_step0(donor_steps, target_total), len(target.steps))
        blob = encode(steps, table.key)
        if len(blob) != target.end - target.start:
            raise WaveformError(f"colour {colour} would take {len(blob)} bytes, "
                                f"not the base entry's {target.end - target.start}")
        out[target.start:target.end] = blob
        changes.append(Change(colour, donor_name, donor_colour, target.steps,
                              donor_steps, tuple(steps), target.start, target.end))
    image = bytes(out)
    check(base, image, changes, total)
    return image, changes


def check(base: bytes, image: bytes, changes: Sequence[Change],
          total: int | None = None) -> None:
    """The self-check run before anything is written: only the replaced
    entries, and with `total` the header's two total bytes, may differ."""
    if len(image) != len(base):
        raise WaveformError(f"size {len(image)} != base {len(base)}")
    old = parse(base)
    ranges = [(c.start, c.end) for c in changes]
    if total is not None:
        ranges.append(total_range(old))
    for i, (a, b) in enumerate(zip(base, image)):
        if a != b and not any(s <= i < e for s, e in ranges):
            raise WaveformError(f"byte 0x{i:X} changed outside the replaced entries"
                                + (" and the total" if total is not None else ""))
    new = parse(image)
    want = old.total if total is None else total
    if (new.header, new.name, new.total, new.key) != (old.header, old.name, want, old.key):
        raise WaveformError("table header moved or changed")
    for entry in new.entries:
        if entry.duration != new.total:
            raise WaveformError(f"colour {entry.colour} lasts {entry.duration}, "
                                f"table total is {new.total}")
        if (entry.start, entry.end) != (old.entries[entry.colour].start,
                                        old.entries[entry.colour].end):
            raise WaveformError(f"colour {entry.colour} moved")
    for c in changes:
        steps = new.entries[c.colour].steps
        if steps != c.after:
            raise WaveformError(f"colour {c.colour} does not read back as written")
        if timeline(steps[1:]) != timeline(c.donor_steps[1:]):
            raise WaveformError(f"colour {c.colour} drive after step 0 differs from the donor's")
        s0, d0 = steps[0], c.donor_steps[0]
        if s0[:2] + s0[3:] != d0[:2] + d0[3:]:
            raise WaveformError(f"colour {c.colour} step 0 differs from the donor's beyond timeA")


# ---- retime --end-align (plan D, 2026-11-20) ----

def _pairs(step: Step):
    return ((step[1], step[2]), (step[3], step[4]), (step[5], step[6]))


def is_rest(step: Step) -> bool:
    """Every (level, time) pair that takes time is level 0."""
    return all(level == 0 for level, time in _pairs(step) if time)


def rest_suffix(steps: Sequence[Step]) -> int:
    """Index where the maximal suffix of whole pure-rest steps starts
    (len(steps) when the entry ends driving)."""
    k = len(steps)
    while k > 0 and is_rest(steps[k - 1]):
        k -= 1
    return k


def drive_span(steps: Sequence[Step], start: int = 0) -> Tuple[int | None, int | None]:
    """(first tick at or after `start` where a non-zero level is driven,
    tick where the last non-zero drive ends) - ticks of 10 ms."""
    t, first, last = 0, None, None
    for step in steps:
        for _ in range(step[0]):
            for level, time in _pairs(step):
                if time and level:
                    if first is None and t >= start:
                        first = t
                    last = t + time
                t += time
    return first, last


def cumulative(steps: Sequence[Step]) -> List[int]:
    """Tick at the end of each step."""
    out, t = [], 0
    for step in steps:
        t += step_duration(step)
        out.append(t)
    return out


@dataclass(frozen=True)
class Retime:
    colour: int
    shake_end: int               # this colour's tick after --after-step
    moved: int                   # steps moved (0: entry untouched)
    rest: int                    # ticks moved
    insert_at: int               # index the moved steps now start at
    insert_tick: int             # tick they now start at
    before: Tuple[Step, ...]
    after: Tuple[Step, ...]
    start: int
    end: int


def common_shake_end(table: Table, after_step: int) -> int:
    """The most common tick at the end of step `after_step` (354 in plan
    C: 12 colours; yellow 348 mid-shake, green/turquoise/almond 426)."""
    ends = Counter(cumulative(e.steps)[after_step] for e in table.entries
                   if len(e.steps) > after_step)
    if not ends:
        raise WaveformError(f"no colour has a step {after_step}")
    (best, n), *rest = ends.most_common()
    if rest and rest[0][1] == n:
        raise WaveformError(f"no single most common shake end after step {after_step}: "
                            f"{dict(ends)}; give --shake-end")
    return best


def end_align(steps: Sequence[Step], shake_end: int) -> Tuple[List[Step], int, int]:
    """(steps, insert index, moved count): the trailing pure-rest steps
    moved, in order, to right after the first step whose end reaches
    `shake_end`. Untouched when there is no rest suffix; refuses when
    that point is at or after the suffix itself."""
    k = rest_suffix(steps)
    if k == len(steps):
        return list(steps), k, 0
    if k == 0:
        raise WaveformError("the entry is rest only")
    cum = cumulative(steps)
    j = next((i for i, t in enumerate(cum) if t >= shake_end), None)
    if j is None or j + 1 >= k:
        raise WaveformError(f"the shake end {shake_end} is reached at step {j}, at or after "
                            f"the trailing rest starting at step {k}")
    return list(steps[:j + 1]) + list(steps[k:]) + list(steps[j + 1:k]), j + 1, len(steps) - k


def retime(base: bytes, shake_end: int | None = None, after_step: int = 9
           ) -> Tuple[bytes, List[Retime], int]:
    """End-align every colour of `base`; returns (image, rows, shake end
    used), after check_retime()."""
    table = validate(base, "base")
    if shake_end is None:
        shake_end = common_shake_end(table, after_step)
    out = bytearray(base)
    rows = []
    for e in table.entries:
        steps, at, moved = end_align(e.steps, shake_end)
        blob = encode(steps, table.key)
        if len(blob) != e.end - e.start:
            raise WaveformError(f"colour {e.colour} changed size")
        out[e.start:e.end] = blob
        # This colour's own shake end: the end of its first step reaching
        # the common one (yellow 364, green/turquoise/almond 426 in plan C).
        own_end = next((t for t in cumulative(e.steps) if t >= shake_end), -1)
        rest = sum(step_duration(x) for x in e.steps[len(e.steps) - moved:]) if moved else 0
        rows.append(Retime(e.colour, own_end, moved, rest, at,
                           own_end if moved else -1, e.steps, tuple(steps), e.start, e.end))
    image = bytes(out)
    check_retime(base, image, rows, shake_end)
    return image, rows, shake_end


def check_retime(base: bytes, image: bytes, rows: Sequence[Retime], shake_end: int) -> None:
    """The self-check run before anything is written."""
    if len(image) != len(base):
        raise WaveformError(f"size {len(image)} != base {len(base)}")
    old, new = parse(base), parse(image)
    ranges = [(e.start, e.end) for e in old.entries]
    for i, (a, b) in enumerate(zip(base, image)):
        if a != b and not any(s <= i < e for s, e in ranges):
            raise WaveformError(f"byte 0x{i:X} changed outside the table entries")
    if (new.header, new.name, new.total, new.key) != (old.header, old.name, old.total, old.key):
        raise WaveformError("table header moved or changed")
    for row, o, n in zip(rows, old.entries, new.entries):
        if (n.start, n.end) != (o.start, o.end) or n.steps != row.after:
            raise WaveformError(f"colour {o.colour} does not read back as written")
        if sorted(n.steps) != sorted(o.steps) or n.duration != o.duration:
            raise WaveformError(f"colour {o.colour} is not a permutation of its steps")
        k = len(o.steps) - row.moved
        kept = n.steps[:row.insert_at] + n.steps[row.insert_at + row.moved:]
        if kept != o.steps[:k] or n.steps[row.insert_at:row.insert_at + row.moved] != o.steps[k:]:
            raise WaveformError(f"colour {o.colour} moved more than its trailing rest")
        if row.moved:
            cum = cumulative(n.steps)
            before = cum[row.insert_at - 2] if row.insert_at >= 2 else 0
            if not (cum[row.insert_at - 1] >= shake_end > before):
                raise WaveformError(f"colour {o.colour} rest is not right after the shake end")


def retime_table(rows: Sequence[Retime], after_step: int) -> List[str]:
    lines = [f"colour  shake-end  moved  rest  insert@tick  last-drive-end  "
             f"first-drive-start (after the insert point)"]
    for r in rows:
        if r.moved:
            fb, lb = drive_span(r.before, r.insert_tick)
            fa, la = drive_span(r.after, r.insert_tick)
            lines.append(f"{r.colour:2d} 0x{r.colour:02X}  {r.shake_end:9d}  {r.moved:5d}  "
                         f"{r.rest:4d}  {r.insert_at:2d}@{r.insert_tick:<8d}  {lb:4d} -> {la:<4d}"
                         f"      {fb:4d} -> {fa}")
        else:
            _, last = drive_span(r.before)
            lines.append(f"{r.colour:2d} 0x{r.colour:02X}  {r.shake_end:9d}      0     0  "
                         f"-            {last:4d} (untouched, no trailing rest)")
    return lines


def crc16_modbus(data: bytes) -> int:
    """Same CRC as host/ota.py's image fingerprint (0x26 / 0x29)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _norm(path: Path) -> Path:
    return Path(os.path.normcase(str(Path(path).resolve())))


def check_out(out: Path, sources: Sequence[Path], force: bool,
              fw_root: Path | None = None) -> None:
    """Refuse an --out that would overwrite an input (even with --force),
    an existing file without --force, or one in the repo's FW/ tree
    whose nearest FW_ folder holds no TRIAL file - ui/updater.py
    is_trial's rule: without TRIAL there, find_firmware would make an
    untested home-made image the UPDATE FW default (review 2026-11-19).
    Writing TRIAL is left to the user, on purpose."""
    for src in sources:
        same = _norm(out) == _norm(src)
        if not same and out.exists() and src.exists():
            same = os.path.samefile(out, src)
        if same:
            raise WaveformError(f"--out {out} is the same file as the input {src}")
    if out.exists() and not force:
        raise WaveformError(f"{out} exists (use --force to overwrite)")
    root = _norm(FW_ROOT if fw_root is None else fw_root)
    try:
        rel = _norm(out).relative_to(root)
    except ValueError:
        return                                   # outside FW/: allowed
    folder = None                                # nearest FW_ ancestor, as is_trial
    for depth in range(len(rel.parts) - 1, 0, -1):
        if rel.parts[depth - 1].upper().startswith("FW_"):
            folder = root.joinpath(*rel.parts[:depth])
            break
    if folder is None or not (folder / TRIAL_MARKER).is_file():
        raise WaveformError(f"{out} is in FW/ but not in a FW_ folder with a "
                            f"{TRIAL_MARKER} file - UPDATE FW would offer it as the "
                            f"default image; write the {TRIAL_MARKER} file first")


def write_atomic(out: Path, data: bytes, force: bool) -> None:
    """Temp file in the destination folder, fsync, then into place: with
    --force os.replace, without it a hard link, so a file that appeared
    after check_out() is never truncated. A volume without hard links is
    refused rather than written with a create-then-replace gap. Nothing
    is left behind on a failure."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if force:
            os.replace(tmp, out)
            return
        try:
            os.link(tmp, out)                    # FileExistsError if it appeared
        except FileExistsError:
            raise
        except OSError as exc:                   # no hard links on this volume
            raise WaveformError(f"this volume cannot create {out.name} without a race "
                                f"({exc}); write elsewhere or use --force") from None
        os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fmt(step: Sequence[int]) -> str:
    return "[" + " ".join(str(v) for v in step) + "]"


def dump(path: Path) -> str:
    image = path.read_bytes()
    t = parse(image)
    start, _ = total_range(t)
    lines = [f"{path}  {len(image)} B  crc16 0x{crc16_modbus(image):04X}",
             f"table at 0x{t.header:X}  date {t.date}  key {t.key.hex(' ')}  "
             f"total {t.total} (u16 at 0x{start:X})"]
    for e in t.entries:
        flag = "" if e.duration == t.total else "  <- != total"
        lines.append(f"colour {e.colour:2d} (0x{e.colour:02X})  steps {len(e.steps):2d}  "
                     f"total {e.duration}{flag}  @0x{e.start:X}-0x{e.end:X}")
        lines.append("    " + " ".join(_fmt(s) for s in e.steps))
    return "\n".join(lines)


def _parse_set(text: str) -> Tuple[int, Path, int]:
    """'10=path/to/donor.bin:12' (rpartition, so a Windows drive letter or
    a '(0a,0c)' in the file name is fine)."""
    colour, eq, rest = text.partition("=")
    path, colon, donor_colour = rest.rpartition(":")
    if not (eq and colon and path):
        raise argparse.ArgumentTypeError(f"--set {text!r} is not COLOUR=DONOR:COLOUR")
    try:
        return int(colour, 0), Path(path), int(donor_colour, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--set {text!r}: colours are numbers") from None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="print the waveform table")
    d.add_argument("image", type=Path)
    t = sub.add_parser("transplant", help="copy colour entries from donor images")
    t.add_argument("--base", type=Path, required=True)
    t.add_argument("--set", dest="sets", type=_parse_set, action="append", required=True,
                   metavar="COLOUR=DONOR:COLOUR")
    t.add_argument("--total", type=int, default=None,
                   help="rewrite the header total; every colour must then last it")
    t.add_argument("--end-align", action="store_true",
                   help="then retime --end-align the result, in the same command")
    t.add_argument("--after-step", type=int, default=9)
    t.add_argument("--shake-end", type=int, default=None)
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--force", action="store_true", help="overwrite --out")
    r = sub.add_parser("retime", help="move each colour's trailing rest after the shake")
    r.add_argument("--base", type=Path, required=True)
    r.add_argument("--end-align", action="store_true", required=True)
    r.add_argument("--after-step", type=int, default=9,
                   help="step that ends the common shake (default 9)")
    r.add_argument("--shake-end", type=int, default=None,
                   help="tick of the shake end (default: the most common after --after-step)")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--force", action="store_true", help="overwrite --out")
    args = ap.parse_args(argv)
    if args.cmd == "retime":
        return _main_retime(args)
    try:
        if args.cmd == "dump":
            print(dump(args.image))
            return 0
        check_out(args.out, [args.base] + [p for _, p, _ in args.sets], args.force)
        base = args.base.read_bytes()
        image, changes = transplant(
            base, [(c, p.read_bytes(), dc, p.name) for c, p, dc in args.sets], args.total)
        aligned = (retime(image, args.shake_end, args.after_step)
                   if args.end_align else None)
        write_atomic(args.out, aligned[0] if aligned else image, args.force)
    except (WaveformError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    old = parse(base)
    print(f"base  {args.base}  {len(base)} B  crc16 0x{crc16_modbus(base):04X}  total {old.total}")
    if args.total is not None:
        start, end = total_range(old)
        print(f"header total {old.total} -> {args.total}  bytes 0x{start:X}-0x{end - 1:X}")
    for c in sorted(changes, key=lambda c: c.colour):
        print(f"colour {c.colour} (0x{c.colour:02X}) <- {c.donor} colour {c.donor_colour}"
              f"  bytes 0x{c.start:X}-0x{c.end - 1:X} ({c.end - c.start} B)"
              + ("  copied as is" if c.after == c.donor_steps else ""))
        rows = (("before", c.before), ("donor", c.donor_steps), ("after", c.after))
        for label, steps in rows if c.after != c.donor_steps else rows[::2]:
            dur = sum(step_duration(s) for s in steps)
            print(f"  {label:6s} {len(steps):2d} steps  total {dur:4d}  "
                  + " ".join(_fmt(s) for s in steps))
    if aligned:
        image, rows, shake_end = aligned
        print("then retime --end-align:")
        _print_retime(rows, shake_end, args)
    print(f"wrote {args.out}  {len(image)} B  crc16 0x{crc16_modbus(image):04X}  (self-check passed)")
    return 0


def _print_retime(rows: Sequence[Retime], shake_end: int, args) -> None:
    print(f"shake end {shake_end} (tick after step {args.after_step}"
          + (", most common)" if args.shake_end is None else ", given)"))
    odd = [f"{r.colour}: {r.shake_end}" for r in rows if r.shake_end != shake_end]
    if odd:
        print("colours whose own shake ends later: " + ", ".join(odd))
    for line in retime_table(rows, args.after_step):
        print(line)


def _main_retime(args) -> int:
    try:
        check_out(args.out, [args.base], args.force)
        base = args.base.read_bytes()
        image, rows, shake_end = retime(base, args.shake_end, args.after_step)
        write_atomic(args.out, image, args.force)
    except (WaveformError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"base  {args.base}  {len(base)} B  crc16 0x{crc16_modbus(base):04X}  "
          f"total {parse(base).total}")
    _print_retime(rows, shake_end, args)
    print(f"wrote {args.out}  {len(image)} B  crc16 0x{crc16_modbus(image):04X}  (self-check passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
