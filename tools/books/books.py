"""Book helpers: vanilla book models, readable vanilla text, kind-based model switching and vanilla-style markup."""
import re
import statistics
import struct
from collections import defaultdict

MODEL_DIR = "Clutter\\Books\\"

# variant name -> model file; skill books (BasicBook06a/07a) and spell tomes are deliberately absent
VARIANTS = {
    'Note01': 'Note01.nif', 'Note02': 'Note02.nif', 'Note03': 'Note03.nif', 'Note04': 'Note04.nif',
    'NoteBlood': 'Note01Blood.nif', 'NoteTornPaper': 'NoteTornPaper.nif',
    'Journal01': 'JournalLowPoly01.nif', 'Journal02': 'JournalLowPoly02.nif', 'Journal03': 'JournalLowPoly03.nif',
    'BasicBook01': 'BasicBook01.nif', 'BasicBook03': 'BasicBook03.nif', 'BasicBook03a': 'BasicBook03a.nif',
    'BasicBook04': 'BasicBook04.nif', 'BasicBook04a': 'BasicBook04a.nif', 'BasicBook05': 'BasicBook05.nif',
    'BasicBook05a': 'BasicBook05a.nif', 'BasicBook06': 'BasicBook06.nif', 'BasicBook07': 'BasicBook07.nif',
}
KINDS = {
    'note': {'default': 'Note03', 'words': (40, 100), 'value': 0, 'weight': 0.0, 'handwritten': True},
    'journal': {'default': 'Journal01', 'words': (100, 450), 'value': 10, 'weight': 1.0, 'handwritten': True},
    'book': {'default': 'BasicBook05', 'words': (250, 1000), 'value': 20, 'weight': 1.0, 'handwritten': False},
}
DROP_CAP = "<img src='img://Textures/Interface/Books/Illuminated_Letters/{}_letter.png'>"


def kind_of_model(model_path):
    """'note', 'journal' or 'book' for a MODL path, or None for spell tomes and other models."""
    name = (model_path or '').replace('/', '\\').rsplit('\\', 1)[-1].lower()
    if name.startswith('note'):
        return 'note'
    if 'journal' in name:
        return 'journal'
    if name.startswith('basicbook'):
        return 'book'
    return None


def variant_model(variant):
    """Full model path for a named book variant; raises ValueError for unknown names."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {', '.join(VARIANTS)}")
    return MODEL_DIR + VARIANTS[variant]


def book_text(plugin, rec):
    """Readable text of a BOOK record (resolves localized string IDs of vanilla plugins)."""
    sub = rec.get_subrecord('DESC')
    if sub is None:
        return None
    data = bytes(sub.data)
    if len(data) == 4 and plugin.string_tables:
        return plugin.resolve_string(sub.get_uint32())
    return data.rstrip(b'\0').decode('cp1252', errors='replace')


def word_count(text):
    """Words in book text, ignoring markup tags and [pagebreak]."""
    return len(re.sub(r'<[^>]+>|\[pagebreak\]', ' ', text or '').split())


def _sub_string(rec, sig):
    s = rec.get_subrecord(sig)
    return s.get_string() if s is not None else None


def _scan(model):
    """Per model path: records of that model (vanilla-style flavour books only, no skill books or tomes)."""
    groups = defaultdict(list)
    for rec in model.by_sig.get('BOOK', {}).values():
        path = _sub_string(rec, 'MODL')
        data = rec.get_subrecord('DATA')
        if not path or data is None or len(data.data) < 16 or kind_of_model(path) is None:
            continue
        flags_type = bytes(data.data[:2])
        if flags_type != b'\0\0' or bytes(data.data[4:8]) != b'\xff\xff\xff\xff':
            continue
        groups[path].append(rec)
    return groups


def _inam_editor_id(model, rec):
    """Editor ID of the STAT referenced by the book's INAM (inventory model), or None."""
    sub = rec.get_subrecord('INAM')
    if sub is None:
        return None
    fid = sub.get_form_id()
    value = getattr(fid, 'value', fid)
    for stat in model.by_sig.get('STAT', {}).values():
        if stat.form_id.value == value:
            return stat.editor_id
    return None


def reference_book(model, variant):
    """A vanilla record using the variant's model, to copy MODT/OBND/INAM from."""
    path = variant_model(variant)
    recs = _scan(model).get(path)
    if not recs:
        raise ValueError(f"no vanilla book uses {path}; is Skyrim.esm loaded?")
    for rec in recs:
        if rec.get_subrecord('MODT') is not None and rec.get_subrecord('INAM') is not None:
            return rec
    return recs[0]


def model_table(model):
    """Table of flavour-book models: variant, kind, INAM, OBND, typical value/weight and word counts."""
    groups = _scan(model)
    rows = []
    for variant, fname in VARIANTS.items():
        recs = groups.get(MODEL_DIR + fname, [])
        if not recs:
            continue
        ref = reference_book(model, variant)
        data = bytes(ref.get_subrecord('DATA').data)
        words = []
        for r in recs:
            text = book_text(r.plugin, r)
            if text:
                words.append(word_count(text))
        words.sort()
        obnd = ref.get_subrecord('OBND')
        rows.append({
            'variant': variant, 'kind': kind_of_model(fname), 'model': MODEL_DIR + fname,
            'inam': _inam_editor_id(model, ref), 'obnd': bytes(obnd.data).hex() if obnd else None,
            'value': int.from_bytes(data[8:12], 'little'),
            'weight': round(struct.unpack('<f', data[12:16])[0], 2),
            'count': len(recs),
            'words_median': int(statistics.median(words)) if words else None,
            'words_p10_p90': [words[len(words) // 10], words[len(words) * 9 // 10]] if words else None,
            'examples': [r.editor_id for r in recs[:3]],
        })
    return rows


def book_edits(model, variant, value=None, weight=None):
    """edit_record edits that turn a BOOK into the variant's model, copying MODT/OBND/INAM from a vanilla book."""
    ref = reference_book(model, variant)
    ref_data = bytes(ref.get_subrecord('DATA').data)
    kind = kind_of_model(variant_model(variant))
    if value is None:
        value = int.from_bytes(ref_data[8:12], 'little') if kind != 'note' else 0
    if weight is None:
        weight = KINDS[kind]['weight']
    data = b'\0\0\0\0' + b'\xff\xff\xff\xff' + struct.pack('<If', int(value), float(weight))
    edits = [{'sig': 'MODL', 'string': variant_model(variant)}]
    modt, obnd = ref.get_subrecord('MODT'), ref.get_subrecord('OBND')
    if modt is not None:
        edits.append({'sig': 'MODT', 'hex': bytes(modt.data).hex()})
    if obnd is not None:
        edits.append({'sig': 'OBND', 'hex': bytes(obnd.data).hex()})
    inam = _inam_editor_id(model, ref)
    if inam:
        edits.append({'sig': 'INAM', 'form': inam})
    edits.append({'sig': 'DATA', 'hex': data.hex()})
    return edits


def split_book(text):
    """(title, author, body) of a plain book text laid out as 'Title', optional 'by Author', blank line, body.
    Markup (<p>, <font>, [pagebreak]) is stripped first."""
    plain = re.sub(r'<[^>]+>|\[pagebreak\]', '', text.replace('\r\n', '\n')).strip()
    head, _, rest = plain.partition('\n')
    title, author = head.strip(), None
    m = re.match(r'by\s+(.+)', rest.lstrip('\n').split('\n', 1)[0].strip(), re.I)
    if m:
        author = m.group(1).strip()
        rest = rest.lstrip('\n').partition('\n')[2]
    return title, author, rest.strip()


def format_book(body, kind, title=None, author=None, handwritten=None):
    """Wrap plain paragraphs in vanilla markup.

    Printed books: [pagebreak], a centred title page (title, 'by', author), [pagebreak], then the body with an
    illuminated first letter. Notes, letters and journals: <font face="$HandwrittenFont"> around the body.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    handwritten = KINDS[kind]['handwritten'] if handwritten is None else handwritten
    body = re.sub(r'\n{3,}', '\n\n', body.replace('\r\n', '\n').strip()).replace('\n', '\r\n')
    if handwritten:
        head = f"{title}\r\n\r\n" if title else ''
        return f'<font face="$HandwrittenFont">\r\n{head}{body}\r\n</font>'
    first = body[:1]
    if first.isalpha() and first.isascii():
        body = DROP_CAP.format(first.upper()) + body[1:]
    page = ''
    if title:
        by = f"\r\nby\r\n{author}" if author else ''
        page = f'[pagebreak]\r\n<p align="center">\r\n\r\n\r\n{title}\r\n{by}\r\n</p>\r\n\r\n\r\n\r\n'
    return f'{page}[pagebreak]\r\n<p align="left">\r\n{body}\r\n</p>'
