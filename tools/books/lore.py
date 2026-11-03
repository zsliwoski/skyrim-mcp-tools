"""Lore-friendliness lint for in-world text: real-world days, months, oaths and idioms with Tamrielic suggestions."""
import re

WEEKDAYS = {'monday': 'Morndas', 'tuesday': 'Tirdas', 'wednesday': 'Middas', 'thursday': 'Turdas',
            'friday': 'Fredas', 'saturday': 'Loredas', 'sunday': 'Sundas'}
MONTHS = {'january': 'Morning Star', 'february': "Sun's Dawn", 'april': "Rain's Hand", 'june': 'Mid Year',
          'july': "Sun's Height", 'august': 'Last Seed', 'september': 'Hearthfire', 'october': 'Frostfall',
          'november': "Sun's Dusk", 'december': 'Evening Star'}
OATH = "By the Eight! (or By the Nine! for a speaker who reveres Talos)"

# (pattern, suggestion); matched case-insensitively on word boundaries
RULES = (
    [(rf"\b{d}s?\b", f"use {t}") for d, t in WEEKDAYS.items()]
    + [(rf"\b{m}\b", f"use {t}") for m, t in MONTHS.items()]
    + [(r"\b(oh my god|oh god|my god|good god|oh my gosh|for god'?s sake|thank god|goddamn\w*|jesus|christ)\b", OATH),
       (r"\bgod bless( you)?\b", "the Divines bless you / Talos guide you"),
       (r"\b(amen|hallelujah)\b", "use an Eight/Nine-based exclamation"),
       (r"\b(heaven|heavens)\b", "use Sovngarde or Aetherius"),
       (r"\bhell\b", "use Oblivion"),
       (r"\bokay\b|\bok\b", "use 'very well', 'all right' or 'fine'"),
       (r"\bo'clock\b|\b[ap]\.m\.|\bweekend\b", "avoid real-world time; use 'morning', 'dusk', 'Loredas'"),
       (r"\b(christmas|easter|halloween|thanksgiving)\b", "use a Tamrielic festival (Midyear, Sun's Dusk...)"),
       (r"\b(dollars?|bucks?|cents?)\b", "use septims / gold"),
       (r"\b(miles?|kilomet(?:er|re)s?|meters?|metres?)\b", "use leagues, paces or 'a day's walk'"),
       (r"\b(carats?|carbon|molecul\w*|atoms?|oxygen|chemical)\b", "real-world science or unit; use grains, "
                                                                            "'the deep earth', alchemy")]
)
COMPILED = [(re.compile(p, re.I), s) for p, s in RULES]

DAYS = ['Morndas', 'Tirdas', 'Middas', 'Turdas', 'Fredas', 'Loredas', 'Sundas']
MONTH_NAMES = ['Morning Star', "Sun's Dawn", 'First Seed', "Rain's Hand", 'Second Seed', 'Midyear', "Sun's Height",
               'Last Seed', 'Hearthfire', 'Frostfall', "Sun's Dusk", 'Evening Star']
ENTRY_RX = re.compile(r"^(" + '|'.join(DAYS) + r"),?\s+(\d+)(?:st|nd|rd|th)?\s+(?:of\s+)?(" +
                      '|'.join(re.escape(m) for m in MONTH_NAMES) + ")", re.M)
# word-count guidance per model kind; (min, max) of words that is normal in vanilla (p10..p90, rounded)
KIND_WORDS = {'note': (6, 150), 'journal': (60, 600), 'book': (150, 2200)}


def lint_dates(text):
    """Dated entries (e.g. 'Middas, 9th of Frostfall') whose weekday does not follow from the previous entry."""
    out = []
    prev = None
    for m in ENTRY_RX.finditer(text):
        day, num, month = DAYS.index(m.group(1)), int(m.group(2)), MONTH_NAMES.index(m.group(3))
        if prev and prev[2] == month and num > prev[1] and (day - prev[0]) % 7 != (num - prev[1]) % 7:
            expected = DAYS[(prev[0] + num - prev[1]) % 7]
            out.append({'match': m.group(0), 'suggestion': f"{num} {m.group(3)} should be {expected} "
                                                          f"(after {DAYS[prev[0]]} the {prev[1]})",
                        'context': text[max(0, m.start() - 10):m.end() + 20].replace('\n', ' ')})
        prev = (day, num, month)
    return out


def lint_book(model_path, text):
    """Length, paragraph and model checks for one book. Returns [{match, suggestion, context}]."""
    from tools.books.books import kind_of_model, word_count
    kind = kind_of_model(model_path)
    words = word_count(text)
    out = []
    if kind in KIND_WORDS:
        lo, hi = KIND_WORDS[kind]
        if words < lo:
            out.append({'match': f"{words} words", 'suggestion': f"short for a {kind} model (vanilla ~{lo}+ words): "
                        "expand it, or use a smaller model (see list_book_models)", 'context': model_path})
        elif words > hi:
            out.append({'match': f"{words} words", 'suggestion': f"long for a {kind} model (vanilla up to ~{hi}): "
                        "use a larger book model", 'context': model_path})
    if words > 80 and len(re.findall(r'\n\s*\n', text)) < 2:
        out.append({'match': 'paragraphs', 'suggestion': 'long text without paragraph breaks; separate paragraphs '
                    'with blank lines', 'context': ''})
    return out


def lint_text(text):
    """Issues found in a string: [{match, suggestion, context}]."""
    out = []
    for rx, suggestion in COMPILED:
        for m in rx.finditer(text):
            out.append({'match': m.group(0), 'suggestion': suggestion,
                        'context': text[max(0, m.start() - 30):m.end() + 30].replace('\n', ' ')})
    return out + lint_dates(text)


def lint_plugin(path, limit=200):
    """Lint every inline text of a plugin's own records, plus book length/model/paragraph checks."""
    from esplib import Plugin
    from tools.records.edit_tools import is_own, list_texts, as_text
    from tools.records.maintenance import all_records
    out = []
    for t in list_texts(path, limit=100000):
        for issue in lint_text(t['text']):
            out.append({'record': t['record'], 'form_id': t['form_id'], 'sig': t['sig'], 'index': t['index'], **issue})
            if len(out) >= limit:
                return out
    plugin = Plugin.load(str(path))
    for rec in all_records(plugin):
        if rec.signature != 'BOOK' or not is_own(plugin, rec):
            continue
        desc, modl = rec.get_subrecord('DESC'), rec.get_subrecord('MODL')
        text = as_text(desc.data) if desc is not None else None
        if text is None or modl is None:
            continue
        for issue in lint_book(modl.get_string(), text):
            out.append({'record': rec.editor_id, 'form_id': f"{rec.form_id.value & 0xFFFFFF:06X}", 'sig': 'DESC',
                        'index': 0, **issue})
    return out[:limit]
