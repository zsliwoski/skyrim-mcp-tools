"""Pure-logic tests that need neither the game files nor the compiler."""
import ast
from pathlib import Path

import pytest

from tools.npcs.npc_builder import WAITING_FOR_PLAYER, fid_bytes, follower_entries, z
from tools.papyrus.papyrus_build import script_name
from tools.core.plugin_edit import EditError, hi, resolve_target_path
from tools.quests.quest_builder import check_spec, pickup_source
from tools.core.quest_model import u32

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {
    "add_fetch_quest", "add_npc_dialogue", "add_quest_alias", "add_quest_stage_script", "create_npc",
    "create_npc_draft", "create_record", "describe_npc_fields", "edit_npc", "edit_npc_face", "edit_record",
    "get_npc", "get_npc_dialogue", "get_npc_draft", "get_npc_face", "get_quest", "get_quest_structure",
    "get_record", "get_script", "get_weather", "package_plugin", "paint_npc_face", "search_cells",
    "search_forms", "search_npcs", "search_quests", "search_references", "update_npc_draft",
    "validate_npc_draft", "write_npc_plugin",
    "add_kill_quest", "add_plant_quest", "add_quest", "clone_record", "snapshot_plugin", "list_snapshots",
    "restore_snapshot", "install_plugin", "validate_plugin", "quest_console_commands", "make_reference_persistent",
    "list_texts", "edit_text", "edit_texts", "list_book_models", "read_book", "edit_book", "lint_text", "list_conditions", "edit_condition", "edit_script", "diff_plugin",
    "audit_plugin", "create_cell", "add_radiant_quest", "clone_records", "place_item", "get_cell_contents",
        "create_enchantment", "create_script_effect", "enchant_item", "create_spell", "create_script",
        "create_shader", "create_unique_item", "deploy_plugin", "item_console_commands",
        "diff_records", "copy_subrecords", "drop_conditions", "remove_records", "create_scroll",
        "create_spell_tome",
        "create_spell_set",
        "find_free_spots",
        "place_items", "list_records", "find_dependents", "rename_script", "move_records", "detect_skse",
        "enchantment_test_plan", "check_script", "resolve_form", "create_script_effects", "create_enchanted_item",
    }


def tool_functions():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8-sig"))
    return [n for n in tree.body if isinstance(n, ast.FunctionDef) and any(
        isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool" for d in n.decorator_list)]


def valid_spec():
    return {
        "quest_editor_id": "TestQuest", "quest_name": "Test", "item_editor_id": "TestItem",
        "item_name": "Item", "item_clone_from": "Firewood01", "placement": {"worldspace": "Tamriel"},
        "log": {"10": "a", "20": "b", "30": "c"}, "objectives": {"10": "x", "20": "y"},
        "offer": {"prompt": "p", "lines": ["l"]}, "reminder": {"prompt": "p", "lines": ["l"]},
        "turn_in": {"prompt": "p", "lines": ["l"]}, "done": {"prompt": "p", "lines": ["l"]},
    }


class TestServerRegistration:
    def test_every_expected_tool_is_registered(self):
        assert {f.name for f in tool_functions()} == EXPECTED_TOOLS

    def test_every_tool_is_documented(self):
        assert [f.name for f in tool_functions() if not ast.get_docstring(f)] == []

    def test_write_tools_are_all_registered_tools(self):
        import server
        assert server.WRITE_TOOLS <= {f.name for f in tool_functions()}

    def test_package_layout_is_importable(self):
        import server  # noqa: F401
        from tools.npcs import facegen  # noqa: F401
        from tools.npcs import npc_builder  # noqa: F401
        from tools.papyrus import papyrus_build  # noqa: F401
        from tools.core import plugin_edit  # noqa: F401
        from tools.quests import quest_builder  # noqa: F401
        from tools.dialogue import dialogue_tools  # noqa: F401
        from tools.npcs import npc_tools  # noqa: F401
        from tools.papyrus import pex_tools  # noqa: F401
        from tools.quests import quest_tools  # noqa: F401
        from tools.papyrus import script_tools  # noqa: F401


class TestHelpers:
    def test_z_is_nul_terminated_cp1252(self):
        assert z("Ab") == b"Ab\0"
        assert z("é") == b"\xe9\0"

    def test_fid_bytes_little_endian(self):
        assert fid_bytes(0x01000802) == bytes([2, 8, 0, 1])

    def test_hi_is_load_order_byte(self):
        assert hi(0x01000802) == 1
        assert hi(0x00013BA2) == 0

    def test_u32_reads_at_offset(self):
        assert u32(b"\0\0\x01\0\0\0", 2) == 1


class TestScriptName:
    def test_fragment_name(self):
        assert script_name("Nevs.esp", "TIF", 0x81B) == "TIF__Nevs_00081B"

    def test_stem_is_sanitised_and_truncated(self):
        name = script_name("My Very-Long Plugin Name.esp", "QF", 0x1)
        assert name == "QF__MyVeryLongPlugin_000001"


class TestResolveTargetPath:
    def test_bare_name_goes_to_output_dir_and_gets_extension(self, tmp_path):
        assert resolve_target_path("Mod", tmp_path, None) == tmp_path / "Mod.esp"

    def test_prefers_existing_file_in_data_dir(self, tmp_path):
        out, data = tmp_path / "out", tmp_path / "data"
        out.mkdir(), data.mkdir()
        (data / "Mod.esp").write_bytes(b"")
        assert resolve_target_path("Mod.esp", out, data) == data / "Mod.esp"

    def test_official_plugins_are_refused(self, tmp_path):
        with pytest.raises(EditError, match="official"):
            resolve_target_path("Skyrim.esm", tmp_path, None)

    def test_sub_paths_are_refused(self, tmp_path):
        with pytest.raises(EditError):
            resolve_target_path("sub/Mod.esp", tmp_path, None)

    def test_must_exist(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_target_path("Missing.esp", tmp_path, None, must_exist=True)


class TestFetchQuestSpec:
    def test_valid_spec_has_no_errors(self):
        assert check_spec(valid_spec()) == []

    def test_missing_fields_are_reported(self):
        spec = valid_spec()
        del spec["offer"], spec["log"]
        errors = check_spec(spec)
        assert "missing field 'offer'" in errors and "missing field 'log'" in errors

    def test_editor_ids_are_restricted(self):
        spec = valid_spec()
        spec["quest_editor_id"] = "bad id!"
        assert any("quest_editor_id" in e for e in check_spec(spec))

    def test_topics_need_prompt_and_lines(self):
        spec = valid_spec()
        spec["done"] = {"prompt": "p", "lines": []}
        assert any("done needs" in e for e in check_spec(spec))

    def test_every_stage_needs_log_and_objective_text(self):
        spec = valid_spec()
        del spec["log"]["20"], spec["objectives"]["10"]
        errors = check_spec(spec)
        assert "log needs an entry for stage 20" in errors
        assert "objectives needs an entry for 10" in errors

    def test_pickup_script_advances_the_quest(self):
        src = pickup_source("My_Pickup")
        assert src.startswith("Scriptname My_Pickup Extends ObjectReference")
        assert "FetchQuest.SetStage(20)" in src


class TestFollowerEntries:
    def by_prompt(self, cost=50):
        out = {}
        for e in follower_entries(cost):
            out.setdefault(e["prompt"], []).append(e)
        return out

    def test_topics_present(self):
        prompts = self.by_prompt(25)
        assert set(prompts) == {"Would you travel with me? (25 gold)", "Wait here.", "Follow me.",
                                "I don't need you anymore."}
        assert len(prompts["Would you travel with me? (25 gold)"]) == 2

    def test_hiring_only_while_not_hired_and_needs_gold(self):
        accept, refuse = self.by_prompt(30)["Would you travel with me? (30 gold)"]
        not_hired = {"function": "GetInFaction", "form": "CurrentHireling", "op": "==", "value": 0}
        assert not_hired in accept["conditions"] and not_hired in refuse["conditions"]
        assert any(c["function"] == "GetItemCount" and c["op"] == ">=" for c in accept["conditions"])
        assert any(c["function"] == "GetItemCount" and c["op"] == "<" for c in refuse["conditions"])
        assert "SetFollower" in accept["code"]

    def test_wait_and_follow_are_mutually_exclusive(self):
        prompts = self.by_prompt()

        def waiting_value(prompt):
            (entry,) = prompts[prompt]
            (cond,) = [c for c in entry["conditions"] if c["function"] == "GetActorValue"]
            assert cond["actor_value"] == WAITING_FOR_PLAYER
            return cond["value"]

        assert waiting_value("Wait here.") == 0
        assert waiting_value("Follow me.") == 1

    def test_dismiss_requires_hired(self):
        (dismiss,) = self.by_prompt()["I don't need you anymore."]
        assert dismiss["conditions"] == [{"function": "GetInFaction", "form": "CurrentHireling",
                                          "op": "==", "value": 1}]
        assert "DismissFollower" in dismiss["code"]
