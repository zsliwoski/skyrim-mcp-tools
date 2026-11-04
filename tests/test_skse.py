from tools.papyrus.skse import detect_skse


def _game(tmp_path):
    data = tmp_path / "game" / "Data"
    data.mkdir(parents=True)
    return data, tmp_path / "out"


def test_absent(tmp_path):
    data, out = _game(tmp_path)
    r = detect_skse(data, out)
    assert not r["installed"]
    assert r["notes"] == []


def test_installed_without_sources(tmp_path):
    data, out = _game(tmp_path)
    (data.parent / "skse64_loader.exe").write_bytes(b"")
    (data.parent / "skse64_1_6_1170.dll").write_bytes(b"")
    (data / "SKSE" / "Plugins").mkdir(parents=True)
    (data / "SKSE" / "Plugins" / "po3_papyrusextender.dll").write_bytes(b"")
    r = detect_skse(data, out)
    assert r["installed"] and r["loader"] == ["skse64_loader.exe"]
    assert r["plugins"] == ["po3_papyrusextender.dll"]
    assert r["compiler_sees_skse_sources"] == [] and r["notes"]


def test_sources_and_script_users(tmp_path):
    data, out = _game(tmp_path)
    (data / "Scripts").mkdir()
    (data / "Scripts" / "ModEvent.pex").write_bytes(b"")
    src = tmp_path / "src"
    src.mkdir()
    (src / "ModEvent.psc").write_text("Scriptname ModEvent Hidden")
    (out / "Scripts").mkdir(parents=True)
    (out / "Scripts" / "UsesIt.pex").write_bytes(b"xx ModEvent yy")
    (out / "Scripts" / "Plain.pex").write_bytes(b"nothing")
    r = detect_skse(data, out, [src])
    assert r["installed"] and r["compiler_sees_skse_sources"] == [str(src)]
    assert r["output_scripts_using_skse"] == [{"script": "UsesIt", "references": ["ModEvent"]}]
    assert r["notes"] == []
