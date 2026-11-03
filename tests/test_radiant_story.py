"""Pure-logic tests for the Story Manager radiant quest builder."""
import struct

from tools.quests.radiant_story import (BOSS_CONTAINER, LOC_TYPE_DUNGEON, check_spec, event_condition,
                                     location_conditions, offer_code, turn_in_code)

LINES = {"prompt": "p", "lines": ["l"]}
ITEM = {"item": "GemRuby", "name": "rubies", "count": 3, "reward": 150}


def spec(**kw):
    base = {"quest_editor_id": "T_Q", "quest_name": "T", "offer": LINES, "reminder": LINES, "turn_in": LINES,
            "items": [ITEM]}
    base.update(kw)
    return base


def test_spec_checks():
    assert check_spec(spec()) == []
    assert check_spec(spec(items=[]))
    assert check_spec(spec(items=[dict(ITEM, count=0)]))
    assert check_spec(spec(quest_editor_id="bad id"))


def test_event_condition_matches_vanilla_layout():
    # vanilla MGRUragNode: GetEventData 0x240, 'K1' keyword slot, keyword 0x00028A54, run on subject
    vanilla = bytes.fromhex("000000000000803f4002000000004b31548a02000000000000000000ffffffff")
    got = event_condition(0x28A54)
    assert len(got) == 32
    assert got[4:] == vanilla[4:] or got[4:] == vanilla[4:12] + b"\0\0K1" + vanilla[16:]


def test_location_conditions_match_vanilla():
    vanilla = [bytes.fromhex("000000000000803f32020000db300100000000000000000000000000ffffffff"),
               bytes.fromhex("000000000000803f33020000f8300100000000000000000000000000ffffffff")]
    assert location_conditions(LOC_TYPE_DUNGEON, BOSS_CONTAINER) == vanilla


def test_scripts():
    assert offer_code(2).splitlines() == ["(Q0 as Quest).Reset()", "(Q1 as Quest).Reset()",
                                          "(EventKw as Keyword).SendStoryEvent(None, None, None, 0, 0)"]
    code = turn_in_code(spec(on_complete="pl.AddItem(Gold)"), ITEM)
    assert "GetItemCount(want) >= 3" in code and "AddItem(Gold, 150)" in code and "q.Stop()" in code
    assert struct.calcsize('<B3xfHH4sIIIi') == 32
