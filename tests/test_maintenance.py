"""Unit tests for snapshot, install and validate helpers (no game files needed)."""
import pytest

from tools.records import maintenance as m


@pytest.fixture
def plugin(tmp_path):
    p = tmp_path / "out" / "Test.esp"
    p.parent.mkdir()
    p.write_bytes(b"v1")
    return p


def test_snapshot_and_restore_roundtrip(plugin):
    m.snapshot(plugin, "first")
    plugin.write_bytes(b"v2")
    res = m.restore(plugin, "first")
    assert plugin.read_bytes() == b"v1"
    assert res["previous_state_saved_as"].endswith(".pre_restore.esp")
    assert len(m.list_snapshots(plugin)) == 2


def test_restore_without_snapshots_fails(plugin):
    with pytest.raises(ValueError):
        m.restore(plugin)


def test_restore_unknown_label_fails(plugin):
    m.snapshot(plugin, "a")
    with pytest.raises(ValueError):
        m.restore(plugin, "nope")


def test_install_reports_new_changed_and_same(plugin, tmp_path, monkeypatch):
    data = tmp_path / "Data"
    data.mkdir()
    monkeypatch.setattr(m, "plugin_files", lambda path: [plugin])
    assert m.install(plugin, data, dry_run=True)["files"][0]["state"] == "new"
    assert not (data / "Test.esp").exists()
    assert m.install(plugin, data)["files"][0]["state"] == "new"
    assert m.install(plugin, data)["files"][0]["state"] == "same"
    plugin.write_bytes(b"v2")
    assert m.install(plugin, data)["files"][0]["state"] == "changed"
    assert (data / "Test.esp").read_bytes() == b"v2"
