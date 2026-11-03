"""Tkinter GUI: load all quests from a plugin and show a dependency tree
(scripts, aliases, NPCs, world references, dialogue, scenes) for each."""
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esplib.vmad import VmadData
from graph_view import GraphView
from tools.papyrus.pex_tools import ScriptStore, script_bsas
from tools.core.quest_model import *  # noqa: F401,F403


class App(tk.Tk):
    def __init__(self, paths):
        super().__init__()
        self.paths = []
        self.title("Skyrim quest dependency viewer")
        self.geometry("1300x800")
        self.model = None
        self.details = {}
        self.script_of = {}  # tree node id -> script name
        self.key_of, self.iid_of = {}, {}  # tree node id <-> graph entity key
        self.g_nodes, self.g_edges = {}, set()
        self.store = None
        self.extra_bsas = []
        self.calls = queue.Queue()
        self.path = paths[0]
        self._build_ui()
        self.poll()
        self.load(paths)

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=4)
        ttk.Button(top, text="Open plugins...", command=self.choose).pack(side='left')
        ttk.Button(top, text="Add plugins...", command=self.add_plugins).pack(side='left', padx=4)
        ttk.Button(top, text="Add BSAs...", command=self.add_bsas).pack(side='left')
        ttk.Button(top, text="Expand all", command=lambda: self.expand(True)).pack(side='left', padx=4)
        ttk.Button(top, text="Collapse all", command=lambda: self.expand(False)).pack(side='left')
        self.status = ttk.Label(top, text="")
        self.status.pack(side='left', padx=10)

        panes = ttk.PanedWindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True)

        left = ttk.Frame(panes)
        self.filter = tk.StringVar()
        self.filter.trace_add('write', lambda *_: self.fill_quests())
        ttk.Entry(left, textvariable=self.filter).pack(fill='x', padx=4, pady=4)
        self.qlist = ttk.Treeview(left, columns=('name', 'plugin'), show='tree headings', selectmode='browse')
        self.qlist.heading('#0', text='Editor ID')
        self.qlist.heading('name', text='Name')
        self.qlist.heading('plugin', text='Plugin')
        self.qlist.column('#0', width=170)
        self.qlist.column('name', width=130)
        self.qlist.column('plugin', width=110)
        sb = ttk.Scrollbar(left, command=self.qlist.yview)
        self.qlist.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.qlist.pack(fill='both', expand=True)
        self.qlist.bind('<<TreeviewSelect>>', self.on_quest)
        panes.add(left, weight=1)

        right = ttk.PanedWindow(panes, orient='vertical')
        self.tabs = ttk.Notebook(right)
        frame = ttk.Frame(self.tabs)
        self.graph = GraphView(self.tabs, self.on_graph_select)
        self.tabs.add(frame, text="Tree")
        self.tabs.add(self.graph, text="Graph")
        self.tree = ttk.Treeview(frame, show='tree')
        sb2 = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb2.set)
        sb2.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.on_node)
        right.add(self.tabs, weight=4)
        txt = ttk.Frame(right)
        self.text = tk.Text(txt, height=14, wrap='none', font=('Consolas', 10), tabs=('1c',))
        ysb = ttk.Scrollbar(txt, command=self.text.yview)
        xsb = ttk.Scrollbar(txt, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side='right', fill='y')
        xsb.pack(side='bottom', fill='x')
        self.text.pack(fill='both', expand=True)
        for tag, color in (('kw', '#0000cc'), ('type', '#267f99'), ('num', '#098658'),
                           ('label', '#a31515'), ('temp', '#777777'), ('str', '#a31515'),
                           ('comment', '#008000')):
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure('kw', font=('Consolas', 10, 'bold'))
        right.add(txt, weight=2)
        panes.add(right, weight=4)

    def set_status(self, msg):
        self.calls.put(lambda: self.status.config(text=msg))

    def poll(self):
        while True:
            try:
                self.calls.get_nowait()()
            except queue.Empty:
                break
        self.after(100, self.poll)

    def pick(self):
        return filedialog.askopenfilenames(filetypes=[("Plugins", "*.esm *.esp *.esl"), ("All", "*.*")])

    def choose(self):
        ps = self.pick()
        if ps:
            self.load(list(ps))

    def add_plugins(self):
        ps = self.pick()
        if ps:
            self.load(self.paths + [p for p in ps if str(Path(p)) not in map(str, self.paths)])

    def load(self, paths):
        if isinstance(paths, (str, Path)):
            paths = [paths]
        self.paths = [Path(p) for p in paths]
        self.path = self.paths[0]
        self.store = None
        self.qlist.delete(*self.qlist.get_children())
        self.tree.delete(*self.tree.get_children())

        def work():
            try:
                self.model = Model(self.paths, self.set_status)
            except Exception as e:
                self.set_status(f"Load failed: {e!r}")
                return
            self.calls.put(self.loaded)

        threading.Thread(target=work, daemon=True).start()

    def loaded(self):
        names = ', '.join(p.file_path.name for p in self.model.plugins)
        self.set_status(f"{len(self.model.quests)} quests from {len(self.model.plugins)} plugin(s): {names}")
        self.fill_quests()

    def fill_quests(self):
        if not self.model:
            return
        self.qlist.delete(*self.qlist.get_children())
        needle = self.filter.get().lower()
        for q in self.model.quests:
            name = q.full_name or ''
            edid = q.editor_id or ''
            if needle and needle not in edid.lower() and needle not in name.lower():
                continue
            fid = self.model.rid(q)
            self.qlist.insert('', 'end', iid=str(fid), text=edid or f"{fid:08X}",
                              values=(name, q.plugin.file_path.name))

    def on_quest(self, _):
        sel = self.qlist.selection()
        if not sel:
            return
        self.tree.delete(*self.tree.get_children())
        self.details.clear()
        self.script_of.clear()
        self.key_of.clear()
        self.iid_of.clear()
        self.g_nodes, self.g_edges = {}, set()
        qid = int(sel[0])
        QuestTree(self, self.model, qid).build()
        self.graph.set_graph(self.g_nodes, self.g_edges, ('f', qid))

    def on_graph_select(self, key):
        iid = self.iid_of.get(key)
        if iid:
            self.tree.see(iid)
            self.tree.selection_set(iid)  # on_node shows details / script text

    KEYWORDS = (r'ScriptName|extends|Function|EndFunction|Event|EndEvent|Property|EndProperty|'
                r'State|EndState|Auto|Global|Native|Return|return|goto|if|new|as|Get|Set')
    TYPES = r'\b(?:Int|Float|Bool|String|Form|Actor|ObjectReference|Quest|Alias|ReferenceAlias|None)\b'

    def on_node(self, _):
        sel = self.tree.selection()
        self.text.delete('1.0', 'end')
        if not sel:
            return
        if sel[0] in self.key_of:
            self.graph.highlight(self.key_of[sel[0]])
        script = self.script_of.get(sel[0])
        if script:
            self.show_script(script)
        else:
            self.text.insert('1.0', self.details.get(sel[0], self.tree.item(sel[0], 'text')))

    def script_bsas(self):
        plugins = self.model.plugins if self.model else []
        return script_bsas([pl.file_path for pl in plugins], self.extra_bsas)

    def add_bsas(self):
        ps = filedialog.askopenfilenames(filetypes=[("BSA archives", "*.bsa"), ("All", "*.*")])
        if ps:
            self.extra_bsas += [str(Path(p)) for p in ps if str(Path(p)) not in self.extra_bsas]
            self.store = None
            self.set_status(f"{len(self.extra_bsas)} extra BSA(s) added")
            self.on_node(None)

    def show_script(self, name):
        self.text.insert('1.0', f"; loading {name}...")
        self.status.config(text=f"loading script {name}")

        def work():
            try:
                if self.store is None:
                    self.store = ScriptStore(Path(self.path).parent, self.script_bsas())
                found = self.store.get(name)
            except Exception as e:
                found = (f"; failed to load script: {e!r}", "error")
            self.calls.put(lambda: self.render_script(name, found))

        threading.Thread(target=work, daemon=True).start()

    def render_script(self, name, found):
        sel = self.tree.selection()
        if not sel or self.script_of.get(sel[0]) != name:
            return  # selection moved on
        self.text.delete('1.0', 'end')
        self.status.config(text="")
        if found is None:
            self.text.insert('1.0', f"; script {name} not found in the loaded BSAs or Data\\Scripts")
            return
        body, origin = found
        self.text.insert('1.0', f"; {origin}\n{body}")
        for tag, pattern, flags in (
                ('kw', r'\b(?:' + self.KEYWORDS + r')\b', re.I),
                ('type', self.TYPES, re.I),
                ('num', r'\b\d+(?:\.\d+)?\b', 0),
                ('label', r'\bL\d+\b:?', 0),
                ('temp', r'::\w+', 0),
                ('str', r'"(?:[^"\\\n]|\\.)*"', 0),
                ('comment', r';[^\n]*', 0)):
            for m in re.finditer(pattern, f"; {origin}\n{body}", flags):
                self.text.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        self.text.tag_raise('str')
        self.text.tag_raise('comment')

    def expand(self, state, node=''):
        for c in self.tree.get_children(node):
            self.tree.item(c, open=state)
            self.expand(state, c)


class QuestTree:
    MAX_DEPTH = 3

    def __init__(self, app, model, qid):
        self.app, self.m, self.qid = app, model, qid
        self.tree = app.tree
        self.quest = model.get(qid)
        self.seen = set()
        self.npcs, self.places, self.scripts = {}, {}, set()
        self.alias_names = {}

    KIND_ICONS = {'quest': '🎯', 'alias': '🏷️', 'script': '📜', 'npc': '🧑', 'ref': '📍',
                  'location': '🗺️', 'scene': '🎬', 'dialogue': '💬', 'unknown': '❓'}
    SIG_ICONS = {'WEAP': '⚔️', 'ARMO': '🛡️', 'DOOR': '🚪', 'CONT': '📦', 'MISC': '🧰', 'BOOK': '📖',
                 'ALCH': '🧪', 'INGR': '🌿', 'FLOR': '🌸', 'TREE': '🌳', 'LIGH': '💡', 'KEYM': '🔑',
                 'AMMO': '🏹', 'SCRL': '📃', 'SLGM': '💎', 'ACTI': '🔘', 'TACT': '🗣️', 'FURN': '🪑',
                 'STAT': '🧱', 'MSTT': '🪨', 'SPEL': '✨', 'HAZD': '☠️', 'SOUN': '🔊', 'FACT': '🚩',
                 'PACK': '📋', 'APPA': '⚗️', 'IDLM': '🎭', 'ARTO': '🎨', 'LCTN': '🗺️', 'NPC_': '🧑'}
    TEXT_ICONS = (('Scripts', '📚'), ('All scripts', '📚'), ('Aliases', '🏷️'), ('Stages', '📊'),
                  ('stages:', '🔢'), ('objective', '🏁'), ('Scenes', '🎬'), ('Dialogue', '💬'),
                  ('INFO', '🗣️'), ('All NPCs', '👥'), ('All locations', '🧭'), ('fragments', '🧩'),
                  ('alias scripts', '🔗'), ('actors/aliases', '🎭'))

    def icon(self, text, kind, script):
        if script:
            return '📜'
        sig = re.search(r'(?:^|: )([A-Z][A-Z0-9_]{3}) ', text)
        if sig and sig.group(1) in self.SIG_ICONS and kind in ('form', 'npc', 'location', None):
            return self.SIG_ICONS[sig.group(1)]
        if kind == 'form':
            return '📎'
        if kind:
            return self.KIND_ICONS.get(kind, '')
        for start, ic in self.TEXT_ICONS:
            if text.startswith(start):
                return ic
        if ' = ' in text:
            return '🔧'
        if '->' in text:
            return '🔗'
        return '▫️'

    def add(self, parent, text, detail=None, open_=False, script=None, key=None, kind=None, label=None):
        shown = f"{self.icon(text, kind, script)} {text}"
        iid = self.tree.insert(parent, 'end', text=shown, open=open_)
        if detail is not None:
            self.app.details[iid] = detail
        if script:
            self.app.script_of[iid] = script
            if key is None:
                key, kind, label = ('s', script.lower()), 'script', script
        if key is not None:
            app = self.app
            app.g_nodes.setdefault(key, (kind, label or text))
            app.key_of[iid] = key
            app.iid_of.setdefault(key, iid)
            anc = parent
            while anc and anc not in app.key_of:
                anc = self.tree.parent(anc)
            if anc and app.key_of[anc] != key:
                app.g_edges.add((app.key_of[anc], key))
        return iid

    @staticmethod
    def kind_of(sig):
        return {'NPC_': 'npc', 'LCTN': 'location', 'DIAL': 'dialogue', 'SCEN': 'scene',
                'QUST': 'quest'}.get(sig, 'form')

    def raw_dump(self, rec):
        lines = [self.m.label(self.m.rid(rec)), ""]
        for sr in rec.subrecords:
            d = sr.data
            lines.append(f"{sr.signature} ({len(d)}b): {d[:32].hex()}{'...' if len(d) > 32 else ''}")
        return '\n'.join(lines)

    def add_scripts(self, parent, vm, depth, alias_ctx=False):
        if vm is None:
            return
        for s in vm.scripts:
            self.scripts.add(s.name)
            node = self.add(parent, f"script {s.name}", script=s.name)
            for pr in s.properties:
                self.add_property(node, pr, depth)
        if vm.fragment_data is not None and vm.fragment_data.fragments:
            fd = vm.fragment_data
            node = self.add(parent, f"fragments ({len(fd.fragments)}) in {fd.filename}")
            for fr in fd.fragments:
                stage = getattr(fr, 'quest_stage', None)
                tag = f"stage {stage}: " if stage is not None else ""
                self.scripts.add(fr.script_name)
                self.add(node, f"{tag}{fr.script_name}.{fr.fragment_name}", script=fr.script_name)
        if vm.alias_scripts and not alias_ctx:
            node = self.add(parent, f"alias scripts ({len(vm.alias_scripts)})")
            for a in vm.alias_scripts:
                an = self.alias_names.get(a.alias_obj.alias, f"alias {a.alias_obj.alias}")
                sub = self.add(node, an)
                self.add_scripts(sub, VmadDataShim(a.scripts), depth, True)

    def add_property(self, parent, pr, depth):
        vals = pr.value if isinstance(pr.value, list) else [pr.value]
        objs = [v for v in vals if isinstance(v, VmadObject)]
        if not objs:
            shown = pr.value if not isinstance(pr.value, list) else f"[{len(vals)} items]"
            self.add(parent, f"{pr.name} = {shown}")
            return
        node = self.add(parent, f"{pr.name} -> {len(objs)} object(s)" if len(objs) > 1 else pr.name)
        for o in objs:
            if o.form_id:
                self.add_form(node, o.form_id, depth + 1, f"{pr.name}: " if len(objs) == 1 else "")

    def add_form(self, parent, fid, depth, prefix=""):
        if fid in self.m.refs:
            return self.add_ref(parent, fid, depth, prefix)
        rec = self.m.get(fid)
        node = self.add(parent, prefix + self.m.label(fid), self.raw_dump(rec) if rec else None,
                        key=('f', fid), kind=self.kind_of(rec.signature) if rec else 'unknown',
                        label=((rec.editor_id or rec.full_name or f"{fid:08X}") if rec else f"{fid:08X}"))
        if rec is None:
            return node
        if rec.signature == 'NPC_':
            self.npcs[fid] = self.m.label(fid)
        if rec.signature == 'LCTN':
            self.places[fid] = self.m.label(fid)
        if depth < self.MAX_DEPTH and (fid, 'form') not in self.seen:
            self.seen.add((fid, 'form'))
            self.add_scripts(node, self.m.vmad(rec), depth)
        return node

    def add_ref(self, parent, fid, depth, prefix=""):
        place, base, pos = self.m.refs[fid]
        where = f"{place} @ ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})" if pos else place
        node = self.add(parent, f"{prefix}REF {fid:08X} in {where}",
                        f"Reference {fid:08X}\nLocation: {where}\nBase: {self.m.label(base) if base else '?'}",
                        key=('f', fid), kind='ref', label=f"REF {fid:08X} {place}")
        self.places.setdefault(('ref', fid), f"{fid:08X}: {where}")
        if base:
            self.add_form(node, base, depth + 1, "base: ")
        return node

    def parse_aliases(self):
        aliases, cur = [], None
        for sr in self.quest.subrecords:
            s = sr.signature
            if s in ('ALST', 'ALLS'):
                cur = {'id': u32(sr.data), 'name': '', 'items': [], 'kind': s}
                aliases.append(cur)
            elif cur is None:
                continue
            elif s == 'ALID':
                cur['name'] = sr.data.rstrip(b'\0').decode('cp1252', 'replace')
                self.alias_names[cur['id']] = cur['name']
            elif s in ALIAS_FORM_SUBS and len(sr.data) >= 4:
                cur['items'].append((ALIAS_FORM_SUBS[s], self.m.absid(self.quest.plugin, u32(sr.data))))
            elif s == 'ALED':
                cur = None
        return aliases

    def build(self):
        q, m = self.quest, self.m
        root = self.add('', m.label(self.qid), self.raw_dump(q), open_=True,
                        key=('f', self.qid), kind='quest', label=q.editor_id or f"{self.qid:08X}")
        aliases = self.parse_aliases()

        vm = m.vmad(q)
        sn = self.add(root, "Scripts", open_=True)
        self.add_scripts(sn, vm, 0)

        an = self.add(root, f"Aliases ({len(aliases)})", open_=True)
        for a in aliases:
            kind = 'location' if a['kind'] == 'ALLS' else 'reference'
            node = self.add(an, f"[{a['id']}] {a['name']} ({kind} alias)",
                            key=('a', self.qid, a['id']), kind='alias', label=a['name'] or f"alias {a['id']}")
            for label, fid in a['items']:
                if fid:
                    self.add_form(node, fid, 1, f"{label}: ")

        stages = sorted({u32(sr.data) & 0xFFFF for sr in q.subrecords if sr.signature == 'INDX'})
        objectives = []
        for i, sr in enumerate(q.subrecords):
            if sr.signature == 'QOBJ':
                text = None
                for nxt in q.subrecords[i + 1:i + 6]:
                    if nxt.signature == 'NNAM':
                        text = m.string(nxt.data, q)
                        break
                objectives.append((u32(sr.data) & 0xFFFF, text))
        pn = self.add(root, f"Stages ({len(stages)}) / objectives ({len(objectives)})")
        self.add(pn, "stages: " + ', '.join(map(str, stages)))
        for idx, text in objectives:
            self.add(pn, f"objective {idx}: {text or '(no text)'}")

        scenes = m.scenes.get(self.qid, [])
        sc_node = self.add(root, f"Scenes ({len(scenes)})")
        for sc in scenes:
            node = self.add(sc_node, m.label(m.rid(sc)), self.raw_dump(sc),
                            key=('f', m.rid(sc)), kind='scene', label=sc.editor_id or f"{m.rid(sc):08X}")
            self.add_scripts(node, m.vmad(sc), 1)
            ids = [u32(s.data) for s in sc.subrecords if s.signature == 'ALID' and len(s.data) >= 4]
            if ids:
                self.add(node, "actors/aliases: " + ', '.join(self.alias_names.get(i, str(i)) for i in ids))

        topics = m.dialogue.get(self.qid, [])
        info_total = sum(len(i) for _, i in topics)
        dn = self.add(root, f"Dialogue ({len(topics)} topics, {info_total} responses)")
        for dial, infos in topics:
            tn = self.add(dn, f"{m.label(m.rid(dial))} ({len(infos)})", self.raw_dump(dial),
                          key=('f', m.rid(dial)), kind='dialogue',
                          label=dial.editor_id or f"{m.rid(dial):08X}")
            for info in infos:
                resp = None
                for sr in info.subrecords:
                    if sr.signature == 'NAM1':
                        resp = m.string(sr.data, info)
                        break
                inode = self.add(tn, f"INFO [{m.rid(info):08X}] {(resp or '')[:80]}", self.raw_dump(info))
                spk = info.get_subrecord('ANAM')
                if spk and u32(spk.data):
                    self.add_form(inode, m.absid(info.plugin, u32(spk.data)), 2, "speaker: ")
                self.add_scripts(inode, m.vmad(info), 2)

        nn = self.add(root, f"All NPCs involved ({len(self.npcs)})")
        for fid, lab in sorted(self.npcs.items(), key=lambda kv: kv[1]):
            self.add(nn, lab)
        ln = self.add(root, f"All locations / references ({len(self.places)})")
        for lab in sorted(map(str, self.places.values())):
            self.add(ln, lab)
        sl = self.add(root, f"All scripts ({len(self.scripts)})")
        for s in sorted(self.scripts, key=str.lower):
            self.add(sl, s, script=s)


class VmadDataShim:
    """Lets alias script lists reuse add_scripts."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.fragment_data = None
        self.alias_scripts = []


if __name__ == '__main__':
    App(sys.argv[1:] or [DEFAULT_ESM]).mainloop()
