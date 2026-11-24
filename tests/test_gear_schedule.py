"""Weapon/armor builders and NPC schedules against the fixture master."""
import struct

import pytest
from esplib import Plugin

import server
from tools.records.maintenance import all_records

pytestmark = pytest.mark.fakegame


def _recs(path):
    server._models.clear()
    return {r.editor_id: r for r in all_records(Plugin.load(str(path))) if r.editor_id}


def _sub(rec, sig):
    return [bytes(s.data) for s in rec.subrecords if s.signature == sig]


def test_weapon_and_armor_stats(npc_plugin):
    res = server.create_weapon("TestMod.esp", "IronSword", "MySword", "My Sword", damage=40, value=500,
                               weight=6.5, speed=1.2, reach=1.1)
    assert "error" not in res, res
    res = server.create_armor("TestMod.esp", "ArmorIronBoots", "MyBoots", "My Boots", armor_rating=33.5, value=99)
    assert "error" not in res, res
    recs = _recs(npc_plugin)
    assert struct.unpack("<IfH", _sub(recs["MySword"], "DATA")[0]) == (500, 6.5, 40)
    assert struct.unpack_from("<ff", _sub(recs["MySword"], "DNAM")[0], 4) == pytest.approx((1.2, 1.1))
    assert struct.unpack("<If", _sub(recs["MyBoots"], "DATA")[0]) == (99, 6.0)
    assert struct.unpack("<i", _sub(recs["MyBoots"], "DNAM")[0]) == (3350,)
    assert "error" in server.create_armor("TestMod.esp", "ArmorIronBoots", "BadBoots", armor_rating=-1)
    assert "error" in server.create_weapon("TestMod.esp", "ArmorIronBoots", "NotAWeapon")


def test_npc_schedule_adds_timed_packages(npc_plugin):
    res = server.set_npc_schedule("TestMod.esp", "TestSven", [
        {"activity": "sleep", "start": 22, "hours": 8}, {"activity": "eat", "start": 18.5, "hours": 1, "day": 2}])
    assert "error" not in res, res
    recs = _recs(npc_plugin)
    psdt = _sub(recs["TestSvenSched1"], "PSDT")[0]
    assert struct.unpack("<bbBbb3xi", psdt) == (-1, -1, 0, 22, 0, 480)
    assert struct.unpack("<bbBbb3xi", _sub(recs["TestSvenSched2"], "PSDT")[0]) == (-1, 2, 0, 18, 30, 60)
    pkids = [struct.unpack("<I", d)[0] & 0xFFFFFF for d in _sub(recs["TestSven"], "PKID")]
    assert pkids[-2:] == [recs["TestSvenSched1"].form_id.value & 0xFFFFFF, recs["TestSvenSched2"].form_id.value & 0xFFFFFF]
    assert "error" in server.set_npc_schedule("TestMod.esp", "TestSven", [{"activity": "dance", "start": 1, "hours": 1}])
    assert "error" in server.set_npc_schedule("TestMod.esp", "TestSven", [{"activity": "sleep", "start": 25, "hours": 1}])
