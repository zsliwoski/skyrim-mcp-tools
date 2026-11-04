"""Shared helpers for the plugin-writing tests."""
from esplib import Plugin

import server

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


def make_hall():
    """Ench.esp with an enchantment (TestEnch) and an empty cell (TestHall)."""
    res = server.create_enchantment("Ench.esp", "TestEnch", "Test Fire", [
        {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon")
    assert "error" not in res, res
    server._models.clear()
    assert "error" not in server.create_cell("Ench.esp", "TestHall", "Test Hall")
    server._models.clear()
