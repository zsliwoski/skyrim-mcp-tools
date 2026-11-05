"""Lore database: wikitext cleaning, dump import and search, using invented text only."""
import gzip
from xml.sax.saxutils import escape

import numpy as np
import pytest

from tools.knowledge import wikitext
from tools.lore import db, uesp, vectors

PAGE = """{{Infobox|name=Foo}}
The '''Zorgmoot''' are a [[Lore:Imaginary Folk|fictional people]] of the [[Skyrim:Test Hold|test hold]].<ref>x</ref>
They keep very many glass owls in their halls.

== History ==
Long ago the Zorgmoot built a tower of [[File:Tower.jpg|thumb|a tower]] pale stone near the river.

== References ==
Should be dropped entirely because it is boilerplate text for the reader.
"""

ESC = escape(PAGE)
DUMP = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Lore:Zorgmoot</title><revision><id>42</id><text>{ESC}</text></revision></page>
<page><title>Lore:Zorg</title><revision><id>43</id><text>#REDIRECT [[Lore:Zorgmoot]]</text></revision></page>
<page><title>Talk:Lore:Zorgmoot</title><revision><id>44</id><text>{ESC}</text></revision></page>
</mediawiki>"""


def test_clean_strips_markup():
    out = wikitext.clean(PAGE)
    assert "Infobox" not in out and "<ref" not in out and "Tower.jpg" not in out
    assert "fictional people" in out and "'''" not in out


def test_chunks_drop_boilerplate_sections():
    sections = [s for s, _ in wikitext.chunks(PAGE)]
    assert sections == ['', 'History']


def test_redirect_target():
    assert wikitext.redirect_target("#REDIRECT [[Lore:Zorgmoot]]") == "Lore:Zorgmoot"
    assert wikitext.redirect_target("plain") is None


@pytest.fixture
def con(tmp_path):
    dump = tmp_path / "dump.xml.gz"
    dump.write_bytes(gzip.compress(DUMP.encode()))
    con = db.connect(tmp_path / "lore.db", create=True)
    assert uesp.import_dump(con, dump) == 2
    return con


def test_import_filters_namespaces_and_records_source(con):
    titles = {r['title'] for r in con.execute("SELECT title FROM chunks")}
    assert titles == {"Lore:Zorgmoot"}
    src = db.status(con)['sources'][0]
    assert src['name'] == 'uesp' and 'CC BY-SA' in src['license']


def test_search_ranks_and_falls_back_to_any_term(con):
    hits = db.search(con, "glass owls")
    assert hits and hits[0]['title'] == "Lore:Zorgmoot" and hits[0]['url'].startswith("https://")
    assert db.search(con, "owls nonexistentword")
    assert db.search(con, "") == []


def test_get_page_follows_redirect(con):
    rows = db.get_page(con, "lore:zorg")
    assert rows and rows[0]['title'] == "Lore:Zorgmoot"
    assert db.get_page(con, "Lore:Zorgmoot", section="History")[0]['section'] == "History"
    assert db.get_page(con, "Nope") is None


def test_missing_database_has_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="tools.lore build"):
        db.connect(tmp_path / "none.db")


class FakeEmbedder:
    """Bag-of-letters vectors: deterministic, no model download."""
    model = "fake"

    def _vec(self, text):
        v = np.zeros(26, dtype=np.float32)
        for ch in text.lower():
            if 'a' <= ch <= 'z':
                v[ord(ch) - 97] += 1
        return v

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    def query(self, text):
        return self._vec(text)


def test_vectors_build_is_incremental_and_searchable(con):
    emb = FakeEmbedder()
    assert vectors.build(con, embedder=emb) == 2
    assert vectors.build(con, embedder=emb) == 0
    assert db.status(con)['embeddings'] == {'count': 2, 'model': 'fake', 'chunks_without': 0}
    for mode in ('semantic', 'hybrid', 'auto'):
        hits = vectors.search(con, "glass owls", 5, mode=mode, embedder=emb)
        assert hits and hits[0]['title'] == "Lore:Zorgmoot" and hits[0]['snippet']
    assert vectors.search(con, "glass owls", 5, source="game", mode="semantic", embedder=emb) == []


def test_semantic_needs_vectors_and_auto_falls_back(con):
    assert vectors.search(con, "glass owls", mode="auto")
    with pytest.raises(ValueError, match="build vectors"):
        vectors.search(con, "glass owls", mode="semantic")


def test_rebuilding_a_source_drops_its_vectors(con, tmp_path):
    vectors.build(con, embedder=FakeEmbedder())
    dump = tmp_path / "dump.xml.gz"
    uesp.import_dump(con, dump)
    assert db.status(con)['embeddings']['count'] == 0
