import pytest

import server

pytestmark = pytest.mark.fakegame


def record_first_line(sandbox, folder):
    """Drop a fake recording for the NPC's first line under Sound/Voice/TestMod.esp/<folder>."""
    d = server.get_npc_dialogue("TestSven", include_generic=False, plugins=["TestMod.esp"])
    info = int(d["direct"][0]["info"], 16) & 0xFFFFFF
    voice = sandbox / "Sound" / "Voice" / "TestMod.esp" / folder
    voice.mkdir(parents=True)
    (voice / f"TestMod_Hello_{info:08x}_1.fuz").write_bytes(b"x")


def test_recorded_npc_without_combat_lines_is_flagged_then_fixed(npc_plugin, sandbox):
    record_first_line(sandbox, "MaleNord")
    res = server.audit_voice("TestMod.esp")
    assert res["npcs"][0]["recorded"] == 1 and res["npcs"][0]["combat_lines"] == 0
    assert any("TestSven" in i and "no combat lines" in i for i in res["issues"])
    assert any("no combat lines" in i for i in server.audit_plugin("TestMod.esp")["issues"])

    edit = server.edit_npc("TestSven", {"combat_lines": True}, "TestMod.esp", ["TestMod.esp"])
    assert "error" not in edit, edit
    server._models.clear()  # the suite disables _invalidate
    res = server.audit_voice("TestMod.esp")
    assert res["npcs"][0]["combat_lines"] > 0 and res["ok"], res["issues"]


def test_npc_without_recordings_is_not_flagged(npc_plugin):
    res = server.audit_voice("TestMod.esp")
    assert res["npcs"][0]["recorded"] == 0 and res["ok"]


def test_recording_in_the_wrong_voice_folder_is_flagged(npc_plugin, sandbox):
    record_first_line(sandbox, "FemaleNord")
    server.edit_npc("TestSven", {"combat_lines": True}, "TestMod.esp", ["TestMod.esp"])
    server._models.clear()
    assert any("voice type is MaleNord" in i for i in server.audit_voice("TestMod.esp")["issues"])
