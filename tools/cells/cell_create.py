"""Create a new interior cell in a plugin, from scratch or by cloning an existing interior cell."""
import re
import struct

from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.npcs.npc_builder import Plan, z
from tools.core.plugin_edit import Target, hi, resolve_target_path

INTERIOR = 0x0001
CANT_TRAVEL = 0x0004
PUBLIC = 0x0020


def default_xcll():
    """92-byte XCLL: warm, readable lighting so a from-scratch cell is not pitch black."""
    def rgba(r, g, b):
        return bytes((r, g, b, 0))
    return (rgba(120, 120, 120) + rgba(150, 140, 120) + rgba(60, 60, 70)
            + struct.pack('<ffiifff', 0.0, 3000.0, 0, 0, 1.0, 1000.0, 1.0)
            + rgba(110, 110, 110) * 6
            + rgba(0, 0, 0) + struct.pack('<f', 1.0)
            + rgba(60, 60, 70) + struct.pack('<fffI', 1.0, 0.0, 0.0, 0))


def interior_group(plugin, fid):
    """Group that holds an interior cell: CELL > block (last decimal digit) > sub-block (next digit)."""
    obj = fid & 0xFFFFFF
    top = next((g for g in plugin.groups if g.group_type == 0 and g.label == 'CELL'), None)
    if top is None:
        top = GroupRecord(0, 'CELL')
        plugin.groups.append(top)
    parent = top
    for gtype, label in ((2, obj % 10), (3, (obj // 10) % 10)):
        group = next((g for g in parent.records if isinstance(g, GroupRecord)
                      and g.group_type == gtype and g.label == label), None)
        if group is None:
            group = GroupRecord(gtype, label)
            parent.records.append(group)
        parent = group
    return parent


REF_SIGS = ('REFR', 'ACHR')
DROP_FROM_REFS = ('XTEL', 'VMAD')


def source_children(src_plugin, cell):
    """Persistent (8) and temporary (9) child groups of a cell, loaded with their placed references."""
    loaded = Plugin.load_cached(str(src_plugin.file_path), only_signatures={'CELL', 'WRLD', *REF_SIGS})
    found = []

    def visit(group):
        for item in group.records:
            if isinstance(item, GroupRecord):
                if item.group_type == 6 and item.label == cell.form_id.value:
                    found.extend(g for g in item.records if isinstance(g, GroupRecord))
                else:
                    visit(item)

    for g in loaded.groups:
        visit(g)
    return loaded, found


def copy_references(b, plugin, src_plugin, src_cell, new_cell, new_id):
    """Copy a cell's placed objects (not actors) under new form IDs; teleport links and scripts are dropped."""
    loaded, groups = source_children(src_plugin, src_cell)
    children = GroupRecord(6, new_cell.form_id.value)
    count = 0
    for g in groups:
        holder = GroupRecord(g.group_type, new_cell.form_id.value)
        for ref in g.records:
            if isinstance(ref, GroupRecord) or ref.signature != 'REFR':
                continue
            copy = b.override(plugin, ref)
            copy.form_id = FormID(b.own(new_id()))
            copy.subrecords[:] = [s for s in copy.subrecords if s.signature not in DROP_FROM_REFS]
            holder.records.append(copy)
            b.register(plugin, copy)
            count += 1
        children.records.append(holder)
    return children, count


def create_cell(b, target_plugin, editor_id, name=None, source=None, can_travel=True, copy_refs=True):
    """Add a new interior cell to target_plugin (which must exist); clone `source` (an interior cell) if given."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', editor_id or ''):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    if not source and not name:
        raise ValueError("name is required when not cloning a cell")
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; text cannot be added inline")
    low = editor_id.lower()
    if any((r.editor_id or '').lower() == low for r in b.m.records.values()) or \
            any((r.editor_id or '').lower() == low for r in plugin.records):
        raise ValueError(f"editor_id {editor_id!r} already exists")

    src = None
    p = Plan()
    if source:
        src_plugin, chain, src = b.cells.find(source)
        if any(t == 1 for t, _, _ in chain):
            raise ValueError(f"{source!r} is an exterior cell; only interior cells can be cloned")
        p.clone = src
    b.target = target
    b.plugin = plugin
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    new_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    counter = {'n': new_id + 1}

    def next_id():
        counter['n'] += 1
        return counter['n'] - 1
    fid = b.own(new_id)

    if src is not None:
        rec = b.override(plugin, src)
        rec.form_id = FormID(fid)
        rec.subrecords[:] = [s for s in rec.subrecords if s.signature not in ('EDID', 'VMAD')]
        rec.add_subrecord('EDID', z(editor_id))
        # EDID must lead; move it to the front
        rec.subrecords.insert(0, rec.subrecords.pop())
        if name is not None:
            hit = [s for s in rec.subrecords if s.signature == 'FULL']
            if hit:
                hit[0].data = z(name)
            else:
                rec.subrecords.insert(1, type(rec.subrecords[0])('FULL', z(name)))
    else:
        rec = Record('CELL', FormID(fid), 0)
        rec.add_subrecord('EDID', z(editor_id))
        rec.add_subrecord('FULL', z(name))
        rec.add_subrecord('DATA', struct.pack('<H', INTERIOR | PUBLIC))
        rec.add_subrecord('XCLL', default_xcll())
    data = rec.get_subrecord('DATA')
    if data is not None and len(data.data) >= 2:
        flags = struct.unpack_from('<H', data.data)[0] | INTERIOR
        flags = flags & ~CANT_TRAVEL if can_travel else flags | CANT_TRAVEL
        data.data = struct.pack('<H', flags) + data.data[2:]

    interior_group(plugin, fid).records.append(rec)
    b.register(plugin, rec)
    refs = 0
    if src is not None and copy_refs:
        children, refs = copy_references(b, plugin, src_plugin, src, rec, next_id)
        interior_group(plugin, fid).records.append(children)
    plugin.header.next_object_id = counter['n']
    target.save()
    return {'path': str(path), 'created': {'type': 'CELL', 'editor_id': editor_id, 'name': name or rec.full_name,
                                          'form_id': f"{rec.form_id.value:08X}", 'cloned_from': source,
                                          'references_copied': refs},
            'masters': list(plugin.header.masters),
            'note': 'Actors, navmesh and door teleports are not copied; use coc <editor_id> to enter the cell.'}
