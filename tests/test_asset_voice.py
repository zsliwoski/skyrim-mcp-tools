"""duplicate_assets and the voice tools, on standalone plugins (no Skyrim install needed)."""
import pytest
from esplib import Plugin

import server
from test_asset_duplicator import mod  # noqa: F401  (fixture)
from test_voice_lines import QUEST, plugin_path  # noqa: F401  (fixture)
from tools.records import asset_voice as av


def test_duplicate_assets_clones_by_editor_id(mod):  # noqa: F811
    res = av.duplicate_assets(mod, "ThingStatic", "Mine", "MyStatic")
    assert res["record"] == "MyStatic" and (mod.parent / "Meshes" / "Mine" / "thing.nif").is_file()
    assert any(r.editor_id == "MyStatic" for r in Plugin.load(str(mod)).records)
    with pytest.raises(ValueError, match="no record"):
        av.duplicate_assets(mod, "NoSuchThing", "Mine", "Other")


def test_duplicate_assets_tool_reports_errors(sandbox):
    assert "error" in server.duplicate_assets("Missing.esp", "x", "Mine")


def test_voice_type_assign_and_import(plugin_path, tmp_path):  # noqa: F811
    vt = av.create_voice_type(plugin_path, "MyVoice")
    assert vt["editor_id"] == "MyVoice"
    res = av.assign_voice_type(plugin_path, ["VoiceTestNpc"], "MyVoice")
    assert res["npcs"] == ["VoiceTestNpc"]
    with pytest.raises(ValueError, match="not NPCs"):
        av.assign_voice_type(plugin_path, ["Nobody"], "MyVoice")
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF")
    out = av.import_voice_audio(plugin_path, "Second line", wav, "MyVoice")
    assert out["file"].endswith("_2.wav") and (plugin_path.parent / "Sound" / "Voice" / "Voice.esp" / "MyVoice").is_dir()
    with pytest.raises(ValueError, match="matches no spoken line"):
        av.import_voice_audio(plugin_path, "zzz", wav, "MyVoice")
    with pytest.raises(ValueError, match="ambiguous"):
        av.import_voice_audio(plugin_path, QUEST[:10], wav, "MyVoice")
