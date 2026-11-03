"""Canvas graph view: radial layout, pan/zoom, type filters, neighbor highlight."""
import math
import tkinter as tk
from collections import defaultdict, deque
from tkinter import ttk

KIND_COLORS = {
    'quest': '#d62728', 'alias': '#ff7f0e', 'script': '#1f77b4', 'npc': '#2ca02c',
    'ref': '#9467bd', 'location': '#8c564b', 'scene': '#e377c2', 'dialogue': '#bcbd22',
    'form': '#7f7f7f', 'unknown': '#cccccc',
}
KIND_NAMES = {
    'quest': 'Quest', 'alias': 'Aliases', 'script': 'Scripts', 'npc': 'NPCs',
    'ref': 'World refs', 'location': 'Locations', 'scene': 'Scenes',
    'dialogue': 'Dialogue topics', 'form': 'Other forms', 'unknown': 'Unresolved',
}
DEFAULT_OFF = {'dialogue'}


class GraphView(ttk.Frame):
    def __init__(self, master, on_select):
        super().__init__(master)
        self.on_select = on_select
        self.nodes, self.edges, self.root = {}, set(), None
        self.pos = {}            # key -> (x, y) in world coordinates
        self.tree_edges = set()
        self.selected = None
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self.item_key = {}
        self.vars = {k: tk.BooleanVar(value=k not in DEFAULT_OFF) for k in KIND_NAMES}

        bar = ttk.Frame(self)
        bar.pack(fill='x')
        for k, name in KIND_NAMES.items():
            if k == 'unknown':
                continue
            tk.Checkbutton(bar, text=name, variable=self.vars[k], command=self.relayout,
                           fg=KIND_COLORS[k], font=('Segoe UI', 9, 'bold')).pack(side='left')
        ttk.Button(bar, text="Fit", command=self.fit).pack(side='right', padx=4)
        self.info = ttk.Label(bar, text="")
        self.info.pack(side='right', padx=8)

        self.canvas = tk.Canvas(self, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        c = self.canvas
        c.bind('<ButtonPress-1>', self._press)
        c.bind('<B1-Motion>', self._drag)
        c.bind('<ButtonRelease-1>', self._release)
        c.bind('<MouseWheel>', self._wheel)
        c.bind('<Button-4>', lambda e: self._zoom(e, 1.15))
        c.bind('<Button-5>', lambda e: self._zoom(e, 1 / 1.15))
        c.bind('<Configure>', lambda e: self.draw())

    # ---- data
    def set_graph(self, nodes, edges, root):
        self.nodes, self.edges, self.root = nodes, set(edges), root
        self.selected = None
        self.relayout(fit=True)

    def visible(self, key):
        kind = self.nodes[key][0]
        return key == self.root or self.vars.get(kind, tk.BooleanVar(value=True)).get()

    # ---- layout
    def relayout(self, fit=False):
        self.pos.clear()
        self.tree_edges = set()
        if self.root not in self.nodes:
            self.draw()
            return
        adj = defaultdict(list)
        for a, b in self.edges:
            if a in self.nodes and b in self.nodes and self.visible(a) and self.visible(b):
                adj[a].append(b)
                adj[b].append(a)
        depth, children, order = {self.root: 0}, defaultdict(list), [self.root]
        dq = deque([self.root])
        while dq:
            n = dq.popleft()
            for m in sorted(adj[n], key=lambda k: self.nodes[k][1]):
                if m not in depth:
                    depth[m] = depth[n] + 1
                    children[n].append(m)
                    self.tree_edges.add((n, m))
                    order.append(m)
                    dq.append(m)
        leaves = {}
        for n in reversed(order):
            leaves[n] = max(1, sum(leaves[c] for c in children[n]))
        per_depth = defaultdict(int)
        for n in order:
            per_depth[depth[n]] += 1
        radius = {0: 0.0}
        for d in range(1, max(per_depth) + 1 if per_depth else 1):
            radius[d] = max(radius[d - 1] + 110, per_depth[d] * 26 / (2 * math.pi))
        span = {self.root: (0.0, 2 * math.pi)}
        for n in order:
            a0, a1 = span[n]
            self.pos[n] = (0.0, 0.0) if n == self.root else self._polar(radius[depth[n]], (a0 + a1) / 2)
            total, cur = leaves[n], a0
            for c in children[n]:
                w = (a1 - a0) * leaves[c] / total
                span[c] = (cur, cur + w)
                cur += w
        self.info.config(text=f"{len(self.pos)} nodes, "
                              f"{sum(1 for a, b in self.edges if a in self.pos and b in self.pos)} edges")
        if fit:
            self.fit()
        else:
            self.draw()

    @staticmethod
    def _polar(r, a):
        return r * math.cos(a), r * math.sin(a)

    def fit(self):
        if not self.pos:
            return self.draw()
        xs = [p[0] for p in self.pos.values()]
        ys = [p[1] for p in self.pos.values()]
        w = max(self.canvas.winfo_width(), 200)
        h = max(self.canvas.winfo_height(), 200)
        sx = (w - 80) / max(max(xs) - min(xs), 1)
        sy = (h - 80) / max(max(ys) - min(ys), 1)
        self.scale = min(sx, sy, 2.0)
        self.ox = w / 2 - (max(xs) + min(xs)) / 2 * self.scale
        self.oy = h / 2 - (max(ys) + min(ys)) / 2 * self.scale
        self.draw()

    # ---- drawing
    def _xy(self, key):
        x, y = self.pos[key]
        return x * self.scale + self.ox, y * self.scale + self.oy

    def draw(self):
        c = self.canvas
        c.delete('all')
        self.item_key.clear()
        if not self.pos:
            return
        neigh = set()
        if self.selected in self.pos:
            for a, b in self.edges:
                if a == self.selected:
                    neigh.add(b)
                elif b == self.selected:
                    neigh.add(a)
        for a, b in self.edges:
            if a in self.pos and b in self.pos:
                x1, y1 = self._xy(a)
                x2, y2 = self._xy(b)
                hot = self.selected in (a, b)
                if hot:
                    c.create_line(x1, y1, x2, y2, fill='#e00000', width=2)
                elif (a, b) in self.tree_edges or (b, a) in self.tree_edges:
                    c.create_line(x1, y1, x2, y2, fill='#bbbbbb')
                else:
                    c.create_line(x1, y1, x2, y2, fill='#dddddd', dash=(3, 3))
        label_all = self.scale >= 0.6
        for key in self.pos:
            kind, label = self.nodes[key]
            x, y = self._xy(key)
            r = 9 if key == self.root else 6
            outline = '#e00000' if key == self.selected else ('#000000' if key in neigh else 'white')
            item = c.create_oval(x - r, y - r, x + r, y + r, fill=KIND_COLORS.get(kind, '#999'),
                                 outline=outline, width=3 if outline != 'white' else 1)
            self.item_key[item] = key
            if label_all or key == self.root or key == self.selected or key in neigh:
                t = c.create_text(x + r + 3, y, text=label[:34], anchor='w', font=('Segoe UI', 8))
                self.item_key[t] = key

    # ---- interaction
    def _press(self, e):
        self._down = (e.x, e.y)
        self.canvas.scan_mark(e.x, e.y)
        self._ox0, self._oy0 = self.ox, self.oy

    def _drag(self, e):
        self.ox = self._ox0 + e.x - self._down[0]
        self.oy = self._oy0 + e.y - self._down[1]
        self.draw()

    def _release(self, e):
        if abs(e.x - self._down[0]) + abs(e.y - self._down[1]) > 4:
            return
        hit = [i for i in self.canvas.find_overlapping(e.x - 3, e.y - 3, e.x + 3, e.y + 3)
               if i in self.item_key]
        hit.sort(key=lambda i: self.canvas.type(i) == 'oval')  # circles win over labels
        ovals = [i for i in hit if self.canvas.type(i) == 'oval']
        if ovals:
            def dist(i):
                x1, y1, x2, y2 = self.canvas.coords(i)
                return (e.x - (x1 + x2) / 2) ** 2 + (e.y - (y1 + y2) / 2) ** 2
            hit = [min(ovals, key=dist)]
        if hit:
            self.selected = self.item_key[hit[-1]]
            self.draw()
            self.on_select(self.selected)

    def _wheel(self, e):
        self._zoom(e, 1.15 if e.delta > 0 else 1 / 1.15)

    def _zoom(self, e, f):
        new = min(max(self.scale * f, 0.05), 6.0)
        f = new / self.scale
        self.ox = e.x - (e.x - self.ox) * f
        self.oy = e.y - (e.y - self.oy) * f
        self.scale = new
        self.draw()

    def highlight(self, key):
        """Select a node from outside (e.g. tree selection) without calling back."""
        if key in self.pos and key != self.selected:
            self.selected = key
            self.draw()
