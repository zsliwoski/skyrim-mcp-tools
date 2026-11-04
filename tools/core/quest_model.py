"""Quest data model shared by the MCP tools and the GUI: loads plugins and indexes quests, scripts, NPCs, references, dialogue and scenes."""
import os
import pickle
import struct
from collections import defaultdict
from pathlib import Path

from esplib import Plugin
from esplib.vmad import VmadData, VmadObject
DEFAULT_ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm"
CACHE_DIR = Path(os.environ.get('ESPLIB_CACHE_DIR') or Path.home() / '.cache' / 'esplib')

# Record types loaded so form IDs can be resolved to names.
LOAD_SIGS = {
    'QUST', 'DIAL', 'SCEN', 'NPC_', 'LCTN', 'FACT', 'PACK',
    'STAT', 'MSTT', 'DOOR', 'FURN', 'LIGH', 'CONT', 'MISC', 'WEAP', 'ARMO',
    'ACTI', 'ALCH', 'BOOK', 'FLOR', 'TREE', 'INGR', 'KEYM', 'AMMO', 'SCRL',
    'SLGM', 'APPA', 'SOUN', 'TACT', 'SPEL', 'HAZD', 'IDLM', 'ARTO',
    'RACE', 'CLAS', 'VTYP', 'PERK', 'KYWD', 'OTFT', 'CLFM', 'CSTY', 'LVLI', 'LVLN', 'LVSP', 'SHOU', 'FLST',
    'HDPT', 'ENCH', 'MGEF', 'GLOB', 'MESG', 'EQUP', 'ARMA', 'EFSH',
}
REF_SIGS = {'REFR', 'ACHR', 'PGRE', 'PHZD', 'PMIS', 'PARW', 'PBEA', 'PCON', 'PFLA'}
ALIAS_FORM_SUBS = {
    'ALFR': 'Forced reference', 'ALUA': 'Unique actor', 'ALFL': 'Forced location',
    'ALCO': 'Created object', 'ALEQ': 'External alias quest', 'ALFE': 'From event',
    'VTCK': 'Voice type', 'ALFA': 'Fill alias', 'KNAM': 'Keyword', 'ALRT': 'Location ref type',
    'ALCA': 'Created actor', 'ALSP': 'Spell', 'ALFC': 'Faction',
}


def u32(data, off=0):
    return struct.unpack_from('<I', data, off)[0] if len(data) >= off + 4 else 0


def walk_ctx(group, ctx, visit):
    """Walk a group tree, tracking the most recent record of CELL/WRLD/DIAL."""
    for item in group.records:
        if hasattr(item, 'records'):
            walk_ctx(item, ctx, visit)
        else:
            if item.signature in ('CELL', 'WRLD', 'DIAL'):
                ctx[item.signature] = item
            visit(item, ctx)


def build_ref_index(path, wanted):
    """{ref form id: (place, base form id, (x, y, z))} for the wanted refs."""
    p = Plugin.load(path, only_signatures={'CELL', 'WRLD'})
    out = {}

    def visit(rec, ctx):
        if rec.signature == 'WRLD':
            ctx['world'] = rec.editor_id
            return
        if rec.signature == 'CELL':
            data = rec.get_subrecord('DATA')
            if data is not None and data.data and data.data[0] & 1:
                ctx['world'] = None
            name = rec.full_name or rec.editor_id or f"{rec.form_id.value:08X}"
            grid = rec.get_subrecord('XCLC')
            if grid is not None and len(grid.data) >= 8:
                gx, gy = struct.unpack_from('<ii', grid.data)
                name = f"{name} ({gx},{gy})" if rec.full_name or rec.editor_id else f"({gx},{gy})"
            ctx['place'] = name
            return
        if rec.signature in REF_SIGS and rec.form_id.value in wanted:
            nm, dt = rec.get_subrecord('NAME'), rec.get_subrecord('DATA')
            pos = struct.unpack_from('<3f', dt.data) if dt is not None and len(dt.data) >= 12 else None
            place = ctx.get('place') or '?'
            if ctx.get('world'):
                place = f"{ctx['world']} / {place}"
            out[rec.form_id.value] = (place, u32(nm.data) if nm else 0, pos)

    ctx = {}
    for g in p.groups:
        walk_ctx(g, ctx, visit)
    return out


def load_ref_index(path, wanted):
    """build_ref_index behind a pickle cache keyed on file size, mtime and the wanted set."""
    st = os.stat(path)
    stamp = (st.st_size, st.st_mtime_ns, len(wanted), sum(wanted) & 0xFFFFFFFF)
    cache = CACHE_DIR / f"refindex-{Path(path).stem}.pkl"
    try:
        with open(cache, 'rb') as f:
            s, idx = pickle.load(f)
        if s == stamp:
            return idx
    except Exception:
        pass
    idx = build_ref_index(path, wanted)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache, 'wb') as f:
            pickle.dump((stamp, idx), f)
    except Exception:
        pass
    return idx


def vmad_objects(vm):
    """Yield every object-typed script property value in a VMAD, including ones inside arrays and alias scripts."""
    def scan(v):
        if isinstance(v, VmadObject):
            yield v
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from scan(x)

    if vm is None:
        return
    for s in vm.scripts:
        for pr in s.properties:
            yield from scan(pr.value)
    for a in vm.alias_scripts:
        for s in a.scripts:
            for pr in s.properties:
                yield from scan(pr.value)


class Model:
    """Everything loaded from one or more plugins (masters first), merged so
    form IDs are 'absolute': (position in self.plugins << 24) | object id.
    Later plugins override earlier ones."""

    def __init__(self, paths, progress, resolve_refs=True):
        self.paths = [Path(p) for p in paths]
        self.plugins, self.slots, loaded = [], {}, set()

        def load(path):
            path = Path(path)
            if path.name.lower() in loaded:
                return
            loaded.add(path.name.lower())
            progress(f"Loading {path.name} (first run is slow, then cached)...")
            pl = Plugin.load_cached(str(path), only_signatures=LOAD_SIGS)
            for master in pl.header.masters:
                mp = path.parent / master
                if mp.exists():
                    load(mp)
            self.slots[path.name.lower()] = len(self.plugins)
            self.plugins.append(pl)

        for p in self.paths:
            load(p)

        self.records = {}
        self.by_sig = defaultdict(dict)
        for pl in self.plugins:
            for rec in pl.records:
                fid = self.rid(rec)
                self.records[fid] = rec
                self.by_sig[rec.signature][fid] = rec
        self.quests = sorted(self.by_sig['QUST'].values(),
                             key=lambda r: (r.editor_id or '').lower())

        progress("Indexing dialogue and scenes...")
        self.dialogue = defaultdict(list)  # quest id -> [(DIAL, [INFO])]
        topics, infos = {}, {}

        def make_visit(pl):
            def visit(rec, ctx):
                if rec.signature == 'DIAL':
                    fid = self.rid(rec)
                    if fid not in topics:
                        topics[fid] = (rec, [])
                        q = rec.get_subrecord('QNAM')
                        if q:
                            self.dialogue[self.absid(pl, u32(q.data))].append(topics[fid])
                elif rec.signature == 'INFO' and ctx.get('DIAL') is not None:
                    lst = topics[self.rid(ctx['DIAL'])][1]
                    key = (self.rid(ctx['DIAL']), self.rid(rec))
                    if key in infos:
                        lst[infos[key]] = rec
                    else:
                        infos[key] = len(lst)
                        lst.append(rec)
            return visit

        for pl in self.plugins:
            for g in pl.groups:
                walk_ctx(g, {}, make_visit(pl))
        scenes = defaultdict(dict)
        for fid, sc in self.by_sig['SCEN'].items():
            q = sc.get_subrecord('PNAM')
            if q:
                scenes[self.absid(sc.plugin, u32(q.data))][fid] = sc
        self.scenes = {q: list(d.values()) for q, d in scenes.items()}

        self.refs = {}
        if not resolve_refs:
            return
        progress("Resolving world references...")
        wanted = set()
        for q in self.quests:
            for sr in q.subrecords:
                if sr.signature == 'ALFR':
                    wanted.add(self.absid(q.plugin, u32(sr.data)))
            wanted.update(v.form_id for v in vmad_objects(self.vmad(q)))
        wanted.discard(0)
        for i, pl in enumerate(self.plugins):
            own = len(pl.header.masters) << 24
            local = {own | (a & 0xFFFFFF) for a in wanted if a >> 24 == i}
            if not local:
                continue
            for k, (place, base, pos) in load_ref_index(str(pl.file_path), local).items():
                self.refs[self.absid(pl, k)] = (place, self.absid(pl, base), pos)

    def absid(self, plugin, raw):
        """Raw form ID as stored in `plugin` -> absolute ID (load slot << 24 | object id); 0 stays 0."""
        if not raw:
            return 0
        masters = plugin.header.masters
        idx = raw >> 24
        name = (masters[idx] if idx < len(masters) else plugin.file_path.name).lower()
        slot = self.slots.setdefault(name, len(self.slots))
        return (slot << 24) | (raw & 0xFFFFFF)

    def rid(self, rec):
        """Absolute form ID of a record."""
        return self.absid(rec.plugin, rec.form_id.value)

    def get(self, fid):
        return self.records.get(fid)

    def vmad(self, rec):
        """Parsed VMAD of a record with object form IDs made absolute, or None if absent or unparseable."""
        try:
            vm = VmadData.from_record(rec)
        except Exception:
            return None
        for v in vmad_objects(vm):
            v.form_id = self.absid(rec.plugin, v.form_id)
        return vm

    def label(self, fid):
        """Display text for a form ID: 'SIG editor_id "name" [id]', REF for known world refs, or 'not loaded'."""
        rec = self.get(fid)
        if rec is None:
            if fid in self.refs:
                return f"REF {fid:08X}"
            return f"{fid:08X} (not loaded)"
        name = rec.full_name if rec.signature != 'INFO' else None
        parts = [rec.signature, rec.editor_id or '', f'"{name}"' if name else '', f"[{fid:08X}]"]
        return ' '.join(x for x in parts if x)

    def string(self, data, rec):
        """Text of a string subrecord: inline for non-localized plugins, else a string-table ID (0 means none)."""
        if not getattr(rec.plugin, 'is_localized', True):
            return data.rstrip(b'\0').decode('cp1252', 'replace') or None
        sid = u32(data)
        s = rec.plugin.resolve_string(sid) if sid else None
        return s.rstrip('\0') if s else s
