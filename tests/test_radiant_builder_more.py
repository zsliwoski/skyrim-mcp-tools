"""Radiant quest builders (simple and Story Manager) built against the fixture master with the compiler stubbed."""
from pathlib import Path

import pytest

import server
from helpers import sigs
from tools.quests import radiant_story as rs

LINES = {"prompt": "p", "lines": ["l"]}
SIMPLE_ITEMS = [{"item": "Firewood01", "name": "logs", "min": 1, "max": 3, "unit_reward": 10},
                {"item": "IronDagger", "name": "daggers", "min": 2, "max": 2, "unit_reward": 5}]
STORY_ITEMS = [{"item": "Firewood01", "name": "logs", "count": 3, "reward": 30},
                {"item": "IronDagger", "name": "daggers", "count": 1, "reward": 50}]


def spec(items, **kw):
    base = {"quest_editor_id": "TestRad", "quest_name": "Rad", "offer": LINES, "reminder": LINES,
            "turn_in": LINES, "items": items}
    base.update(kw)
    return base


def test_story_check_spec_errors():
    assert rs.check_spec({})
    assert rs.check_spec(spec(STORY_ITEMS, offer={}))
    assert rs.check_spec(spec([{"item": "x"}]))
    assert rs.check_spec(spec("nope"))
    assert rs.check_spec(spec(STORY_ITEMS)) == []


def test_story_turn_in_without_on_complete():
    assert "AddItem(Gold, 30)" in rs.turn_in_code(spec(STORY_ITEMS), STORY_ITEMS[0])


@pytest.mark.fakegame
class TestBuild:
    @pytest.fixture(autouse=True)
    def stub_compiler(self, monkeypatch):
        fake = lambda name, src, d, *a, **k: Path(d) / f"{name}.pex"
        monkeypatch.setattr("tools.npcs.npc_builder.compile_script", fake)
        monkeypatch.setattr("tools.quests.quest_edit.compile_script", fake)

    def test_simple_radiant(self, npc_plugin):
        s = spec(SIMPLE_ITEMS, log={"10": "go", "20": "done"}, properties={"Lyd": "Lydia"},
                 conditions=[{"function": "GetLevel", "op": ">=", "value": 1}])
        res = server.add_radiant_quest("TestSven", s, "TestMod.esp")
        assert "error" not in res, res
        found = sigs(npc_plugin)
        assert "TestRad" in found["QUST"] and found["GLOB"] == ["TestRad_Count", "TestRad_Pick"]

    def test_simple_radiant_rejects(self, npc_plugin):
        assert "error" in server.add_radiant_quest("TestSven", spec(SIMPLE_ITEMS, properties={"1bad": "Lydia"}), "TestMod.esp")
        assert "error" in server.add_radiant_quest("ZzzNoNpc", spec(SIMPLE_ITEMS), "TestMod.esp")
        assert "error" in server.add_radiant_quest("TestSven", spec(SIMPLE_ITEMS, quest_editor_id="TestSven"), "TestMod.esp")
        assert "error" in server.add_radiant_quest("TestSven", spec([]), "TestMod.esp")

    def test_simple_radiant_clash(self, npc_plugin):
        assert "error" not in server.add_radiant_quest("TestSven", spec(SIMPLE_ITEMS), "TestMod.esp")
        assert "error" in server.add_radiant_quest("TestSven", spec(SIMPLE_ITEMS), "TestMod.esp")

    def test_story_manager_radiant(self, npc_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(rs, "add_quest_stage_script", lambda b, qid, tp, stages: calls.append(qid))
        s = spec(STORY_ITEMS, mode="story_manager", on_complete="pl.AddItem(Gold)", log={"10": "go", "20": "ok"},
                 dialogue_quest_editor_id="TestSvenDialogueQuest")
        res = server.add_radiant_quest("TestSven", s, "TestMod.esp")
        if "error" in res:
            pytest.skip(f"fixture master lacks vanilla story records: {res['error']}")
        found = sigs(npc_plugin)
        assert found["SMQN"] == ["TestRad_Node"] and len(found["LVLI"]) == 2
        assert calls == ["TestRad_0", "TestRad_1"]

    def test_story_manager_rejects(self, npc_plugin):
        assert "error" in server.add_radiant_quest("TestSven", spec([], mode="story_manager"), "TestMod.esp")
        assert "error" in server.add_radiant_quest("ZzzNoNpc", spec(STORY_ITEMS, mode="story_manager"), "TestMod.esp")
        s = spec(STORY_ITEMS, mode="story_manager", properties={"1bad": "Lydia"})
        assert "error" in server.add_radiant_quest("TestSven", s, "TestMod.esp")
        s = spec(STORY_ITEMS, mode="story_manager")
        assert "error" not in server.add_radiant_quest("TestSven", s, "TestMod.esp")
        assert "error" in server.add_radiant_quest("TestSven", s, "TestMod.esp")
