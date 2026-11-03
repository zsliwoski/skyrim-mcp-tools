"""Pure-logic tests for the radiant quest builder."""
from tools.quests.radiant_builder import check_spec, offer_code, turn_in_code

LINES = {"prompt": "p", "lines": ["l"]}


def spec(**kw):
    base = {"quest_editor_id": "T_Q", "quest_name": "T", "offer": LINES, "reminder": LINES, "turn_in": LINES,
            "items": [{"item": "A", "name": "A", "min": 1, "max": 3, "unit_reward": 10},
                      {"item": "B", "name": "B", "min": 2, "max": 2, "unit_reward": 5}]}
    base.update(kw)
    return base


def test_valid_spec():
    assert check_spec(spec()) == []


def test_missing_and_bad_fields():
    assert check_spec({})
    assert check_spec(spec(items=[]))
    assert check_spec(spec(quest_editor_id="bad id"))
    assert check_spec(spec(items=[{"item": "A", "name": "A", "min": 3, "max": 1, "unit_reward": 1}]))


def test_offer_code_picks_each_item():
    code = offer_code(spec(on_accept="Debug.Trace(\"x\")"), 2)
    assert "Utility.RandomInt(0, 1)" in code
    assert "pick == 1" in code and "Debug.Trace" in code
    assert "SetStage(10)" in code


def test_turn_in_code_pays_and_completes():
    code = turn_in_code(spec(on_complete="pl.AddItem(Gold001)"), 2)
    assert "need * 10" in code and "need * 5" in code
    assert "pl.AddItem" in code and "SetStage(20)" in code
