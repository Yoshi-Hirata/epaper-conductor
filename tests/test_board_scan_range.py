"""UPDATE FW, FW VERSION and CLEAR BOARD look for board IDs 1..30 when the
unit names none (2026-11-22, Kunitachi: radxa-03 carries 27 boards with
DIP IDs up to 28 - the old 1..20 could not find IDs 21 and up)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import ota  # noqa: E402
from ui.boardclear import BoardClearer  # noqa: E402
from ui.updater import FirmwareUpdater  # noqa: E402
from ui.versions import BoardVersions  # noqa: E402


def test_the_scan_covers_ids_1_to_30():
    assert ota.SCAN_BOARDS == list(range(1, 31))


def test_the_three_menu_workers_default_to_the_scan_range(tmp_path):
    assert BoardVersions().boards == list(range(1, 31))
    assert BoardClearer().boards == list(range(1, 31))
    updater = FirmwareUpdater(None, images=[], flash_log=tmp_path / "log.json")
    assert updater.boards == list(range(1, 31))


def test_named_boards_still_win(tmp_path):
    assert BoardVersions(boards=[3, 28]).boards == [3, 28]
