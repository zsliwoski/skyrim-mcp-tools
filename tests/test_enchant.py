"""ENCH subrecord layout, checked without the game files."""
import struct

import pytest

from tools.records.enchant import build_subrecords


def test_weapon_layout():
    subs = build_subrecords("Test", [{"effect": "FireDamage", "magnitude": 10, "duration": 0}], cost=500)
    assert [s["sig"] for s in subs] == ["OBND", "FULL", "ENIT", "EFID", "EFIT"]
    enit = bytes.fromhex(subs[2]["hex"])
    assert len(enit) == 36
    cost, flags, cast, amount, delivery, ench_type, _, base, worn = struct.unpack('<iIIiIIfII', enit)
    assert (flags, cast, amount, delivery, ench_type, base, worn) == (0, 1, 500, 1, 6, 0, 0)
    assert struct.unpack('<fII', bytes.fromhex(subs[4]["hex"])) == (10.0, 0, 0)


def test_armor_is_constant_self():
    enit = bytes.fromhex(build_subrecords("T", [{"effect": "x"}], kind="armor")[2]["hex"])
    assert struct.unpack('<iIIiIIfII', enit)[2:5] == (0, 0, 0)


def test_no_auto_calc_sets_flag():
    enit = bytes.fromhex(build_subrecords("T", [{"effect": "x"}], auto_calc=False, cost=40)[2]["hex"])
    assert struct.unpack('<iIIiIIfII', enit)[:2] == (40, 1)


@pytest.mark.parametrize("kwargs", [
    {"effects": []}, {"effects": [{"magnitude": 1}]}, {"effects": [{"effect": "x"}], "kind": "ring"},
    {"effects": [{"effect": "x", "duration": 5}], "kind": "armor"},
])
def test_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        build_subrecords("T", **kwargs)


import struct as _struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID
from esplib.vmad import VmadData, VmadScript

from tools.core.plugin_edit import plugin_files, vmad_script_names
from tools.records import maintenance
from tools.records.enchant import create_script_effect, script_effect_data, script_effect_subrecords

SCRIPT = "Scriptname Mod_Thing Extends ActiveMagicEffect\n"


@pytest.mark.parametrize("kind,cast,delivery", [("armor", 0, 0), ("weapon", 1, 1), ("staff", 1, 2)])
def test_script_effect_data(kind, cast, delivery):
    d = script_effect_data(kind)
    assert len(d) == 152
    assert _struct.unpack_from('<I', d, 64)[0] == 1
    assert _struct.unpack_from('<i', d, 68)[0] == -1
    # worn effects have no skill; weapon effects copy vanilla's hostile flags and Alteration skill
    assert _struct.unpack_from('<i', d, 12)[0] == (-1 if kind == 'armor' else 0x12)
    assert _struct.unpack_from('<I', d, 0)[0] == (0x200A02 if kind == 'armor' else 0x401403)
    assert _struct.unpack_from('<II', d, 80) == (cast, delivery)
    # offset 36 held a vanilla form ID in the template; a new plugin has no masters to resolve it
    assert _struct.unpack_from('<I', d, 36)[0] == 0


def test_weapon_script_effect_carries_harmful_keyword():
    subs = script_effect_subrecords("Hit", "Mod_Thing", kind="weapon")
    assert [s["sig"] for s in subs] == ["VMAD", "FULL", "KSIZ", "KWDA", "DATA"]
    assert subs[3]["hex"] == "70ea0100"


def test_script_effect_subrecords_attach_script():
    subs = script_effect_subrecords("Winter", "Mod_Thing", "Tooltip")
    assert [s["sig"] for s in subs] == ["VMAD", "FULL", "DATA", "DNAM"]
    vm = VmadData.parse(bytes.fromhex(subs[0]["hex"]), "MGEF")
    assert [s.name for s in vm.scripts] == ["Mod_Thing"]
    assert "DNAM" not in [s["sig"] for s in script_effect_subrecords("Winter", "Mod_Thing")]


@pytest.mark.parametrize("name,source", [
    ("Bad Name", SCRIPT), ("Mod_Thing", "Scriptname Other Extends ActiveMagicEffect\n"),
    ("Mod_Thing", "Scriptname Mod_Thing Extends Quest\n"),
])
def test_create_script_effect_rejects_bad_script(name, source):
    with pytest.raises(ValueError):
        create_script_effect(None, "Rec.esp", "Eff", "Eff", name, source)


@pytest.fixture
def scripted_plugin(tmp_path):
    p = Plugin.new_plugin(tmp_path / "Host.esp", masters=[], game='tes5')
    rec = Record("MGEF", FormID(0x800), 0)
    rec.add_subrecord("EDID", b"Eff\0")
    vm = VmadData(version=5, obj_format=2, scripts=[VmadScript(name="Mod_Thing", flags=0, properties=[])])
    rec.add_subrecord("VMAD", vm.to_bytes("MGEF"))
    p.add_record(rec)
    p.save()
    return tmp_path / "Host.esp"


def test_vmad_script_names(scripted_plugin):
    assert vmad_script_names(Plugin.load(str(scripted_plugin))) == {"Mod_Thing"}


def test_plugin_files_ships_any_attached_script(scripted_plugin):
    scripts = scripted_plugin.parent / "Scripts"
    scripts.mkdir()
    (scripts / "Mod_Thing.pex").write_bytes(b"x")
    (scripts / "Unrelated.pex").write_bytes(b"x")
    names = {f.name for f in plugin_files(scripted_plugin, regen_seq=False)}
    assert "Mod_Thing.pex" in names and "Unrelated.pex" not in names


def test_validate_flags_missing_pex_only_for_scripts_authored_here(scripted_plugin):
    def errors():
        return [e for e in maintenance.validate(scripted_plugin)["errors"] if "Mod_Thing" in e]
    assert errors() == []                       # no source here: assumed to be someone else's script
    src = scripted_plugin.parent / "Scripts" / "Source"
    src.mkdir(parents=True)
    (src / "Mod_Thing.psc").write_text(SCRIPT)
    assert len(errors()) == 1
    (scripted_plugin.parent / "Scripts" / "Mod_Thing.pex").write_bytes(b"x")
    assert errors() == []


def test_new_plugins_use_se_header_version(tmp_path):
    # 1.71 (esplib's default) makes the game drop biped slots and armor addons of records made by the tools
    from tools.core.plugin_edit import new_plugin
    p = new_plugin(tmp_path / "New.esp")
    p.save()
    assert Plugin.load(str(tmp_path / "New.esp")).header.version == pytest.approx(1.7, abs=0.001)
