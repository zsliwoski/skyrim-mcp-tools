"""Unit tests for book markup and kind helpers (no game files needed)."""
import pytest

from tools.books import books


def test_kind_of_model():
    assert books.kind_of_model("Clutter\\Books\\Note03.nif") == 'note'
    assert books.kind_of_model("Clutter\\Books\\JournalLowPoly02.nif") == 'journal'
    assert books.kind_of_model("Clutter\\Books\\BasicBook05a.nif") == 'book'
    assert books.kind_of_model("Clutter\\Books\\SpellTomeDestructionLowPoly.nif") is None


def test_variant_model_rejects_unknown():
    assert books.variant_model('Journal01') == "Clutter\\Books\\JournalLowPoly01.nif"
    with pytest.raises(ValueError):
        books.variant_model('SkillBook')


def test_format_note_uses_handwriting_font():
    text = books.format_book("Meet me at dusk.\n\nBring coin.", 'note')
    assert text.startswith('<font face="$HandwrittenFont">') and text.endswith('</font>')
    assert '\r\n\r\nBring coin.' in text


def test_format_printed_book_has_title_page_and_drop_cap():
    text = books.format_book("Among the Argonians...", 'book', title="Veins", author="Sees-Deep-Veins")
    assert text.startswith('[pagebreak]\r\n<p align="center">')
    assert 'by\r\nSees-Deep-Veins' in text
    assert "A_letter.png'>mong the Argonians" in text
    assert text.endswith('</p>')


def test_word_count_ignores_markup():
    assert books.word_count('[pagebreak]<p align="left">one two <b>three</b></p>') == 3
