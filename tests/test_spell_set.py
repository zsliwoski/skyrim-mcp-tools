"""Pure tests for named record fields, free-spot picking and spell-set variants (no game files needed)."""
import struct

import pytest

from tools.cells.cell_contents import pick_spots
from tools.records.fields import decode_fields, encode_field
from tools.records.spell_set import VARIANTS


def test_encode_field_accepts_names_and_numbers():
    assert encode_field("MGEF", "cast_type", "concentration") == ("DATA", 80, struct.pack("<I", 2))
    assert encode_field("MGEF", "delivery", 1) == ("DATA", 84, struct.pack("<I", 1))
    assert encode_field("SCRL", "cast_type", "scroll") == ("SPIT", 16, struct.pack("<I", 3))
    assert encode_field("WEAP", "charge", 500) == ("EAMT", 0, struct.pack("<H", 500))
    assert encode_field("MGEF", "base_cost", 2) == ("DATA", 4, struct.pack("<f", 2.0))


@pytest.mark.parametrize("sig,field,value", [("MGEF", "nope", 1), ("QUST", "x", 1), ("MGEF", "delivery", "sideways")])
def test_encode_field_rejects_bad_input(sig, field, value):
    with pytest.raises(ValueError):
        encode_field(sig, field, value)


def test_decode_fields_names_words():
    data = bytearray(152)
    struct.pack_into("<II", data, 80, 2, 2)
    fields = decode_fields("MGEF", "DATA", bytes(data))
    assert fields["cast_type"] == "concentration (2)" and fields["delivery"] == "aimed (2)"
    assert decode_fields("MGEF", "EDID", b"x\0") is None


def test_pick_spots_is_nearest_first_and_avoids_blockers():
    assert pick_spots([], 0, 0, 1) == [(0, 0)]
    spots = pick_spots([(0, 0)], 0, 0, 3, spacing=30, clearance=25)
    assert (0, 0) not in spots and len(spots) == 3
    assert all(abs(x) + abs(y) == 30 for x, y in spots)


def test_pick_spots_returns_fewer_when_crowded():
    assert pick_spots([(0, 0)], 0, 0, 5, spacing=30, clearance=1000, radius=60) == []


def test_variants_cover_every_delivery():
    assert {v[2] for v in VARIANTS.values()} == {"aimed", "self"}
    assert {v[3] for v in VARIANTS.values()} == {"fire_and_forget", "concentration"}
