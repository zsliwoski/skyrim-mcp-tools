"""Parse compiled Papyrus (.pex) files and render them as readable pseudo-source."""
import re
import struct
from pathlib import Path

import threading
from esplib.bsa import BsaReader

OPS = {
    # name: (fixed arg count, kind)
    0: ('nop', 0), 1: ('iadd', 3), 2: ('fadd', 3), 3: ('isub', 3), 4: ('fsub', 3),
    5: ('imul', 3), 6: ('fmul', 3), 7: ('idiv', 3), 8: ('fdiv', 3), 9: ('imod', 3),
    10: ('not', 2), 11: ('ineg', 2), 12: ('fneg', 2), 13: ('assign', 2), 14: ('cast', 2),
    15: ('cmp_eq', 3), 16: ('cmp_lt', 3), 17: ('cmp_le', 3), 18: ('cmp_gt', 3), 19: ('cmp_ge', 3),
    20: ('jmp', 1), 21: ('jmpt', 2), 22: ('jmpf', 2),
    23: ('callmethod', 3), 24: ('callparent', 2), 25: ('callstatic', 3),
    26: ('return', 1), 27: ('strcat', 3), 28: ('propget', 3), 29: ('propset', 3),
    30: ('array_create', 2), 31: ('array_length', 2), 32: ('array_get', 3),
    33: ('array_set', 3), 34: ('array_find', 4), 35: ('array_rfind', 4),
}
BINOPS = {'iadd': '+', 'fadd': '+', 'isub': '-', 'fsub': '-', 'imul': '*', 'fmul': '*',
          'idiv': '/', 'fdiv': '/', 'imod': '%', 'strcat': '+', 'cmp_eq': '==',
          'cmp_lt': '<', 'cmp_le': '<=', 'cmp_gt': '>', 'cmp_ge': '>='}


class Reader:
    """Cursor over PEX bytes; PEX is big-endian, unlike the rest of the Skyrim formats."""
    def __init__(self, data):
        self.d, self.p = data, 0

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def _unpack(self, fmt, n):
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += n
        return v

    def u16(self): return self._unpack('>H', 2)
    def u32(self): return self._unpack('>I', 4)
    def i32(self): return self._unpack('>i', 4)
    def u64(self): return self._unpack('>Q', 8)
    def f32(self): return self._unpack('>f', 4)

    def string(self):
        """Inline string: u16 length then cp1252 bytes (not a string-table index)."""
        n = self.u16()
        s = self.d[self.p:self.p + n].decode('cp1252', 'replace')
        self.p += n
        return s


class Pex:
    """Bare attribute holder for the parsed file, objects, properties and functions."""
    pass


def parse(data):
    """Parse PEX bytes into a Pex with strings, line tables and objects (variables, properties, states)."""
    r = Reader(data)
    if r.u32() != 0xFA57C0DE:
        raise ValueError("not a PEX file")
    x = Pex()
    r.u8(); r.u8(); r.u16()
    x.compiled = r.u64()
    x.source, x.user, x.computer = r.string(), r.string(), r.string()
    S = [r.string() for _ in range(r.u16())]
    x.strings = S
    x.lines = {}
    if r.u8():
        r.u64()
        for _ in range(r.u16()):
            obj, state, fn, _t = S[r.u16()], S[r.u16()], S[r.u16()], r.u8()
            x.lines[(obj, state, fn)] = [r.u16() for _ in range(r.u16())]
    for _ in range(r.u16()):
        r.u16(); r.u8()

    def variant():
        t = r.u8()
        if t == 0:
            return ('none', None)
        if t in (1, 2):
            return ('id' if t == 1 else 'str', S[r.u16()])
        if t == 3:
            return ('int', r.i32())
        if t == 4:
            return ('float', r.f32())
        if t == 5:
            return ('bool', bool(r.u8()))
        raise ValueError(f"bad variant {t}")

    def function():
        f = Pex()
        f.ret, f.doc, f.uflags, f.flags = S[r.u16()], S[r.u16()], r.u32(), r.u8()
        f.params = [(S[r.u16()], S[r.u16()]) for _ in range(r.u16())]
        f.locals = [(S[r.u16()], S[r.u16()]) for _ in range(r.u16())]
        f.code = []
        for _ in range(r.u16()):
            op = r.u8()
            name, n = OPS[op]
            args = [variant() for _ in range(n)]
            extra = []
            if op in (23, 24, 25):
                cnt = variant()[1]
                extra = [variant() for _ in range(cnt)]
            f.code.append((name, args, extra))
        return f

    x.objects = []
    for _ in range(r.u16()):
        o = Pex()
        o.name = S[r.u16()]
        r.u32()
        o.parent, o.doc, o.uflags, o.auto_state = S[r.u16()], S[r.u16()], r.u32(), S[r.u16()]
        o.variables = []
        for _ in range(r.u16()):
            o.variables.append((S[r.u16()], S[r.u16()], r.u32(), variant()))
        o.properties = []
        for _ in range(r.u16()):
            p = Pex()
            p.name, p.type, p.doc, p.uflags, p.flags = S[r.u16()], S[r.u16()], S[r.u16()], r.u32(), r.u8()
            p.auto_var = p.get = p.set = None
            if p.flags & 4:
                p.auto_var = S[r.u16()]
            else:
                if p.flags & 1:
                    p.get = function()
                if p.flags & 2:
                    p.set = function()
            o.properties.append(p)
        o.states = []
        for _ in range(r.u16()):
            sname = S[r.u16()]
            fns = [(S[r.u16()], function()) for _ in range(r.u16())]
            o.states.append((sname, fns))
        x.objects.append(o)
    return x


def val(v):
    """Format a (kind, value) variant as Papyrus source text; identifiers are left unquoted."""
    kind, x = v
    if kind == 'none':
        return 'None'
    if kind == 'str':
        return '"' + x.replace('"', '\\"') + '"'
    if kind == 'bool':
        return 'True' if x else 'False'
    if kind == 'float':
        return f"{x:g}"
    return str(x)


def render_function(fn, name, obj, state, lines, ind, is_prop=None):
    """Lines of pseudo-source for one function: header, locals, jump-labelled statements, End marker."""
    out = []
    head_kind = 'Function'
    if not (fn.flags & 1) and name.lower().startswith('on') and fn.ret.lower() == 'none':
        head_kind = 'Event'
    params = ', '.join(f"{t} {n}" for n, t in fn.params)
    ret = '' if fn.ret.lower() == 'none' else f"{fn.ret} "
    flags = (' Global' if fn.flags & 1 else '') + (' Native' if fn.flags & 2 else '')
    out.append(f"{ind}{ret}{head_kind} {name}({params}){flags}" if head_kind == 'Function' or ret
               else f"{ind}Event {name}({params}){flags}")
    if fn.doc:
        out.append(f"{ind}    ; {fn.doc}")
    if fn.flags & 2 or not fn.code:
        return out
    temps = [(n, t) for n, t in fn.locals]
    if temps:
        out.append(f"{ind}    ; locals: " + ', '.join(f"{t} {n}" for n, t in temps))
    targets = set()
    for i, (op, a, _e) in enumerate(fn.code):
        if op == 'jmp':
            targets.add(i + a[0][1])
        elif op in ('jmpt', 'jmpf'):
            targets.add(i + a[1][1])
    ln = lines.get((obj, state, name))
    for i, (op, a, extra) in enumerate(fn.code):
        if i in targets:
            out.append(f"{ind}  L{i}:")
        s = stmt(op, a, extra, i)
        if s:
            tag = f"   ; line {ln[i]}" if ln and i < len(ln) and ln[i] else ""
            out.append(f"{ind}    {s}{tag}")
    if len(fn.code) in targets:
        out.append(f"{ind}  L{len(fn.code)}:")
    out.append(f"{ind}End{head_kind}")
    return out


def stmt(op, a, extra, i):
    """One instruction as a pseudo-source line, or None for nop; i is its index, which jump offsets are relative to."""
    v = [val(x) for x in a]

    def dest(d, rhs):
        return rhs if d[1] == '::NoneVar' else f"{val(d)} = {rhs}"
    args = ', '.join(val(x) for x in extra)
    if op == 'nop':
        return None
    if op in BINOPS:
        return f"{v[0]} = {v[1]} {BINOPS[op]} {v[2]}"
    if op == 'not':
        return f"{v[0]} = !{v[1]}"
    if op in ('ineg', 'fneg'):
        return f"{v[0]} = -{v[1]}"
    if op == 'assign':
        return f"{v[0]} = {v[1]}"
    if op == 'cast':
        return f"{v[0]} = {v[1]} as <cast>"
    if op == 'jmp':
        return f"goto L{i + a[0][1]}"
    if op == 'jmpt':
        return f"if {v[0]} goto L{i + a[1][1]}"
    if op == 'jmpf':
        return f"if !{v[0]} goto L{i + a[1][1]}"
    if op == 'callmethod':
        return dest(a[2], f"{v[1].strip(chr(34))}.{a[0][1]}({args})")
    if op == 'callparent':
        return dest(a[1], f"Parent.{a[0][1]}({args})")
    if op == 'callstatic':
        return dest(a[2], f"{a[0][1]}.{a[1][1]}({args})")
    if op == 'return':
        return 'return' if a[0][0] == 'none' else f"return {v[0]}"
    if op == 'propget':
        return f"{v[2]} = {a[1][1]}.{a[0][1]}"
    if op == 'propset':
        return f"{a[1][1]}.{a[0][1]} = {v[2]}"
    if op == 'array_create':
        return f"{v[0]} = new array[{v[1]}]"
    if op == 'array_length':
        return f"{v[0]} = {v[1]}.Length"
    if op == 'array_get':
        return f"{v[1]} = {a[0][1]}[{v[2]}]"
    if op == 'array_set':
        return f"{a[0][1]}[{v[1]}] = {v[2]}"
    if op in ('array_find', 'array_rfind'):
        fn = 'Find' if op == 'array_find' else 'RFind'
        return f"{v[1]} = {a[0][1]}.{fn}({v[2]}, {v[3]})"
    return f"{op} {', '.join(v)}"


def render(data):
    """Pseudo-source text for a whole script; accepts raw PEX bytes or an already parsed Pex."""
    x = data if isinstance(data, Pex) else parse(data)
    out = []
    for o in x.objects:
        out.append(f"; Decompiled from {x.source or 'compiled script'} (compiled by {x.user or '?'})")
        out.append("; Pseudo-source: statements are shown as bytecode steps with labels for jumps.")
        out.append(f"ScriptName {o.name}" + (f" extends {o.parent}" if o.parent else ""))
        out.append("")
        if o.variables:
            out.append("; ---- Variables")
            for n, t, _f, init in o.variables:
                out.append(f"{t} {n}" + (f" = {val(init)}" if init[0] != 'none' else ""))
            out.append("")
        if o.properties:
            out.append("; ---- Properties")
            for p in o.properties:
                if p.auto_var is not None:
                    out.append(f"{p.type} Property {p.name} Auto")
                else:
                    out.append(f"{p.type} Property {p.name}")
                    for label, fn in (('Get', p.get), ('Set', p.set)):
                        if fn is not None:
                            out.extend(render_function(fn, label, o.name, '', x.lines, "    ")
                                       if fn.code else [f"    {label}"])
                    out.append("EndProperty")
            out.append("")
        for sname, fns in o.states:
            is_state = bool(sname)
            if is_state:
                out.append(f"{'Auto ' if sname == o.auto_state else ''}State {sname}")
            for name, fn in fns:
                out.extend(render_function(fn, name, o.name, sname, x.lines, "  " if is_state else ""))
                out.append("")
            if is_state:
                out.append("EndState")
                out.append("")
    return '\n'.join(out)


ASSET_BSA = re.compile(
    r'(textures|meshes|sounds|voices\w*|animations|interface|shaders|lodtextures)\d*\.bsa$', re.I)


def script_bsas(plugin_paths, extra=()):
    """Misc.bsa, then BSAs named after each plugin (load order), then user-added ones."""
    out, seen = [], set()

    def add(b):
        k = Path(b).name.lower()
        if k not in seen:
            seen.add(k)
            out.append(str(b))

    add("Skyrim - Misc.bsa")
    for p in plugin_paths:
        d, stem = Path(p).parent, Path(p).stem
        for b in sorted(d.glob(f"{stem}*.bsa")):
            if (b.stem.lower() == stem.lower() or b.stem.lower().startswith(stem.lower() + ' - ')) \
                    and not ASSET_BSA.search(b.name):
                add(b)
    for b in extra:
        add(b)
    return out


class ScriptStore:
    """Finds scripts: loose .psc source first, then compiled .pex (loose, then BSA).
    Later BSAs override earlier ones; BSAs are indexed up front and read on demand."""

    def __init__(self, data_dir, bsas=("Skyrim - Misc.bsa",)):
        self.data = Path(data_dir)
        self.bsas = list(bsas)
        self.pex = None
        self.psc = {}
        self.readers = {}
        self.lock = threading.Lock()

    def add_bsas(self, bsas):
        """Append BSAs not already listed and drop the index so the next lookup rebuilds it."""
        self.bsas += [b for b in bsas if str(b) not in map(str, self.bsas)]
        self.pex = None

    def _load(self):
        self.pex = {}
        self.psc = {}
        for bsa in self.bsas:
            path = self.data / bsa  # absolute paths win over the data dir
            if not path.exists():
                continue
            key = str(path).lower()
            if key not in self.readers:
                try:
                    self.readers[key] = BsaReader(path).__enter__()
                except Exception:
                    continue
            for f in self.readers[key].list_files():
                if f.lower().endswith('.pex'):
                    self.pex[Path(f).stem.lower()] = ('bsa', key, f)
        for p in (self.data / 'Scripts').glob('*.pex'):
            self.pex[p.stem.lower()] = ('loose', p, None)
        for p in self.data.rglob('*.psc'):
            self.psc[p.stem.lower()] = p

    def get_pex(self, name):
        """Return (parsed Pex, origin) for the compiled script, or None."""
        with self.lock:
            if self.pex is None:
                self._load()
            hit = self.pex.get(name.lower())
            if hit is None:
                return None
            kind, src, f = hit
            data = self.readers[src].read_file(f) if kind == 'bsa' else src.read_bytes()
            return parse(data), Path(src).name

    def get(self, name):
        """Script text and origin for a name, locked for thread safety."""
        with self.lock:
            return self._get(name)

    def _get(self, name):
        """Return (text, origin description) or None."""
        if self.pex is None:
            self._load()
        key = name.lower()
        if key in self.psc:
            return self.psc[key].read_text(encoding='cp1252', errors='replace'), f"source: {self.psc[key]}"
        if key in self.pex:
            kind, src, f = self.pex[key]
            data = self.readers[src].read_file(f) if kind == 'bsa' else src.read_bytes()
            origin = f"decompiled from compiled script ({Path(src).name})"
            return render(data), origin
        return None
