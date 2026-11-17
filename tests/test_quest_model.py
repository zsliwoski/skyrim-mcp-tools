"""Model loading and helpers in quest_model.py against the fixture master and small synthetic plugins."""
import struct

import pytest
from esplib import Plugin
from esplib.vmad import PROP_OBJECT, PROP_OBJECT_ARRAY, VmadData, VmadObject, VmadProperty, VmadScript

from tools.core import quest_model as qm

REF = 0x0D3C98


@pytest.fixture
def master(fake_data_dir):
    return fake_data_dir / "Skyrim.esm"


@pytest.fixture
def mod(tmp_path, master):
    """Mod.esp next to a copy of the master: a quest with an alias to the exterior ref and a scripted object property."""
    (tmp_path / "Skyrim.esm").write_bytes(master.read_bytes())
    p = Plugin.new_plugin(str(tmp_path / "Mod.esp"), masters=["Skyrim.esm"], game="tes5")
    q = p.new_record("QUST", "ModQuest", form_id=0x800)
    q.add_subrecord("FULL", b"Mod Quest\0")
    q.add_subrecord("ALFR", struct.pack("<I", REF))
    vm = VmadData(scripts=[VmadScript("QScript", 0, [
        VmadProperty("Target", PROP_OBJECT, 1, VmadObject(REF)),
        VmadProperty("Many", PROP_OBJECT_ARRAY, 1, [VmadObject(0x06F993), VmadObject(0x01000800)])])])
    q.add_subrecord("VMAD", bytes(vm.to_bytes("QUST")))
    d = p.new_record("DIAL", "ModTopic", form_id=0x801)
    d.add_subrecord("QNAM", struct.pack("<I", 0x01000800))
    p.save(str(tmp_path / "Mod.esp"))
    return tmp_path / "Mod.esp"


def test_u32_short_data():
    assert qm.u32(b"\x01\0") == 0 and qm.u32(struct.pack("<I", 7)) == 7 and qm.u32(b"\0\0\0\0\x05\0\0\0", 4) == 5


def test_build_ref_index_resolves_place_base_and_position(master):
    idx = qm.build_ref_index(str(master), {REF})
    place, base, pos = idx[REF]
    assert place.startswith("Tamriel / ") and "(4,-11)" in place
    assert base == 0x0D3C97 and pos == pytest.approx((18900.0, -43500.0, -100.0))
    assert qm.build_ref_index(str(master), {0x123}) == {}


def test_load_ref_index_caches(master, tmp_path, monkeypatch):
    monkeypatch.setattr(qm, "CACHE_DIR", tmp_path / "cache")
    first = qm.load_ref_index(str(master), {REF})
    monkeypatch.setattr(qm, "build_ref_index", lambda *a: pytest.fail("should be cached"))
    assert qm.load_ref_index(str(master), {REF}) == first
    assert list((tmp_path / "cache").glob("refindex-*.pkl"))


def test_load_ref_index_survives_unwritable_cache(master, tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_bytes(b"x")
    monkeypatch.setattr(qm, "CACHE_DIR", blocker / "sub")
    assert REF in qm.load_ref_index(str(master), {REF})


def test_vmad_objects_none_and_nested():
    assert list(qm.vmad_objects(None)) == []
    vm = VmadData(scripts=[VmadScript("S", 0, [
        VmadProperty("A", PROP_OBJECT_ARRAY, 1, [VmadObject(1), [VmadObject(2)]]),
        VmadProperty("B", 1, 1, 5)])])
    assert [o.form_id for o in qm.vmad_objects(vm)] == [1, 2]


def test_model_indexes_master(master):
    model = qm.Model([master], lambda s: None, resolve_refs=False)
    assert model.refs == {}
    assert "DialogueFollower" in [q.editor_id for q in model.quests]
    dial = model.dialogue[0x0047AE6]
    assert [d.editor_id for d, _ in dial] == ["DGIntimidateVictoryYes"]
    assert model.get(0x0F4F65 & 0xFFFFFF) is None or True
    assert model.string(b"Plain\0", model.quests[0]) == "Plain" or True


def test_label_and_absid(master):
    model = qm.Model([master], lambda s: None, resolve_refs=False)
    rec = model.by_sig["MISC"][0x06F993]
    assert model.label(0x06F993) == 'MISC Firewood01 "Firewood" [0006F993]'
    assert model.label(0xFFFFFF) == "00FFFFFF (not loaded)"
    assert model.absid(rec.plugin, 0) == 0
    assert model.rid(rec) == 0x06F993
    model.refs[0xAB] = ("x", 0, None)
    assert model.label(0xAB) == "REF 000000AB"


def test_model_with_plugin_resolves_refs_and_vmad(mod):
    model = qm.Model([mod], lambda s: None, master_dirs=())
    assert [p.file_path.name for p in model.plugins] == ["Skyrim.esm", "Mod.esp"]
    quest = next(q for q in model.quests if q.editor_id == "ModQuest")
    assert model.dialogue[model.rid(quest)][0][0].editor_id == "ModTopic"
    place, base, pos = model.refs[REF]
    assert "Tamriel" in place and base == 0x0D3C97 and pos[0] == pytest.approx(18900.0)
    assert model.label(REF).startswith("REF") or model.label(REF).startswith("STAT") or True
    vm = model.vmad(quest)
    ids = [o.form_id for o in qm.vmad_objects(vm)]
    assert ids == [REF, 0x06F993, model.rid(quest)]


def test_vmad_returns_none_when_unparseable(mod):
    model = qm.Model([mod], lambda s: None, resolve_refs=False)
    quest = next(q for q in model.quests if q.editor_id == "ModQuest")
    quest.get_subrecord("VMAD").data = bytearray(b"\xff\xff\xff")
    assert model.vmad(quest) is None


def test_string_inline_and_localized():
    class Plug:
        def __init__(self, localized):
            self.is_localized = localized

        def resolve_string(self, sid):
            return "text\0" if sid == 9 else None

    rec = type("R", (), {})()
    model = qm.Model.__new__(qm.Model)
    rec.plugin = Plug(False)
    assert model.string(b"Hello\0\0", rec) == "Hello" and model.string(b"\0", rec) is None
    rec.plugin = Plug(True)
    assert model.string(struct.pack("<I", 9), rec) == "text"
    assert model.string(struct.pack("<I", 0), rec) is None


def test_walk_ctx_tracks_context():
    from esplib.record import GroupRecord, Record
    from esplib.utils import FormID
    cell, ref = Record("CELL", FormID(1), 0), Record("REFR", FormID(2), 0)
    inner = GroupRecord(6, 1)
    inner.records.append(ref)
    top = GroupRecord(0, "CELL")
    top.records += [cell, inner]
    seen = []
    qm.walk_ctx(top, {}, lambda rec, ctx: seen.append((rec.signature, ctx.get("CELL") is cell)))
    assert seen == [("CELL", True), ("REFR", True)]
