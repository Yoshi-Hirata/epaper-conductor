"""The panel firmware profiles, read from conductor/web/sim/panelfw.js.

What one refresh of a scale looks like under each board firmware (the
lead-in, shake, rest and colour drive of every colour code, as measured
on the real panel) and which
codes the firmware gives another colour (FW_261119F's two pinks at 0x0A
and 0x0C). The data lives ONCE, as a JSON block inside that script, so
the designers' simulator can load it off file:// with no server; this
module reads the same block for the Conductor's page (/api/state's
"panel_fw") and for conductor/export3d.py (2026-11-19). See the script's
own header for where the numbers come from.

Only the on-screen simulation and the palette follow a profile. The
show, its cue timing and everything the units receive do not.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "web" / "sim" / "panelfw.js"
CAPTURE = SOURCE.parent / "refresh_capture_planD.json"
_HAND = ("PANEL_FW_JSON_BEGIN", "PANEL_FW_JSON_END")
_CURVES = ("PANEL_FW_CURVES_BEGIN", "PANEL_FW_CURVES_END")


def block_pattern(markers: "tuple[str, str]") -> "re.Pattern[str]":
    return re.compile(r"/\* %s \*/(.*?)/\* %s \*/" % markers, re.S)


def read_block(text: str, markers: "tuple[str, str]"):
    match = block_pattern(markers).search(text)
    if match is None:
        raise RuntimeError(f"{SOURCE.name}: no {markers[0]} block")
    return json.loads(match.group(1))


def load(source: "Path | None" = None) -> dict:
    """The hand block with each profile's generated curves on it, and the
    curves' white - what /api/state sends as "panel_fw". Raises on a
    missing file or a block that does not parse."""
    text = (source or SOURCE).read_text(encoding="utf-8")
    data = read_block(text, _HAND)
    curves = read_block(text, _CURVES)
    data["white"] = curves["white"]
    for p in data["profiles"]:
        p["curves"] = curves[p["id"]]
    return data


def _load_soft() -> "dict | None":
    """load(), but never the reason the Conductor does not start (on the
    show PC or the exhibition unit): a missing or broken panelfw.js costs
    one warning, /api/state then carries no "panel_fw" (the page's own
    fallback: a white repaint, then the colour), and the 3D export uses
    FALLBACK below. tools/make_panelfw.py rebuilds a broken generated
    block (2026-11-19, review)."""
    try:
        return load()
    except Exception as exc:        # noqa: BLE001 - any reason at all
        print(f"warning: panel firmware profiles not loaded from {SOURCE}: "
              f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return None


DATA = _load_soft()
DEFAULT = DATA["default"] if DATA else None
IDS = [p["id"] for p in DATA["profiles"]] if DATA else []
_SHARED = ("tick_s", "wave_ticks", "lead_in_ticks", "cross_ticks", "white")
# What a refresh is drawn as when DATA did not load - index.html's
# PANEL_FW_FALLBACK: white for one waveform, then the colour.
FALLBACK = {"id": "", "label": "panel firmware profiles not loaded", "tick_s": 0.011,
            "wave_ticks": 662, "lead_in_ticks": 122, "cross_ticks": 10,
            "white": "#89adc3", "palette": {}, "timeline": [[354, 354, 662]] * 16,
            "curves": {str(c): {"end": 662, "keys": [[0, "#89adc3"]]} for c in range(16)}}


def profile(fw_id: "str | None" = None) -> dict:
    """The profile called fw_id, or the default one for an unknown or
    absent id - panelfw.js's profile(), curves and shared numbers on it;
    FALLBACK when the profiles did not load."""
    if DATA is None:
        return FALLBACK
    found = next((p for p in DATA["profiles"] if p["id"] == fw_id), None)
    if found is None:
        found = next(p for p in DATA["profiles"] if p["id"] == DEFAULT)
    return dict(found, **{k: DATA[k] for k in _SHARED})


def palette_of(base: "list[dict]", fw_id: "str | None" = None) -> "list[dict]":
    """base ([{name, rgb}], as /api/state's palette) with the profile's own
    codes put over it - panelfw.js's paletteOf(). A new list."""
    over = profile(fw_id)["palette"]
    return [dict(over[str(code)]) if str(code) in over else p
            for code, p in enumerate(base)]
