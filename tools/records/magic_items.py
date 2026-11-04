"""Spell tomes and scrolls built from vanilla templates: a tome that teaches a spell, a scroll that casts effects."""
import struct

from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.records.record_create import create_record
from tools.records.spell import CAST_TYPES, DELIVERY, effect_subrecords

TOME_TEMPLATE = 'SpellTomeParalyze'   # Alteration low-poly tome
SCROLL_TEMPLATE = 'ParalyzeScroll'
TEACHES_SPELL = 0x04                  # BOOK DATA flag: reading teaches the spell


def _template_specs(builder, template, signature, replace):
    """The vanilla record's subrecords as raw create_record specs, with FULL/DESC and any `replace` sigs swapped.

    replace maps a signature to a spec (or list of specs); other sigs keep their vanilla bytes. EDID is dropped.
    Raw bytes hold Skyrim.esm form IDs, so Skyrim.esm must be the target's first master."""
    _, rec = builder.resolver.lookup(template, [signature])
    specs = []
    for s in rec.subrecords:
        if s.signature == 'EDID':
            continue
        if s.signature in replace:
            new = replace[s.signature]
            specs += new if isinstance(new, list) else [new]
        else:
            specs.append({'sig': s.signature, 'hex': bytes(s.data).hex()})
    return specs


def _ensure_skyrim_master(builder, target_plugin):
    path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
    target = Target(builder.m, builder.names, path, create=True)
    target.ensure_master('Skyrim.esm')
    target.save()
    return path


def create_scroll(builder, target_plugin, editor_id, name, effects, delivery='aimed', description='', cost=0,
                  template=SCROLL_TEMPLATE):
    """Add a scroll (SCRL) that casts `effects` ([{effect, magnitude, area, duration}]) once when read.

    The model, keywords, value and weight come from a vanilla scroll. The effects' own MGEF delivery must
    match `delivery` (self, touch, aimed...)."""
    if delivery not in DELIVERY:
        raise ValueError(f"delivery must be one of {sorted(DELIVERY)}")
    if not effects:
        raise ValueError("give at least one effect")
    _ensure_skyrim_master(builder, target_plugin)
    spit = struct.pack('<IIIfIIffI', int(cost), 1 if cost else 0, 0, 0.0, CAST_TYPES['scroll'],
                       DELIVERY[delivery], 0.0, 0.0, 0)
    replace = {'FULL': {'sig': 'FULL', 'text': name}, 'DESC': {'sig': 'DESC', 'text': description},
               'SPIT': {'sig': 'SPIT', 'hex': spit.hex()}, 'EFID': [], 'EFIT': [], 'CTDA': []}
    subs = _template_specs(builder, template, 'SCRL', replace) + effect_subrecords(effects)
    return create_record(builder, target_plugin, 'SCRL', editor_id, subs)


def spell_form_id(plugin, spell):
    """Form ID (with this plugin's own master index) of a SPEL in the plugin, by editor ID."""
    own = len(plugin.header.masters)
    for r in plugin.records:
        if r.signature == 'SPEL' and (r.editor_id or '').lower() == spell.lower() and hi(r.form_id.value) == own:
            return r.form_id.value
    raise ValueError(f"spell {spell!r} not found in {plugin.file_path.name}; create it first")


def create_spell_tome(builder, target_plugin, editor_id, name, spell, text='', value=None, template=TOME_TEMPLATE):
    """Add a spell tome (BOOK) that teaches `spell` (a SPEL in target_plugin) when read; text is the book body."""
    path = _ensure_skyrim_master(builder, target_plugin)
    from esplib import Plugin
    plugin = Plugin.load(str(path))
    _, rec = builder.resolver.lookup(template, ['BOOK'])
    old = bytes(rec.get_subrecord('DATA').data)
    price = struct.unpack_from('<I', old, 8)[0] if value is None else int(value)
    data = struct.pack('<BBxxIIf', TEACHES_SPELL, 0, spell_form_id(plugin, spell), price,
                       struct.unpack_from('<f', old, 12)[0])
    replace = {'FULL': {'sig': 'FULL', 'text': name}, 'DESC': {'sig': 'DESC', 'text': text},
               'DATA': {'sig': 'DATA', 'hex': data.hex()}}
    subs = [s for s in _template_specs(builder, template, 'BOOK', replace)
            if not (s['sig'] == 'CNAM' and len(bytes.fromhex(s['hex'])) == 4)]
    return create_record(builder, target_plugin, 'BOOK', editor_id, subs)
