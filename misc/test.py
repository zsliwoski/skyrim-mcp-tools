"""Scratch script that dumps a few cells and the base objects their references point at."""
import struct
from collections import Counter

from esplib import Plugin

SKYRIM_ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm"
CELLS_TO_DUMP = 5
OBJECTS_TO_SHOW = 10

# Base-object types that placed references (REFR/ACHR) point at via NAME.
BASE_OBJECT_TYPES = {
    'STAT', 'MSTT', 'DOOR', 'FURN', 'LIGH', 'CONT', 'MISC', 'WEAP', 'ARMO',
    'ACTI', 'ALCH', 'BOOK', 'FLOR', 'TREE', 'INGR', 'KEYM', 'AMMO', 'SCRL',
    'SLGM', 'APPA', 'SOUN', 'TACT', 'NPC_', 'SPEL', 'HAZD', 'IDLM', 'ARTO',
}

plugin = Plugin.load_cached(SKYRIM_ESM, only_signatures={'CELL'} | BASE_OBJECT_TYPES)


def formid(data):
    return f"{struct.unpack_from('<I', data)[0]:08X}"


def rgba(data):
    return tuple(data[:3]) + ((data[3],) if len(data) > 3 else ())


def decode_xcll(d):
    (amb, dirc, fog_near_c, fog_near, fog_far, rot_xy, rot_z, fade, clip, power) = (
        rgba(d[0:4]), rgba(d[4:8]), rgba(d[8:12]), *struct.unpack_from('<ff', d, 12),
        *struct.unpack_from('<ii', d, 20), *struct.unpack_from('<fff', d, 28))
    out = {
        'ambient': amb, 'directional': dirc,
        'fog_near_color': fog_near_c, 'fog_near': fog_near, 'fog_far': fog_far,
        'directional_rotation_xy': rot_xy, 'directional_rotation_z': rot_z,
        'directional_fade': fade, 'fog_clip_distance': clip, 'fog_power': power,
    }
    if len(d) >= 92:
        out['ambient_cube'] = [rgba(d[o:o + 4]) for o in range(40, 64, 4)]
        out['specular'] = rgba(d[64:68])
        out['fresnel_power'] = struct.unpack_from('<f', d, 68)[0]
        out['fog_far_color'] = rgba(d[72:76])
        out['fog_max'], out['light_fade_begin'], out['light_fade_end'], inherits = \
            struct.unpack_from('<fffI', d, 76)
        out['inherits_flags'] = f"0x{inherits:X}"
    return out


DECODERS = {
    'EDID': lambda d: d.rstrip(b'\x00').decode('cp1252', 'replace'),
    'DATA': lambda d: f"flags=0x{int.from_bytes(d, 'little'):X}",
    'XCLC': lambda d: dict(zip(('grid_x', 'grid_y'), struct.unpack_from('<ii', d)),
                           force_hide_land=d[8] if len(d) > 8 else None),
    'XCLL': decode_xcll,
    'XCLW': lambda d: struct.unpack('<f', d)[0],
    'XCLR': lambda d: [formid(d[i:i + 4]) for i in range(0, len(d), 4)],
    'LTMP': formid, 'XCIM': formid, 'XLCN': formid, 'XCMO': formid,
    'XEZN': formid, 'XCAS': formid, 'XCCM': formid, 'XOWN': formid,
    'XILL': formid, 'XWCN': lambda d: struct.unpack('<I', d)[0],
    'XWCU': lambda d: struct.unpack('<' + 'f' * (len(d) // 4), d),
}


def describe(sub):
    data = sub.data
    if sub.signature == 'FULL':
        return None  # handled through record.full_name (localized string)
    try:
        return DECODERS[sub.signature](data)
    except KeyError:
        return f"<{len(data)} bytes> {data[:16].hex()}{'...' if len(data) > 16 else ''}"
    except Exception as e:
        return f"<decode error {e!r}> {data[:16].hex()}"


cells = list(plugin.get_records_by_signature('CELL'))
sig_counts = Counter(s.signature for c in cells for s in c.subrecords)

print(f"{len(cells)} cells. Subrecords present across all cells:")
for sig, n in sig_counts.most_common():
    print(f"  {sig}: {n}")

for cell in cells[:CELLS_TO_DUMP]:
    print(f"\n=== {cell.full_name or '(unnamed)'} [{cell.editor_id}] ===")
    print(f"form_id: {cell.form_id.value:08X}  flags: 0x{int(cell.flags):X}")
    print(f"name: {cell.full_name}")
    for sub in cell.subrecords:
        value = describe(sub)
        if value is None:
            continue
        if isinstance(value, dict):
            print(f"{sub.signature}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"{sub.signature}: {value}")


def cell_children(group, out=None):
    """Map each CELL record's FormID to its child group (persistent/temporary)."""
    out = {} if out is None else out
    items = group.records
    for i, item in enumerate(items):
        if hasattr(item, 'records'):
            cell_children(item, out)
        elif item.signature == 'CELL' and i + 1 < len(items):
            nxt = items[i + 1]
            if getattr(nxt, 'group_type', None) == 6:
                out[item.form_id.value] = nxt
    return out


def placed_objects(cell_group):
    """Yield (kind, record) for every placed object in a cell's child group."""
    for sub in cell_group.records:
        kind = {8: 'persistent', 9: 'temporary'}.get(sub.group_type, str(sub.group_type))
        for rec in sub.records:
            if hasattr(rec, 'signature'):
                yield kind, rec


def base_name(base_id):
    base = plugin.get_record_by_form_id(base_id)
    if base is None:
        return f"{base_id:08X} (not loaded)"
    return f"{base.signature} {base.editor_id or ''} {base.full_name or ''}".strip()


contents = {}
for group in plugin.groups:
    cell_children(group, contents)

print(f"\n\n##### Objects placed in cells ({len(contents)} cells with children) #####")
for cell in cells[:CELLS_TO_DUMP]:
    group = contents.get(cell.form_id.value)
    if group is None:
        continue
    objs = list(placed_objects(group))
    kinds = Counter(r.signature for _, r in objs)
    print(f"\n=== {cell.full_name or '(unnamed)'} [{cell.editor_id}]: {len(objs)} objects {dict(kinds)} ===")
    shown = 0
    for kind, rec in objs:
        if rec.signature not in ('REFR', 'ACHR'):
            continue
        name, data = rec.get_subrecord('NAME'), rec.get_subrecord('DATA')
        if name is None or data is None or len(data.data) < 24:
            continue
        x, y, z, rx, ry, rz = struct.unpack_from('<6f', data.data)
        scale = rec.get_subrecord('XSCL')
        scale_txt = f" scale={struct.unpack('<f', scale.data)[0]:.2f}" if scale else ""
        print(f"  [{kind[:4]}] {rec.form_id.value:08X} {base_name(struct.unpack('<I', name.data)[0])}"
              f" pos=({x:.1f}, {y:.1f}, {z:.1f}) rot=({rx:.2f}, {ry:.2f}, {rz:.2f}){scale_txt}")
        shown += 1
        if shown >= OBJECTS_TO_SHOW:
            print(f"  ... ({len(objs) - shown} more)")
            break
