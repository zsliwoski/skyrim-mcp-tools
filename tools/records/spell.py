"""Create SPEL records (spells, powers, abilities) and compile standalone Papyrus scripts."""
import re
import struct

from tools.papyrus.papyrus_build import compile_script
from tools.records.record_create import create_record

# (SPIT spell type, cast type) per kind: abilities are permanent self effects, spells/powers fire once.
SPELL_KINDS = {
    'ability': (4, 0),
    'spell': (0, 1),
    'power': (2, 1),
    'lesser_power': (3, 1),
}
DELIVERY = {'self': 0, 'touch': 1, 'aimed': 2, 'target_actor': 3, 'target_location': 4}
CAST_TYPES = {'constant': 0, 'fire_and_forget': 1, 'concentration': 2, 'scroll': 3}
EITHER_HAND = 0x00013F44   # EQUP vanilla spells use so they can go in either hand
SPELL_DISPLAY_OBJECT = 0x0010A046  # MDOB vanilla hand-cast spells carry


def spell_subrecords(name, effects, kind='ability', delivery=None, cost=0, cast_duration=0.0, range_=0.0,
                     description='', cast=None):
    """Subrecord specs for a SPEL: OBND, FULL, (MDOB, ETYP for hand spells), optional DESC, SPIT, then one
    EFID/EFIT pair per effect. cast overrides the cast type (fire_and_forget, concentration, constant)."""
    if kind not in SPELL_KINDS:
        raise ValueError(f"kind must be one of {sorted(SPELL_KINDS)}")
    if not effects:
        raise ValueError("give at least one effect")
    if delivery is None:
        delivery = 'self'
    if delivery not in DELIVERY:
        raise ValueError(f"delivery must be one of {sorted(DELIVERY)}")
    spell_type, cast_type = SPELL_KINDS[kind]
    if cast is not None:
        if cast not in CAST_TYPES or cast == 'scroll':
            raise ValueError("cast must be fire_and_forget, concentration or constant")
        cast_type = CAST_TYPES[cast]
    if kind == 'ability' and delivery != 'self':
        raise ValueError("abilities are constant self effects; use kind='spell' for other deliveries")
    # flags 0 = auto-calculate cost; a non-zero cost without auto-calc is set via the cost-override flag
    flags = 0 if not cost else 1
    spit = struct.pack('<IIIfIIffI', int(cost), flags, spell_type, 0.0, cast_type, DELIVERY[delivery],
                       float(cast_duration), float(range_), 0)
    subs = [{'sig': 'OBND', 'hex': '00' * 12}, {'sig': 'FULL', 'text': name}]
    if kind == 'spell':
        subs += [{'sig': 'MDOB', 'hex': struct.pack('<I', SPELL_DISPLAY_OBJECT).hex()},
                 {'sig': 'ETYP', 'hex': struct.pack('<I', EITHER_HAND).hex()}]
    if description:
        subs.append({'sig': 'DESC', 'text': description})
    subs.append({'sig': 'SPIT', 'hex': spit.hex()})
    subs += effect_subrecords(effects)
    return subs


def effect_subrecords(effects):
    """EFID/EFIT subrecord specs for [{effect, magnitude, area, duration}]."""
    subs = []
    for e in effects:
        if 'effect' not in e:
            raise ValueError("each effect needs an 'effect' (MGEF editor ID, name or form ID)")
        efit = struct.pack('<fII', float(e.get('magnitude', 0)), int(e.get('area', 0)), int(e.get('duration', 0)))
        subs += [{'sig': 'EFID', 'form': e['effect'], 'sigs': ['MGEF']}, {'sig': 'EFIT', 'hex': efit.hex()}]
    return subs


def create_spell(builder, target_plugin, editor_id, name, effects, kind='ability', delivery=None, cost=0,
                 cast_duration=0.0, range_=0.0, description='', cast=None):
    """Add a SPEL to target_plugin built from existing magic effects."""
    subs = spell_subrecords(name, effects, kind, delivery, cost, cast_duration, range_, description, cast)
    return create_record(builder, target_plugin, 'SPEL', editor_id, subs)


def create_script(builder, target_plugin, script_name, source):
    """Compile a Papyrus script into the Scripts folder beside target_plugin (source kept in Scripts/Source).

    The script is not attached to anything; attach it through a VMAD (e.g. create_script_effect, quest tools).
    """
    from tools.core.plugin_edit import resolve_target_path
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', script_name):
        raise ValueError("script_name may contain only letters, digits and underscores")
    m = re.search(r'^\s*scriptname\s+(\w+)(?:\s+extends\s+(\w+))?', source, re.I | re.M)
    if not m or m.group(1).lower() != script_name.lower():
        raise ValueError(f"source must start with 'Scriptname {script_name}'")
    path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
    pex = compile_script(script_name, source, path.parent / 'Scripts')
    return {'script': str(pex), 'source': str(path.parent / 'Scripts' / 'Source' / f'{script_name}.psc'),
            'extends': m.group(2) or ''}
