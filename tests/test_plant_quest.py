"""Plant quest builder: spec checks, script source and a full build against the fixture master (compiler stubbed)."""
from pathlib import Path

import pytest

import server
from helpers import sigs
from tools.quests import plant_quest_builder as pq

LINES = {"prompt": "p", "lines": ["l"]}


def spec(**kw):
    base = {"quest_editor_id": "TestPlant", "quest_name": "Plant It", "prerequisite_quest": "TestSvenDialogueQuest",
            "book": {"editor_id": "TestPlantBook", "name": "A Book", "text": "Words.", "clone_from": "Book1CheapHorkers"},
            "container_ref": "0xD3C98",
            "reward_weapon": {"editor_id": "TestPlantAxe", "name": "Axe", "clone_from": "IronSword",
                              "enchantment": "TestEnch", "damage": 9, "value": 77},
            "log": {"10": "a", "20": "b", "30": "c"}, "objectives": {"10": "Plant", "20": "Return"},
            "offer": LINES, "turn_in": LINES, "done": LINES}
    base.update(kw)
    return base


def test_check_spec():
    assert pq.check_spec(spec()) == []
    assert len(pq.check_spec({})) == len(pq.REQUIRED)
    assert pq.check_spec(spec(quest_editor_id="bad id"))
    assert pq.check_spec(spec(book={"editor_id": "x"}))
    assert pq.check_spec(spec(offer={}))
    assert pq.check_spec(spec(log={"10": "a"}))
    assert pq.check_spec(spec(objectives={"20": "a"}))
    assert pq.check_spec(spec(during={"prompt": "x"}))
    assert pq.check_spec(spec(lost_book={"prompt": "x"}))
    assert pq.check_spec(spec(lost_book=LINES)) == []


def test_plant_source():
    src = pq.plant_source("S", 0x123)
    assert "Extends ObjectReference" in src and "== 291" in src


@pytest.mark.fakegame
class TestBuild:
    @pytest.fixture(autouse=True)
    def stub_compiler(self, monkeypatch):
        monkeypatch.setattr(pq, "compile_script", lambda name, src, d: Path(d) / f"{name}.pex")
        monkeypatch.setattr("tools.npcs.npc_builder.compile_script", lambda name, src, d, *a, **k: Path(d) / f"{name}.pex")

    @pytest.fixture
    def ench(self, monkeypatch):
        """The fixture master has no ENCH and its cell is unnamed, so stand in for both lookups."""
        monkeypatch.setattr(pq, "persist_reference", lambda b, abs_id, made: 0)
        from tools.npcs.npc_builder import Resolver
        real = Resolver.lookup
        monkeypatch.setattr(Resolver, "lookup",
                            lambda self, q, sigs: (0x10A7E1, None) if q == "TestEnch" else real(self, q, sigs))

    def test_builds_book_weapon_quest(self, npc_plugin, ench):
        extra = {"during": {"prompt": "More?", "lines": ["Yes."], "topics": [{"prompt": "Why?", "lines": ["So."]}]},
                 "lost_book": {"prompt": "Lost it", "lines": ["Again."]}, "reward_gold": 25}
        res = server.add_plant_quest("TestSven", spec(**extra), "TestMod.esp")
        assert "error" not in res, res
        found = sigs(npc_plugin)
        assert found["BOOK"] == ["TestPlantBook"] and found["WEAP"] == ["TestPlantAxe"]
        assert "TestPlant" in found["QUST"]

    def test_lost_book_only_and_weapon_defaults(self, npc_plugin, ench):
        s = spec(lost_book=LINES)
        s["reward_weapon"] = {k: v for k, v in s["reward_weapon"].items() if k not in ("damage", "value")}
        assert "error" not in server.add_plant_quest("TestSven", s, "TestMod.esp")

    @pytest.mark.parametrize("change", [
        {"container_ref": "zz"},
        {"prerequisite_quest": "ZzzNone"},
        {"quest_editor_id": "TestSvenDialogueQuest"},
    ])
    def test_rejects(self, npc_plugin, ench, change):
        assert "error" in server.add_plant_quest("TestSven", spec(**change), "TestMod.esp")

    def test_unknown_npc(self, npc_plugin, ench):
        assert "error" in server.add_plant_quest("ZzzNoNpc", spec(), "TestMod.esp")
