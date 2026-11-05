"""The lore database: the shared knowledge store at SKYRIM_LORE_DB (default lore_db/lore.db)."""
import os
from pathlib import Path

from tools.knowledge import db as _kb
from tools.knowledge.db import Writer, get_page, search, status  # noqa: F401  (re-exported for callers)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "lore_db" / "lore.db"


def db_path():
    return Path(os.environ.get("SKYRIM_LORE_DB") or DEFAULT_PATH)


def connect(path=None, create=False):
    return _kb.connect(path or db_path(), create, "lore", "python -m tools.lore build ...")
