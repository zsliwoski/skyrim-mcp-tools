"""Build the 'uesp' lore source from the UESP wiki (https://en.uesp.net), under its CC BY-SA license.

Two ways in: a MediaWiki XML export you downloaded yourself, or a polite crawl of the API. The text is
only ever written to the user's local database, never to this repository.
"""
import bz2
import gzip
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import wikitext
from .db import Writer

SOURCE = "uesp"
LICENSE = "CC BY-SA 2.5"
ATTRIBUTION = "Text from the Unofficial Elder Scrolls Pages (UESP), https://en.uesp.net, CC BY-SA 2.5"
API = "https://en.uesp.net/w/api.php"
WIKI = "https://en.uesp.net/wiki/"
USER_AGENT = "skyrim-mcp-tools-lore/1.0 (local research index; respects rate limits)"
DEFAULT_NAMESPACES = ("Lore", "Skyrim")


def page_url(title):
    return WIKI + urllib.parse.quote(title.replace(' ', '_'), safe=':/_(),\'')


def _open(path):
    p = str(path)
    if p.endswith('.bz2'):
        return bz2.open(p, 'rb')
    if p.endswith('.gz'):
        return gzip.open(p, 'rb')
    return open(p, 'rb')


def _wanted(title, namespaces):
    prefix = title.split(':', 1)[0] if ':' in title else ''
    return prefix in namespaces


def _add_page(writer, title, text, revision):
    target = wikitext.redirect_target(text)
    if target:
        writer.alias(title, target)
        return
    pieces = wikitext.chunks(text)
    if pieces:
        writer.page(title, pieces, page_url(title), revision)


def import_dump(con, path, namespaces=DEFAULT_NAMESPACES, progress=None):
    """Index pages of a MediaWiki XML export (.xml, .xml.gz or .xml.bz2) whose title is in a namespace."""
    namespaces = set(namespaces)
    writer = Writer(con, SOURCE, LICENSE, ATTRIBUTION)
    seen = 0
    with _open(path) as f:
        title = rev = text = None
        for _, el in ET.iterparse(f, events=('end',)):
            tag = el.tag.rsplit('}', 1)[-1]
            if tag == 'title':
                title = el.text or ''
            elif tag == 'id' and rev is None and title is not None:
                rev = el.text
            elif tag == 'text':
                text = el.text or ''
            elif tag == 'page':
                if title and text and _wanted(title, namespaces):
                    _add_page(writer, title, text, rev)
                    seen += 1
                    if progress and seen % 500 == 0:
                        progress(f"{seen} pages")
                title = rev = text = None
                el.clear()
    return writer.close()


def _get(params, delay, attempts=6):
    url = API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except (OSError, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(min(120, 5 * 2 ** attempt))
    time.sleep(delay)
    return data


def _namespace_ids(names, delay):
    data = _get({'action': 'query', 'meta': 'siteinfo', 'siprop': 'namespaces', 'format': 'json'}, delay)
    by_name = {v.get('*', '').lower(): int(k) for k, v in data['query']['namespaces'].items()}
    missing = [n for n in names if n.lower() not in by_name]
    if missing:
        raise ValueError(f"unknown UESP namespaces: {missing}")
    return {n: by_name[n.lower()] for n in names}


def crawl(con, namespaces=DEFAULT_NAMESPACES, delay=1.0, limit=None, progress=None):
    """Fetch pages of the given namespaces through the API, one request per `delay` seconds."""
    ids = _namespace_ids(namespaces, delay)
    writer = Writer(con, SOURCE, LICENSE, ATTRIBUTION)
    seen = 0
    try:
        for name, ns in ids.items():
            cont = {}
            while True:
                params = {'action': 'query', 'generator': 'allpages', 'gapnamespace': ns, 'gaplimit': 25,
                          'prop': 'revisions', 'rvprop': 'content|ids', 'rvslots': 'main', 'format': 'json',
                          'formatversion': 2, **cont}
                data = _get(params, delay)
                for page in data.get('query', {}).get('pages', []):
                    revs = page.get('revisions') or []
                    if not revs:
                        continue
                    rev = revs[0]
                    _add_page(writer, page['title'], rev['slots']['main']['content'], str(rev.get('revid')))
                    seen += 1
                con.commit()
                if progress and seen % 500 < 25:
                    progress(f"{name}: {seen} pages")
                if limit and seen >= limit:
                    return writer.close()
                if 'continue' not in data:
                    break
                cont = data['continue']
    except BaseException:
        # keep what was fetched so far searchable instead of losing a long crawl
        writer.close()
        raise
    return writer.close()
