"""Place a perk in a skill tree by overriding the skill's AVIF and appending a node."""
import struct

from tools.records.loot_records import _lookup, _open
from tools.records.world_records import skill_index
from tools.npcs.npc_builder import Plan

SKILL_EDIDS = {6: 'AVOneHanded', 7: 'AVTwoHanded', 8: 'AVMarksman', 9: 'AVBlock', 10: 'AVSmithing',
               11: 'AVHeavyArmor', 12: 'AVLightArmor', 13: 'AVPickpocket', 14: 'AVLockpicking', 15: 'AVSneak',
               16: 'AVAlchemy', 17: 'AVSpeechcraft', 18: 'AVAlteration', 19: 'AVConjuration',
               20: 'AVDestruction', 21: 'AVIllusion', 22: 'AVRestoration', 23: 'AVEnchanting'}
NODE_SIGS = ('PNAM', 'FNAM', 'XNAM', 'YNAM', 'HNAM', 'VNAM', 'SNAM', 'CNAM', 'INAM')


def _nodes(subs):
    """[(start, end, info)] spans in a [(sig, data)] list; a node runs from PNAM to its INAM."""
    out, start = [], None
    for i, (sig, data) in enumerate(subs):
        if sig == 'PNAM':
            start, info = i, {'perk': struct.unpack('<I', data)[0], 'links': []}
        elif start is not None:
            if sig == 'CNAM':
                info['links'].append(struct.unpack('<I', data)[0])
            elif sig == 'INAM':
                info['index'] = struct.unpack('<I', data)[0]
                out.append((start, i, info))
                start = None
            elif sig in ('XNAM', 'YNAM'):
                info[sig] = struct.unpack('<I', data)[0]
            elif sig in ('HNAM', 'VNAM'):
                info[sig] = struct.unpack('<f', data)[0]
    return out


def add_perk_to_skill_tree(b, target_plugin, skill, perk, parents=None, x=None, y=None, horizontal=None,
                           vertical=None):
    av = SKILL_EDIDS[skill_index(skill)]
    path, target = _open(b, target_plugin)
    p = Plan()
    avif_fid, src = b.resolver.lookup(av, ['AVIF'])
    perk_rec = target.find_own(perk, ['PERK'])
    if perk_rec is not None:
        perk_local = perk_rec.form_id.value
    else:
        perk_local = None
        perk_abs = _lookup(b, p, perk, ['PERK'])
    parent_abs = [_lookup(b, p, name, ['PERK']) for name in parents or []]
    b.add_masters(target.plugin, p)
    if perk_local is None:
        perk_local = b.local(perk_abs)
    parent_local = [b.local(f) for f in parent_abs]
    pl = target.plugin

    rec = target.find_matching(avif_fid)
    if rec is None:
        rec = target.override(src)
        # remap node form IDs from the source plugin ourselves so they are right whatever the schema covers
        for new, old in zip(rec.subrecords, src.subrecords):
            if old.signature in ('PNAM', 'SNAM') and struct.unpack('<I', bytes(old.data))[0]:
                new.data = struct.pack('<I', pl.remap_formid(struct.unpack('<I', bytes(old.data))[0], src.plugin))
    subs = [(s.signature, bytes(s.data)) for s in rec.subrecords]
    nodes = _nodes(subs)
    if any(info['perk'] == perk_local for _, _, info in nodes):
        raise ValueError(f"{perk} is already in the {skill} tree")
    by_perk = {info['perk']: (s, e, info) for s, e, info in nodes}
    missing = [n for n, f in zip(parents or [], parent_local) if f not in by_perk]
    if missing:
        raise ValueError(f"not in the {skill} tree: {', '.join(missing)}")
    index = max(info['index'] for _, _, info in nodes) + 1
    first = by_perk[parent_local[0]][2] if parent_local else None
    gx = x if x is not None else (first['XNAM'] if first else 0)
    gy = y if y is not None else ((first['YNAM'] + 1) if first else 0)
    h = horizontal if horizontal is not None else ((first['HNAM'] + 0.2) if first else 0.0)
    v = vertical if vertical is not None else ((first['VNAM'] + 0.2) if first else 0.0)
    skill_sub = next(d for sig, d in subs if sig == 'SNAM')
    node = [('PNAM', struct.pack('<I', perk_local)), ('FNAM', struct.pack('<I', 1)),
            ('XNAM', struct.pack('<I', gx)), ('YNAM', struct.pack('<I', gy)), ('HNAM', struct.pack('<f', h)),
            ('VNAM', struct.pack('<f', v)), ('SNAM', skill_sub), ('INAM', struct.pack('<I', index))]
    # a line goes from each parent's node to the new node's index, just before the parent's INAM
    for f in sorted({parent_local[i] for i in range(len(parent_local))}, key=lambda f: -by_perk[f][1]):
        end = by_perk[f][1]
        subs.insert(end, ('CNAM', struct.pack('<I', index)))
    subs.extend(node)
    rec.clear_subrecords()
    for sig, data in subs:
        rec.add_subrecord(sig, data)
    rec.modified = True
    pl.modified = True
    target.save()
    return {'path': str(path), 'skill': av, 'node': index, 'parents': parents or [], 'tree_size': len(nodes) + 1}
