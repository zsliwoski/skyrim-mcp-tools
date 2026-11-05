"""Shared local SQLite + FTS5 knowledge store (chunks, aliases, embeddings). Each database (lore, Papyrus API)
is its own file with its own path; nothing is shipped, users build them on their own machine."""
import re
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
    name TEXT PRIMARY KEY, license TEXT, attribution TEXT, built_at TEXT, chunk_count INTEGER);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, section TEXT NOT NULL DEFAULT '',
    ord INTEGER NOT NULL DEFAULT 0, url TEXT, revision TEXT, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_title ON chunks(source, title COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS aliases(source TEXT NOT NULL, alias TEXT NOT NULL COLLATE NOCASE, title TEXT NOT NULL,
    PRIMARY KEY(source, alias));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, section, text, content='chunks', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS embeddings(id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path, create=False, label="knowledge", build_hint="see README"):
    path = Path(path)
    if not create and not path.exists():
        raise FileNotFoundError(f"no {label} database at {path}; build one with `{build_hint}` (see README)")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


class Writer:
    """Replaces everything from one source, then rebuilds the full-text index on close."""

    def __init__(self, con, source, license_, attribution):
        self.con, self.source, self.license, self.attribution = con, source, license_, attribution
        con.execute("DELETE FROM embeddings WHERE id IN (SELECT id FROM chunks WHERE source=?)", (source,))
        con.execute("DELETE FROM chunks WHERE source=?", (source,))
        con.execute("DELETE FROM aliases WHERE source=?", (source,))
        self.count = 0

    def page(self, title, chunk_list, url=None, revision=None):
        for i, (section, text) in enumerate(chunk_list):
            self.con.execute(
                "INSERT INTO chunks(source,title,section,ord,url,revision,text) VALUES(?,?,?,?,?,?,?)",
                (self.source, title, section, i, url, revision, text))
            self.count += 1

    def alias(self, alias, title):
        self.con.execute("INSERT OR REPLACE INTO aliases(source,alias,title) VALUES(?,?,?)",
                         (self.source, alias, title))

    def close(self):
        self.con.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",
                         (self.source, self.license, self.attribution,
                          time.strftime('%Y-%m-%d %H:%M:%S'), self.count))
        self.con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self.con.commit()
        return self.count


def search(con, query, limit=10, source=None):
    """Ranked chunks for a query: all terms must match, else any term. Title matches rank highest."""
    terms = re.findall(r"\w+", query, flags=re.U)
    if not terms:
        return []
    quoted = ['"%s"' % t for t in terms]
    sql = ("SELECT c.id, c.source, c.title, c.section, c.url, c.revision, "
           "snippet(chunks_fts, 2, '[', ']', ' ... ', 40) AS snippet, s.license "
           "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
           "LEFT JOIN sources s ON s.name = c.source "
           "WHERE chunks_fts MATCH ?" + (" AND c.source = ?" if source else "") +
           " ORDER BY bm25(chunks_fts, 10.0, 3.0, 1.0) LIMIT ?")
    for joiner in (' ', ' OR '):
        args = [joiner.join(quoted)] + ([source] if source else []) + [limit]
        rows = [dict(r) for r in con.execute(sql, args)]
        if rows or len(terms) == 1:
            return rows
    return []


def get_page(con, title, section=None, source=None):
    """Chunks of a page (or one section), following aliases/redirects. None if unknown."""
    args = [source] if source else []
    where = " AND source=?" if source else ""
    row = con.execute("SELECT title FROM chunks WHERE title=? COLLATE NOCASE" + where + " LIMIT 1",
                      [title] + args).fetchone()
    if row is None:
        row = con.execute("SELECT title FROM aliases WHERE alias=?" + where + " LIMIT 1",
                          [title] + args).fetchone()
    if row is None:
        return None
    q = "SELECT source, title, section, url, revision, text FROM chunks WHERE title=? COLLATE NOCASE" + where
    params = [row['title']] + args
    if section is not None:
        q += " AND section=? COLLATE NOCASE"
        params.append(section)
    rows = [dict(r) for r in con.execute(q + " ORDER BY source, id", params)]
    return rows or None


def status(con):
    rows = con.execute("SELECT name, license, attribution, built_at, chunk_count FROM sources")
    emb = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    model = con.execute("SELECT value FROM meta WHERE key='embedding_model'").fetchone()
    path = con.execute("PRAGMA database_list").fetchone()['file']
    return {'path': path, 'sources': [dict(r) for r in rows],
            'embeddings': {'count': emb, 'model': model['value'] if model else None,
                           'chunks_without': con.execute("SELECT COUNT(*) FROM chunks c LEFT JOIN embeddings e "
                                                         "ON e.id = c.id WHERE e.id IS NULL").fetchone()[0]}}
