"""Find the meshes and textures a record uses and copy them into a plugin's own folders so they can be edited alone.

Duplicating a model record copies its NIF to `Meshes\\<dest>\\`, copies every texture the NIF names to
`Textures\\<dest>\\`, rewrites the texture paths inside the copied NIF, and points a (cloned) record at the copy.
Texture-set records (TXST) get the same treatment for their TX00-TX07 paths.
"""
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.core import nif_tools  # noqa: E402
from tools.core.quest_model import Model  # noqa: E402

MODEL_SIGS = ('MODL', 'MOD2', 'MOD3', 'MOD4', 'MOD5')
MODEL_HASH_SIGS = ('MODT', 'MO2T', 'MO3T', 'MO4T', 'MO5T')
TEMPLATE_SIGS = {'WEAP': 'CNAM', 'ARMO': 'TNAM'}  # a template record's model overrides the record's own
TEXTURE_SIGS = tuple(f'TX0{i}' for i in range(8))
NAME_RE = re.compile(r'[A-Za-z0-9_]+')


@dataclass
class Asset:
    record: object
    sig: str
    edid: str
    fid: int
    owned: bool  # defined or overridden by the opened plugin
    models: list = field(default_factory=list)  # [(subrecord signature, path relative to Meshes)]
    textures: list = field(default_factory=list)  # TXST: [(subrecord signature, path relative to Textures)]


def zstr(data):
    return bytes(data).split(b'\0')[0].decode('cp1252', 'replace')


def norm(path):
    return path.replace('/', '\\').lstrip('\\').lower()


def strip_dir(path, kind):
    path = norm(path)
    return path[len(kind) + 1:] if path.startswith(kind + '\\') else path


def load_assets(plugin_path, progress=lambda s: None, master_dirs=()):
    """(Model, [Asset]) for every record with a model path or texture set in the loaded plugins."""
    model = Model([plugin_path], progress, resolve_refs=False, master_dirs=master_dirs)
    own = Path(plugin_path).name.lower()
    out = []
    for fid, rec in model.records.items():
        models = [(s.signature, zstr(s.data)) for s in rec.subrecords
                  if s.signature in MODEL_SIGS and zstr(s.data).lower().endswith('.nif')]
        textures = [(s.signature, zstr(s.data)) for s in rec.subrecords if s.signature in TEXTURE_SIGS and s.data] \
            if rec.signature == 'TXST' else []
        if models or textures:
            out.append(Asset(rec, rec.signature, rec.editor_id or '', fid,
                             rec.plugin.file_path.name.lower() == own, models, textures))
    out.sort(key=lambda a: (not a.owned, a.sig, a.edid.lower()))
    return model, out


def archive_index(dirs, plugin_name):
    """{lowercase 'meshes\\...' / 'textures\\...' path: archive path}; archives named after the plugin win."""
    from esplib.bsa import BsaReader
    stem = Path(plugin_name).stem.lower()
    found = [b for d in dirs for b in Path(d).glob('*.bsa')]
    found.sort(key=lambda b: (not b.stem.lower().startswith(stem), b.name.lower()))
    index = {}
    for b in found:
        try:
            with BsaReader(str(b)) as reader:
                for name in reader.list_files():
                    low = name.lower().replace('/', '\\')
                    if low.startswith(('meshes\\', 'textures\\')):
                        index.setdefault(low, str(b))
        except Exception:
            continue
    return index


def read_asset(kind, rel, dirs, archives):
    """(bytes, origin) of a mesh or texture from loose files or an archive; (None, '') when it can't be found."""
    rel = strip_dir(rel, kind)
    for d in dirs:
        f = Path(d) / kind / rel
        if f.is_file():
            return f.read_bytes(), str(f)
    bsa = (archives or {}).get(f'{kind}\\{rel}')
    if bsa:
        from esplib.bsa import BsaReader
        with BsaReader(bsa) as reader:
            return reader.read_file(f'{kind}\\{rel}'), f'{Path(bsa).name} :: {kind}\\{rel}'
    return None, ''


def nif_textures(data):
    """Texture paths a NIF names, without duplicates, in order."""
    seen = {}
    for t in nif_tools.texture_paths(nif_tools.parse(data)):
        seen.setdefault(norm(t), t)
    return list(seen.values())


def _unique(name, taken):
    stem, ext = Path(name).stem, Path(name).suffix
    cand, n = name, 2
    while cand.lower() in taken:
        cand, n = f'{stem}_{n}{ext}', n + 1
    taken.add(cand.lower())
    return cand


def _copy_texture(old, dest, plugin_dir, dirs, archives, taken, done, warnings):
    """Copy one texture into Textures\\<dest>; returns its new path relative to Textures, or None if missing."""
    key = strip_dir(old, 'textures')
    if key in done:
        return done[key]
    data, _ = read_asset('textures', key, dirs, archives)
    if data is None:
        warnings.append(f"texture not found, path left unchanged: {old}")
        done[key] = None
        return None
    name = _unique(key.rsplit('\\', 1)[-1], taken)
    out = Path(plugin_dir) / 'Textures' / dest / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    done[key] = f'{dest}\\{name}'
    return done[key]


def duplicate_asset(model, plugin_path, asset, dest, editor_id=None, dirs=(), archives=None, data_dir=None, mode=None,
                    model_name=None):
    """Copy an asset's meshes/textures into the plugin folder under `dest` and point a record at the copies.

    The first mesh is saved as Meshes\\<dest>\\<model_name>.nif (default: its original name) and the textures it uses
    go to Textures\\<dest>\\<that name>\\. Other meshes keep their names.
    mode 'clone' clones the record under `editor_id`; 'repoint' edits the record itself (a master's record becomes an
    override in the plugin); 'files' only copies the files and leaves every record alone. By default it is 'clone'
    when an editor ID is given and 'repoint' otherwise. Returns {'record', 'files', 'warnings'}.
    """
    from esplib import Plugin
    from esplib.record import Record
    from esplib.utils import FormID
    from tools.npcs.npc_builder import NpcBuilder, z
    from tools.records.clone import clone_record
    from tools.records.maintenance import snapshot

    plugin_path = Path(plugin_path)
    if not NAME_RE.fullmatch(dest or ''):
        raise ValueError("folder name may contain only letters, digits and underscores")
    if model_name and not NAME_RE.fullmatch(model_name):
        raise ValueError("model name may contain only letters, digits and underscores")
    mode = mode or ('clone' if editor_id else 'repoint')
    if mode not in ('clone', 'repoint', 'files'):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == 'clone':
        if not editor_id or not NAME_RE.fullmatch(editor_id):
            raise ValueError("editor ID may contain only letters, digits and underscores")
        if any((r.editor_id or '').lower() == editor_id.lower() for r in model.records.values()):
            raise ValueError(f"editor ID {editor_id!r} already exists")
    if not (asset.models or asset.textures):
        raise ValueError("this record has no model or texture paths")
    plugin_dir = plugin_path.parent
    warnings, files = [], []
    nif_names, tex_names, done = set(), set(), {}
    new_paths = {}  # (subrecord signature) -> new relative path

    for sig, rel in asset.models:
        data, origin = read_asset('meshes', rel, dirs, archives)
        if data is None:
            raise ValueError(f"mesh not found: {rel}")
        nif = nif_tools.parse(data)
        mapping = {}
        orig = norm(rel).rsplit('\\', 1)[-1]
        name = _unique(f'{model_name}.nif' if model_name and not new_paths else orig, nif_names)
        for tex in nif_tools.texture_paths(nif):
            new = _copy_texture(tex, f'{dest}\\{Path(name).stem}', plugin_dir, dirs, archives, tex_names, done, warnings)
            if new:
                mapping[norm(tex)] = f'textures\\{new}'
        out = plugin_dir / 'Meshes' / dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(nif_tools.replace_textures(nif, mapping))
        files.append(str(out))
        new_paths[sig] = f'{dest}\\{name}'
    for sig, rel in asset.textures:
        new = _copy_texture(rel, dest, plugin_dir, dirs, archives, tex_names, done, warnings)
        if new:
            new_paths[sig] = new
    files += [str(plugin_dir / 'Textures' / n.replace('\\', '/')) for n in done.values() if n]

    if mode == 'files':
        return {'record': '', 'files': files, 'warnings': warnings}
    snapshot(plugin_path, 'pre_asset_duplicate')
    if not asset.owned:
        builder = NpcBuilder(model, plugin_dir, data_dir)
        if mode == 'clone':
            clone_record(builder, asset.edid or f'{asset.fid:08X}', str(plugin_path), editor_id, signature=asset.sig)
        else:
            _override(builder, asset, plugin_path)
    plugin = Plugin.load(str(plugin_path))
    if mode == 'clone' and asset.owned:
        src = next(r for r in plugin.records if r.form_id.value == asset.record.form_id.value)
        target = Record(src.signature, FormID(_next_id(plugin)), 0)
        target.flags = src.flags
        for s in src.subrecords:
            target.add_subrecord(s.signature, z(editor_id) if s.signature == 'EDID' else bytes(s.data))
        plugin.add_record(target)
    elif mode == 'clone':
        target = next(r for r in plugin.records if r.signature == asset.sig and r.editor_id == editor_id)
    elif asset.edid:
        target = next(r for r in plugin.records if r.signature == asset.sig and r.editor_id == asset.edid)
    else:
        target = next(r for r in plugin.records if r.signature == asset.sig and
                      (r.form_id.value & 0xFFFFFF) == (asset.fid & 0xFFFFFF))
    for s in target.subrecords:
        if s.signature in new_paths:
            s.data = z(new_paths[s.signature])
    if asset.models:
        target.subrecords[:] = [s for s in target.subrecords if s.signature not in MODEL_HASH_SIGS]
        _drop_template(target)
    target.modified = True
    plugin.save(str(plugin_path))
    return {'record': target.editor_id, 'files': files, 'warnings': warnings}


def _drop_template(record):
    """Detach a weapon/armor from its template record, whose model would otherwise replace the one we set."""
    sig = TEMPLATE_SIGS.get(record.signature)
    if sig:
        record.subrecords[:] = [s for s in record.subrecords if s.signature != sig]


def first_person_asset(model, assets, weapon):
    """The STAT a WEAP uses as its first-person model (WNAM), or None."""
    sub = weapon.record.get_subrecord('WNAM')
    if weapon.sig != 'WEAP' or sub is None or len(sub.data) < 4:
        return None
    fid = model.absid(weapon.record.plugin, int.from_bytes(bytes(sub.data)[:4], 'little'))
    return next((a for a in assets if a.fid == fid), None)


LABELS = {
    ('WEAP', 'MODL'): "World / third-person model",
    ('STAT', 'MODL'): "Model",
    ('BOOK', 'MODL'): "World model",
    ('ARMO', 'MOD2'): "Male world model", ('ARMO', 'MOD4'): "Female world model",
    ('ARMO', 'MOD3'): "Male model (MOD3)", ('ARMO', 'MOD5'): "Female model (MOD5)",
    ('ARMA', 'MOD2'): "Male body model", ('ARMA', 'MOD3'): "Female body model",
    ('ARMA', 'MOD4'): "Male first-person model", ('ARMA', 'MOD5'): "Female first-person model",
}


@dataclass
class ModelEntry:
    holder: object  # the Asset whose subrecord holds the path
    sig: str
    rel: str
    label: str
    parent: object = None  # the Asset that reaches `holder` through a form ID (None when holder is the record itself)
    ref_sig: str = ''  # subrecord of `parent` holding that form ID


def model_entries(model, assets, asset):
    """Every NIF a record uses: its own models plus those of the records it points to (a weapon's first-person STAT,
    an armor's armor addons)."""
    by_fid = {a.fid: a for a in assets}
    out = [ModelEntry(asset, sig, rel, LABELS.get((asset.sig, sig), f"Model ({sig})")) for sig, rel in asset.models]
    refs = []
    if asset.sig == 'WEAP':
        sub = asset.record.get_subrecord('WNAM')
        if sub is not None and len(sub.data) >= 4:
            refs.append(('WNAM', int.from_bytes(bytes(sub.data)[:4], 'little'), "First-person"))
    elif asset.sig == 'BOOK':
        sub = asset.record.get_subrecord('INAM')
        if sub is not None and len(sub.data) >= 4:
            refs.append(('INAM', int.from_bytes(bytes(sub.data)[:4], 'little'), "Inventory"))
    elif asset.sig == 'ARMO':
        refs = [('MODL', int.from_bytes(bytes(s.data), 'little'), "Armor addon")
                for s in asset.record.subrecords if s.signature == 'MODL' and len(s.data) == 4]
    for ref_sig, raw, kind in refs:
        linked = by_fid.get(model.absid(asset.record.plugin, raw))
        if linked is None:
            continue
        for sig, rel in linked.models:
            label = LABELS.get((linked.sig, sig), f"Model ({sig})")
            out.append(ModelEntry(linked, sig, rel, f"{kind} {linked.edid}: {label}".replace('  ', ' '), asset, ref_sig))
    return out


def replace_model(model, plugin_path, entry, dest, name, editor_id=None, dirs=(), archives=None, data_dir=None):
    """Replace one NIF of a record with your own copy (named `name`, textures in Textures\\<dest>\\<name>).

    The record holding the path is edited in place when it is yours or is the record itself; a master's linked record
    (a vanilla first-person STAT or armor addon) is cloned as `editor_id` and the parent is pointed at the clone, so
    other items sharing it are unaffected. Returns the editor ID of the record that was changed or created.
    """
    from dataclasses import replace as clone_asset
    plugin_path = Path(plugin_path)
    one = clone_asset(entry.holder, models=[(entry.sig, entry.rel)])
    if entry.parent is None or entry.holder.owned:
        return duplicate_asset(model, plugin_path, one, dest, None, dirs, archives, data_dir, 'repoint', name)['record']
    edid = editor_id or f"{entry.holder.edid or entry.holder.sig}_{name}"
    duplicate_asset(model, plugin_path, one, dest, edid, dirs, archives, data_dir, 'clone', name)
    _point_reference(model, plugin_path, entry.parent, entry.ref_sig, entry.holder.fid, edid, data_dir)
    return edid


def _point_reference(model, plugin_path, parent, ref_sig, old_fid, new_edid, data_dir):
    from esplib import Plugin
    from tools.npcs.npc_builder import NpcBuilder
    if not parent.owned:
        _override(NpcBuilder(model, plugin_path.parent, data_dir), parent, plugin_path)
    plugin = Plugin.load(str(plugin_path))
    new = next(r for r in plugin.records if r.editor_id == new_edid)
    rec = next(r for r in plugin.records if r.signature == parent.sig and
               (r.form_id.value & 0xFFFFFF) == (parent.fid & 0xFFFFFF))
    for s in rec.subrecords:
        if s.signature == ref_sig and len(s.data) == 4 and \
                (int.from_bytes(bytes(s.data), 'little') & 0xFFFFFF) == (old_fid & 0xFFFFFF):
            s.data = new.form_id.value.to_bytes(4, 'little')
    rec.modified = True
    plugin.save(str(plugin_path))


def give_first_person(model, assets, plugin_path, weapon, dest, dirs=(), archives=None, data_dir=None):
    """Give a weapon its own copy of the first-person model (what shows when you hold it in first person).

    The STAT is cloned as <weapon>_1P with its mesh in Meshes\\<dest>; textures that the weapon's own copies in
    Textures\\<dest> already cover (same file name) are reused, so custom retextures carry over. Returns the new
    STAT's editor ID.
    """
    from esplib import Plugin
    from tools.npcs.npc_builder import NpcBuilder, z
    from tools.records.maintenance import snapshot

    fp = first_person_asset(model, assets, weapon)
    if fp is None or not fp.models:
        raise ValueError("this weapon has no first-person model record with a mesh")
    plugin_path = Path(plugin_path)
    plugin_dir = plugin_path.parent
    edid = f"{weapon.edid or 'Weapon'}_1P"
    res = duplicate_asset(model, plugin_path, fp, f'{dest}_1P', edid, dirs, archives, data_dir, 'clone')
    own = plugin_dir / 'Textures' / dest
    extra = plugin_dir / 'Textures' / f'{dest}_1P'
    for mesh in (plugin_dir / 'Meshes' / f'{dest}_1P').glob('*.nif'):
        nif = nif_tools.parse(mesh.read_bytes())
        mapping = {}
        for tex in nif_tools.texture_paths(nif):
            name = tex.replace('/', '\\').rsplit('\\', 1)[-1].lower()
            found = next((f for f in own.rglob('*') if f.is_file() and f.name.lower() == name), None) \
                if own.is_dir() else None
            if found:
                mapping[norm(tex)] = 'textures\\' + str(found.relative_to(plugin_dir / 'Textures')).replace('/', '\\')
        if mapping:
            mesh.write_bytes(nif_tools.replace_textures(nif, mapping))
    if extra.is_dir():
        used = {norm(t) for m in (plugin_dir / 'Meshes' / f'{dest}_1P').glob('*.nif')
                for t in nif_tools.texture_paths(nif_tools.parse(m.read_bytes()))}
        for f in list(extra.rglob('*')):
            if f.is_file() and norm('textures\\' + str(f.relative_to(plugin_dir / 'Textures')).replace('/', '\\')) not in used:
                f.unlink()
        for d in sorted((p for p in extra.rglob('*') if p.is_dir()), reverse=True) + [extra]:
            if not any(d.iterdir()):
                d.rmdir()

    snapshot(plugin_path, 'pre_first_person')
    if not weapon.owned:
        _override(NpcBuilder(model, plugin_dir, data_dir), weapon, plugin_path)
    plugin = Plugin.load(str(plugin_path))
    new = next(r for r in plugin.records if r.editor_id == res['record'])
    target = next(r for r in plugin.records if r.signature == 'WEAP' and
                  (r.form_id.value & 0xFFFFFF) == (weapon.fid & 0xFFFFFF))
    target.get_subrecord('WNAM').data = new.form_id.value.to_bytes(4, 'little')
    _drop_template(target)
    target.modified = True
    plugin.save(str(plugin_path))
    return res['record']


def use_model(model, plugin_path, asset, source, data_dir=None):
    """Point `asset`'s model paths at the ones `source` uses (e.g. make an original record use its duplicate's mesh).

    A master's record becomes an override in the plugin. Returns the edited record's editor ID.
    """
    from esplib import Plugin
    from tools.npcs.npc_builder import NpcBuilder, z
    from tools.records.maintenance import snapshot

    if not asset.models or not source.models:
        raise ValueError("both records need a model path")
    if asset.fid == source.fid:
        raise ValueError("pick a different record to copy the model from")
    plugin_path = Path(plugin_path)
    snapshot(plugin_path, 'pre_use_model')
    if not asset.owned:
        _override(NpcBuilder(model, plugin_path.parent, data_dir), asset, plugin_path)
    plugin = Plugin.load(str(plugin_path))
    target = next(r for r in plugin.records if r.signature == asset.sig and (r.form_id.value & 0xFFFFFF) == (asset.fid & 0xFFFFFF))
    paths = dict(source.models)
    for s in target.subrecords:
        if s.signature in MODEL_SIGS and s.signature in paths:
            s.data = z(paths[s.signature])
    target.subrecords[:] = [s for s in target.subrecords if s.signature not in MODEL_HASH_SIGS]
    _drop_template(target)
    target.modified = True
    plugin.save(str(plugin_path))
    return target.editor_id


def _override(builder, asset, plugin_path):
    """Add an override of a master's record to the plugin (masters are added as needed)."""
    from tools.core.plugin_edit import Target, resolve_target_path
    from tools.npcs.npc_builder import Plan
    path = resolve_target_path(str(plugin_path), builder.output_dir, builder.data_dir)
    target = Target(builder.m, builder.names, path, create=False)
    plan = Plan()
    plan.clone = asset.record
    builder.target = target
    builder.add_masters(target.plugin, plan)
    builder.plugin = target.plugin
    target.plugin.add_record(builder.override(target.plugin, asset.record))
    target.save()


def _next_id(plugin):
    own = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if r.form_id.value >> 24 == own]
    new = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    plugin.header.next_object_id = new + 1
    return (own << 24) | new


def replace_texture(path, source):
    """Overwrite a duplicated texture in the plugin folder with another file."""
    shutil.copyfile(source, path)


def texture_image(data):
    """PIL image from DDS/PNG bytes, or None when Pillow can't decode it."""
    import io
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert('RGBA')
    except Exception:
        return None


def texture_array(data):
    """RGB uint8 array (H, W, 3) from texture bytes, or None when it can't be decoded."""
    import numpy as np
    img = texture_image(data)
    if img is None:
        return None
    img.thumbnail((512, 512))
    return np.asarray(img.convert('RGB'))


def render_mesh(nif, size=420, yaw=0.6, pitch=0.3, load_texture=None):
    """PIL image of a lit view of the NIF's triangle shapes, or None if it has none we can read.

    `load_texture(path)` may return an (H, W, 3) array for a texture path (as stored in the NIF); shapes that have
    UVs and a diffuse texture are drawn with it, the rest in grey.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    verts, tris, uvs, tex_of, textures = [], [], [], [], []
    cache = {}
    for _, v, t, uv, path in nif_tools.textured_shapes(nif):
        if not v or not t:
            continue
        tex = -1
        if load_texture and path and len(uv) == len(v):
            if path not in cache:
                arr = load_texture(path)
                cache[path] = len(textures) if arr is not None else -1
                if arr is not None:
                    textures.append(arr)
            tex = cache[path]
        base = sum(len(x) for x in verts)
        verts.append(np.array(v, dtype=float))
        tris.append(np.array(t, dtype=int) + base)
        uvs.append(np.array(uv, dtype=float) if len(uv) == len(v) else np.zeros((len(v), 2)))
        tex_of.append(np.full(len(t), tex))
    if not verts:
        return None
    verts, tris, uvs, tex_of = np.vstack(verts), np.vstack(tris), np.vstack(uvs), np.concatenate(tex_of)
    keep = (tris < len(verts)).all(axis=1)
    tris, tex_of = tris[keep], tex_of[keep]
    verts = verts - (verts.max(axis=0) + verts.min(axis=0)) / 2
    verts /= max(np.abs(verts).max(), 1e-6)
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    pts = verts @ (rx @ rz).T  # x right, y into the screen, z up
    tri = pts[tris]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    shade = 0.6 + 0.4 * np.abs(normal @ np.array([0.3, -0.8, 0.5]))
    scr = np.stack([size / 2 + pts[:, 0] * size * 0.45, size / 2 - pts[:, 2] * size * 0.45], axis=1)
    if len(tris) > 40000:
        pick = np.linspace(0, len(tris) - 1, 40000).astype(int)
        tris, tex_of, shade, tri = tris[pick], tex_of[pick], shade[pick], tri[pick]

    def sample(k, u, v):
        arr = textures[tex_of[k]]
        h, w = arr.shape[:2]
        return arr[(np.floor(v % 1 * h).astype(int)) % h, (np.floor(u % 1 * w).astype(int)) % w]

    bg = (36, 38, 44)
    if len(tris) <= 5000:  # per-pixel z-buffer with interpolated UVs
        canvas = np.empty((size, size, 3), dtype=np.uint8)
        canvas[:] = bg
        zbuf = np.full((size, size), np.inf)
        for k in range(len(tris)):
            a, b, c = scr[tris[k]]
            x0, x1 = max(int(min(a[0], b[0], c[0])), 0), min(int(max(a[0], b[0], c[0])) + 1, size - 1)
            y0, y1 = max(int(min(a[1], b[1], c[1])), 0), min(int(max(a[1], b[1], c[1])) + 1, size - 1)
            if x0 > x1 or y0 > y1:
                continue
            den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(den) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            w0 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / den
            w1 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not inside.any():
                continue
            depth = w0 * tri[k, 0, 1] + w1 * tri[k, 1, 1] + w2 * tri[k, 2, 1]
            win = inside & (depth < zbuf[y0:y1 + 1, x0:x1 + 1])
            if not win.any():
                continue
            zbuf[y0:y1 + 1, x0:x1 + 1][win] = depth[win]
            if tex_of[k] >= 0:
                ta, tb, tc = uvs[tris[k]]
                col = sample(k, (w0 * ta[0] + w1 * tb[0] + w2 * tc[0])[win], (w0 * ta[1] + w1 * tb[1] + w2 * tc[1])[win])
                canvas[y0:y1 + 1, x0:x1 + 1][win] = np.clip(col * shade[k] * 1.3, 0, 255).astype(np.uint8)
            else:
                canvas[y0:y1 + 1, x0:x1 + 1][win] = int(30 + 200 * (shade[k] - 0.6) / 0.4 * 0.8 + 60)
        return Image.fromarray(canvas)

    img = Image.new('RGB', (size, size), bg)
    draw = ImageDraw.Draw(img)
    for k in np.argsort(-tri[:, :, 1].mean(axis=1)):
        poly = [tuple(scr[i]) for i in tris[k]]
        if tex_of[k] >= 0:
            uv = uvs[tris[k]].mean(axis=0)
            col = tuple(int(x) for x in np.clip(sample(k, np.array(uv[0]), np.array(uv[1])) * shade[k] * 1.3, 0, 255))
        else:
            c = int(30 + 200 * (shade[k] - 0.6) / 0.4 * 0.8 + 60)
            col = (c, c, min(255, c + 12))
        draw.polygon(poly, fill=col)
    return img
