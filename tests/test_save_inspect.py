import struct

import pytest

import server
from tools.records import save_file, save_inspect

pytestmark = pytest.mark.fakegame


def _w(s):
    b = s.encode()
    return struct.pack("<H", len(b)) + b


def _vs(n):
    return bytes([n << 2])


def build_save(path, plugins, quests):
    """Uncompressed SE save holding only what the reader decodes. quests: {full form id: (flags, [(stage, done)])}."""
    header = struct.pack("<II", 12, 7) + _w("Tester") + struct.pack("<I", 5) + _w("Whiterun") + _w("001.01.01") \
        + _w("NordRace") + struct.pack("<Hff", 0, 0, 0) + bytes(8) + struct.pack("<IIH", 0, 0, 0)
    info = bytes([len(plugins)]) + b"".join(_w(p) for p in plugins) + struct.pack("<H", 0)
    form_ids = list(quests)
    forms = b""
    for i, (fid, (flags, stages)) in enumerate(quests.items()):
        data = bytes([flags, 0]) + _vs(len(stages)) + b"".join(struct.pack("<HB", s, d) for s, d in stages) + b"\x01"
        forms += bytes([(i + 1) >> 16, ((i + 1) >> 8) & 0xFF, (i + 1) & 0xFF]) + struct.pack("<I", 0x8000_0002)
        forms += bytes([8, 0, len(data), 0]) + data
    table_len = 40 + 60
    body_pre = bytes([74]) + struct.pack("<I", len(info)) + info
    g1 = len(body_pre) + table_len  # position of the first global data table in the body
    form_off = g1 + len(forms)
    table = struct.pack("<10I", form_off, 0, g1, 0, g1, 0, 0, 0, 0, len(quests)) + bytes(60)
    body = body_pre + table + forms + struct.pack("<I", len(form_ids)) + b"".join(struct.pack("<I", f) for f in form_ids)
    path.write_bytes(b"TESV_SAVEGAME" + struct.pack("<I", len(header)) + header + body)


def _quest_form_id(npc_plugin, edid):
    from esplib import Plugin
    plug = Plugin(str(npc_plugin))
    return next(r.form_id.value for r in plug.records if r.editor_id == edid) & 0xFFFFFF


def test_reads_quest_stages_and_flags(npc_plugin, tmp_path):
    assert "error" not in server.create_quest("TestMod.esp", "TestQ", "Test Quest")
    for st in (10, 20, 30):
        assert "error" not in server.add_quest_stage("TestQ", "TestMod.esp", st)
    assert "error" not in server.create_quest("TestMod.esp", "TestIdle", "Idle")
    oid = _quest_form_id(npc_plugin, "TestQ")
    save = tmp_path / "Save1.ess"
    build_save(save, ["Skyrim.esm", "TestMod.esp"], {(1 << 24) | oid: (17, [(10, 1), (20, 0), (30, 1)])})

    r = save_inspect.inspect_plugin_in_save(npc_plugin, save)
    assert r["plugin_in_save"] and r["load_index"] == "01" and r["player"] == "Tester"
    q = next(x for x in r["quests"] if x["quest"] == "TestQ")
    assert q["in_save"] and q["flags"] == 17 and q["stages_reached"] == [10, 30] and q["highest_stage"] == 30
    assert q["defined_stages"] == [10, 20, 30]
    idle = next(x for x in r["quests"] if x["quest"] == "TestIdle")
    assert not idle["in_save"] and any("TestIdle" in n for n in r["notes"])
    assert [x["quest"] for x in save_inspect.inspect_plugin_in_save(npc_plugin, save, "idle")["quests"]] == ["TestIdle"]


def test_plugin_missing_from_save(npc_plugin, tmp_path):
    save = tmp_path / "Save1.ess"
    build_save(save, ["Skyrim.esm"], {})
    r = save_inspect.inspect_plugin_in_save(npc_plugin, save)
    assert not r["plugin_in_save"] and "save again" in r["notes"][0]


def test_rejects_non_save(tmp_path):
    bad = tmp_path / "x.ess"
    bad.write_bytes(b"not a save at all")
    with pytest.raises(save_file.SaveError):
        save_file.load_save(bad)
    assert save_inspect.list_saves(tmp_path)[0]["error"]
