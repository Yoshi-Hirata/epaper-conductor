"""In what order the scales of a garment change.

A board refreshes its sockets P01 -> P60 about 0.1 s apart, in an order
the wiring decided. With the per-segment delay table of the firmware
request (docs/FW_REQUEST_SEGMENT_DELAY.md), which V1.4 (FW_260923)
shipped as 0x1F/0x25, the host can say instead when each socket starts,
so the change sweeps the garment in a direction:

    natural      as the wiring has it (no table)
    center       outward from the middle of the garment
    top_down     row by row from the top
    bottom_up    row by row from the hem
    left_right   column by column from the audience's left
    right_left   column by column from the audience's right

Every scale gets a rank (0, 1, 2 ...); its delay is proportional to that
rank, scaled so the last rank's delay is exactly the sweep's span (the
seconds from the first scale's command to the last one's). Rows and
columns are the grid's own (one row = one step, one column = one step),
which is what the designer sees in the CSV. Left and right are the
audience's, for each side as they face it: the CSV is drawn from the
inside, and the site's columns run towards the wearer's right on both
sides, so the front is mirrored and the back is not. The centre is the
front's centroid; the back uses the point straight behind it.

A sweep may also have an EDGE (the operator, 2026-10-22: the border of a
swept change should not advance as a straight line): `straight` (the
ranks above, unchanged), `wave`, `fray` or `wave_fray` move each scale's
rank by up to `edge_rows` grid steps - one fixed width, or with
`edge_random` a width that itself varies between `edge_min_rows` and
`edge_rows` from place to place - drawn from `edge_seed`, so the shape
is the same in the Conductor's preview, on the units, in the 3D export
and in the designers' simulator, every loop. edge_ranks() below is the
reference; conductor/web/index.html and conductor/web/sim/ port it bit
for bit (tools/make_goldens.py's "edge_ranks" cases check). The ranks
become floats, the delay formula does not change, and the last scale
still lands exactly on the span: the edge is inside the span, never
added to it. The units need nothing new - they already take per-socket
start delays.
"""

from __future__ import annotations

import math
import struct

from .look import ARRAY_LEN, LookMap

SEQUENCES = ("natural", "center", "top_down", "bottom_up",
             "left_right", "right_left")
LABELS = {"natural": "Socket order (P01 to P60)",
          "center": "Centre outward",
          "top_down": "Top to bottom",
          "bottom_up": "Bottom to top",
          "left_right": "Left to right (audience)",
          "right_left": "Right to left (audience)"}
FRAME_S = 0.01                  # V1.4's delay table unit (7.4.2)
NO_DELAY = 0xFFFF               # a socket without a scale; the runner sends 0
TABLE_LEN = 128                 # 64 sockets x uint16, big-endian
MAX_DELAY_S = 30.0              # firmware guidance -> validate() problem
SPAN_HARD_MAX_S = 120.0         # clean_span()'s clamp, so the file stays sane

# The sweep's edge (module doc). Absent fields are these defaults, and a
# cue or a design transition whose five fields are all at their defaults
# stores none of them (edge_stored()), so every show made before the edge
# existed keeps its show.json, its revision and its unit show ids.
EDGES = ("straight", "wave", "fray", "wave_fray")
EDGE_LABELS = {"straight": "Straight", "wave": "Wave", "fray": "Fray",
               "wave_fray": "Wave + fray"}
EDGE_DEFAULTS = {"edge": "straight", "edge_rows": 6.0, "edge_random": False,
                 "edge_min_rows": 1.0, "edge_seed": 1}
EDGE_FIELDS = tuple(EDGE_DEFAULTS)
EDGE_ROWS_MAX = 24.0
EDGE_SEED_MAX = 999999
_MASK = 0xFFFFFFFF


def clean_sequence(value) -> str:
    return value if value in SEQUENCES else "natural"


def clean_span(value) -> float:
    try:
        span = round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
    if span != span:                     # NaN: junk, same as an unusable value
        return 0.0
    return min(SPAN_HARD_MAX_S, max(0.0, span))


def _clean_rows(value, default: float, high: float) -> float:
    try:
        rows = round(float(value), 1)
    except (TypeError, ValueError, OverflowError):
        return default
    if rows != rows:                     # NaN
        return default
    return min(high, max(0.0, rows))


def _clean_seed(value) -> int:
    try:
        seed = int(float(value))
    except (TypeError, ValueError, OverflowError):   # junk, NaN, inf
        return EDGE_DEFAULTS["edge_seed"]
    return min(EDGE_SEED_MAX, max(0, seed))


def clean_edge(entry) -> dict:
    """The five edge fields of a cue or a design transition, tidied: an
    unknown edge is straight, the widths are one-decimal grid steps
    (0-24, the minimum never above the maximum), the seed 0-999999.
    Anything missing is its default."""
    entry = entry if isinstance(entry, dict) else {}
    edge = entry.get("edge", "straight")
    rows = _clean_rows(entry.get("edge_rows", EDGE_DEFAULTS["edge_rows"]),
                       EDGE_DEFAULTS["edge_rows"], EDGE_ROWS_MAX)
    return {"edge": edge if isinstance(edge, str) and edge in EDGES
                    else "straight",
            "edge_rows": rows,
            "edge_random": bool(entry.get("edge_random", False)),
            "edge_min_rows": _clean_rows(
                entry.get("edge_min_rows", EDGE_DEFAULTS["edge_min_rows"]),
                min(rows, EDGE_DEFAULTS["edge_min_rows"]), rows),
            "edge_seed": _clean_seed(entry.get("edge_seed",
                                               EDGE_DEFAULTS["edge_seed"]))}


def edge_stored(entry) -> dict:
    """What show.json keeps of an edge: nothing when all five fields are
    at their defaults (a show without edges stays byte-identical, ids and
    "changed since" marks included), else all five, cleaned - so switching
    the edge back to straight keeps the width and seed the operator chose,
    the way a cue keeps its sequence and span when it goes back to its
    design's transition."""
    edge = clean_edge(entry)
    if all(edge[k] == v for k, v in EDGE_DEFAULTS.items()):
        return {}
    return edge


def rank_set(sweep) -> str:
    """The key of a sweep's ranks in the page state's item.sweep_ranks:
    "" when the edge changes nothing (straight, natural, or a span of 0),
    so the page keeps using item.sequences[sequence] as before. Otherwise
    it names the sequence and all five fields - the page keys its preview
    on it, so it changes whenever any of them does (even the minimum of a
    fixed width, which moves no rank) - and nothing else (not the span)."""
    sweep = sweep if isinstance(sweep, dict) else {}
    seq = clean_sequence(sweep.get("sequence", "natural"))
    edge = clean_edge(sweep)
    if (seq == "natural" or edge["edge"] == "straight"
            or clean_span(sweep.get("span_s", 0.0)) <= 0):
        return ""
    return (f"{seq}/{edge['edge']}/{edge['edge_rows']:g}/"
            f"{'random' if edge['edge_random'] else 'fixed'}/"
            f"{edge['edge_min_rows']:g}/{edge['edge_seed']}")


def _imul(a: int, b: int) -> int:
    return ((a & _MASK) * (b & _MASK)) & _MASK


def hash32(a: int, b: int) -> int:
    """flicker.js hash32(), bit for bit (Math.imul and >>> on uint32).
    The one implementation: conductor/export3d.py's flicker uses it too."""
    h = _imul(a ^ 0x9e3779b1, 0x85ebca6b) ^ _imul(b ^ 0x27d4eb2f, 0xc2b2ae35)
    h = _imul(h ^ (h >> 15), 0x2c1b3c6d)
    h = _imul(h ^ (h >> 12), 0x297a2d39)
    return (h ^ (h >> 15)) & _MASK


def h01(a: int, b: int) -> float:
    """hash32 as a number in [0, 1); inputs masked to uint32 first (JS
    `>>> 0`), so a negative or large argument hashes the same both sides."""
    return hash32(a & _MASK, b & _MASK) / 4294967296.0


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def vnoise(x: float, y: float, k: int) -> float:
    """Value noise in [0, 1]: h01 on the integer lattice, smoothstepped
    between. `k` picks an independent field (the seed, the side)."""
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = x - xi, y - yi

    def q(a: int, b: int) -> float:
        return h01(a + k * 7919, b - k * 104729)

    a, b = q(xi, yi), q(xi + 1, yi)
    c, d = q(xi, yi + 1), q(xi + 1, yi + 1)
    sx, sy = _smooth(xf), _smooth(yf)
    top = a + (b - a) * sx
    bot = c + (d - c) * sx
    return top + (bot - top) * sy


def edge_ranks(look_map: LookMap, sweep: dict) -> "dict[tuple, float]":
    """position -> rank for a sweep WITH its edge (module doc): the raw
    row/column/centre rank plus an offset of up to the edge's width, then
    rebased so the lowest is 0 as ranks() does. A straight edge or a
    natural sequence is ranks() itself, integers and all.

    The reference algorithm of the spec (2026-10-22): the page and the
    designers' simulator compute the same numbers in JS, so the order of
    every operation below matters - do not "simplify" it."""
    seq = clean_sequence(sweep.get("sequence", "natural"))
    e = clean_edge(sweep)
    edge = e["edge"]
    if edge == "straight" or seq == "natural":
        return ranks(look_map, seq)
    raw_rank = _raw_ranks(look_map, seq)
    vertical = seq in ("top_down", "bottom_up", "center")
    out = {}
    for s in look_map.scales:
        across = s.col if vertical else s.row
        along = s.row if vertical else s.col
        k = e["edge_seed"] + (1000 if s.side == "back" else 0)
        if e["edge_random"]:
            lo, hi = e["edge_min_rows"], e["edge_rows"]
            g = (vnoise(across * 0.09, along * 0.06, k + 50) - 0.15) / 0.7
            width = lo + (hi - lo) * min(1.0, max(0.0, g))
        else:
            width = e["edge_rows"]
        wave = vnoise(across * 0.16, along * 0.07, k) * 2 - 1
        fray = h01(across * 7 + k, along * 13 + 5 * k) * 2 - 1
        if edge == "wave":
            offset = width * wave
        elif edge == "fray":
            offset = width * fray
        else:                                           # wave_fray
            offset = width * wave + 0.5 * width * fray
        out[s.position] = raw_rank[s.position] + offset
    if not out:
        return out
    low = min(out.values())
    return {p: r - low for p, r in out.items()}


def _ranks_for(look_map: LookMap, sequence: str, edge) -> dict:
    """ranks() for a sweep without an edge (or a straight one) - today's
    integers, so every existing table is byte-identical - else
    edge_ranks()."""
    if (edge is None or sequence == "natural"
            or clean_edge(edge)["edge"] == "straight"):
        return ranks(look_map, sequence)
    return edge_ranks(look_map, dict(edge, sequence=sequence))


def ranks(look_map: LookMap, sequence: str) -> "dict[tuple, int]":
    """position -> rank (0 first) for every scale of the map.

    Rebased, so the lowest rank of a garment is always 0 and the sweep
    gets its whole span. The row and column sequences already count
    from their own extreme scale, so this changes nothing for them;
    `center` measures a distance from a centroid that on a real map
    falls BETWEEN scales, so its lowest rank is normally 1, not 0
    (radxa-01, 2026-09-26: the first scale of a centre-outward sweep
    started at span/max_rank - +0.19 s of a 3 s span - instead of with
    the broadcast, and the sweep only ever used the rest of its span).
    """
    return _rebased(_raw_ranks(look_map, sequence))


def _rebased(result: "dict[tuple, int]") -> "dict[tuple, int]":
    lowest = min(result.values(), default=0)
    if lowest == 0:
        return result
    return {position: rank - lowest for position, rank in result.items()}


def _raw_ranks(look_map: LookMap, sequence: str) -> "dict[tuple, int]":
    scales = look_map.scales
    if sequence == "natural" or not scales:
        return {s.position: 0 for s in scales}
    rows = [s.row for s in scales]
    if sequence == "top_down":
        top = max(rows)
        return {s.position: top - s.row for s in scales}
    if sequence == "bottom_up":
        hem = min(rows)
        return {s.position: s.row - hem for s in scales}
    if sequence in ("left_right", "right_left"):
        # Audience's left: for the front the wearer's right (the last
        # column), for the back the wearer's left (the first column).
        result = {}
        for side in look_map.sides:
            cols = [s.col for s in scales if s.side == side]
            first, last = min(cols), max(cols)
            from_wearers_right = (side == "front") == (sequence == "left_right")
            for s in scales:
                if s.side == side:
                    result[s.position] = (last - s.col if from_wearers_right
                                          else s.col - first)
        return result
    if sequence == "center":
        front = [s for s in scales if s.side == "front"] or scales
        xs = [s.col + look_map.shift(s.side, s.row) for s in front]
        ys = [s.row for s in front]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        return {s.position: int(round(math.hypot(
            s.col + look_map.shift(s.side, s.row) - cx, s.row - cy))) for s in scales}
    raise ValueError(f"unknown sequence {sequence!r}")


def span_s(look_map: LookMap, sequence: str, span: float,
           edge: "dict | None" = None) -> float:
    """The sweep's effective span: 0 for a natural sequence or a garment
    whose ranks never move (one scale, or all of a kind) - there is
    nothing to time. Otherwise `span` itself: the caller already knows
    the seconds wanted from the first scale to the last. `edge` is the
    sweep's edge fields (a resolved cue["sweep"] will do): an edge can
    give even a one-row garment a top-down sweep to time."""
    if sequence == "natural":
        return 0.0
    if max(_ranks_for(look_map, sequence, edge).values(), default=0) == 0:
        return 0.0
    return round(float(span), 2)


def compile_delays(look_map: LookMap, sequence: str, span: float,
                   ids: "dict[int, int] | None" = None,
                   edge: "dict | None" = None
                   ) -> "dict[int, bytes]":
    """bus address -> 128-byte delay table (64 x uint16, big-endian, V1.4
    7.4), for every board of the item.

    Index = socket, value = the frame (10 ms) the socket's segment
    starts on; sockets without a scale, and index 0 and 63, are
    NO_DELAY. A natural sequence gives every board a table of NO_DELAY,
    which tells a board that held a sweep to forget it. The highest
    rank always lands on exactly `span` seconds - not "close to it" -
    because frames() multiplies before it divides by the rank count.
    `edge` (the sweep's edge fields) swaps in edge_ranks(): float ranks,
    the same formula, the highest still on exactly `span`; without one,
    or with a straight one, the tables are byte for byte today's.
    """
    ids = ids or look_map.board_ids
    tables = {ids[no]: [NO_DELAY] * ARRAY_LEN for no in look_map.board_nos}
    if sequence != "natural":
        rank_of = _ranks_for(look_map, sequence, edge)
        max_rank = max(rank_of.values(), default=0)
        if max_rank > 0:
            for scale, rank in rank_of.items():
                frame = max(0, min(0xFFFF, int(round(
                    rank * span / max_rank / FRAME_S))))
                tables[ids[look_map.by_position[scale].board_no]][
                    look_map.by_position[scale].socket] = frame
    return {address: struct.pack(">64H", *table)
            for address, table in tables.items()}
