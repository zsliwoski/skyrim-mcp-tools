"""Book model scanning, edits and text helpers using synthetic BOOK records, plus the book tools on the fixture master."""
import struct
from types import SimpleNamespace

import pytest
from esplib.record import Record
from esplib.utils import FormID

import server
from tools.books import books


def _book(fid, edid, model, text="one two three", value=20, weight=1.0, modt=True, inam=None):
    rec = Record('BOOK', FormID(fid), 0)
    rec.add_subrecord('EDID', edid.encode() + b'\0')
    rec.add_subrecord('OBND', bytes(range(12)))
    rec.add_subrecord('MODL', model.encode() + b'\0')
    if modt:
        rec.add_subrecord('MODT', b'\x01\x02\x03')
    if inam is not None:
        rec.add_subrecord('INAM', struct.pack('<I', inam))
    rec.add_subrecord('DESC', text.encode() + b'\0')
    rec.add_subrecord('DATA', b'\0\0\0\0' + b'\xff\xff\xff\xff' + struct.pack('<If', value, weight))
    rec.plugin = SimpleNamespace(string_tables=None)
    return rec


def _model(*recs, stats=()):
    return SimpleNamespace(by_sig={'BOOK': {r.form_id.value: r for r in recs},
                                   'STAT': {s.form_id.value: s for s in stats}})


@pytest.fixture
def stat():
    s = Record('STAT', FormID(0x777), 0)
    s.add_subrecord('EDID', b'BookArtStat\0')
    return s


@pytest.fixture
def model(stat):
    return _model(
        _book(1, 'JournalA', books.MODEL_DIR + 'JournalLowPoly01.nif', "a b c d e f", value=15, weight=2.0, inam=0x777),
        _book(2, 'JournalB', books.MODEL_DIR + 'JournalLowPoly01.nif', "a b"),
        _book(3, 'NoteX', books.MODEL_DIR + 'Note03.nif', "hi there", value=3, weight=0.0, modt=False),
        _book(4, 'Tome', books.MODEL_DIR + 'SpellTomeDestructionLowPoly.nif'),
        stats=[stat])


def test_scan_skips_tomes_and_odd_data(model):
    groups = books._scan(model)
    assert set(groups) == {books.MODEL_DIR + 'JournalLowPoly01.nif', books.MODEL_DIR + 'Note03.nif'}
    skill = _book(9, 'Skill', books.MODEL_DIR + 'BasicBook05.nif')
    skill.get_subrecord('DATA').data = b'\x01\0\0\0' + bytes(12)
    assert books._scan(_model(skill)) == {}


def test_reference_book_prefers_complete_record(model):
    assert books.reference_book(model, 'Journal01').editor_id == 'JournalA'
    assert books.reference_book(model, 'Note03').editor_id == 'NoteX'
    with pytest.raises(ValueError):
        books.reference_book(model, 'Note01')


def test_model_table(model):
    rows = {r['variant']: r for r in books.model_table(model)}
    assert set(rows) == {'Journal01', 'Note03'}
    j = rows['Journal01']
    assert j['kind'] == 'journal' and j['inam'] == 'BookArtStat' and j['count'] == 2
    assert j['value'] == 15 and j['weight'] == 2.0 and j['words_median'] in (4, 6)
    assert rows['Note03']['obnd'] == bytes(range(12)).hex()


def test_book_edits_copy_reference(model):
    edits = {e['sig']: e for e in books.book_edits(model, 'Journal01')}
    assert edits['MODL']['string'].endswith('JournalLowPoly01.nif')
    assert edits['MODT']['hex'] == '010203' and edits['INAM']['form'] == 'BookArtStat'
    data = bytes.fromhex(edits['DATA']['hex'])
    assert struct.unpack('<If', data[8:]) == (15, 1.0)


def test_book_edits_note_defaults_and_overrides(model):
    edits = {e['sig']: e for e in books.book_edits(model, 'Note03')}
    assert 'MODT' not in edits and 'INAM' not in edits
    assert struct.unpack('<If', bytes.fromhex(edits['DATA']['hex'])[8:]) == (0, 0.0)
    over = {e['sig']: e for e in books.book_edits(model, 'Journal01', value=99, weight=0.5)}
    assert struct.unpack('<If', bytes.fromhex(over['DATA']['hex'])[8:]) == (99, 0.5)


def test_book_text_inline_and_missing():
    rec = _book(1, 'B', 'x.nif', "Hello")
    assert books.book_text(rec.plugin, rec) == "Hello"
    rec.subrecords[:] = [s for s in rec.subrecords if s.signature != 'DESC']
    assert books.book_text(rec.plugin, rec) is None


def test_book_text_localized():
    rec = _book(1, 'B', 'x.nif')
    rec.get_subrecord('DESC').data = struct.pack('<I', 5)
    plugin = SimpleNamespace(string_tables={1: 1}, resolve_string=lambda i: f"string{i}")
    assert books.book_text(plugin, rec) == "string5"


def test_kind_of_model_empty_and_slashes():
    assert books.kind_of_model(None) is None
    assert books.kind_of_model("Clutter/Books/Note01.nif") == 'note'


def test_split_book_with_and_without_author():
    assert books.split_book("Title\nby Me\n\nBody text.") == ("Title", "Me", "Body text.")
    assert books.split_book('<p>Title</p>\r\n\r\nBody') == ("Title", None, "Body")


def test_format_book_variants():
    plain = books.format_book("1st line", 'book')
    assert plain.startswith('[pagebreak]\r\n<p align="left">') and '1st line' in plain
    note = books.format_book("Body", 'note', title="Dear")
    assert 'Dear\r\n\r\nBody' in note
    assert books.format_book("x\n\n\n\ny", 'journal', handwritten=False).count('\r\n\r\n\r\n') == 0
    with pytest.raises(ValueError):
        books.format_book("x", 'scroll')


@pytest.mark.fakegame
class TestBookTools:
    def test_read_book(self):
        res = server.read_book("Book1CheapHorkers")
        assert res['editor_id'] == 'Book1CheapHorkers' and res['text'] == 'Text.' and res['words'] == 1

    def test_read_book_unknown(self):
        assert 'error' in server.read_book("NoSuchBookAnywhere")

    def test_list_book_models_runs(self):
        assert isinstance(server.list_book_models(), list)

    def test_edit_book_validation(self, sandbox):
        assert 'give kind or variant' in server.edit_book("B.esp", "X")['error']
        assert 'kind must be' in server.edit_book("B.esp", "X", kind="scroll")['error']
        assert 'unknown variant' in server.edit_book("B.esp", "X", variant="Nope")['error']
