"""Plugin refactoring: list records, find what points at them, rename scripts and move records between plugins."""
import re
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.vmad import VmadData

from tools.core.plugin_edit import hi, new_plugin, vmad_script_names
from tools.papyrus.papyrus_build import compile_script
from tools.records.maintenance import all_records
from tools.records.record_tools import find_any, remove_records

# Subrecords that hold text, where four bytes can look like a form ID by accident.
TEXT_SIGS = {'EDID', 'FULL', 'DESC', 'NAM1', 'MODL', 'MODT', 'MODS', 'ICON', 'MICO', 'NNAM', 'ITXT', 'CNAM'}
PLACED = ('REFR', 'ACHR', 'ACRE')
# Records that live inside cells, dialogue or worldspaces and cannot be moved on their own.
NOT_MOVABLE = set(PLACED) | {'CELL', 'WRLD', 'DIAL', 'INFO', 'NAVM', 'LAND', 'DLBR', 'SCEN'}
NAME_RX = r'[A-Za-z][A-Za-z0-9_]*'


def _oid(rec):
    return rec.form_id.value & 0xFFFFFF


def _own_records(plugin):
    own = len(plugin.header.masters)
    return [r for r in all_records(plugin) if hi(r.form_id.value) == own]


def _name(rec):
    sr = rec.get_subrecord('FULL')
    if sr is None or len(sr.data) == 4 and not bytes(sr.data).endswith(b'\0'):
        return ''
    return bytes(sr.data).split(b'\0')[0].decode('cp1252', 'replace')


def _targets_in(rec, own, ids):
    """Form IDs from `ids` that appear as four-byte values in the record's non-text subrecords."""
    found = set()
    for s in rec.subrecords:
        if s.signature in TEXT_SIGS or len(s.data) < 4:
            continue
        d = bytes(s.data)
        for i in range(len(d) - 3):
            if d[i + 3] == own:
                v = struct.unpack_from('<I', d, i)[0]
                if v in ids:
                    found.add(v)
    return found


def resolve_seeds(plugin, records=None, query=None):
    """Own records picked by editor ID / hex ID, by editor-ID substring, or both."""
    own = len(plugin.header.masters)
    picked = {}
    for ident in records or ():
        r = find_any(plugin, ident)
        if hi(r.form_id.value) != own:
            raise ValueError(f"{ident} is an override, not an own record")
        picked[r.form_id.value] = r
    if query:
        q = query.lower()
        for r in _own_records(plugin):
            if q in (r.editor_id or '').lower() and r.signature not in NOT_MOVABLE:
                picked.setdefault(r.form_id.value, r)
    if not picked:
        raise ValueError('no records matched; give records or a query')
    return list(picked.values())


def list_records(path, query=None, signature=None, min_id=None, max_id=None, limit=100):
    """Own records of a plugin: form ID, type, editor ID and name, filtered by editor-ID/name substring,
    record type and object-ID range. Returns the matches and a count per type."""
    plugin = Plugin.load(str(path))
    q = (query or '').lower()
    sig = (signature or '').upper()
    rows = []
    for r in _own_records(plugin):
        oid = _oid(r)
        if sig and r.signature != sig or min_id is not None and oid < min_id or max_id is not None and oid > max_id:
            continue
        name = _name(r)
        if q and q not in (r.editor_id or '').lower() and q not in name.lower():
            continue
        rows.append({'form_id': f'{r.form_id.value:08X}', 'type': r.signature, 'editor_id': r.editor_id or '',
                     'name': name})
    by_type = {}
    for row in rows:
        by_type[row['type']] = by_type.get(row['type'], 0) + 1
    return {'total': len(rows), 'by_type': by_type, 'records': rows[:limit]}


def _script_hits(path, plugin_name, ids):
    """Lines of loose .psc sources that name the plugin and one of the object IDs (GetFormFromFile calls)."""
    src = Path(path).parent / 'Scripts' / 'Source'
    hits = []
    if not src.is_dir():
        return hits
    rx = re.compile('|'.join(rf'0x0*{i:X}\b' for i in sorted(ids)), re.I) if ids else None
    for f in sorted(src.glob('*.psc')):
        text = f.read_text(errors='replace')
        if rx is None or plugin_name.lower() not in text.lower():
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({'file': f.name, 'line': n, 'text': line.strip()})
    return hits


def find_dependents(path, records=None, query=None, include_dependencies=False):
    """What a set of own records points at and what points at them.

    referenced_by: other records holding their form IDs (placed references listed under `placed`).
    refers_to: own records the set points at that are not in the set. include_dependencies adds those to the
    set, repeatedly, so the result is the full group that must travel together.
    scripts: .psc lines in Scripts/Source that look a record up by object ID in this plugin.
    """
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    seeds = {r.form_id.value: r for r in resolve_seeds(plugin, records, query)}
    others = {r.form_id.value: r for r in _own_records(plugin)}
    refers = {}
    while True:
        found = {}
        for rec in seeds.values():
            for t in _targets_in(rec, own, set(others) - set(seeds)):
                if others[t].signature not in NOT_MOVABLE:
                    found[t] = others[t]
        if not include_dependencies:
            refers = found
            break
        if not found:
            break
        refers.update(found)
        seeds.update(found)
    ids = set(seeds)
    referenced, placed = [], []
    for r in all_records(plugin):
        if r.form_id.value in ids:
            continue
        hit = _targets_in(r, own, ids)
        if not hit:
            continue
        row = {'record': r.editor_id or f'{r.form_id.value:08X}', 'type': r.signature,
               'points_at': sorted(f'{seeds[h].editor_id or hex(h)}' for h in hit)}
        (placed if r.signature in PLACED else referenced).append(row)
    return {'records': sorted(f"{r.signature} {r.editor_id or f'{r.form_id.value:08X}'}" for r in seeds.values()),
            'referenced_by': referenced, 'placed': placed,
            'refers_to': sorted(f"{r.signature} {r.editor_id or f'{r.form_id.value:08X}'}" for r in refers.values()),
            'scripts': _script_hits(path, Path(path).name, {_oid(r) for r in seeds.values()})}


def _retarget_scripts(rec, mapping):
    """Rename scripts attached to a record's VMAD (case-insensitive); returns the names changed."""
    sr = rec.get_subrecord('VMAD')
    if sr is None:
        return []
    vm = VmadData.parse(bytes(sr.data), rec.signature)
    changed = []
    holders = list(vm.scripts)
    for a in vm.alias_scripts:
        holders += list(a.scripts)
    for s in holders:
        new = mapping.get(s.name.lower())
        if new:
            changed.append(s.name)
            s.name = new
    if changed:
        sr.data = bytearray(vm.to_bytes(rec.signature))
        sr.modified = True
        rec.modified = True
    return changed


def _check_script_names(mapping):
    for old, new in mapping.items():
        if not re.fullmatch(NAME_RX, new) or not re.fullmatch(NAME_RX, old):
            raise ValueError("script names may contain only letters, digits and underscores")
    if len({v.lower() for v in mapping.values()}) != len(mapping):
        raise ValueError('two scripts cannot be renamed to the same name')


def _build_renamed_script(old, new, from_dir, to_dir, plugin_swap=None):
    """Compile `old`'s source under the name `new` (and a different plugin name if given) into to_dir."""
    src = Path(from_dir) / 'Source' / f'{old}.psc'
    if not src.is_file():
        raise FileNotFoundError(f'source of script {old} not found: {src}')
    text = re.sub(rf'\b{re.escape(old)}\b', new, src.read_text(errors='replace'), flags=re.I)
    if plugin_swap:
        text = re.sub(re.escape(plugin_swap[0]), plugin_swap[1], text, flags=re.I)
    return compile_script(new, text, str(to_dir))


def _delete_script(scripts_dir, name):
    for f in (Path(scripts_dir) / f'{name}.pex', Path(scripts_dir) / 'Source' / f'{name}.psc'):
        f.unlink(missing_ok=True)


def rename_script(path, old, new):
    """Rename a Papyrus script everywhere in an output plugin: VMAD on every record, and the compiled
    Scripts/<new>.pex plus Scripts/Source/<new>.psc (the old files are deleted). Fragment scripts (QF_/TIF_/SF_/PF_)
    are named after their record and cannot be renamed."""
    _check_script_names({old: new})
    plugin = Plugin.load(str(path))
    if old.lower() not in {n.lower() for n in vmad_script_names(plugin)}:
        raise ValueError(f'no record in {Path(path).name} attaches a script named {old}')
    if re.match(r'(QF|TIF|PF|SF|TRIG)_', old, re.I):
        raise ValueError(f'{old} is a fragment script, named after its record; it cannot be renamed')
    scripts_dir = Path(path).parent / 'Scripts'
    pex = _build_renamed_script(old, new, scripts_dir, scripts_dir)
    records = []
    for r in all_records(plugin):
        if _retarget_scripts(r, {old.lower(): new}):
            records.append(r.editor_id or f'{r.form_id.value:08X}')
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    if old.lower() != new.lower():
        _delete_script(scripts_dir, old)
    return {'script': str(pex), 'records_updated': records}


def move_records(source_path, target_path, records=None, query=None, rename_prefix=None, script_renames=None,
                 remove_from_source=True, allow_dangling=False):
    """Move own records from one output plugin to another (created if missing), keeping their form IDs.

    rename_prefix: {"from": "GCG_", "to": "MT_"} rewrites the start of each editor ID. script_renames:
    {"OldScript": "NewScript"} renames scripts the moved records attach; the source is recompiled for the
    target plugin's file name. remove_from_source deletes the originals and every reference placed in a cell
    (re-place items in the new plugin afterwards). The two plugins must have identical master lists. Fails if
    a moved record points at an own record that stays behind, unless allow_dangling.
    """
    source_path, target_path = Path(source_path), Path(target_path)
    if source_path.resolve() == target_path.resolve():
        raise ValueError('source and target are the same plugin')
    mapping = {k.lower(): v for k, v in (script_renames or {}).items()}
    _check_script_names({k: v for k, v in (script_renames or {}).items()})
    src = Plugin.load(str(source_path))
    own = len(src.header.masters)
    seeds = resolve_seeds(src, records, query)
    bad = [r.editor_id or f'{r.form_id.value:08X}' for r in seeds if r.signature in NOT_MOVABLE]
    if bad:
        raise ValueError(f"cannot move {', '.join(bad)}: placed references, cells and dialogue are not movable")
    seed_ids = {r.form_id.value for r in seeds}
    stay = {r.form_id.value: r for r in _own_records(src) if r.form_id.value not in seed_ids}
    left = sorted({stay[t].editor_id or f'{t:08X}' for r in seeds for t in _targets_in(r, own, set(stay))
                   if stay[t].signature not in PLACED})
    if left and not allow_dangling:
        raise ValueError('moved records point at records that stay in the source: ' + ', '.join(left)
                         + ' (move them too, or pass allow_dangling)')
    if target_path.exists():
        dst = Plugin.load(str(target_path))
        if [m.lower() for m in dst.header.masters] != [m.lower() for m in src.header.masters]:
            raise ValueError('source and target must have the same master list')
        taken_ids = {r.form_id.value for r in all_records(dst)}
        taken_eids = {(r.editor_id or '').lower() for r in all_records(dst)}
    else:
        dst = new_plugin(str(target_path))
        dst.header.masters = list(src.header.masters)
        dst.header.master_sizes = [0] * len(src.header.masters)
        taken_ids, taken_eids = set(), set()
    prefix = rename_prefix or {}
    copies = []
    for r in seeds:
        rec = r.copy()
        eid = rec.editor_id or ''
        if prefix.get('from') and eid.startswith(prefix['from']):
            eid = prefix.get('to', '') + eid[len(prefix['from']):]
            rec.editor_id = eid
        if rec.form_id.value in taken_ids or eid.lower() in taken_eids:
            raise ValueError(f'{target_path.name} already has a record {eid} or form ID {rec.form_id.value:08X}')
        copies.append(rec)
    scripts_dir = source_path.parent / 'Scripts'
    built = {}
    if mapping:
        used = {n.lower(): n for n in vmad_script_names(src)}
        for old_l, new in mapping.items():
            if old_l not in used:
                raise ValueError(f'no record in {source_path.name} attaches a script named {old_l}')
            built[used[old_l]] = new
        for old, new in built.items():
            _build_renamed_script(old, new, scripts_dir, target_path.parent / 'Scripts',
                                  (source_path.name, target_path.name))
    for rec in copies:
        _retarget_scripts(rec, mapping)
        dst.add_record(rec)
    dst.header.version = 1.7
    dst.modified = True
    dst.save(str(target_path))
    out = {'moved': [f"{r.signature} {r.editor_id}" for r in copies], 'scripts_built': list(built.values()),
           'removed_from_source': [], 'still_referenced_by': []}
    if remove_from_source:
        res = remove_records(source_path, [f'{r.form_id.value:08X}' for r in seeds])
        out['removed_from_source'], out['still_referenced_by'] = res['removed'], res['still_referenced_by']
        remaining = {n.lower() for n in vmad_script_names(Plugin.load(str(source_path)))}
        for old in built:
            if old.lower() not in remaining:
                _delete_script(scripts_dir, old)
    return out
