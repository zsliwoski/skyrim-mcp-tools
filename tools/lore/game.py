"""Index the player-visible text of the user's own Skyrim install (books) as the 'game' lore source."""
from tools.books.books import book_text

from .db import Writer
from .wikitext import _split_words, clean

SOURCE = "game"
LICENSE = "user's own game data"
ATTRIBUTION = "Text read from the user's installed Skyrim plugins; for local reference only"


def index_books(con, model):
    """Index every BOOK record of the model's loaded plugins. Returns the number of chunks written."""
    writer = Writer(con, SOURCE, LICENSE, ATTRIBUTION)
    for rec in model.by_sig['BOOK'].values():
        text = clean((book_text(rec.plugin, rec) or '').replace('[pagebreak]', '\n\n'))
        if len(text.split()) < 8:
            continue
        title = rec.full_name or rec.editor_id or f"{rec.form_id.value & 0xFFFFFF:06X}"
        pieces = [('', p) for p in _split_words(text, 350)]
        writer.page(f"Book: {title}", pieces, None, rec.editor_id)
    return writer.close()
