"""Create an arbitrary new record in a plugin from a simple subrecord list."""
import re
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.npcs.npc_builder import fid_bytes, z
from tools.core.plugin_edit import Target, hi, resolve_target_path


def encode(item, builder, plan_ids):
    """Bytes for one subrecord spec: exactly one of text, hex, form, u32, u16, u8, i32, float, empty."""
    keys = [k for k in ('text', 'hex', 'form', 'u32', 'u16', 'u8', 'i32', 'float', 'empty') if k in item]
    if len(keys) != 1:
        raise ValueError(f"{item.get('sig')}: give exactly one of text, hex, form, u32, u16, u8, i32, float, empty")
    k, v = keys[0], item[keys[0]]
    if k == 'text':
        return z(str(v))
    if k == 'hex':
        return bytes.fromhex(re.sub(r'\s', '', str(v)))
    if k == 'form':
        fid, _ = builder.resolver.lookup(v, item.get('sigs') or list(builder.m.by_sig))
        plan_ids.add(fid)
        return ('form', fid)
    if k == 'empty':
        return b''
    fmt = {'u32': '<I', 'u16': '<H', 'u8': '<B', 'i32': '<i', 'float': '<f'}[k]
    return struct.pack(fmt, v)


def create_record(builder, target_plugin, signature, editor_id, subrecords, flags=0):
    """Add a record to target_plugin. subrecords: [{sig, <one value key>}] in file order (EDID is added first).

    A `form` value is an editor ID, name or form ID resolved against the loaded plugins (masters are added).
    """
    from tools.npcs.npc_builder import Plan
    sig = signature.upper()
    if not re.fullmatch(r'[A-Z_]{4}', sig):
        raise ValueError("signature must be four letters, e.g. MISC")
    if not re.fullmatch(r'[A-Za-z0-9_]+', str(editor_id)):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
    target = Target(builder.m, builder.names, path, create=True)
    plugin = target.plugin
    if any((r.editor_id or '').lower() == editor_id.lower() for r in builder.m.records.values()) or \
            any((r.editor_id or '').lower() == editor_id.lower() for r in plugin.records):
        raise ValueError(f"editor_id {editor_id!r} already exists")
    p, parts = Plan(), []
    for s in subrecords:
        if not re.fullmatch(r'[A-Z0-9_]{4}', str(s.get('sig', ''))):
            raise ValueError(f"bad subrecord signature {s.get('sig')!r}")
        parts.append((s['sig'], encode(s, builder, p.abs_ids)))
    builder.target, builder.plugin = target, plugin
    builder.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    oid = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    rec = Record(sig, FormID(builder.own(oid)), flags)
    rec.add_subrecord('EDID', z(editor_id))
    for name, data in parts:
        rec.add_subrecord(name, fid_bytes(builder.local(data[1])) if isinstance(data, tuple) else data)
    plugin.add_record(rec)
    plugin.header.next_object_id = oid + 1
    target.save()
    return {'path': str(path), 'record': builder.describe(rec), 'masters': list(plugin.header.masters)}
