"""Edit existing records of an output plugin in place: text, conditions and compiled scripts; and diff plugins."""
import re
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin

from tools.records.maintenance import all_records
from tools.papyrus.papyrus_build import compile_script
from tools.core.plugin_edit import hi

TEXT_SIGS = ('FULL', 'DESC', 'NAM1', 'NNAM', 'CNAM', 'RNAM', 'ITXT', 'SHRT')
OPERATORS = {'==': 0, '!=': 1, '>': 2, '>=': 3, '<': 4, '<=': 5}
OPERATOR_NAMES = {v: k for k, v in OPERATORS.items()}


def encode(text):
    """Null-terminated cp1252 bytes for an inline string subrecord."""
    return text.encode('cp1252', errors='replace') + b'\0'


def as_text(data):
    """Decode a subrecord as inline text, or None if it does not look like text."""
    data = bytes(data)
    if len(data) < 2 or not data.endswith(b'\0') or b'\0' in data[:-1]:
        return None
    try:
        text = data[:-1].decode('cp1252')
    except UnicodeDecodeError:
        return None
    return text if all(c.isprintable() or c in '\r\n\t' for c in text) else None


def is_own(plugin, rec):
    """True if the record is defined by this plugin rather than one of its masters."""
    return hi(rec.form_id.value) == len(plugin.header.masters)


def find_record(plugin, ident):
    """A record of this plugin by editor ID or by hex object ID (needed for dialogue responses)."""
    q = str(ident).strip()
    raw = int(q, 16) & 0xFFFFFF if re.fullmatch(r'(0x)?[0-9a-fA-F]{6,8}', q) and \
        (q.lower().startswith('0x') or len(q) in (6, 8)) else None
    hits = [r for r in all_records(plugin) if is_own(plugin, r) and (
        (raw is not None and r.form_id.value & 0xFFFFFF == raw) or (r.editor_id or '').lower() == q.lower())]
    if not hits:
        raise ValueError(f"record {ident!r} not found in {plugin.file_path.name} (own records only)")
    if len(hits) > 1:
        raise ValueError(f"{ident!r} matches several records: " + ', '.join(f"{h.signature} {h.form_id.value:08X}" for h in hits))
    return hits[0]


def list_texts(path, query=None, limit=50):
    """Inline text subrecords of the plugin's own records, optionally filtered by a substring."""
    plugin = Plugin.load(str(path))
    low = query.lower() if query else None
    out = []
    for r in all_records(plugin):
        if not is_own(plugin, r):
            continue
        counts = {}
        for s in r.subrecords:
            if s.signature not in TEXT_SIGS:
                continue
            i = counts[s.signature] = counts.get(s.signature, -1) + 1
            text = as_text(s.data)
            if text is None or (low and low not in text.lower() and low not in (r.editor_id or '').lower()):
                continue
            out.append({'record': r.editor_id or f"{r.form_id.value & 0xFFFFFF:06X}", 'type': r.signature,
                        'form_id': f"{r.form_id.value & 0xFFFFFF:06X}", 'sig': s.signature, 'index': i,
                        'text': text})
            if len(out) >= limit:
                return out
    return out


def _apply_text(plugin, record, sig, text, find, replace, index):
    """Edit one text subrecord of a loaded plugin; returns (old, new)."""
    if (text is None) == (find is None):
        raise ValueError("give either text, or find and replace")
    if find is not None and replace is None:
        raise ValueError("find needs replace")
    rec = find_record(plugin, record)
    subs = [s for s in rec.subrecords if s.signature == sig.upper()]
    if index >= len(subs):
        raise ValueError(f"{record} has {len(subs)} {sig} subrecord(s); index {index} is out of range")
    old = as_text(subs[index].data)
    if old is None:
        raise ValueError(f"{record} {sig}[{index}] is not inline text")
    if find is not None:
        if find not in old:
            raise ValueError(f"{find!r} not found in {sig}[{index}] of {record}")
        new = old.replace(find, replace)
    else:
        new = text
    subs[index].data = encode(new)
    rec.modified = True
    return old, new


def _text_summary(record, sig, index, old, new, verbose):
    """Edit report with char/word counts; includes the full before and after text only when verbose."""
    out = {'record': record, 'sig': sig.upper(), 'index': index, 'chars_before': len(old), 'chars_after': len(new),
           'words_after': len(re.sub(r'<[^>]+>|\[pagebreak\]', ' ', new).split())}
    if verbose:
        out.update(before=old, after=new)
    return out


def edit_text(path, record, sig, text=None, find=None, replace=None, index=0, verbose=False):
    """Set a text subrecord, or replace a substring in it. Returns a short summary (before/after if verbose)."""
    plugin = Plugin.load(str(path))
    old, new = _apply_text(plugin, record, sig, text, find, replace, index)
    plugin.save(str(path))
    return _text_summary(record, sig, index, old, new, verbose)


def edit_texts(path, items, verbose=False):
    """Apply several text edits with one load and one save. items: [{record, sig, text | find+replace, index?}].
    All edits are validated before anything is written; on any error nothing changes."""
    plugin = Plugin.load(str(path))
    results = []
    for i, item in enumerate(items):
        try:
            old, new = _apply_text(plugin, item['record'], item['sig'], item.get('text'), item.get('find'),
                                   item.get('replace'), item.get('index', 0))
        except (KeyError, ValueError) as e:
            raise ValueError(f"item {i} ({item.get('record')}): {e}") from e
        results.append(_text_summary(item['record'], item['sig'], item.get('index', 0), old, new, verbose))
    plugin.save(str(path))
    return results


def list_conditions(path, record):
    """Decoded CTDA conditions of a record (operator, value, function index, params)."""
    plugin = Plugin.load(str(path))
    rec = find_record(plugin, record)
    out = []
    for i, s in enumerate(x for x in rec.subrecords if x.signature == 'CTDA'):
        d = bytes(s.data)
        op = d[0] >> 5
        func, p1, p2, run_on = struct.unpack_from('<H2xIII', d, 8)
        out.append({'index': i, 'operator': OPERATOR_NAMES.get(op, str(op)), 'value': struct.unpack_from('<f', d, 4)[0],
                    'or_next': bool(d[0] & 1), 'function': func, 'param1': f"{p1:08X}", 'param2': f"{p2:08X}",
                    'run_on': run_on})
    return out


def edit_condition(path, record, index, operator=None, value=None):
    """Change the comparison operator and/or value of one CTDA condition (e.g. a quest stage threshold)."""
    if operator is None and value is None:
        raise ValueError("give operator and/or value")
    if operator is not None and operator not in OPERATORS:
        raise ValueError(f"operator must be one of {', '.join(OPERATORS)}")
    plugin = Plugin.load(str(path))
    rec = find_record(plugin, record)
    conds = [s for s in rec.subrecords if s.signature == 'CTDA']
    if not 0 <= index < len(conds):
        raise ValueError(f"{record} has {len(conds)} condition(s); index {index} is out of range")
    before = list_conditions(path, record)[index]
    d = bytearray(conds[index].data)
    if operator is not None:
        d[0] = (d[0] & 0x1F) | (OPERATORS[operator] << 5)
    if value is not None:
        struct.pack_into('<f', d, 4, float(value))
    conds[index].data = bytes(d)
    rec.modified = True
    plugin.save(str(path))
    return {'record': record, 'index': index, 'before': before, 'after': list_conditions(path, record)[index]}


def script_source_path(path, script):
    """Path of Scripts/Source/<script>.psc next to the plugin; rejects anything but a plain script name."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', script):
        raise ValueError("script must be a script name")
    return Path(path).parent / 'Scripts' / 'Source' / f"{script}.psc"


def patch_source(source, find, replace):
    """Replace every occurrence of find in the source; raises ValueError if it is absent."""
    if find not in source:
        raise ValueError(f"{find!r} not found in the script source")
    return source.replace(find, replace)


def edit_script(path, script, source=None, find=None, replace=None):
    """Read a script's source, or replace/patch it and recompile its .pex."""
    psc = script_source_path(path, script)
    have = psc.read_text(encoding='cp1252') if psc.is_file() else None
    if source is None and find is None:
        if have is None:
            raise ValueError(f"no source for {script}; pass the full source")
        return {'script': script, 'source': have}
    if source is None:
        if have is None:
            raise ValueError(f"no source for {script}; pass the full source")
        if replace is None:
            raise ValueError("find needs replace")
        source = patch_source(have, find, replace)
    try:
        pex = compile_script(script, source, psc.parent.parent)
    except Exception:
        if have is not None:
            psc.write_text(have, encoding='cp1252')
        raise
    return {'script': script, 'pex': str(pex), 'source': source}


def diff_plugins(path, other):
    """Records added, removed and changed in `path` relative to `other` (the older plugin)."""
    def load(p):
        pl = Plugin.load(str(p))
        return {r.form_id.value: r for r in all_records(pl)}

    new, old = load(path), load(other)

    def subs(r):
        return [(s.signature, bytes(s.data)) for s in r.subrecords]

    def label(r):
        return {'type': r.signature, 'form_id': f"{r.form_id.value:08X}", 'editor_id': r.editor_id}

    def show(v):
        if v is None:
            return None
        t = as_text(v[1])
        return t if t is not None else v[1].hex()

    added = [label(new[k]) for k in new if k not in old]
    removed = [label(old[k]) for k in old if k not in new]
    changed = []
    for k in new:
        if k not in old or subs(new[k]) == subs(old[k]):
            continue
        a, b = subs(old[k]), subs(new[k])
        diffs = []
        for i in range(max(len(a), len(b))):
            x, y = (a[i] if i < len(a) else None), (b[i] if i < len(b) else None)
            if x != y:
                diffs.append({'sig': (y or x)[0], 'position': i, 'before': show(x), 'after': show(y)})
        changed.append({**label(new[k]), 'subrecords': diffs})
    return {'added': added, 'removed': removed, 'changed': changed,
            'summary': f"{len(added)} added, {len(removed)} removed, {len(changed)} changed"}
