"""The sweep's edge (conductor/sequence.py, 2026-10-22): a swept change
whose border is a wave, a fray or both, from a seed - not a straight line.

Straight must stay exactly today's: integer ranks, byte-identical delay
tables. The others move each scale's rank by up to the edge's width,
deterministically, and the last scale still starts exactly on the span.
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conductor import export3d  # noqa: E402
from conductor.look import LookMap  # noqa: E402
from conductor.sequence import (EDGE_DEFAULTS, EDGES, NO_DELAY,  # noqa: E402
                                SEQUENCES, _raw_ranks, clean_edge,
                                compile_delays, edge_ranks, edge_stored, h01,
                                hash32, rank_set, ranks, span_s, vnoise)

SWEPT = [s for s in SEQUENCES if s != "natural"]
STYLES = [e for e in EDGES if e != "straight"]


def garment(rows=10, cols=8, sides=("front", "back")) -> LookMap:
    """A rectangle of scales per side, 60 sockets to a board."""
    lines = ["side,row,col,board_no,socket,label"]
    n = 0
    for side in sides:
        for row in range(rows):
            for col in range(1, cols + 1):
                lines.append(f"{side},{row},{col},{17 + n // 60},{n % 60 + 1},")
                n += 1
    return LookMap.parse(io.StringIO("\n".join(lines) + "\n"), name="g.csv")


def sweep(sequence="top_down", **edge) -> dict:
    return dict({"sequence": sequence, "span_s": 2.0}, **edge)


def frames(tables) -> "list[int]":
    return [v for t in tables.values() for v in struct.unpack(">64H", t)
            if v != NO_DELAY]


def test_straight_is_todays_ranks_and_tables_byte_for_byte():
    m = garment()
    for seq in SEQUENCES:
        straight = edge_ranks(m, sweep(seq, edge="straight", edge_rows=9,
                                       edge_seed=77))
        assert straight == ranks(m, seq)
        assert all(isinstance(r, int) for r in straight.values())
        for edge in (None, {}, {"edge": "straight", "edge_seed": 5},
                     {"edge": "nonsense"}):
            assert compile_delays(m, seq, 2.0, edge=edge) == \
                compile_delays(m, seq, 2.0)
            assert span_s(m, seq, 2.0, edge=edge) == span_s(m, seq, 2.0)


def test_a_natural_sequence_ignores_the_edge():
    m = garment()
    assert edge_ranks(m, sweep("natural", edge="wave")) == ranks(m, "natural")
    assert compile_delays(m, "natural", 2.0, edge={"edge": "fray"}) == \
        compile_delays(m, "natural", 2.0)
    assert span_s(m, "natural", 2.0, edge={"edge": "fray"}) == 0.0


@pytest.mark.parametrize("seq", SWEPT)
@pytest.mark.parametrize("style", STYLES)
def test_every_edge_style_moves_the_ranks_and_does_so_the_same_way_twice(seq, style):
    m = garment()
    got = edge_ranks(m, sweep(seq, edge=style))
    assert got != ranks(m, seq)
    assert got == edge_ranks(m, sweep(seq, edge=style))        # deterministic
    assert set(got) == set(ranks(m, seq))
    assert min(got.values()) == 0                              # rebased
    # The styles are not one another.
    others = [edge_ranks(m, sweep(seq, edge=o)) for o in STYLES if o != style]
    assert all(got != other for other in others)


def test_the_seed_fixes_the_shape():
    m = garment()
    for style in STYLES:
        one = edge_ranks(m, sweep(edge=style, edge_seed=41))
        assert one == edge_ranks(m, sweep(edge=style, edge_seed=41))
        assert one != edge_ranks(m, sweep(edge=style, edge_seed=42))


def test_the_back_has_a_border_of_its_own():
    """k = seed + 1000 on the back: the same row is not frayed the same
    way behind the model as in front of it."""
    m = garment()
    got = edge_ranks(m, sweep(edge="fray", edge_rows=3))
    front = [got[("front", 4, c)] for c in range(1, 9)]
    back = [got[("back", 4, c)] for c in range(1, 9)]
    assert front != back


def offsets(m, seq, edge) -> "list[float]":
    """Each scale's edge offset, less the (common) rebasing constant."""
    raw = _raw_ranks(m, seq)
    got = edge_ranks(m, dict(edge, sequence=seq))
    return [got[p] - raw[p] for p in raw]


def test_a_fixed_width_bounds_the_offset():
    m = garment()
    for width in (0.5, 3.0, 6.0, 24.0):
        for style, reach in (("wave", 1.0), ("fray", 1.0), ("wave_fray", 1.5)):
            spread = offsets(m, "top_down", {"edge": style, "edge_rows": width})
            assert max(spread) - min(spread) <= 2 * reach * width + 1e-9


def test_a_random_width_stays_between_its_minimum_and_maximum():
    m = garment(rows=20, cols=16)
    for style, reach in (("wave", 1.0), ("fray", 1.0), ("wave_fray", 1.5)):
        # Never wider than the maximum...
        random = {"edge": style, "edge_random": True, "edge_rows": 5,
                  "edge_min_rows": 1}
        spread = offsets(m, "left_right", random)
        assert max(spread) - min(spread) <= 2 * reach * 5 + 1e-9
        # ...and a minimum equal to the maximum IS the fixed width.
        assert edge_ranks(m, sweep("left_right", edge=style, edge_random=True,
                                   edge_rows=4, edge_min_rows=4)) == \
            edge_ranks(m, sweep("left_right", edge=style, edge_rows=4))
        # A varying width is not the fixed maximum.
        assert edge_ranks(m, sweep("left_right", **random)) != \
            edge_ranks(m, sweep("left_right", edge=style, edge_rows=5))
    # Zero width: the straight ranks, as floats.
    zero = edge_ranks(m, sweep(edge="wave_fray", edge_random=True,
                               edge_rows=0, edge_min_rows=0))
    assert zero == ranks(m, "top_down")


@pytest.mark.parametrize("seq", SWEPT)
@pytest.mark.parametrize("style", STYLES)
def test_the_last_scale_still_lands_exactly_on_the_span(seq, style):
    m = garment()
    for span in (0.5, 1.0, 2.37, 12.0):
        for random in (False, True):
            edge = {"edge": style, "edge_random": random, "edge_rows": 6,
                    "edge_min_rows": 2, "edge_seed": 5}
            got = frames(compile_delays(m, seq, span, edge=edge))
            assert min(got) == 0
            assert max(got) == round(span / 0.01)     # inside the span, not added
            assert span_s(m, seq, span, edge=edge) == round(span, 2)


def test_an_edge_gives_even_a_one_row_garment_something_to_time():
    m = garment(rows=1, cols=12, sides=("front",))
    assert span_s(m, "top_down", 2.0) == 0.0
    assert span_s(m, "top_down", 2.0, edge={"edge": "fray"}) == 2.0
    assert max(frames(compile_delays(m, "top_down", 2.0,
                                     edge={"edge": "fray"}))) == 200


def test_hash32_is_one_implementation():
    assert export3d.hash32 is hash32


def test_the_reference_numbers_are_pinned():
    """The JS ports (conductor/web/index.html, conductor/web/sim/) must
    give exactly these; tools/make_goldens.py's edge_ranks cases check
    them on whole garments. A change here is a change of every shape."""
    assert hash32(0, 0) == 2703953877
    assert h01(-1, 3) == hash32(0xFFFFFFFF, 3) / 4294967296.0     # masked
    assert vnoise(0.0, 0.0, 0) == h01(0, 0)                         # a lattice point
    assert vnoise(2.5, 1.25, 7) == 0.6241842873714631
    got = edge_ranks(garment(rows=3, cols=3, sides=("front",)),
                     sweep(edge="wave_fray", edge_rows=2, edge_seed=3))
    assert got == {("front", 0, 1): 1.5876534924357364,
                   ("front", 0, 2): 3.498429386268211,
                   ("front", 0, 3): 2.8562001108388735,
                   ("front", 1, 1): 1.3718965348440328,
                   ("front", 1, 2): 1.276697447910493,
                   ("front", 1, 3): 3.204178990102479,
                   ("front", 2, 1): 0.0,
                   ("front", 2, 2): 0.23729424406536515,
                   ("front", 2, 3): 1.772140802646921}


def test_clean_edge_and_what_is_stored():
    assert clean_edge(None) == EDGE_DEFAULTS
    assert clean_edge({"edge": ["wave"]})["edge"] == "straight"   # unhashable junk
    assert clean_edge({"edge_rows": -2})["edge_rows"] == 0.0
    assert clean_edge({"edge_rows": 0})["edge_min_rows"] == 0.0   # min <= max
    assert clean_edge({"edge_seed": -4})["edge_seed"] == 0
    assert clean_edge({"edge_seed": float("inf")})["edge_seed"] == 1
    assert clean_edge({"edge_rows": 2.25})["edge_rows"] == 2.2
    assert edge_stored({"edge": "straight", "edge_rows": 6.0}) == {}
    assert edge_stored({"edge": "wave"}) == dict(EDGE_DEFAULTS, edge="wave")


def test_rank_set_names_the_sequence_and_all_five_fields_but_not_the_span():
    assert rank_set(sweep(edge="straight")) == ""
    assert rank_set(sweep("natural", edge="wave")) == ""
    assert rank_set(sweep(edge="wave", span_s=0)) == ""
    assert rank_set(sweep(edge="wave")) == "top_down/wave/6/fixed/1/1"
    assert rank_set(sweep(edge="wave", span_s=9)) == "top_down/wave/6/fixed/1/1"
    assert rank_set(sweep(edge="fray", edge_random=True, edge_min_rows=3,
                          edge_rows=7.5)) == "top_down/fray/7.5/random/3/1"
    # The page keys its preview on it: any change of the sequence or of one
    # of the five fields is a new key (2026-10-22, the PM) - even a fixed
    # width's minimum, which moves no rank.
    base = sweep(edge="wave", edge_rows=4, edge_min_rows=2, edge_seed=5)
    variants = [dict(base, sequence="left_right"), dict(base, edge="fray"),
                dict(base, edge_rows=4.5), dict(base, edge_random=True),
                dict(base, edge_min_rows=1), dict(base, edge_seed=6)]
    keys = {rank_set(base)} | {rank_set(v) for v in variants}
    assert len(keys) == 1 + len(variants)
