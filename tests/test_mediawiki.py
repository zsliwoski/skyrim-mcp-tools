"""MediaWiki import and crawl with invented pages and a stubbed HTTP layer."""
import bz2
import gzip
import io
import json
from xml.sax.saxutils import escape

import pytest

from tools.knowledge import mediawiki
from tools.knowledge.db import connect

WIKI = mediawiki.Wiki("t", "lic", "attr", "http://x/api.php", "http://x/wiki/", "ua")
BODY = "The Zorgmoot keep very many glass owls in their halls by the pale river near the old tower."


def _dump(n_main=1):
    pages = "".join(f"<page><title>P{i}</title><revision><id>{i}</id><text>{escape(BODY)}</text></revision></page>"
                    for i in range(n_main))
    pages += f"<page><title>Old</title><revision><id>9</id><text>#REDIRECT [[P0]]</text></revision></page>"
    pages += "<page><title>User:Z</title><revision><id>8</id><text>x</text></revision></page>"
    pages += "<page><title>Empty</title><revision><id>7</id><text></text></revision></page>"
    return f'<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">{pages}</mediawiki>'.encode()


@pytest.fixture
def con(tmp_path):
    return connect(tmp_path / "k.db", create=True)


def test_page_url_quotes_title():
    assert WIKI.page_url("Lore:A b (c)") == "http://x/wiki/Lore:A_b_(c)"


@pytest.mark.parametrize("name,opener", [("d.xml", open), ("d.xml.gz", gzip.open), ("d.xml.bz2", bz2.open)])
def test_import_dump_formats(con, tmp_path, name, opener):
    path = tmp_path / name
    with opener(path, "wb") as f:
        f.write(_dump())
    assert mediawiki.import_dump(con, WIKI, path, [""]) == 1
    assert con.execute("SELECT alias, title FROM aliases").fetchone()[:] == ("Old", "P0")
    assert con.execute("SELECT url FROM chunks").fetchone()[0] == "http://x/wiki/P0"


def test_import_dump_progress_and_namespaces(con, tmp_path):
    path = tmp_path / "d.xml"
    path.write_bytes(_dump(501))
    msgs = []
    mediawiki.import_dump(con, WIKI, path, {"", "User"}, msgs.append)
    assert msgs == ["500 pages"]
    assert con.execute("SELECT COUNT(*) FROM chunks WHERE title='User:Z'").fetchone()[0] == 0


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _api(monkeypatch, handler):
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req)
        out = handler(req.full_url)
        if isinstance(out, Exception):
            raise out
        return FakeResp(json.dumps(out).encode())
    monkeypatch.setattr(mediawiki.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mediawiki.time, "sleep", lambda s: None)
    return seen


def test_get_retries_then_succeeds_and_sends_user_agent(monkeypatch):
    replies = [OSError("x"), ValueError("y"), {"ok": 1}]
    seen = _api(monkeypatch, lambda url: replies.pop(0))
    assert mediawiki._get(WIKI, {"a": 1}, 0) == {"ok": 1}
    assert len(seen) == 3 and seen[0].get_header("User-agent") == "ua"


def test_get_raises_after_last_attempt(monkeypatch):
    _api(monkeypatch, lambda url: OSError("down"))
    with pytest.raises(OSError):
        mediawiki._get(WIKI, {}, 0, attempts=2)


NS = {"query": {"namespaces": {"0": {"*": ""}, "100": {"*": "Lore"}}}}


def _page(i, rev=True):
    p = {"title": f"Page{i}"}
    if rev:
        p["revisions"] = [{"revid": i, "slots": {"main": {"content": BODY}}}]
    return p


def test_namespace_ids_unknown_raises(monkeypatch):
    _api(monkeypatch, lambda url: NS)
    assert mediawiki._namespace_ids(WIKI, ["lore", ""], 0) == {"lore": 100, "": 0}
    with pytest.raises(ValueError, match="Nope"):
        mediawiki._namespace_ids(WIKI, ["Nope"], 0)


def test_crawl_follows_continuation(con, monkeypatch):
    def handler(url):
        if "siteinfo" in url:
            return NS
        if "gapcontinue" in url:
            return {"query": {"pages": [_page(2)]}}
        return {"query": {"pages": [_page(1), _page(3, rev=False)]}, "continue": {"gapcontinue": "Z"}}
    _api(monkeypatch, handler)
    msgs = []
    assert mediawiki.crawl(con, WIKI, ["Lore"], 0, progress=msgs.append) == 2
    assert msgs and "Lore" in msgs[0]


def test_crawl_limit_stops_early(con, monkeypatch):
    def handler(url):
        if "siteinfo" in url:
            return NS
        return {"query": {"pages": [_page(1)]}, "continue": {"gapcontinue": "Z"}}
    seen = _api(monkeypatch, handler)
    assert mediawiki.crawl(con, WIKI, [""], 0, limit=1) == 1
    assert len(seen) == 2


def test_crawl_error_keeps_partial_results(con, monkeypatch):
    state = {"n": 0}

    def handler(url):
        if "siteinfo" in url:
            return NS
        state["n"] += 1
        if state["n"] == 1:
            return {"query": {"pages": [_page(1)]}, "continue": {"gapcontinue": "Z"}}
        return OSError("down")
    _api(monkeypatch, handler)
    monkeypatch.setattr(mediawiki, "_get", mediawiki._get)
    real = mediawiki._get
    monkeypatch.setattr(mediawiki, "_get", lambda w, p, d, attempts=6: real(w, p, d, attempts=1))
    with pytest.raises(OSError):
        mediawiki.crawl(con, WIKI, [""], 0)
    assert con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert con.execute("SELECT chunk_count FROM sources").fetchone()[0] == 1
