"""Build a whole family of spell items from one Papyrus effect script: effects, spells, tomes, a scroll, a staff."""
from tools.cells.place_items import place_items
from tools.records.enchant import create_enchantment, create_script_effect
from tools.records.items import create_unique_item
from tools.records.magic_items import create_scroll, create_spell_tome
from tools.records.spell import create_spell

# variant -> (label, effect delivery key, spell delivery, cast type, area, default mana cost). Bolt and burst
# share the aimed effect: a burst is the same effect with an area. A nova is a self-delivered effect with an area.
VARIANTS = {
    'touch': ('Touch', 'touch', 'aimed', 'concentration', 0, 40),
    'bolt': ('Bolt', 'aimed', 'aimed', 'fire_and_forget', 0, 250),
    'ray': ('Ray', 'ray', 'aimed', 'concentration', 0, 40),
    'burst': ('Burst', 'aimed', 'aimed', 'fire_and_forget', 15, 400),
    'nova': ('Nova', 'self', 'self', 'fire_and_forget', 40, 500),
}


def create_spell_set(refresh, target_plugin, prefix, name, script_name, source, description='', variants=None,
                     duration=10, magnitude=0, costs=None, tome_texts=None, tomes=True, scroll=True, staff=True,
                     staff_source='StaffParalyze', staff_charge=3000, cell=None, x=0.0, y=0.0, z=0.0,
                     spacing=30.0, clearance=25.0, radius=300.0):
    """Create one effect per delivery, a spell per variant, a teach-spell tome per spell, a scroll and a staff.

    All effects run `script_name` (source is compiled once per effect), so one script can back the whole family.
    variants: any of VARIANTS (default all). costs: {variant: mana}. tome_texts: {variant: book text}.
    If cell is given, tomes, scroll and staff are placed on free floor spots nearest (x, y, z).
    Editor IDs: <prefix><Delivery>Effect, <prefix><Label>Spell/Tome, <prefix>Scroll, <prefix>StaffEnch, <prefix>Staff."""
    variants = list(variants or VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant(s) {unknown}; choose from {sorted(VARIANTS)}")
    if not prefix or not name:
        raise ValueError("prefix and name are required")
    costs, tome_texts = costs or {}, tome_texts or {}
    effect_keys = list(dict.fromkeys(VARIANTS[v][1] for v in variants))
    if (scroll or staff) and 'aimed' not in effect_keys:
        effect_keys.append('aimed')

    out = {'effects': [], 'spells': [], 'tomes': [], 'items': []}
    effect_ids = {}
    for key in effect_keys:
        eid = f"{prefix}{key.title()}Effect"
        builder, _ = refresh()
        label = next(VARIANTS[v][0] for v in VARIANTS if VARIANTS[v][1] == key)
        create_script_effect(builder, target_plugin, eid, f"{name} {label}", script_name, source, description,
                             rebuild=lambda: refresh()[0], delivery=key)
        effect_ids[key] = eid
        out['effects'].append(eid)

    spell_ids = {}
    for v in variants:
        label, key, delivery, cast, area, cost = VARIANTS[v]
        sid = f"{prefix}{label}Spell"
        builder, _ = refresh()
        create_spell(builder, target_plugin, sid, f"{name} {label}",
                     [{'effect': effect_ids[key], 'magnitude': magnitude, 'area': area, 'duration': duration}],
                     'spell', delivery, costs.get(v, cost), 0.0, 0.0, description, cast)
        spell_ids[v] = sid
        out['spells'].append(sid)

    to_place = []
    if tomes:
        for v in variants:
            label = VARIANTS[v][0]
            tid = f"{prefix}{label}Tome"
            text = tome_texts.get(v) or f"{name} {label}\n\n{description}"
            builder, _ = refresh()
            create_spell_tome(builder, target_plugin, tid, f"Spell Tome: {name} {label}", spell_ids[v], text)
            out['tomes'].append(tid)
            to_place.append(tid)

    aimed = [{'effect': effect_ids['aimed'], 'magnitude': magnitude, 'duration': duration}]
    if scroll:
        builder, _ = refresh()
        create_scroll(builder, target_plugin, f"{prefix}Scroll", f"Scroll of {name}", aimed, 'aimed', description)
        out['items'].append(f"{prefix}Scroll")
        to_place.append(f"{prefix}Scroll")
    if staff:
        builder, _ = refresh()
        create_enchantment(builder, target_plugin, f"{prefix}StaffEnch", name, aimed, 'staff',
                           costs.get('bolt', VARIANTS['bolt'][5]))
        create_unique_item(refresh, staff_source, target_plugin, f"{prefix}Staff", f"Staff of {name}", description,
                           f"{prefix}StaffEnch", staff_charge, True)
        out['items'] += [f"{prefix}StaffEnch", f"{prefix}Staff"]
        to_place.append(f"{prefix}Staff")

    if cell and to_place:
        out['placed'] = place_items(refresh, to_place, target_plugin, cell, x, y, z, spacing, clearance,
                                    radius)['placed']
    return out
