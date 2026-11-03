"""Controlled test: overriding records from a LIGHT (ESL) master and a normal
ESP master must write the FormID exactly as xEdit does.

Ground truth is the xEdit-authored ESLTest02.esp: a patch mastering
[Fallout4.esm, BarryFieldAtelier.esl, Gunner.esp] (the canonical ESM>ESL>ESP
order) stores, on disk:

    BFAHank (BarryFieldAtelier.esl)              -> 0x01000025  (master index 1)
    EncGunner01SniperTemplateLow (Gunner.esp)    -> 0x02000029  (master index 2)

i.e. plain master-list-index encoding. We rebuild that patch through esplib and
read the RAW header bytes back (struct, not esplib) so the check can't be fooled
by esplib's own FormID interpretation.

Needs FO4 + BarryFieldAtelier.esl + Gunner.esp installed.
"""

from __future__ import annotations

import struct

import pytest

from esplib import LoadOrder, PluginSet, Plugin
from esplib.game_discovery import find_game_data


def _raw_npc_formids(path):
    """object_id (low 24 bits) -> raw header FormID, read straight from bytes."""
    data = path.read_bytes()
    assert data[:4] == b'TES4'
    out = {}

    def walk(start, end):
        p = start
        while p + 24 <= end:
            sig = data[p:p+4]
            sz = struct.unpack_from('<I', data, p + 4)[0]
            if sig == b'GRUP':
                walk(p + 24, p + sz)
                p += sz
            else:
                fid = struct.unpack_from('<I', data, p + 12)[0]
                if sig == b'NPC_':
                    out.setdefault(fid & 0xFFFFFF, fid)
                p += 24 + sz

    walk(24 + struct.unpack_from('<I', data, 4)[0], len(data))
    return out


@pytest.mark.gamefiles
def test_esl_and_esp_override_formids(tmp_path):
    data = find_game_data("fo4")
    lo = LoadOrder.from_list(
        ['Fallout4.esm', 'BarryFieldAtelier.esl', 'Gunner.esp'],
        data_dir=str(data))
    ps = PluginSet(lo)
    ps.load_all()

    barry = ps.get_plugin('BarryFieldAtelier.esl')
    gunner = ps.get_plugin('Gunner.esp')
    bfa = next(r for r in barry.records
               if r.signature == 'NPC_' and r.editor_id == 'BFAHank')
    gn = next(r for r in gunner.records if r.signature == 'NPC_'
              and r.editor_id == 'EncGunner01SniperTemplateLow')

    out = tmp_path / "Patch.esp"
    patch = Plugin.new_plugin(str(out), masters=[], game='fo4')
    patch.plugin_set = ps
    patch.copy_record(bfa, barry)
    patch.copy_record(gn, gunner)
    patch.sort_masters()
    patch.save()

    assert patch.header.masters == [
        'Fallout4.esm', 'BarryFieldAtelier.esl', 'Gunner.esp']

    # FO4 header MUST be version 1.0 — a 0.95 header is treated as pre-ESL and
    # the game/xEdit cannot resolve references to the light master (they blank
    # the master-index byte to 0). This is the actual root cause of the bug.
    assert patch.header.version == 1.0
    assert struct.unpack_from('<f', out.read_bytes(), 30)[0] == 1.0

    raw = _raw_npc_formids(out)
    # xEdit ground truth (ESLTest02.esp):
    assert raw[0x25] == 0x01000025, f"BFAHank: got 0x{raw[0x25]:08X}"
    assert raw[0x29] == 0x02000029, \
        f"EncGunner01SniperTemplateLow: got 0x{raw[0x29]:08X}"


@pytest.mark.gamefiles
def test_gunner_only_override_remapped_by_sort(tmp_path):
    """Overriding ONLY Gunner records (so Gunner gets added as a master before
    Fallout4, landing at index 0 pre-sort) must still produce correct on-disk
    bytes after sort_masters: masters [Fallout4, Gunner], refs at index 1."""
    data = find_game_data("fo4")
    lo = LoadOrder.from_list(
        ['Fallout4.esm', 'BarryFieldAtelier.esl', 'Gunner.esp'],
        data_dir=str(data))
    ps = PluginSet(lo)
    ps.load_all()
    gunner = ps.get_plugin('Gunner.esp')

    names = ['EncGunner01SniperTemplateLow', 'EncGunner03Sniper',
             'EncGunnerTemplateLow', 'EncGunnerBossTemplate_C']
    objs = {'EncGunner01SniperTemplateLow': 0x29, 'EncGunner03Sniper': 0x2D,
            'EncGunnerTemplateLow': 0x67, 'EncGunnerBossTemplate_C': 0xC1}

    out = tmp_path / "P.esp"
    patch = Plugin.new_plugin(str(out), masters=[], game='fo4')
    patch.plugin_set = ps
    for nm in names:
        src = next(r for r in gunner.records
                   if r.signature == 'NPC_' and r.editor_id == nm)
        patch.copy_record(src, gunner)
    patch.sort_masters()
    patch.save()

    assert patch.header.masters == ['Fallout4.esm', 'Gunner.esp']
    raw = _raw_npc_formids(out)
    # Gunner is master index 1 -> every ref must be 0x0100xxxx, NOT 0x0000xxxx.
    for nm, obj in objs.items():
        assert raw[obj] == (0x01 << 24) | obj, \
            f"{nm}: got 0x{raw[obj]:08X}, expected 0x{(0x01 << 24) | obj:08X}"
