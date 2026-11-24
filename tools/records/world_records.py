"""Globals, messages, activators, lights, sounds, ability perks and music."""
import re
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.papyrus.papyrus_build import VmadData, VmadScript
from tools.records.ai_records import _next_id
from tools.records.loot_records import _finish, _lookup, _open
from tools.npcs.npc_builder import Plan, fid_bytes, z

GLOBAL_TYPES = {'float': b'f', 'short': b's', 'long': b'l'}
LIGHT_FLAGS = {'dynamic': 0x1, 'carried': 0x2, 'negative': 0x4, 'flicker': 0x8, 'off_by_default': 0x20,
               'flicker_slow': 0x40, 'pulse': 0x80, 'pulse_slow': 0x100, 'spot': 0x200, 'shadow_spot': 0x400,
               'shadow_hemisphere': 0x800, 'shadow_omni': 0x1000}
MUSIC_SINGLE_TRACK = 0x6ED7E048
# Subrecords that come before XCMO in a CELL.
BEFORE_XCMO = {'EDID', 'FULL', 'DATA', 'XCLC', 'XCLL', 'TVDT', 'LTMP', 'LNAM', 'XCLW', 'XCIM', 'XLCN'}


def _new(b, target_plugin, editor_id, sig, forms=()):
    """(path, target, plan, absolute form IDs) with masters added, ready to build record `sig`."""
    path, target = _open(b, target_plugin, editor_id)
    p = Plan()
    fids = [_lookup(b, p, f, s) for f, s in forms]
    b.add_masters(target.plugin, p)
    return path, target, fids


def _start(b, target):
    oid = _next_id(b, target.plugin)
    return oid


def create_global(b, target_plugin, editor_id, value=0.0, kind='float'):
    """GLOB variable (a quest or script can read it with GetGlobalValue / a GlobalVariable property)."""
    if kind not in GLOBAL_TYPES:
        raise ValueError(f"kind must be one of {list(GLOBAL_TYPES)}")
    path, target, _ = _new(b, target_plugin, editor_id, 'GLOB')
    oid = _start(b, target)
    rec = Record('GLOB', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('FNAM', GLOBAL_TYPES[kind])
    rec.add_subrecord('FLTV', struct.pack('<f', float(value)))
    return _finish(b, path, target, rec, oid)


def create_message(b, target_plugin, editor_id, text, title=None, buttons=None, seconds=4.0):
    """MESG. With buttons it is a message box (Message.Show returns the pressed index); otherwise a corner
    notification shown for `seconds`."""
    if not text:
        raise ValueError("text is required")
    path, target, _ = _new(b, target_plugin, editor_id, 'MESG')
    oid = _start(b, target)
    rec = Record('MESG', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('DESC', z(text))
    if title:
        rec.add_subrecord('FULL', z(title))
    rec.add_subrecord('INAM', bytes(4))
    rec.add_subrecord('DNAM', struct.pack('<I', 1 if buttons else 2))
    if not buttons:
        rec.add_subrecord('TNAM', struct.pack('<f', float(seconds)))
    for label in buttons or []:
        rec.add_subrecord('ITXT', z(label))
    return _finish(b, path, target, rec, oid)


def _donor(b, clone_from, sigs):
    return b.resolver.lookup(clone_from, sigs)[1] if clone_from else None


def create_activator(b, target_plugin, editor_id, name, model=None, clone_from=None, verb=None, script=None):
    """ACTI with a name and mesh (model path, or copied from the ACTI `clone_from`). script: a compiled script
    name to attach (see create_script). Place it with place_item."""
    if not name:
        raise ValueError("name is required")
    donor = _donor(b, clone_from, ['ACTI'])
    if not model and donor is None:
        raise ValueError("give model (a nif path) or clone_from (an activator to copy the mesh from)")
    path, target, _ = _new(b, target_plugin, editor_id, 'ACTI')
    oid = _start(b, target)
    rec = Record('ACTI', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    if script:
        vm = VmadData(version=5, obj_format=2, scripts=[VmadScript(name=script, flags=0, properties=[])])
        rec.add_subrecord('VMAD', vm.to_bytes('ACTI'))
    keep = lambda sig: bytes(donor.get_subrecord(sig).data) if donor is not None and donor.get_subrecord(sig) \
        is not None else None
    rec.add_subrecord('OBND', keep('OBND') or struct.pack('<6h', -16, -16, 0, 16, 16, 32))
    rec.add_subrecord('FULL', z(name))
    rec.add_subrecord('MODL', z(model) if model else keep('MODL'))
    if not model and keep('MODT'):
        rec.add_subrecord('MODT', keep('MODT'))
    if verb:
        rec.add_subrecord('RNAM', z(verb))
    rec.add_subrecord('FNAM', bytes(2))
    return _finish(b, path, target, rec, oid)


def create_light(b, target_plugin, editor_id, name=None, radius=256, color=(255, 255, 255), flags=None,
                 model=None, fade=1.0, value=0, weight=0.0, flicker_period=0.667, flicker_intensity=0.5,
                 flicker_movement=10.0):
    """LIGH. color is (r, g, b); flags from dynamic, carried, negative, flicker, off_by_default, flicker_slow,
    pulse, pulse_slow, spot, shadow_spot, shadow_hemisphere, shadow_omni (default: dynamic). A carried light
    (torch) needs a model; a plain placed light does not."""
    bits = 0
    for f in flags or ['dynamic']:
        if f not in LIGHT_FLAGS:
            raise ValueError(f"unknown light flag {f!r}; use {list(LIGHT_FLAGS)}")
        bits |= LIGHT_FLAGS[f]
    if bits & 0x2 and not model:
        raise ValueError("a carried light needs a model (a nif path)")
    if len(color) != 3 or not all(0 <= c <= 255 for c in color):
        raise ValueError("color must be three values from 0 to 255")
    path, target, _ = _new(b, target_plugin, editor_id, 'LIGH')
    oid = _start(b, target)
    rec = Record('LIGH', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    r = int(radius)
    rec.add_subrecord('OBND', struct.pack('<6h', -8, -8, -8, 8, 8, 8))
    if model:
        rec.add_subrecord('MODL', z(model))
    if name:
        rec.add_subrecord('FULL', z(name))
    rec.add_subrecord('DATA', struct.pack('<iI4BIffffffIf', 240 if bits & 0x2 else 0, r, *color, 0, bits, 1.0,
                                          90.0, 18.0, flicker_period, flicker_intensity, flicker_movement,
                                          int(value), float(weight)))
    rec.add_subrecord('FNAM', struct.pack('<f', float(fade)))
    return _finish(b, path, target, rec, oid)


def create_sound(b, target_plugin, editor_id, descriptor):
    """SOUN (the sound object a script's Sound property or a placed emitter needs) wrapping a sound descriptor
    (SNDR, e.g. an existing vanilla one)."""
    path, target, (sndr,) = _new(b, target_plugin, editor_id, 'SOUN', [(descriptor, ['SNDR'])])
    oid = _start(b, target)
    rec = Record('SOUN', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('OBND', bytes(12))
    rec.add_subrecord('SDSC', fid_bytes(b.local(sndr)))
    return _finish(b, path, target, rec, oid)


def create_perk(b, target_plugin, editor_id, abilities, name=None, description='', hidden=True, playable=True):
    """PERK that grants spells (abilities, e.g. from create_spell kind=ability) once the player has it.
    Add it with Actor.AddPerk. Entry-point (damage, cost...) perks are not supported."""
    if not abilities:
        raise ValueError("give at least one ability spell")
    path, target, spells = _new(b, target_plugin, editor_id, 'PERK', [(a, ['SPEL']) for a in abilities])
    oid = _start(b, target)
    rec = Record('PERK', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    if name:
        rec.add_subrecord('FULL', z(name))
    rec.add_subrecord('DESC', z(description))
    rec.add_subrecord('DATA', bytes([0, 0, 1, int(playable), int(hidden)]))
    for fid in spells:
        rec.add_subrecord('PRKE', bytes([1, 0, 0]))
        rec.add_subrecord('DATA', fid_bytes(b.local(fid)))
        rec.add_subrecord('PRKF', b'')
    return _finish(b, path, target, rec, oid)


def create_music(b, target_plugin, editor_id, file, priority=3, fade=12.0, cells=None):
    """A music type (MUSC) playing one audio file, and the track (MUST) behind it. file: path under Data, e.g.
    Music\\MyMod\\theme.xwm (you supply the file). cells: cell editor IDs in target_plugin that use it."""
    if not file or not re.search(r'\.(wav|xwm|mp3)$', file, re.I):
        raise ValueError("file must be a .wav, .xwm or .mp3 path under Data (e.g. Music\\MyMod\\theme.xwm)")
    path, target, _ = _new(b, target_plugin, editor_id, 'MUSC')
    plugin = target.plugin
    from tools.records.maintenance import all_records
    cell_recs = []
    for c in cells or []:
        cell = next((r for r in all_records(plugin) if r.signature == 'CELL'
                     and (r.editor_id or '').lower() == c.lower()), None)
        if cell is None:
            raise ValueError(f"cell {c!r} is not in {target_plugin}; create it first with create_cell")
        cell_recs.append(cell)
    oid = _start(b, target)
    track = Record('MUST', FormID(b.own(oid)), 0)
    track.add_subrecord('EDID', z(editor_id + 'Track'))
    track.add_subrecord('CNAM', struct.pack('<I', MUSIC_SINGLE_TRACK))
    track.add_subrecord('ANAM', z(file))
    track.add_subrecord('CITC', bytes(4))
    plugin.add_record(track)
    mid = oid + 1
    rec = Record('MUSC', FormID(b.own(mid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('FNAM', struct.pack('<I', 0))
    rec.add_subrecord('PNAM', struct.pack('<HH', int(priority), 0))
    rec.add_subrecord('WNAM', struct.pack('<f', float(fade)))
    rec.add_subrecord('TNAM', fid_bytes(track.form_id.value))
    for cell in cell_recs:
        cell.subrecords[:] = [s for s in cell.subrecords if s.signature != 'XCMO']
        at = max([i + 1 for i, s in enumerate(cell.subrecords) if s.signature in BEFORE_XCMO] or [0])
        cell.subrecords.insert(at, type(cell.subrecords[0])('XCMO', fid_bytes(rec.form_id.value)))
    out = _finish(b, path, target, rec, mid)
    out.update(track=f"{track.form_id.value:08X}", file=file, cells=list(cells or []),
               note='Untested in game. The audio file must exist under Data and be included when you package.')
    return out
