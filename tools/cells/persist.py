"""Make a vanilla placed reference persistent so a quest alias can fill it and show a compass marker."""
import re

from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, resolve_target_path

PERSISTENT = 0x400


def find_reference(b, abs_id):
    """(reference record, owning CELL record) for an absolute form ID, searching the loaded plugins."""
    found = {}

    def visit(group, plugin, cell):
        for item in group.records:
            if isinstance(item, GroupRecord):
                visit(item, plugin, cell)
            elif item.signature == 'CELL':
                cell = item
            elif item.signature in ('REFR', 'ACHR') and b.m.absid(plugin, item.form_id.value) == abs_id:
                found['hit'] = (item, cell)

    for pl in b.m.plugins:
        loaded = Plugin.load_cached(str(pl.file_path), only_signatures={'CELL', 'WRLD', 'REFR', 'ACHR'})
        for g in loaded.groups:
            visit(g, loaded, None)
    return found.get('hit')


def persist_reference(b, abs_id, made):
    """Add a persistent override of a placed reference to the plugin held by b.target. Returns its local FormID."""
    plugin = b.target.plugin
    existing = b.target.find_matching(abs_id) if hasattr(b.target, 'find_matching') else None
    if existing is not None and int(existing.flags) & PERSISTENT:
        return existing.form_id.value
    hit = find_reference(b, abs_id)
    if hit is None or hit[1] is None:
        raise ValueError(f"reference {abs_id & 0xFFFFFF:06X} not found in a cell of the loaded plugins")
    ref, cell = hit
    if int(ref.flags) & PERSISTENT:
        return b.local(abs_id)
    info = b.cells.find(cell.editor_id or cell.full_name, None, None, None)
    over = b.override(plugin, ref)
    over.flags = Record('REFR', FormID(0), int(over.flags) | PERSISTENT).flags
    b.place_ref(plugin, info, over, True, made)
    return over.form_id.value


def make_reference_persistent(b, reference, target_plugin):
    """Tool body: persist one Skyrim.esm reference (hex ID) in target_plugin and save."""
    if not re.fullmatch(r'(0x)?[0-9a-fA-F]{1,8}', str(reference)):
        raise ValueError("reference must be a hex reference ID (see search_references)")
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    b.target, b.plugin = target, target.plugin
    made = []
    fid = persist_reference(b, int(str(reference), 16) & 0xFFFFFF, made)
    target.save()
    return {'path': str(path), 'persistent_reference': f"{fid:08X}", 'records': made}
