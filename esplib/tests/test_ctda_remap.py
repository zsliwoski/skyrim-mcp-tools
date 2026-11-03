"""CTDA (condition) FormID remapping across plugins with differing masters.

CTDA is stored as an opaque 32-byte blob, so the schema-driven FormID
walk in `_remap_subrecord_formids` cannot see the FormIDs embedded in
it. Without dedicated handling the raw bytes are copied verbatim,
keeping the *source* plugin's master index, which is then reinterpreted
against the *destination* plugin's differently-ordered master list --
silently producing a valid-looking reference to the wrong record.

Four slots inside a CTDA can hold a FormID (see xEdit's CTDA struct):
    offset  4  Comparison Value -- a GLOB ref when type flag 0x04 is set
    offset 12  Parameter #1     -- when the function's ParamType1 is a form
    offset 16  Parameter #2     -- when the function's ParamType2 is a form
    offset 24  Reference        -- when Run On == 2 (Reference)
"""

import struct

import pytest

from esplib import Plugin, Record, FormID, LoadOrder, PluginSet
from esplib.game_discovery import find_game
import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas


# Condition function indices used below (from xEdit's wbConditionFunctions).
GET_ACTOR_VALUE = 14           # ParamType1: ptActorValue (NOT a form)
GET_FACTION_RANK_DIFF = 60     # ParamType1: ptFaction, ParamType2: ptActor
GET_IS_RACE = 69               # ParamType1: ptRace
GET_FACTION_RANK = 73          # ParamType1: ptFaction

CTDA_USE_GLOBAL = 0x04         # type-byte flag: comparison value is a GLOB
RUN_ON_SUBJECT = 0
RUN_ON_REFERENCE = 2


def make_ctda(function, param1=0, param2=0, ctype=0x00,
              comparison=b'\x00\x00\x00\x00', run_on=RUN_ON_SUBJECT,
              reference=0, param3=-1):
    """Build a raw 32-byte Skyrim CTDA blob."""
    assert len(comparison) == 4
    return (struct.pack('<B3s', ctype, b'\x00\x00\x00')
            + comparison
            + struct.pack('<H2s', function, b'\x00\x00')
            + struct.pack('<II', param1, param2)
            + struct.pack('<I', run_on)
            + struct.pack('<I', reference)
            + struct.pack('<i', param3))


def ctda_fields(data):
    """Decode the FormID-bearing slots back out of a CTDA blob."""
    return {
        'comparison': struct.unpack_from('<I', data, 4)[0],
        'function': struct.unpack_from('<H', data, 8)[0],
        'param1': struct.unpack_from('<I', data, 12)[0],
        'param2': struct.unpack_from('<I', data, 16)[0],
        'run_on': struct.unpack_from('<I', data, 20)[0],
        'reference': struct.unpack_from('<I', data, 24)[0],
    }


# The source plugin has three masters, so index 3 is the plugin itself --
# exactly the Schlongs of Skyrim shape that surfaced this bug. The target
# has a different master list, so a verbatim index 3 means something else
# there (or nothing at all).
SOURCE_MASTERS = ['Skyrim.esm', 'Update.esm', 'Core.esm']
TARGET_MASTERS = ['Skyrim.esm', 'Dawnguard.esm']

SELF_REF = 0x0300AFF8      # defined in source.esp itself (master index 3)


def build_pair(tmp_path, ctda_blobs, control_ref=SELF_REF):
    """Copy a COBJ carrying `ctda_blobs` from source into target.

    COBJ's CNAM is a plain schema-declared FormID pointing at the same
    form as the conditions under test, so it acts as a control: whatever
    CNAM remaps to is what a correctly-remapped CTDA must also hold.
    """
    source = Plugin.new_plugin(tmp_path / 'source.esp',
                               masters=SOURCE_MASTERS)
    rec = Record('COBJ', FormID(0x03000800), 0)
    rec.add_subrecord('EDID', b'TestRecipe\x00')
    rec.add_subrecord('CITC', struct.pack('<I', len(ctda_blobs)))
    for blob in ctda_blobs:
        rec.add_subrecord('CTDA', blob)
    rec.add_subrecord('CNAM', struct.pack('<I', control_ref))
    source.add_record(rec)

    target = Plugin.new_plugin(tmp_path / 'target.esp',
                               masters=TARGET_MASTERS)
    copied = target.copy_record(rec, source)
    control = struct.unpack('<I', bytes(
        copied.get_subrecord('CNAM').data))[0]
    conditions = [ctda_fields(bytes(sr.data))
                  for sr in copied.get_subrecords('CTDA')]
    return control, conditions


class TestCtdaParameterRemap:


    def test_param1_form_is_remapped(self, tmp_path):
        """GetIsRace's Parameter #1 is a RACE form and must be remapped."""
        control, conds = build_pair(
            tmp_path, [make_ctda(GET_IS_RACE, param1=SELF_REF)])
        assert control != SELF_REF, "control did not move; test is vacuous"
        assert conds[0]['param1'] == control


    def test_param2_form_is_remapped(self, tmp_path):
        """GetFactionRankDifference takes a form in both parameters."""
        control, conds = build_pair(
            tmp_path,
            [make_ctda(GET_FACTION_RANK_DIFF,
                       param1=SELF_REF, param2=SELF_REF)])
        assert conds[0]['param1'] == control
        assert conds[0]['param2'] == control


    def test_non_form_param_is_left_alone(self, tmp_path):
        """GetActorValue's Parameter #1 is an actor-value index, not a form.

        Its bit pattern can look exactly like a FormID with master index
        3; remapping it would corrupt the condition.
        """
        raw = 0x03000019
        _, conds = build_pair(
            tmp_path, [make_ctda(GET_ACTOR_VALUE, param1=raw)])
        assert conds[0]['param1'] == raw


    def test_unknown_function_leaves_parameters_alone(self, tmp_path):
        """An unrecognised function index must not crash or guess."""
        raw = 0x03000019
        _, conds = build_pair(
            tmp_path, [make_ctda(60000, param1=raw, param2=raw)])
        assert conds[0]['param1'] == raw
        assert conds[0]['param2'] == raw


class TestCtdaComparisonValue:


    def test_global_comparison_is_remapped(self, tmp_path):
        """With the 'use global' flag set, the comparison value is a GLOB."""
        control, conds = build_pair(
            tmp_path,
            [make_ctda(GET_IS_RACE, param1=SELF_REF,
                       ctype=CTDA_USE_GLOBAL,
                       comparison=struct.pack('<I', SELF_REF))])
        assert conds[0]['comparison'] == control


    def test_float_comparison_is_left_alone(self, tmp_path):
        """Without the flag the comparison value is a float, not a form."""
        raw_float = struct.pack('<f', 1.0)
        _, conds = build_pair(
            tmp_path,
            [make_ctda(GET_IS_RACE, param1=SELF_REF, comparison=raw_float)])
        assert conds[0]['comparison'] == struct.unpack('<I', raw_float)[0]


class TestCtdaRunOnReference:


    def test_reference_is_remapped_when_run_on_reference(self, tmp_path):
        """Run On == 2 makes offset 24 a REFR-ish form."""
        control, conds = build_pair(
            tmp_path,
            [make_ctda(GET_IS_RACE, param1=SELF_REF,
                       run_on=RUN_ON_REFERENCE, reference=SELF_REF)])
        assert conds[0]['reference'] == control


    def test_reference_is_left_alone_for_other_run_on(self, tmp_path):
        """Any other Run On leaves offset 24 as an unused integer."""
        raw = 0x03000019
        _, conds = build_pair(
            tmp_path,
            [make_ctda(GET_IS_RACE, param1=SELF_REF,
                       run_on=RUN_ON_SUBJECT, reference=raw)])
        assert conds[0]['reference'] == raw


class TestCtdaStructuralIntegrity:


    def test_untouched_bytes_survive(self, tmp_path):
        """Only the FormID slots may change; everything else is verbatim."""
        blob = make_ctda(GET_IS_RACE, param1=SELF_REF, ctype=0x41,
                         comparison=struct.pack('<f', 2.5), param3=-1)
        source = Plugin.new_plugin(tmp_path / 'source.esp',
                                   masters=SOURCE_MASTERS)
        rec = Record('COBJ', FormID(0x03000800), 0)
        rec.add_subrecord('CTDA', blob)
        source.add_record(rec)
        target = Plugin.new_plugin(tmp_path / 'target.esp',
                                   masters=TARGET_MASTERS)
        copied = target.copy_record(rec, source)
        out = bytes(copied.get_subrecord('CTDA').data)

        assert len(out) == 32
        assert out[0:4] == blob[0:4]      # type + unused
        assert out[4:8] == blob[4:8]      # float comparison
        assert out[8:12] == blob[8:12]    # function + unused
        assert out[16:24] == blob[16:24]  # param2 + run on
        assert out[28:32] == blob[28:32]  # param3


    def test_multiple_conditions_all_remapped(self, tmp_path):
        """Every CTDA on a record is handled, not just the first."""
        control, conds = build_pair(tmp_path, [
            make_ctda(GET_IS_RACE, param1=SELF_REF),
            make_ctda(GET_FACTION_RANK, param1=SELF_REF),
            make_ctda(GET_IS_RACE, param1=SELF_REF),
        ])
        assert len(conds) == 3
        assert all(c['param1'] == control for c in conds)


    def test_short_ctda_does_not_crash(self, tmp_path):
        """A truncated CTDA is left alone rather than raising."""
        source = Plugin.new_plugin(tmp_path / 'source.esp',
                                   masters=SOURCE_MASTERS)
        rec = Record('COBJ', FormID(0x03000800), 0)
        rec.add_subrecord('CTDA', b'\x00' * 20)
        source.add_record(rec)
        target = Plugin.new_plugin(tmp_path / 'target.esp',
                                   masters=TARGET_MASTERS)
        copied = target.copy_record(rec, source)
        assert bytes(copied.get_subrecord('CTDA').data) == b'\x00' * 20


# The condition that surfaced this bug: SOS_SetupNPCMagicEffect gates
# itself on GetFactionRank(SOS_SchlongifiedFaction) > 0 -- the "already
# set up, don't re-roll" check. The faction is defined in Schlongs of
# Skyrim.esp itself, so the reference carries that plugin's *self* master
# index, which is meaningless in a patch with a shorter master list.
SOS_PLUGIN = 'Schlongs of Skyrim.esp'
SOS_LOAD_ORDER = [
    'Skyrim.esm', 'Update.esm', 'Dawnguard.esm', 'HearthFires.esm',
    'Dragonborn.esm', 'Schlongs of Skyrim - Core.esm', SOS_PLUGIN,
]
SOS_SETUP_MGEF = 'SOS_SetupNPCMagicEffect'
GET_FACTION_RANK_PARAM1 = 12


class TestSosRegression:


    @pytest.fixture(scope='class')
    def sos_set(self):
        game = find_game('tes5')
        if game is None:
            pytest.skip("Skyrim SE not installed")
        missing = [name for name in SOS_LOAD_ORDER
                   if not (game.data_dir / name).exists()]
        if missing:
            pytest.skip(f"not deployed: {', '.join(missing)}")
        load_order = LoadOrder.from_list(
            SOS_LOAD_ORDER, data_dir=game.data_dir, game_id='tes5')
        plugin_set = PluginSet(load_order)
        plugin_set.load_all()
        return plugin_set, game.data_dir


    @pytest.mark.gamefiles
    def test_faction_gate_survives_a_shorter_master_list(self, sos_set):
        """The GetFactionRank gate must still resolve after a copy.

        The patch deliberately carries fewer masters than SOS does, so
        SOS's own self-index no longer names the same plugin. Copying
        without remapping leaves an out-of-range index that resolves to
        a record the patch does not define, and the gate can never be
        true -- the symptom being NPCs re-rolling their assignment.
        """
        plugin_set, data_dir = sos_set
        sos = plugin_set.get_plugin(SOS_PLUGIN)
        mgef = next(r for r in sos.get_records_by_signature('MGEF')
                    if r.editor_id == SOS_SETUP_MGEF)

        conditions = mgef.get_subrecords('CTDA')
        source_raw = struct.unpack_from(
            '<I', bytes(conditions[-1].data), GET_FACTION_RANK_PARAM1)[0]
        expected = sos.normalize_form_id(source_raw).value

        patch = Plugin.new_plugin(
            data_dir / 'ctda-regression-patch.esp',
            masters=['Skyrim.esm', 'Schlongs of Skyrim - Core.esm',
                     SOS_PLUGIN])
        patch.plugin_set = plugin_set
        copied = patch.copy_record(mgef, sos)

        copied_raw = struct.unpack_from(
            '<I', bytes(copied.get_subrecords('CTDA')[-1].data),
            GET_FACTION_RANK_PARAM1)[0]

        assert copied_raw != source_raw, (
            "master index did not move; the test no longer exercises "
            "the reordering it was written for")
        assert patch.normalize_form_id(copied_raw).value == expected
