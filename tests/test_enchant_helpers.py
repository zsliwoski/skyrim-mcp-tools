import pytest

import server


@pytest.mark.game
class TestEnchantHelpers:
    def test_check_script_ok_and_error(self, compiler_available):
        good = server.check_script("Scriptname CheckProbeOk Extends ActiveMagicEffect\nEvent OnEffectStart(Actor a, Actor b)\nEndEvent\n")
        assert good["ok"] and good["script"] == "CheckProbeOk"
        bad = server.check_script("Scriptname CheckProbeBad Extends ActiveMagicEffect\nEvent OnEffectStart(Actor a, Actor b)\n NoSuchCall()\nEndEvent\n")
        assert not bad["ok"] and "NoSuchCall" in bad["output"]
        assert "error" in server.check_script("not a script")

    def test_resolve_form(self):
        res = server.resolve_form("JobJarlFaction", "FACT")
        assert res["matches"][0]["local_id"] == "050920"
        miss = server.resolve_form("JobJarlFactio", "FACT")
        assert miss["matches"] == [] and "JobJarlFaction" in miss["similar"]

    def test_clone_clears_non_playable(self, sandbox):
        from helpers import make_hall
        make_hall()
        res = server.clone_record("ArmorThievesGuildHelmet", "Ench.esp", "PlayableHood", "Hood")
        assert "error" not in res, res
        assert res["created"]["cleared_flags"] == "Non-Playable"
        assert server.get_record("PlayableHood", plugin="Ench.esp")["flags"] == "0x00000000"

    def test_edit_record_requires_an_edit(self, sandbox):
        res = server.edit_record("JobJarlFaction", "X.esp")
        assert "nothing to edit" in res["error"]


@pytest.mark.game
class TestConsoleAndPlan:
    def test_filters_and_plan(self, sandbox, compiler_available):
        res = server.create_script_effects("PlanMod.esp", [{
            "editor_id": "PlanEffect", "name": "Plan Effect", "script_name": "PlanMod_Effect", "cost": 200,
            "description": "Does a thing.",
            "source": "Scriptname PlanMod_Effect Extends ActiveMagicEffect\nEvent OnEffectStart(Actor a, Actor b)\n"
                      "\tRegisterForMenu(\"BarterMenu\")\nEndEvent\n",
        }])
        assert "error" not in res[0], res
        ench = res[0]["enchantment"]["editor_id"]
        plan = server.enchantment_test_plan("PlanMod.esp", ench)
        assert plan["effects"][0]["scripts"] == ["PlanMod_Effect"]
        assert plan["effects"][0]["description"] == "Does a thing."
        assert any("RegisterForMenu" in s for s in plan["requires_skse"])
        cmds = server.item_console_commands("PlanMod.esp", sections=["effects"])
        assert set(cmds) == {"effects", "note"} and len(cmds["effects"]) == 2
        assert server.item_console_commands("PlanMod.esp", query="nomatch")["effects"] == []
        assert "error" in server.item_console_commands("PlanMod.esp", sections=["bogus"])


SOURCE = ("Scriptname {name} Extends ActiveMagicEffect\nEvent OnEffectStart(Actor a, Actor b)\n"
          "\tRegisterForAnimationEvent(a, \"FootSprintLeft\")\nEndEvent\n")


@pytest.mark.game
class TestCellsAndPlacement:
    def test_cell_tools_see_target_plugin(self, sandbox):
        from helpers import make_hall
        make_hall()
        assert server.search_cells("TestHall") == []
        found = server.search_cells("TestHall", target_plugin="Ench.esp")
        assert [c["editor_id"] for c in found] == ["TestHall"]
        assert "error" not in server.get_cell_contents("TestHall", target_plugin="Ench.esp")
        spots = server.find_free_spots("TestHall", 0, 0, 0, count=1, target_plugin="Ench.esp")
        assert len(spots["spots"]) == 1
        listed = server.list_records("Ench.esp", signature="CELL")
        assert listed["total"] == 1

    def test_place_item_near(self, sandbox):
        from helpers import make_hall
        make_hall()
        server.clone_record("Firewood01", "Ench.esp", "NearAnchor", "Anchor")
        server.clone_record("Firewood01", "Ench.esp", "NearBoots", "Boots")
        server._models.clear()
        assert "error" not in server.place_item("NearAnchor", "Ench.esp", "TestHall", 100, 200, 0)
        server._models.clear()
        res = server.place_item("NearBoots", "Ench.esp", "TestHall", near="NearAnchor", editor_id="NearBootsRef")
        assert "error" not in res, res
        pos = res["position"]
        assert (pos["x"], pos["y"]) != (100, 200) and abs(pos["x"] - 100) <= 400 and abs(pos["y"] - 200) <= 400

    def test_place_item_needs_a_position(self, sandbox):
        from helpers import make_hall
        make_hall()
        server.clone_record("Firewood01", "Ench.esp", "NearBoots", "Boots")
        assert "x, y and z, or near" in server.place_item("NearBoots", "Ench.esp", "TestHall")["error"]
        missing = server.place_item("NearBoots", "Ench.esp", "TestHall", near="NoSuchThing")
        assert "nothing matching" in missing["error"]


@pytest.mark.game
class TestCreateEnchantedItem:
    def test_boots_with_script_placed_beside_npc(self, sandbox, compiler_available):
        from helpers import make_hall
        make_hall()
        server.clone_record("Firewood01", "Ench.esp", "EnchAnchor", "Anchor")
        server._models.clear()
        server.place_item("EnchAnchor", "Ench.esp", "TestHall", 0, 0, 0)
        server._models.clear()
        res = server.create_enchanted_item(
            "ArmorIronBoots", "Ench.esp", "TestRoadBoots", "Test Road Boots", "TestRoadScript",
            SOURCE.format(name="TestRoadScript"), effect_description="Test.", cell="TestHall", near="EnchAnchor")
        assert "error" not in res, res
        assert res["enchantment"] == "TestRoadBootsEffectEnch"
        assert set(res["steps"]) == {"item", "effect", "enchant", "placed"}
        plan = server.enchantment_test_plan("Ench.esp", "TestRoadBoots")
        assert plan["effects"][0]["scripts"] == ["TestRoadScript"]
        assert "debug_tip" in plan and any("RegisterForAnimationEvent" in s for s in plan["requires_skse"])
        assert server.validate_plugin("Ench.esp")["errors"] == []

    def test_compile_error_changes_nothing(self, sandbox, compiler_available):
        from helpers import make_hall
        make_hall()
        before = server.list_records("Ench.esp")["total"]
        res = server.create_enchanted_item("ArmorIronBoots", "Ench.esp", "BadBoots", "Bad", "BadScript",
                                           "Scriptname BadScript Extends ActiveMagicEffect\nNoSuchCall()\n")
        assert "does not compile" in res["error"]
        assert server.list_records("Ench.esp")["total"] == before

    def test_rejects_other_item_types(self, sandbox, compiler_available):
        res = server.create_enchanted_item("Firewood01", "Ench.esp", "WoodThing", "Wood", "WoodScript",
                                           SOURCE.format(name="WoodScript"))
        assert "ARMO or WEAP" in res["error"]


@pytest.mark.game
class TestDeployBoth:
    def test_both_zips_and_installs(self, sandbox, tmp_path, monkeypatch):
        from tools.records import maintenance
        monkeypatch.setattr(maintenance, "validate", lambda *a, **k: {"errors": [], "warnings": []})
        deploy_plugin = maintenance.deploy_plugin
        server.create_enchantment("Dep.esp", "DepEnch", "Dep Fire", [
            {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon")
        data = tmp_path / "fakedata"
        data.mkdir()
        out = deploy_plugin(sandbox / "Dep.esp", data, "both")
        assert out["deployed"] and (data / "Dep.esp").is_file() and "install" in out and out["zip"]
        with pytest.raises(ValueError):
            deploy_plugin(sandbox / "Dep.esp", data, "nope")
