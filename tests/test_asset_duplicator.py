import struct
import sys
from pathlib import Path

import pytest
from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "misc"))
import assets_model as am  # noqa: E402
from tools.core import nif_tools  # noqa: E402
from tools.core.plugin_edit import new_plugin  # noqa: E402

TEX = ["textures\\orig\\Thing_d.dds", "textures\\orig\\Thing_n.dds"]


def z(text):
    return text.encode("cp1252") + b"\0"


def sized(text):
    return struct.pack("<I", len(text)) + text.encode("cp1252")


def tri_shape():
    """A BSTriShape with one triangle at (0,0,0) (1,0,0) (0,1,0)."""
    b = struct.pack("<IIiI", 0, 0, -1, 0)  # name, extra data count, controller, flags
    b += struct.pack("<3f", 0, 0, 0) + struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1) + struct.pack("<f", 1.0)
    b += struct.pack("<i", -1) + bytes(16) + struct.pack("<iii", -1, -1, -1)  # collision, sphere, skin/shader/alpha
    desc = 4 | (1 << 44)
    verts = b"".join(struct.pack("<3f", *v) + bytes(4) for v in ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
    tris = struct.pack("<3H", 0, 1, 2)
    return b + struct.pack("<QHHI", desc, 1, 3, len(verts) + len(tris)) + verts + tris


def build_nif(textures=TEX):
    tset = struct.pack("<I", len(textures)) + b"".join(sized(t) for t in textures)
    blocks = [("BSTriShape", tri_shape()), ("BSShaderTextureSet", tset)]
    names = [n for n, _ in blocks]
    h = b"Gamebryo File Format, Version 20.2.0.7\n" + struct.pack("<IBIII", 0x14020007, 1, 12, len(blocks), 100)
    h += b"\x00\x00\x00" + struct.pack("<H", len(names))
    h += b"".join(struct.pack("<I", len(n)) + n.encode() for n in names)
    h += struct.pack("<2H", 0, 1) + struct.pack("<2I", *(len(d) for _, d in blocks)) + struct.pack("<III", 0, 0, 0)
    return h + b"".join(d for _, d in blocks) + struct.pack("<I", 0)


@pytest.fixture
def mod(tmp_path):
    """Thing.esp (no masters) with a STAT using a loose mesh and a TXST, both with loose files."""
    folder = tmp_path / "mod"
    (folder / "Meshes" / "orig").mkdir(parents=True)
    (folder / "Textures" / "orig").mkdir(parents=True)
    (folder / "Meshes" / "orig" / "Thing.nif").write_bytes(build_nif())
    (folder / "Textures" / "orig" / "Thing_d.dds").write_bytes(b"DIFFUSE")
    (folder / "Textures" / "orig" / "Thing_n.dds").write_bytes(b"NORMAL")
    path = folder / "Thing.esp"
    plugin = new_plugin(str(path))
    stat = Record("STAT", FormID(0x800), 0)
    stat.add_subrecord("EDID", z("ThingStatic"))
    stat.add_subrecord("MODL", z("orig\\Thing.nif"))
    stat.add_subrecord("MODT", bytes(12))
    plugin.add_record(stat)
    txst = Record("TXST", FormID(0x801), 0)
    txst.add_subrecord("EDID", z("ThingTextures"))
    txst.add_subrecord("TX00", z("orig\\Thing_d.dds"))
    txst.add_subrecord("TX01", z("orig\\Thing_n.dds"))
    plugin.add_record(txst)
    plugin.save(str(path))
    return path


def assets(path):
    model, rows = am.load_assets(path)
    return model, {a.edid: a for a in rows}


def test_nif_texture_rewrite_and_shape():
    nif = nif_tools.parse(build_nif())
    assert nif_tools.texture_paths(nif) == TEX
    new = nif_tools.replace_textures(nif, {TEX[0].lower(): "textures\\a\\much_longer_diffuse_name.dds"})
    again = nif_tools.parse(new)
    assert nif_tools.texture_paths(again) == ["textures\\a\\much_longer_diffuse_name.dds", TEX[1]]
    (_, verts, tris), = nif_tools.shapes(again)
    assert verts == [(0, 0, 0), (1, 0, 0), (0, 1, 0)] and tris == [(0, 1, 2)]


def test_lists_model_and_texture_set_records(mod):
    _, rows = assets(mod)
    assert rows["ThingStatic"].models == [("MODL", "orig\\Thing.nif")] and rows["ThingStatic"].owned
    assert [s for s, _ in rows["ThingTextures"].textures] == ["TX00", "TX01"]


def test_duplicate_model_clones_record_and_copies_files(mod):
    model, rows = assets(mod)
    res = am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent])
    folder = mod.parent
    nif = nif_tools.parse((folder / "Meshes" / "Mine" / "thing.nif").read_bytes())
    assert nif_tools.texture_paths(nif) == ["textures\\Mine\\thing\\thing_d.dds", "textures\\Mine\\thing\\thing_n.dds"]
    assert (folder / "Textures" / "Mine" / "thing" / "thing_d.dds").read_bytes() == b"DIFFUSE"
    assert (folder / "Textures" / "orig" / "Thing_d.dds").exists()  # the original is untouched
    assert res["record"] == "MyStatic" and not res["warnings"]
    plugin = Plugin.load(str(mod))
    recs = {r.editor_id: r for r in plugin.records}
    assert zs(recs["MyStatic"].get_subrecord("MODL").data) == "Mine\\thing.nif"
    assert recs["MyStatic"].get_subrecord("MODT") is None
    assert zs(recs["ThingStatic"].get_subrecord("MODL").data) == "orig\\Thing.nif"


def test_every_nif_of_a_weapon_is_listed_and_replaced_individually(mod):
    plugin = Plugin.load(str(mod))
    weap = Record("WEAP", FormID(0x802), 0)
    weap.add_subrecord("EDID", z("ThingWeapon"))
    weap.add_subrecord("MODL", z("orig\\Thing.nif"))
    weap.add_subrecord("WNAM", (0x800).to_bytes(4, "little"))
    plugin.add_record(weap)
    plugin.save(str(mod))
    model, rows = am.load_assets(mod)
    weapon = next(a for a in rows if a.edid == "ThingWeapon")
    entries = am.model_entries(model, rows, weapon)
    assert [(e.holder.edid, e.sig) for e in entries] == [("ThingWeapon", "MODL"), ("ThingStatic", "MODL")]
    assert entries[0].label.startswith("World") and entries[1].label.startswith("First-person")
    am.replace_model(model, mod, entries[1], "Mine", "Held", dirs=[mod.parent])
    recs = {r.editor_id: r for r in Plugin.load(str(mod)).records}
    assert zs(recs["ThingStatic"].get_subrecord("MODL").data) == "Mine\\Held.nif"
    assert zs(recs["ThingWeapon"].get_subrecord("MODL").data) == "orig\\Thing.nif"
    assert (mod.parent / "Textures" / "Mine" / "Held" / "thing_d.dds").is_file()


def test_first_person_model_reuses_custom_textures(mod):
    (mod.parent / "Textures" / "Mine").mkdir()
    (mod.parent / "Textures" / "Mine" / "thing_d.dds").write_bytes(b"CUSTOM")
    plugin = Plugin.load(str(mod))
    weap = Record("WEAP", FormID(0x802), 0)
    weap.add_subrecord("EDID", z("ThingWeapon"))
    weap.add_subrecord("MODL", z("orig\\Thing.nif"))
    weap.add_subrecord("WNAM", (0x800).to_bytes(4, "little"))
    plugin.add_record(weap)
    plugin.save(str(mod))
    model, rows = am.load_assets(mod)
    weapon = next(a for a in rows if a.edid == "ThingWeapon")
    assert am.first_person_asset(model, rows, weapon).edid == "ThingStatic"
    assert am.give_first_person(model, rows, mod, weapon, "Mine", dirs=[mod.parent]) == "ThingWeapon_1P"
    recs = {r.editor_id: r for r in Plugin.load(str(mod)).records}
    assert recs["ThingWeapon"].get_subrecord("WNAM").data == recs["ThingWeapon_1P"].form_id.value.to_bytes(4, "little")
    nif = nif_tools.parse((mod.parent / "Meshes" / "Mine_1P" / "thing.nif").read_bytes())
    assert nif_tools.texture_paths(nif) == ["textures\\Mine\\thing_d.dds", "textures\\Mine_1P\\thing\\thing_n.dds"]
    assert not (mod.parent / "Textures" / "Mine_1P" / "thing" / "thing_d.dds").exists()


def test_outline_lists_the_shape_and_obj_round_trips_with_new_geometry():
    nif = nif_tools.parse(build_nif())
    assert [(r[1], r[2]) for r in nif_tools.outline(nif)] == [(0, "BSTriShape")]
    assert nif_tools.editable_shapes(nif) == [(0, "Shape_0")]
    text = nif_tools.export_obj(nif)
    assert text.count("\nv ") == 3 and "f 1 2 3" in text
    quad = "o Shape_0\nv 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nf 1 2 3 4\n"
    new = nif_tools.parse(nif_tools.import_obj(nif, 0, quad))
    shape = nif_tools.read_shape(new, 0)
    assert shape["verts"][2] == (2, 2, 0) and len(shape["tris"]) == 2
    assert nif_tools.texture_paths(new) == TEX
    with pytest.raises(nif_tools.ObjError):
        nif_tools.import_obj(nif, 0, "o A\nv 0 0 0\n")


def build_rigged_nif():
    """A skinned BSTriShape (vertices 0 and 1 bound to bones 5 and 7) with its skin instance and partition."""
    desc = 7 | (1 << 44 | 64 << 44)
    shape = bytearray(tri_shape()[:100] + struct.pack("<QHHI", desc, 0, 0, 0))
    struct.pack_into("<i", shape, 88, 1)  # skin ref, just after the 16-byte bounding sphere
    verts = b"".join(struct.pack("<3f", *v) + bytes(4) + struct.pack("<4e", 1, 0, 0, 0) + bytes([b, 0, 0, 0])
                     for v, b in (((0, 0, 0), 5), ((1, 0, 0), 7), ((0, 1, 0), 7)))
    tri = struct.pack("<3H", 0, 1, 2)
    part = struct.pack("<IIIQ", 1, len(verts), 28, desc) + verts
    part += struct.pack("<5H", 3, 1, 2, 0, 4) + struct.pack("<2H", 5, 7)
    part += b"\1" + struct.pack("<3H", 0, 1, 2) + b"\1" + struct.pack("<12f", *([1, 0, 0, 0] * 3))
    part += b"\1" + tri + b"\1" + bytes([0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    part += bytes(2) + struct.pack("<Q", desc) + tri
    blocks = [("BSTriShape", bytes(shape)), ("BSDismemberSkinInstance", struct.pack("<ii", -1, 2)),
              ("NiSkinPartition", part)]
    kinds = sorted({n for n, _ in blocks})
    h = b"Gamebryo File Format, Version 20.2.0.7\n" + struct.pack("<IBIII", 0x14020007, 1, 12, len(blocks), 100)
    h += b"\x00\x00\x00" + struct.pack("<H", len(kinds))
    h += b"".join(struct.pack("<I", len(n)) + n.encode() for n in kinds)
    h += b"".join(struct.pack("<H", kinds.index(n)) for n, _ in blocks)
    h += struct.pack("<3I", *(len(d) for _, d in blocks)) + struct.pack("<III", 0, 0, 0)
    return h + b"".join(d for _, d in blocks) + struct.pack("<I", 0)


def test_rigged_shape_exports_and_imports_with_weights_copied_from_nearest_vertex():
    nif = nif_tools.parse(build_rigged_nif())
    assert nif_tools.editable_shapes(nif) == [(0, "Shape_0")]
    assert len(nif_tools.read_shape(nif, 0)["verts"]) == 3
    quad = "o Shape_0\nv 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nf 1 2 3 4\n"
    new = nif_tools.parse(nif_tools.import_obj(nif, 0, quad))
    assert len(nif_tools.read_shape(new, 0)["tris"]) == 2
    _, sp = nif_tools._skin_partition(new, nif_tools._shape_head(new, 0))
    (part,) = sp["parts"]
    assert part["bones"] == [5, 7] and len(part["tris"]) == 2
    vd, stride = sp["vdata"], sp["stride"]
    assert [vd[v * stride + 24] for v in range(4)] == [5, 7, 7, 7]
    # the rewritten partition still ends exactly where the block does
    assert nif_tools._partition(new.block(2)[1])[0][2] == 4


def test_use_model_detaches_weapon_template(mod):
    plugin = Plugin.load(str(mod))
    weap = Record("WEAP", FormID(0x802), 0)
    weap.add_subrecord("EDID", z("ThingWeapon"))
    weap.add_subrecord("MODL", z("orig\\Thing.nif"))
    weap.add_subrecord("CNAM", (0x800).to_bytes(4, "little"))
    plugin.add_record(weap)
    plugin.save(str(mod))
    model, rows = assets(mod)
    am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent])
    model, rows = assets(mod)
    am.use_model(model, mod, rows["ThingWeapon"], rows["MyStatic"])
    rec = {r.editor_id: r for r in Plugin.load(str(mod)).records}["ThingWeapon"]
    assert rec.get_subrecord("CNAM") is None and zs(rec.get_subrecord("MODL").data) == "Mine\\thing.nif"


def test_use_model_repoints_original_at_the_copy(mod):
    model, rows = assets(mod)
    am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent])
    model, rows = assets(mod)
    assert am.use_model(model, mod, rows["ThingStatic"], rows["MyStatic"]) == "ThingStatic"
    rec = {r.editor_id: r for r in Plugin.load(str(mod)).records}["ThingStatic"]
    assert zs(rec.get_subrecord("MODL").data) == "Mine\\thing.nif" and rec.get_subrecord("MODT") is None
    with pytest.raises(ValueError):
        am.use_model(model, mod, rows["ThingStatic"], rows["ThingStatic"])


def test_duplicate_texture_set_and_repoint_in_place(mod):
    model, rows = assets(mod)
    am.duplicate_asset(model, mod, rows["ThingTextures"], "Mine", None, dirs=[mod.parent])
    rec = {r.editor_id: r for r in Plugin.load(str(mod)).records}["ThingTextures"]
    assert [zs(rec.get_subrecord(s).data) for s in ("TX00", "TX01")] == ["Mine\\thing_d.dds", "Mine\\thing_n.dds"]
    assert (mod.parent / "Textures" / "Mine" / "thing_n.dds").read_bytes() == b"NORMAL"


def test_missing_texture_warns_and_bad_names_are_rejected(mod):
    (mod.parent / "Textures" / "orig" / "Thing_n.dds").unlink()
    model, rows = assets(mod)
    with pytest.raises(ValueError):
        am.duplicate_asset(model, mod, rows["ThingStatic"], "bad name", "X", dirs=[mod.parent])
    res = am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent])
    assert len(res["warnings"]) == 1
    nif = nif_tools.parse((mod.parent / "Meshes" / "Mine" / "thing.nif").read_bytes())
    assert nif_tools.texture_paths(nif)[1] == TEX[1]


def test_render_mesh_returns_image():
    assert am.render_mesh(nif_tools.parse(build_nif()), size=64) is not None


def zs(data):
    return bytes(data).rstrip(b"\0").decode("cp1252")


def test_package_includes_duplicated_meshes_and_textures(mod):
    from tools.core.plugin_edit import package_plugin
    model, rows = assets(mod)
    am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent])
    names = {n.lower() for n in package_plugin(mod)["files"]}
    assert {"meshes/mine/thing.nif", "textures/mine/thing/thing_d.dds", "textures/mine/thing/thing_n.dds"} <= names
    assert "meshes/orig/thing.nif" in names  # the untouched record still uses its own mesh


def test_duplicate_can_rename_the_model_and_textures_follow_its_name(mod):
    model, rows = assets(mod)
    am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "MyStatic", dirs=[mod.parent], model_name="Golden")
    out = mod.parent / "Meshes" / "Mine" / "Golden.nif"
    assert nif_tools.texture_paths(nif_tools.parse(out.read_bytes()))[0] == "textures\\Mine\\Golden\\thing_d.dds"
    assert (mod.parent / "Textures" / "Mine" / "Golden" / "thing_d.dds").is_file()
    with pytest.raises(ValueError):
        am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", "Other", dirs=[mod.parent], model_name="bad name")


def test_files_only_mode_leaves_records_alone(mod):
    model, rows = assets(mod)
    before = mod.read_bytes()
    res = am.duplicate_asset(model, mod, rows["ThingStatic"], "Mine", dirs=[mod.parent], mode="files")
    assert res["record"] == "" and mod.read_bytes() == before
    assert (mod.parent / "Meshes" / "Mine" / "thing.nif").exists()


def test_textured_render_uses_uvs():
    import numpy as np
    nif = nif_tools.parse(build_nif())
    img = am.render_mesh(nif, size=64, load_texture=lambda p: np.full((4, 4, 3), 200, dtype=np.uint8))
    assert img is not None
