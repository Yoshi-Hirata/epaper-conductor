"""tools/fw_waveform.py: decoding the colour waveform table of the shipped
firmware images, and the home-made FW_261118B trial image (FW_260923 with
colours 0x0A/0x0C from the 11/9 pink candidates, 2026-11-19)."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

import ota  # noqa: E402
import tools.fw_waveform as fw  # noqa: E402

FW_DIR = REPO / "FW"
BASE = FW_DIR / "FW_260923" / "fw2029.09.23" / "MCB_16_0923.bin"
RED = FW_DIR / "FW_261109" / "mcb_pink_red_0_16.bin"
PURPLE = FW_DIR / "FW_261109" / "mcb_pink_purple_0_7.bin"
TWO = FW_DIR / "FW_261118" / "mcb_two_pinks(0a,0c).bin"
SHIPPED = FW_DIR / "FW_261118B" / "mcb_0923_pinks_no13.bin"
PLAN = [(10, RED, 12), (12, PURPLE, 12)]


def make_plan_image():
    return fw.transplant(BASE.read_bytes(),
                         [(c, p.read_bytes(), dc, p.name) for c, p, dc in PLAN])


# ---- decoding the shipped images ----

@pytest.mark.parametrize("path, total", [
    (FW_DIR / "FW_260903" / "OTA_16c.bin", 616),     # name PDName1SPS005001
    (FW_DIR / "FW_260917" / "MCB_e16_2029.09.17.bin", 662),   # = FW_260923's table
    (BASE, 662), (PURPLE, 585), (RED, 611), (TWO, 1197)])
def test_every_colour_lasts_the_header_total(path, total):
    table = fw.parse(path.read_bytes())
    assert table.total == total
    assert len(table.entries) == 16
    assert [e.duration for e in table.entries] == [total] * 16


@pytest.mark.parametrize("path", [BASE, PURPLE, RED])
def test_without_step_0_every_colour_lasts_540(path):
    for e in fw.parse(path.read_bytes()).entries:
        assert sum(fw.step_duration(s) for s in e.steps[1:]) == 540


def test_the_donor_and_base_entries_are_as_planned():
    base = fw.parse(BASE.read_bytes())
    assert base.key == bytes.fromhex("c8211551") and base.date == "2026-09-17 10:09:53"
    assert len(base.entries[10].steps) == len(base.entries[12].steps) == 15
    red, purple = fw.parse(RED.read_bytes()), fw.parse(PURPLE.read_bytes())
    assert red.entries[12].steps[0] == (1, 0, 40, 1, 31, 0, 0)
    assert purple.entries[12].steps[0] == (1, 0, 20, 1, 25, 0, 0)
    assert len(red.entries[12].steps) == len(purple.entries[12].steps) == 14


def test_encode_is_the_inverse_of_parse():
    image = BASE.read_bytes()
    table = fw.parse(image)
    for e in table.entries:
        assert fw.encode(e.steps, table.key) == image[e.start:e.end]


# ---- the transplant ----

def test_the_transplant_changes_only_colours_10_and_12():
    base = BASE.read_bytes()
    image, changes = make_plan_image()
    assert len(image) == len(base)
    old, new = fw.parse(base), fw.parse(image)
    ranges = [(old.entries[c].start, old.entries[c].end) for c in (10, 12)]
    assert [(c.start, c.end) for c in changes] == ranges
    diff = [i for i, (a, b) in enumerate(zip(base, image)) if a != b]
    assert diff and all(any(s <= i < e for s, e in ranges) for i in diff)
    assert image[:old.name + 16] == base[:old.name + 16]     # header untouched
    for c in range(16):
        if c not in (10, 12):
            assert new.entries[c] == old.entries[c]
    assert [e.duration for e in new.entries] == [662] * 16


def test_the_transplanted_entries_drive_like_the_donors():
    image, _ = make_plan_image()
    new = fw.parse(image)
    for colour, donor, donor_colour in PLAN:
        donor_steps = fw.parse(donor.read_bytes()).entries[donor_colour].steps
        steps = new.entries[colour].steps
        assert fw.timeline(steps[1:]) == fw.timeline(donor_steps[1:])
        assert steps[0][:2] + steps[0][3:] == donor_steps[0][:2] + donor_steps[0][3:]
        assert all(fw.step_duration(s) > 0 for s in steps)
    assert new.entries[10].steps[0] == (1, 0, 91, 1, 31, 0, 0)
    assert new.entries[12].steps[0] == (1, 0, 97, 1, 25, 0, 0)
    for colour in (10, 12):    # the level-0 hold of 4 split in two
        assert new.entries[colour].steps[10:12] == ((1, 0, 2, 0, 0, 0, 0),) * 2


# ---- the shipped image, pinned by literals (review 2026-11-19: the
# checks above all go through the tool's own parse/timeline) ----

SHIPPED_SHA256 = "6cd0defc8d7ce61bb8987cefa5974b1c37b5c09cb37495da9839d1031a420e04"
MASK = bytes.fromhex("c8211551" "211551")       # FW_260923's key + key[1:]
RANGES = {10: (0xD3F0, 0xD45A), 12: (0xD4B6, 0xD520)}
COMMON = ((26, 6, 2, 1, 2, 0, 0), (1, 6, 10, 0, 0, 0, 0), (5, 6, 2, 1, 2, 0, 0),
          (1, 6, 10, 0, 0, 0, 0), (5, 6, 2, 1, 2, 0, 0), (1, 6, 10, 0, 0, 0, 0),
          (3, 1, 4, 6, 4, 0, 0), (1, 6, 10, 0, 0, 0, 0), (3, 1, 4, 6, 4, 0, 0),
          (1, 0, 2, 0, 0, 0, 0), (1, 0, 2, 0, 0, 0, 0))
STEPS = {
    10: ((1, 0, 91, 1, 31, 0, 0),) + COMMON
    + ((8, 6, 6, 0, 5, 3, 17), (12, 0, 0, 0, 0, 0, 6), (1, 0, 0, 0, 8, 0, 0)),
    12: ((1, 0, 97, 1, 25, 0, 0),) + COMMON
    + ((4, 6, 3, 0, 5, 3, 16), (12, 0, 0, 0, 6, 0, 11), (2, 0, 2, 0, 0, 0, 0)),
}
BASE_10 = ((1, 0, 112, 1, 10, 0, 0), (26, 6, 2, 1, 2, 0, 0), (1, 1, 10, 0, 0, 0, 0),
           (5, 6, 2, 1, 2, 0, 0), (1, 1, 10, 0, 0, 0, 0), (5, 6, 2, 1, 2, 0, 0),
           (1, 1, 10, 0, 0, 0, 0), (3, 1, 4, 6, 4, 0, 0), (1, 6, 10, 0, 0, 0, 0),
           (3, 1, 4, 6, 4, 0, 0), (1, 0, 8, 0, 0, 0, 0), (16, 2, 2, 0, 2, 4, 11),
           (1, 0, 6, 0, 0, 0, 0), (2, 0, 10, 0, 15, 0, 0), (1, 1, 2, 0, 2, 0, 0))
# Colour 10's drive after step 0, worked out by hand from STEPS[10]:
# repeats expanded, zero times dropped, equal neighbours merged.
RUNS_10 = ([(6, 2), (1, 2)] * 26
           + [(6, 12), (1, 2)] + [(6, 2), (1, 2)] * 4
           + [(6, 12), (1, 2)] + [(6, 2), (1, 2)] * 4
           + [(6, 10)]
           + [(1, 4), (6, 4)] * 2 + [(1, 4), (6, 14)]
           + [(1, 4), (6, 4)] * 3
           + [(0, 4)]
           + [(6, 6), (0, 5), (3, 17)] * 8
           + [(0, 80)])


def decode_here(blob):
    """The entry decode written out again, independent of the tool."""
    count = blob[0]
    assert len(blob) == 1 + 7 * count
    return tuple(tuple(blob[1 + 7 * n + j] ^ MASK[j] for j in range(7))
                 for n in range(count))


def runs_here(steps):
    runs = []
    for rep_, a, ta, b, tb, c, tc in steps:
        for _ in range(rep_):
            for level, time in ((a, ta), (b, tb), (c, tc)):
                if not time:
                    continue
                if runs and runs[-1][0] == level:
                    runs[-1] = (level, runs[-1][1] + time)
                else:
                    runs.append((level, time))
    return runs


def test_the_shipped_image_is_pinned():
    image = SHIPPED.read_bytes()
    assert hashlib.sha256(image).hexdigest() == SHIPPED_SHA256
    assert ota.crc16_modbus(image) == 0x92D5 and len(image) == 64328
    base = BASE.read_bytes()
    diff = [i for i, (a, b) in enumerate(zip(base, image)) if a != b]
    assert len(diff) == 52
    assert all(any(s <= i < e for s, e in RANGES.values()) for i in diff)
    for colour, (start, end) in RANGES.items():
        assert decode_here(image[start:end]) == STEPS[colour]
        assert sum(r * (ta + tb + tc) for r, _, ta, _, tb, _, tc in STEPS[colour]) == 662
    assert decode_here(base[slice(*RANGES[10])]) == BASE_10
    assert image[0xCF70] == 0xA5 and image[0xCF7A:0xCF7E] == bytes.fromhex("c999cc5c")


def test_the_drive_of_colour_10_is_the_hand_worked_run_list():
    assert sum(t for _, t in RUNS_10) == 540
    assert runs_here(STEPS[10][1:]) == RUNS_10
    assert fw.timeline(STEPS[10][1:]) == RUNS_10
    red = RED.read_bytes()
    k = red.find(b"PDName1")
    red_mask = red[k - 5:k - 1] + red[k - 4:k - 1]
    i = k + 16
    for _ in range(12):                        # skip red colours 0..11
        i += 1 + 7 * red[i]
    donor = tuple(tuple(red[i + 1 + 7 * n + j] ^ red_mask[j] for j in range(7))
                  for n in range(red[i]))
    assert len(donor) == 14 and runs_here(donor[1:]) == RUNS_10


def test_the_shipped_trial_image_is_what_the_tool_makes_now():
    image, _ = make_plan_image()
    assert SHIPPED.read_bytes() == image


def test_the_trial_image_has_its_own_ota_fingerprint():
    # Same size as FW_260923, so only the CRC tells them apart in the
    # (size, crc) catalog that names a board's 0x29 answer.
    base, image = BASE.read_bytes(), SHIPPED.read_bytes()
    assert len(base) == len(image)
    assert ota.crc16_modbus(base) != ota.crc16_modbus(image)
    assert fw.crc16_modbus(image) == ota.crc16_modbus(image)
    catalog = ota.bundled_images(FW_DIR)
    assert catalog[ota.image_fingerprint(image)] == "FW_261118B"
    images = sorted(FW_DIR.glob("FW_*/**/*.bin"))
    fingerprints = {ota.image_fingerprint(p.read_bytes()) for p in images}
    assert len(fingerprints) == len(images)


def test_the_cli_writes_once_and_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "x" / "trial.bin"
    argv = ["transplant", "--base", str(BASE),
            "--set", f"10={RED}:12", "--set", f"12={PURPLE}:12", "--out", str(out)]
    assert fw.main(argv) == 0
    assert out.read_bytes() == SHIPPED.read_bytes()
    text = capsys.readouterr().out
    assert "[1 0 91 1 31 0 0]" in text and "self-check passed" in text
    out.write_bytes(b"keep")
    assert fw.main(argv) == 2
    assert "exists" in capsys.readouterr().err
    assert out.read_bytes() == b"keep"
    assert fw.main(argv + ["--force"]) == 0
    assert out.read_bytes() == SHIPPED.read_bytes()


def test_dump_prints_every_colour(capsys):
    assert fw.main(["dump", str(BASE)]) == 0
    text = capsys.readouterr().out
    assert "total 662" in text and "colour 15 (0x0F)" in text


# ---- refusals, on small synthetic tables ----

KEY = bytes.fromhex("12345678")


def synth(entries, total, key=KEY):
    """A minimal image: header, total, key, 0x10, name, 16 entries."""
    head = (b"\x00" * 5 + b"\xa5\x26\x11\x19\x12\x00\x00\x01\x00\x01" + b"\xaa" * 4
            + b"\x00" * 13 + total.to_bytes(2, "little") + key + b"\x10"
            + b"PDName1SMP000001")
    return head + b"".join(fw.encode(e, key) for e in entries) + b"\xee" * 8


HOLD = (1, 0, 4, 0, 0, 0, 0)


def base_entries():
    # 3 steps each, 100 long: pad 10, drive 80, hold 10.
    return [[(1, 0, 10, 0, 0, 0, 0), (8, 1, 5, 2, 5, 0, 0), (1, 0, 10, 0, 0, 0, 0)]] * 16


def test_synthetic_transplant_pads_and_splits():
    base = synth(base_entries(), 100)
    donor = synth([[(1, 0, 2, 1, 3, 0, 0), (1, 6, 20, 0, 0, 0, 0)]] * 16, 25)
    image, changes = fw.transplant(base, [(3, donor, 0, "d")])
    assert fw.parse(image).entries[3].steps == (
        (1, 0, 77, 1, 3, 0, 0), (1, 6, 10, 0, 0, 0, 0), (1, 6, 10, 0, 0, 0, 0))
    assert image[-8:] == b"\xee" * 8 and len(image) == len(base)


def test_refuses_when_no_step_can_be_split():
    base = synth(base_entries(), 100)
    donor = synth([[(1, 0, 2, 1, 3, 0, 0), (2, 6, 20, 0, 0, 0, 0)]] * 16, 45)
    with pytest.raises(fw.WaveformError, match="cannot reach 3 steps"):
        fw.transplant(base, [(3, donor, 0, "d")])


def test_refuses_an_odd_hold_and_never_splits_step_0():
    with pytest.raises(fw.WaveformError, match="cannot reach"):
        fw.split_to([(1, 0, 4, 0, 0, 0, 0), (1, 6, 3, 0, 0, 0, 0)], 3)
    assert fw.split_to([(1, 0, 4, 0, 0, 0, 0), (1, 6, 4, 0, 0, 0, 0)], 3) == [
        (1, 0, 4, 0, 0, 0, 0), (1, 6, 2, 0, 0, 0, 0), (1, 6, 2, 0, 0, 0, 0)]


def test_level_0_holds_are_split_first_then_the_latest():
    steps = [(1, 0, 9, 0, 0, 0, 0), (1, 6, 10, 0, 0, 0, 0), HOLD, (1, 6, 10, 0, 0, 0, 0)]
    out = fw.split_to(steps, 5)
    assert out[2:4] == [(1, 0, 2, 0, 0, 0, 0)] * 2
    out = fw.split_to(steps, 7)       # 2,2 -> 1,1,1,1 then the latest level-6 hold
    assert fw.timeline(out) == fw.timeline(steps) and len(out) == 7
    assert all(fw.step_duration(s) > 0 for s in out)


def test_refuses_a_donor_longer_than_the_base_entry():
    base = synth(base_entries(), 100)
    donor = synth([[(1, 0, 1, 0, 0, 0, 0)] + [(1, 6, 1, 0, 0, 0, 0)] * 3] * 16, 4)
    with pytest.raises(fw.WaveformError, match="more than the base entry's 3"):
        fw.transplant(base, [(0, donor, 0, "d")])


def test_refuses_a_donor_that_lasts_longer_than_the_base_total():
    base = synth(base_entries(), 100)
    donor = synth([[(1, 0, 10, 0, 0, 0, 0), (1, 6, 100, 0, 0, 0, 0), HOLD]] * 16, 114)
    with pytest.raises(fw.WaveformError, match="longer than the target total 100"):
        fw.transplant(base, [(0, donor, 0, "d")])


def test_refuses_a_pad_over_255_and_an_unknown_step_0():
    with pytest.raises(fw.WaveformError, match="does not fit a byte"):
        fw.fit_step0([(1, 0, 10, 0, 0, 0, 0), (1, 6, 4, 0, 0, 0, 0)], 400)
    with pytest.raises(fw.WaveformError, match="not of the form"):
        fw.fit_step0([(1, 0, 0, 0, 0, 6, 122)], 662)
    with pytest.raises(fw.WaveformError, match="not of the form"):
        fw.fit_step0([(2, 0, 10, 1, 5, 0, 0)], 662)


def test_the_cli_refuses_a_bad_transplant_without_writing(tmp_path, capsys):
    base = tmp_path / "base.bin"
    base.write_bytes(synth(base_entries(), 100))
    donor = tmp_path / "donor(0a,0c).bin"
    donor.write_bytes(synth([[(1, 0, 2, 1, 3, 0, 0), (2, 6, 20, 0, 0, 0, 0)]] * 16, 45))
    out = tmp_path / "out.bin"
    assert fw.main(["transplant", "--base", str(base), "--set", f"3={donor}:0",
                    "--out", str(out)]) == 2
    assert "refused: cannot reach" in capsys.readouterr().err
    assert not out.exists()


def test_check_catches_an_edit_inside_a_replaced_entry():
    base = BASE.read_bytes()
    image, changes = make_plan_image()
    for offset in (0xD3F0 + 1 + 2, 0xD4B6 + 1 + 7 * 12 + 6):   # a time byte
        bad = bytearray(image)
        bad[offset] ^= 0x01
        with pytest.raises(fw.WaveformError):
            fw.check(base, bytes(bad), changes)


def test_check_catches_a_change_outside_the_entries():
    base = BASE.read_bytes()
    image, changes = make_plan_image()
    bad = bytearray(image)
    bad[-1] ^= 0xFF
    with pytest.raises(fw.WaveformError, match="outside the replaced entries"):
        fw.check(base, bytes(bad), changes)
    with pytest.raises(fw.WaveformError, match="size"):
        fw.check(base, image + b"\x00", changes)


# ---- parsing contract (review 2026-11-19) ----

def test_the_header_is_found_by_its_fixed_place_not_by_a5_26():
    image = synth(base_entries(), 100)
    assert fw.parse(image).header == 5
    later = bytearray(image)
    later[6] = 0x27                            # another year: still found
    assert fw.parse(bytes(later)).date.startswith("2027")
    moved = bytearray(image)
    moved[5] = 0x00
    with pytest.raises(fw.WaveformError, match="not the a5"):
        fw.parse(bytes(moved))
    with pytest.raises(fw.WaveformError, match="no single PDName1"):
        fw.parse(image + image)
    with pytest.raises(fw.WaveformError, match="no single PDName1"):
        fw.parse(b"\x00" * 100)
    with pytest.raises(fw.WaveformError, match="cut off"):
        fw.parse(image[30:])


def test_a_donor_whose_colours_do_not_last_its_total_is_refused():
    # A damaged first time in the red donor's colour 12 would be absorbed
    # by the step-0 padding; validating the donor first catches it.
    red = bytearray(RED.read_bytes())
    table = fw.parse(bytes(red))
    entry = table.entries[12]
    steps = list(entry.steps)
    steps[0] = (1, 0, 41, 1, 31, 0, 0)
    red[entry.start:entry.end] = fw.encode(steps, table.key)
    with pytest.raises(fw.WaveformError, match="colour 12 lasts 612, its table total is 611"):
        fw.transplant(BASE.read_bytes(), [(10, bytes(red), 12, "red")])
    base = bytearray(BASE.read_bytes())
    t = fw.parse(bytes(base))
    e = t.entries[3]
    base[e.start:e.end] = fw.encode([(1, 0, 1) + e.steps[0][3:]] + list(e.steps[1:]), t.key)
    with pytest.raises(fw.WaveformError, match="base: colour 3 lasts"):
        fw.transplant(bytes(base), [(10, RED.read_bytes(), 12, "red")])


def test_the_pad_is_never_shortened():
    # Donor lasts 110 against a base total of 100: shrinking its step-0
    # pad from 20 to 10 would fit, but the rule is lengthen only.
    with pytest.raises(fw.WaveformError, match="lasts 110, longer than the target total 100"):
        fw.fit_step0([(1, 0, 20, 0, 0, 0, 0), (1, 6, 90, 0, 0, 0, 0)], 100)
    assert fw.fit_step0([(1, 0, 20, 0, 0, 0, 0), (1, 6, 80, 0, 0, 0, 0)], 100)[0] == (
        1, 0, 20, 0, 0, 0, 0)


def test_encode_refuses_a_bad_key_and_transplant_a_wrong_size(monkeypatch):
    with pytest.raises(fw.WaveformError, match="not 4 bytes"):
        fw.encode([(1, 0, 1, 0, 0, 0, 0)], b"\x01\x02\x03")
    with pytest.raises(fw.WaveformError, match="not 4 bytes"):
        fw.encode([(1, 0, 1, 0, 0, 0, 0)], b"\x01\x02\x03\x04\x05")
    monkeypatch.setattr(fw, "split_to", lambda steps, count: list(steps))
    with pytest.raises(fw.WaveformError, match="would take 99 bytes, not the base entry's 106"):
        make_plan_image()


# ---- output safety (review 2026-11-19) ----

def transplant_argv(out, base=BASE):
    return ["transplant", "--base", str(base),
            "--set", f"10={RED}:12", "--set", f"12={PURPLE}:12", "--out", str(out)]


def leftovers(folder):
    return sorted(p.name for p in folder.iterdir())


def test_the_output_may_never_be_an_input(tmp_path, capsys):
    base = tmp_path / "base.bin"
    base.write_bytes(BASE.read_bytes())
    for out in (base, tmp_path / "sub" / ".." / "base.bin"):
        for extra in ([], ["--force"]):
            assert fw.main(transplant_argv(out, base) + extra) == 2
            assert "same file as the input" in capsys.readouterr().err
    link = tmp_path / "link.bin"
    os.link(base, link)                         # another name, same file
    assert fw.main(transplant_argv(link, base) + ["--force"]) == 2
    assert "same file as the input" in capsys.readouterr().err
    with pytest.raises(fw.WaveformError, match="same file"):
        fw.check_out(RED, [BASE, RED, PURPLE], force=True)
    assert base.read_bytes() == BASE.read_bytes()


def test_the_output_in_fw_needs_a_trial_folder(tmp_path, monkeypatch, capsys):
    root = tmp_path / "FW"
    monkeypatch.setattr(fw, "FW_ROOT", root)
    (root / "FW_991231").mkdir(parents=True)
    for out in (root / "FW_991231" / "x.bin", root / "x.bin",
                root / "FW_991231" / "sub" / "x.bin"):
        assert fw.main(transplant_argv(out)) == 2
        assert "write the TRIAL file first" in capsys.readouterr().err
        assert not out.exists()
    (root / "FW_991231" / "TRIAL").write_text("trial")
    assert fw.main(transplant_argv(root / "FW_991231" / "sub" / "x.bin")) == 0
    assert (root / "FW_991231" / "sub" / "x.bin").read_bytes() == SHIPPED.read_bytes()
    assert not (root / "TRIAL").exists()        # never written for the user
    assert fw.main(transplant_argv(tmp_path / "elsewhere.bin")) == 0
    # The real FW/ tree: the shipped image's folder has its TRIAL.
    fw.check_out(REPO / "FW" / "FW_261118B" / "new.bin", [BASE], False, REPO / "FW")
    with pytest.raises(fw.WaveformError, match="TRIAL"):
        fw.check_out(REPO / "FW" / "FW_260923" / "new.bin", [BASE], False, REPO / "FW")


def test_a_failed_write_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out" / "trial.bin"

    def boom(*args, **kwargs):
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(fw.os, "fsync", boom)
        assert fw.main(transplant_argv(out)) == 2
    assert "refused: disk full" in capsys.readouterr().err
    assert leftovers(out.parent) == []
    out.write_bytes(b"keep")
    with monkeypatch.context() as m:
        m.setattr(fw.os, "replace", boom)
        assert fw.main(transplant_argv(out) + ["--force"]) == 2
    assert out.read_bytes() == b"keep" and leftovers(out.parent) == ["trial.bin"]


def test_a_file_that_appears_after_the_check_is_not_clobbered(tmp_path):
    out = tmp_path / "trial.bin"
    out.write_bytes(b"theirs")
    with pytest.raises(FileExistsError):
        fw.write_atomic(out, b"ours", force=False)
    assert out.read_bytes() == b"theirs" and leftovers(tmp_path) == ["trial.bin"]
    fw.write_atomic(out, b"ours", force=True)
    assert out.read_bytes() == b"ours" and leftovers(tmp_path) == ["trial.bin"]


def test_without_hard_links_it_refuses_unless_forced(tmp_path, monkeypatch, capsys):
    # No create-then-replace fallback (review 2026-11-19): it would leave
    # a gap in which another file could be clobbered.
    def no_links(*args, **kwargs):
        raise OSError("hard links not supported")
    monkeypatch.setattr(fw.os, "link", no_links)
    out = tmp_path / "out" / "trial.bin"
    assert fw.main(transplant_argv(out)) == 2
    err = capsys.readouterr().err
    assert "refused: this volume cannot create trial.bin without a race" in err
    assert "write elsewhere or use --force" in err
    assert leftovers(out.parent) == []
    assert fw.main(transplant_argv(out) + ["--force"]) == 0
    assert out.read_bytes() == SHIPPED.read_bytes() and leftovers(out.parent) == ["trial.bin"]


def test_the_trial_folder_is_the_nearest_fw_folder(tmp_path, monkeypatch, capsys):
    # ui/updater.py is_trial looks at the NEAREST FW_ ancestor; a TRIAL
    # further out does not keep FW_1/FW_9/x.bin from becoming the default.
    root = tmp_path / "FW"
    monkeypatch.setattr(fw, "FW_ROOT", root)
    inner = root / "FW_1" / "FW_9"
    inner.mkdir(parents=True)
    (root / "FW_1" / "TRIAL").write_text("outer")
    assert fw.main(transplant_argv(inner / "x.bin")) == 2
    assert "write the TRIAL file first" in capsys.readouterr().err
    assert fw.main(transplant_argv(inner / "sub" / "x.bin")) == 2
    capsys.readouterr()
    (inner / "TRIAL").write_text("inner")
    assert fw.main(transplant_argv(inner / "sub" / "x.bin")) == 0
    from ui import updater as updater_mod
    assert updater_mod.is_trial(inner / "sub" / "x.bin")
    (root / "FW_1" / "TRIAL").unlink()
    assert fw.main(transplant_argv(inner / "y.bin")) == 0    # inner alone is enough


def test_validate_refuses_bad_steps_and_every_vendor_image_passes():
    for path in sorted(FW_DIR.glob("FW_*/**/*.bin")):
        fw.validate(path.read_bytes(), path.name)
    base = base_entries()
    for bad, why in (((0, 0, 10, 0, 0, 0, 0), "repeat 0 or lasts 0"),
                     ((1, 0, 0, 0, 0, 0, 0), "repeat 0 or lasts 0"),
                     ((1, 7, 10, 0, 0, 0, 0), "level outside 0..6"),
                     ((1, 0, 5, 0, 0, 9, 5), "level outside 0..6")):
        entries = [list(e) for e in base]
        entries[4] = list(base[4]) + [bad]
        with pytest.raises(fw.WaveformError, match=why):
            fw.validate(synth(entries, 100), "donor")


def test_a_crafted_count_cannot_swallow_bytes_after_the_table():
    # Colour 15 claims one step more than it has: the 7 bytes after the
    # table decode as a zero step and are refused, not transplanted.
    table = synth(base_entries(), 100)[:-8]          # drop synth's tail
    image = bytearray(table + KEY + KEY[1:])        # 7 bytes that unmask to 0
    t = fw.parse(table)
    image[t.entries[15].start] += 1
    with pytest.raises(fw.WaveformError, match="colour 15 step 3 .* repeat 0 or lasts 0"):
        fw.validate(bytes(image), "donor")


def test_a_folder_that_cannot_be_made_is_refused(tmp_path, capsys):
    (tmp_path / "file.txt").write_text("x")
    assert fw.main(transplant_argv(tmp_path / "file.txt" / "sub" / "x.bin")) == 2
    assert capsys.readouterr().err.startswith("refused: ")


# ---- --total and the plan C image FW_261119 (2026-11-20): the vendor's
# FW_261118 program, which ignores a waveform saved in the board's SPI
# flash, carrying FW_260923's table (total 662) and the 11/9 pinks ----

PROG_1118 = TWO
PLAN_C_OUT = FW_DIR / "FW_261119" / "mcb_1118prog_seq0923_pinks_no13.bin"
PLAN_C_SHA256 = "364152af818e88ba1e000abfe12d915388fe75c27becc1aa6cc3be0defef7d50"
PLAN_C_SETS = ([(c, BASE, c) for c in range(16) if c not in (10, 12)]
               + [(10, RED, 12), (12, PURPLE, 12)])
TOTAL_BYTES_C = (0xCAFB, 0xCAFD)           # the header's u16 total in FW_261118
ENTRIES_C = (0xCB12, 0xD196)               # its 16 entries, count bytes included
COMMON_C = ((26, 6, 2, 1, 2, 0, 0), (1, 6, 10, 0, 0, 0, 0), (5, 6, 2, 1, 2, 0, 0),
            (1, 6, 10, 0, 0, 0, 0), (5, 6, 2, 1, 2, 0, 0), (1, 6, 10, 0, 0, 0, 0),
            (3, 1, 4, 6, 4, 0, 0), (1, 6, 10, 0, 0, 0, 0), (3, 1, 4, 6, 4, 0, 0),
            (1, 0, 4, 0, 0, 0, 0))
STEPS_C = {
    10: ((1, 0, 91, 1, 31, 0, 0),) + COMMON_C
    + ((8, 6, 6, 0, 5, 3, 17), (12, 0, 0, 0, 0, 0, 6), (1, 0, 0, 0, 8, 0, 0)),
    12: ((1, 0, 97, 1, 25, 0, 0),) + COMMON_C
    + ((4, 6, 3, 0, 5, 3, 16), (12, 0, 0, 0, 6, 0, 11), (2, 0, 2, 0, 0, 0, 0)),
}


def make_plan_c_image(total=662):
    return fw.transplant(PROG_1118.read_bytes(),
                         [(c, p.read_bytes(), dc, p.name) for c, p, dc in PLAN_C_SETS],
                         total)


def table_here(image):
    """(total, [(start, end, steps)] * 16) decoded without the tool."""
    k = image.find(b"PDName1")
    key = image[k - 5:k - 1]
    mask = key + key[1:]
    i, entries = k + 16, []
    for _ in range(16):
        count = image[i]
        steps = tuple(tuple(image[i + 1 + 7 * n + j] ^ mask[j] for j in range(7))
                      for n in range(count))
        entries.append((i, i + 1 + 7 * count, steps))
        i += 1 + 7 * count
    return int.from_bytes(image[k - 7:k - 5], "little"), entries


def test_the_base_program_tables_line_up_as_planned():
    total, prog = table_here(PROG_1118.read_bytes())
    assert total == 1197
    assert [len(s) for _, _, s in prog] == [14, 15, 13, 25, 14, 17, 14, 17,
                                            14, 13, 14, 13, 14, 12, 12, 15]
    _, seq = table_here(BASE.read_bytes())
    for c in range(16):
        if c not in (10, 12):
            assert len(seq[c][2]) == len(prog[c][2])
    assert (prog[0][0], prog[15][1]) == ENTRIES_C


def test_the_plan_c_image_is_pinned():
    image, prog = PLAN_C_OUT.read_bytes(), PROG_1118.read_bytes()
    assert len(image) == len(prog) == 63144
    assert hashlib.sha256(image).hexdigest() == PLAN_C_SHA256
    assert ota.crc16_modbus(image) == 0x0B6F and ota.crc16_modbus(prog) == 0xB567
    diff = [i for i, (a, b) in enumerate(zip(prog, image)) if a != b]
    assert len(diff) == 254 and (diff[0], diff[1]) == (0xCAFB, 0xCAFC)
    assert all(TOTAL_BYTES_C[0] <= i < TOTAL_BYTES_C[1] or ENTRIES_C[0] <= i < ENTRIES_C[1]
               for i in diff)
    assert image[slice(*TOTAL_BYTES_C)] == (662).to_bytes(2, "little")
    # Outside the table and the total: the FW_261118 bytes, program included.
    assert image[:TOTAL_BYTES_C[0]] == prog[:TOTAL_BYTES_C[0]]
    assert image[TOTAL_BYTES_C[1]:ENTRIES_C[0]] == prog[TOTAL_BYTES_C[1]:ENTRIES_C[0]]
    assert image[ENTRIES_C[1]:] == prog[ENTRIES_C[1]:]
    total, entries = table_here(image)
    _, seq = table_here(BASE.read_bytes())
    _, old = table_here(prog)
    assert total == 662
    for c, (start, end, steps) in enumerate(entries):
        assert (start, end) == old[c][:2]                    # nothing moved
        assert sum(r * (a + b + d) for r, _, a, _, b, _, d in steps) == 662
        if c in (10, 12):
            assert steps == STEPS_C[c]
        else:
            assert steps == seq[c][2]                        # FW_260923 steps, exactly


def test_the_plan_c_image_is_what_the_tool_makes_now():
    image, changes = make_plan_c_image()
    assert PLAN_C_OUT.read_bytes() == image
    copied = sorted(c.colour for c in changes if c.after == c.donor_steps)
    assert copied == [c for c in range(16) if c not in (10, 12)]


def test_plan_c_has_its_own_fingerprint_and_name():
    prog, image = PROG_1118.read_bytes(), PLAN_C_OUT.read_bytes()
    assert len(prog) == len(image)
    assert ota.crc16_modbus(prog) != ota.crc16_modbus(image)
    catalog = ota.bundled_images(FW_DIR)
    assert catalog[ota.image_fingerprint(image)] == "FW_261119"
    assert catalog[ota.image_fingerprint(prog)] == "FW_261118"


def test_total_needs_every_colour_that_does_not_last_it():
    sets = [(10, RED.read_bytes(), 12, "red"), (12, PURPLE.read_bytes(), 12, "purple")]
    with pytest.raises(fw.WaveformError,
                       match=r"colours \[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 14, 15\] "
                             r"last 1197"):
        fw.transplant(PROG_1118.read_bytes(), sets, 662)
    with pytest.raises(fw.WaveformError, match="does not fit"):
        make_plan_c_image(0x10000)
    with pytest.raises(fw.WaveformError, match="longer than the target total 600"):
        make_plan_c_image(600)


def test_total_rewrites_the_header_total_pads_and_copies():
    base = synth(base_entries(), 100)
    # Already lasting 120, with a step 0 the padding rule would refuse:
    at_n = [(1, 0, 0, 0, 0, 6, 10), (10, 1, 5, 2, 5, 0, 0), (1, 6, 10, 0, 0, 0, 0)]
    padded = [(1, 0, 5, 1, 0, 0, 0), (10, 1, 5, 2, 5, 0, 0), (1, 6, 10, 0, 0, 0, 0)]
    donor_a, donor_b = synth([at_n] * 16, 120), synth([padded] * 16, 115)
    sets = [(0, donor_a, 0, "a")] + [(c, donor_b, c, "b") for c in range(1, 16)]
    image, changes = fw.transplant(base, sets, 120)
    t = fw.parse(image)
    assert t.total == 120 and [e.duration for e in t.entries] == [120] * 16
    assert t.entries[0].steps == tuple(at_n)
    assert t.entries[1].steps[0] == (1, 0, 10, 1, 0, 0, 0)
    start, end = fw.total_range(t)
    assert image[start:end] == (120).to_bytes(2, "little")
    assert image[:start] == base[:start] and image[end:t.entries[0].start] == \
        base[end:t.entries[0].start]
    # --total equal to the base total needs no extra colours.
    same, _ = fw.transplant(base, [], 100)
    assert same == base


def test_an_entry_already_at_the_target_is_copied_untouched():
    steps = [(1, 0, 0, 0, 0, 6, 122), (1, 6, 100, 0, 0, 0, 0)]
    assert fw.fit_step0(steps, 222) == steps
    with pytest.raises(fw.WaveformError, match="not of the form"):
        fw.fit_step0(steps, 230)


def test_check_with_total_allows_only_the_total_bytes_in_the_header():
    prog = PROG_1118.read_bytes()
    image, changes = make_plan_c_image()
    t = fw.parse(prog)
    for offset in (t.header, t.header + 3, t.header + 10, t.name - 8, t.name - 5,
                   t.name - 1, t.name, ENTRIES_C[0] - 1, ENTRIES_C[1], 0x100):
        bad = bytearray(image)
        bad[offset] ^= 0x01
        with pytest.raises(fw.WaveformError):
            fw.check(prog, bytes(bad), changes, 662)
    with pytest.raises(fw.WaveformError, match="outside the replaced entries"):
        fw.check(prog, image, changes)                # total changed, not allowed
    bad = bytearray(image)
    bad[TOTAL_BYTES_C[0]] ^= 0x01                     # the total, but wrong
    with pytest.raises(fw.WaveformError):
        fw.check(prog, bytes(bad), changes, 662)


def test_the_cli_takes_total(tmp_path, capsys):
    out = tmp_path / "c.bin"
    argv = ["transplant", "--base", str(PROG_1118), "--total", "662", "--out", str(out)]
    for c, p, dc in PLAN_C_SETS:
        argv += ["--set", f"{c}={p}:{dc}"]
    assert fw.main(argv) == 0
    assert out.read_bytes() == PLAN_C_OUT.read_bytes()
    text = capsys.readouterr().out
    assert "header total 1197 -> 662  bytes 0xCAFB-0xCAFC" in text
    assert "copied as is" in text and "[1 0 91 1 31 0 0]" in text
    short = argv[:5] + ["--out", str(tmp_path / "d.bin"), "--set", f"10={RED}:12"]
    assert fw.main(short) == 2
    assert "not given by --set" in capsys.readouterr().err


# ---- retime --end-align: plan D, FW_261119D (2026-11-20) - plan C with
# each colour's trailing rest moved to right after the shake ----

PLAN_D_OUT = FW_DIR / "FW_261119D" / "mcb_1118prog_pinks_no13_endalign.bin"
PLAN_D_SHA256 = "c07909b5fad5e6fdbd3a00efd119d569ef649c5b28726a981df8041f6ab7e067"
MOVED_D = {0: 1, 1: 1, 2: 1, 4: 1, 8: 2, 9: 1, 10: 2, 11: 1, 12: 2}   # colour: steps
INSERT_D = {c: 10 for c in MOVED_D}
INSERT_D[1] = 11                    # yellow: its shake ends after step 10, at 364
# Tick where the last non-zero drive ends, colour 0..15 (10 ms ticks).
LAST_DRIVE_C = [630, 578, 590, 652, 648, 654, 659, 651, 510, 595, 582, 602, 454, 661, 655, 660]
LAST_DRIVE_D = [662, 662, 662, 652, 662, 654, 659, 651, 662, 661, 662, 662, 662, 661, 655, 660]


def last_drive_here(steps):
    """Independent of the tool: end tick of the last non-zero level."""
    t = last = 0
    for rep_, a, ta, b, tb, c, tc in steps:
        for _ in range(rep_):
            for level, time in ((a, ta), (b, tb), (c, tc)):
                if time and level:
                    last = t + time
                t += time
    return last


def test_the_last_drive_ends_as_measured_before_and_after():
    _, c_entries = table_here(PLAN_C_OUT.read_bytes())
    _, d_entries = table_here(PLAN_D_OUT.read_bytes())
    assert [last_drive_here(s) for _, _, s in c_entries] == LAST_DRIVE_C
    assert [last_drive_here(s) for _, _, s in d_entries] == LAST_DRIVE_D


def test_the_plan_d_image_is_pinned():
    image, plan_c = PLAN_D_OUT.read_bytes(), PLAN_C_OUT.read_bytes()
    assert len(image) == len(plan_c) == 63144
    assert hashlib.sha256(image).hexdigest() == PLAN_D_SHA256
    assert ota.crc16_modbus(image) == 0xC9A1
    diff = [i for i, (a, b) in enumerate(zip(plan_c, image)) if a != b]
    assert len(diff) == 156 and (diff[0], diff[-1]) == (0xCB59, 0xD081)
    _, c_entries = table_here(plan_c)
    _, d_entries = table_here(image)
    assert image[:ENTRIES_C[0]] == plan_c[:ENTRIES_C[0]]          # program, header
    assert image[ENTRIES_C[1]:] == plan_c[ENTRIES_C[1]:]
    for c, ((start, end, old), (start2, end2, new)) in enumerate(zip(c_entries, d_entries)):
        assert (start, end) == (start2, end2)
        assert sorted(new) == sorted(old)                         # a permutation
        assert sum(r * (a + b + d) for r, _, a, _, b, _, d in new) == 662
        if c not in MOVED_D:
            assert image[start:end] == plan_c[start:end]          # byte-identical
            continue
        n, at = MOVED_D[c], INSERT_D[c]
        assert new[at:at + n] == old[len(old) - n:]               # the trailing rest
        assert new[:at] + new[at + n:] == old[:len(old) - n]      # the rest, in order
        assert all(level == 0 for step in new[at:at + n]
                   for level, time in zip(step[1::2], step[2::2]) if time)
        ends = [sum(r * (a + b + d) for r, _, a, _, b, _, d in new[:i + 1])
                for i in range(len(new))]
        assert ends[at - 1] == (364 if c == 1 else 354)


def test_the_plan_d_image_is_what_the_tool_makes_now():
    image, rows, shake_end = fw.retime(PLAN_C_OUT.read_bytes())
    assert image == PLAN_D_OUT.read_bytes() and shake_end == 354
    assert {r.colour: r.moved for r in rows if r.moved} == MOVED_D
    assert {r.colour: r.insert_at for r in rows if r.moved} == INSERT_D
    assert [r.shake_end for r in rows] == [354, 364, 354, 354, 354, 426, 426, 426,
                                           354, 354, 354, 354, 354, 354, 354, 354]
    assert [r.rest for r in rows] == [32, 84, 72, 0, 14, 0, 0, 0, 152, 66, 80, 60, 208,
                                      0, 0, 0]


def test_plan_d_has_its_own_fingerprint_and_name():
    catalog = ota.bundled_images(FW_DIR)
    prints = [ota.image_fingerprint(p.read_bytes()) for p in (PROG_1118, PLAN_C_OUT, PLAN_D_OUT)]
    assert len({size for size, _ in prints}) == 1 and len({crc for _, crc in prints}) == 3
    assert [catalog[f] for f in prints] == ["FW_261118", "FW_261119", "FW_261119D"]


def test_rest_suffix_and_end_align_rules():
    drive, rest = (3, 1, 4, 6, 4, 0, 0), (1, 0, 9, 0, 7, 0, 0)
    assert fw.is_rest((12, 0, 0, 0, 0, 0, 6)) and fw.is_rest((2, 6, 0, 0, 3, 0, 0))
    assert not fw.is_rest((2, 0, 0, 4, 2, 0, 10))     # red's: rest inside a drive step
    assert fw.rest_suffix([drive, drive]) == 2           # none
    assert fw.rest_suffix([drive, rest, drive, rest, rest]) == 3
    steps = [(1, 0, 10, 0, 0, 0, 0), drive, (1, 6, 7, 0, 0, 0, 0), rest]
    assert fw.end_align(steps, 34) == ([steps[0], drive, rest, steps[2]], 2, 1)
    assert fw.end_align(steps, 20) == ([steps[0], drive, rest, steps[2]], 2, 1)
    assert fw.end_align([drive, drive], 10) == ([drive, drive], 2, 0)
    with pytest.raises(fw.WaveformError, match="at or after the trailing rest"):
        fw.end_align(steps, 40)                          # reached at step 2 = just before it
    with pytest.raises(fw.WaveformError, match="at or after the trailing rest"):
        fw.end_align(steps, 999)
    with pytest.raises(fw.WaveformError, match="rest only"):
        fw.end_align([rest, rest], 5)


def test_the_common_shake_end_needs_a_majority():
    a = [(1, 0, 10, 0, 0, 0, 0), (10, 1, 5, 2, 5, 0, 0), (1, 6, 10, 0, 0, 0, 0)]
    b = [(1, 0, 10, 0, 0, 0, 0), (10, 1, 4, 2, 5, 0, 0), (1, 6, 20, 0, 0, 0, 0)]
    with pytest.raises(fw.WaveformError, match="no single most common shake end"):
        fw.retime(synth([a] * 8 + [b] * 8, 120), after_step=1)
    image, rows, end = fw.retime(synth([a] * 9 + [b] * 7, 120), after_step=1)
    assert end == 110 and image == synth([a] * 9 + [b] * 7, 120)   # nothing to move


def test_check_retime_catches_reordered_drive_and_outside_bytes():
    plan_c = PLAN_C_OUT.read_bytes()
    image, rows, end = fw.retime(plan_c)
    t = fw.parse(image)
    e = t.entries[0]
    steps = list(e.steps)
    steps[11], steps[12] = steps[12], steps[11]          # two driving steps swapped
    bad = bytearray(image)
    bad[e.start:e.end] = fw.encode(steps, t.key)
    with pytest.raises(fw.WaveformError):
        fw.check_retime(plan_c, bytes(bad), rows, end)
    for offset in (0x100, t.header + 10, ENTRIES_C[1]):
        bad = bytearray(image)
        bad[offset] ^= 0x01
        with pytest.raises(fw.WaveformError):
            fw.check_retime(plan_c, bytes(bad), rows, end)
    with pytest.raises(fw.WaveformError):
        fw.check_retime(plan_c, image, rows, 300)        # not right after this end


def test_the_cli_retimes(tmp_path, capsys):
    out = tmp_path / "d.bin"
    argv = ["retime", "--base", str(PLAN_C_OUT), "--end-align", "--out", str(out)]
    assert fw.main(argv) == 0
    assert out.read_bytes() == PLAN_D_OUT.read_bytes()
    text = capsys.readouterr().out
    assert "shake end 354 (tick after step 9, most common)" in text
    assert "colours whose own shake ends later: 1: 364, 5: 426, 6: 426, 7: 426" in text
    assert "454 -> 662" in text and "untouched, no trailing rest" in text
    assert fw.main(argv) == 2                            # exists
    assert "exists" in capsys.readouterr().err
    assert fw.main(argv + ["--force", "--shake-end", "999"]) == 2
    assert "at or after the trailing rest" in capsys.readouterr().err
