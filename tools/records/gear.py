"""Weapon and armor builders: clone a vanilla piece, then set its stats by name instead of by raw bytes."""
import struct

from tools.core.plugin_edit import Target, resolve_target_path
from tools.records.clone import clone_record


def _set(rec, sig, fn):
    sub = next((s for s in rec.subrecords if s.signature == sig), None)
    if sub is None:
        raise ValueError(f"{rec.signature} {rec.editor_id} has no {sig} subrecord to set stats on")
    sub.data = fn(bytearray(bytes(sub.data)))


def _patch(b, res, target_plugin, editor_id, sig, edits):
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    rec = target.find_own(editor_id, [sig])
    for subsig, fn in edits:
        _set(rec, subsig, fn)
    target.save()
    res['created']['stats'] = {k: v for k, v in res.pop('stats').items() if v is not None}
    return res


def _poke(fmt, offset, value):
    def apply(data):
        struct.pack_into(fmt, data, offset, value)
        return bytes(data)
    return apply


def create_weapon(b, target_plugin, source, editor_id, name=None, damage=None, value=None, weight=None,
                  speed=None, reach=None, description=None):
    """Clone a vanilla weapon and set damage, gold value, weight, speed and reach (None keeps the source's)."""
    for label, v in (('damage', damage), ('value', value), ('weight', weight), ('speed', speed), ('reach', reach)):
        if v is not None and v < 0:
            raise ValueError(f"{label} cannot be negative")
    res = clone_record(b, source, target_plugin, editor_id, name, description, 'WEAP')
    edits = []
    if value is not None:
        edits.append(('DATA', _poke('<i', 0, int(value))))
    if weight is not None:
        edits.append(('DATA', _poke('<f', 4, float(weight))))
    if damage is not None:
        edits.append(('DATA', _poke('<H', 8, int(damage))))
    if speed is not None:
        edits.append(('DNAM', _poke('<f', 4, float(speed))))
    if reach is not None:
        edits.append(('DNAM', _poke('<f', 8, float(reach))))
    res['stats'] = dict(damage=damage, value=value, weight=weight, speed=speed, reach=reach)
    return _patch(b, res, target_plugin, editor_id, 'WEAP', edits)


def create_armor(b, target_plugin, source, editor_id, name=None, armor_rating=None, value=None, weight=None,
                 description=None):
    """Clone a vanilla armor piece and set its armor rating (as shown in game), gold value and weight."""
    for label, v in (('armor_rating', armor_rating), ('value', value), ('weight', weight)):
        if v is not None and v < 0:
            raise ValueError(f"{label} cannot be negative")
    res = clone_record(b, source, target_plugin, editor_id, name, description, 'ARMO')
    edits = []
    if value is not None:
        edits.append(('DATA', _poke('<i', 0, int(value))))
    if weight is not None:
        edits.append(('DATA', _poke('<f', 4, float(weight))))
    if armor_rating is not None:
        edits.append(('DNAM', _poke('<i', 0, int(round(float(armor_rating) * 100)))))
    res['stats'] = dict(armor_rating=armor_rating, value=value, weight=weight)
    return _patch(b, res, target_plugin, editor_id, 'ARMO', edits)
