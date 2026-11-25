"""The stable form-ID registry: IDs are never reused, and drift from the registry is reported."""
import pytest
from esplib import Plugin

import server
from tools.core import idregistry as reg

pytestmark = pytest.mark.fakegame


def _oid(path, editor_id):
    server._models.clear()
    return next(r.form_id.value & 0xFFFFFF for r in Plugin.load(str(path)).records if r.editor_id == editor_id)


def test_ids_are_not_reused_after_a_delete(sandbox):
    path = sandbox / "Ids.esp"
    assert "error" not in server.create_global("Ids.esp", "First", 1)
    assert reg.sync(path)["records"] == 1
    assert "error" not in server.create_global("Ids.esp", "Second", 2)
    second = _oid(path, "Second")
    assert "error" not in server.remove_records("Ids.esp", ["Second"])
    assert "error" not in server.create_global("Ids.esp", "Third", 3)
    assert _oid(path, "Third") > second


def test_registry_floor_survives_a_rebuild(sandbox):
    path = sandbox / "Rebuilt.esp"
    server.create_global("Rebuilt.esp", "A", 1)
    server.create_global("Rebuilt.esp", "B", 1)
    reg.sync(path)
    top = reg.load(path)["next"]
    path.unlink()
    server._models.clear()
    server.create_global("Rebuilt.esp", "Fresh", 1)
    assert _oid(path, "Fresh") >= top


def test_drift_reports_changed_and_unregistered(sandbox):
    path = sandbox / "Drift.esp"
    server.create_global("Drift.esp", "Keep", 1)
    reg.sync(path)
    assert reg.drift(path) == []
    server.create_global("Drift.esp", "New", 1)
    assert {(p["kind"], p.get("editor_id")) for p in reg.drift(path)} == {("unregistered", "New")}
    reg.sync(path)
    assert reg.drift(path) == []


def test_removing_a_registered_record_keeps_it_and_retires_the_id(sandbox):
    path = sandbox / "Tomb.esp"
    server.create_global("Tomb.esp", "Old", 1)
    reg.sync(path)
    res = server.remove_records("Tomb.esp", ["Old"])
    assert res["retired"] and _oid(path, "Old")
    assert reg.load(path)["retired"]
    assert reg.drift(path) == []


def test_baseline_check_flags_removed_records(sandbox):
    from tools.project import compat
    path = sandbox / "Base.esp"
    server.create_global("Base.esp", "One", 1)
    server.create_global("Base.esp", "Two", 1)
    assert compat.check(path)["verdict"] == "no_baseline"
    compat.release(path, "1.0")
    assert compat.check(path)["verdict"] == "ok"
    server.create_global("Base.esp", "Three", 1)
    assert compat.check(path)["verdict"] == "ok"
    from esplib import Plugin
    plug = Plugin.load(str(path))
    plug.remove_record(next(r for r in plug.records if r.editor_id == "Two"))
    plug.save(str(path))
    res = compat.check(path)
    assert res["verdict"] == "breaking"
    assert [f["kind"] for f in res["findings"]] == ["removed"]
    with pytest.raises(ValueError):
        compat.release(path, "1.1")


def test_migrations_apply_once_and_history_is_locked(sandbox):
    from tools.project import runner
    path = sandbox / "Mig.esp"
    folder = runner.migrations_dir(path)
    folder.mkdir(parents=True)
    first = folder / "0001_globals.py"
    first.write_text("def apply(ctx):\n    ctx.create_global('Alpha', 5)\n", encoding="utf-8")
    assert runner.status(path)["pending"] == ["0001_globals.py"]
    res = runner.run(path, server)
    assert res["applied"] == ["0001_globals.py"] and "Alpha" in str(_oid(path, "Alpha") and "Alpha")
    assert runner.run(path, server)["applied"] == []
    first.write_text("def apply(ctx):\n    pass\n", encoding="utf-8")
    with pytest.raises(ValueError):
        runner.run(path, server)


def test_failed_migration_is_rolled_back(sandbox):
    from tools.project import runner
    path = sandbox / "Fail.esp"
    server.create_global("Fail.esp", "Base", 1)
    folder = runner.migrations_dir(path)
    folder.mkdir(parents=True)
    (folder / "0001_bad.py").write_text(
        "def apply(ctx):\n    ctx.create_global('Half', 1)\n    raise RuntimeError('boom')\n", encoding="utf-8")
    res = runner.run(path, server)
    assert res["failed"] == "0001_bad.py"
    assert runner.status(path)["pending"] == ["0001_bad.py"]
    server._models.clear()
    assert not any(r.editor_id == "Half" for r in Plugin.load(str(path)).records)


def test_plugin_without_a_project_folder_is_untouched(sandbox):
    path = sandbox / "Plain.esp"
    server.create_global("Plain.esp", "One", 1)
    assert not reg.registry_path(path).exists()
