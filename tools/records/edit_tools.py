"""Edit existing records of an output plugin in place: text, conditions and compiled scripts; and diff plugins."""
import re
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.record import GroupRecord
from esplib.defs.condition_functions import get_condition_function
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


def dialogue_topics(plugin):
    """[(DIAL, [INFO])] for the plugin's own topics, in file order (INFOs follow their topic)."""
    topics, current = [], None

    def walk(items):
        nonlocal current
        for it in items:
            if isinstance(it, GroupRecord):
                walk(it.records)
            elif it.signature == 'DIAL':
                current = (it, [])
                topics.append(current)
            elif it.signature == 'INFO' and current is not None:
                current[1].append(it)
    walk(plugin.records)
    return [(d, i) for d, i in topics if is_own(plugin, d) or any(is_own(plugin, x) for x in i)]


def _rid(rec):
    return f"{rec.form_id.value & 0xFFFFFF:06X}"


def _find_topic(plugin, topic):
    """(DIAL, [INFO]) for a topic given by editor ID or hex object ID."""
    q = str(topic).strip().lower()
    for dial, infos in dialogue_topics(plugin):
        if (dial.editor_id or '').lower() == q or _rid(dial).lower() == q.lstrip('0x').rjust(6, '0')[-6:]:
            return dial, infos
    raise ValueError(f"topic {topic!r} not found in {plugin.file_path.name}")


def _quest_fid(plugin, quest):
    rec = find_record(plugin, quest)
    if rec.signature != 'QUST':
        raise ValueError(f"{quest!r} is not a quest")
    return rec.form_id.value


def list_texts(path, query=None, limit=50, record=None, type=None, sig=None, quest=None, offset=0):
    """Inline text subrecords of the plugin's own records.

    query: substring of the text or editor ID. record: one record by editor ID or hex ID. type: record type
    (INFO, DIAL, QUST...). sig: subrecord (NAM1, FULL...). quest: only the dialogue topics and responses of
    this quest. Dialogue responses carry their topic and its prompt. offset/limit page the result; a final
    {'truncated': True, 'next_offset': n} row says more is available."""
    plugin = Plugin.load(str(path))
    low = query.lower() if query else None
    want_rec = str(record).lower().strip() if record else None
    want_type = type.upper() if type else None
    want_sig = sig.upper() if sig else None
    owner = {}
    if quest:
        qf = _quest_fid(plugin, quest)
        keep = set()
        for dial, infos in dialogue_topics(plugin):
            q = dial.get_subrecord('QNAM')
            if q is not None and struct.unpack_from('<I', bytes(q.data))[0] == qf:
                keep.add(id(dial))
                keep.update(id(i) for i in infos)
        scope = keep
    else:
        scope = None
    for dial, infos in dialogue_topics(plugin):
        for i in infos:
            owner[id(i)] = dial
    rows = []
    for r in all_records(plugin):
        if not is_own(plugin, r) or (scope is not None and id(r) not in scope):
            continue
        if want_type and r.signature != want_type:
            continue
        if want_rec and want_rec not in ((r.editor_id or '').lower(), _rid(r).lower(),
                                         f"{r.form_id.value & 0xFFFFFF:08X}".lower()):
            continue
        counts = {}
        for s in r.subrecords:
            if s.signature not in TEXT_SIGS:
                continue
            i = counts[s.signature] = counts.get(s.signature, -1) + 1
            text = as_text(s.data)
            if want_sig and s.signature != want_sig:
                continue
            if text is None or (low and low not in text.lower() and low not in (r.editor_id or '').lower()):
                continue
            row = {'record': r.editor_id or _rid(r), 'type': r.signature, 'form_id': _rid(r), 'sig': s.signature,
                   'index': i, 'text': text}
            dial = owner.get(id(r))
            if dial is not None:
                row['topic'] = dial.editor_id or _rid(dial)
                prompt = dial.get_subrecord('FULL')
                row['prompt'] = as_text(prompt.data) if prompt is not None else None
            rows.append(row)
    page = rows[offset:offset + limit]
    if offset + limit < len(rows):
        page.append({'truncated': True, 'total': len(rows), 'next_offset': offset + limit})
    return page


def _apply_text(plugin, record, sig, text, find, replace, index):
    """Edit one text subrecord of a loaded plugin; returns (old, new)."""
    rec = find_record(plugin, record)
    return _apply_to(rec, record, sig, text, find, replace, index)


def _apply_to(rec, record, sig, text, find, replace, index):
    if (text is None) == (find is None):
        raise ValueError("give either text, or find and replace")
    if find is not None and replace is None:
        raise ValueError("find needs replace")
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


def _item_targets(plugin, item):
    """[(record, label)] an edit_texts item applies to.

    record: one record. topic (+ response): a dialogue topic by editor ID or hex ID; sig FULL edits the
    prompt, sig NAM1 the response at position `response` (default 0). match: every record holding a text
    subrecord equal to this exact text (use it for the same line repeated in several topics)."""
    if item.get('match') is not None:
        sig = item['sig'].upper()
        hits = []
        for r in all_records(plugin):
            if not is_own(plugin, r):
                continue
            if any(s.signature == sig and as_text(s.data) == item['match'] for s in r.subrecords):
                hits.append((r, r.editor_id or _rid(r)))
        if not hits:
            raise ValueError(f"no {sig} text equals {item['match']!r}")
        return hits
    if item.get('topic') is not None:
        dial, infos = _find_topic(plugin, item['topic'])
        if item['sig'].upper() == 'FULL':
            return [(dial, item['topic'])]
        pos = item.get('response', 0)
        if pos >= len(infos):
            raise ValueError(f"topic {item['topic']} has {len(infos)} response(s); response {pos} is out of range")
        return [(infos[pos], f"{item['topic']}[{pos}]")]
    return [(find_record(plugin, item['record']), item['record'])]


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
    """Apply several text edits with one load and one save. items: [{record | topic+response | match, sig,
    text | find+replace, index?}]. All edits are validated before anything is written; on any error nothing
    changes. For `match` items, `text` (or find+replace) is applied to every identical line."""
    plugin = Plugin.load(str(path))
    results = []
    for i, item in enumerate(items):
        label = item.get('record') or item.get('topic') or item.get('match')
        try:
            if 'sig' not in item:
                raise ValueError("sig is required")
            for rec, name in _item_targets(plugin, item):
                old, new = _apply_to(rec, name, item['sig'], item.get('text'), item.get('find'),
                                     item.get('replace'), item.get('index', 0))
                results.append(_text_summary(name, item['sig'], item.get('index', 0), old, new, verbose))
        except (KeyError, ValueError) as e:
            raise ValueError(f"item {i} ({label}): {e}") from e
    plugin.save(str(path))
    return results


def count_sentences(text):
    """Sentences in a line: runs ending in . ! ? or an ellipsis, ignoring tags like <Alias=Player> and (Persuade)."""
    clean = re.sub(r'<[^>]*>|\s\([^)]*\)\s*$', ' ', text)
    return len([p for p in re.split(r'(?<=[.!?…])["\')\]]*\s+', clean.strip()) if re.search(r'\w', p)])


def audit_dialogue_length(path, max_prompt=1, max_reply=3, quest=None):
    """Player prompts (DIAL FULL) longer than max_prompt sentences and NPC replies (all NAM1 lines of one INFO)
    longer than max_reply sentences, from the plugin's own topics. Returns {'ok', 'checked', 'issues'}."""
    plugin = Plugin.load(str(path))
    qf = _quest_fid(plugin, quest) if quest else None
    issues, checked = [], 0
    for dial, infos in dialogue_topics(plugin):
        if qf is not None:
            q = dial.get_subrecord('QNAM')
            if q is None or struct.unpack_from('<I', bytes(q.data))[0] != qf:
                continue
        name = dial.editor_id or _rid(dial)
        full = dial.get_subrecord('FULL')
        prompt = as_text(full.data) if full is not None else None
        if prompt and is_own(plugin, dial):
            checked += 1
            n = count_sentences(prompt)
            if n > max_prompt:
                issues.append({'kind': 'prompt', 'topic': name, 'form_id': _rid(dial), 'sentences': n, 'text': prompt})
        for pos, info in enumerate(infos):
            lines = [as_text(s.data) or '' for s in info.subrecords if s.signature == 'NAM1']
            if not any(lines) or not is_own(plugin, info):
                continue
            checked += 1
            n = sum(count_sentences(x) for x in lines)
            if n > max_reply:
                issues.append({'kind': 'reply', 'topic': name, 'response': pos, 'form_id': _rid(info),
                               'sentences': n, 'text': ' '.join(lines)})
    return {'ok': not issues, 'checked': checked, 'max_prompt_sentences': max_prompt,
            'max_reply_sentences': max_reply, 'issues': issues}


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
                    'or_next': bool(d[0] & 1), 'function': func,
                    'function_name': (get_condition_function('tes5', func) or ('?',))[0],
                    'param1': f"{p1:08X}", 'param2': f"{p2:08X}",
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
