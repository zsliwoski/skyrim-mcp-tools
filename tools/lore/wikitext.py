"""Wikitext to plain text, and splitting a page into search chunks."""
import html
import re

REDIRECT = re.compile(r'^\s*#REDIRECT\s*:?\s*\[\[([^\]|#]+)', re.I)
HEADING = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$', re.M)
SKIP_SECTIONS = {'references', 'external links', 'see also', 'notes', 'gallery', 'bugs', 'trivia',
                 'appearances', 'sources', 'footnotes'}
CHUNK_WORDS = 350


def _strip_nested(text, open_tok, close_tok):
    """Remove balanced open_tok...close_tok blocks (templates, tables), innermost first."""
    pat = re.compile(re.escape(open_tok) + r'(?:(?!' + re.escape(open_tok) + '|' + re.escape(close_tok)
                     + r').)*' + re.escape(close_tok), re.S)
    prev = None
    while prev != text:
        prev = text
        text = pat.sub('', text)
    return text


def clean(text):
    """Plain readable text from wikitext: drops templates, tables, refs, files and markup."""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'<ref[^>/]*/>|<ref[^>]*>.*?</ref>', '', text, flags=re.S)
    text = _strip_nested(text, '{{', '}}')
    text = _strip_nested(text, '{|', '|}')
    text = re.sub(r'\[\[(?:File|Image|Category):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]', '', text, flags=re.I)
    text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\[https?://\S+\s+([^\]]+)\]|\[https?://\S+\]', lambda m: m.group(1) or '', text)
    text = re.sub(r"'{2,5}", '', text)
    text = re.sub(r'</?(?:br|p|div|span|small|big|center|nowiki|includeonly|noinclude|onlyinclude|blockquote)'
                  r'[^>]*>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'^[*#:;]+\s*', '', text, flags=re.M)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def redirect_target(wikitext):
    m = REDIRECT.match(wikitext or '')
    return m.group(1).strip() if m else None


def _split_words(text, limit):
    """Pieces of at most about `limit` words, breaking on paragraphs."""
    pieces, cur, count = [], [], 0
    for para in re.split(r'\n\s*\n', text):
        n = len(para.split())
        if cur and count + n > limit:
            pieces.append('\n\n'.join(cur))
            cur, count = [], 0
        cur.append(para)
        count += n
    if cur:
        pieces.append('\n\n'.join(cur))
    return pieces


def chunks(wikitext, limit=CHUNK_WORDS):
    """(section, text) pairs for a page; the lead section is named ''. Boilerplate sections are dropped."""
    out = []
    marks = list(HEADING.finditer(wikitext))
    bounds = [(0, '', marks[0].start() if marks else len(wikitext))]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(wikitext)
        bounds.append((m.end(), clean(m.group(2)), end))
    for start, name, end in bounds:
        if name.lower() in SKIP_SECTIONS:
            continue
        body = clean(wikitext[start:end])
        if len(body.split()) < 8:
            continue
        for piece in _split_words(body, limit):
            out.append((name, piece))
    return out
