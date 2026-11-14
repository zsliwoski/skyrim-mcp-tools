"""Quest authoring (needs the Papyrus compiler)."""
import zipfile

import pytest

import server
from helpers import FETCH_SPEC, sigs

pytestmark = pytest.mark.game


@pytest.mark.compiler
@pytest.mark.usefixtures("compiler_available")
class TestQuests:
    def test_fetch_quest_and_package(self, npc_plugin):
        res = server.add_fetch_quest("TestSven", FETCH_SPEC, "TestMod.esp")
        assert "error" not in res, res
        found = sigs(npc_plugin)
        assert "TestFetchQuest" in found["QUST"] and found["MISC"] == ["TestWood"]
        assert found["REFR"] == ["TestWoodRef"]
        # the offer, reminder, turn-in and done topics join the NPC's existing dialogue quest
        assert sum(1 for d in found["DIAL"]) == 5

        pkg = server.package_plugin("TestMod.esp")
        assert "error" not in pkg, pkg
        with zipfile.ZipFile(pkg["zip"]) as z:
            names = z.namelist()
        assert "TestMod.esp" in names and "SEQ/TestMod.seq" in names
        assert any(n.endswith("TestMod_TestFetchQuest_Pickup.pex") for n in names)
        assert sum(1 for n in names if "TIF__TestMod_" in n) >= 2

    def test_fetch_item_is_unowned_quest_object(self, npc_plugin):
        from esplib import Plugin
        assert "error" not in server.add_fetch_quest("TestSven", FETCH_SPEC, "TestMod.esp")
        plug = Plugin(str(npc_plugin))
        ref = next(r for r in plug.records if r.editor_id == "TestWoodRef")
        assert bytes(ref.get_subrecord("XOWN").data) == bytes.fromhex("07000000")
        assert [s.signature for s in ref.subrecords].index("XOWN") < [s.signature for s in ref.subrecords].index("DATA")
        quest = next(r for r in plug.records if r.editor_id == "TestFetchQuest")
        alias_flags = [int.from_bytes(bytes(s.data), "little") for s in quest.subrecords if s.signature == "FNAM"
                       and len(s.data) == 4]
        assert 6 in alias_flags

    def test_fetch_quest_rejects_bad_spec(self, npc_plugin):
        assert "error" in server.add_fetch_quest("TestSven", {"quest_editor_id": "Q"}, "TestMod.esp")

    def test_fetch_quest_unknown_npc(self, npc_plugin):
        assert "error" in server.add_fetch_quest("ZzzNoSuchNpc", FETCH_SPEC, "TestMod.esp")

    def test_alias_and_stage_script(self, npc_plugin):
        assert "error" not in server.add_fetch_quest("TestSven", FETCH_SPEC, "TestMod.esp")
        res = server.add_quest_alias("TestFetchQuest", "TestMod.esp", "Helper", npc="Lydia")
        assert "error" not in res, res
        res = server.add_quest_stage_script("TestFetchQuest", "TestMod.esp",
                                            {"20": "SetObjectiveDisplayed(20)"})
        assert "error" not in res, res
        pkg = server.package_plugin("TestMod.esp")
        with zipfile.ZipFile(pkg["zip"]) as z:
            assert any("QF__TestMod_" in n for n in z.namelist())

    def test_stage_script_needs_existing_stage(self, npc_plugin):
        assert "error" in server.add_quest_stage_script("TestSvenDialogueQuest", "TestMod.esp",
                                                       {"99": "SetStage(10)"})

    def test_npc_chat_builds_scene_and_trigger(self, npc_plugin):
        from esplib import Plugin
        spec = {"scene_editor_id": "TestChat", "quest_editor_id": "TestChatQuest", "speaker": "TestSven",
                "partner": "Lydia", "trigger_line": "Is that Lydia?",
                "lines": [{"who": "speaker", "text": "Well met."}, {"who": "partner", "text": "Hm."}]}
        res = server.add_npc_chat("TestMod.esp", spec)
        assert "error" not in res, res
        plug = Plugin(str(npc_plugin))
        scene = next(r for r in plug.records if r.editor_id == "TestChat")
        assert sum(1 for s in scene.subrecords if s.signature == "DATA") == 2
        quest = next(r for r in plug.records if r.editor_id == "TestChatQuest")
        assert sum(1 for s in quest.subrecords if s.signature == "ALUA") == 2
        pkg = server.package_plugin("TestMod.esp")
        with zipfile.ZipFile(pkg["zip"]) as z:
            assert any("TIF__TestMod_" in n for n in z.namelist())
        assert "error" in server.add_npc_chat("TestMod.esp", spec)

    def test_follower_controls_build_factions_packages_and_topics(self, npc_plugin):
        from esplib import Plugin
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven"})
        assert "error" not in res, res
        plug = Plugin(str(npc_plugin))
        ids = {r.editor_id: r for r in plug.records if r.editor_id}
        assert {"TestSvenFollowDistanceFaction", "TestSvenFightingStyleFaction", "TestSvenFollowerControl",
                "TestSvenFollowClosePackage", "TestSvenFollowFarPackage"} <= set(ids)
        pack = ids["TestSvenFollowFarPackage"]
        floats = [bytes(s.data) for s in pack.subrecords if s.signature == "CNAM" and len(s.data) == 4]
        assert [__import__("struct").unpack("<f", f)[0] for f in floats[:2]] == [512.0, 768.0]
        assert sum(1 for s in pack.subrecords if s.signature == "CTDA") == 3
        quest = ids["TestSvenFollowerControl"]
        assert sum(1 for s in quest.subrecords if s.signature == "ALPC") == 2
        npc = ids["TestSven"]
        assert sum(1 for s in npc.subrecords if s.signature == "SNAM") >= 2
        pkg = server.package_plugin("TestMod.esp")
        with zipfile.ZipFile(pkg["zip"]) as z:
            assert sum(1 for n in z.namelist() if "TIF__TestMod_" in n and n.endswith(".pex")) >= 6
        assert "error" in server.add_follower_controls("TestMod.esp", {"npc": "TestSven"})
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "combat_overrides": True})
        assert "error" not in res, res
        plug = Plugin(str(npc_plugin))
        ids = {r.editor_id: r for r in plug.records if r.editor_id}
        assert {"TestSvenCombatCautiousPackage", "TestSvenCombatRecklessPackage",
                "TestSvenCombatOverrideList"} <= set(ids)
        sigs = [s.signature for s in ids["TestSvenFollowerControl"].subrecords]
        assert sigs.count("ECOR") == 1 and sigs.index("ECOR") < sigs.index("ALPC")
        assert sum(1 for s in ids["TestSvenCombatOverrideList"].subrecords if s.signature == "LNAM") == 2
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "horse_sharing": True})
        assert "error" not in res, res
        plug = Plugin(str(npc_plugin))
        ids = {r.editor_id: r for r in plug.records if r.editor_id}
        assert "TestSvenShareHorseFaction" in ids
        quest = ids["TestSvenFollowerControl"]
        assert any(s.signature == "VMAD" for s in quest.subrecords)
        assert [bytes(s.data).rstrip(b"\0") for s in quest.subrecords if s.signature == "ALID"] == [b"Follower", b"Steed"]
        assert (npc_plugin.parent / "Scripts" / "TestSvenControlScript.pex").is_file()
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "horse_radar": True})
        assert "error" not in res, res
        plug = Plugin(str(npc_plugin))
        quest = next(r for r in plug.records if r.editor_id == "TestSvenFollowerControl")
        assert sum(1 for s in quest.subrecords if s.signature == "VMAD") == 1
        assert sum(1 for r in res["records"] if r["type"] == "DIAL") == 1
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "horse_gift": True})
        assert "error" not in res, res
        assert sum(1 for r in res["records"] if r["type"] == "DIAL") == 2
        plug = Plugin(str(npc_plugin))
        quest = next(r for r in plug.records if r.editor_id == "TestSvenFollowerControl")
        assert sum(1 for s in quest.subrecords if s.signature == "VMAD") == 1
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "left_behind": True,
                                                           "map_marker": True})
        assert "error" not in res, res
        assert sum(1 for r in res["records"] if r["type"] == "DIAL") == 3
        plug = Plugin(str(npc_plugin))
        ids = {r.editor_id: r for r in plug.records if r.editor_id}
        assert "TestSvenLeftBehindFaction" in ids
        quest = ids["TestSvenFollowerControl"]
        sigs = [s.signature for s in quest.subrecords]
        assert sigs.count("VMAD") == 1 and sigs.count("QOBJ") == 1 and sigs.count("QSTA") == 1
        assert sigs.index("FULL") < sigs.index("DNAM") < sigs.index("INDX") < sigs.index("QOBJ") < sigs.index("ANAM")
        assert b"LeftBehindFaction" in bytes(next(s for s in quest.subrecords if s.signature == "VMAD").data)
        assert "error" in server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                                       "fighting_style": False, "map_marker": True})
        res = server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                           "fighting_style": False, "summon": True})
        assert "error" not in res, res
        assert sum(1 for r in res["records"] if r["type"] == "DIAL") == 2
        assert "Extends ActiveMagicEffect" in res["summon_spell"]["source"]
        plug = Plugin(str(npc_plugin))
        quest = next(r for r in plug.records if r.editor_id == "TestSvenFollowerControl")
        vmad = bytes(next(s for s in quest.subrecords if s.signature == "VMAD").data)
        assert b"SummonTopic" in vmad and b"CaughtUpTopic" in vmad and b"LeftBehindFaction" in vmad

    def test_summon_needs_left_behind(self, npc_plugin):
        assert "error" in server.add_follower_controls("TestMod.esp", {"npc": "TestSven", "follow_distance": False,
                                                                       "fighting_style": False, "summon": True})

    def test_commentary_presets_add_idle_topics(self, npc_plugin):
        res = server.edit_npc("TestSven", {"weather_lines": True, "time_lines": True, "main_quest_lines": True,
                                           "place_lines": True, "quest_editor_id": "TestSvenCommentQuest"},
                              "TestMod.esp")
        assert "error" not in res and not res.get("errors"), res
        assert sum(1 for r in res["records"] if r["type"] == "DIAL") == 19
        assert "error" in server.edit_npc("TestSven", {"weather_lines": True, "commentary_text": {"nope": ["x"]}},
                                          "TestMod.esp")

    def test_create_faction_and_gated_package(self, npc_plugin):
        res = server.create_faction("TestMod.esp", "TestCtlFaction", "Control", ["A", "B"])
        assert "error" not in res, res
        res = server.create_package("TestMod.esp", "TestCtlPackage", conditions=[
            {"function": "GetFactionRank", "form": "TestCtlFaction", "op": "==", "value": 1}], radii=[100, 200])
        assert "error" not in res, res
        assert "error" in server.create_package("TestMod.esp", "TestCtlPackage2", radii=[1, 2, 3, 4, 5, 6])
