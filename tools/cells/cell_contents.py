"""List the references placed in a cell, with their base objects and positions."""
import math
import struct

from esplib.record import GroupRecord

from tools.cells.ref_search import _plugins


def get_cell_contents(builder, cell, query=None, signature=None, limit=100, x=None, y=None, radius=None):
    """Every REFR/ACHR in `cell` across the loaded plugins (overrides and additions included).

    With x, y and radius only references within that horizontal distance are kept, nearest first."""
    m = builder.m
    src_plugin, _, cell_src = builder.cells.find(cell)
    want = m.absid(cell_src.plugin, cell_src.form_id.value)
    low = (query or '').strip().lower()
    sig = signature.upper() if signature else None
    near = x is not None and y is not None and radius is not None
    refs = []

    def visit(group, plugin, in_cell):
        for item in group.records:
            if isinstance(item, GroupRecord):
                visit(item, plugin, in_cell or (item.group_type == 6 and isinstance(item.label, int)
                                                and m.absid(plugin, item.label) == want))
                continue
            if not in_cell or item.signature not in ('REFR', 'ACHR'):
                continue
            name, data = item.get_subrecord('NAME'), item.get_subrecord('DATA')
            if name is None or data is None or len(data.data) < 24:
                continue
            base = m.records.get(m.absid(plugin, struct.unpack('<I', bytes(name.data))[0]))
            if base is None:
                continue
            if sig and base.signature != sig:
                continue
            if low and low not in (base.editor_id or '').lower() and low not in (base.full_name or '').lower():
                continue
            px, py, pz, _, _, rz = struct.unpack_from('<6f', bytes(data.data))
            if near and math.hypot(px - x, py - y) > radius:
                continue
            refs.append({'ref': f"{item.form_id.value & 0xFFFFFF:06X}", 'type': item.signature,
                         'plugin': plugin.file_path.name, 'base': base.signature,
                         'editor_id': base.editor_id, 'name': base.full_name,
                         'x': round(px, 1), 'y': round(py, 1), 'z': round(pz, 1),
                         'rotation_z': round(math.degrees(rz), 1)})

    for pl in _plugins(m):
        for g in pl.groups:
            if g.group_type == 0 and g.label in ('CELL', 'WRLD'):
                visit(g, pl, False)
    if near:
        refs.sort(key=lambda r: math.hypot(r['x'] - x, r['y'] - y))
    counts = {}
    for r in refs:
        counts[r['base']] = counts.get(r['base'], 0) + 1
    return {'cell': cell_src.editor_id or cell_src.full_name, 'total': len(refs), 'by_type': counts,
            'references': refs[:limit]}


# Bases that take no floor space or are only markers, so they never block an item.
NON_BLOCKING_SIGS = {'LIGH'}


def pick_spots(blockers, x, y, count, spacing=30.0, clearance=25.0, radius=300.0):
    """Up to `count` (x, y) points near (x, y), on a grid of `spacing`, none within `clearance` of a blocker.

    blockers are (x, y) pairs. Points are taken nearest-first, so the first one is (x, y) itself when it is free."""
    steps = int(radius // spacing)
    grid = sorted(((x + i * spacing, y + j * spacing) for i in range(-steps, steps + 1)
                   for j in range(-steps, steps + 1) if math.hypot(i, j) * spacing <= radius),
                  key=lambda p: (round(math.hypot(p[0] - x, p[1] - y), 3), p[1], p[0]))
    return [p for p in grid if all(math.hypot(p[0] - bx, p[1] - by) >= clearance for bx, by in blockers)][:count]


def find_free_spots(builder, cell, x, y, z, count=1, spacing=30.0, clearance=25.0, radius=300.0, height=120.0):
    """Free floor points near (x, y, z) in a cell, avoiding references within `height` of z (lights are ignored)."""
    contents = get_cell_contents(builder, cell, limit=1 << 30, x=x, y=y, radius=radius + clearance)
    blockers = [(r['x'], r['y']) for r in contents['references']
                if r['base'] not in NON_BLOCKING_SIGS and abs(r['z'] - z) <= height]
    spots = pick_spots(blockers, x, y, count, spacing, clearance, radius)
    if len(spots) < count:
        raise ValueError(f"only {len(spots)} free spot(s) within {radius:g} units of ({x:g}, {y:g}) in {cell}; "
                         f"raise radius or lower spacing/clearance")
    return {'cell': contents['cell'], 'spots': [{'x': round(px, 1), 'y': round(py, 1), 'z': z} for px, py in spots],
            'blockers_checked': len(blockers)}
