"""Containers, leveled lists, outfits and recipes against the fixture master."""
import struct

import pytest
from esplib import Plugin

import server

pytestmark = pytest.mark.fakegame


def _recs(path):
    return {r.editor_id: r for r in Plugin.load(str(path)).records if r.editor_id}


def _subs(rec, sig):
    return [bytes(s.data) for s in rec.subrecords if s.signature == sig]


@pytest.fixture
def plugin(sandbox):
    res = server.create_container("Loot.esp", "TestChest", "Test Chest",
                                  [{"item": "IronDagger", "count": 2}, {"item": "LItemTestLoot"}], respawns=True)
    assert "error" not in res, res
    server._models.clear()
    return sandbox / "Loot.esp"


def test_container_lists_items_and_respawns(plugin):
    chest = _recs(plugin)["TestChest"]
    assert struct.unpack('<I', _subs(chest, 'COCT')[0])[0] == 2
    assert [s[4:] for s in _subs(chest, 'CNTO')] == [struct.pack('<I', 2), struct.pack('<I', 1)]
    assert _subs(chest, 'DATA')[0][0] == 3


def test_leveled_list_and_adding_to_a_vanilla_one(plugin):
    kw = {"plugins": ["Skyrim.esm", str(plugin)]}
    res = server.create_leveled_list("Loot.esp", "TestList", [{"form": "IronDagger"}, {"form": "IronSword", "level": 5}],
                                     chance_none=25, **kw)
    assert "error" not in res, res
    server._models.clear()
    lst = _recs(plugin)["TestList"]
    assert _subs(lst, 'LVLD') == [b'\x19'] and _subs(lst, 'LLCT') == [b'\x02']
    assert struct.unpack('<HHIHH', _subs(lst, 'LVLO')[1])[0] == 5
    added = server.add_to_leveled_list("Loot.esp", "LItemTestLoot", [{"form": "ArmorIronBoots", "level": 3}], **kw)
    assert "error" not in added, added
    assert added["entries_total"] == 2
    server._models.clear()
    over = next(r for r in Plugin.load(str(plugin)).records if r.signature == 'LVLI')
    assert _subs(over, 'LLCT') == [b'\x02'] and len(_subs(over, 'LVLO')) == 2


def test_outfit_and_recipes(plugin):
    kw = {"plugins": ["Skyrim.esm", str(plugin)]}
    assert "error" not in server.create_outfit("Loot.esp", "TestOutfit", ["ArmorIronBoots", "ClothesCirclet01"], **kw)
    server._models.clear()
    assert len(_subs(_recs(plugin)["TestOutfit"], 'INAM')[0]) == 8
    craft = server.create_recipe("Loot.esp", "TestCraft", "IronSword", [{"item": "IngotIron", "count": 2}], **kw)
    assert "error" not in craft, craft
    server._models.clear()
    temper = server.create_recipe("Loot.esp", "TestTemper", "IronSword", [{"item": "IngotIron"}], temper=True, **kw)
    assert temper["station"] == "sharpening", temper
    server._models.clear()
    recs = _recs(plugin)
    assert _subs(recs["TestCraft"], 'NAM1') == [b'\x01\x00'] and not _subs(recs["TestCraft"], 'CTDA')
    assert len(_subs(recs["TestTemper"], 'CTDA')[0]) == 32


def test_validation(plugin):
    assert "at least one entry" in server.create_leveled_list("Loot.esp", "Empty", [])["error"]
    assert "station" in server.create_recipe("Loot.esp", "Bad", "IronSword", [{"item": "IngotIron"}],
                                             station="nope")["error"]
    assert "already exists" in server.create_outfit("Loot.esp", "TestChest", ["ArmorIronBoots"])["error"]
