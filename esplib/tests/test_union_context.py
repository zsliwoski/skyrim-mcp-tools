"""EspUnion resolution against sibling fields.

A union inside a struct decides which branch is active by looking at
other fields of the same struct. Reading fills those in as it goes;
writing has all of them up front. Both sides run the *same* decider, so
serializing is a decision rather than a guess -- which matters where the
branches are indistinguishable by Python type.
"""

import struct

import pytest

from esplib.defs.context import EspContext
from esplib.defs.types import (
    EspStruct, EspUnion, EspInteger, EspFloat, EspFormID, EspByteArray,
    IntType,
)
from esplib.utils import BinaryReader, FormID


# A struct whose second field is an integer or a FormID depending on a
# flag in the first. The two branches are byte-identical and, once
# parsed, near-indistinguishable by value -- 5 and FormID(5) both live
# in the same four bytes.
TAGGED = EspStruct.new('tagged', [
    EspInteger.new('kind', IntType.U32),
    EspUnion.new(
        'payload',
        decider=lambda ctx: 1 if ctx.fields.get('kind') == 1 else 0,
        members=[EspInteger.new('payload', IntType.U32),
                 EspFormID.new('payload')]),
])


def parse(data, ctx=None):
    return TAGGED.from_bytes(BinaryReader(data), ctx or EspContext(),
                             available=len(data))


class TestSiblingFieldResolution:


    def test_decider_reads_an_earlier_sibling(self):
        as_int = parse(struct.pack('<II', 0, 5))
        as_form = parse(struct.pack('<II', 1, 5))
        assert as_int['payload'] == 5
        assert not isinstance(as_int['payload'], FormID)
        assert isinstance(as_form['payload'], FormID)
        assert as_form['payload'].value == 5


    @pytest.mark.parametrize('kind', [0, 1])
    def test_round_trip_is_byte_exact(self, kind):
        raw = struct.pack('<II', kind, 0x00012345)
        assert TAGGED.to_bytes(parse(raw)) == raw


    def test_write_uses_the_decider_not_the_value_type(self):
        """The tag decides, even when the value looks like the other branch.

        A bare int in a FormID slot must still be written; this is the
        case the old try-each-member fallback got right only by luck.
        """
        value = {'kind': 1, 'payload': 7}
        assert TAGGED.to_bytes(value) == struct.pack('<II', 1, 7)


    def test_changing_the_tag_changes_what_the_value_must_be(self):
        """An inconsistent edit fails loudly rather than writing garbage."""
        value = parse(struct.pack('<II', 1, 5))
        value['kind'] = 0                       # now the int branch...
        assert isinstance(value['payload'], FormID)   # ...but still a form
        with pytest.raises(TypeError, match="payload"):
            TAGGED.to_bytes(value)


class TestNestedStructIsolation:


    def test_inner_struct_does_not_clobber_outer_fields(self):
        """Each struct gets its own field scope.

        Without isolation a nested struct's members would overwrite the
        outer struct's, and an outer union after the nested one would
        decide from the wrong values.
        """
        inner = EspStruct.new('inner', [EspInteger.new('kind', IntType.U32)])
        outer = EspStruct.new('outer', [
            EspInteger.new('kind', IntType.U32),
            inner,
            EspUnion.new(
                'payload',
                decider=lambda ctx: 1 if ctx.fields.get('kind') == 1 else 0,
                members=[EspInteger.new('payload', IntType.U32),
                         EspFormID.new('payload')]),
        ])
        # outer kind = 1 (form), inner kind = 0 -- the inner value must
        # not be what the outer union sees.
        raw = struct.pack('<III', 1, 0, 9)
        parsed = outer.from_bytes(BinaryReader(raw), EspContext(),
                                  available=len(raw))
        assert isinstance(parsed['payload'], FormID)
        assert outer.to_bytes(parsed) == raw


class TestUnionByteSize:


    def test_size_reported_when_branches_agree(self):
        union = EspUnion.new('u', decider=lambda ctx: 0, members=[
            EspInteger.new('a', IntType.U32), EspFormID.new('b')])
        assert union.byte_size == 4


    def test_no_size_when_branches_disagree(self):
        union = EspUnion.new('u', decider=lambda ctx: 0, members=[
            EspInteger.new('a', IntType.U16), EspFormID.new('b')])
        assert union.byte_size is None


    def test_no_size_for_a_variable_length_branch(self):
        union = EspUnion.new('u', decider=lambda ctx: 0, members=[
            EspByteArray.new('a'), EspFormID.new('b')])
        assert union.byte_size is None


class TestExistingUnionsUnaffected:
    """FULL/DESC sit directly under a subrecord, not inside a struct.

    They have no sibling fields, so they must keep resolving the way
    they always did -- by subrecord size on read, and by trying each
    member on write.
    """


    def test_localized_and_inline_full_still_resolve(self):
        from esplib.defs.common import FULL

        class _Sub:
            def __init__(self, data):
                self.data = data
                self.size = len(data)

        localized = FULL.from_subrecord(_Sub(struct.pack('<I', 0x1234)))
        assert localized == 0x1234

        inline = FULL.from_subrecord(_Sub(b'Iron Sword\x00'))
        assert inline == 'Iron Sword'


    def test_inline_full_serializes_without_sibling_context(self):
        from esplib.defs.common import FULL
        assert FULL.to_subrecord_data('Iron Sword') == b'Iron Sword\x00'


class TestMemberOffset:
    """Offsets come from the schema so callers stop keeping their own copy."""


    def test_condition_offsets_match_the_documented_layout(self):
        from esplib.defs.common import CONDITION
        assert [CONDITION.member_offset(n) for n in (
            'type', 'comparison', 'function', 'param1',
            'param2', 'run_on', 'reference', 'param3',
        )] == [0, 4, 8, 12, 16, 20, 24, 28]


    def test_unknown_member_raises(self):
        with pytest.raises(KeyError):
            TAGGED.member_offset('nope')


    def test_variable_length_member_in_front_raises(self):
        """A variable-length field means later offsets are not constants."""
        blob = EspStruct.new('blob', [
            EspByteArray.new('head'),           # no fixed size
            EspInteger.new('tail', IntType.U32),
        ])
        with pytest.raises(ValueError, match='not at a constant offset'):
            blob.member_offset('tail')
