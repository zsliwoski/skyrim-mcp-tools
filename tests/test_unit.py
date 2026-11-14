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
    "add_fetch_quest", "add_npc_chat", "add_npc_dialogue", "add_quest_alias", "add_quest_stage_script", "create_npc",
    "create_npc_draft", "create_record", "describe_npc_fields", "edit_npc", "edit_npc_face", "edit_record",
    "get_npc", "get_npc_dialogue", "get_npc_draft", "get_npc_face", "get_quest", "get_quest_structure",
    "get_record", "get_script", "package_plugin", "paint_npc_face", "search_cells",
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
        "lore_search", "lore_get", "lore_status", "build_lore_db",
        "api_search", "api_get", "api_status", "build_api_db",
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
    def test_ambient_and_travel_entries_use_known_subtypes(self):
        from tools.npcs import npc_builder as nb
        hired = {'function': 'GetInFaction', 'form': 'CurrentHireling', 'op': '==', 'value': 1}
        entries = nb.ambient_entries() + nb.travel_entries(hired)
        assert {e['kind'] for e in entries} == {'notice_corpse', 'observe_combat', 'shout_reaction', 'idle'}
        assert all(e['kind'] in nb.SUBTYPES for e in entries)
        assert all(hired in e['conditions'] for e in nb.travel_entries(hired))
        assert {c['function'] for e in nb.travel_entries(hired) for c in e['conditions']} <= set(
            nb.CONDITION_FUNCTIONS)

    def test_combat_entries_use_known_subtypes(self):
        from tools.npcs import npc_builder as nb
        entries = nb.combat_entries()
        assert entries and all(e['kind'] in nb.SUBTYPES and len(e['answers']) >= 2 for e in entries)

    def by_prompt(self, cost=50):
        out = {}
        for e in follower_entries(cost):
            if "prompt" in e:
                out.setdefault(e["prompt"], []).append(e)
        return out

    def test_topics_present(self):
        prompts = self.by_prompt(25)
        assert set(prompts) == {"Would you travel with me? (25 gold)", "Wait here.", "Follow me.",
                                "I don't need you anymore.", "Trade some items.",
                                "I need you to do something.", "Yes. Let me show you.", "No. Never mind."}
        assert len(prompts["Would you travel with me? (25 gold)"]) == 2

    def test_favor_flow_uses_a_blocking_topic(self):
        entries = follower_entries(50, 3)
        favor = lambda v: {"function": "IsDoingFavor", "op": "==", "value": v}
        start = next(e for e in entries if e.get("prompt") == "I need you to do something.")
        assert favor(0) in start["conditions"]
        (block_idx,) = [i for i, e in enumerate(entries) if e.get("blocking")]
        assert favor(1) in entries[block_idx]["conditions"] and "prompt" not in entries[block_idx]
        for prompt in ("Yes. Let me show you.", "No. Never mind."):
            (child,) = [e for e in entries if e.get("prompt") == prompt]
            assert child["child_of"] == 3 + block_idx
        end = next(e for e in entries if e.get("prompt") == "No. Never mind.")
        assert "SetDoingFavor(false)" in end["code"]
        for prompt in ("Wait here.", "Follow me.", "I don't need you anymore.", "I need you to do something.",
                       "Yes. Let me show you.", "No. Never mind."):
            assert next(e for e in entries if e.get("prompt") == prompt)["goodbye"]

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


class TestNpcChat:
    def spec(self, **over):
        spec = {"scene_editor_id": "ChatA", "quest_editor_id": "ChatQuest", "speaker": "Sven", "partner": "Lydia",
                "trigger_line": "Is that Lydia?",
                "lines": [{"who": "speaker", "text": "Hello."}, {"who": "partner", "text": "Hi."}]}
        spec.update(over)
        return spec

    def test_valid_spec_has_no_errors(self):
        from tools.quests.chat_builder import check_spec
        assert check_spec(self.spec()) == []

    def test_bad_specs_are_reported(self):
        from tools.quests.chat_builder import check_spec
        assert any("trigger_line" in e for e in check_spec(self.spec(trigger_line="")))
        assert any("who" in e for e in check_spec(self.spec(lines=[{"who": "x", "text": "a"}])))
        assert any("unknown key" in e for e in check_spec(self.spec(bogus=1)))
        assert any("scene_editor_id" in e for e in check_spec(self.spec(scene_editor_id="a b")))

    def test_scene_has_one_phase_per_line_and_links_actions_to_topics(self):
        import struct
        from tools.quests.chat_builder import scene_record
        rec = scene_record(0x01000800, "ChatA", 0x01000801, [0, 1], [(0, 0x01000802, 1), (1, 0x01000803, 0)])
        sigs = [s.signature for s in rec.subrecords]
        assert sigs.count("HNAM") == 4 and sigs.count("WNAM") == 2 and sigs.count("ALID") == 4
        data = [bytes(s.data) for s in rec.subrecords if s.signature == "DATA"]
        assert [struct.unpack("<I", d)[0] for d in data] == [0x01000802, 0x01000803]
        assert bytes(rec.get_subrecord("PNAM").data) == fid_bytes(0x01000801)
        assert [s.signature for s in rec.subrecords][-3:] == ["PNAM", "INAM", "VNAM"]

    def test_trigger_guards_and_new_conditions_are_known(self):
        from tools.npcs import npc_builder as nb
        from tools.quests.chat_builder import trigger_conditions
        conds = trigger_conditions(self.spec(partner_reference="Skyrim.esm:013487"))
        assert {c["function"] for c in conds} <= set(nb.CONDITION_FUNCTIONS)
        assert any(c["function"] == "GetDistance" and c["run_on"] == "target" for c in conds)
        assert not any(c["function"] == "GetInFaction" for c in trigger_conditions(self.spec(hired=False)))
        for name in ("GetDayOfWeek", "GetSitting", "IsInCombat", "IsTalking", "IsRaining", "GetIsRace"):
            assert name in nb.CONDITION_FUNCTIONS
