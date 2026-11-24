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


def test_sound_perk_and_music(sandbox):
    _ok(server.create_sound("World.esp", "TestSoundObj", "TestSoundDescriptor"))
    server._models.clear()
    assert "error" not in server.create_cell("World.esp", "MusicHall", "Music Hall")
    server._models.clear()
    kw = {"plugins": ["Skyrim.esm", str(sandbox / "World.esp")]}
    _ok(server.create_perk("World.esp", "TestPerk", ["TestAbilitySpell"], name="Test Perk", **kw))
    _ok(server.create_music("World.esp", "TestTheme", "Music\\Test\\theme.xwm", cells=["MusicHall"], **kw))
    recs = _recs(sandbox / "World.esp")
    assert len(_subs(recs["TestSoundObj"], 'SDSC')[0]) == 4
    perk = recs["TestPerk"]
    assert _subs(perk, 'PRKE') == [bytes([1, 0, 0])] and len(_subs(perk, 'DATA')[1]) == 4
    assert struct.unpack('<I', _subs(recs["MusicHall"], 'XCMO')[0])[0] == recs["TestTheme"].form_id.value
    assert _subs(recs["TestTheme"], 'TNAM') == [struct.pack('<I', recs["TestThemeTrack"].form_id.value)]
    assert "must be a" in server.create_music("World.esp", "Bad", "theme.ogg")["error"]
    # the track file ships with the plugin when it exists next to it
    track = sandbox / "Music" / "Test" / "theme.xwm"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"xwm")
    from tools.core.plugin_edit import plugin_files
    assert track.resolve() in {f.resolve() for f in plugin_files(sandbox / "World.esp", regen_seq=False)}
