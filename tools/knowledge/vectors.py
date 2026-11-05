"""Optional semantic layer: local embeddings stored in the same SQLite file, merged with keyword search.

Needs `pip install fastembed` (ONNX, CPU only, no PyTorch). The model is downloaded on first use and
everything runs locally. Vectors are brute-force searched with numpy, which is plenty for a wiki-sized corpus.
"""
import numpy as np

from . import db

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60
_embedders = {}
_matrices = {}


class FastEmbedder:
    def __init__(self, model):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise ValueError("semantic search needs `pip install fastembed` (see requirements-lore.txt, shared by every database)") from e
        self.model = model
        self._m = TextEmbedding(model_name=model)

    def embed(self, texts):
        return self._m.embed(texts)

    def query(self, text):
        fn = getattr(self._m, 'query_embed', None)
        return next(iter(fn(text) if fn else self._m.embed([text])))


def get_embedder(model=DEFAULT_MODEL):
    if model not in _embedders:
        _embedders[model] = FastEmbedder(model)
    return _embedders[model]


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def stored_model(con):
    row = con.execute("SELECT value FROM meta WHERE key='embedding_model'").fetchone()
    return row['value'] if row else None


def count(con):
    return con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]


def build(con, embedder=None, model=DEFAULT_MODEL, batch=64, progress=None):
    """Embed every chunk that has no vector yet (all of them if the model changed). Returns vectors added."""
    embedder = embedder or get_embedder(model)
    model = getattr(embedder, 'model', model)
    if stored_model(con) not in (None, model):
        con.execute("DELETE FROM embeddings")
    con.execute("INSERT OR REPLACE INTO meta VALUES('embedding_model', ?)", (model,))
    con.commit()
    _matrices.clear()
    todo = con.execute("SELECT c.id, c.title, c.section, c.text FROM chunks c "
                       "LEFT JOIN embeddings e ON e.id = c.id WHERE e.id IS NULL ORDER BY c.id").fetchall()
    done = 0
    for i in range(0, len(todo), batch):
        rows = todo[i:i + batch]
        texts = [f"{r['title']} - {r['section']}\n{r['text']}" if r['section'] else f"{r['title']}\n{r['text']}"
                 for r in rows]
        vecs = [_unit(v) for v in embedder.embed(texts)]
        con.executemany("INSERT OR REPLACE INTO embeddings VALUES(?,?)",
                        [(r['id'], v.tobytes()) for r, v in zip(rows, vecs)])
        con.commit()
        done += len(rows)
        if progress:
            progress(f"{done}/{len(todo)} chunks embedded")
    return done


def _matrix(con):
    key = (id(con), count(con), stored_model(con))
    if key not in _matrices:
        _matrices.clear()
        rows = con.execute("SELECT id, vec FROM embeddings ORDER BY id").fetchall()
        ids = np.array([r['id'] for r in rows], dtype=np.int64)
        mat = (np.vstack([np.frombuffer(r['vec'], dtype=np.float32) for r in rows])
               if rows else np.zeros((0, 1), dtype=np.float32))
        _matrices[key] = (ids, mat)
    return _matrices[key]


def _rows(con, ids, snippet_words=45):
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    out = {}
    for r in con.execute(
            f"SELECT c.id, c.source, c.title, c.section, c.url, c.revision, c.text, s.license FROM chunks c "
            f"LEFT JOIN sources s ON s.name = c.source WHERE c.id IN ({marks})", ids):
        d = dict(r)
        words = d.pop('text').split()
        d['snippet'] = ' '.join(words[:snippet_words]) + (' ...' if len(words) > snippet_words else '')
        out[d['id']] = d
    return out


def semantic_ids(con, query, limit=50, source=None, embedder=None):
    """Chunk ids nearest to the query, best first."""
    if not count(con):
        return []
    embedder = embedder or get_embedder(stored_model(con) or DEFAULT_MODEL)
    ids, mat = _matrix(con)
    scores = mat @ _unit(embedder.query(query))
    order = np.argsort(-scores)
    if source:
        allowed = {r[0] for r in con.execute("SELECT id FROM chunks WHERE source=?", (source,))}
        order = [i for i in order if int(ids[i]) in allowed]
    return [int(ids[i]) for i in order[:limit]]


def search(con, query, limit=10, source=None, mode="auto", embedder=None, build_hint="build vectors"):
    """mode: 'keyword' (FTS5 only), 'semantic' (vectors only), 'hybrid' (reciprocal rank fusion) or
    'auto' (hybrid when vectors have been built, else keyword)."""
    have = count(con) > 0
    if mode == 'auto':
        mode = 'hybrid' if have else 'keyword'
    if mode == 'keyword':
        return db.search(con, query, limit, source)
    if not have:
        raise ValueError(f"no embeddings yet; run `{build_hint}`")
    sem = semantic_ids(con, query, 50, source, embedder)
    if mode == 'semantic':
        rows = _rows(con, sem[:limit])
        return [rows[i] for i in sem[:limit] if i in rows]
    kw = db.search(con, query, 50, source)
    scores, by_id = {}, {r['id']: r for r in kw}
    for lst in ([r['id'] for r in kw], sem):
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    best = sorted(scores, key=scores.get, reverse=True)[:limit]
    missing = _rows(con, [i for i in best if i not in by_id])
    return [by_id.get(i) or missing[i] for i in best if i in by_id or i in missing]
