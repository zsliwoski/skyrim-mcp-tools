"""Place a reference to an existing base object in an existing cell."""
import math
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.core.plugin_edit import Target, hi, resolve_target_path

# Base types that can be placed with a plain REFR.
PLACEABLE = ['WEAP', 'ARMO', 'ALCH', 'BOOK', 'MISC', 'AMMO', 'INGR', 'KEYM', 'SCRL', 'SLGM', 'APPA', 'LIGH',
             'STAT', 'MSTT', 'ACTI', 'CONT', 'FURN', 'DOOR', 'FLOR', 'TREE', 'LVLI']


def place_item(b, base, target_plugin, cell, x, y, z_pos, rotation_z=0.0, scale=None, persistent=False,
               editor_id=None):
    """Add a REFR for `base` to `cell` inside target_plugin (the file must already exist)."""
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    own_id = None
    own = target.find_own(base, PLACEABLE)
    if own is not None:
        own_id = own.form_id.value & 0xFFFFFF
        fid = 0
    else:
        fid, _ = b.resolver.lookup(base, PLACEABLE)
    if scale is not None and not 0.1 <= scale <= 10:
        raise ValueError("scale must be between 0.1 and 10")

    p = Plan()
    p.cell = b.cells.find(cell)
    if fid:
        p.abs_ids.add(fid)
    b.target, b.plugin = target, plugin
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    new_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])

    base_local = b.own(own_id) if own_id is not None else b.local(fid)
    ref = Record('REFR', FormID(b.own(new_id)), 0x400 if persistent else 0)
    if editor_id:
        ref.add_subrecord('EDID', z(editor_id))
    ref.add_subrecord('NAME', fid_bytes(base_local))
    if scale is not None:
        ref.add_subrecord('XSCL', struct.pack('<f', scale))
    ref.add_subrecord('DATA', struct.pack('<6f', x, y, z_pos, 0.0, 0.0, math.radians(rotation_z)))
    made = []
    b.place_ref(plugin, p.cell, ref, persistent, made)
    plugin.header.next_object_id = new_id + 1
    target.save()
    return {'path': str(path), 'placed': made[-1], 'base': base, 'cell': cell,
            'position': {'x': x, 'y': y, 'z': z_pos, 'rotation_z': rotation_z}, 'records': made}
