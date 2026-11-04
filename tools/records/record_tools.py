"""Record-level helpers: diff two records, copy subrecords between them, remove records and their placements."""
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.defs.condition_functions import get_condition_function

from tools.core.plugin_edit import hi
from tools.records.maintenance import all_records

MGEF_DATA_FIELDS = {0: 'flags', 4: 'base cost', 8: 'associated item', 12: 'magic skill', 16: 'resist value',
                    24: 'casting light', 32: 'hit shader', 36: 'enchant shader', 64: 'archetype',
                    68: 'actor value', 72: 'projectile', 76: 'explosion', 80: 'cast type', 84: 'delivery',
                    96: 'casting art'}


def _hex(data, limit=64):
    h = bytes(data).hex()
    return h if len(h) <= limit * 2 else h[:limit * 2] + f'...(+{len(h) // 2 - limit} bytes)'


def find_any(plugin, ident):
    """A record of the plugin (own or override) by editor ID or hex ID, as a (record) or ValueError."""
    q = str(ident).strip().lower()
    hits = [r for r in all_records(plugin) if (r.editor_id or '').lower() == q
            or (len(q) in (6, 8) and all(c in '0123456789abcdef' for c in q)
                and r.form_id.value & 0xFFFFFF == int(q, 16) & 0xFFFFFF)]
    if not hits:
        raise ValueError(f"record {ident!r} not found in {plugin.file_path.name}")
    if len(hits) > 1:
        raise ValueError(f"{ident!r} matches several records: "
                         + ', '.join(f"{h.signature} {h.form_id.value:08X}" for h in hits))
    return hits[0]


def _words(a, b):
    """Differing 4-byte words of two equal-length blobs: [{offset, a, b}] (hex values)."""
    out = []
    for off in range(0, min(len(a), len(b)) - 3, 4):
        x, y = a[off:off + 4], b[off:off + 4]
        if x != y:
            out.append({'offset': off, 'a': x.hex(), 'b': y.hex()})
    return out


def _condition_text(data):
    d = bytes(data)
    if len(d) < 20:
        return _hex(d)
    func, p1 = struct.unpack_from('<H2xI', d, 8)
    name = (get_condition_function('tes5', func) or (f'function {func}',))[0]
    return f"{name} param1={p1:08X} op={d[0] >> 5} value={struct.unpack_from('<f', d, 4)[0]:g}"


def diff_records(path_a, ident_a, path_b, ident_b):
    """Field-level comparison of two records: subrecords only on one side, and differing subrecords with the
    changed 4-byte words (named for MGEF DATA). Form IDs are compared raw, so use records from plugins that
    share a master order (e.g. an output plugin and Skyrim.esm)."""
    ra = find_any(Plugin.load(str(path_a)), ident_a)
    rb = find_any(Plugin.load(str(path_b)), ident_b)
    out = {'a': f"{ra.signature} {ra.editor_id}", 'b': f"{rb.signature} {rb.editor_id}",
           'flags': None, 'only_in_a': [], 'only_in_b': [], 'different': []}
    if int(ra.flags) != int(rb.flags):
        out['flags'] = {'a': int(ra.flags), 'b': int(rb.flags)}
    by_sig = {}
    for side, rec in (('a', ra), ('b', rb)):
        for s in rec.subrecords:
            by_sig.setdefault(s.signature, {'a': [], 'b': []})[side].append(bytes(s.data))
    for sig, sides in by_sig.items():
        la, lb = sides['a'], sides['b']
        for i in range(max(len(la), len(lb))):
            tag = f"{sig}[{i}]" if max(len(la), len(lb)) > 1 else sig
            if i >= len(la):
                out['only_in_b'].append({'sig': tag, 'value': _condition_text(lb[i]) if sig == 'CTDA' else _hex(lb[i])})
            elif i >= len(lb):
                out['only_in_a'].append({'sig': tag, 'value': _condition_text(la[i]) if sig == 'CTDA' else _hex(la[i])})
            elif la[i] != lb[i]:
                if sig == 'CTDA':
                    out['different'].append({'sig': tag, 'a': _condition_text(la[i]), 'b': _condition_text(lb[i])})
                elif len(la[i]) == len(lb[i]) and len(la[i]) > 8:
                    words = _words(la[i], lb[i])
                    if ra.signature == 'MGEF' and sig == 'DATA':
                        for w in words:
                            w['field'] = MGEF_DATA_FIELDS.get(w['offset'], '?')
                    out['different'].append({'sig': tag, 'words': words})
                else:
                    out['different'].append({'sig': tag, 'a': _hex(la[i]), 'b': _hex(lb[i])})
    return out


def copy_subrecords(path, record, source_path, source_record, sigs=None, skip=(), keep=('EDID', 'VMAD', 'FULL')):
    """Replace subrecords of `record` in `path` with those of a record from a master such as Skyrim.esm.

    sigs: only copy these signatures (default all). skip: signatures never copied. keep: signatures of the
    target left untouched when not listed in sigs. Form IDs are copied as raw bytes, so the source must be
    the target's first master with no masters of its own (Skyrim.esm)."""
    plugin = Plugin.load(str(path))
    source = Plugin.load(str(source_path))
    masters = list(plugin.header.masters)
    if source.header.masters or not masters or masters[0].lower() != Path(source_path).name.lower():
        raise ValueError(f"{Path(source_path).name} must be the first master of {Path(path).name} and have no "
                         "masters of its own")
    target = find_any(plugin, record)
    src = find_any(source, source_record)
    if target.signature != src.signature:
        raise ValueError(f"{target.signature} and {src.signature} records do not match")
    skipped = {s.upper() for s in skip}
    if sigs:
        copy_sigs = {s.upper() for s in sigs} - skipped
    else:
        copy_sigs = {s.signature for s in src.subrecords} - skipped - {s.upper() for s in keep}
    copy_sigs.discard('EDID')
    copied = [(s.signature, bytes(s.data)) for s in src.subrecords if s.signature in copy_sigs]
    if not copied:
        raise ValueError('nothing to copy; check sigs against the source record')
    head_sigs = ('EDID', 'VMAD', 'FULL')
    head = [(s.signature, bytes(s.data)) for s in target.subrecords
            if s.signature in head_sigs and s.signature not in copy_sigs]
    rest = [(s.signature, bytes(s.data)) for s in target.subrecords
            if s.signature not in head_sigs and s.signature not in copy_sigs]
    target.clear_subrecords()
    for sig, data in head + copied + rest:
        target.add_subrecord(sig, data)
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    return {'record': target.editor_id, 'copied': sorted(copy_sigs), 'from': src.editor_id}


def set_fields(path, record, values):
    """Set named fields ({cast_type: 'concentration', ...}, see tools.records.fields) on an own record."""
    from tools.records.fields import encode_field
    plugin = Plugin.load(str(path))
    rec = find_any(plugin, record)
    plugin.header.version = 1.7
    for field, value in values.items():
        sub, offset, raw = encode_field(rec.signature, field, value)
        sr = rec.get_subrecord(sub)
        if sr is None:
            raise ValueError(f"{record} has no {sub} subrecord")
        buf = bytearray(sr.data)
        buf[offset:offset + len(raw)] = raw
        sr.data = buf
        sr.modified = True
        rec.modified = True
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    return {'record': record, 'set': values}


def drop_conditions(path, record, indexes):
    """Remove CTDA conditions (by index, see list_conditions) from an own record; returns what was removed."""
    plugin = Plugin.load(str(path))
    rec = find_any(plugin, record)
    conds = rec.get_subrecords('CTDA')
    bad = [i for i in indexes if not 0 <= i < len(conds)]
    if bad:
        raise ValueError(f"{record} has {len(conds)} condition(s); index {bad[0]} is out of range")
    removed = [{'index': i, 'condition': _condition_text(conds[i].data)} for i in sorted(set(indexes))]
    for i in sorted(set(indexes), reverse=True):
        rec.remove_subrecord(conds[i])
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    return {'record': record, 'removed': removed}


def remove_records(path, records):
    """Delete own records by editor ID or hex ID, plus any placed REFR/ACHR that points at them."""
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    targets = [find_any(plugin, r) for r in records]
    for t in targets:
        if hi(t.form_id.value) != own:
            raise ValueError(f"{t.editor_id or t.form_id.value:} is an override, not an own record")
    ids = {t.form_id.value for t in targets}
    refs = [r for r in all_records(plugin) if r.signature in ('REFR', 'ACHR') and r.get_subrecord('NAME') is not None
            and struct.unpack('<I', bytes(r.get_subrecord('NAME').data))[0] in ids]
    removed = [f"{r.signature} {r.editor_id or ''} {r.form_id.value:08X}".replace('  ', ' ') for r in targets + refs]
    for r in targets + refs:
        plugin.remove_record(r)
    dangling = sorted({f"{r.editor_id or r.signature} {s.signature}" for r in all_records(plugin)
                       for s in r.subrecords if len(s.data) == 4
                       and struct.unpack('<I', bytes(s.data))[0] in ids and s.signature != 'NAME'})
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    return {'removed': removed, 'still_referenced_by': dangling}
