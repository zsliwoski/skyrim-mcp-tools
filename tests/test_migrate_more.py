"""Script-related and edge-case tests for migrate.py (compiler mocked)."""
import struct

import pytest
from esplib import Plugin
from esplib.vmad import PROP_OBJECT, VmadData, VmadObject, VmadProperty, VmadScript

from tools.records import migrate


def _vmad(*scripts, sig="MISC"):
    return bytes(VmadData(scripts=[VmadScript(n, 0, []) for n in scripts]).to_bytes(sig))


@pytest.fixture
def scripted(tmp_path, monkeypatch):
    p = Plugin.new_plugin(str(tmp_path / "Mod.esp"), masters=["Skyrim.esm"], game="tes5")
    p.header.version = 1.7
    a = p.new_record("MISC", "GCG_A", form_id=0x800)
    a.add_subrecord("VMAD", _vmad("GCG_Script"))
    b = p.new_record("MISC", "GCG_B", form_id=0x801)
    b.add_subrecord("VMAD", _vmad("Other_Script"))
    b.add_subrecord("FULL", b"B\0")
    p.new_record("CELL", "GCG_Cell", form_id=0x802)
    p.save(str(tmp_path / "Mod.esp"))
    src = tmp_path / "Scripts" / "Source"
    src.mkdir(parents=True)
    (src / "GCG_Script.psc").write_text(
        'Scriptname GCG_Script extends Quest\nForm f = Game.GetFormFromFile(0x000800, "Mod.esp")\n')
    (tmp_path / "Scripts" / "GCG_Script.pex").write_bytes(b"old")
    built = []

    def fake_compile(name, text, out_dir):
        built.append((name, text, out_dir))
        out = tmp_path / "Scripts" if str(out_dir).endswith("Scripts") else tmp_path
        (out / f"{name}.pex").write_bytes(b"new")
        return str(out / f"{name}.pex")

    monkeypatch.setattr(migrate, "compile_script", fake_compile)
    return tmp_path / "Mod.esp", built


def test_find_dependents_reports_script_lines(scripted):
    path, _ = scripted
    res = migrate.find_dependents(path, records=["GCG_A"])
    assert res["scripts"] == [{"file": "GCG_Script.psc", "line": 2,
                               "text": 'Form f = Game.GetFormFromFile(0x000800, "Mod.esp")'}]
    assert migrate.find_dependents(path, records=["GCG_B"])["scripts"] == []


def test_resolve_seeds_errors(scripted):
    path, _ = scripted
    plugin = Plugin.load(str(path))
    with pytest.raises(ValueError, match="no records matched"):
        migrate.resolve_seeds(plugin, query="zzz")
    assert [r.editor_id for r in migrate.resolve_seeds(plugin, records=["GCG_A"], query="gcg_b")] == ["GCG_A", "GCG_B"]
    assert all(r.signature != "CELL" for r in migrate.resolve_seeds(plugin, query="gcg_"))


def test_rename_script_updates_vmad_and_files(scripted, tmp_path):
    path, built = scripted
    res = migrate.rename_script(path, "gcg_script", "MT_Script")
    assert res["records_updated"] == ["GCG_A"]
    assert built[0][0] == "MT_Script" and "Scriptname MT_Script" in built[0][1]
    assert not (tmp_path / "Scripts" / "GCG_Script.pex").exists()
    assert not (tmp_path / "Scripts" / "Source" / "GCG_Script.psc").exists()
    assert "MT_Script" in migrate.vmad_script_names(Plugin.load(str(path)))


@pytest.mark.parametrize("old,new,msg", [
    ("GCG_Script", "bad name", "letters, digits"),
    ("Nope", "New_Name", "no record in"),
    ("QF_Mod_000800", "New_Name", "fragment"),
])
def test_rename_script_errors(scripted, old, new, msg):
    path, _ = scripted
    if old.startswith("QF_"):
        p = Plugin.load(str(path))
        p.get_record_by_editor_id("GCG_B").get_subrecord("VMAD").data = bytearray(_vmad(old))
        p.save(str(path))
    with pytest.raises(ValueError, match=msg):
        migrate.rename_script(path, old, new)


def test_rename_script_missing_source(scripted, tmp_path):
    path, _ = scripted
    (tmp_path / "Scripts" / "Source" / "GCG_Script.psc").unlink()
    with pytest.raises(FileNotFoundError):
        migrate.rename_script(path, "GCG_Script", "MT_Script")


def test_check_script_names_rejects_duplicates():
    with pytest.raises(ValueError, match="same name"):
        migrate._check_script_names({"A_One": "Same", "B_Two": "same"})


def test_move_records_renames_scripts(scripted, tmp_path):
    path, built = scripted
    target = tmp_path / "Split.esp"
    res = migrate.move_records(path, target, records=["GCG_A"], script_renames={"GCG_Script": "MT_Script"})
    assert res["scripts_built"] == ["MT_Script"] and res["removed_from_source"]
    assert "Split.esp" in built[0][1] and "Mod.esp" not in built[0][1]
    assert "MT_Script" in migrate.vmad_script_names(Plugin.load(str(target)))
    assert not (tmp_path / "Scripts" / "GCG_Script.pex").exists()


def test_move_records_errors(scripted, tmp_path):
    path, _ = scripted
    with pytest.raises(ValueError, match="same plugin"):
        migrate.move_records(path, path, records=["GCG_A"])
    with pytest.raises(ValueError, match="no record in"):
        migrate.move_records(path, tmp_path / "S.esp", records=["GCG_B"], script_renames={"Missing": "New_One"})
    other = Plugin.new_plugin(str(tmp_path / "Mismatch.esp"), masters=[], game="tes5")
    other.save(str(tmp_path / "Mismatch.esp"))
    with pytest.raises(ValueError, match="same master list"):
        migrate.move_records(path, tmp_path / "Mismatch.esp", records=["GCG_A"])


def test_move_records_into_existing_plugin_keeps_source(scripted, tmp_path):
    path, _ = scripted
    target = tmp_path / "Split.esp"
    migrate.move_records(path, target, records=["GCG_A"], remove_from_source=False)
    res = migrate.move_records(path, target, records=["GCG_B"], remove_from_source=False)
    assert res["removed_from_source"] == []
    assert {r.editor_id for r in Plugin.load(str(target)).records} >= {"GCG_A", "GCG_B"}


def test_move_records_remaps_colliding_ids(scripted, tmp_path):
    path, _ = scripted
    target = tmp_path / "Split.esp"
    clash = Plugin.new_plugin(str(target), masters=["Skyrim.esm"], game="tes5")
    clash.new_record("MISC", "Other_Item", form_id=0x801)
    clash.save(str(target))
    with pytest.raises(ValueError, match="remap_ids"):
        migrate.move_records(path, target, records=["GCG_B"], remove_from_source=False)
    res = migrate.move_records(path, target, records=["GCG_B"], remove_from_source=False, remap_ids=True)
    assert res["remapped_ids"] == {"01000801": "01000802"}
    moved = Plugin.load(str(target)).get_record_by_editor_id("GCG_B")
    assert moved.form_id.value == 0x01000802


def test_list_records_limit_and_name_filter(scripted):
    path, _ = scripted
    res = migrate.list_records(path, query="b", limit=1)
    assert res["total"] >= 1 and len(res["records"]) == 1
    assert migrate.list_records(path, signature="cell")["by_type"] == {"CELL": 1}
