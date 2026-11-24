"""Change a placed reference from a master (move, rotate, scale, disable) through an override in the plugin."""
import math
import re
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.cells.persist import PERSISTENT, find_reference
from tools.core.plugin_edit import Target, resolve_target_path

DISABLED = 0x800  # "initially disabled"


def _set(ref, sig, data, before=('DATA',)):
    sub = type(ref.subrecords[0])(sig, data)
    for i, s in enumerate(ref.subrecords):
        if s.signature == sig:
            ref.subrecords[i] = sub
            return
    at = next((i for i, s in enumerate(ref.subrecords) if s.signature in before), len(ref.subrecords))
    ref.subrecords.insert(at, sub)


def edit_reference(b, reference, target_plugin, disable=None, x=None, y=None, z=None, rotation_x=None,
                   rotation_y=None, rotation_z=None, scale=None, persistent=None, worldspace='Tamriel'):
    """Override a placed reference (hex ID) from a master and apply the given changes. Returns what changed."""
    if not re.fullmatch(r'(0x)?[0-9a-fA-F]{1,8}', str(reference)):
        raise ValueError("reference must be a hex reference ID (see search_references)")
    if scale is not None and not 0.1 <= scale <= 10:
        raise ValueError("scale must be between 0.1 and 10")
    if all(v is None for v in (disable, x, y, z, rotation_x, rotation_y, rotation_z, scale, persistent)):
        raise ValueError("nothing to change: give disable, a position/rotation, scale or persistent")
    abs_id = int(str(reference), 16) & 0xFFFFFF
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    b.target, b.plugin = target, plugin
    ref = target.find_matching(abs_id)
    made = []
    fresh = ref is None
    if fresh:
        hit = find_reference(b, abs_id)
        if hit is None or hit[1] is None:
            raise ValueError(f"reference {abs_id:06X} not found in a cell of the loaded plugins")
        src, cell = hit
        from tools.npcs.npc_builder import Plan
        p = Plan()
        p.abs_ids.add(abs_id)
        b.add_masters(plugin, p)
        name = cell.editor_id or cell.full_name
        info = b.cells.find(name, None, None, None) if name else b.cells.find(None, worldspace, *_grid(cell))
        ref = b.override(plugin, src)
    flags = int(ref.flags)
    if disable is not None:
        flags = flags | DISABLED if disable else flags & ~DISABLED
    if persistent is not None:
        flags = flags | PERSISTENT if persistent else flags & ~PERSISTENT
    ref.flags = Record('REFR', FormID(0), flags).flags
    data = ref.get_subrecord('DATA')
    if data is None or len(data.data) < 24:
        raise ValueError("the reference has no position data")
    vals = list(struct.unpack('<6f', bytes(data.data)[:24]))
    for i, v in enumerate((x, y, z)):
        if v is not None:
            vals[i] = float(v)
    for i, v in enumerate((rotation_x, rotation_y, rotation_z)):
        if v is not None:
            vals[3 + i] = math.radians(v)
    _set(ref, 'DATA', struct.pack('<6f', *vals), before=())
    if scale is not None:
        _set(ref, 'XSCL', struct.pack('<f', scale))
    if fresh:
        b.place_ref(plugin, info, ref, bool(flags & PERSISTENT), made)
    plugin.modified = True
    target.save()
    return {'path': str(path), 'reference': f"{ref.form_id.value:08X}", 'disabled': bool(flags & DISABLED),
            'persistent': bool(flags & PERSISTENT),
            'position': dict(zip('xyz', vals[:3])),
            'rotation_degrees': {k: round(math.degrees(v), 3) for k, v in zip(('x', 'y', 'z'), vals[3:])},
            'records': made}


def _grid(cell):
    sub = cell.get_subrecord('XCLC')
    if sub is None:
        raise ValueError("the reference's cell has no name; cannot locate it")
    gx, gy = struct.unpack_from('<ii', bytes(sub.data))
    return gx, gy
