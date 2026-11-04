import pytest

import server


def pytest_collection_modifyitems(items):
    if (server.DATA_DIR / "Skyrim.esm").is_file():
        return
    skip = pytest.mark.skip(reason=f"Skyrim.esm not found in {server.DATA_DIR} (set SKYRIM_DATA_DIR)")
    for item in items:
        if "game" in item.keywords:
            item.add_marker(skip)


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
