"""Create an ENCH (object effect) record from a plain description of its magic effects."""
import re
import struct
from pathlib import Path

from esplib.vmad import VmadData, VmadScript

from tools.papyrus.papyrus_build import compile_script
from tools.records.record_create import create_record

# DATA of the vanilla EnchFortifyDestructionConstantSelf MGEF; the fields that differ are patched below.
MGEF_DATA = bytes.fromhex(
    "020a20000000d0410000000016000000ffffffff00000000000000000000000000000000e72d0900000000000000000000000000000000000000000000000000220000006e00000000000000000000000000000000000000ffffffff00000000000000000000000000000000000000000000803f000000000000000000000000000000000000000000000000000000000000000000000000")
assert len(MGEF_DATA) == 152

# (cast type, delivery, enchant type) per kind. Weapons fire on hit (touch); worn gear is constant self.
KINDS = {
    'weapon': (1, 1, 0x06),
    'armor': (0, 0, 0x06),
    'staff': (1, 2, 0x0C),
}

# Spell delivery -> (vanilla effect to copy, whether the copy becomes a concentration effect). These vanilla
# spell effects carry the flags, keywords and hidden conditions the game expects for that delivery, and no
# random-chance condition. A ray is the aimed effect with the concentration cast type. Skyrim has no working
# touch-delivery spell for script effects (in game it either did nothing or would not cast), so 'touch' is a
# ray whose script gives up beyond TOUCH_RANGE units.
TOUCH_RANGE = 192.0
SPELL_EFFECT_BASES = {
    'touch': ('ParalysisFFAimed', True),
    'aimed': ('ParalysisFFAimed', False),
    'ray': ('ParalysisFFAimed', True),
    'self': ('ParalysisFFSelfArea', False),
}

def build_subrecords(name, effects, kind='weapon', cost=0, auto_calc=True, charge_time=0.0):
    """Subrecord specs for create_record: OBND, FULL, ENIT, then one EFID/EFIT pair per effect."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    if not effects:
        raise ValueError("give at least one effect")
    cast, delivery, ench_type = KINDS[kind]
    enit = struct.pack('<iIIiIIfII', int(cost), 0 if auto_calc else 1, cast, int(cost), delivery,
                       ench_type, float(charge_time), 0, 0)
    subs = [{'sig': 'OBND', 'hex': '00' * 12}, {'sig': 'FULL', 'text': name}, {'sig': 'ENIT', 'hex': enit.hex()}]
    for e in effects:
        if 'effect' not in e:
            raise ValueError("each effect needs an 'effect' (MGEF editor ID, name or form ID)")
        duration = int(e.get('duration', 0))
        if kind == 'armor' and duration:
            raise ValueError("constant armor enchantments use duration 0")
        efit = struct.pack('<fII', float(e.get('magnitude', 0)), int(e.get('area', 0)), duration)
        subs += [{'sig': 'EFID', 'form': e['effect'], 'sigs': ['MGEF']}, {'sig': 'EFIT', 'hex': efit.hex()}]
    return subs


def auto_cost(builder, effects):
    """Enchantment cost like the CK's auto-calc: each effect's MGEF base cost times its magnitude (at least 1)."""
    total = 0.0
    for e in effects:
        _, rec = builder.resolver.lookup(e['effect'], ['MGEF'])
        data = rec.get_subrecord('DATA')
        base = struct.unpack_from('<f', bytes(data.data), 4)[0] if data is not None and len(data.data) >= 8 else 0.0
        total += base * max(float(e.get('magnitude', 0)), 1.0)
    return max(int(round(total)), 1)


def create_enchantment(builder, target_plugin, editor_id, name, effects, kind='weapon', cost=0, charge=0,
                       auto_calc=True, charge_time=0.0):
    """Add an ENCH to target_plugin. Apply it with edit_record (EITM, plus EAMT for weapons).

    A cost of 0 is replaced by the auto-calculated one: vanilla enchantments all carry a non-zero cost and
    amount, and a weapon enchanted with a zero-cost ENCH shows no charge in game."""
    if not cost and auto_calc:
        cost = auto_cost(builder, effects)
    subs = build_subrecords(name, effects, kind, cost, auto_calc, charge_time)
    return create_record(builder, target_plugin, 'ENCH', editor_id, subs)


def script_effect_data(kind='armor'):
    """DATA bytes for a Script-archetype MGEF: no actor value, constant/self or fire-and-forget/touch.

    Weapon and staff effects copy the flags and magic skill of vanilla EnchParalysisFFContact (hostile,
    no magnitude, no hit effect, Alteration); the worn-gear template's flags left such enchantments without
    a charge bar or recharge option."""
    cast, delivery, _ = KINDS[kind]
    d = bytearray(MGEF_DATA)
    struct.pack_into('<I', d, 36, 0)        # the template's enchant shader is a form ID
    struct.pack_into('<i', d, 12, -1)       # magic skill
    struct.pack_into('<I', d, 64, 1)        # archetype: Script
    struct.pack_into('<i', d, 68, -1)       # actor value
    struct.pack_into('<II', d, 80, cast, delivery)
    if kind != 'armor':
        struct.pack_into('<I', d, 0, 0x00401403)
        struct.pack_into('<i', d, 12, 0x12)
    return bytes(d)


MAGIC_ALCH_HARMFUL = 0x0001EA70  # Skyrim.esm keyword vanilla weapon effects carry


def script_effect_subrecords(name, script_name, description='', kind='armor'):
    """Subrecord specs for an MGEF that runs `script_name` (an ActiveMagicEffect): VMAD, FULL, [KSIZ, KWDA], DATA, DNAM.

    Skyrim.esm must be master 0 (create_script_effect adds it first) for the raw keyword form ID."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    vmad = VmadData(version=5, obj_format=2, scripts=[VmadScript(name=script_name, flags=0, properties=[])])
    subs = [{'sig': 'VMAD', 'hex': vmad.to_bytes('MGEF').hex()}, {'sig': 'FULL', 'text': name}]
    if kind != 'armor':
        subs += [{'sig': 'KSIZ', 'hex': struct.pack('<I', 1).hex()},
                 {'sig': 'KWDA', 'hex': struct.pack('<I', MAGIC_ALCH_HARMFUL).hex()}]
    subs.append({'sig': 'DATA', 'hex': script_effect_data(kind).hex()})
    if description:
        subs.append({'sig': 'DNAM', 'text': description})
    return subs


def create_script_effect(builder, target_plugin, editor_id, name, script_name, source, description='',
                         kind='armor', enchantment_editor_id=None, cost=0, rebuild=None, extra_effects=None,
                         duration=None, base_effect=None, drop_conditions=None, delivery=None,
                         touch_range=TOUCH_RANGE):
    """Compile an ActiveMagicEffect script, add an MGEF that runs it and (unless enchantment_editor_id is
    ''), an ENCH wrapping it. The script is compiled first, so a compile error leaves the plugin untouched.
    extra_effects ([{effect, magnitude, area, duration}]) join the script effect in the ENCH; duration is
    the script effect's own (default 1 second for weapons and staves, 0 for worn gear).
    base_effect: a vanilla MGEF (e.g. EnchParalysisFFContact) whose data, keywords, sounds and conditions
    the new effect copies, so the game treats it like that effect; drop_conditions lists condition indexes
    of the copy to remove (e.g. vanilla paralysis has a 25% chance condition).
    delivery ('touch', 'aimed', 'ray' or 'self') makes a spell-style effect: it copies the matching vanilla
    effect (SPELL_EFFECT_BASES), sets the cast type for 'ray', uses weapon-style data and skips the ENCH
    unless enchantment_editor_id is given. 'touch' is a ray with a range check (touch_range units) added to
    the script's OnEffectStart, because script effects do not work with touch-delivery spells."""
    from tools.core.plugin_edit import resolve_target_path
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', script_name):
        raise ValueError("script_name may contain only letters, digits and underscores")
    if not re.search(rf'^\s*scriptname\s+{re.escape(script_name)}\s+extends\s+activemagiceffect\b',
                     source, re.I | re.M):
        raise ValueError(f"source must start with 'Scriptname {script_name} Extends ActiveMagicEffect'")
    if drop_conditions and not base_effect:
        raise ValueError('drop_conditions needs base_effect')
    ray = False
    if delivery:
        if delivery not in SPELL_EFFECT_BASES:
            raise ValueError(f"delivery must be one of {sorted(SPELL_EFFECT_BASES)}")
        if base_effect:
            raise ValueError('give delivery or base_effect, not both')
        base_effect, ray = SPELL_EFFECT_BASES[delivery]
        if delivery == 'touch':
            guard = (f"\n\tif akCaster && akTarget && akCaster.GetDistance(akTarget) > {touch_range}\n"
                     "\t\treturn\n\tendif")
            source, hits = re.subn(r'^([ \t]*Event[ \t]+OnEffectStart[ \t]*\([^)]*\)[^\r\n]*)', lambda m: m.group(1) + guard,
                                   source, count=1, flags=re.I | re.M)
            if not hits:
                raise ValueError("a touch effect needs an 'Event OnEffectStart(Actor akTarget, Actor akCaster)' "
                                 "handler to add the range check to")
        kind = 'weapon'
        if enchantment_editor_id is None:
            enchantment_editor_id = ''
    subs = script_effect_subrecords(name, script_name, description, kind)
    path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
    pex = compile_script(script_name, source, path.parent / 'Scripts')
    # The MGEF references nothing, so no master would be added; the item override added later by enchant_item
    # needs Skyrim.esm, and adding a master afterwards would renumber these schemaless records.
    from tools.core.plugin_edit import Target
    base = Target(builder.m, builder.names, path, create=True)
    base.ensure_master('Skyrim.esm')
    base.save()
    res = create_record(builder, target_plugin, 'MGEF', editor_id, subs)
    out = {'effect': res['record'], 'script': str(pex), 'masters': res['masters']}
    if base_effect:
        from tools.records import record_tools
        vanilla = Path(builder.data_dir) / 'Skyrim.esm'
        out['copied_from'] = record_tools.copy_subrecords(
            path, editor_id, vanilla, base_effect,
            keep=('EDID', 'VMAD', 'FULL') + (('DNAM',) if description else ()))['from']
        if ray:
            record_tools.set_fields(path, editor_id, {'cast_type': 'concentration'})
            out['cast_type'] = 'concentration'
        if drop_conditions:
            out['dropped_conditions'] = record_tools.drop_conditions(path, editor_id, drop_conditions)['removed']
    ench_id = editor_id + 'Ench' if enchantment_editor_id is None else enchantment_editor_id
    if ench_id:
        # the new MGEF is not in the builder's loaded model, so the ENCH needs a fresh one to resolve it
        if rebuild:
            builder = rebuild()
        if duration is None:
            duration = 0 if kind == 'armor' else 1
        effects = [{'effect': editor_id, 'magnitude': 0, 'duration': duration}] + list(extra_effects or [])
        out['enchantment'] = create_enchantment(builder, target_plugin, ench_id, name, effects, kind, cost)['record']
    return out


def enchant_item(editor, item, target_plugin, enchantment, charge=None, replace=False):
    """Attach an ENCH to a WEAP or ARMO (EITM, plus EAMT charge on weapons) in target_plugin."""
    from tools.core.plugin_edit import EditError
    try:
        info = editor.get_record(item, plugin=target_plugin)
    except (EditError, FileNotFoundError, ValueError):
        info = editor.get_record(item)
    sig = info['signature']
    if sig not in ('WEAP', 'ARMO'):
        raise ValueError(f"{item!r} is a {sig}; only WEAP and ARMO can be enchanted")
    if any(s['sig'] == 'EITM' for s in info['subrecords']) and not replace:
        raise ValueError(f"{item!r} is already enchanted; pass replace=true to swap the enchantment")
    editor.resolver.lookup(enchantment, ['ENCH'])
    edits = [{'sig': 'EITM', 'form': enchantment, 'form_type': 'ENCH'}]
    if sig == 'WEAP':
        edits.append({'sig': 'EAMT', 'uint16': 500 if charge is None else int(charge)})
    elif charge is not None:
        raise ValueError("charge only applies to weapons; worn enchantments are constant")
    return editor.edit_record(item, target_plugin, edits, signature=sig)
