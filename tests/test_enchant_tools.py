"""Enchantments, script effects, unique items, record diffing and maintenance."""
import math
import struct
from pathlib import Path

import pytest
from esplib import Plugin

import server
from helpers import make_hall, sigs
from tools.core.plugin_edit import plugin_files
from tools.records.maintenance import all_records

pytestmark = pytest.mark.game


class TestEnchantTools:
    def test_create_unique_item_places_enchanted_dagger_with_full_charge(self, sandbox):
        make_hall()
        res = server.create_unique_item("IronDagger", "Ench.esp", "TestDagger", "Test Dagger",
                                        enchantment="TestEnch", charge=1234, cell="TestHall", x=1, y=2, z=3,
                                        rotation_x=90)
        assert "error" not in res, res
        assert res["placed"]["charge"] == 1234
        refs = [r for r in all_records(Plugin.load(str(sandbox / "Ench.esp"))) if r.signature == "REFR"]
        assert len(refs) == 1
        assert struct.unpack("<f", bytes(refs[0].get_subrecord("XCHG").data))[0] == 1234
        data = struct.unpack("<6f", bytes(refs[0].get_subrecord("DATA").data))
        assert data[3] == pytest.approx(math.pi / 2)

    def test_place_enchanted_weapon_defaults_to_full_charge(self, sandbox):
        make_hall()
        assert "error" not in server.create_unique_item("IronDagger", "Ench.esp", "TestDagger", "Test Dagger",
                                                        enchantment="TestEnch")
        server._models.clear()
        res = server.place_item("TestDagger", "Ench.esp", "TestHall", 0, 0, 0)
        assert "error" not in res, res
        assert res["charge"] == 500
        server._models.clear()
        assert server.place_item("IronDagger", "Ench.esp", "TestHall", 0, 0, 0)["charge"] is None

    def test_create_shader_recolours_a_clone(self, sandbox):
        make_hall()
        server._models.clear()
        res = server.create_shader("Ench.esp", "TestGold", "#FFC832")
        assert "error" not in res, res
        rec = next(r for r in Plugin.load(str(sandbox / "Ench.esp")).records if r.editor_id == "TestGold")
        assert bytes(rec.get_subrecord("DATA").data)[56:60] == bytes.fromhex("ffc83200")

    def test_item_console_commands_and_deploy(self, sandbox):
        server.create_enchantment("Ench.esp", "TestEnch", "Test Fire", [
            {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon")
        server._models.clear()
        assert "error" not in server.create_unique_item("IronDagger", "Ench.esp", "TestDagger", "Test Dagger",
                                                        enchantment="TestEnch")
        cmds = server.item_console_commands("Ench.esp")
        assert any("player.additem XX" in c and "TestDagger" in c for c in cmds["items"])
        res = server.deploy_plugin("Ench.esp")
        assert res["deployed"] and Path(res["zip"]).is_file()

    def test_search_forms_rejects_unsupported_signature(self, sandbox):
        assert "error" in server.search_forms("ZZZZ", "x")

    def test_script_effect_accepts_extra_effects(self):
        from tools.records.enchant import build_subrecords
        subs = build_subrecords("Both", [{"effect": "A", "duration": 1}, {"effect": "B", "duration": 600}], "weapon")
        assert [s["sig"] for s in subs].count("EFID") == 2

    def test_zero_cost_enchantment_gets_auto_cost(self, sandbox):
        self.setup_ench(sandbox)
        rec = server.get_record("TestEnch", plugin="Ench.esp", signature="ENCH")
        enit = bytes.fromhex(next(s for s in rec["subrecords"] if s["sig"] == "ENIT")["hex"])
        cost, _, _, amount = struct.unpack_from("<iIIi", enit)
        assert cost > 0 and amount == cost

    def test_remove_records_deletes_item_and_its_placement(self, sandbox):
        make_hall()
        assert "error" not in server.create_unique_item("IronDagger", "Ench.esp", "TestDagger", "Test Dagger",
                                                        enchantment="TestEnch", cell="TestHall", x=1, y=2, z=3)
        res = server.remove_records("Ench.esp", ["TestDagger"])
        assert "error" not in res, res
        assert len(res["removed"]) == 2
        recs = list(all_records(Plugin.load(str(sandbox / "Ench.esp"))))
        assert not [r for r in recs if r.signature in ("WEAP", "REFR")]

    def test_diff_records_names_mgef_fields_and_conditions(self, sandbox):
        res = server.diff_records("Skyrim.esm", "EnchParalysisFFContact", "Skyrim.esm", "EnchFireDamageFFContact")
        assert "error" not in res, res
        data = next(d for d in res["different"] if d["sig"] == "DATA")
        assert {"flags", "archetype"} & {w["field"] for w in data["words"]}

    def test_validate_warns_about_zero_cost_and_header(self, sandbox):
        make_hall()
        path = sandbox / "Ench.esp"
        p = Plugin.load(str(path))
        p.header.version = 1.71
        for r in all_records(p):
            if r.signature == "ENCH":
                enit = bytearray(r.get_subrecord("ENIT").data)
                struct.pack_into("<iI", enit, 0, 0, 0)
                r.get_subrecord("ENIT").data = bytes(enit)
        p.modified = True
        p.save(str(path))
        warnings = " ".join(server.validate_plugin("Ench.esp")["warnings"])
        assert "1.70" in warnings and "cost is 0" in warnings

    @pytest.mark.compiler
    @pytest.mark.usefixtures("compiler_available")
    def test_script_effect_can_imitate_vanilla_effect_and_drop_conditions(self, sandbox):
        src = ("Scriptname Ench_Imitate Extends ActiveMagicEffect\n"
               "Event OnEffectStart(Actor akTarget, Actor akCaster)\n"
               "  Debug.Trace(\"on\")\nEndEvent\n")
        res = server.create_script_effect("Ench.esp", "TestImitate", "Test Imitate", "Ench_Imitate", src,
                                          kind="weapon", base_effect="EnchParalysisFFContact",
                                          drop_conditions=[0])
        assert "error" not in res, res
        assert res["dropped_conditions"][0]["condition"].startswith("GetRandomPercent")
        rec = next(r for r in all_records(Plugin.load(str(sandbox / "Ench.esp"))) if r.editor_id == "TestImitate")
        assert struct.unpack_from("<I", bytes(rec.get_subrecord("DATA").data), 64)[0] == 0x15
        assert rec.get_subrecord("VMAD") is not None and len(rec.get_subrecords("CTDA")) == 3

    def setup_ench(self, sandbox):
        res = server.create_enchantment("Ench.esp", "TestEnch", "Test Fire", [
            {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon")
        assert "error" not in res, res

    def test_enchant_weapon_sets_eitm_and_charge(self, sandbox):
        self.setup_ench(sandbox)
        res = server.enchant_item("IronSword", "Ench.esp", "TestEnch", charge=300)
        assert "error" not in res, res
        rec = server.get_record("IronSword", plugin="Ench.esp")
        subs = {s["sig"]: s for s in rec["subrecords"]}
        assert subs["EITM"]["size"] == 4 and subs["EITM"]["hex"][:6] == "000800"
        assert subs["EAMT"]["hex"] == "2c01"

    def test_enchant_refuses_enchanted_item_unless_replace(self, sandbox):
        self.setup_ench(sandbox)
        assert "error" not in server.enchant_item("IronSword", "Ench.esp", "TestEnch")
        assert "error" in server.enchant_item("IronSword", "Ench.esp", "TestEnch")
        assert "error" not in server.enchant_item("IronSword", "Ench.esp", "TestEnch", replace=True)

    def test_enchant_armor_rejects_charge_and_non_gear(self, sandbox):
        self.setup_ench(sandbox)
        assert "error" in server.enchant_item("ClothesCirclet01", "Ench.esp", "TestEnch", charge=100)
        assert "error" in server.enchant_item("Firewood01", "Ench.esp", "TestEnch")
        assert "error" in server.enchant_item("IronSword", "Ench.esp", "NoSuchEnchantment")

    @pytest.mark.compiler
    @pytest.mark.usefixtures("compiler_available")
    def test_script_effect_end_to_end(self, sandbox):
        src = ("Scriptname Ench_Winter Extends ActiveMagicEffect\n"
               "Event OnEffectStart(Actor akTarget, Actor akCaster)\n"
               "  Debug.Trace(\"on\")\nEndEvent\n")
        res = server.create_script_effect("Ench.esp", "TestWinter", "Test Winter", "Ench_Winter", src,
                                          description="Colder is stronger.")
        assert "error" not in res, res
        server._models.clear()  # the suite disables _invalidate
        assert "error" not in server.enchant_item("ClothesCirclet01", "Ench.esp", "TestWinterEnch")
        assert (sandbox / "Scripts" / "Ench_Winter.pex").is_file()
        assert sigs(sandbox / "Ench.esp")["MGEF"] == ["TestWinter"]
        assert server.validate_plugin("Ench.esp")["errors"] == []
        names = {f.name for f in plugin_files(sandbox / "Ench.esp", regen_seq=False)}
        assert "Ench_Winter.pex" in names

    @pytest.mark.compiler
    @pytest.mark.usefixtures("compiler_available")
    def test_script_effect_compile_error_changes_nothing(self, sandbox):
        res = server.create_script_effect("Ench.esp", "TestBad", "Bad", "Ench_Bad",
                                          "Scriptname Ench_Bad Extends ActiveMagicEffect\nThis is not papyrus\n")
        assert "error" in res
        assert not (sandbox / "Ench.esp").exists() or "MGEF" not in sigs(sandbox / "Ench.esp")

    def test_create_spell_ability(self, sandbox):
        res = server.create_spell("Ench.esp", "TestAbility", "Test Ability",
                                  [{"effect": "EnchFortifyDestructionConstantSelf", "magnitude": 10}])
        assert "error" not in res, res
        assert sigs(sandbox / "Ench.esp") == {"SPEL": ["TestAbility"]}
        assert "error" in server.create_spell("Ench.esp", "TestBad", "Bad", [{"effect": "NoSuchEffectXyz"}])

    @pytest.mark.compiler
    @pytest.mark.usefixtures("compiler_available")
    def test_create_script_compiles_and_reports_errors(self, sandbox):
        ok = server.create_script("Ench.esp", "Ench_Util", "Scriptname Ench_Util Extends Quest\n")
        assert "error" not in ok, ok
        assert (sandbox / "Scripts" / "Ench_Util.pex").is_file()
        assert (sandbox / "Scripts" / "Source" / "Ench_Util.psc").is_file()
        bad = server.create_script("Ench.esp", "Ench_Broken", "Scriptname Ench_Broken\nnot papyrus\n")
        assert "error" in bad and not (sandbox / "Scripts" / "Ench_Broken.pex").exists()
