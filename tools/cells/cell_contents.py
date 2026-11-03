"""List the references placed in a cell, with their base objects and positions."""
import math
import struct

from esplib.record import GroupRecord

from tools.cells.ref_search import _plugins


def get_cell_contents(builder, cell, query=None, signature=None, limit=100):
    """Every REFR/ACHR in `cell` across the loaded plugins (overrides and additions included)."""
    m = builder.m
    src_plugin, _, cell_src = builder.cells.find(cell)
    want = m.absid(cell_src.plugin, cell_src.form_id.value)
    low = (query or '').strip().lower()
    sig = signature.upper() if signature else None
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
            refs.append({'ref': f"{item.form_id.value & 0xFFFFFF:06X}", 'type': item.signature,
                         'plugin': plugin.file_path.name, 'base': base.signature,
                         'editor_id': base.editor_id, 'name': base.full_name,
                         'x': round(px, 1), 'y': round(py, 1), 'z': round(pz, 1),
                         'rotation_z': round(math.degrees(rz), 1)})

    for pl in _plugins(m):
        for g in pl.groups:
            if g.group_type == 0 and g.label in ('CELL', 'WRLD'):
                visit(g, pl, False)
    counts = {}
    for r in refs:
        counts[r['base']] = counts.get(r['base'], 0) + 1
    return {'cell': cell_src.editor_id or cell_src.full_name, 'total': len(refs), 'by_type': counts,
            'references': refs[:limit]}
