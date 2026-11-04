"""Find placed references (REFR/ACHR) by base form, worldspace and position."""
import struct

from esplib import Plugin
from esplib.record import GroupRecord

_cache = {}


def _plugins(model):
    """Plugins of the model, loaded once per path with only the signatures this search needs."""
    out = []
    for pl in model.plugins:
        path = pl.file_path
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in _cache:
            for old in [k for k in _cache if k[0] == key[0]]:
                del _cache[old]
            _cache[key] = Plugin.load_cached(str(path), only_signatures={'CELL', 'WRLD', 'REFR', 'ACHR'})
        out.append(_cache[key])
    return out


def search_references(builder, base, worldspace=None, cell=None, x=None, y=None, radius=None, limit=25):
    """References whose base form matches `base` (editor ID, name or form ID).

    worldspace / cell restrict where to look; x, y, radius (game units) keep only references near that point.
    Each hit has the reference ID, base, position, plugin and cell (name, or worldspace + grid).
    """
    m = builder.m
    fid, brec = builder.resolver.lookup(base, list(m.by_sig))
    wrld = worldspace.lower() if worldspace else None
    want_cell = str(cell).lower() if cell else None
    out = []

    def visit(group, plugin, world, cell_rec):
        for item in group.records:
            if isinstance(item, GroupRecord):
                if item.group_type == 1:
                    prev = visit.prev
                    visit(item, plugin, prev, cell_rec)
                else:
                    visit(item, plugin, world, cell_rec)
                continue
            visit.prev = item
            if item.signature == 'CELL':
                visit.cell = item
                continue
            if item.signature not in ('REFR', 'ACHR'):
                continue
            name, data = item.get_subrecord('NAME'), item.get_subrecord('DATA')
            if name is None or data is None or len(data.data) < 12:
                continue
            if m.absid(plugin, struct.unpack('<I', bytes(name.data))[0]) != fid:
                continue
            c = getattr(visit, 'cell', None)
            world_id = (world.editor_id or '') if world is not None else None
            if wrld and (world_id or '').lower() != wrld:
                continue
            if want_cell and not (c is not None and want_cell in ((c.editor_id or '').lower(),
                                                                (c.full_name or '').lower())):
                continue
            px, py, pz = struct.unpack_from('<3f', bytes(data.data))
            if x is not None and y is not None and radius is not None \
                    and (px - x) ** 2 + (py - y) ** 2 > radius ** 2:
                continue
            grid = None
            sr = c.get_subrecord('XCLC') if c is not None else None
            if sr is not None and len(sr.data) >= 8:
                grid = struct.unpack_from('<ii', bytes(sr.data))
            out.append({'ref': f"{item.form_id.value & 0xFFFFFF:06X}", 'signature': item.signature,
                        'editor_id': item.editor_id, 'base': brec.editor_id, 'plugin': plugin.file_path.name,
                        'worldspace': world_id, 'cell': (c.editor_id or c.full_name) if c is not None else None,
                        'grid': grid, 'x': round(px, 1), 'y': round(py, 1), 'z': round(pz, 1)})

    visit.prev = visit.cell = None
    for pl in _plugins(m):
        for g in pl.groups:
            if g.group_type == 0 and g.label in ('WRLD', 'CELL'):
                visit(g, pl, None, None)
            if len(out) >= limit * 20:
                break
    if x is not None and y is not None:
        out.sort(key=lambda r: (r['x'] - x) ** 2 + (r['y'] - y) ** 2)
    return out[:limit]
