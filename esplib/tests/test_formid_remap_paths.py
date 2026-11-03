"""Every path `copy_record` uses to find FormIDs inside a subrecord.

`_remap_subrecord_formids` dispatches to a handful of strategies
depending on how a subrecord is described:

    EspFormID              a bare reference
    EspArray of EspFormID  KWDA and friends
    EspStruct              walk members, remap the EspFormID ones
    EspUnion in a struct   resolve against the data first (see
                           test_union_context.py and test_ctda_remap.py)
    EspAlternateTextures   FormIDs inside a variable-length struct array
    VMAD                   a full Papyrus parse

Only the first two and CTDA had tests. The rest were exercised solely
by consumers, and only incidentally -- which is exactly how the CTDA
bug survived: the failure is silent, producing a valid-looking
reference to the wrong record rather than an error.

Every test here copies between plugins whose master lists order the
same files differently, since a copy where the orderings happen to
agree cannot detect a missing remap.
"""

import struct

import pytest

from esplib import Plugin, Record, FormID
from esplib.vmad import (
    VmadData, VmadScript, VmadProperty, VmadObject, PROP_OBJECT,
)
import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas


# Three masters, so index 3 is the source plugin itself. The target
# lists different files, so a verbatim index 3 means something else
# there -- or nothing at all.
SOURCE_MASTERS = ['Skyrim.esm', 'Update.esm', 'Core.esm']
TARGET_MASTERS = ['Skyrim.esm', 'Dawnguard.esm']

SELF_REF = 0x03000AFF     # defined in source.esp itself
SKYRIM_REF = 0x00000019   # DefaultRace, in Skyrim.esm
CORE_REF = 0x02000ABC     # in Core.esm, which the target does not list


def resolve(plugin, raw):
    """Resolve a raw FormID to (owning plugin name, object index).

    Comparing resolved names is what makes these assertions independent
    of where each plugin lands in a given master list -- which is the
    whole point of remapping.
    """
    index = raw >> 24
    masters = plugin.header.masters
    if index < len(masters):
        name = masters[index]
    elif index == len(masters):
        name = plugin.file_path.name    # the plugin's own index
    else:
        name = f'<out of range: {index}>'
    return name, raw & 0xFFFFFF


def copy_between(tmp_path, signature, build):
    """Build a record in source.esp and copy it into target.esp."""
    source = Plugin.new_plugin(tmp_path / 'source.esp',
                               masters=SOURCE_MASTERS)
    record = Record(signature, FormID(0x03000800), 0)
    record.add_subrecord('EDID', b'TestRecord\x00')
    build(record)
    source.add_record(record)

    target = Plugin.new_plugin(tmp_path / 'target.esp',
                               masters=TARGET_MASTERS)
    return source, target, target.copy_record(record, source)


# ---------------------------------------------------------------------------
# Alternate textures (MO2S/MO3S/MO4S/MO5S)
# ---------------------------------------------------------------------------

def make_alternate_textures(entries):
    """Build alternate-texture data.

    uint32 count, then per entry:
        uint32 name_length, char[name_length] name, uint32 TXST FormID,
        uint32 3D index
    """
    out = struct.pack('<I', len(entries))
    for name, form_id, index_3d in entries:
        raw = name.encode('cp1252') + b'\x00'
        out += struct.pack('<I', len(raw)) + raw
        out += struct.pack('<II', form_id, index_3d)
    return out


def read_alternate_textures(data):
    """Decode it back to [(name, form_id, 3d_index), ...]."""
    count = struct.unpack_from('<I', data, 0)[0]
    offset = 4
    entries = []
    for _ in range(count):
        name_len = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        name = data[offset:offset + name_len].rstrip(b'\x00').decode('cp1252')
        offset += name_len
        form_id, index_3d = struct.unpack_from('<II', data, offset)
        offset += 8
        entries.append((name, form_id, index_3d))
    return entries


class TestAlternateTextures:
    """ARMO's MO2S holds TXST references inside a variable-length array."""


    def test_single_texture_reference_is_remapped(self, tmp_path):
        source, target, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord('MO2S', make_alternate_textures(
                [('Body', SELF_REF, 0)])))

        [(name, form_id, index_3d)] = read_alternate_textures(
            bytes(copied.get_subrecord('MO2S').data))
        assert name == 'Body'
        assert index_3d == 0
        assert form_id != SELF_REF, "did not move; test would be vacuous"
        assert resolve(target, form_id) == resolve(source, SELF_REF)


    def test_every_entry_is_remapped_not_just_the_first(self, tmp_path):
        """The walk steps over each variable-length name to find the next."""
        entries = [('Body', SELF_REF, 0),
                   ('Hands', CORE_REF, 1),
                   ('LongerNameHere', SELF_REF, 2)]
        source, target, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord('MO2S',
                                      make_alternate_textures(entries)))

        got = read_alternate_textures(bytes(copied.get_subrecord('MO2S').data))
        assert [g[0] for g in got] == [e[0] for e in entries]
        assert [g[2] for g in got] == [e[2] for e in entries]
        for (_, original, _), (_, moved, _) in zip(entries, got):
            assert resolve(target, moved) == resolve(source, original)


    def test_names_of_varying_length_keep_entries_aligned(self, tmp_path):
        """A miscounted name length would corrupt every later entry."""
        entries = [('A', SELF_REF, 0), ('BBBBBBBBBBBBBBBB', SELF_REF, 7)]
        _source, _target, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord('MO2S',
                                      make_alternate_textures(entries)))
        got = read_alternate_textures(bytes(copied.get_subrecord('MO2S').data))
        assert [(g[0], g[2]) for g in got] == [('A', 0), ('BBBBBBBBBBBBBBBB', 7)]


    def test_empty_list_is_handled(self, tmp_path):
        _s, _t, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord('MO2S', make_alternate_textures([])))
        assert bytes(copied.get_subrecord('MO2S').data) == struct.pack('<I', 0)


    def test_truncated_data_does_not_crash(self, tmp_path):
        """A count promising more entries than the bytes hold."""
        truncated = struct.pack('<I', 5) + struct.pack('<I', 4) + b'ab\x00'
        _s, _t, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord('MO2S', truncated))
        assert copied.get_subrecord('MO2S') is not None


# ---------------------------------------------------------------------------
# VMAD (Papyrus script data)
# ---------------------------------------------------------------------------

def make_vmad(form_ids, script_name='TestScript'):
    vmad = VmadData(version=5, obj_format=2)
    script = VmadScript(name=script_name, flags=0)
    for i, form_id in enumerate(form_ids):
        script.properties.append(VmadProperty(
            name=f'Prop{i}', type=PROP_OBJECT, flags=1,
            value=VmadObject(form_id=form_id, alias=-1)))
    vmad.scripts.append(script)
    return vmad.to_bytes()


def vmad_form_ids(data):
    return [p.value.form_id
            for s in VmadData.parse(data).scripts for p in s.properties]


class TestVmad:
    """test_vmad.py covers VmadData.remap_form_ids in isolation.

    What was untested is the copy_record -> _remap_vmad integration:
    that a record carrying a script property actually gets it remapped
    when copied across differing master lists.
    """


    def test_script_property_form_is_remapped(self, tmp_path):
        source, target, copied = copy_between(
            tmp_path, 'QUST',
            lambda r: r.add_subrecord('VMAD', make_vmad([SELF_REF])))

        [moved] = vmad_form_ids(bytes(copied.get_subrecord('VMAD').data))
        assert moved != SELF_REF, "did not move; test would be vacuous"
        assert resolve(target, moved) == resolve(source, SELF_REF)


    def test_every_property_is_remapped(self, tmp_path):
        originals = [SELF_REF, SKYRIM_REF, CORE_REF]
        source, target, copied = copy_between(
            tmp_path, 'QUST',
            lambda r: r.add_subrecord('VMAD', make_vmad(originals)))

        moved = vmad_form_ids(bytes(copied.get_subrecord('VMAD').data))
        assert len(moved) == len(originals)
        for original, new in zip(originals, moved):
            assert resolve(target, new) == resolve(source, original)


    def test_script_name_survives(self, tmp_path):
        _s, _t, copied = copy_between(
            tmp_path, 'QUST',
            lambda r: r.add_subrecord(
                'VMAD', make_vmad([SELF_REF], script_name='MyQuestScript')))
        parsed = VmadData.parse(bytes(copied.get_subrecord('VMAD').data))
        assert parsed.scripts[0].name == 'MyQuestScript'


    def test_unparseable_vmad_is_preserved_verbatim(self, tmp_path, caplog):
        """A VMAD the parser chokes on must not fail the whole copy.

        Its bytes carry through unchanged and the gap is logged, rather
        than the record being dropped or half-written.
        """
        import logging
        garbage = struct.pack('<hhh', 5, 2, 99) + b'\x00\x02'
        with caplog.at_level(logging.WARNING):
            _s, _t, copied = copy_between(
                tmp_path, 'QUST',
                lambda r: r.add_subrecord('VMAD', garbage))
        assert bytes(copied.get_subrecord('VMAD').data) == garbage
        assert 'VMAD' in caplog.text


    def test_a_record_without_vmad_is_untouched(self, tmp_path):
        _s, _t, copied = copy_between(
            tmp_path, 'QUST', lambda r: None)
        assert copied.get_subrecord('VMAD') is None


# ---------------------------------------------------------------------------
# Structs containing FormIDs
# ---------------------------------------------------------------------------

class TestStructMembers:
    """The walk advances by each member's byte_size to find the next."""


    def test_form_at_the_start_of_a_struct(self, tmp_path):
        """COBJ's CNTO is FormID + int32."""
        source, target, copied = copy_between(
            tmp_path, 'COBJ',
            lambda r: r.add_subrecord(
                'CNTO', struct.pack('<Ii', SELF_REF, 7)))

        form_id, count = struct.unpack(
            '<Ii', bytes(copied.get_subrecord('CNTO').data))
        assert count == 7, "the non-FormID member must not move"
        assert form_id != SELF_REF, "did not move; test would be vacuous"
        assert resolve(target, form_id) == resolve(source, SELF_REF)


    def test_form_after_earlier_members(self, tmp_path):
        """LVLO is level(u16) + padding(2) + FormID + count(u16).

        The FormID sits at offset 4, so this fails if the walk does not
        accumulate the preceding members' sizes correctly.
        """
        source, target, copied = copy_between(
            tmp_path, 'LVLI',
            lambda r: r.add_subrecord(
                'LVLO', struct.pack('<HHIH', 12, 0, SELF_REF, 3)))

        level, _pad, form_id, count = struct.unpack(
            '<HHIH', bytes(copied.get_subrecord('LVLO').data))
        assert (level, count) == (12, 3), "surrounding members must not move"
        assert resolve(target, form_id) == resolve(source, SELF_REF)


    def test_repeated_struct_subrecords_are_all_remapped(self, tmp_path):
        def build(record):
            for fid in (SELF_REF, CORE_REF, SKYRIM_REF):
                record.add_subrecord('CNTO', struct.pack('<Ii', fid, 1))

        source, target, copied = copy_between(tmp_path, 'COBJ', build)
        moved = [struct.unpack('<Ii', bytes(sr.data))[0]
                 for sr in copied.get_subrecords('CNTO')]
        for original, new in zip((SELF_REF, CORE_REF, SKYRIM_REF), moved):
            assert resolve(target, new) == resolve(source, original)


# ---------------------------------------------------------------------------
# The strategies that were already covered, pinned here alongside the rest
# ---------------------------------------------------------------------------

class TestBareAndArrayForms:


    def test_bare_formid_subrecord(self, tmp_path):
        """COBJ's CNAM is a plain EspFormID."""
        source, target, copied = copy_between(
            tmp_path, 'COBJ',
            lambda r: r.add_subrecord('CNAM', struct.pack('<I', SELF_REF)))
        moved = struct.unpack('<I', bytes(copied.get_subrecord('CNAM').data))[0]
        assert resolve(target, moved) == resolve(source, SELF_REF)


    def test_array_of_formids(self, tmp_path):
        """KWDA is an EspArray of EspFormID -- every element moves."""
        originals = [SELF_REF, SKYRIM_REF, CORE_REF]
        source, target, copied = copy_between(
            tmp_path, 'ARMO',
            lambda r: r.add_subrecord(
                'KWDA', struct.pack(f'<{len(originals)}I', *originals)))

        data = bytes(copied.get_subrecord('KWDA').data)
        moved = struct.unpack(f'<{len(originals)}I', data)
        for original, new in zip(originals, moved):
            assert resolve(target, new) == resolve(source, original)
