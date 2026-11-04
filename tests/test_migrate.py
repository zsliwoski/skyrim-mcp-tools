"""Plugin refactoring tools: list_records, find_dependents, move_records."""
import struct

import pytest
from esplib import Plugin

import server
from helpers import sigs


@pytest.fixture
def mod(sandbox):
    """Mod.esp: Gem and Wand (GCG_), Stray (GCG_) pointing at Wand, Plain, and a placed reference to Gem."""
    p = Plugin.new_plugin(str(sandbox / "Mod.esp"), masters=["Skyrim.esm"], game="tes5")
    p.header.version = 1.7
    ids = {}
    for name, oid in (("GCG_Gem", 0x800), ("GCG_Wand", 0x801), ("GCG_Stray", 0x802), ("Plain", 0x803)):
        r = p.new_record("MISC", name, form_id=oid)
        r.add_subrecord("FULL", name.replace("_", " ") + "\0")
        ids[name] = r.form_id.value
    p.get_record_by_editor_id("GCG_Stray").add_subrecord("ZZZZ", struct.pack("<I", ids["GCG_Wand"]))
    ref = p.new_record("REFR", "GemRef", form_id=0x810)
    ref.add_subrecord("NAME", struct.pack("<I", ids["GCG_Gem"]))
    p.save(str(sandbox / "Mod.esp"))
    return sandbox / "Mod.esp"


def test_list_records(mod):
    res = server.list_records("Mod.esp", query="gcg_")
    assert res["total"] == 3 and res["by_type"] == {"MISC": 3}
    assert server.list_records("Mod.esp", min_id=0x802, max_id=0x803, signature="MISC")["total"] == 2


def test_find_dependents(mod):
    res = server.find_dependents("Mod.esp", records=["GCG_Wand"])
    assert [r["record"] for r in res["referenced_by"]] == ["GCG_Stray"]
    res = server.find_dependents("Mod.esp", records=["GCG_Gem"])
    assert [r["record"] for r in res["placed"]] == ["GemRef"]
    res = server.find_dependents("Mod.esp", records=["GCG_Stray"])
    assert res["refers_to"] == ["MISC GCG_Wand"]
    res = server.find_dependents("Mod.esp", records=["GCG_Stray"], include_dependencies=True)
    assert "MISC GCG_Wand" in res["records"] and res["refers_to"] == ["MISC GCG_Wand"]


def test_move_records(mod, sandbox):
    res = server.move_records("Mod.esp", "Split.esp", records=["GCG_Gem"], rename_prefix={"from": "GCG_", "to": "MT_"})
    assert "error" not in res, res
    assert sigs(sandbox / "Split.esp") == {"MISC": ["MT_Gem"]}
    left = sigs(sandbox / "Mod.esp")
    assert left["MISC"] == ["GCG_Wand", "GCG_Stray", "Plain"] and "REFR" not in left
    moved = Plugin.load(str(sandbox / "Split.esp")).get_record_by_editor_id("MT_Gem")
    assert moved.form_id.value == 0x01000800


def test_move_refuses_dangling(mod, sandbox):
    res = server.move_records("Mod.esp", "Split.esp", records=["GCG_Stray"])
    assert "GCG_Wand" in res["error"]
    assert not (sandbox / "Split.esp").exists()
    assert "error" not in server.move_records("Mod.esp", "Split.esp", query="GCG_")


def test_move_refuses_clash(mod, sandbox):
    server.move_records("Mod.esp", "Split.esp", records=["GCG_Gem"], remove_from_source=False)
    assert "already has" in server.move_records("Mod.esp", "Split.esp", records=["GCG_Gem"])["error"]
    assert "cannot move" in server.move_records("Mod.esp", "Split.esp", records=["GemRef"])["error"]
