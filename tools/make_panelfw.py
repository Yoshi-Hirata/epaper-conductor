"""tools/make_panelfw.py - the measured refresh curves of conductor/web/sim/panelfw.js.

Reads the reference video's capture (conductor/web/sim/refresh_capture_planD.json:
for each colour code, the scale's raw camera RGB every 0.08 s during a refresh
from white to that code, recorded on the real panel with the plan-D image) and
the hand block of panelfw.js (timelines, palette overrides, which recording
each code plays), and writes panelfw.js's generated block: per profile and
code, the colour from each waveform tick on (keys [[tick, "#rrggbb"], ...],
held to the next key) until `end` (drive_end), where the palette colour takes
over (user's measurement, 2026-11-19).

Normalisation (camera -> screen): each recording is white-balanced on its own
pre-refresh white (the scale before tick 0), per channel, so that white is
conductor/look.py's palette white; then, over the code's drive window
[drive_start, drive_end], an offset ramps in from 0 to whatever makes the
settled end of the recording (mean of its last 3 samples) the code's palette
colour exactly. Before the drive the curve is the white-balanced camera
colour as is.

FW_261119F plays the recordings on its own tick table (plan D = FW_261119F
with 0x0A/0x0C exchanged: "recorded" maps them). FW_260923 replays the same
curves re-timed in ticks: everything before its drive_start unchanged, the
FW_261119F drive [drive_start_F, drive_end_F] moved to start at its own
drive_start (same length), the idle time thus moved to after the end. Its
0x0A/0x0C (orange, olive grey) have no recording: "synth" names the code
whose lead-in and shake they borrow ("from"; the lead-in's yellow onset
re-timed to the image's own step 0) and the code whose measured drive they
replay ("drive": 15, whose tail has the same shape - a long block to 602, a
rest, a 1-2 tick pulse at the end). That drive is pulled towards their own
colour (an offset of target - code 15's colour ramping in to "hold"), the
colour reached at "hold" held through the rest, and the palette colour shown
from the final pulse (drive_end) on (PM, 2026-11-19).

    python tools/make_panelfw.py            # rewrite the generated block
    python tools/make_panelfw.py --check    # exit 1 if it is stale

Run after editing the hand block (a pink's RGB, a timeline), the capture, or
conductor/look.py's PALETTE.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conductor import panelfw  # noqa: E402
from conductor.look import PALETTE  # noqa: E402

SETTLED_SAMPLES = 3


def _rgb(hex6: str) -> "tuple[int, int, int]":
    return tuple(int(hex6[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02x}" for v in rgb)


def samples(capture: dict, key: str) -> "list[tuple[float, tuple]]":
    """[(tick, raw rgb)] of one recording, tick on the capture's tick."""
    text = capture["curves"][key]
    out = []
    for i in range(capture["n"]):
        t = round(capture["first_sample_t"] + i * capture["dt"] - capture["t0_video"], 6)
        out.append((round(t / capture["tick_s"], 2), _rgb(text[6 * i:6 * i + 6])))
    return out


def normalised(capture: dict, key: str, timeline: list, target) -> "list[tuple[float, tuple]]":
    """One recording white-balanced and pulled onto its palette colour (see
    the module doc), every sample from tick 0 on, floats."""
    raw = samples(capture, key)
    before = [rgb for tick, rgb in raw if tick < 0]
    white = PALETTE[0][1]
    gain = [white[ch] / (sum(rgb[ch] for rgb in before) / len(before)) for ch in range(3)]
    tail = [rgb for _, rgb in raw[-SETTLED_SAMPLES:]]
    settled = [gain[ch] * sum(rgb[ch] for rgb in tail) / len(tail) for ch in range(3)]
    offset = [target[ch] - settled[ch] for ch in range(3)]
    _, drive_start, drive_end = timeline
    out = []
    for tick, rgb in raw:
        if tick < 0:
            continue
        span = drive_end - drive_start
        w = (1.0 if tick >= drive_end else 0.0) if span <= 0 else \
            max(0.0, min(1.0, (tick - drive_start) / span))
        out.append((tick, [gain[ch] * rgb[ch] + w * offset[ch] for ch in range(3)]))
    return out


def colour_at(curve, tick):
    """The sample in force at tick (the last one at or before it)."""
    found = curve[0][1]
    for t, rgb in curve:
        if t > tick:
            break
        found = rgb
    return found


def keys_of(points, end) -> list:
    """[[tick, "#rrggbb"]] before end, a key only where the colour changes."""
    out = []
    for tick, rgb in sorted(points, key=lambda p: p[0]):
        if tick >= end:
            break
        colour = _hex(rgb)
        if out and out[-1][1] == colour:
            continue
        if out and out[-1][0] == tick:
            out[-1][1] = colour
            continue
        out.append([tick, colour])
    return out


def palette_of(profile: dict) -> list:
    """look.PALETTE with THIS hand-block profile's overrides on it - from
    the block being built, never from conductor/panelfw.DATA (what the file
    held when the process started), so build() is a pure function of
    (capture, hand, PALETTE) - review, 2026-11-19."""
    over = profile.get("palette") or {}
    return [dict(over[str(code)]) if str(code) in over else {"name": n, "rgb": list(rgb)}
            for code, (n, rgb) in enumerate(PALETTE)]


def build(capture: dict, hand: dict) -> dict:
    by_id = {p["id"]: p for p in hand["profiles"]}
    fast = by_id["FW_261119F"]
    fast_palette = palette_of(fast)
    # FW_261119F's normalised recordings: the base every profile replays.
    recorded = {}
    for code in range(16):
        key = fast["recorded"].get(str(code), str(code))
        recorded[code] = normalised(capture, key, fast["timeline"][code],
                                    fast_palette[code]["rgb"])
    out = {"white": _hex(PALETTE[0][1])}
    for profile in hand["profiles"]:
        palette = palette_of(profile)
        curves = {}
        for code in range(16):
            shake_end, drive_start, drive_end = profile["timeline"][code]
            synth = profile["synth"].get(str(code))
            if profile is fast:
                points = recorded[code]
            elif synth is not None:
                src = recorded[synth["from"]]
                lead = hand["lead_in_ticks"]
                points = []
                for tick, rgb in src:
                    if tick >= shake_end:
                        break
                    if tick < lead and "onset" in synth and tick >= synth["onset_from"]:
                        tick = round(synth["onset"] + (tick - synth["onset_from"])
                                     * (lead - synth["onset"]) / (lead - synth["onset_from"]), 2)
                    points.append((tick, rgb))
                template = recorded[synth["drive"]]
                t_start = fast["timeline"][synth["drive"]][1]
                shift = drive_start - t_start
                hold = synth["hold"]
                target = palette[code]["rgb"]
                pull = [target[ch] - fast_palette[synth["drive"]]["rgb"][ch] for ch in range(3)]

                def pulled(rgb, tick):
                    w = max(0.0, min(1.0, (tick - drive_start) / (hold - drive_start)))
                    return [rgb[ch] + w * pull[ch] for ch in range(3)]
                points.append((drive_start, pulled(colour_at(template, t_start), drive_start)))
                points += [(round(t + shift, 2), pulled(rgb, t + shift)) for t, rgb in template
                           if t_start < t and t + shift < hold]
                points.append((hold, pulled(colour_at(template, hold - shift), hold)))
            else:
                f_start, f_end = fast["timeline"][code][1:]
                if f_end - f_start != drive_end - drive_start:
                    raise SystemExit(f"{profile['id']} code {code}: drive length "
                                     f"{drive_end - drive_start} != FW_261119F's {f_end - f_start}")
                src = recorded[code]
                shift = drive_start - f_start
                points = [(t, rgb) for t, rgb in src if t < drive_start]
                points.append((drive_start, colour_at(src, f_start)))
                points += [(round(t + shift, 2), rgb) for t, rgb in src if f_start < t < f_end]
            curves[str(code)] = {"end": drive_end, "keys": keys_of(points, drive_end)}
        out[profile["id"]] = curves
    return out


def render(curves: dict) -> str:
    lines = ["{", f' "white": {json.dumps(curves["white"])},']
    ids = [k for k in curves if k != "white"]
    for n, fw in enumerate(ids):
        lines.append(f" {json.dumps(fw)}: {{")
        for code in range(16):
            body = json.dumps(curves[fw][str(code)], separators=(",", ":"))
            lines.append(f'  "{code}": {body}' + ("," if code < 15 else ""))
        lines.append(" }" + ("," if n < len(ids) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def updated_source(text: "str | None" = None) -> "tuple[str, str]":
    """(the script's text, that text with the generated block rebuilt)."""
    if text is None:
        text = panelfw.SOURCE.read_text(encoding="utf-8")
    hand = panelfw.read_block(text, panelfw._HAND)
    capture = json.loads(panelfw.CAPTURE.read_text(encoding="utf-8"))
    if capture["tick_s"] != hand["tick_s"]:
        raise SystemExit(f"tick_s: capture {capture['tick_s']} != panelfw.js {hand['tick_s']}")
    block = render(build(capture, hand))
    pattern = panelfw.block_pattern(panelfw._CURVES)
    # Whatever is between the markers is replaced unread, so an empty or
    # broken generated block is rebuilt; the markers themselves must be
    # there (they sit in the script's code, which this tool does not write).
    if not pattern.search(text):
        raise SystemExit(f"{panelfw.SOURCE.name}: no /* {panelfw._CURVES[0]} */ ... "
                         f"/* {panelfw._CURVES[1]} */ markers to write between")
    new = pattern.sub(lambda m: "/* %s */\n%s/* %s */" % (panelfw._CURVES[0], block,
                                                          panelfw._CURVES[1]), text, count=1)
    return text, new


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if panelfw.js is stale")
    args = parser.parse_args(argv)
    old, new = updated_source()
    if args.check:
        if old != new:
            print("conductor/web/sim/panelfw.js is stale: run python tools/make_panelfw.py")
            return 1
        return 0
    if old != new:
        panelfw.SOURCE.write_text(new, encoding="utf-8", newline="\n")
        print("wrote", panelfw.SOURCE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
