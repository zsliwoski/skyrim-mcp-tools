"""Tools that write plugins, run against the real Skyrim.esm but write only to a temp output folder."""
import zipfile

import pytest
from esplib import Plugin

import server

pytestmark = pytest.mark.game

FETCH_SPEC = {
    "quest_editor_id": "TestFetchQuest", "quest_name": "The Test Wood",
    "item_editor_id": "TestWood", "item_name": "Test Wood", "item_clone_from": "Firewood01",
    "placement": {"worldspace": "Tamriel", "grid_x": 4, "grid_y": -11, "x": 18902.0, "y": -43515.0, "z": -105.0},
    "log": {"10": "Find it.", "20": "Bring it back.", "30": "Done."},
    "objectives": {"10": "Find the wood", "20": "Return the wood"},
    "offer": {"prompt": "Anything I can help with?", "lines": ["Fetch my wood."]},
    "reminder": {"prompt": "Where was it?", "lines": ["By the block."]},
    "turn_in": {"prompt": "I found it.", "lines": ["Thanks."]},
    "done": {"prompt": "About the wood...", "lines": ["Lovely."]},
    "reward_gold": 100,
}


def sigs(path):
    plugin = Plugin.load(str(path))
    out = {}
    for r in plugin.records:
        out.setdefault(r.signature, []).append(r.editor_id)
    return out


@pytest.fixture
def npc_plugin(sandbox):
    res = server.create_npc({
        "plugin_name": "TestMod.esp", "editor_id": "TestSven", "name": "Test Sven", "clone_from": "Sven",
        "dialogue": [{"kind": "topic", "prompt": "Hello there", "lines": ["Hi."]}],
    })
    assert "error" not in res, res
    return sandbox / "TestMod.esp"


class TestCreateNpc:
    def test_clone_records_and_place_and_add_inventory(self, npc_plugin):
        res = server.clone_records("TestMod.esp", [
            {"source": "Book1CheapHorkers", "editor_id": "TestBookA", "name": "A", "signature": "BOOK"},
            {"source": "Book1CheapHorkers", "editor_id": "TestBookB", "name": "B", "signature": "BOOK"}])
        assert [c["editor_id"] for c in res["created"]] == ["TestBookA", "TestBookB"] and not res["errors"]
        assert len({c["form_id"] for c in res["created"]}) == 2
        server._models.clear()  # the suite disables _invalidate; the new books must be visible to the next call
        res = server.edit_npc("TestSven", {"add_inventory": [{"item": "TestBookA", "count": 2}]}, "TestMod.esp")
        assert "error" not in res, res
        server._models.clear()
        npc = server.get_npc("TestSven", plugins=["Skyrim.esm", str(npc_plugin)])
        assert any(i["editor_id"] == "TestBookA" and i["count"] == 2 for i in npc["inventory"])

    def test_place_item_in_a_cell(self, npc_plugin):
        assert "error" in server.clone_records("Missing.esp", [])
        made = server.create_cell("TestMod.esp", "TestHall", "Test Hall")
        assert "error" not in made, made
        server._models.clear()
        server.clone_records("TestMod.esp", [
            {"source": "Book1CheapHorkers", "editor_id": "TestPlacedBook", "name": "P", "signature": "BOOK"}])
        server._models.clear()
        res = server.place_item("TestPlacedBook", "TestMod.esp", "TestHall", 1.0, 2.0, 3.0)
        assert "error" not in res, res
        server._models.clear()
        seen = server.get_cell_contents("TestHall", signature="BOOK", plugins=["Skyrim.esm", "TestMod.esp"])
        assert [r["editor_id"] for r in seen["references"]] == ["TestPlacedBook"]

    def test_writes_npc_and_dialogue(self, npc_plugin):
        found = sigs(npc_plugin)
        assert found["NPC_"] == ["TestSven"]
        assert found["QUST"] == ["TestSvenDialogueQuest"]
        assert {"DIAL", "DLBR", "INFO"} <= set(found)

    def test_answers_make_several_responses_under_one_topic(self, sandbox):
        import struct
        res = server.create_npc({
            "plugin_name": "Rum.esp", "editor_id": "RumSven", "name": "Rum Sven", "clone_from": "Sven",
            "dialogue": [
                {"kind": "topic", "prompt": "Heard any rumors?", "answers": [["One."], ["Two.", "Two b."], ["Three."]]},
                {"kind": "topic", "prompt": "What about Sven?", "pick": "in_order",
                 "answers": [["First."], ["Second."], ["Last."]]}]})
        assert "error" not in res, res
        plugin = Plugin.load(str(sandbox / "Rum.esp"))
        flags = [struct.unpack("<HH", bytes(r.get_subrecord("ENAM").data[:4]))[0]
                 for r in plugin.records if r.signature == "INFO"]
        assert len([r for r in plugin.records if r.signature == "DIAL"]) == 2
        assert flags[:3] == [2, 2, 2] and flags[3:] == [4, 4, 0]

    def test_answers_validation(self, sandbox):
        base = {"plugin_name": "Bad.esp", "editor_id": "X", "name": "X", "clone_from": "Sven"}
        for entry in ({"prompt": "a", "lines": ["x"], "answers": [["y"]]},
                      {"prompt": "a", "answers": [[]]},
                      {"prompt": "a", "answers": [["y"], ["z"]], "code": "Debug.Trace(\"x\")"},
                      {"prompt": "a", "answers": [["y"], ["z"]], "pick": "nope"}):
            assert "error" in server.create_npc({**base, "dialogue": [{"kind": "topic", **entry}]}), entry

    def test_refuses_to_overwrite(self, npc_plugin):
        res = server.create_npc({"plugin_name": "TestMod.esp", "editor_id": "Other", "name": "Other",
                                 "clone_from": "Sven"})
        assert "error" in res

    def test_overwrite_flag(self, npc_plugin):
        res = server.create_npc({"plugin_name": "TestMod.esp", "editor_id": "Other", "name": "Other",
                                 "clone_from": "Sven"}, overwrite=True)
        assert "error" not in res
        assert sigs(npc_plugin)["NPC_"] == ["Other"]

    def test_missing_required_fields_are_errors(self, sandbox):
        assert "error" in server.create_npc({"plugin_name": "Bad.esp", "clone_from": "Sven"})
        assert not (sandbox / "Bad.esp").exists()

    def test_unknown_reference_is_an_error(self, sandbox):
        res = server.create_npc({"plugin_name": "Bad.esp", "editor_id": "X", "name": "X",
                                 "clone_from": "ZzzNoSuchNpc"})
        assert "error" in res

    def test_follower_adds_hiring_topics_and_hides_normal_ones(self, sandbox):
        res = server.create_npc({
            "plugin_name": "Fol.esp", "editor_id": "FolSven", "name": "Fol Sven", "clone_from": "Sven",
            "follower": {"cost": 25},
            "dialogue": [{"kind": "topic", "prompt": "Nice day", "lines": ["Yes."]}],
        })
        assert "error" not in res, res
        plugin = Plugin.load(str(sandbox / "Fol.esp"))
        prompts = [r.get_subrecord("FULL") for r in plugin.records if r.signature == "DIAL"]
        assert len([p for p in prompts if p is not None]) == 6  # chat + 2 hire + wait + follow + dismiss


class TestDrafts:
    def test_draft_lifecycle(self, sandbox):
        res = server.create_npc_draft("d1", {"plugin_name": "Draft.esp", "editor_id": "DraftNpc",
                                             "name": "Draft", "clone_from": "Sven"})
        assert res["draft_id"] == "d1" and not res.get("errors")
        assert server.get_npc_draft("")["drafts"] == ["d1"]
        server.add_npc_dialogue("d1", ["Greetings."], "Hi")
        server.add_npc_dialogue("d1", ["Welcome."], kind="greeting")
        assert len(server.get_npc_draft("d1")["dialogue"]) == 2
        server.update_npc_draft("d1", {"name": "Renamed"}, remove=["dialogue"])
        spec = server.get_npc_draft("d1")
        assert spec["name"] == "Renamed" and "dialogue" not in spec
        assert not server.validate_npc_draft("d1").get("errors")
        written = server.write_npc_plugin("d1")
        assert "error" not in written
        assert sigs(sandbox / "Draft.esp")["NPC_"] == ["DraftNpc"]

    def test_duplicate_draft_id(self, sandbox):
        fields = {"editor_id": "A", "name": "A", "clone_from": "Sven"}
        server.create_npc_draft("dup", fields)
        assert "error" in server.create_npc_draft("dup", fields)

    def test_unknown_draft(self, sandbox):
        with pytest.raises(ValueError):
            server.get_npc_draft("nope")

    def test_validation_reports_bad_reference(self, sandbox):
        res = server.create_npc_draft("bad", {"editor_id": "A", "name": "A", "clone_from": "ZzzNoSuchNpc"})
        assert res.get("errors")


class TestEditNpc:
    def test_patches_vanilla_npc(self, sandbox):
        res = server.edit_npc("Sven", {"name": "Svenny"}, "Patch.esp")
        assert "error" not in res, res
        plugin = Plugin.load(str(sandbox / "Patch.esp"))
        (rec,) = [r for r in plugin.records if r.signature == "NPC_"]
        assert rec.editor_id == "Sven"

    def test_edits_own_npc_in_place_and_backs_up(self, npc_plugin):
        assert "error" not in server.edit_npc("TestSven", {"name": "Renamed"}, "TestMod.esp")
        assert npc_plugin.with_name("TestMod.esp.bak").exists()
        assert sigs(npc_plugin)["NPC_"] == ["TestSven"]

    def test_clone_from_not_allowed(self, npc_plugin):
        assert "error" in server.edit_npc("TestSven", {"clone_from": "Lydia"}, "TestMod.esp")

    def test_official_master_refused(self, sandbox):
        assert "error" in server.edit_npc("Sven", {"name": "X"}, "Skyrim.esm")

    def test_unknown_npc(self, sandbox):
        assert "error" in server.edit_npc("ZzzNoSuchNpc", {"name": "X"}, "Patch.esp")


class TestFace:
    def test_edit_face_round_trip(self, npc_plugin):
        before = server.get_npc_face("TestSven", "TestMod.esp")["face"]["nose_length"]
        res = server.edit_npc_face("TestSven", {"nose_length": -0.9, "jaw_height": 0.5}, "TestMod.esp")
        assert "error" not in res, res
        face = server.get_npc_face("TestSven", "TestMod.esp")["face"]
        assert face["nose_length"] == pytest.approx(-0.9, abs=0.01) and face["nose_length"] != before
        assert face["jaw_height"] == pytest.approx(0.5, abs=0.01)

    def test_unknown_slider(self, npc_plugin):
        assert "error" in server.edit_npc_face("TestSven", {"not_a_slider": 1}, "TestMod.esp")


class TestRecords:
    def test_edit_record_makes_patch(self, sandbox):
        res = server.edit_record("Firewood01", "RecPatch.esp", edits=[{"sig": "FULL", "string": "Kindling"}])
        assert "error" not in res, res
        assert server.get_record("Firewood01", plugin="RecPatch.esp")["name"] == "Kindling"

    def test_edit_record_unknown(self, sandbox):
        assert "error" in server.edit_record("ZzzNoSuchRecord", "RecPatch.esp", edits=[])

    def test_create_record(self, sandbox):
        res = server.create_record("Rec.esp", "MISC", "TestMisc", [
            {"sig": "FULL", "text": "Test Misc"}, {"sig": "DATA", "hex": "0a00000000000000"}])
        assert "error" not in res, res
        assert sigs(sandbox / "Rec.esp") == {"MISC": ["TestMisc"]}

    def test_create_record_duplicate_editor_id(self, sandbox):
        args = ("Rec.esp", "MISC", "TestMisc", [{"sig": "FULL", "text": "x"}])
        server.create_record(*args)
        assert "error" in server.create_record(*args)


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


class TestPackage:
    def test_package_includes_seq_for_start_game_quests(self, npc_plugin):
        pkg = server.package_plugin("TestMod.esp")
        with zipfile.ZipFile(pkg["zip"]) as z:
            seq = z.read("SEQ/TestMod.seq")
        assert len(seq) % 4 == 0 and len(seq) >= 4

    def test_package_missing_plugin(self, sandbox):
        assert "error" in server.package_plugin("Missing.esp")
