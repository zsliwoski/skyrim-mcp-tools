"""Functional tests for the voice line editor (misc/voice_lines_model.py); no Skyrim install or audio tools needed."""
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from esplib import Plugin
from esplib.record import Record, GroupRecord
from esplib.utils import FormID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "misc"))

import voice_lines_model as vl  # noqa: E402
from tools.core.plugin_edit import new_plugin, package_plugin  # noqa: E402

GET_IS_ID = 72
QUEST, TOPIC = "VoiceTestQuestLongName", "VoiceTestTopicWithLongName"
INFO_IDS = (0x810, 0x811)


def z(text):
    return text.encode("cp1252") + b"\0"


@pytest.fixture
def plugin_path(tmp_path):
    """Voice.esp (no masters): one NPC, a quest, one topic with two INFOs; the first has two responses."""
    path = tmp_path / "Voice.esp"
    plugin = new_plugin(str(path))
    npc = Record("NPC_", FormID(0x800), 0)
    npc.add_subrecord("EDID", z("VoiceTestNpc"))
    plugin.add_record(npc)
    quest = Record("QUST", FormID(0x801), 0)
    quest.add_subrecord("EDID", z(QUEST))
    plugin.add_record(quest)
    dial = Record("DIAL", FormID(0x802), 0)
    dial.add_subrecord("EDID", z(TOPIC))
    dial.add_subrecord("FULL", z("Tell me more"))
    dial.add_subrecord("QNAM", struct.pack("<I", 0x801))
    plugin.add_record(dial)
    group = GroupRecord(7, 0x802)
    next(g for g in plugin.groups if g.group_type == 0 and g.label == "DIAL").records.append(group)
    condition = struct.pack("<B3xfH2xIIIIi", 0, 1.0, GET_IS_ID, 0x800, 0, 0, 0, -1)
    for oid, texts in zip(INFO_IDS, (["First line.", "Second line."], ["Another answer."])):
        info = Record("INFO", FormID(oid), 0)
        for i, text in enumerate(texts, 1):
            info.add_subrecord("TRDT", struct.pack("<II4xB3xIB3x", 0, 50, i, 0, 1))
            info.add_subrecord("NAM1", z(text))
        info.add_subrecord("CTDA", condition)
        group.records.append(info)
    plugin.save(str(path))
    return path


def lines_of(path):
    return vl.load_lines(path)[1]


def test_lists_every_response_with_speaker_and_ck_file_names(plugin_path):
    lines = lines_of(plugin_path)
    assert [(ln.text, ln.response) for ln in lines] == [
        ("First line.", 1), ("Second line.", 2), ("Another answer.", 1)]
    first = lines[0]
    assert first.speaker == "VoiceTestNpc"
    assert first.prompt == "Tell me more"
    assert first.plugin_dir == "Voice.esp"
    # quest cut to 10 characters, topic to 15, load order byte of the info ID zeroed
    assert first.stem == f"{QUEST[:10]}_{TOPIC[:15]}_00000810_1"
    assert lines[2].stem.endswith("_00000811_1")


def test_voice_stem_zeroes_load_order_byte():
    assert vl.voice_stem("Q", "T", 0x05000ABC, 2) == "Q_T_00000abc_2"


def test_find_audio_loose_file_sets_voice_type(plugin_path):
    ln = lines_of(plugin_path)[0]
    assert vl.find_audio(ln, plugin_path).audio is None
    wav = vl.line_wav_path(ln, plugin_path, "MaleNord")
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF")
    vl.find_audio(ln, plugin_path)
    assert ln.audio == wav
    assert ln.voice_types == ["MaleNord"]


def test_find_audio_prefers_the_requested_voice_type(plugin_path):
    ln = lines_of(plugin_path)[0]
    for vt in ("MaleNord", "FemaleNord"):
        f = vl.line_wav_path(ln, plugin_path, vt)
        f.parent.mkdir(parents=True)
        f.write_bytes(b"RIFF")
    vl.find_audio(ln, plugin_path, voice_type="FemaleNord")
    assert ln.audio.parent.name == "FemaleNord"


def test_find_audio_in_bsa(plugin_path, monkeypatch):
    ln = lines_of(plugin_path)[0]
    inner = f"Sound\\Voice\\Voice.esp\\MaleNord\\{ln.stem}.fuz"

    class FakeBsa:
        def __init__(self, path): pass
        def open(self): pass
        def close(self): pass
        def list_files(self): return [inner.lower(), "meshes\\x.nif"]

    import esplib.bsa
    monkeypatch.setattr(esplib.bsa, "BsaReader", FakeBsa)
    (plugin_path.parent / "Voice.bsa").write_bytes(b"")
    index = vl.bsa_index([plugin_path.parent], "Voice.esp")
    vl.find_audio(ln, plugin_path, bsas=index)
    assert ln.bsa == (str(plugin_path.parent / "Voice.bsa"), inner.lower())
    assert [v.lower() for v in ln.voice_types] == ["malenord"]


def test_create_voice_type_and_assign_to_npc(plugin_path):
    made = vl.create_voice_type(plugin_path, "VoiceTestVoice", female=True)
    plugin = Plugin.load(str(plugin_path))
    vtyp = next(r for r in plugin.records if r.signature == "VTYP")
    assert vtyp.editor_id == "VoiceTestVoice"
    assert vtyp.form_id.value == made["form_id"]
    assert bytes(vtyp.get_subrecord("DNAM").data) == bytes([3])  # allow default dialogue + female
    assert any(plugin_path.parent.rglob("*pre_voice_type*"))

    with pytest.raises(ValueError):
        vl.create_voice_type(plugin_path, "VoiceTestVoice")
    with pytest.raises(ValueError):
        vl.create_voice_type(plugin_path, "bad id!")

    assert vl.assign_voice_type(plugin_path, {0x800}, made["form_id"]) == ["VoiceTestNpc"]
    npc = next(r for r in Plugin.load(str(plugin_path)).records if r.signature == "NPC_")
    assert bytes(npc.get_subrecord("VTCK").data) == struct.pack("<I", made["form_id"])  # one form ID, not male/female
    assert vl.load_lines(plugin_path)[1][0].voice_types == ["VoiceTestVoice"]
    model, _ = vl.load_lines(plugin_path)
    assert "VoiceTestVoice" in vl.all_voice_types(model)


def test_import_audio_copies_wav_and_replaces_other_formats(plugin_path, tmp_path):
    ln = lines_of(plugin_path)[0]
    src = tmp_path / "take.wav"
    src.write_bytes(b"RIFFdata")
    old = vl.voice_root(plugin_path, ln.plugin_dir) / "MaleNord" / f"{ln.stem}.fuz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    dest = vl.import_audio(ln, src, plugin_path, "MaleNord")
    assert dest.read_bytes() == b"RIFFdata"
    assert dest.name == f"{ln.stem}.wav"
    assert not old.exists()


def test_import_audio_converts_other_formats_with_ffmpeg(plugin_path, tmp_path, monkeypatch):
    ln = lines_of(plugin_path)[0]
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"converted")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(vl, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(vl.subprocess, "run", fake_run)
    src = tmp_path / "take.mp3"
    src.write_bytes(b"mp3")
    dest = vl.import_audio(ln, src, plugin_path, "MaleNord")
    assert dest.suffix == ".wav" and dest.read_bytes() == b"converted"
    assert "-ac" in calls[0] and "s16" in calls[0]

    monkeypatch.setattr(vl, "find_ffmpeg", lambda: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        vl.import_audio(ln, src, plugin_path, "MaleNord")


def fuz(lip, xwm):
    return b"FUZE" + struct.pack("<II", 1, len(lip)) + lip + xwm


def test_split_fuz():
    assert vl.split_fuz(fuz(b"LIP", b"XWMA")) == (b"LIP", b"XWMA")
    with pytest.raises(ValueError):
        vl.split_fuz(b"nope")


def test_convert_to_fuz_pipeline(plugin_path, monkeypatch):
    ln = lines_of(plugin_path)[0]
    wav = vl.line_wav_path(ln, plugin_path, "MaleNord")
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF")
    vl.find_audio(ln, plugin_path)
    tools = {k: Path(k) for k in ("xwmaencode", "lipgenerator", "lipfuzer")}
    seen = []

    def fake_run(cmd, cwd=None, **kw):
        cmd = [str(c) for c in cmd]
        tool, work = Path(cmd[0]).name, Path(cwd)
        seen.append(tool)
        if tool == "lipgenerator":
            assert cmd[2] == "First line."  # the spoken text drives lip sync
            (work / f"{ln.stem}.lip").write_bytes(b"LIP")
        elif tool == "xwmaencode":
            Path(cmd[-1]).write_bytes(b"XWMA")
        elif tool == "lipfuzer":
            (work / f"{ln.stem}.fuz").write_bytes(fuz((work / f"{ln.stem}.lip").read_bytes(),
                                                      (work / f"{ln.stem}.xwm").read_bytes()))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(vl, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(vl.subprocess, "run", fake_run)
    out = vl.convert_to_fuz(ln, plugin_path, tools)
    assert seen == ["lipgenerator", "xwmaencode", "lipfuzer"]
    assert out.suffix == ".fuz" and vl.split_fuz(out.read_bytes()) == (b"LIP", b"XWMA")
    assert not wav.exists() and ln.audio == out

    with pytest.raises(RuntimeError, match="no loose"):
        vl.convert_to_fuz(ln, plugin_path, tools)
    with pytest.raises(RuntimeError, match="missing tools"):
        vl.convert_to_fuz(ln, plugin_path, {})


def test_find_voice_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYRIM_TOOLS_DIR", raising=False)
    tools = tmp_path / "Tools"
    (tools / "Audio").mkdir(parents=True)
    (tools / "Audio" / "xwmaencode.exe").write_bytes(b"")
    assert set(vl.find_voice_tools(tmp_path / "Data")) == {"xwmaencode"}
    monkeypatch.setenv("SKYRIM_TOOLS_DIR", str(tools))
    assert set(vl.find_voice_tools(tmp_path / "elsewhere" / "Data")) == {"xwmaencode"}


def test_package_plugin_ships_voice_files(plugin_path):
    ln = lines_of(plugin_path)[0]
    f = vl.line_wav_path(ln, plugin_path, "MaleNord")
    f.parent.mkdir(parents=True)
    f.write_bytes(b"RIFF")
    names = package_plugin(plugin_path)["files"]
    assert f"Sound/Voice/Voice.esp/MaleNord/{ln.stem}.wav" in names
    with zipfile.ZipFile(plugin_path.parent / "packages" / "Voice.zip") as z_:
        assert f"Sound/Voice/Voice.esp/MaleNord/{ln.stem}.wav" in z_.namelist()


def test_edit_response_changes_text_emotion_and_idles(plugin_path):
    model, lines = vl.load_lines(plugin_path)
    ln = lines[1]
    vl.edit_response(model, plugin_path, ln, "New words.", 5, 80, True, 0, 0)
    again = vl.load_lines(plugin_path)[1][1]
    assert (again.text, again.emotion, again.emotion_value, again.use_emotion_anim) == ("New words.", 5, 80, True)
    assert again.index == 1 and vl.load_lines(plugin_path)[1][0].text != "New words."


def test_edit_response_sets_and_clears_idle_animation(plugin_path):
    plugin = Plugin.load(str(plugin_path))
    idle = Record("IDLE", FormID(0x830), 0)
    idle.add_subrecord("EDID", z("TestIdle"))
    plugin.add_record(idle)
    plugin.save(str(plugin_path))
    model, lines = vl.load_lines(plugin_path)
    assert vl.all_idles(model) == {"TestIdle": 0x830}
    ln = lines[0]
    vl.edit_response(model, plugin_path, ln, ln.text, 0, 50, False, 0x830, 0x830)
    again = vl.load_lines(plugin_path)[1][0]
    assert (again.speaker_idle, again.listener_idle) == (0x830, 0x830)
    first = list(Plugin.load(str(plugin_path)).get_records_by_signature("INFO"))[0]
    sigs = [s.signature for s in first.subrecords]
    second = sigs.index("TRDT", 1)
    assert sigs.index("NAM1") < sigs.index("SNAM") < second and sigs.index("NAM1") < sigs.index("LNAM") < second
    vl.edit_response(model, plugin_path, again, again.text, 0, 50, False, 0, 0)
    again = vl.load_lines(plugin_path)[1][0]
    assert (again.speaker_idle, again.listener_idle) == (0, 0)


def test_edit_response_rejects_bad_emotion_and_takes_snapshot(plugin_path):
    model, lines = vl.load_lines(plugin_path)
    with pytest.raises(ValueError):
        vl.edit_response(model, plugin_path, lines[0], "x", 99, 50, False, 0, 0)
    vl.edit_response(model, plugin_path, lines[0], "x", 1, 50, False, 0, 0)
    assert list(plugin_path.parent.rglob("*pre_dialogue_edit*"))
