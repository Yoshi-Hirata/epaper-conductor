"""The show resolved for a 3D simulator: every scale's colour over time.

A show file (Save show...) holds the cues as the operator sets them - a
design, a time, a transition (sequence + span) and a refresh. Turning
that into what each scale shows when is this Conductor's own model
(conductor/sequence.py for the sweep order, the page's flicker model for
the repaint), and the other team's 3D simulator must reproduce the wall
WITHOUT that model (operator's decision, 2026-10-14: the rules stay
here). So this file carries only the RESULT: for every scale, keyed by
its map label ("017-01" = board 17, socket 1), the instants its colour
changes and the colour it changes to. Held between keys (constant
interpolation), the flicker of a repaint included.

The flicker below is the page's own (conductor/web/sim/flicker.js,
wornAt with flicker on), ported line for line so the 3D view and the
Conductor's Timeline show the same frames; tests/test_export3d.py runs
both on the same show and compares them.

Format "epaper-3d-timeline", version 1:

    {"format", "version", "exported", "duration", "music": {"name"},
     "interpolation": "constant",
     "colors": ["#rrggbb", ...],            # the keys' colour table
     "palette": [{"code", "name", "hex"}],  # the 16 design colours
     "items": {"<item>": {
         "look", "model",
         "scales": [{"label", "side", "row", "col"}],
         "cues": [{"id", "design", "label", "at", "partial"}],
         "keys": {"<label>": [[t, color_index], ...]}}}}

`t` is seconds on the show clock (0 = START; a preset starts before 0),
to the millisecond. `color_index` points into "colors"; null means no
design on that scale (as before the show). Before its first key a scale
has no colour from the show.
"""

from __future__ import annotations

import math
import time

from . import timeline

FORMAT = "epaper-3d-timeline"
FORMAT_VERSION = 1

# --- the page's flicker model (conductor/web/sim/flicker.js) -------------
JITTER_MAX = 0.35
SWEEP_JITTER_FRAC = 0.3
REFRESH_TINT = (0xb8, 0xa0, 0x30)
REFRESH_PHASE_A = ("#2b2a4e", "#3a2f5a")
REFRESH_PALETTE = ("#2b2a4e", "#3a2f5a", "#9a9aa0", "#c9c9cc", "#e6e3d8",
                   "#d8cf7a", "#b8b83a", "#7a5a3a", "#8a4a3a", "#2f4a7a")
REFRESH_PHASE_C = ("#c9c9cc", "#e6e3d8", "#d8cf7a", "#b8b83a", "#7a5a3a")
TINT_STEPS = 8
BUCKET_S = 0.25                 # refreshPhaseColor's flicker step
# The fractions of a scale's repaint where refreshPhaseColor changes phase.
PHASE_FRACS = (0.13, 0.50, 0.72, 0.95)
_MASK = 0xFFFFFFFF
# Keys are taken just after each candidate instant, so a boundary that
# floating point puts a hair early still lands on the new colour.
_EPS = 1e-6


def _imul(a: int, b: int) -> int:
    return ((a & _MASK) * (b & _MASK)) & _MASK


def hash32(a: int, b: int) -> int:
    """flicker.js hash32(), bit for bit (Math.imul and >>> on uint32)."""
    h = _imul(a ^ 0x9e3779b1, 0x85ebca6b) ^ _imul(b ^ 0x27d4eb2f, 0xc2b2ae35)
    h = _imul(h ^ (h >> 15), 0x2c1b3c6d)
    h = _imul(h ^ (h >> 12), 0x297a2d39)
    return (h ^ (h >> 15)) & _MASK


def _js_round(x: float) -> int:
    """Math.round: halves go up, not to even."""
    return math.floor(x + 0.5)


def _hex(rgb) -> str:
    return "#" + "".join(f"{_js_round(v):02x}" for v in rgb)


class _Palette:
    def __init__(self, palette: "list[dict]"):
        self.rgb = [tuple(p["rgb"]) for p in palette]
        self._tinted: "dict[tuple[int, int], str]" = {}

    def hex(self, code: int) -> str:
        return _hex(self.rgb[code])

    def tinted(self, code: int, w: float) -> str:
        step = max(0, min(TINT_STEPS, _js_round(w * TINT_STEPS)))
        key = (code, step)
        if key not in self._tinted:
            ww = step / TINT_STEPS
            target = [int(c[1:][i:i + 2], 16) for c in [self.hex(code)]
                      for i in (0, 2, 4)]
            self._tinted[key] = "#" + "".join(
                f"{_js_round(v * (1 - ww) + REFRESH_TINT[i] * ww):02x}"
                for i, v in enumerate(target))
        return self._tinted[key]


def phase_color(n: int, tau: float, frac: float, code: int,
                palette: _Palette) -> str:
    """refreshPhaseColor(): scale n's colour tau seconds into its repaint."""
    bucket = math.floor(tau / BUCKET_S)
    h = hash32(n, bucket)
    if frac < 0.13:
        return REFRESH_PHASE_A[h % len(REFRESH_PHASE_A)]
    if frac < 0.50:
        return REFRESH_PALETTE[h % len(REFRESH_PALETTE)]
    if frac < 0.72:
        return REFRESH_PHASE_C[h % len(REFRESH_PHASE_C)]
    if frac < 0.95:
        if h % 6 == 0:
            return REFRESH_PHASE_C[hash32(n, bucket + 1) % len(REFRESH_PHASE_C)]
        return palette.tinted(code, 0.45 * (1 - (frac - 0.72) / 0.23))
    return palette.hex(code)


# The fractions where the tint's step (round(w * 8)) moves: w * 8 = k + 0.5.
_TINT_FRACS = tuple(0.72 + 0.23 * (1 - (k + 0.5) / (0.45 * TINT_STEPS))
                    for k in range(TINT_STEPS))


class _Cue:
    """One cue of an item, with refreshModelFor()'s delay and jitter."""

    def __init__(self, cue: dict, item: dict, n_scales: int):
        self.cue = cue
        self.sent = float(cue["sent"])
        self.complete = float(cue["complete"])
        self.partial = bool(cue.get("partial"))
        sweep = cue.get("sweep") or {"sequence": "natural", "span_s": 0}
        span = float(sweep.get("span_s") or 0)
        ranks = (item.get("sequences") or {}).get(sweep.get("sequence"))
        max_rank = max(ranks, default=0) if ranks else 0
        swept = bool(ranks) and max_rank > 0 and span > 0
        cap = (min(JITTER_MAX, SWEEP_JITTER_FRAC * span / max_rank) if swept
               else JITTER_MAX)
        # panelRepaintFor(): one physical repaint, never past the cue's refresh.
        self.repaint = min(timeline.PANEL_REPAINT_S, float(cue["refresh"]))
        self.delay = [ranks[i] * span / max_rank if ranks and max_rank > 0
                      else 0.0 for i in range(n_scales)]
        # startJitter(): a late start, never more than half the repaint.
        self.jitter = [min((hash32(i, 0xa5a5) % 1000) / 1000 * cap,
                           self.repaint / 2) for i in range(n_scales)]

    def scale_start(self, n: int) -> float:
        return self.sent + self.delay[n] + self.jitter[n]


def _color_at(n: int, key: str, t: float, cues: "list[tuple[_Cue, dict]]",
              palette: _Palette) -> "str | None":
    """wornAt() with flicker, for the one scale n."""
    color = None
    for c, design in cues:
        if c.sent > t:          # sorted by send: nothing later has begun
            break
        if c.complete > t:
            if c.sent <= t:
                code = design["colors"].get(key)
                if code is None:
                    continue
                start = c.scale_start(n)
                tau = t - start
                if tau < 0:
                    continue
                own = c.repaint - c.jitter[n]
                color = (palette.hex(code) if tau >= own
                         else phase_color(n, tau, tau / own, code, palette))
            continue
        code = design["colors"].get(key)
        if c.partial:
            if code is not None:
                color = palette.hex(code)
        else:
            color = palette.hex(code) if code is not None else None
    return color


def _instants(n: int, key: str, cues: "list[tuple[_Cue, dict]]") -> "list[float]":
    """Every instant scale n's colour can change: each cue's send and
    complete, and inside each repaint the flicker's steps, its phase
    boundaries, the tint's steps and the end."""
    out = []
    for c, design in cues:
        out += [c.sent, c.complete]
        if design["colors"].get(key) is None:
            continue
        start = c.scale_start(n)
        own = c.repaint - c.jitter[n]
        out += [start, start + own]
        out += [start + BUCKET_S * k for k in range(1, int(own / BUCKET_S) + 1)]
        out += [start + f * own for f in PHASE_FRACS + _TINT_FRACS]
    return sorted(set(out))


def resolve(state: dict, now: "float | None" = None) -> dict:
    """The 3D timeline from Workspace.state() (see the module doc)."""
    palette = _Palette(state["palette"])
    colors: "list[str]" = []
    index: "dict[str, int]" = {}

    def color_index(hex_color: "str | None") -> "int | None":
        if hex_color is None:
            return None
        if hex_color not in index:
            index[hex_color] = len(colors)
            colors.append(hex_color)
        return index[hex_color]

    show = state["show"]
    items = {}
    for item in state["items"]:
        scales = item["map"]["scales"]
        if not scales:
            continue
        designs = {d["name"]: d for d in item["designs"]}
        own = sorted((c for c in show["cues"] if c["item"] == item["item"]),
                     key=lambda c: c["sent"])
        cues = [(_Cue(c, item, len(scales)), designs[c["design"]])
                for c in own if c["design"] in designs]
        labels, keys = [], {}
        for n, (side, row, col, board_no, socket) in enumerate(scales):
            label = f"{board_no:03d}-{socket:02d}"
            labels.append({"label": label, "side": side, "row": row, "col": col})
            key = f"{side}|{row}|{col}"
            track, last = [], None
            for t in _instants(n, key, cues):
                color = _color_at(n, key, t + _EPS, cues, palette)
                if color == last:
                    continue
                at = round(t, 3)
                if track and track[-1][0] == at:
                    # Two changes inside one millisecond: keep the later,
                    # so times stay strictly increasing for importers.
                    track.pop()
                    if (track[-1][1] if track else None) == color_index(color):
                        last = color
                        continue
                track.append([at, color_index(color)])
                last = color
            keys[label] = track
        items[item["item"]] = {
            "look": item.get("look", ""), "model": item.get("model", ""),
            "scales": labels,
            # What and when only. The sweep, its span, the refresh and the
            # send/complete instants would let the keys be turned back into
            # the rules (review, 2026-10-14) - a player needs none of them.
            "cues": [{"id": c["id"], "design": c["design"],
                      "label": designs[c["design"]].get("label", "")
                      if c["design"] in designs else "",
                      "at": c["at"], "partial": bool(c.get("partial"))}
                     for c in own],
            "keys": keys,
        }
    return {
        "format": FORMAT, "version": FORMAT_VERSION,
        "exported": time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.localtime(now if now is not None else time.time())),
        "duration": show["duration"],
        "music": {"name": (state.get("music") or {}).get("name")},
        "interpolation": "constant",
        "colors": colors,
        "palette": [{"code": i, "name": p["name"], "hex": palette.hex(i)}
                    for i, p in enumerate(state["palette"])],
        "items": items,
    }
