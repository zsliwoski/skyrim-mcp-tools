"""Clone a vanilla record into a plugin under a new form ID and editor ID, with inline (non-localized) text."""
import re
import struct

from esplib.utils import FormID

from tools.npcs.npc_builder import Plan, z
from tools.core.plugin_edit import Target, hi, resolve_target_path

# Text subrecords per record type that can be overridden in place.
TEXT_SUBS = {'name': 'FULL', 'description': 'DESC'}
NON_PLAYABLE = 0x4


def clone_into(b, plugin, src, new_fid, edid, drop, extra=()):
    """Copy a vanilla record into the plugin under a new form ID, dropping localized strings."""
    rec = b.override(plugin, src)
    rec.form_id = FormID(new_fid)
    rec.subrecords[:] = [s for s in rec.subrecords if s.signature not in ('EDID',) + tuple(drop)]
    rec.add_subrecord('EDID', z(edid))
    for sig, data in extra:
        rec.add_subrecord(sig, data)
    return rec


def clone_record(b, source, target_plugin, editor_id, name=None, description=None, signature=None):
    """Clone a record from the loaded plugins into target_plugin (which must already exist)."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', editor_id or ''):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    sigs = [signature.upper()] if signature else sorted(b.m.by_sig)
    _, src = b.resolver.lookup(source, sigs)
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; text cannot be added inline")
    low = editor_id.lower()
    if any((r.editor_id or '').lower() == low for r in b.m.records.values()) or \
            any((r.editor_id or '').lower() == low for r in plugin.records):
        raise ValueError(f"editor_id {editor_id!r} already exists")

    p = Plan()
    p.clone = src
    b.target = target
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    b.plugin = plugin
    new_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    rec = b.override(plugin, src)
    rec.form_id = FormID(b.own(new_id))
    for s in rec.subrecords:
        if s.signature == 'EDID':
            s.data = z(editor_id)
    # a leftover 4-byte lstring ID would be read as inline text, so drop it
    rec.subrecords[:] = [s for s in rec.subrecords if not (src.signature == 'BOOK' and s.signature == 'CNAM'
                                                          and len(s.data) == 4)]
    for key, sub in TEXT_SUBS.items():
        value = {'name': name, 'description': description}[key]
        if value is None:
            continue
        hit = [s for s in rec.subrecords if s.signature == sub]
        if not hit:
            raise ValueError(f"{src.signature} {source} has no {sub} subrecord to set the {key} on")
        hit[0].data = z(value)
    if src.get_subrecord('VMAD') is not None:
        rec.remove_subrecords('VMAD')
    # vanilla NPC-only gear carries the Non-Playable flag, which keeps a clone out of the inventory
    cleared = src.signature in ('ARMO', 'WEAP') and bool(int(rec.flags) & NON_PLAYABLE)
    if cleared:
        value = int(rec.flags) & ~NON_PLAYABLE
        rec.flags = value if isinstance(rec.flags, int) else type(rec.flags)(value, rec.flags._names)
    plugin.add_record(rec)
    plugin.header.next_object_id = new_id + 1
    target.save()
    created = {'type': src.signature, 'editor_id': editor_id,
               'form_id': f"{rec.form_id.value:08X}", 'cloned_from': source}
    if cleared:
        created['cleared_flags'] = 'Non-Playable'
    return {'path': str(path), 'created': created, 'masters': list(plugin.header.masters)}
