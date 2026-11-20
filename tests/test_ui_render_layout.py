"""The REMOTE screen's log rows end above the hint strip (LCD video, 2026-11-20)."""
from PIL import Image

from ui import render
from ui.config import HEIGHT, LOG_LINES, WIDTH


def _status():
    return {"phase": "armed", "label": "TEST06 soakA", "boards": [3], "saved": [],
            "failed": [], "late_ms": None, "fire_at": None, "error": None,
            "show": None}


def test_the_newest_log_line_ends_above_the_hint_strip():
    # By the font's own metrics: the last row's lowest pixel (descenders
    # included) must stay above the strip, on the unit's DejaVu as on the
    # Windows preview font.
    bottom_of_text = render.FONT_S.getbbox("Agjpqy(|")[3]
    last_row = render.REMOTE_LOG_TOP + (LOG_LINES - 1) * render.REMOTE_LOG_PITCH
    assert last_row + bottom_of_text <= HEIGHT - 20


def test_the_drawn_screen_has_the_sixth_line_whole():
    lines = [f"05:29:{n:02d} beacon run, joined 2{n}s" for n in range(LOG_LINES - 1)]
    lines.append("05:30:16 gjpqy beacon stop")        # descenders on the newest row
    image = render.remote_screen(_status(), lines, now=0.0)
    assert isinstance(image, Image.Image) and image.size == (WIDTH, HEIGHT)
    last_row = render.REMOTE_LOG_TOP + (LOG_LINES - 1) * render.REMOTE_LOG_PITCH
    band = image.crop((0, last_row, WIDTH, HEIGHT - 20))
    assert any(p != render.BG for p in band.get_flattened_data()), "the sixth line is not drawn"
    # The strip is painted over whatever reaches it, so look just ABOVE it:
    # the pixel row right over the strip is empty when the text ended before
    # it (with the old 134 + 15 px rows the descenders ran through this row).
    above = image.crop((0, HEIGHT - 21, WIDTH, HEIGHT - 20))
    assert set(above.get_flattened_data()) == {render.BG}, "text runs into the hint strip"
