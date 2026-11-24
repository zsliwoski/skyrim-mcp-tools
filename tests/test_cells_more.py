"""Cell creation (from scratch and cloned) and reference persistence against the fixture master."""
import pytest
from esplib import Plugin

import server
from helpers import sigs
from tools.cells import cell_create, persist

pytestmark = pytest.mark.fakegame

REF_ID = "0D3C98"


def _fresh():
    server._models.clear()


@pytest.fixture
def plugin_with_hall(sandbox):
    res = server.create_enchantment("Cells.esp", "TestCellEnch", "Test", [
        {"effect": "EnchFireDamageFFContact", "magnitude": 7}], kind="weapon")
    assert "error" not in res, res
    _fresh()
    assert "error" not in server.create_cell("Cells.esp", "HallA", "Hall A")
    _fresh()
    return sandbox / "Cells.esp"


def test_default_xcll_size():
    assert len(cell_create.default_xcll()) == 92


def test_remap_navmesh_reparents_and_keeps_copied_doors():
    import struct
    head = struct.pack('<IIII', 12, 0xA5E9A03C, 0, 0x165A8)
    body = (struct.pack('<I', 1) + bytes(12) + struct.pack('<I', 1) + bytes(16)
            + struct.pack('<I', 1) + struct.pack('<IIH', 1, 0xAAA, 0)
            + struct.pack('<I', 2) + struct.pack('<HII', 0, 7, 0x111) + struct.pack('<HII', 1, 8, 0x222)
            + struct.pack('<I', 0))
    out = cell_create.remap_navmesh(head + body, 0x800, {0x111: 0x900}, {0xAAA: 0xBBB})
    assert struct.unpack_from('<I', out, 12)[0] == 0x800
    o = 16 + 4 + 12 + 4 + 16
    assert struct.unpack_from('<IIH', out, o + 4) == (1, 0xBBB, 0)
    o += 4 + 10
    assert struct.unpack_from('<I', out, o)[0] == 1
    assert struct.unpack_from('<HII', out, o + 4) == (0, 7, 0x900)
    assert struct.unpack_from('<I', out, o + 14)[0] == 0


def test_interior_group_reuses_nesting():
    plugin = Plugin.new_plugin("x.esp", masters=[], game="tes5")
    a = cell_create.interior_group(plugin, 0x000812)
    b = cell_create.interior_group(plugin, 0x000812)
    c = cell_create.interior_group(plugin, 0x000822)
    assert a is b and a is not c
    assert (a.group_type, a.label) == (3, 6)
    assert len([g for g in plugin.groups if g.label == 'CELL']) == 1


@pytest.mark.parametrize("args,msg", [
    (("Cells.esp", "bad id", "N"), "letters, digits"),
    (("Cells.esp", "NoName"), "name is required"),
])
def test_create_cell_validation(sandbox, args, msg):
    res = server.create_cell(*args)
    assert msg in res["error"]


def test_create_cell_missing_plugin_and_duplicate(plugin_with_hall):
    assert "error" in server.create_cell("Nope.esp", "X", "X")
    assert "already exists" in server.create_cell("Cells.esp", "hallA", "Again")["error"]


def test_create_cell_from_scratch_flags(plugin_with_hall):
    res = server.create_cell("Cells.esp", "HallB", "Hall B", can_travel=False)
    assert "error" not in res, res
    cell = next(r for r in Plugin.load(str(plugin_with_hall)).records if r.editor_id == "HallB")
    assert cell.get_subrecord('DATA').data[0] & 0x04
    assert res["created"]["references_copied"] == 0


def test_clone_cell_copies_references(plugin_with_hall):
    placed = server.place_item("Book1CheapHorkers", "Cells.esp", "HallA", 1.0, 2.0, 3.0)
    assert "error" not in placed, placed
    _fresh()
    res = server.create_cell("Cells.esp", "HallClone", "Clone Hall", source="HallA",
                             plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert "error" not in res, res
    assert res["created"]["references_copied"] == 1
    _fresh()
    assert "HallClone" in sigs(plugin_with_hall)["CELL"]


def test_link_cells_points_each_door_at_the_other(plugin_with_hall):
    import math
    import struct
    assert "error" not in server.create_cell("Cells.esp", "HallB", "Hall B")
    _fresh()
    res = server.link_cells("Cells.esp", "Hall", {"cell": "HallA", "x": 0, "y": 0, "z": 0, "rotation_z": 90},
                            {"cell": "HallB", "x": 500, "y": 0, "z": 10, "rotation_z": 0},
                            plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert "error" not in res, res
    _fresh()
    refs = {r.editor_id: r for r in Plugin.load(str(plugin_with_hall)).records if r.editor_id}
    for src, dst in (("HallDoorA", "HallDoorB"), ("HallDoorB", "HallDoorA")):
        data = bytes(refs[src].get_subrecord('XTEL').data)
        assert len(data) == 32
        assert struct.unpack_from('<I', data)[0] == refs[dst].form_id.value
    x, y, z, _, _, rz, _ = struct.unpack_from('<3f3fI', bytes(refs["HallDoorA"].get_subrecord('XTEL').data), 4)[:7]
    assert (round(x), round(y), round(z)) == (500, -80, 10) and abs(rz - math.pi) < 1e-5


def test_link_cells_rejects_missing_position(plugin_with_hall):
    assert "x, y and z" in server.link_cells("Cells.esp", "P", {"cell": "HallA"}, {"cell": "HallA"})["error"]


def test_location_assigns_cell_and_marker_links_back(plugin_with_hall):
    import struct
    res = server.create_location("Cells.esp", "HallLoc", "Hall of Tests", keywords=["LocTypeDungeon"],
                                 cells=["HallA"], plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert "error" not in res, res
    _fresh()
    mark = server.add_map_marker("Cells.esp", "HallMarker", "Hall of Tests", 10, 20, 30, grid_x=4, grid_y=-11,
                                 marker_type="dwemer_ruin", visible=False, location="HallLoc",
                                 plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert "error" not in mark, mark
    _fresh()
    plug = Plugin.load(str(plugin_with_hall))
    recs = {r.editor_id: r for r in plug.records if r.editor_id}
    loc = recs["HallLoc"]
    assert loc.get_subrecord('FULL').data.rstrip(b'\0') == b"Hall of Tests"
    assert struct.unpack_from('<I', bytes(recs["HallA"].get_subrecord('XLCN').data))[0] == loc.form_id.value
    from tools.records.maintenance import all_records
    ref = next(r for r in all_records(plug) if r.editor_id == "HallMarker")
    assert int(ref.flags) & 0x400 and bytes(ref.get_subrecord('TNAM').data) == bytes([8, 0])
    assert bytes(ref.get_subrecord('FNAM').data) == bytes([2])
    assert struct.unpack_from('<I', bytes(loc.get_subrecord('MNAM').data))[0] == ref.form_id.value


def test_location_rejects_cell_outside_plugin(plugin_with_hall):
    res = server.create_location("Cells.esp", "BadLoc", "Bad", cells=["Nowhere"])
    assert "not in Cells.esp" in res["error"]


def test_edit_reference_moves_and_disables_vanilla_ref(plugin_with_hall):
    import struct
    from tools.records.maintenance import all_records
    res = server.edit_reference(REF_ID, "Cells.esp", disable=True, x=111, z=5, rotation_z=90, scale=2,
                                plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert "error" not in res, res
    assert res["disabled"] and res["position"]["x"] == 111
    _fresh()
    ref = next(r for r in all_records(Plugin.load(str(plugin_with_hall))) if r.signature == 'REFR'
               and (r.form_id.value & 0xFFFFFF) == int(REF_ID, 16))
    assert int(ref.flags) & 0x800
    x, y, z_, _, _, rz = struct.unpack('<6f', bytes(ref.get_subrecord('DATA').data))
    assert (x, z_) == (111, 5) and abs(rz - 1.5708) < 1e-3
    assert struct.unpack('<f', bytes(ref.get_subrecord('XSCL').data))[0] == 2
    again = server.edit_reference(REF_ID, "Cells.esp", disable=False, plugins=["Skyrim.esm", str(plugin_with_hall)])
    assert not again["disabled"] and again["position"]["x"] == 111


def test_edit_reference_needs_a_change(plugin_with_hall):
    assert "nothing to change" in server.edit_reference(REF_ID, "Cells.esp")["error"]


def test_clone_exterior_cell_rejected(plugin_with_hall):
    res = server.create_cell("Cells.esp", "HallExt", "Ext", source="(4,-11)")
    assert "error" in res


def test_persist_rejects_bad_reference(sandbox):
    res = server.make_reference_persistent("zzz", "Cells.esp")
    assert "hex reference ID" in res["error"]


def test_persist_unknown_reference(plugin_with_hall):
    res = server.make_reference_persistent("0ABCDEF", "Cells.esp")
    assert "not found" in res["error"]


class _Stub:
    def __init__(self):
        self.placed = []
        self.target = type("T", (), {"plugin": object()})()
        self.cells = type("C", (), {"find": staticmethod(lambda *a: "info")})()

    def override(self, plugin, ref):
        return ref

    def place_ref(self, plugin, info, rec, persistent, made):
        self.placed.append((info, persistent))

    def local(self, abs_id):
        return abs_id & 0xFFFFFF


def _ref(flags=0):
    from esplib.record import Record
    from esplib.utils import FormID
    return Record('REFR', FormID(0x123456), flags)


def test_persist_reference_sets_flag_and_places(monkeypatch):
    ref, cell = _ref(), type("Cell", (), {"editor_id": "SomeCell", "full_name": None})()
    monkeypatch.setattr(persist, "find_reference", lambda b, a: (ref, cell))
    b = _Stub()
    fid = persist.persist_reference(b, 0x123456, [])
    assert int(ref.flags) & persist.PERSISTENT and fid == 0x123456
    assert b.placed == [("info", True)]


def test_persist_reference_already_persistent(monkeypatch):
    ref, cell = _ref(persist.PERSISTENT), type("Cell", (), {"editor_id": "C", "full_name": None})()
    monkeypatch.setattr(persist, "find_reference", lambda b, a: (ref, cell))
    b = _Stub()
    assert persist.persist_reference(b, 0x123456, []) == 0x123456
    assert not b.placed


def test_find_reference_in_fixture_world():
    import server as s
    b = s.npc_builder(None)
    ref, cell = persist.find_reference(b, 0x0D3C98)
    assert ref.signature == 'REFR' and cell.signature == 'CELL'
    assert persist.find_reference(b, 0x0DFFFF) is None