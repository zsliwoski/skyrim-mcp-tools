"""Unit tests for text/condition/script editing, lore linting and plugin diffing (no game files needed)."""
import struct

import pytest
import esplib.defs.tes5  # noqa: F401
from esplib import Plugin, Record, SubRecord, FormID

from tools.records import edit_tools as et
from tools.books import lore
from tools.records import maintenance


def _book(plugin, obj_id, editor_id, text):
    rec = Record('BOOK', FormID(obj_id), 0)
    rec.subrecords.append(SubRecord('EDID', et.encode(editor_id)))
    rec.subrecords.append(SubRecord('DESC', et.encode(text)))
    plugin.add_record(rec)
    return rec


def _info(plugin, obj_id):
    rec = Record('INFO', FormID(obj_id), 0)
    ctda = struct.pack('<B3xfH2xIIIIi', 0, 10.0, 58, 0x800, 0, 0, 0, -1)
    rec.subrecords.append(SubRecord('CTDA', ctda))
    plugin.add_record(rec)
    return rec


@pytest.fixture
def plugin_path(tmp_path):
    path = tmp_path / "Test.esp"
    p = Plugin.new_plugin(str(path), masters=[], game='tes5')
    _book(p, 0x800, "TestBook", "Meet me on Tuesday, okay?")
    _info(p, 0x801)
    p.save(str(path))
    return path


def test_lint_flags_real_world_terms():
    found = {i['match'] for i in lore.lint_text("Oh my god, it's Tuesday, okay? Five miles.")}
    assert {'Tuesday', 'Oh my god', 'okay', 'miles'} <= found


def test_lint_suggests_tamrielic_day():
    (issue,) = lore.lint_text("See you on Tuesday")
    assert 'Tirdas' in issue['suggestion']


def test_lint_clean_text_has_no_issues():
    assert lore.lint_text("By the Eight! Meet me on Tirdas, a day's walk away.") == []


def test_lint_plugin_reports_inline_text(plugin_path):
    assert {i['match'] for i in lore.lint_plugin(plugin_path)} >= {'Tuesday', 'okay'}


def test_as_text_accepts_zstrings_only():
    assert et.as_text(b"hello\0") == "hello"
    assert et.as_text(b"a\0b\0") is None
    assert et.as_text(b"\x01\x02\x03\0") is None
    assert et.as_text(b"no terminator") is None


def test_patch_source_replaces_and_rejects_missing():
    assert et.patch_source("if a == 1", "== 1", ">= 1") == "if a >= 1"
    with pytest.raises(ValueError):
        et.patch_source("if a == 1", "nope", "x")


def test_list_texts_filters_by_query(plugin_path):
    hits = et.list_texts(plugin_path, "tuesday")
    assert [h['record'] for h in hits] == ['TestBook']
    assert et.list_texts(plugin_path, "absent") == []


def test_edit_text_find_replace_and_full(plugin_path):
    res = et.edit_text(plugin_path, "TestBook", "DESC", find="Tuesday", replace="Tirdas", verbose=True)
    assert res['after'] == "Meet me on Tirdas, okay?"
    quiet = et.edit_text(plugin_path, "TestBook", "DESC", text="New text")
    assert 'after' not in quiet and quiet['words_after'] == 2
    assert et.list_texts(plugin_path, "New text")[0]['text'] == "New text"


def test_edit_texts_batch_is_atomic(plugin_path):
    res = et.edit_texts(plugin_path, [{'record': 'TestBook', 'sig': 'DESC', 'text': 'One two three'},
                                      {'record': 'TestBook', 'sig': 'FULL', 'text': 'x', 'index': 0}][:1])
    assert res[0]['words_after'] == 3
    with pytest.raises(ValueError, match="item 1"):
        et.edit_texts(plugin_path, [{'record': 'TestBook', 'sig': 'DESC', 'text': 'changed'},
                                    {'record': 'Missing', 'sig': 'DESC', 'text': 'x'}])
    assert et.list_texts(plugin_path, "One two")[0]['text'] == "One two three"


def test_lint_dates_checks_weekday_order():
    ok = "Middas, 9th of Frostfall\nx\nTurdas, 10th of Frostfall"
    assert lore.lint_dates(ok) == []
    (bad,) = lore.lint_dates("Middas, 9th of Frostfall\nx\nFredas, 10th of Frostfall")
    assert 'Turdas' in bad['suggestion']


def test_lint_flags_real_world_science():
    assert {i['match'] for i in lore.lint_text("a carat of carbon")} == {'carat', 'carbon'}


def test_lint_book_length_vs_model():
    assert lore.lint_book("Clutter\\Books\\BasicBook05.nif", "Too short.")
    assert not lore.lint_book("Clutter\\Books\\Note03.nif", "Meet me at dusk.\n\nBring the ring in a pouch, now.")
    long_text = "word " * 500
    assert any('paragraphs' == i['match'] for i in lore.lint_book("Clutter\\Books\\BasicBook05.nif", long_text))


def test_edit_text_errors(plugin_path):
    with pytest.raises(ValueError):
        et.edit_text(plugin_path, "TestBook", "DESC")
    with pytest.raises(ValueError):
        et.edit_text(plugin_path, "TestBook", "DESC", find="zzz", replace="x")
    with pytest.raises(ValueError):
        et.edit_text(plugin_path, "TestBook", "DESC", text="x", index=3)
    with pytest.raises(ValueError):
        et.edit_text(plugin_path, "Missing", "DESC", text="x")


def test_condition_list_and_edit(plugin_path):
    (cond,) = et.list_conditions(plugin_path, "000801")
    assert (cond['operator'], cond['value'], cond['function']) == ('==', 10.0, 58)
    et.edit_condition(plugin_path, "000801", 0, operator=">=", value=25)
    (cond,) = et.list_conditions(plugin_path, "000801")
    assert (cond['operator'], cond['value'], cond['function']) == ('>=', 25.0, 58)
    with pytest.raises(ValueError):
        et.edit_condition(plugin_path, "000801", 5, value=1)


def test_diff_plugins_reports_text_change(plugin_path, tmp_path):
    before = tmp_path / "before.esp"
    before.write_bytes(plugin_path.read_bytes())
    et.edit_text(plugin_path, "TestBook", "DESC", text="Changed")
    diff = et.diff_plugins(plugin_path, before)
    assert diff['changed'][0]['editor_id'] == 'TestBook'
    assert diff['changed'][0]['subrecords'][0]['after'] == 'Changed'
    assert diff['added'] == [] and diff['removed'] == []


def test_audit_scripts_flags_unguarded_property_compare(tmp_path):
    src = tmp_path / "Scripts" / "Source"
    src.mkdir(parents=True)
    (src / "Bad.psc").write_text(
        "Scriptname Bad Extends ObjectReference\nObjectReference Property Chest Auto\n"
        "Event OnContainerChanged(ObjectReference a, ObjectReference b)\n"
        "  If b == Chest\n  EndIf\nEndEvent\n")
    (src / "Good.psc").write_text(
        "Scriptname Good Extends ObjectReference\nObjectReference Property Chest Auto\n"
        "Event OnContainerChanged(ObjectReference a, ObjectReference b)\n"
        "  If b != None && b.GetFormID() == 5\n  EndIf\nEndEvent\n")
    issues = maintenance.audit_scripts(tmp_path / "Test.esp")
    assert any("Bad.psc" in i for i in issues)
    assert not any("Good.psc" in i for i in issues)
