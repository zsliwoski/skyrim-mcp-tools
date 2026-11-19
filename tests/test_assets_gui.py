import sys
import tkinter as tk
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "misc"))
import assets_gui  # noqa: E402
import assets_model as am  # noqa: E402
from test_asset_duplicator import mod  # noqa: E402,F401  (fixture: Thing.esp with a loose mesh and textures)


class Dialogs:
    """Records the message boxes and answers the file/yes-no prompts."""
    def __init__(self):
        self.shown, self.errors = [], []
        self.yes = True
        self.open_file = self.save_file = ''

    def showinfo(self, title, text, **_):
        self.shown.append((title, text))

    def showerror(self, title, text, **_):
        self.errors.append((title, text))

    def askyesno(self, *_, **__):
        return self.yes

    def askopenfilename(self, **_):
        return self.open_file

    def asksaveasfilename(self, **_):
        return self.save_file


class SyncThread:
    """Runs the worker immediately so results are queued before the test continues."""
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def dialogs(monkeypatch):
    d = Dialogs()
    for name in ("showinfo", "showerror", "askyesno"):
        monkeypatch.setattr(assets_gui.messagebox, name, getattr(d, name))
    monkeypatch.setattr(assets_gui.filedialog, "askopenfilename", d.askopenfilename)
    monkeypatch.setattr(assets_gui.filedialog, "asksaveasfilename", d.asksaveasfilename)
    return d


@pytest.fixture
def app(dialogs, monkeypatch, tmp_path):
    monkeypatch.setattr(assets_gui.threading, "Thread", SyncThread)
    monkeypatch.setattr(assets_gui, "DATA_DIR", tmp_path / "no_data_dir")
    try:
        root = assets_gui.App()
    except tk.TclError as e:
        pytest.skip(f"no display for Tk: {e}")
    root.withdraw()
    yield root
    root.destroy()


def drain(app):
    while not app.calls.empty():
        app.calls.get()()


def load(app, path):
    app.load(path)
    drain(app)


def select(app, edid):
    row = next(i for i, a in enumerate(app.shown) if a.edid == edid)
    app.tree.selection_set(str(row))
    app.on_select()


def test_window_builds_its_widgets(app):
    assert app.status.winfo_exists() and app.tree.winfo_exists() and app.tex_list.winfo_exists()
    assert app.shown == [] and app.current() is None


def test_load_lists_own_records_and_filters(app, mod):
    load(app, mod)
    assert {a.edid for a in app.shown} == {"ThingStatic", "ThingTextures"}
    assert "2 own records" in app.status.cget("text")
    app.filter.set("textures")
    assert [a.edid for a in app.shown] == ["ThingTextures"]
    assert len(app.tree.get_children()) == 1


def test_load_failure_shows_the_error(app, dialogs, monkeypatch, mod):
    def boom(*_, **__):
        raise RuntimeError("cannot read plugin")
    monkeypatch.setattr(am, "load_assets", boom)
    load(app, mod)
    assert dialogs.errors == [("Load failed", "cannot read plugin")]
    assert app.status.cget("text") == "Load failed"


def test_selecting_a_record_shows_mesh_and_textures(app, mod):
    load(app, mod)
    select(app, "ThingStatic")
    assert app.nif is not None
    assert "Mesh:" in app.detail.cget("text")
    assert app.tex_list.size() == 2 and "[own]" in app.tex_list.get(0)
    assert app.mesh_photo is not None and app.tex_label.cget("text") == "Can't preview this format"


def test_missing_mesh_is_reported(app, mod):
    (mod.parent / "Meshes" / "orig" / "Thing.nif").unlink()
    load(app, mod)
    select(app, "ThingStatic")
    assert "Mesh not found" in app.detail.cget("text") and app.nif is None


def test_duplicate_files_only_writes_copies_and_reports(app, dialogs, mod):
    load(app, mod)
    select(app, "ThingStatic")
    app.run_duplicate(app.current(), "Mine", "files", "", None)
    drain(app)
    assert (mod.parent / "Meshes" / "Mine" / "thing.nif").is_file()
    assert dialogs.shown and "files written" in dialogs.shown[0][1] and "No record was changed" in dialogs.shown[0][1]
    assert not dialogs.errors


def test_duplicate_failure_shows_the_error(app, dialogs, monkeypatch, mod):
    load(app, mod)
    select(app, "ThingStatic")

    def boom(*_, **__):
        raise ValueError("bad folder")
    monkeypatch.setattr(am, "duplicate_asset", boom)
    app.run_duplicate(app.current(), "Mine", "files", "", None)
    drain(app)
    assert dialogs.errors == [("Duplicate failed", "bad folder")]


def test_replace_texture_only_touches_the_plugins_own_file(app, dialogs, mod, tmp_path):
    load(app, mod)
    select(app, "ThingTextures")
    new = tmp_path / "new.dds"
    new.write_bytes(b"REPLACED")
    dialogs.open_file = str(new)
    app.tex_list.selection_clear(0, "end")
    app.tex_list.selection_set(0)
    app.replace()
    assert (mod.parent / "Textures" / "orig" / "Thing_d.dds").read_bytes() == b"REPLACED"
    assert "Replaced" in app.status.cget("text")


def test_export_obj_writes_the_selected_mesh(app, dialogs, mod, tmp_path):
    load(app, mod)
    select(app, "ThingStatic")
    out = tmp_path / "thing.obj"
    dialogs.save_file = str(out)
    app.export_obj(None, dict(assets_gui.nif_tools.editable_shapes(app.nif)))
    text = out.read_text(encoding="utf-8")
    assert len([ln for ln in text.splitlines() if ln.startswith("v ")]) == 3


def test_import_obj_needs_a_copy_in_the_plugin_folder(app, dialogs, mod):
    load(app, mod)
    select(app, "ThingStatic")
    (mod.parent / "Meshes" / "orig" / "Thing.nif").unlink()
    editable = {1: "shape"}
    app.import_obj(app.current(), 1, editable, app)
    assert dialogs.shown and "isn't in the plugin's own Meshes folder" in dialogs.shown[0][1]


def test_pack_zip_and_install_report_results(app, dialogs, monkeypatch, mod):
    load(app, mod)
    monkeypatch.setattr(assets_gui, "package_plugin", lambda p: {"zip": "Thing.zip", "files": ["a", "b"]})
    app.pack_zip()
    assert dialogs.shown[-1] == ("Pack", "Thing.zip\n2 files")
    monkeypatch.setattr(assets_gui, "install", lambda p, d: {"summary": "installed 2 files"})
    app.install_data()
    assert dialogs.shown[-1] == ("Install", "installed 2 files")
    dialogs.yes = False
    app.install_data()
    assert dialogs.shown[-1][0] == "Install" and len(dialogs.shown) == 2


def test_pack_zip_error_is_shown(app, dialogs, monkeypatch, mod):
    load(app, mod)

    def boom(_):
        raise OSError("disk full")
    monkeypatch.setattr(assets_gui, "package_plugin", boom)
    app.pack_zip()
    assert dialogs.errors == [("Pack", "disk full")]
