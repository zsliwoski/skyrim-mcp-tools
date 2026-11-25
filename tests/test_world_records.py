"""Globals, messages, activators, lights, sounds, perks and music against the fixture master."""
import struct

import pytest
from esplib import Plugin

import server
from tools.records.maintenance import all_records

pytestmark = pytest.mark.fakegame


def _recs(path):
    server._models.clear()
    return {r.editor_id: r for r in all_records(Plugin.load(str(path))) if r.editor_id}


def _subs(rec, sig):
    return [bytes(s.data) for s in rec.subrecords if s.signature == sig]


def _ok(res):
    assert "error" not in res, res
    return res


def test_global_and_message(sandbox):
    _ok(server.create_global("World.esp", "TestGlobal", 2.5, "short"))
    _ok(server.create_message("World.esp", "TestBox", "Pull it?", "Lever", ["Yes", "No"]))
    _ok(server.create_message("World.esp", "TestNote", "Done."))
    recs = _recs(sandbox / "World.esp")
    assert _subs(recs["TestGlobal"], 'FNAM') == [b's'] and _subs(recs["TestGlobal"], 'FLTV') == [struct.pack('<f', 2.5)]
    assert _subs(recs["TestBox"], 'DNAM') == [struct.pack('<I', 1)] and len(_subs(recs["TestBox"], 'ITXT')) == 2
    assert _subs(recs["TestNote"], 'DNAM') == [struct.pack('<I', 2)] and _subs(recs["TestNote"], 'TNAM')


def test_activator_copies_mesh_and_light_packs_data(sandbox):
    _ok(server.create_activator("World.esp", "MyLever", "My Lever", clone_from="TestActivator", verb="Pull"))
    _ok(server.create_light("World.esp", "MyLight", radius=300, color=[255, 128, 0], flags=["dynamic", "flicker"]))
    recs = _recs(sandbox / "World.esp")
    lever = recs["MyLever"]
    assert _subs(lever, 'MODL') == [b"Clutter\\Lever.nif\0"] and _subs(lever, 'RNAM') == [b"Pull\0"]
    data = _subs(recs["MyLight"], 'DATA')[0]
    assert len(data) == 48 and struct.unpack_from('<II', data, 0)[1] == 300 and struct.unpack_from('<I', data, 12)[0] == 9
    assert "model" in server.create_activator("World.esp", "NoMesh", "x")["error"]
    assert "carried" in server.create_light("World.esp", "Torch", flags=["carried"])["error"]


def test_attach_script_fills_properties_by_editor_id(sandbox, compiler_available):
    from esplib.vmad import VmadData
    _ok(server.create_global("World.esp", "Counter", 0))
    _ok(server.create_activator("World.esp", "Pull", "Pull", clone_from="TestActivator"))
    src = "Scriptname PullScript extends ObjectReference\nGlobalVariable Property Counter Auto\nInt Property Max Auto\n"
    _ok(server.attach_script("Pull", "World.esp", "PullScript", {"Counter": {"form": "Counter"}, "Max": 3}, source=src))
    recs = _recs(sandbox / "World.esp")
    vm = VmadData.parse(_subs(recs["Pull"], 'VMAD')[0], 'ACTI')
    props = {p.name: p for p in vm.scripts[0].properties}
    assert props["Counter"].value.form_id == recs["Counter"].form_id.value and props["Max"].value == 3
    assert "declares no property" in server.attach_script("Pull", "World.esp", "PullScript", {"Nope": 1})["error"]


def test_check_script_warns_on_hardcoded_form_ids():
    src = 'Scriptname HardCoded\nEvent OnInit()\n  Form f = Game.GetFormFromFile(0x800, "X.esp")\nEndEvent\n'
    assert server.check_script(src)["warnings"]
    assert not server.check_script("Scriptname Clean\n")["warnings"]


def test_perk_conditions_and_skill_tree_node(sandbox):
    from tools.records.skill_tree import _nodes
    _ok(server.create_perk("Tree.esp", "TreePerk", name="Tree Perk", hidden=False, skill="Alchemy", skill_level=30,
                           requires_perks=["TestAlchemist"], entry_points=[{"entry_point": 73, "function": "set",
                                                                           "value": 1.0, "tabs": 1}]))
    perk = _recs(sandbox / "Tree.esp")["TreePerk"]
    conds = _subs(perk, 'CTDA')
    assert struct.unpack_from('<BxxxfHxxI', conds[0]) == (0x60, 30.0, 0x115, 16)
    assert struct.unpack_from('<fH', conds[1], 4) == (1.0, 0x1C0) and len(conds) == 2
    assert _subs(perk, 'DATA')[0][4] == 0
    assert "unknown skill" in server.create_perk("Tree.esp", "Bad", skill="Cooking", entry_points=[
        {"entry_point": 73, "value": 1}])["error"]

    res = _ok(server.add_perk_to_skill_tree("Tree.esp", "Alchemy", "TreePerk", parents=["TestAlchemist"]))
    assert res["node"] == 2 and res["tree_size"] == 3
    recs = _recs(sandbox / "Tree.esp")
    avif = recs["AVAlchemy"]
    nodes = {i["index"]: i for _, _, i in _nodes([(s.signature, bytes(s.data)) for s in avif.subrecords])}
    assert nodes[1]["links"] == [2] and nodes[2]["perk"] == recs["TreePerk"].form_id.value
    assert nodes[1]["perk"] == 0x0BE127 and nodes[0]["links"] == [1]
    assert nodes[2]["YNAM"] == 1 and nodes[2]["XNAM"] == 3
    assert "already in" in server.add_perk_to_skill_tree("Tree.esp", "Alchemy", "TreePerk")["error"]
    _ok(server.create_perk("Tree.esp", "Other", hidden=False, entry_points=[{"entry_point": 73, "value": 1}]))
    _ok(server.create_perk("Tree.esp", "Child", hidden=False, entry_points=[{"entry_point": 73, "value": 1}]))
    assert "not in the Alchemy tree" in server.add_perk_to_skill_tree(
        "Tree.esp", "Alchemy", "Child", parents=["Other"])["error"]


def test_sound_perk_and_music(sandbox):
    _ok(server.create_sound("World.esp", "TestSoundObj", "TestSoundDescriptor"))
    server._models.clear()
    assert "error" not in server.create_cell("World.esp", "MusicHall", "Music Hall")
    server._models.clear()
    kw = {"plugins": ["Skyrim.esm", str(sandbox / "World.esp")]}
    _ok(server.create_perk("World.esp", "TestPerk", ["TestAbilitySpell"], name="Test Perk", **kw))
    _ok(server.create_perk("World.esp", "TestEntryPerk", name="Light Fingers", **kw,
                           entry_points=[{"entry_point": "pickpocket_chance", "function": "add", "value": 40}]))
    _ok(server.create_music("World.esp", "TestTheme", "Music\\Test\\theme.xwm", cells=["MusicHall"], **kw))
    recs = _recs(sandbox / "World.esp")
    assert len(_subs(recs["TestSoundObj"], 'SDSC')[0]) == 4
    perk = recs["TestPerk"]
    assert _subs(perk, 'PRKE') == [bytes([1, 0, 0])] and len(_subs(perk, 'DATA')[1]) == 4
    ep = recs["TestEntryPerk"]
    assert _subs(ep, 'PRKE') == [bytes([2, 0, 0])] and _subs(ep, 'DATA')[1] == bytes([56, 2, 3])
    assert _subs(ep, 'EPFT') == [bytes([1])] and _subs(ep, 'EPFD') == [struct.pack('<f', 40)]
    assert struct.unpack('<I', _subs(recs["MusicHall"], 'XCMO')[0])[0] == recs["TestTheme"].form_id.value
    assert _subs(recs["TestTheme"], 'TNAM') == [struct.pack('<I', recs["TestThemeTrack"].form_id.value)]
    assert "must be a" in server.create_music("World.esp", "Bad", "theme.ogg")["error"]
    # the track file ships with the plugin when it exists next to it
    track = sandbox / "Music" / "Test" / "theme.xwm"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"xwm")
    from tools.core.plugin_edit import plugin_files
    assert track.resolve() in {f.resolve() for f in plugin_files(sandbox / "World.esp", regen_seq=False)}
