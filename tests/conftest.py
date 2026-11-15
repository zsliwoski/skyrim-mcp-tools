import pytest

import server


def pytest_collection_modifyitems(items):
    if (server.DATA_DIR / "Skyrim.esm").is_file():
        return
    skip = pytest.mark.skip(reason=f"Skyrim.esm not found in {server.DATA_DIR} (set SKYRIM_DATA_DIR)")
    for item in items:
        if "game" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fake_data_dir(tmp_path_factory):
    """A Data folder holding only the tiny fixture Skyrim.esm from fake_master.py."""
    import fake_master
    mp = pytest.MonkeyPatch()
    mp.setenv("ESPLIB_CACHE_DIR", str(tmp_path_factory.mktemp("esplib_cache")))
    data = tmp_path_factory.mktemp("data")
    fake_master.build(data / "Skyrim.esm")
    yield data
    mp.undo()


@pytest.fixture(autouse=True)
def use_fake_master(request, monkeypatch):
    """Tests marked fakegame run against the fixture master instead of the real game files."""
    if "fakegame" in request.keywords:
        monkeypatch.setattr(server, "DATA_DIR", request.getfixturevalue("fake_data_dir"))


@pytest.fixture(scope="session", autouse=True)
def keep_models_cached():
    """Writes normally drop the cached models, which would reload Skyrim.esm after every test."""
    mp = pytest.MonkeyPatch()
    mp.setattr(server, "_invalidate", lambda: None)
    yield
    mp.undo()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every tool's output folder to a temp dir and clear NPC drafts."""
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "_drafts", {})
    return tmp_path


@pytest.fixture
def npc_plugin(sandbox):
    res = server.create_npc({
        "plugin_name": "TestMod.esp", "editor_id": "TestSven", "name": "Test Sven", "clone_from": "Sven",
        "dialogue": [{"kind": "topic", "prompt": "Hello there", "lines": ["Hi."]}],
    })
    assert "error" not in res, res
    return sandbox / "TestMod.esp"


@pytest.fixture(scope="session")
def compiler_available():
    from tools.papyrus.papyrus_build import CK_DIR
    if not (CK_DIR / "Papyrus Compiler" / "PapyrusCompiler.exe").is_file():
        pytest.skip("Papyrus compiler not found (set SKYRIM_CK_DIR)")
