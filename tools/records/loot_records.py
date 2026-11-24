"""Containers, leveled lists, outfits and crafting/tempering recipes."""
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, resolve_target_path
from tools.npcs.npc_builder import ITEM_SIGS, Plan, fid_bytes, z
from tools.records.ai_records import _check_new, _next_id, package_conditions, resolve_late

CHEST_MODEL = 'Clutter\\Containers\\Chest01\\Chest01.nif'
CHEST_OBND = struct.pack('<6h', -48, -24, 0, 48, 24, 48)
LIST_KINDS = {'item': 'LVLI', 'npc': 'LVLN', 'spell': 'LVSP'}
LIST_TARGET_SIGS = {'LVLI': ITEM_SIGS, 'LVLN': ['NPC_', 'LVLN'], 'LVSP': ['SPEL', 'LVSP']}
STATIONS = {
    'forge': 'CraftingSmithingForge', 'skyforge': 'CraftingSmithingSkyforge',
    'sharpening': 'CraftingSmithingSharpeningWheel', 'armor_table': 'CraftingSmithingArmorTable',
    'cooking': 'CraftingCookpot', 'smelter': 'CraftingSmelter', 'tanning': 'CraftingTanningRack',
}
ARMOR_SIGS = ['ARMO']


def _open(b, target_plugin, editor_id=None):
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=True)
    if editor_id is not None:
        _check_new(b, target.plugin, editor_id)
    b.target, b.plugin = target, target.plugin
    return path, target


def _lookup(b, plan, ident, sigs):
    fid = b.resolver.lookup(ident, sigs)[0]
    plan.abs_ids.add(fid)
    return fid


def _finish(b, path, target, rec, oid):
    target.plugin.add_record(rec)
    target.plugin.header.next_object_id = oid + 1
    target.save()
    return {'path': str(path), 'record': b.describe(rec)}


def _count(entry, key='count'):
    n = int(entry.get(key, 1))
    if not 1 <= n <= 65535:
        raise ValueError(f"{key} must be between 1 and 65535")
    return n


def create_container(b, target_plugin, editor_id, name, items=None, model=CHEST_MODEL, respawns=False,
                     weight=0.0):
    """CONT holding items ([{"item", "count"}]); items may be leveled lists. model is a nif path under Meshes."""
    if not name:
        raise ValueError("name is required")
    path, target = _open(b, target_plugin, editor_id)
    p = Plan()
    entries = [(_lookup(b, p, e['item'], ITEM_SIGS), _count(e)) for e in items or []]
    b.add_masters(target.plugin, p)
    oid = _next_id(b, target.plugin)
    rec = Record('CONT', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('OBND', CHEST_OBND)
    rec.add_subrecord('FULL', z(name))
    rec.add_subrecord('MODL', z(model))
    if entries:
        rec.add_subrecord('COCT', struct.pack('<I', len(entries)))
        for fid, n in entries:
            rec.add_subrecord('CNTO', fid_bytes(b.local(fid)) + struct.pack('<I', n))
    rec.add_subrecord('DATA', struct.pack('<Bf', 1 | (2 if respawns else 0), float(weight)))
    res = _finish(b, path, target, rec, oid)
    res['items'] = len(entries)
    return res


def _lvlo(level, fid, count):
    return struct.pack('<HHIHH', level, 0, fid, count, 0)


def _list_flags(calc_all_levels, calc_each, use_all):
    return (1 if calc_all_levels else 0) | (2 if calc_each else 0) | (4 if use_all else 0)


def create_leveled_list(b, target_plugin, editor_id, entries, kind='item', chance_none=0, calc_all_levels=False,
                        calc_each=True, use_all=False):
    """Leveled list of items (LVLI), actors (LVLN) or spells (LVSP). entries: [{"form", "level", "count"}]."""
    sig = LIST_KINDS.get(kind)
    if sig is None:
        raise ValueError(f"kind must be one of {list(LIST_KINDS)}")
    if not entries:
        raise ValueError("give at least one entry")
    if not 0 <= chance_none <= 100:
        raise ValueError("chance_none must be 0-100")
    path, target = _open(b, target_plugin, editor_id)
    p = Plan()
    rows = [(max(1, int(e.get('level', 1))), _lookup(b, p, e['form'], LIST_TARGET_SIGS[sig]), _count(e))
            for e in entries]
    b.add_masters(target.plugin, p)
    oid = _next_id(b, target.plugin)
    rec = Record(sig, FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('OBND', bytes(12))
    rec.add_subrecord('LVLD', bytes([chance_none]))
    rec.add_subrecord('LVLF', bytes([_list_flags(calc_all_levels, calc_each, use_all)]))
    rec.add_subrecord('LLCT', bytes([len(rows)]))
    for level, fid, n in rows:
        rec.add_subrecord('LVLO', _lvlo(level, b.local(fid), n))
    res = _finish(b, path, target, rec, oid)
    res['entries'] = len(rows)
    return res


def add_to_leveled_list(b, target_plugin, leveled_list, entries):
    """Append entries to a leveled list (own or from a master, which is overridden in target_plugin)."""
    sigs = list(LIST_TARGET_SIGS)
    path, target = _open(b, target_plugin)
    plugin = target.plugin
    own = target.find_own(leveled_list, sigs)
    p = Plan()
    if own is not None:
        rec, src_sig = own, own.signature
    else:
        abs_id, src = b.resolver.lookup(leveled_list, sigs)
        src_sig = src.signature
        rec = target.find_matching(abs_id)
    rows = [(max(1, int(e.get('level', 1))), _lookup(b, p, e['form'], LIST_TARGET_SIGS[src_sig]), _count(e))
            for e in entries or []]
    if not rows:
        raise ValueError("give at least one entry")
    b.add_masters(plugin, p)
    if rec is None:
        rec = target.override(src)
    subs = list(rec.subrecords)
    last = max(i for i, s in enumerate(subs) if s.signature in ('LLCT', 'LVLO'))
    count = sum(1 for s in subs if s.signature == 'LVLO') + len(rows)
    if count > 255:
        raise ValueError("a leveled list holds at most 255 entries")
    new = [type(subs[0])('LVLO', _lvlo(level, b.local(fid), n)) for level, fid, n in rows]
    subs[last + 1:last + 1] = new
    for i, s in enumerate(subs):
        if s.signature == 'LLCT':
            subs[i] = type(subs[0])('LLCT', bytes([count]))
    if not any(s.signature == 'LLCT' for s in subs):
        subs.insert(last, type(subs[0])('LLCT', bytes([count])))
    rec.subrecords[:] = subs
    plugin.modified = True
    target.save()
    return {'path': str(path), 'list': f"{rec.form_id.value:08X}", 'entries_total': count, 'added': len(rows)}


def create_outfit(b, target_plugin, editor_id, items):
    """OTFT from armor and leveled-item identifiers. Give it to an NPC with edit_npc or edit_record (DOFT)."""
    if not items:
        raise ValueError("give at least one armor or leveled list")
    path, target = _open(b, target_plugin, editor_id)
    p = Plan()
    fids = [_lookup(b, p, i, ['ARMO', 'LVLI']) for i in items]
    b.add_masters(target.plugin, p)
    oid = _next_id(b, target.plugin)
    rec = Record('OTFT', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('INAM', b''.join(fid_bytes(b.local(f)) for f in fids))
    return _finish(b, path, target, rec, oid)


def create_recipe(b, target_plugin, editor_id, result, ingredients, station=None, count=1, temper=False,
                  conditions=None):
    """COBJ crafting recipe, or with temper=True a tempering recipe that improves `result` (a weapon or armor).

    station: forge, skyforge, sharpening, armor_table, cooking, smelter or tanning (default: forge; tempering
    picks sharpening for weapons and armor_table for armor). conditions as for create_package.
    """
    if not ingredients:
        raise ValueError("give at least one ingredient")
    if station is not None and station not in STATIONS:
        raise ValueError(f"station must be one of {list(STATIONS)}")
    path, target = _open(b, target_plugin, editor_id)
    p = Plan()
    res_fid, res_rec = b.resolver.lookup(result, ITEM_SIGS)
    p.abs_ids.add(res_fid)
    if temper:
        if res_rec.signature not in ('WEAP', 'ARMO'):
            raise ValueError("only a weapon or armor can be tempered")
        station = station or ('sharpening' if res_rec.signature == 'WEAP' else 'armor_table')
    bench = _lookup(b, p, STATIONS[station or 'forge'], ['KYWD'])
    mats = [(_lookup(b, p, i['item'], ITEM_SIGS), _count(i)) for i in ingredients]
    conds = package_conditions(b, target, p, conditions)
    b.add_masters(target.plugin, p)
    conds = resolve_late(b, conds)
    if temper:
        from tools.records.ai_records import ctda
        conds.insert(0, ctda(47, b.local(res_fid), 3, 1.0, 2))  # player has the item
    oid = _next_id(b, target.plugin)
    rec = Record('COBJ', FormID(b.own(oid)), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('COCT', struct.pack('<I', len(mats)))
    for fid, n in mats:
        rec.add_subrecord('CNTO', fid_bytes(b.local(fid)) + struct.pack('<I', n))
    for c in conds:
        rec.add_subrecord('CTDA', c)
    rec.add_subrecord('CNAM', fid_bytes(b.local(res_fid)))
    rec.add_subrecord('BNAM', fid_bytes(b.local(bench)))
    rec.add_subrecord('NAM1', struct.pack('<H', _count({'count': count})))
    out = _finish(b, path, target, rec, oid)
    out.update(station=station or 'forge', temper=temper)
    return out
