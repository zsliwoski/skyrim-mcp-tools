"""End-to-end test for FormID indexing in a LARGE, ESL-heavy load order.

A real Skyrim/FO4 modlist routinely exceeds 255 active plugins — only possible
because most are light (ESL / ESPFE) plugins, which all collapse into the single
0xFE load-order namespace (FExxxyyy) rather than each consuming a full one-byte
master index. esplib's FormID indexing must model that:

  * full (non-light) plugins   -> load-order index 0x00..0xFD
  * light (ESL/ESPFE) plugins  -> 0xFE, with a 12-bit light index in bits 12..23
  * object index               -> 24 bits (full) / 12 bits (light)

The bug this guards against: the indexing used the RAW position of a plugin in
the load-order list as a one-byte master index (`pos << 24`). Past 255 entries
that overflows 32 bits and raises FormIDError on read (the original crash); on
the write side it silently addresses the wrong master.

Rather than depend on a game install (you cannot ship 256+ real plugins, and a
user's exact list isn't reproducible), this authors a synthetic load order with
esplib itself: 256 tiny filler ESLs to push two "meaningful" masters past
position 255, then exercises read (normalize) and write (override + raw bytes)
end-to-end on real files on disk.
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from esplib import LoadOrder, PluginSet, Plugin, Record, FormID


# Pushes the meaningful masters comfortably past load-order position 255 (the
# one-byte boundary where the old `pos << 24` indexing overflowed). 256 is the
# bare minimum that crosses it; 255 would land at 0xFF, which both fits in a byte
# (no overflow) AND collides with the local-record sentinel — so we go well over.
NUM_FILLERS = 260

# Per the project convention (see furrifier test_leveled_npcs), the generated
# plugins are left on disk for manual inspection in xEdit rather than in pytest's
# auto-cleaned tmp dir. The patch masters only the 3 meaningful plugins below
# (not the 256 fillers), so Patch.esp + those 3 are all xEdit needs to open it.
OUTPUT_DIR = Path('c:/tmp/esl_large_lo')

# Every generated file shares this prefix so a copy into the game Data folder is
# trivial to clean up afterwards (delete ESLTest_*).
PREFIX = 'ESLTest_'

# Meaningful masters: filename, the GLOB record's object index, EditorID, value.
LIGHT_MASTER = PREFIX + 'LightMaster.esl'   # light: absolute high byte must be 0xFE
LIGHT_OBJ = 0x801                            # light object indices live in 0x000..0xFFF
LIGHT_EDID = 'ESLTestLightGlobal'
LIGHT_VAL = 11.0
ESPFE_MASTER = PREFIX + 'EspfeMaster.esp'    # .esp extension but Light-flagged (ESPFE)
ESPFE_OBJ = 0x802
ESPFE_EDID = 'ESLTestEspfeGlobal'
ESPFE_VAL = 22.0
FULL_MASTER = PREFIX + 'FullMaster.esp'      # full: counted among full plugins only
FULL_OBJ = 0x001234
FULL_EDID = 'ESLTestFullGlobal'
FULL_VAL = 33.0
PATCH = PREFIX + 'Patch.esp'


def _make_plugin(path, *, light, glob=None):
    """Author a minimal tes5 plugin on disk.

    `glob`, if given, is (object_index, editor_id, value) and produces a real,
    fully-formed GLOB record: EditorID + FNAM type + FLTV value (a float global),
    so the file shows meaningfully in xEdit rather than as an empty stub.
    """
    p = Plugin.new_plugin(str(path), masters=[], game='tes5')
    p.header.is_esl = light
    if glob is not None:
        obj, edid, value = glob
        rec = Record('GLOB', FormID(obj))
        rec.add_subrecord('EDID', edid)
        p.add_record(rec)
        rec['FNAM'] = ord('f')  # float-type global
        rec['FLTV'] = value
    p.save()


@pytest.fixture(scope='module')
def big_world():
    """Build a >255-entry load order on disk and return (data_dir, plugin_names).

    Written to a persistent dir (OUTPUT_DIR), rebuilt fresh each run, so the
    plugins survive for manual inspection in xEdit.

    Layout (load order, all names prefixed ESLTest_, NUM_FILLERS=260):
        Filler000.esl .. Filler259.esl   positions 0..259  (all light)
        LightMaster.esl                  position 260       (light)
        EspfeMaster.esp                  position 261       (light via flag)
        FullMaster.esp                   position 262       (full)
    """
    data = OUTPUT_DIR
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    names = []
    for i in range(NUM_FILLERS):
        name = f'{PREFIX}Filler{i:03d}.esl'
        _make_plugin(data / name, light=True)
        names.append(name)

    _make_plugin(data / LIGHT_MASTER, light=True,
                 glob=(LIGHT_OBJ, LIGHT_EDID, LIGHT_VAL))
    _make_plugin(data / ESPFE_MASTER, light=True,
                 glob=(ESPFE_OBJ, ESPFE_EDID, ESPFE_VAL))
    _make_plugin(data / FULL_MASTER, light=False,
                 glob=(FULL_OBJ, FULL_EDID, FULL_VAL))
    names += [LIGHT_MASTER, ESPFE_MASTER, FULL_MASTER]

    return data, names


def _load(big_world):
    data, names = big_world
    lo = LoadOrder.from_list(names, data_dir=str(data), game_id='tes5')
    ps = PluginSet(lo)
    ps.load_all()
    return ps


def _record_abs(ps, plugin_name, edid):
    """Normalize a plugin's own record FormID to its absolute (load-order) form."""
    plugin = ps.get_plugin(plugin_name)
    rec = next(r for r in plugin.records if r.editor_id == edid)
    return plugin.normalize_form_id(rec.form_id).value


def test_fixture_positions(big_world):
    """Sanity: the meaningful masters really do sit past position 255."""
    _, names = big_world
    assert names.index(LIGHT_MASTER) == NUM_FILLERS
    assert names.index(ESPFE_MASTER) == NUM_FILLERS + 1
    assert names.index(FULL_MASTER) == NUM_FILLERS + 2
    assert len(names) == NUM_FILLERS + 3
    assert NUM_FILLERS > 255  # must cross the one-byte master-index boundary


def test_light_master_past_255_gets_FE_namespace(big_world):
    """A light master beyond position 255 normalizes to 0xFE | lightidx<<12 | obj.

    All NUM_FILLERS light fillers precede it, so its light index is NUM_FILLERS.
    Pre-fix: `260 << 24` overflows 32 bits -> FormIDError (the original crash).
    """
    ps = _load(big_world)
    abs_val = _record_abs(ps, LIGHT_MASTER, LIGHT_EDID)
    expected = (0xFE << 24) | (NUM_FILLERS << 12) | LIGHT_OBJ
    assert abs_val == expected, f'got 0x{abs_val:08X}, want 0x{expected:08X}'


def test_espfe_master_is_light_by_flag_not_extension(big_world):
    """An .esp with the Light flag (ESPFE) must also land in the 0xFE namespace."""
    ps = _load(big_world)
    abs_val = _record_abs(ps, ESPFE_MASTER, ESPFE_EDID)
    # Light index = NUM_FILLERS + 1 (all fillers + LightMaster precede it).
    expected = (0xFE << 24) | ((NUM_FILLERS + 1) << 12) | ESPFE_OBJ
    assert abs_val == expected, f'got 0x{abs_val:08X}, want 0x{expected:08X}'


def test_full_master_counts_only_full_plugins_no_overflow(big_world):
    """A full master at raw position 258 gets full-index 0x00 (all 258 before it
    are light), proving we count only full plugins and never overflow."""
    ps = _load(big_world)
    abs_val = _record_abs(ps, FULL_MASTER, FULL_EDID)
    expected = (0x00 << 24) | FULL_OBJ
    assert abs_val == expected, f'got 0x{abs_val:08X}, want 0x{expected:08X}'


def _raw_glob_formids(path):
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
                if sig == b'GLOB':
                    out.setdefault(fid & 0xFFFFFF, fid)
                p += 24 + sz

    walk(24 + struct.unpack_from('<I', data, 4)[0], len(data))
    return out


def test_override_patch_writes_correct_formids(big_world):
    """The full end-to-end write path: override records from a light master, an
    ESPFE master, and a full master that all sit past position 255, save, and
    verify the ON-DISK FormIDs are the patch's own (small) master-list indices.

    The on-disk bytes only come out right if the FE-namespace round-trip
    (normalize -> absolute -> denormalize) is correct. Pre-fix this crashes at
    copy_record (normalize overflows on the >255 masters).
    """
    data, _ = big_world
    ps = _load(big_world)
    light = ps.get_plugin(LIGHT_MASTER)
    espfe = ps.get_plugin(ESPFE_MASTER)
    full = ps.get_plugin(FULL_MASTER)

    def rec(plugin, edid):
        return next(r for r in plugin.records if r.editor_id == edid)

    # Write the patch alongside its masters so it loads in xEdit as-is.
    out = data / PATCH
    patch = Plugin.new_plugin(str(out), masters=[], game='tes5')
    patch.plugin_set = ps
    patch.copy_record(rec(light, LIGHT_EDID), light)
    patch.copy_record(rec(espfe, ESPFE_EDID), espfe)
    patch.copy_record(rec(full, FULL_EDID), full)
    patch.sort_masters()
    patch.save()

    masters = patch.header.masters
    assert LIGHT_MASTER in masters
    assert ESPFE_MASTER in masters
    assert FULL_MASTER in masters

    raw = _raw_glob_formids(out)
    # On disk, each ref is (this-plugin's master index << 24) | object index.
    for name, obj in ((LIGHT_MASTER, LIGHT_OBJ),
                      (ESPFE_MASTER, ESPFE_OBJ),
                      (FULL_MASTER, FULL_OBJ)):
        want = (masters.index(name) << 24) | obj
        assert raw[obj] == want, \
            f'{name}: got 0x{raw[obj]:08X}, want 0x{want:08X}'

    # And reloading the patch resolves each override back to the right master.
    reloaded = Plugin(out)
    assert reloaded.header.masters == masters
