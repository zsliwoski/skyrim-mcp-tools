"""Named-field edits and spell families."""
import struct

import pytest
from esplib import Plugin

import server
from helpers import make_hall
from tools.records.maintenance import all_records

pytestmark = pytest.mark.game


class TestSpellSet:
    SRC = ("Scriptname Set_Effect Extends ActiveMagicEffect\n"
           "Event OnEffectStart(Actor akTarget, Actor akCaster)\n  Debug.Trace(\"on\")\nEndEvent\n")

    def test_named_field_edit_and_get_record_fields(self, sandbox):
        res = server.create_enchantment("Ench.esp", "TestEnch", "Test Fire", [
            {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon", cost=200)
        assert "error" not in res, res
        server._models.clear()
        res = server.edit_record("TestEnch", "Ench.esp", [{"field": "cost", "value": 250}], signature="ENCH")
        assert "error" not in res, res
        server._models.clear()
        rec = server.get_record("TestEnch", plugin="Ench.esp", signature="ENCH")
        enit = next(s for s in rec["subrecords"] if s["sig"] == "ENIT")
        assert enit["fields"]["cost"] == 250 and enit["fields"]["cast_type"].startswith("fire_and_forget")
        assert "error" in server.edit_record("TestEnch", "Ench.esp", [{"field": "nope", "value": 1}], signature="ENCH")

    @pytest.mark.compiler
    @pytest.mark.usefixtures("compiler_available")
    def test_spell_set_builds_every_piece_and_places_it(self, sandbox):
        make_hall()
        res = server.create_spell_set("Ench.esp", "TestSet", "Test Set", "Set_Effect", self.SRC,
                                      description="A test.", variants=["touch", "ray", "nova"],
                                      cell="TestHall", x=0, y=0, z=0)
        assert "error" not in res, res
        assert res["spells"] == ["TestSetTouchSpell", "TestSetRaySpell", "TestSetNovaSpell"]
        assert len(res["tomes"]) == 3 and len(res["placed"]) == 5
        spots = {(p["x"], p["y"]) for p in res["placed"]}
        assert len(spots) == 5
        recs = {r.editor_id: r for r in all_records(Plugin.load(str(sandbox / "Ench.esp"))) if r.editor_id}
        ray = recs["TestSetRayEffect"]
        assert struct.unpack_from("<II", bytes(ray.get_subrecord("DATA").data), 80) == (2, 2)
        assert struct.unpack_from("<II", bytes(recs["TestSetRaySpell"].get_subrecord("SPIT").data), 16) == (2, 2)
        assert struct.unpack_from("<II", bytes(recs["TestSetTouchEffect"].get_subrecord("DATA").data), 80) == (2, 2)
        assert struct.unpack_from("<II", bytes(recs["TestSetTouchSpell"].get_subrecord("SPIT").data), 16) == (2, 2)
        assert struct.unpack_from("<H", bytes(recs["TestSetStaff"].get_subrecord("EAMT").data))[0] == 3000
        server._models.clear()
        placed = server.item_console_commands("Ench.esp", sections=["placed"])["placed"]
        assert any("TestSetStaffRef" in c for c in placed)
        server._models.clear()
        free = server.find_free_spots("TestHall", 0, 0, 0, count=2, plugins=["Skyrim.esm", "Ench.esp"])
        assert "error" not in free and not ({(s["x"], s["y"]) for s in free["spots"]} & spots)

    def test_spell_set_rejects_unknown_variant(self, sandbox):
        res = server.create_spell_set("Ench.esp", "TestSet", "Test Set", "Set_Effect", self.SRC, variants=["zap"])
        assert "error" in res
