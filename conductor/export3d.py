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

Which panel firmware the refresh follows is the export's own choice
(2026-11-19, conductor/panelfw.py): the page asks for its selector's
profile, the keys play that firmware's waveform per colour and "palette"
carries that firmware's names and colours (FW_261119F's pinks at 0x0A /
0x0C, their RGB still estimates). "panel_firmware" names it.

Format "epaper-3d-timeline", version 1:

    {"format", "version", "exported", "duration", "music": {"name"},
     "interpolation": "constant",
     "panel_firmware": {"id", "label"},     # whose waveform and colours
     "colors": ["#rrggbb", ...],            # the keys' colour table
     "palette": [{"code", "name", "hex"[, "estimate"]}],  # the 16 colours
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

import bisect
import math
import time

from . import panelfw, timeline
# flicker.js hash32(), bit for bit - one implementation, shared with the
# sweep edge's noise (conductor/sequence.py, 2026-10-22).
from .sequence import hash32  # noqa: F401  (export3d.hash32 stays importable)

FORMAT = "epaper-3d-timeline"
FORMAT_VERSION = 1

# --- the page's flicker model (conductor/web/sim/flicker.js) -------------
# Since 2026-11-19 a scale's refresh plays the panel firmware profile's
# MEASURED curve for its target code (conductor/web/sim/panelfw.js, made
# from the reference video by tools/make_panelfw.py); the old generic
# flicker went.
JITTER_MAX = 0.35
SWEEP_JITTER_FRAC = 0.3
# Keys are taken just after each candidate instant, so a boundary that
# floating point puts a hair early still lands on the new colour.
_EPS = 1e-6


def _js_round(x: float) -> int:
    """Math.round: halves go up, not to even."""
    return math.floor(x + 0.5)


def _hex(rgb) -> str:
    return "#" + "".join(f"{_js_round(v):02x}" for v in rgb)


def _rgb(hex_color: str) -> "list[int]":
    return [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]


class _Palette:
    def __init__(self, palette: "list[dict]"):
        self.rgb = [tuple(p["rgb"]) for p in palette]
        self._lead: "dict[tuple[str, str, int], str]" = {}

    def hex(self, code: int) -> str:
        return _hex(self.rgb[code])

    def lead(self, from_hex: str, measured_hex: str, step: int, fw: dict) -> str:
        """leadHex(): the previous colour plus the measured change from
        white, blended into the measured colour over the cross."""
        key = (from_hex, measured_hex, step)
        if key not in self._lead:
            source, measured, white = _rgb(from_hex), _rgb(measured_hex), _rgb(fw["white"])
            w = step / fw["cross_ticks"]
            self._lead[key] = "#" + "".join(
                f"{_js_round(max(0, min(255, source[i] + v - white[i])) * (1 - w) + v * w):02x}"
                for i, v in enumerate(measured))
        return self._lead[key]


# id(curve["keys"]) -> its ticks, for bisect (the curves live as long as
# conductor/panelfw.DATA, i.e. the process).
_KEY_TICKS: "dict[int, list[float]]" = {}


def phase_color(tick: float, code: int, prev: "str | None", palette: _Palette,
                fw: dict) -> str:
    """refreshPhaseColor(): the look `tick` waveform ticks into a refresh to
    code, from prev (the colour before, None = the bare garment, as white)."""
    curve = fw["curves"][str(code)]
    if tick >= curve["end"]:
        return palette.hex(code)
    keys = curve["keys"]
    ticks = _KEY_TICKS.get(id(keys))
    if ticks is None:
        ticks = _KEY_TICKS[id(keys)] = [k[0] for k in keys]
    measured = keys[max(bisect.bisect_right(ticks, tick) - 1, 0)][1]
    cross = tick - fw["lead_in_ticks"]
    if cross >= fw["cross_ticks"]:
        return measured
    return palette.lead(prev if prev is not None else fw["white"], measured,
                        0 if cross < 0 else math.floor(cross), fw)


def _phase_ticks(code: int, fw: dict) -> "list[float]":
    """Every tick where phase_color() for code can change: its keys, the
    steps of the cross into the shake, and the end."""
    curve = fw["curves"][str(code)]
    lead = fw["lead_in_ticks"]
    return ([k[0] for k in curve["keys"]] + [curve["end"]]
            + [lead + j for j in range(fw["cross_ticks"] + 1)])


class _Cue:
    """One cue of an item, with refreshModelFor()'s delay and jitter."""

    def __init__(self, cue: dict, item: dict, n_scales: int,
                 fw: "dict | None" = None):
        fw = fw or panelfw.profile()
        self.cue = cue
        self.sent = float(cue["sent"])
        self.complete = float(cue["complete"])
        self.partial = bool(cue.get("partial"))
        sweep = cue.get("sweep") or {"sequence": "natural", "span_s": 0}
        span = float(sweep.get("span_s") or 0)
        # A sweep with an edge (2026-10-22) has float ranks of its own,
        # keyed by rank_set in item.sweep_ranks - the page's own lookup,
        # so the 3D view's border is the Timeline's; a straight one keeps
        # item.sequences, unchanged.
        rank_key = sweep.get("rank_set") or ""
        ranks = ((item.get("sweep_ranks") or {}).get(rank_key) if rank_key
                 else (item.get("sequences") or {}).get(sweep.get("sequence")))
        max_rank = max(ranks, default=0) if ranks else 0
        swept = bool(ranks) and max_rank > 0 and span > 0
        cap = (min(JITTER_MAX, SWEEP_JITTER_FRAC * span / max_rank) if swept
               else JITTER_MAX)
        # panelRepaintFor(): one physical repaint, never past the cue's refresh.
        self.repaint = min(timeline.PANEL_REPAINT_S, float(cue["refresh"]))
        # applySweptColors(): the firmware's waveform inside the repaint.
        self.wave = min(self.repaint, fw["wave_ticks"] * fw["tick_s"])
        self.delay = [ranks[i] * span / max_rank if ranks and max_rank > 0
                      else 0.0 for i in range(n_scales)]
        # startJitter(): a late start, never more than half the waveform.
        self.jitter = [min((hash32(i, 0xa5a5) % 1000) / 1000 * cap,
                           self.wave / 2) for i in range(n_scales)]

    def scale_start(self, n: int) -> float:
        return self.sent + self.delay[n] + self.jitter[n]


def _color_at(n: int, key: str, t: float, cues: "list[tuple[_Cue, dict]]",
              palette: _Palette, fw: dict) -> "str | None":
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
                own = c.wave - c.jitter[n]
                color = (palette.hex(code) if t >= c.sent + c.delay[n] + c.wave
                         else phase_color(tau / own * fw["wave_ticks"],
                                          code, color, palette, fw))
            continue
        code = design["colors"].get(key)
        if c.partial:
            if code is not None:
                color = palette.hex(code)
        else:
            color = palette.hex(code) if code is not None else None
    return color


def _instants(n: int, key: str, cues: "list[tuple[_Cue, dict]]",
              fw: dict) -> "list[float]":
    """Every instant scale n's colour can change: each cue's send and
    complete, and inside each waveform its curve's keys, the cross into the
    shake and the end."""
    out = []
    for c, design in cues:
        out += [c.sent, c.complete]
        code = design["colors"].get(key)
        if code is None:
            continue
        start = c.scale_start(n)
        own = c.wave - c.jitter[n]
        out += [start, start + own, c.sent + c.delay[n] + c.wave]
        out += [start + tick / fw["wave_ticks"] * own
                for tick in _phase_ticks(code, fw)]
    return sorted(set(out))


def resolve(state: dict, now: "float | None" = None,
            fw: "str | None" = None) -> dict:
    """The 3D timeline from Workspace.state() (see the module doc), with
    the refresh and the palette of panel firmware profile fw (absent or
    unknown: conductor/panelfw.py's default)."""
    profile = panelfw.profile(fw)
    design_palette = panelfw.palette_of(state["palette"], profile["id"])
    palette = _Palette(design_palette)
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
        cues = [(_Cue(c, item, len(scales), profile), designs[c["design"]])
                for c in own if c["design"] in designs]
        labels, keys = [], {}
        for n, (side, row, col, board_no, socket) in enumerate(scales):
            label = f"{board_no:03d}-{socket:02d}"
            labels.append({"label": label, "side": side, "row": row, "col": col})
            key = f"{side}|{row}|{col}"
            track, last = [], None
            for t in _instants(n, key, cues, profile):
                color = _color_at(n, key, t + _EPS, cues, palette, profile)
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
        "panel_firmware": {"id": profile["id"], "label": profile["label"]},
        "colors": colors,
        # "estimate": true on a colour with no measured RGB yet.
        "palette": [dict({"code": i, "name": p["name"], "hex": palette.hex(i)},
                         **({"estimate": True} if p.get("estimate") else {}))
                    for i, p in enumerate(design_palette)],
        "items": items,
    }
