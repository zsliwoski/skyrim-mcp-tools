"""Kill quest builder: spec checks, script source and a full build against the fixture master (compiler stubbed)."""
from pathlib import Path

import pytest

import server
from helpers import sigs
from tools.quests import kill_quest_builder as kq

LINES = {"prompt": "p", "lines": ["l"]}


def spec(**kw):
    base = {"quest_editor_id": "TestKill", "quest_name": "Kill It", "target_base": "Sven",
            "placement": {"worldspace": "Tamriel", "grid_x": 4, "grid_y": -11, "x": 18902.0, "y": -43515.0,
                          "z": -105.0, "rotation_z": 90.0},
            "prerequisite_quest": "TestSvenDialogueQuest", "log": {"10": "a", "20": "b", "30": "c"},
            "objectives": {"10": "Kill", "20": "Return"}, "offer": LINES, "turn_in": LINES, "done": LINES}
    base.update(kw)
    return base


def test_check_spec():
    assert kq.check_spec(spec()) == []
    assert len(kq.check_spec({})) == len(kq.REQUIRED)
    assert kq.check_spec(spec(quest_editor_id="bad id"))
    assert kq.check_spec(spec(offer={"prompt": "x"}))
    assert kq.check_spec(spec(log={"10": "a"}))
    assert kq.check_spec(spec(objectives={"10": "a"}))
    assert kq.check_spec(spec(during={"prompt": "x", "lines": ["y"], "topics": [{"prompt": "z"}]}))
    assert kq.check_spec(spec(during={"prompt": "x", "lines": ["y"], "topics": [{"prompt": "z", "lines": ["q"]}]})) == []


def test_death_source():
    src = kq.death_source("S")
    assert src.startswith("Scriptname S Extends Actor") and "SetStage(20)" in src


@pytest.mark.fakegame
class TestBuild:
    @pytest.fixture(autouse=True)
    def stub_compiler(self, monkeypatch):
        monkeypatch.setattr(kq, "compile_script", lambda name, src, d: Path(d) / f"{name}.pex")
        monkeypatch.setattr("tools.npcs.npc_builder.compile_script", lambda name, src, d, *a, **k: Path(d) / f"{name}.pex")

    def test_builds_target_quest_and_dialogue(self, npc_plugin):
        before = sum(1 for _ in sigs(npc_plugin)["DIAL"])
        during = {"prompt": "Tell me more", "lines": ["Hm."], "topics": [{"prompt": "Where?", "lines": ["There."]}]}
        res = server.add_kill_quest("TestSven", spec(during=during, reward_gold=50), "TestMod.esp")
        assert "error" not in res, res
        found = sigs(npc_plugin)
        assert "TestKill" in found["QUST"] and "TestKillTarget" in found["ACHR"]
        assert len(found["DIAL"]) > before + 3
        assert any(r["type"] == "script" for r in res["records"])

    def test_default_dialogue_quest_fallback_and_no_gold(self, npc_plugin):
        res = server.add_kill_quest("TestSven", spec(dialogue_quest_editor_id="Nope"), "TestMod.esp")
        assert "error" not in res, res

    @pytest.mark.parametrize("change", [
        {"quest_editor_id": "TestSvenDialogueQuest"},
        {"prerequisite_quest": "ZzzNone"},
        {"quest_name": None},
    ])
    def test_rejects(self, npc_plugin, change):
        s = spec(**change)
        if s["quest_name"] is None:
            del s["quest_name"]
        assert "error" in server.add_kill_quest("TestSven", s, "TestMod.esp")

    def test_unknown_npc(self, npc_plugin):
        assert "error" in server.add_kill_quest("ZzzNoNpc", spec(), "TestMod.esp")
