"""Stable form-ID registry for a plugin project, kept in output/<Plugin>/ids.json.

Saves point at a plugin's records by their object ID, so an ID must never change meaning. IDs are handed out in
increasing order and never reused: the registry keeps the highest ID ever given out, so deleting records or
rebuilding a plugin cannot hand an old ID to something new. `sync` records every own record; `drift` reports where
the plugin no longer matches the registry (a record gone, renumbered, or changed type).
"""
import json
from pathlib import Path

FIRST_ID = 0x800


def project_dir(plugin_path):
    p = Path(plugin_path)
    return p.parent / p.stem


def registry_path(plugin_path):
    return project_dir(plugin_path) / 'ids.json'


def _empty():
    return {'next': FIRST_ID, 'ids': {}, 'retired': []}


def load(plugin_path):
    path = registry_path(plugin_path)
    if not path.is_file():
        return _empty()
    data = json.loads(path.read_text(encoding='utf-8'))
    return {**_empty(), **data}


def save(plugin_path, reg):
    path = registry_path(plugin_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = {'next': reg['next'], 'ids': dict(sorted(reg['ids'].items())), 'retired': sorted(set(reg['retired']))}
    path.write_text(json.dumps(reg, indent=1) + '\n', encoding='utf-8')


def _raise_floor(plugin, path):
    if path and registry_path(path).is_file():
        plugin.header.next_object_id = max(plugin.header.next_object_id, load(path)['next'])
    return plugin


def install():
    """Make every tool respect the registry without changing its allocator: a plugin loaded or created through
    esplib starts with its header counter at the registry's high-water mark, and saving raises the mark."""
    from esplib import Plugin
    if getattr(Plugin, '_id_registry_installed', False):
        return
    load_orig, new_orig, save_orig = Plugin.load.__func__, Plugin.new_plugin.__func__, Plugin.save

    def load_hooked(cls, file_path, *a, **kw):
        return _raise_floor(load_orig(cls, file_path, *a, **kw), file_path)

    def new_hooked(cls, file_path, *a, **kw):
        return _raise_floor(new_orig(cls, file_path, *a, **kw), file_path)

    def save_hooked(self, file_path=None, *a, **kw):
        out = save_orig(self, file_path, *a, **kw)
        path = file_path or getattr(self, 'file_path', None)
        if path and registry_path(path).is_file():
            reg = load(path)
            if self.header.next_object_id > reg['next']:
                reg['next'] = self.header.next_object_id
                save(path, reg)
        return out

    Plugin.load, Plugin.new_plugin, Plugin.save = (classmethod(load_hooked), classmethod(new_hooked), save_hooked)
    Plugin._id_registry_installed = True


def _own_records(plugin):
    from tools.core.plugin_edit import hi
    from tools.records.maintenance import all_records
    own = len(plugin.header.masters)
    for rec in all_records(plugin):
        if hi(rec.form_id.value) == own:
            yield rec.form_id.value & 0xFFFFFF, rec


def _key(oid):
    return f"{oid:06X}"


def sync(plugin_path):
    """Record every own record of the plugin in the registry and raise its high-water mark. Returns counts."""
    from esplib import Plugin
    plugin = Plugin.load(str(plugin_path))
    reg = load(plugin_path)
    added = 0
    top = max(plugin.header.next_object_id, FIRST_ID)
    for oid, rec in _own_records(plugin):
        top = max(top, oid + 1)
        key = _key(oid)
        if key not in reg['ids']:
            added += 1
        reg['ids'][key] = {'sig': rec.signature, 'edid': rec.editor_id or ''}
    reg['next'] = max(reg['next'], top)
    save(plugin_path, reg)
    return {'registry': str(registry_path(plugin_path)), 'records': len(reg['ids']), 'added': added,
            'next_id': f"{reg['next']:06X}"}


def drift(plugin_path, plugin=None):
    """Differences between the plugin and its registry: records missing, changed type or editor ID, or
    carrying an ID that was never registered. Empty means the IDs are stable."""
    from esplib import Plugin
    plugin = plugin or Plugin.load(str(plugin_path))
    reg = load(plugin_path)
    retired = set(reg['retired'])
    now = {_key(oid): rec for oid, rec in _own_records(plugin)}
    problems = []
    by_edid = {}
    for key, rec in now.items():
        if rec.editor_id:
            by_edid.setdefault(rec.editor_id, key)
    for key, info in reg['ids'].items():
        rec = now.get(key)
        if rec is None:
            if key in retired:
                continue
            moved = by_edid.get(info['edid']) if info['edid'] else None
            problems.append({'kind': 'renumbered' if moved else 'missing', 'id': key, 'sig': info['sig'],
                             'editor_id': info['edid'], **({'now': moved} if moved else {})})
        elif rec.signature != info['sig']:
            problems.append({'kind': 'sig_changed', 'id': key, 'was': info['sig'], 'now': rec.signature,
                             'editor_id': info['edid']})
        elif info['edid'] and rec.editor_id != info['edid']:
            problems.append({'kind': 'edid_changed', 'id': key, 'was': info['edid'], 'now': rec.editor_id})
    for key, rec in now.items():
        if key not in reg['ids']:
            problems.append({'kind': 'unregistered', 'id': key, 'sig': rec.signature,
                             'editor_id': rec.editor_id or ''})
        elif int(key, 16) >= reg['next']:
            problems.append({'kind': 'above_high_water', 'id': key})
    return problems


def retire(plugin_path, object_ids):
    """Mark IDs as retired: the record is gone on purpose and the ID will not come back."""
    reg = load(plugin_path)
    for oid in object_ids:
        key = _key(oid)
        if key in reg['ids']:
            reg['retired'].append(key)
        reg['next'] = max(reg['next'], oid + 1)
    save(plugin_path, reg)
    return sorted(set(reg['retired']))
