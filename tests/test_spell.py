import struct

import pytest

from tools.records.spell import create_script, spell_subrecords


def spit(subs):
    return struct.unpack('<IIIfIIffI', bytes.fromhex(next(s for s in subs if s['sig'] == 'SPIT')['hex']))


@pytest.mark.parametrize("kind,spell_type,cast", [("ability", 4, 0), ("spell", 0, 1), ("power", 2, 1),
                                                  ("lesser_power", 3, 1)])
def test_spit_type_and_cast(kind, spell_type, cast):
    s = spit(spell_subrecords("X", [{"effect": "E"}], kind))
    assert (s[2], s[4]) == (spell_type, cast)


def test_effects_and_delivery():
    subs = spell_subrecords("X", [{"effect": "A", "magnitude": 5, "duration": 3}, {"effect": "B"}], "spell",
                            "aimed", description="d")
    by_sig = {}
    for s in subs:
        by_sig.setdefault(s['sig'], []).append(s)
    assert {'MDOB', 'ETYP', 'DESC', 'SPIT'} <= set(by_sig) and len(by_sig['EFID']) == len(by_sig['EFIT']) == 2
    assert spit(subs)[5] == 2
    assert struct.unpack('<fII', bytes.fromhex(by_sig['EFIT'][0]['hex'])) == (5.0, 0, 3)
    assert by_sig['EFID'][0]['sigs'] == ['MGEF']


@pytest.mark.parametrize("kwargs", [{"kind": "wand"}, {"delivery": "sideways"}, {"kind": "ability",
                                                                                  "delivery": "touch"}])
def test_spell_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        spell_subrecords("X", [{"effect": "E"}], **kwargs)


def test_spell_needs_effects():
    with pytest.raises(ValueError):
        spell_subrecords("X", [])
    with pytest.raises(ValueError):
        spell_subrecords("X", [{"magnitude": 1}])


@pytest.mark.parametrize("name,src", [("Bad Name", "Scriptname Bad Name\n"), ("A_B", "Scriptname Other\n"),
                                      ("A_B", "Event OnInit()\nEndEvent\n")])
def test_create_script_rejects_bad_source(name, src):
    with pytest.raises(ValueError):
        create_script(None, "P.esp", name, src)
