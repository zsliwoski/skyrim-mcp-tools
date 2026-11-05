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
