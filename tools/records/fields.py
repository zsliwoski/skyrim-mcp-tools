"""Named fields inside fixed-layout subrecords (MGEF DATA, SPIT, ENIT...), for editing and decoding by name."""
import struct

from tools.records.spell import CAST_TYPES, DELIVERY

# (record signature, subrecord signature) -> {field: (offset, struct format, names)}; names maps a word to its value.
FIELDS = {
    ('MGEF', 'DATA'): {
        'flags': (0, 'I', None), 'base_cost': (4, 'f', None), 'skill': (12, 'I', None),
        'resist': (16, 'I', None), 'archetype': (64, 'I', None), 'cast_type': (80, 'I', CAST_TYPES),
        'delivery': (84, 'I', DELIVERY)},
    ('SPEL', 'SPIT'): {
        'cost': (0, 'I', None), 'flags': (4, 'I', None), 'type': (8, 'I', None), 'charge_time': (12, 'f', None),
        'cast_type': (16, 'I', CAST_TYPES), 'delivery': (20, 'I', DELIVERY), 'cast_duration': (24, 'f', None),
        'range': (28, 'f', None)},
    ('ENCH', 'ENIT'): {
        'cost': (0, 'I', None), 'flags': (4, 'I', None), 'cast_type': (8, 'I', CAST_TYPES),
        'amount': (12, 'I', None), 'delivery': (16, 'I', DELIVERY)},
    ('BOOK', 'DATA'): {'flags': (0, 'B', None), 'value': (8, 'I', None), 'weight': (12, 'f', None)},
    ('WEAP', 'EAMT'): {'charge': (0, 'H', None)},
    ('REFR', 'XCHG'): {'charge': (0, 'f', None)},
}
FIELDS[('SCRL', 'SPIT')] = FIELDS[('SPEL', 'SPIT')]


def field_table(signature):
    """(subrecord signature, {field: spec}) for a record signature that has named fields, else None."""
    for (rec, sub), table in FIELDS.items():
        if rec == signature:
            return sub, table
    return None


def encode_field(signature, field, value):
    """(subrecord signature, byte offset, bytes) for setting `field` of a `signature` record to `value`.

    value is a number or one of the field's names (e.g. cast_type 'concentration', delivery 'touch')."""
    found = field_table(signature)
    if found is None or field not in found[1]:
        known = sorted(found[1]) if found else []
        raise ValueError(f"{signature} has no named field {field!r}" + (f"; known: {known}" if known else ''))
    sub, table = found
    offset, fmt, names = table[field]
    if isinstance(value, str):
        if not names or value not in names:
            raise ValueError(f"{field} must be a number or one of {sorted(names or [])}")
        value = names[value]
    return sub, offset, struct.pack('<' + fmt, float(value) if fmt == 'f' else int(value))


def decode_fields(signature, sub_signature, data):
    """{field: value} for a subrecord with named fields; named words show as 'name (number)'."""
    table = FIELDS.get((signature, sub_signature))
    if not table:
        return None
    out = {}
    for field, (offset, fmt, names) in table.items():
        size = struct.calcsize(fmt)
        if len(data) < offset + size:
            continue
        v = struct.unpack_from('<' + fmt, data, offset)[0]
        label = next((k for k, n in names.items() if n == v), None) if names else None
        out[field] = f"{label} ({v})" if label else (round(v, 3) if fmt == 'f' else v)
    return out
