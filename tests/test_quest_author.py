"""General quest authoring: create_quest, add_quest_stage, add_quest_objective, build_quest."""
import struct

import pytest

import server
from esplib import Plugin

pytestmark = pytest.mark.fakegame


def quest(path, editor_id):
    return next(r for r in Plugin(str(path)).records if r.editor_id == editor_id)


def order(q, sig):
    return [struct.unpack('<H', bytes(s.data)[:2])[0] for s in q.subrecords if s.signature == sig]


def test_create_and_grow_quest_in_index_order(npc_plugin):
    res = server.create_quest("TestMod.esp", "TestQ", "Test Quest", quest_type="side")
    assert "error" not in res, res
    for st in (30, 10, 20):
        assert "error" not in server.add_quest_stage("TestQ", "TestMod.esp", st, log=f"stage {st}")
    for ix in (20, 10):
        assert "error" not in server.add_quest_objective("TestQ", "TestMod.esp", ix, f"obj {ix}")
    q = quest(npc_plugin, "TestQ")
    assert order(q, 'INDX') == [10, 20, 30] and order(q, 'QOBJ') == [10, 20]
    names = [s.signature for s in q.subrecords]
    assert names.index('ANAM') > max(i for i, n in enumerate(names) if n in ('INDX', 'QOBJ', 'NNAM'))
    assert "error" in server.add_quest_stage("TestQ", "TestMod.esp", 20)
    assert "error" in server.add_quest_objective("TestQ", "TestMod.esp", 10, "again")


def test_create_quest_validation(npc_plugin):
    assert "error" not in server.create_quest("TestMod.esp", "TestQ", "Q")
    assert "error" in server.create_quest("TestMod.esp", "TestQ", "Q")
    assert "error" in server.create_quest("TestMod.esp", "TestQ2", "Q", quest_type="zzz")
    assert "error" in server.create_quest("TestMod.esp", "TestQ3", "Q", flags=["bogus"])


def test_build_quest_with_alias_and_marker(npc_plugin):
    spec = {"editor_id": "TestBuilt", "name": "Built", "quest_type": "Thieves Guild", "flags": ["run_once"],
            "stages": [{"stage": 10, "log": "Start"}, {"stage": 20, "log": "Done", "complete_quest": True}],
            "objectives": [{"index": 10, "text": "Find Lydia"}],
            "aliases": [{"name": "Helper", "npc": "Lydia", "objectives": [10]}]}
    res = server.build_quest("TestMod.esp", spec)
    assert "error" not in res, res
    assert res["stages"] == [10, 20] and res["aliases"] == ["Helper"]
    q = quest(npc_plugin, "TestBuilt")
    assert any(s.signature == 'QSTA' for s in q.subrecords)
    dnam = bytes(next(s for s in q.subrecords if s.signature == 'DNAM').data)
    assert struct.unpack('<HBBII', dnam) == (0x100, 50, 0, 0, 3)


def test_build_quest_is_atomic(npc_plugin):
    spec = {"editor_id": "TestBad", "name": "Bad", "stages": [{"stage": 10}, {"stage": 10}]}
    assert "error" in server.build_quest("TestMod.esp", spec)
    assert all(r.editor_id != "TestBad" for r in Plugin(str(npc_plugin)).records)


@pytest.mark.compiler
@pytest.mark.usefixtures("compiler_available")
def test_stage_code_merges(npc_plugin):
    assert "error" not in server.create_quest("TestMod.esp", "TestQ", "Q")
    for st in (10, 20):
        assert "error" not in server.add_quest_stage("TestQ", "TestMod.esp", st)
    res = server.add_quest_stage_script("TestQ", "TestMod.esp", {"10": "SetStage(20)"})
    assert "error" not in res, res
    res = server.add_quest_stage_script("TestQ", "TestMod.esp", {"20": "CompleteQuest()"}, merge=True)
    assert "error" not in res and res["stages"] == [10, 20], res
    assert "error" not in server.add_quest_stage("TestQ", "TestMod.esp", 30, code="CompleteQuest()")


def alias_blocks(q):
    out, cur = {}, None
    for s in q.subrecords:
        if s.signature in ('ALST', 'ALLS'):
            cur = out.setdefault(len(out), {'start': s.signature, 'subs': []})
        elif cur is not None:
            cur['subs'].append((s.signature, bytes(s.data)))
    return list(out.values())


def test_alias_flags_location_and_external(npc_plugin):
    assert "error" not in server.build_quest("TestMod.esp", {
        "editor_id": "TestQ", "name": "Q", "stages": [{"stage": 10}], "objectives": [{"index": 10, "text": "go"}],
        "aliases": [{"name": "Boss", "npc": "Lydia", "flags": ["essential", "protected"], "objectives": [10]}]})
    blocks = alias_blocks(quest(npc_plugin, "TestQ"))
    fnam = dict(blocks[0]['subs'])['FNAM']
    assert struct.unpack('<I', fnam)[0] == 0x440
    res = server.add_quest_alias("TestQ", "TestMod.esp", "Town", location="Whiterun")
    assert "error" not in res, res
    res = server.add_quest_alias("TestQ", "TestMod.esp", "Same", external={"quest": "TestQ", "alias": "Boss"})
    assert "error" not in res, res
    blocks = alias_blocks(quest(npc_plugin, "TestQ"))
    assert blocks[1]['start'] == 'ALLS' and 'ALFL' in dict(blocks[1]['subs'])
    assert [n for n, _ in blocks[2]['subs']][2:4] == ['ALEQ', 'ALEA']
    assert [n for n, _ in blocks[2]['subs']][-2:] == ['VTCK', 'ALED']
    assert "error" not in server.add_quest_objective("TestQ", "TestMod.esp", 20, "stay", targets=["Town"])
    assert not [e for e in server.validate_plugin("TestMod.esp")["errors"] if "TestQ" in e]
    assert "error" in server.add_quest_alias("TestQ", "TestMod.esp", "Bad", npc="Lydia", location="Whiterun")
    assert "error" in server.add_quest_alias("TestQ", "TestMod.esp", "Bad2", npc="Lydia", flags=["nope"])


def test_edit_and_remove_alias(npc_plugin):
    assert "error" not in server.build_quest("TestMod.esp", {
        "editor_id": "TestQ", "name": "Q", "stages": [{"stage": 10}], "objectives": [{"index": 10, "text": "go"}],
        "aliases": [{"name": "A", "npc": "Lydia", "objectives": [10]}, {"name": "B", "npc": "TestSven"}]})
    assert "error" not in server.edit_quest_alias("TestQ", "TestMod.esp", "B", ["quest_object"])
    assert struct.unpack('<I', dict(alias_blocks(quest(npc_plugin, "TestQ"))[1]['subs'])['FNAM'])[0] == 4
    assert "error" not in server.remove_quest_alias("TestQ", "TestMod.esp", "A")
    q = quest(npc_plugin, "TestQ")
    assert len(alias_blocks(q)) == 1 and not any(s.signature == 'QSTA' for s in q.subrecords)
    assert "error" in server.remove_quest_alias("TestQ", "TestMod.esp", "A")


def vmad(path, editor_id):
    from esplib.vmad import VmadData
    return VmadData.from_record(quest(path, editor_id), 'QUST')


@pytest.mark.compiler
@pytest.mark.usefixtures("compiler_available")
class TestScripts:
    def build(self):
        from helpers import FETCH_SPEC
        assert "error" not in server.add_fetch_quest("TestSven", FETCH_SPEC, "TestMod.esp")
        assert "error" not in server.build_quest("TestMod.esp", {
            "editor_id": "TestQ", "name": "Q", "stages": [{"stage": 10}, {"stage": 20}],
            "aliases": [{"name": "Door", "reference": "TestWoodRef"}]})

    def test_stock_alias_script_and_quest_script_keep_each_other(self, npc_plugin):
        self.build()
        res = server.attach_alias_script("TestQ", "TestMod.esp", "Door", "defaultSetStageAliasScript",
                                         {"stage": 20, "TriggerType": 0})
        assert "error" not in res, res
        assert "error" in server.attach_alias_script("TestQ", "TestMod.esp", "Door", "defaultSetStageAliasScript",
                                                     {"nope": 1})
        assert "error" in server.attach_alias_script("TestQ", "TestMod.esp", "Door", "NoSuchScriptAnywhere")
        src = "Scriptname TestQuestLogic Extends Quest\nInt Property Clues Auto\nReferenceAlias Property Marker Auto\n"
        res = server.attach_quest_script("TestQ", "TestMod.esp", "TestQuestLogic",
                                         {"Clues": 3, "Marker": {"alias": "Door"}}, source=src)
        assert "error" not in res, res
        assert "error" not in server.add_quest_stage_script("TestQ", "TestMod.esp", {"10": "SetStage(20)"})
        vm = vmad(npc_plugin, "TestQ")
        assert {s.name for s in vm.scripts} >= {"TestQuestLogic"} and len(vm.scripts) == 2
        assert [s.name for a in vm.alias_scripts for s in a.scripts] == ["defaultSetStageAliasScript"]

    def test_clue_set(self, npc_plugin):
        self.build()
        res = server.add_clue_set("TestQ", "TestMod.esp", [
            {"alias": "ClueA", "reference": "TestWoodRef", "stage": 30, "log": "Found A"},
            {"alias": "ClueB", "reference": "TestWoodRef", "stage": 40, "log": "Found B"}], done_stage=50,
            done_log="All found")
        assert "error" not in res, res
        q = quest(npc_plugin, "TestQ")
        assert order(q, 'INDX') == [10, 20, 30, 40, 50]
        vm = vmad(npc_plugin, "TestQ")
        scripts = [s for a in vm.alias_scripts for s in a.scripts]
        assert len(scripts) == 2 and all(s.name == "TestQClueScript" for s in scripts)
        assert [p.value for p in scripts[1].properties if p.name == "AllClueStages"] == [[30, 40]]
        assert "error" in server.add_clue_set("TestQ", "TestMod.esp", [])


def test_quest_dialogue_gating_without_code(npc_plugin):
    assert "error" not in server.build_quest("TestMod.esp", {
        "editor_id": "TestQ", "name": "Q", "stages": [{"stage": 10}, {"stage": 20}]})
    res = server.add_quest_dialogue("TestMod.esp", "TestSven", [
        {"prompt": "Seen anything odd?", "lines": ["Not yet."], "stage_max": 9},
        {"prompt": "About the murder.", "lines": ["Terrible business."], "stage_min": 10, "stage_max": 19,
         "not_stage_done": 20}], quest="TestQ")
    assert "error" not in res, res
    assert len(res["topics"]) == 2 and len(res["infos"]) == 2
    funcs = {struct.unpack('<H', bytes(s.data)[8:10])[0] for r in Plugin(str(npc_plugin)).records
             if r.signature == 'INFO' for s in r.subrecords if s.signature == 'CTDA'}
    assert {58, 59} <= funcs
    assert "error" in server.add_quest_dialogue("TestMod.esp", "TestSven",
                                                [{"prompt": "x", "lines": ["y"], "stage_min": 1}])


@pytest.mark.compiler
@pytest.mark.usefixtures("compiler_available")
def test_quest_dialogue_set_stage(npc_plugin):
    assert "error" not in server.build_quest("TestMod.esp", {
        "editor_id": "TestQ", "name": "Q", "stages": [{"stage": 10}, {"stage": 20}]})
    res = server.add_quest_dialogue("TestMod.esp", "TestSven", [
        {"prompt": "I know who did it.", "lines": ["Do tell."], "stage_min": 10, "set_stage": 20}], quest="TestQ")
    assert "error" not in res, res
    assert len(res["scripts"]) == 1


@pytest.mark.compiler
@pytest.mark.usefixtures("compiler_available")
def test_end_to_end_quest_with_script_and_gated_topic(npc_plugin):
    from helpers import FETCH_SPEC
    assert "error" not in server.add_fetch_quest("TestSven", FETCH_SPEC, "TestMod.esp")
    assert "error" not in server.build_quest("TestMod.esp", {
        "editor_id": "TestE2E", "name": "E2E",
        "stages": [{"stage": 10, "log": "Start"}, {"stage": 20, "log": "Middle"},
                   {"stage": 200, "log": "Done", "complete_quest": True}],
        "objectives": [{"index": 10, "text": "Look"}],
        "aliases": [{"name": "Clue", "reference": "TestWoodRef", "objectives": [10]}]})
    assert "error" not in server.attach_alias_script("TestE2E", "TestMod.esp", "Clue",
                                                     "defaultSetStageAliasScript", {"stage": 20})
    assert "error" not in server.add_quest_dialogue("TestMod.esp", "TestSven", [
        {"prompt": "I found it.", "lines": ["Well done."], "stage_min": 20, "set_stage": 200}], quest="TestE2E")
    s = server.get_quest_structure("TestE2E", plugins=["TestMod.esp"])
    assert "error" not in s, s
    text = str(s)
    for needle in ("Clue", "Middle", "defaultSetStageAliasScript"):
        assert needle in text, (needle, text)
    assert order(quest(npc_plugin, "TestE2E"), 'INDX') == [10, 20, 200]
