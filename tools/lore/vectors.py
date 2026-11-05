"""Semantic layer for the lore database; the implementation is shared in tools.knowledge.vectors."""
from tools.knowledge import vectors as _kv
from tools.knowledge.vectors import (DEFAULT_MODEL, build, count, get_embedder,  # noqa: F401
                                     semantic_ids, stored_model)


def search(con, query, limit=10, source=None, mode="auto", embedder=None):
    return _kv.search(con, query, limit, source, mode, embedder, "python -m tools.lore build vectors")
