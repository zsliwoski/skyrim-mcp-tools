"""Tkinter GUI: browse the meshes and textures records use, preview them, and duplicate them into the plugin's own
Meshes/Textures folders under a cloned record so they can be edited without touching the originals.

Usage: python misc/assets_gui.py [plugin.esp]"""
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assets_model as am
from tools.core import nif_tools
from tools.core.plugin_edit import package_plugin
from tools.records.maintenance import install

DATA_DIR = Path(os.environ.get('SKYRIM_DATA_DIR',
                               r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"))
PREVIEW = 420


class App(tk.Tk):
    def __init__(self, plugin=None):
        super().__init__()
        self.title("Skyrim mesh & texture duplicator")
        self.geometry("1350x760")
        self.plugin = None
        self.model = None
        self.assets = []
        self.archives = {}
        self.shown = []
        self.nif = None
        self.tex_cache = {}
        self.view = [0.6, 0.3]
        self.drag = None
        self.mesh_photo = self.tex_photo = None
        self.tex_files = {}  # texture list index -> (path relative to Textures, loose file in the plugin folder or None)
        self.calls = queue.Queue()
        self._build_ui()
        self.after(100, self.poll)
        if plugin:
            self.load(plugin)

    def dirs(self):
        return [self.plugin.parent] + ([DATA_DIR] if DATA_DIR.is_dir() else [])

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=4)
        ttk.Button(top, text="Open plugin...", command=self.choose).pack(side='left')
        ttk.Button(top, text="Pack zip", command=self.pack_zip).pack(side='left', padx=(10, 0))
        ttk.Button(top, text="Install to Data", command=self.install_data).pack(side='left', padx=4)
        self.status = ttk.Label(top, text="")
        self.status.pack(side='left', padx=10)

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=6)
        self.filter = tk.StringVar()
        self.filter.trace_add('write', lambda *_: self.fill())
        ttk.Label(bar, text="Filter:").pack(side='left')
        ttk.Entry(bar, textvariable=self.filter, width=40).pack(side='left', padx=4)
        self.everything = tk.BooleanVar()
        ttk.Checkbutton(bar, text="Include master records (type 3+ filter characters)", variable=self.everything,
                        command=self.fill).pack(side='left', padx=8)

        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=6, pady=6)
        left = ttk.Frame(body)
        body.add(left, weight=3)
        cols = ('mine', 'type', 'editor', 'files')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', selectmode='browse')
        for c, w in zip(cols, (50, 60, 230, 420)):
            self.tree.heading(c, text={'mine': 'Own'}.get(c, c.title()))
            self.tree.column(c, width=w, stretch=(c == 'files'))
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        right = ttk.Frame(body)
        body.add(right, weight=2)
        self.mesh = self.pane(right, PREVIEW, "Select a record")
        self.mesh.bind('<ButtonPress-1>', self.start_drag)
        self.mesh.bind('<B1-Motion>', self.do_drag)
        self.textured = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Show textures on the mesh (drag to rotate)", variable=self.textured,
                        command=self.draw_mesh).pack(anchor='w')
        ttk.Label(right, text="Textures:").pack(anchor='w', pady=(6, 0))
        self.tex_list = tk.Listbox(right, height=6)
        self.tex_list.pack(fill='x')
        self.tex_list.bind('<<ListboxSelect>>', self.on_texture)
        self.tex_label = self.pane(right, 256, '')
        row = ttk.Frame(right)
        row.pack(fill='x')
        ttk.Button(row, text="Duplicate...", command=self.duplicate).pack(side='left')
        ttk.Button(row, text="Use another model...", command=self.use_model).pack(side='left', padx=4)
        ttk.Button(row, text="Mesh structure...", command=self.structure).pack(side='left', padx=4)
        row2 = ttk.Frame(right)
        row2.pack(fill='x', pady=(4, 0))
        ttk.Button(row2, text="First-person model...", command=self.first_person).pack(side='left')
        ttk.Button(row2, text="Replace texture...", command=self.replace).pack(side='left', padx=4)
        ttk.Button(row2, text="Open folder", command=self.open_folder).pack(side='left')
        self.detail = ttk.Label(right, text="", wraplength=440, justify='left')
        self.detail.pack(fill='x', pady=4)

    @staticmethod
    def pane(parent, size, text):
        """A fixed pixel-size label for images."""
        frame = tk.Frame(parent, width=size, height=size, bg='#24262c')
        frame.pack_propagate(False)
        frame.pack(pady=4)
        label = tk.Label(frame, bg='#24262c', fg='#aaa', text=text, wraplength=size - 20)
        label.pack(fill='both', expand=True)
        return label

    def choose(self):
        p = filedialog.askopenfilename(title="Plugin", filetypes=[("Plugins", "*.esp *.esm *.esl"), ("All", "*.*")])
        if p:
            self.load(p)

    def load(self, path, then=None):
        self.plugin = Path(path)
        self.status.config(text=f"Loading {self.plugin.name}...")

        def work():
            try:
                model, assets = am.load_assets(self.plugin, lambda s: self.calls.put(lambda: self.status.config(text=s)),
                                               master_dirs=[DATA_DIR])
                self.calls.put(lambda: self.status.config(text="Indexing archives..."))
                archives = am.archive_index(self.dirs(), self.plugin.name)
                self.calls.put(lambda: (self.loaded(model, assets, archives), then and then()))
            except Exception as e:
                self.calls.put(lambda: (self.status.config(text="Load failed"), messagebox.showerror("Load failed", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, model, assets, archives):
        self.model, self.assets, self.archives = model, assets, archives
        self.fill()

    def poll(self):
        while not self.calls.empty():
            self.calls.get()()
        self.after(100, self.poll)

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        q = self.filter.get().lower()
        self.shown = []
        wide = self.everything.get()
        for a in self.assets:
            if not a.owned and not (wide and len(q) >= 3):
                continue
            files = ', '.join(p for _, p in a.models + a.textures)
            if q and q not in f"{a.sig} {a.edid} {files}".lower():
                continue
            self.tree.insert('', 'end', iid=str(len(self.shown)), values=('yes' if a.owned else '', a.sig, a.edid, files))
            self.shown.append(a)
            if len(self.shown) >= 3000:
                break
        own = sum(a.owned for a in self.assets)
        self.status.config(text=f"{self.plugin.name if self.plugin else ''}: {own} own records with models/textures, "
                                f"{len(self.shown)} shown")

    def current(self):
        sel = self.tree.selection()
        return self.shown[int(sel[0])] if sel else None

    def on_select(self, _=None):
        a = self.current()
        self.nif = None
        self.tex_cache = {}
        self.tex_list.delete(0, 'end')
        self.tex_files = {}
        self.tex_label.config(image='', text='')
        self.mesh.config(image='', text="No mesh")
        if a is None:
            return
        notes = []
        textures = [(p, 'textures') for _, p in a.textures]
        if a.models:
            data, origin = am.read_asset('meshes', a.models[0][1], self.dirs(), self.archives)
            if data is None:
                notes.append(f"Mesh not found: {a.models[0][1]}")
            else:
                notes.append(f"Mesh: {origin}")
                try:
                    self.nif = nif_tools.parse(data)
                    textures += [(p, 'textures') for p in am.nif_textures(data)]
                except nif_tools.NifError as e:
                    notes.append(f"Can't read the NIF: {e}")
                self.draw_mesh()
        for p, _ in textures:
            rel = am.strip_dir(p, 'textures')
            loose = self.plugin.parent / 'Textures' / rel
            self.tex_files[self.tex_list.size()] = (rel, loose if loose.is_file() else None)
            self.tex_list.insert('end', f"{'[own] ' if loose.is_file() else ''}{rel}")
        self.detail.config(text='\n'.join(notes))
        if textures:
            self.tex_list.selection_set(0)
            self.on_texture()

    def load_texture(self, path):
        if path not in self.tex_cache:
            data, _ = am.read_asset('textures', path, self.dirs(), self.archives)
            self.tex_cache[path] = am.texture_array(data) if data else None
        return self.tex_cache[path]

    def draw_mesh(self):
        if self.nif is None:
            return
        from PIL import ImageTk
        img = am.render_mesh(self.nif, PREVIEW, *self.view, self.load_texture if self.textured.get() else None) \
            if self.nif else None
        if img is None:
            self.mesh.config(image='', text="No previewable geometry (skinned or unsupported shape)")
            self.mesh_photo = None
            return
        self.mesh_photo = ImageTk.PhotoImage(img)
        self.mesh.config(image=self.mesh_photo, text='')

    def start_drag(self, e):
        self.drag = (e.x, e.y, *self.view)

    def do_drag(self, e):
        if self.nif is None or self.drag is None:
            return
        x, y, yaw, pitch = self.drag
        self.view = [yaw + (e.x - x) * 0.01, max(-1.5, min(1.5, pitch + (e.y - y) * 0.01))]
        self.draw_mesh()

    def on_texture(self, _=None):
        sel = self.tex_list.curselection()
        if not sel:
            return
        from PIL import ImageTk
        rel, loose = self.tex_files[sel[0]]
        data, origin = am.read_asset('textures', rel, self.dirs(), self.archives)
        img = am.texture_image(data) if data else None
        if img is None:
            self.tex_label.config(image='', text="Texture not found" if data is None else "Can't preview this format")
            self.tex_photo = None
            return
        img.thumbnail((256, 256))
        self.tex_photo = ImageTk.PhotoImage(img)
        self.tex_label.config(image=self.tex_photo, text='')

    def duplicate(self):
        a = self.current()
        if a is None or self.plugin is None:
            return
        win = tk.Toplevel(self)
        win.title("Duplicate")
        win.transient(self)
        dest = tk.StringVar(value=self.plugin.stem.replace(' ', '_'))
        edid = tk.StringVar(value=f"{a.edid or a.sig}_Copy")
        mode = tk.StringVar(value='clone')
        ttk.Label(win, text="Folder for the copies (Meshes\\<name>, Textures\\<name>):").pack(anchor='w', padx=8, pady=(8, 0))
        ttk.Entry(win, textvariable=dest, width=40).pack(padx=8, fill='x')
        ttk.Radiobutton(win, text="Clone the record (new editor ID below)", variable=mode, value='clone').pack(
            anchor='w', padx=8, pady=(8, 0))
        ttk.Entry(win, textvariable=edid, width=40).pack(padx=28, fill='x')
        ttk.Radiobutton(win, text="Repoint this record at the copies" + ("" if a.owned else " (adds an override to the plugin)"),
                        variable=mode, value='repoint').pack(anchor='w', padx=8, pady=(6, 0))
        ttk.Radiobutton(win, text="Copy the files only (don't touch any record)", variable=mode, value='files').pack(
            anchor='w', padx=8, pady=(6, 0))

        def go():
            win.destroy()
            self.run_duplicate(a, dest.get().strip(), mode.get(), edid.get().strip())
        ttk.Button(win, text="Duplicate", command=go).pack(pady=8)
        win.grab_set()

    def run_duplicate(self, a, dest, mode, edid):
        self.status.config(text="Duplicating...")

        def work():
            try:
                res = am.duplicate_asset(self.model, self.plugin, a, dest, edid if mode == 'clone' else None, self.dirs(),
                                         self.archives, DATA_DIR if DATA_DIR.is_dir() else None, mode)
                self.calls.put(lambda: self.duplicated(res, a, mode))
            except Exception as e:
                self.calls.put(lambda: (self.status.config(text="Duplicate failed"),
                                        messagebox.showerror("Duplicate failed", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def duplicated(self, res, original=None, mode=None):
        warn = '\n'.join(res['warnings'])
        what = f"Record {res['record']} updated; snapshot 'pre_asset_duplicate' taken first." if res['record'] \
            else "No record was changed."
        messagebox.showinfo("Duplicate", f"{len(res['files'])} files written. {what}\n{warn}")
        if mode == 'clone' and original is not None and original.models and messagebox.askyesno(
                "Use the copy?", f"{original.edid or original.sig} still uses the original mesh, and that is the record "
                                 f"placed in the world or on NPCs.\n\nMake it use the new copy's mesh as well?"):
            self.load(self.plugin, then=lambda: self.apply_model(original.fid, res['record']))
            return
        self.load(self.plugin)

    def apply_model(self, fid, source_edid):
        target = next((a for a in self.assets if a.fid == fid), None)
        source = next((a for a in self.assets if a.edid == source_edid and a.owned), None)
        if target is None or source is None:
            return
        self.run_use_model(target, source)

    def first_person(self):
        a = self.current()
        if a is None or self.plugin is None:
            return
        fp = am.first_person_asset(self.model, self.assets, a)
        if fp is None:
            messagebox.showinfo("First person", "Select a weapon that has a first-person model record.")
            return
        guess = a.models[0][1].split('\\')[0] if a.models and '\\' in a.models[0][1] else self.plugin.stem
        dest = simpledialog.askstring("First person", f"Give {a.edid} its own copy of {fp.edid}.\n\nFolder of the weapon's "
                                      "own copies (its textures there are reused):", initialvalue=guess, parent=self)
        if not dest:
            return
        self.status.config(text="Copying first-person model...")

        def work():
            try:
                edid = am.give_first_person(self.model, self.assets, self.plugin, a, dest.strip(), self.dirs(),
                                            self.archives, DATA_DIR if DATA_DIR.is_dir() else None)
                self.calls.put(lambda: (self.load(self.plugin), messagebox.showinfo(
                    "First person", f"{a.edid} now uses {edid} in first person. Use Install to Data.")))
            except Exception as e:
                self.calls.put(lambda: (self.status.config(text="Failed"), messagebox.showerror("First person", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def use_model(self):
        a = self.current()
        if a is None or self.plugin is None or not a.models:
            return
        mine = [s for s in self.assets if s.owned and s.models and s.fid != a.fid]
        if not mine:
            messagebox.showinfo("Use model", "No other record of yours has a model to copy.")
            return
        win = tk.Toplevel(self)
        win.title("Use another record's model")
        win.transient(self)
        ttk.Label(win, text=f"Make {a.edid or a.sig} use the model of:").pack(anchor='w', padx=8, pady=(8, 0))
        box = tk.Listbox(win, width=60, height=12, exportselection=False)
        for s in mine:
            box.insert('end', f"{s.edid or s.sig}  -  {s.models[0][1]}")
        box.pack(padx=8, pady=4, fill='both', expand=True)

        def go():
            if box.curselection():
                src = mine[box.curselection()[0]]
                win.destroy()
                self.run_use_model(a, src)
        ttk.Button(win, text="Use this model", command=go).pack(pady=8)
        win.grab_set()

    def run_use_model(self, target, source):
        self.status.config(text="Updating...")

        def work():
            try:
                edid = am.use_model(self.model, self.plugin, target, source, DATA_DIR if DATA_DIR.is_dir() else None)
                self.calls.put(lambda: (self.load(self.plugin), messagebox.showinfo(
                    "Use model", f"{edid} now uses {source.models[0][1]}. Use Install to Data so the game sees it.")))
            except Exception as e:
                self.calls.put(lambda: (self.status.config(text="Failed"), messagebox.showerror("Use model", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def structure(self):
        a = self.current()
        if a is None or self.nif is None:
            messagebox.showinfo("Structure", "Select a record with a readable mesh first.")
            return
        win = tk.Toplevel(self)
        win.title(f"Mesh structure - {a.models[0][1]}")
        win.geometry("760x520")
        tree = ttk.Treeview(win, columns=('block', 'detail'), selectmode='browse')
        tree.heading('#0', text="Node / shape")
        tree.heading('block', text="Block")
        tree.heading('detail', text="Detail")
        tree.column('#0', width=380)
        tree.column('block', width=60, stretch=False)
        tree.column('detail', width=240)
        tree.pack(fill='both', expand=True, padx=6, pady=6)
        editable = dict(nif_tools.editable_shapes(self.nif))
        parents = {}
        for n, (i, depth, typ, name, detail) in enumerate(nif_tools.outline(self.nif)):
            parent = parents.get(depth - 1, '')
            tag = ('shape',) if i in editable and typ == 'BSTriShape' else ()
            parents[depth] = tree.insert(parent, 'end', iid=str(n), open=True, text=f"{typ}  {name}".strip(),
                                         values=(i, detail), tags=tag)
        tree.tag_configure('shape', foreground='#0a6')
        ttk.Label(win, text="Green shapes can be exported/imported (unrigged BSTriShape). Importing rewrites the mesh "
                            "file in this plugin's Meshes folder, so Duplicate it first.", wraplength=720).pack(anchor='w', padx=6)
        row = ttk.Frame(win)
        row.pack(fill='x', padx=6, pady=6)

        def picked():
            sel = tree.selection()
            if not sel:
                return None
            block = int(tree.item(sel[0], 'values')[0])
            return block if block in editable else None
        ttk.Button(row, text="Export selected shape...", command=lambda: self.export_obj(picked(), editable)).pack(side='left')
        ttk.Button(row, text="Export all shapes...", command=lambda: self.export_obj(None, editable)).pack(side='left', padx=4)
        ttk.Button(row, text="Import OBJ into selected shape...",
                   command=lambda: self.import_obj(a, picked(), editable, win)).pack(side='left')

    def export_obj(self, block, editable):
        if block is None and not messagebox.askyesno("Export", f"Export all {len(editable)} shapes into one OBJ?"):
            return
        if not editable:
            messagebox.showinfo("Export", "This mesh has no exportable shapes.")
            return
        stem = Path(self.current().models[0][1]).stem
        name = editable[block] if block is not None else stem
        path = filedialog.asksaveasfilename(title="Export OBJ", defaultextension=".obj", initialfile=f"{name}.obj".replace(':', '_'),
                                            filetypes=[("Wavefront OBJ", "*.obj")])
        if not path:
            return
        try:
            text = nif_tools.export_obj(self.nif, None if block is None else [block])
        except nif_tools.NifError as e:
            messagebox.showerror("Export", str(e))
            return
        Path(path).write_text(text, encoding='utf-8')
        self.status.config(text=f"Exported {path}")

    def import_obj(self, asset, block, editable, parent):
        if block is None:
            messagebox.showinfo("Import", "Select one of the green shapes first.", parent=parent)
            return
        target = self.plugin.parent / 'Meshes' / am.strip_dir(asset.models[0][1], 'meshes')
        if not target.is_file():
            messagebox.showinfo("Import", "This mesh isn't in the plugin's own Meshes folder yet. Use Duplicate "
                                          "(any mode, files only is fine) and select the copy.", parent=parent)
            return
        src = filedialog.askopenfilename(title=f"OBJ for {editable[block]}", filetypes=[("Wavefront OBJ", "*.obj")], parent=parent)
        if not src:
            return
        try:
            text = Path(src).read_text(encoding='utf-8', errors='replace')
            names = list(nif_tools.parse_obj(text))
            pick = None
            if len(names) > 1 and editable[block] not in names:
                pick = simpledialog.askstring("Import", "The OBJ has several objects: " + ", ".join(names) +
                                              "\n\nWhich one replaces this shape?", initialvalue=names[0], parent=parent)
                if not pick:
                    return
            elif len(names) > 1:
                pick = editable[block]
            new = nif_tools.import_obj(nif_tools.parse(target.read_bytes()), block, text, pick)
        except (nif_tools.NifError, nif_tools.ObjError) as e:
            messagebox.showerror("Import", str(e), parent=parent)
            return
        target.write_bytes(new)
        self.on_select()
        messagebox.showinfo("Import", f"{editable[block]} replaced in {target.name}. Pack or Install to Data to use it.",
                            parent=parent)

    def replace(self):
        sel = self.tex_list.curselection()
        if not sel:
            return
        rel, loose = self.tex_files[sel[0]]
        if loose is None:
            messagebox.showinfo("Replace", "Only textures in the plugin's own Textures folder can be replaced. "
                                           "Duplicate the record first, then select the copy.")
            return
        src = filedialog.askopenfilename(title=f"Replace {rel}", filetypes=[("DDS texture", "*.dds")])
        if not src:
            return
        shutil.copyfile(src, loose)
        self.on_texture()
        self.status.config(text=f"Replaced {rel}")

    def open_folder(self):
        if self.plugin:
            os.startfile(self.plugin.parent)

    def pack_zip(self):
        if self.plugin is None:
            return
        try:
            r = package_plugin(self.plugin)
        except Exception as e:
            messagebox.showerror("Pack", str(e))
            return
        messagebox.showinfo("Pack", f"{r['zip']}\n{len(r['files'])} files")

    def install_data(self):
        if self.plugin is None:
            return
        if not messagebox.askyesno("Install", f"Copy the plugin and its files into\n{DATA_DIR}?"):
            return
        try:
            r = install(self.plugin, DATA_DIR)
        except Exception as e:
            messagebox.showerror("Install", str(e))
            return
        messagebox.showinfo("Install", r['summary'])


if __name__ == '__main__':
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
