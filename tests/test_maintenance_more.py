"""validate, audit, deploy and console-helper tests for maintenance.py on small synthetic plugins."""
import struct

import pytest
from esplib import Plugin
from esplib.vmad import VmadData, VmadObject, VmadProperty, VmadScript, PROP_OBJECT

from tools.records import maintenance as m


def _plugin(tmp_path, name="Mod.esp", masters=("Skyrim.esm",), version=1.7):
    p = Plugin.new_plugin(str(tmp_path / name), masters=list(masters), game="tes5")
    p.header.version = version
    return p


def _vmad(script, prop=None, target=0, sig="QUST"):
    props = [VmadProperty(prop, PROP_OBJECT, 1, VmadObject(target))] if prop else []
    return bytes(VmadData(scripts=[VmadScript(script, 0, props)]).to_bytes(sig))


def _data(tmp_path, masters=("Skyrim.esm",)):
    d = tmp_path / "Data"
    d.mkdir(exist_ok=True)
    for name in masters:
        (d / name).write_bytes(b"x")
    return d


def _save(p, tmp_path):
    p.save(str(p.file_path))
    return p.file_path


def _quest(p, edid, oid, flags=0x1, subs=(), enam=False):
    q = p.new_record("QUST", edid, form_id=oid)
    q.add_subrecord("DNAM", struct.pack("<HBBII", flags, 50, 0, 0, 0))
    if enam:
        q.add_subrecord("ENAM", bytes(4))
    for sig, data in subs:
        q.add_subrecord(sig, data)
    return q


@pytest.fixture
def lore_off(monkeypatch):
    import tools.books.lore as lore
    monkeypatch.setattr(lore, "lint_plugin", lambda path, limit=20: [])


def test_all_records_walks_groups(tmp_path):
    p = _plugin(tmp_path)
    p.new_record("MISC", "A", form_id=0x800)
    assert [r.editor_id for r in m.all_records(p)] == ["A"]


def test_validate_clean_plugin(tmp_path, lore_off):
    p = _plugin(tmp_path)
    p.new_record("MISC", "Thing", form_id=0x800)
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    assert res["ok"] and res["errors"] == [] and res["records"] == 1


def test_validate_reports_masters_and_duplicates(tmp_path, lore_off):
    p = _plugin(tmp_path, masters=("Skyrim.esm", "Other.esp"))
    p.new_record("MISC", "Dup", form_id=0x800)
    p.new_record("MISC", "dup", form_id=0x801)
    path = _save(p, tmp_path)
    res = m.validate(path, tmp_path / "Empty")
    text = "\n".join(res["errors"])
    assert "official master Skyrim.esm is missing" in text and "master Other.esp is not in" in text
    assert "duplicate editor ID" in text and not res["ok"]


def test_validate_bad_form_references(tmp_path, lore_off):
    p = _plugin(tmp_path)
    r = p.new_record("MISC", "Holder", form_id=0x800)
    r.add_subrecord("NAME", struct.pack("<I", 0x01000999))
    r2 = p.new_record("MISC", "Far", form_id=0x801)
    r2.add_subrecord("NAME", struct.pack("<I", 0x05000001))
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    text = "\n".join(res["errors"])
    assert "missing own record 01000999" in text and "beyond 1" in text


def test_validate_bad_master_index(tmp_path, lore_off):
    p = _plugin(tmp_path)
    p.new_record("MISC", "Bad", form_id=0x03000800)
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    assert any("master index 3" in e for e in res["errors"])


def test_validate_missing_pex_and_present_pex(tmp_path, lore_off):
    p = _plugin(tmp_path)
    q = _quest(p, "Q", 0x800)
    q.add_subrecord("VMAD", _vmad("Mod_Script"))
    path = _save(p, tmp_path)
    data = _data(tmp_path)
    assert any("Mod_Script.pex was not found" in e for e in m.validate(path, data)["errors"])
    (tmp_path / "Scripts").mkdir()
    (tmp_path / "Scripts" / "Mod_Script.pex").write_bytes(b"x")
    assert not any("pex" in e for e in m.validate(path, data)["errors"])


def test_validate_quest_checks_and_seq(tmp_path, lore_off):
    p = _plugin(tmp_path)
    _quest(p, "NoAlias", 0x800, flags=0, subs=[("QSTA", struct.pack("<II", 7, 0))])
    _quest(p, "Auto", 0x801, flags=0x1, subs=[("ALST", struct.pack("<I", 0))])
    path = _save(p, tmp_path)
    res = m.validate(path, _data(tmp_path))
    text = "\n".join(res["errors"] + res["warnings"])
    assert "objective target alias 7 does not exist" in text
    assert "NoAlias is not start-game-enabled" in text
    assert "SEQ file is stale" in text
    (tmp_path / "SEQ").mkdir()
    (tmp_path / "SEQ" / "Mod.seq").write_bytes(struct.pack("<I", 0x01000801))
    assert not any("SEQ" in e for e in m.validate(path, _data(tmp_path))["errors"])


def test_validate_alias_reference_persistence(tmp_path, lore_off):
    p = _plugin(tmp_path)
    ref = p.new_record("REFR", "SomeRef", form_id=0x810)
    ref.add_subrecord("NAME", struct.pack("<I", 0x0F))
    _quest(p, "Q", 0x800, subs=[("ALFR", struct.pack("<I", 0x01000810))])
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    assert any("is not persistent" in w for w in res["warnings"])


def test_validate_version_enchant_and_weapon_warnings(tmp_path, lore_off):
    p = _plugin(tmp_path, version=1.0)
    e = p.new_record("ENCH", "ZeroEnch", form_id=0x800)
    e.add_subrecord("ENIT", struct.pack("<iIIiIfII", 0, 0, 0, 0, 0, 0.0, 0, 0))
    w = p.new_record("WEAP", "Blade", form_id=0x801)
    w.add_subrecord("EITM", struct.pack("<I", 0x01000800))
    ref = p.new_record("REFR", "BladeRef", form_id=0x802)
    ref.add_subrecord("NAME", struct.pack("<I", 0x01000801))
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    text = "\n".join(res["warnings"])
    assert "not 1.70" in text and "auto-calculated ENIT cost is 0" in text
    assert "has no EAMT" in text and "has no XCHG" in text


def test_validate_reports_data_differences(tmp_path, lore_off):
    p = _plugin(tmp_path)
    p.new_record("MISC", "Thing", form_id=0x800)
    path = _save(p, tmp_path)
    data = _data(tmp_path)
    assert any("is not installed in Data" in w for w in m.validate(path, data)["warnings"])
    (data / "Mod.esp").write_bytes(b"different")
    assert any("differs from the output copy" in w for w in m.validate(path, data)["warnings"])


def test_validate_includes_lore_issues(tmp_path, monkeypatch):
    import tools.books.lore as lore
    monkeypatch.setattr(lore, "lint_plugin", lambda path, limit=20: [
        {"record": "R", "sig": "DESC", "match": "x", "suggestion": "y"}])
    p = _plugin(tmp_path)
    res = m.validate(_save(p, tmp_path), _data(tmp_path))
    assert any(w.startswith("lore: R DESC") for w in res["warnings"])


def test_ref_persistent_own_and_master(tmp_path, fake_data_dir):
    p = _plugin(tmp_path)
    r = p.new_record("REFR", "PersistRef", form_id=0x800)
    r.flags = 0x400
    recs = list(m.all_records(p))
    assert m.ref_persistent(p, recs, 0x01000800) is True
    assert m.ref_persistent(p, recs, 0x01000999) is None
    assert m.ref_persistent(p, recs, 0x00D3C98) is None
    assert m.ref_persistent(p, recs, 0x00D3C98, fake_data_dir) is False
    assert m.ref_persistent(p, recs, 0x00D3C98, tmp_path / "nowhere") is None
    assert m.master_ref_flags(fake_data_dir / "Skyrim.esm")[0xD3C98] is False


def test_audit_plugin_reports_fragile_quests(tmp_path, fake_data_dir):
    p = _plugin(tmp_path)
    q = _quest(p, "Q", 0x800, subs=[("QOBJ", struct.pack("<H", 10)), ("QOBJ", struct.pack("<H", 20)),
                                     ("QSTA", struct.pack("<II", 1, 0))])
    q.add_subrecord("VMAD", _vmad("QuestScript", "Target", 0x00D3C98))
    src = tmp_path / "Scripts" / "Source"
    src.mkdir(parents=True)
    (src / "QuestScript.psc").write_text(
        "Scriptname QuestScript extends Quest\nObjectReference Property Target Auto\n"
        "Function F()\n  If Game.GetPlayer() == Target\n  EndIf\n  If Target != None && Target == Target\n  EndIf\nEndFunction\n")
    res = m.audit_plugin(_save(p, tmp_path), fake_data_dir)
    text = "\n".join(res["issues"])
    assert "objective 10 has no alias target" in text and "objective 20" not in text
    assert "points at non-persistent reference" in text
    assert "compares with property Target" in text and not res["ok"]


def test_audit_plugin_clean(tmp_path):
    p = _plugin(tmp_path)
    p.new_record("MISC", "Thing", form_id=0x800)
    assert m.audit_plugin(_save(p, tmp_path))["ok"]


def test_deploy_rejects_mode_and_stops_on_errors(tmp_path, lore_off):
    p = _plugin(tmp_path)
    path = _save(p, tmp_path)
    with pytest.raises(ValueError):
        m.deploy_plugin(path, tmp_path, mode="nope")
    res = m.deploy_plugin(path, tmp_path / "NoData", mode="install")
    assert not res["deployed"] and res["errors"]


def test_deploy_package_install_and_both(tmp_path, lore_off):
    p = _plugin(tmp_path)
    p.new_record("MISC", "Thing", form_id=0x800)
    path = _save(p, tmp_path)
    data = _data(tmp_path)
    (data / "Mod.esp").write_bytes(b"loose")
    res = m.deploy_plugin(path, data, mode="package")
    assert res["deployed"] and (tmp_path / "packages" / "Mod.zip").is_file()
    assert res["loose_in_data"] and "note" in res
    res = m.deploy_plugin(path, data, mode="package", remove_loose=True)
    assert res["removed_loose"] == 1 and not (data / "Mod.esp").exists()
    res = m.deploy_plugin(path, data, mode="install")
    assert res["deployed"] and (data / "Mod.esp").is_file()
    res = m.deploy_plugin(path, data, mode="both")
    assert "install" in res and res["deployed"]


def test_install_prunes_stale_fragments(tmp_path):
    p = _plugin(tmp_path)
    path = _save(p, tmp_path)
    data = _data(tmp_path)
    (data / "Scripts").mkdir()
    stale = data / "Scripts" / "TIF__Mod_000ABC.pex"
    stale.write_bytes(b"x")
    keep = data / "Scripts" / "TIF__Other_000ABC.pex"
    keep.write_bytes(b"x")
    dry = m.install(path, data, dry_run=True)
    assert dry["pruned_stale_scripts"] == ["Scripts/TIF__Mod_000ABC.pex"] and stale.exists()
    assert m.install(path, data)["pruned_stale_scripts"] and not stale.exists() and keep.exists()
    assert m.install(path, data, prune=False)["pruned_stale_scripts"] == []


def test_install_requires_data_dir(tmp_path):
    with pytest.raises(ValueError):
        m.install(tmp_path / "A.esp", tmp_path / "missing")


def test_install_locked_file_message(tmp_path, monkeypatch):
    p = _plugin(tmp_path)
    path = _save(p, tmp_path)

    def locked(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(m.shutil, "copy2", locked)
    with pytest.raises(ValueError, match="locked"):
        m.install(path, _data(tmp_path))


def test_snapshot_missing_plugin(tmp_path):
    with pytest.raises(FileNotFoundError):
        m.snapshot(tmp_path / "none.esp")


def test_restore_by_label(tmp_path):
    f = tmp_path / "A.esp"
    f.write_bytes(b"1")
    m.snapshot(f, "keep me!")
    f.write_bytes(b"2")
    assert m.restore(f, "keepme")["restored"].endswith(".keepme.esp")
    assert f.read_bytes() == b"1"


def test_item_console_commands(tmp_path):
    p = _plugin(tmp_path)
    p.new_record("WEAP", "SwordA", form_id=0x800)
    p.new_record("SPEL", "SpellA", form_id=0x801)
    p.new_record("NPC_", "NpcA", form_id=0x802)
    p.new_record("ENCH", "EnchA", form_id=0x803)
    p.new_record("CELL", "CellA", form_id=0x804)
    p.new_record("REFR", "RefA", form_id=0x805)
    path = _save(p, tmp_path)
    res = m.item_console_commands(path)
    assert res["items"] == ["player.additem XX000800 1    ; SwordA"]
    assert res["spells"] == ["player.addspell XX000801    ; SpellA"]
    assert res["npcs"][0].startswith("player.placeatme XX000802") and res["cells"] == ["coc CellA"]
    assert res["effects"] == ["ENCH XX000803 EnchA"] and "placed" not in res
    assert m.item_console_commands(path, sections=["placed"])["placed"][0].startswith("player.moveto XX000805")
    assert m.item_console_commands(path, query="swordz", sections=["items"])["items"] == []
    with pytest.raises(ValueError):
        m.item_console_commands(path, sections=["bogus"])


def test_quest_console_commands(tmp_path):
    p = _plugin(tmp_path)
    _quest(p, "MyQuest", 0x800, subs=[("INDX", struct.pack("<HBB", 10, 0, 0)), ("INDX", struct.pack("<HBB", 20, 0, 0)),
                                       ("QOBJ", struct.pack("<H", 5)), ("ALID", b"Chief\0")])
    path = _save(p, tmp_path)
    res = m.quest_console_commands(path, "myquest")
    assert res["advance"] == ["setstage MyQuest 10", "setstage MyQuest 20"]
    assert res["objectives"] == ["setobjectivedisplayed MyQuest 5 1"] and res["aliases"] == ["Chief"]
    with pytest.raises(ValueError):
        m.quest_console_commands(path, "Nope")


def test_check_script_requires_header():
    with pytest.raises(ValueError):
        m.check_script("Function F()\nEndFunction")


def _ench_plugin(tmp_path, script=None):
    p = _plugin(tmp_path)
    mg = p.new_record("MGEF", "TestEffect", form_id=0x800)
    mg.add_subrecord("DNAM", b"Does a thing.\0")
    if script:
        mg.add_subrecord("VMAD", _vmad(script, sig="MGEF"))
    e = p.new_record("ENCH", "TestEnch", form_id=0x801)
    e.add_subrecord("EFID", struct.pack("<I", 0x01000800))
    e.add_subrecord("EFIT", struct.pack("<fII", 5.0, 0, 0))
    e.add_subrecord("EFID", struct.pack("<I", 0x00123456))
    e.add_subrecord("EFIT", struct.pack("<fII", 1.0, 0, 0))
    w = p.new_record("WEAP", "TestBlade", form_id=0x802)
    w.add_subrecord("EITM", struct.pack("<I", 0x01000801))
    bare = p.new_record("WEAP", "BareBlade", form_id=0x803)
    lost = p.new_record("WEAP", "LostBlade", form_id=0x804)
    lost.add_subrecord("EITM", struct.pack("<I", 0x01000999))
    return _save(p, tmp_path)


def test_enchantment_test_plan_for_weapon(tmp_path):
    path = _ench_plugin(tmp_path)
    plan = m.enchantment_test_plan(path, "testblade")
    assert plan["item"] == "TestBlade" and plan["enchantment"] == "TestEnch"
    assert plan["commands"][0].startswith("player.additem XX000802")
    assert plan["effects"][0]["description"] == "Does a thing." and plan["effects"][1]["origin"].startswith("vanilla")
    assert "debug_tip" not in plan and "requires_skse" not in plan


def test_enchantment_test_plan_scripts_and_skse(tmp_path):
    src = tmp_path / "Scripts" / "Source"
    src.mkdir(parents=True)
    (src / "FxScript.psc").write_text(
        "Scriptname FxScript extends ActiveMagicEffect\n"
        "Event OnEffectStart(Actor a, Actor b)\n  RegisterForModEvent(\"x\", \"y\")\n"
        "  Game.GetFormFromFile(0x0012AB, \"Mod.esp\")\nEndEvent\n")
    path = _ench_plugin(tmp_path, script="FxScript")
    plan = m.enchantment_test_plan(path, "TestEnch")
    assert plan["commands"] == [] and plan["effects"][0]["scripts"] == ["FxScript"]
    assert "FxScript" in plan["debug_tip"] and plan["ability_spells_looked_up"] == ["0012AB"]
    assert plan["requires_skse"] == ["FxScript: RegisterForModEvent"]


def test_enchantment_test_plan_errors(tmp_path):
    path = _ench_plugin(tmp_path)
    for item, msg in (("Missing", "is not a record"), ("BareBlade", "no enchantment"), ("LostBlade", "not in")):
        with pytest.raises(ValueError, match=msg):
            m.enchantment_test_plan(path, item)
