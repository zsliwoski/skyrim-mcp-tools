"""Locations (LCTN), their cells, and world-map markers."""
import re
import struct

from esplib.record import Record
from esplib.utils import FormID
from esplib.plugin import Plugin

from tools.cells.place_item import place_item
from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.records.maintenance import all_records

MARKER_TYPES = [
    'none', 'city', 'town', 'settlement', 'cave', 'camp', 'fort', 'nordic_ruin', 'dwemer_ruin', 'shipwreck',
    'grove', 'landmark', 'dragon_lair', 'farm', 'wood_mill', 'mine', 'imperial_camp', 'stormcloak_camp',
    'doomstone', 'wheat_mill', 'smelter', 'stable', 'imperial_tower', 'clearing', 'pass', 'altar', 'rock',
    'lighthouse', 'orc_stronghold', 'giant_camp', 'shack', 'nordic_tower', 'nordic_dwelling', 'docks', 'shrine',
]
# Subrecords that precede XLCN in a CELL.
BEFORE_XLCN = {'EDID', 'FULL', 'DATA', 'XCLC', 'XCLL', 'TVDT', 'LTMP', 'LNAM', 'XCLW', 'XCIM'}


def _new_id(plugin):
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    return max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])


def create_location(b, target_plugin, editor_id, name, parent=None, keywords=None, cells=None, radius=None):
    """Add a location (LCTN) with a name, optional parent location and keywords, and assign cells to it.

    keywords: LocType keyword editor IDs (e.g. LocTypeDungeon, LocTypeInn, LocTypeHabitation).
    cells: editor IDs of cells already in target_plugin; each gets this location as its XLCN.
    """
    if not re.fullmatch(r'[A-Za-z0-9_]+', editor_id or ''):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    if not name:
        raise ValueError("name is required")
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=True)
    plugin = target.plugin
    if any((r.editor_id or '').lower() == editor_id.lower() for r in plugin.records) or \
            any((r.editor_id or '').lower() == editor_id.lower() for r in b.m.records.values()):
        raise ValueError(f"editor_id {editor_id!r} already exists")
    p = Plan()
    parent_fid = b.resolver.lookup(parent, ['LCTN'])[0] if parent else 0
    kw = [b.resolver.lookup(k, ['KYWD'])[0] for k in (keywords or [])]
    p.abs_ids.update(kw)
    if parent_fid:
        p.abs_ids.add(parent_fid)
    cell_recs = []
    for c in cells or []:
        cell = next((r for r in all_records(plugin) if r.signature == 'CELL'
                     and (r.editor_id or '').lower() == c.lower()), None)
        if cell is None:
            raise ValueError(f"cell {c!r} is not in {target_plugin}; create it first with create_cell")
        cell_recs.append(cell)
    b.target, b.plugin = target, plugin
    b.add_masters(plugin, p)
    oid = _new_id(plugin)
    rec = Record('LCTN', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('FULL', z(name))
    if kw:
        rec.add_subrecord('KSIZ', struct.pack('<I', len(kw)))
        rec.add_subrecord('KWDA', b''.join(fid_bytes(b.local(k)) for k in kw))
    if parent_fid:
        rec.add_subrecord('PNAM', fid_bytes(b.local(parent_fid)))
    if radius is not None:
        rec.add_subrecord('RNAM', struct.pack('<f', float(radius)))
    plugin.add_record(rec)
    plugin.header.next_object_id = oid + 1
    for cell in cell_recs:
        cell.subrecords[:] = [s for s in cell.subrecords if s.signature != 'XLCN']
        at = max([i + 1 for i, s in enumerate(cell.subrecords) if s.signature in BEFORE_XLCN] or [0])
        sub = type(cell.subrecords[0])('XLCN', fid_bytes(rec.form_id.value))
        cell.subrecords.insert(at, sub)
    plugin.modified = True
    target.save()
    return {'path': str(path), 'location': f"{rec.form_id.value:08X}", 'editor_id': editor_id,
            'parent': parent, 'keywords': keywords or [], 'cells_assigned': list(cells or [])}


def add_map_marker(refresh, target_plugin, editor_id, name, x, y, z_pos, marker_type='landmark', cell=None,
                   worldspace='Tamriel', grid_x=None, grid_y=None, visible=True, can_travel=True,
                   location=None):
    """Place a named map marker in an exterior cell (persistent). It shows on the map once discovered.

    marker_type: a name from MARKER_TYPES or the number. visible: shown without discovery. can_travel: fast travel
    to it. location: editor ID of an LCTN in target_plugin; the marker becomes its world marker (MNAM).
    """
    if not re.fullmatch(r'[A-Za-z0-9_]+', editor_id or ''):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    if not name:
        raise ValueError("name is required")
    kind = MARKER_TYPES.index(marker_type) if marker_type in MARKER_TYPES else marker_type
    if not isinstance(kind, int) or not 0 <= kind <= 255:
        raise ValueError(f"marker_type must be one of {', '.join(MARKER_TYPES)} or a number")
    flags = (1 if visible else 0) | (2 if can_travel else 0)
    extra = [('XMRK', b''), ('FNAM', bytes([flags])), ('FULL', z(name)), ('TNAM', bytes([kind, 0]))]
    builder, _ = refresh()
    res = place_item(builder, 'MapMarker', target_plugin, cell, x, y, z_pos, 0.0, None, True, editor_id,
                     worldspace=worldspace, grid_x=grid_x, grid_y=grid_y, kinds=['STAT'], extra=extra)
    if location:
        builder, _ = refresh()
        path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
        plugin = Plugin.load(str(path))
        ref = next((r for r in all_records(plugin) if r.signature == 'REFR' and r.editor_id == editor_id), None)
        lctn = next((r for r in plugin.records if r.signature == 'LCTN'
                     and (r.editor_id or '').lower() == location.lower()), None)
        if lctn is None:
            raise ValueError(f"location {location!r} is not in {target_plugin}; create it with create_location")
        lctn.subrecords[:] = [s for s in lctn.subrecords if s.signature != 'MNAM']
        at = max([i + 1 for i, s in enumerate(lctn.subrecords) if s.signature in
                  ('EDID', 'LCPR', 'LCUN', 'LCSR', 'LCEC', 'LCID', 'LCEP', 'FULL', 'KSIZ', 'KWDA', 'PNAM',
                   'NAM1')] or [0])
        lctn.subrecords.insert(at, type(lctn.subrecords[0])('MNAM', fid_bytes(ref.form_id.value)))
        plugin.modified = True
        plugin.save(str(path))
        res['location'] = location
    res['marker_type'] = MARKER_TYPES[kind] if kind < len(MARKER_TYPES) else kind
    return res
