"""Plugin.sort_masters: canonical master ordering + FormID high-byte remap.

esplib adds masters in first-referenced order; FO4 requires them sorted
(master-flagged ESM/ESL before ESP). A stranded ESL otherwise mis-resolves
in the engine/xEdit. sort_masters reorders and remaps every FormID so each
reference still points at the same master by name.
"""

import struct

from esplib import Plugin, Record


def _mk(plugin, sig, fid, refs):
    rec = Record(sig, fid, 0)
    for srsig, val in refs:
        rec.add_subrecord(srsig, struct.pack('<I', val))
    plugin.add_record(rec)
    return rec


def _master_of(plugin, fid):
    hi = (fid >> 24) & 0xFF
    masters = plugin.header.masters
    return masters[hi] if hi < len(masters) else "(self)"


def _sub_fid(rec, sig):
    return struct.unpack('<I', rec.get_subrecord(sig).data)[0]


def test_sort_orders_masters_and_remaps_formids():
    # Jumbled: ESM, ESP, ESL, ESM, ESP — exactly the shape esplib emits.
    p = Plugin.new_plugin(
        "Test.esp", masters=["A.esm", "B.esp", "C.esl", "D.esm", "E.esp"],
        game="fo4")
    r1 = _mk(p, 'NPC_', 0x00000010, [('RNAM', 0x04000020)])  # A.esm / E.esp
    r2 = _mk(p, 'NPC_', 0x02000030, [('PNAM', 0x01000040)])  # C.esl / B.esp

    assert _master_of(p, r1.form_id.value) == "A.esm"
    assert _master_of(p, _sub_fid(r1, 'RNAM')) == "E.esp"

    assert p.sort_masters() is True

    # master-flagged (A.esm, C.esl, D.esm) first in original order, then ESPs.
    assert p.header.masters == ["A.esm", "C.esl", "D.esm", "B.esp", "E.esp"]

    # Every FormID still points at the SAME master by name; objects preserved.
    assert _master_of(p, r1.form_id.value) == "A.esm"
    assert r1.form_id.value & 0xFFFFFF == 0x10
    assert _master_of(p, _sub_fid(r1, 'RNAM')) == "E.esp"
    assert _sub_fid(r1, 'RNAM') & 0xFFFFFF == 0x20
    assert _master_of(p, r2.form_id.value) == "C.esl"
    assert r2.form_id.value & 0xFFFFFF == 0x30
    assert _master_of(p, _sub_fid(r2, 'PNAM')) == "B.esp"
    assert _sub_fid(r2, 'PNAM') & 0xFFFFFF == 0x40


def test_esl_moves_ahead_of_esps():
    # The reported bug: an ESL stranded after ESPs must move into the master
    # block, and references to it must follow.
    p = Plugin.new_plugin(
        "T.esp", masters=["Fallout4.esm", "Furry.esp", "Gun.esp",
                          "Barry.esl"], game="fo4")
    rec = _mk(p, 'NPC_', 0x03000025, [])  # Barry.esl, obj 0x25
    assert _master_of(p, rec.form_id.value) == "Barry.esl"

    p.sort_masters()

    assert p.header.masters == ["Fallout4.esm", "Barry.esl",
                                "Furry.esp", "Gun.esp"]
    # still Barry.esl, now at index 1
    assert _master_of(p, rec.form_id.value) == "Barry.esl"
    assert rec.form_id.value == 0x01000025


def test_noop_when_already_canonical():
    p = Plugin.new_plugin(
        "T.esp", masters=["A.esm", "C.esl", "B.esp"], game="fo4")
    _mk(p, 'NPC_', 0x00000001, [])
    assert p.sort_masters() is False
    assert p.header.masters == ["A.esm", "C.esl", "B.esp"]


def test_round_trips_through_save(tmp_path):
    path = tmp_path / "T.esp"
    p = Plugin.new_plugin(
        str(path), masters=["A.esm", "B.esp", "C.esl"], game="fo4")
    _mk(p, 'NPC_', 0x02000005, [('RNAM', 0x01000006)])  # C.esl / B.esp
    p.sort_masters()
    assert p.header.masters == ["A.esm", "C.esl", "B.esp"]
    p.save()

    q = Plugin.load(str(path))
    assert q.header.masters == ["A.esm", "C.esl", "B.esp"]
    npc = next(r for r in q.records if r.signature == 'NPC_')
    assert _master_of(q, npc.form_id.value) == "C.esl"
    assert _master_of(q, _sub_fid(npc, 'RNAM')) == "B.esp"
