"""Author a repeatable ("radiant") collection quest for an NPC: each offer picks a random item and amount."""
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID

from tools.npcs.npc_builder import Plan, z
from tools.core.plugin_edit import Target, hi, resolve_target_path

GET_STAGE = 58
EQ, NE = 0, 1
SUBJECT = 0
QUEST_TYPE_MISC = 6
ACTIVE, DONE = 10, 20
REQUIRED = ('quest_editor_id', 'quest_name', 'items', 'offer', 'reminder', 'turn_in')
ITEM_KEYS = ('item', 'name', 'min', 'max', 'unit_reward')


def check_spec(spec):
    """Return a list of problems with the spec (empty when it is valid)."""
    errors = [f"missing field {k!r}" for k in REQUIRED if k not in spec]
    if 'quest_editor_id' in spec and not re.fullmatch(r'[A-Za-z0-9_]+', str(spec['quest_editor_id'])):
        errors.append("quest_editor_id may contain only letters, digits and underscores")
    for k in ('offer', 'reminder', 'turn_in'):
        d = spec.get(k)
        if k in spec and not (isinstance(d, dict) and d.get('prompt') and d.get('lines')):
            errors.append(f"{k} needs a prompt and lines")
    items = spec.get('items')
    if 'items' in spec and not (isinstance(items, list) and items):
        errors.append("items must be a non-empty list")
    for n, it in enumerate(items if isinstance(items, list) else []):
        miss = [k for k in ITEM_KEYS if k not in it]
        if miss:
            errors.append(f"items[{n}] is missing {miss}")
        elif not (1 <= int(it['min']) <= int(it['max'])):
            errors.append(f"items[{n}]: need 1 <= min <= max")
    return errors


def offer_code(spec, n):
    """Papyrus fragment for the offer topic: rolls one of n items and an amount in its range, stores both in the globals, starts the quest."""
    lines = ["Quest q = RQuest as Quest", "q.Reset()", f"int pick = Utility.RandomInt(0, {n - 1})", "int need = 1"]
    for i, it in enumerate(spec['items']):
        lines += [f"{'If' if i == 0 else 'ElseIf'} pick == {i}",
                  f"  need = Utility.RandomInt({int(it['min'])}, {int(it['max'])})"]
    lines += ["EndIf", "(CountG as GlobalVariable).SetValue(need as float)",
              "(PickG as GlobalVariable).SetValue(pick as float)"]
    if spec.get('on_accept'):
        lines.append(spec['on_accept'])
    lines += [f"q.SetStage({ACTIVE})", f"q.SetObjectiveDisplayed({ACTIVE} + pick)"]
    return "\n".join(lines)


def turn_in_code(spec, n):
    """Papyrus fragment for the turn-in topic: takes the rolled item and amount, pays gold per unit and finishes the quest, or tells the player they are short."""
    lines = ["Actor pl = Game.GetPlayer()", "Quest q = RQuest as Quest",
             "int pick = (PickG as GlobalVariable).GetValueInt()",
             "int need = (CountG as GlobalVariable).GetValueInt()", "Form want = None", "int pay = 0"]
    for i, it in enumerate(spec['items']):
        lines += [f"{'If' if i == 0 else 'ElseIf'} pick == {i}", f"  want = Item{i}",
                  f"  pay = need * {int(it['unit_reward'])}"]
    lines += ["EndIf", "If want != None && pl.GetItemCount(want) >= need", "  pl.RemoveItem(want, need)",
              "  pl.AddItem(Gold, pay)", "  Debug.Notification(\"Contract complete: \" + pay + \" gold\")",
              f"  q.SetObjectiveCompleted({ACTIVE} + pick)"]
    if spec.get('on_complete'):
        lines += ["  " + ln for ln in spec['on_complete'].splitlines()]
    lines += [f"  q.SetStage({DONE})", "Else", "  Debug.Notification(\"You are still short of what was asked.\")",
              "EndIf"]
    return "\n".join(lines)


def add_radiant_quest(b, identifier, spec, target_plugin):
    """Write the quest, its two globals and its dialogue into target_plugin; b is an NpcBuilder.

    spec["mode"] = "story_manager" builds Story Manager quests instead (tools/quests/radiant_story.py).
    """
    if spec.get('mode') == 'story_manager':
        from tools.quests.radiant_story import add_story_radiant_quest
        return add_story_radiant_quest(b, identifier, spec, target_plugin)
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be added inline")
    qid = spec['quest_editor_id']
    for e in (qid, f"{qid}_Count", f"{qid}_Pick"):
        if any((r.editor_id or '').lower() == e.lower() for r in b.m.records.values()) or \
                any((r.editor_id or '').lower() == e.lower() for r in plugin.records):
            raise ValueError(f"editor_id {e!r} already exists")
    npc = target.find_own(identifier, ['NPC_'])
    if npc is None:
        raise ValueError(f"NPC {identifier!r} not found in {path.name}")

    p = Plan()
    items = spec['items']
    item_ids = []
    for it in items:
        fid, _ = b.resolver.lookup(it['item'], ['MISC', 'ALCH', 'INGR', 'ARMO', 'WEAP', 'BOOK', 'AMMO', 'SLGM'])
        p.abs_ids.add(fid)
        item_ids.append(fid)
    gold, _ = b.resolver.lookup('Gold001', ['MISC'])
    p.abs_ids.add(gold)
    extra_props = []
    for name, ident in (spec.get('properties') or {}).items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(name)):
            raise ValueError(f"property name {name!r} is not a valid Papyrus identifier")
        fid, _ = b.resolver.lookup(ident, list(b.m.by_sig))
        p.abs_ids.add(fid)
        extra_props.append((name, fid))
    offer_conditions = b.plan_conditions(p, {'conditions': spec.get('conditions')}, 0)
    if p.errors:
        raise ValueError("; ".join(p.errors))

    b.target = target
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    b.next_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    b.plugin = plugin
    made = []

    def new_id():
        """Hand out the next free form id from the builder."""
        v = b.next_id
        b.next_id += 1
        return v

    quest_fid, count_fid, pick_fid = (b.own(new_id()) for _ in range(3))
    for fid, suffix in ((count_fid, 'Count'), (pick_fid, 'Pick')):
        g = Record('GLOB', FormID(fid), 0)
        g.add_subrecord('EDID', z(f"{qid}_{suffix}"))
        g.add_subrecord('FNAM', b'f')
        g.add_subrecord('FLTV', struct.pack('<f', 0.0))
        plugin.add_record(g)
        made.append(b.describe(g))

    q = Record('QUST', FormID(quest_fid), 0)
    q.add_subrecord('EDID', z(qid))
    q.add_subrecord('FULL', z(spec['quest_name']))
    q.add_subrecord('DNAM', struct.pack('<HBBII', 0x1, 50, 0, 0, QUEST_TYPE_MISC))
    q.add_subrecord('NEXT', b'')
    log = spec.get('log') or {}
    for idx, text in ((ACTIVE, log.get('10', "I have taken a contract. I should collect what was asked.")),
                      (DONE, log.get('20', "I delivered what was asked and was paid."))):
        q.add_subrecord('INDX', struct.pack('<HBB', idx, 0, 0))
        q.add_subrecord('QSDT', struct.pack('<B', 0))
        q.add_subrecord('CNAM', z(text))
    for i, it in enumerate(items):
        q.add_subrecord('QOBJ', struct.pack('<H', ACTIVE + i))
        q.add_subrecord('FNAM', struct.pack('<I', 0))
        q.add_subrecord('NNAM', z(f"Collect <Global={qid}_Count> {it['name']}"))
    q.add_subrecord('ANAM', struct.pack('<I', 0))
    plugin.add_record(q)
    made.append(b.describe(q))

    props = [('RQuest', -quest_fid), ('CountG', -count_fid), ('PickG', -pick_fid), ('Gold', gold)]
    props += [(f"Item{i}", fid) for i, fid in enumerate(item_ids)] + extra_props
    stage = lambda op, v: (GET_STAGE, -quest_fid, op, float(v), SUBJECT)
    o, r, t = spec['offer'], spec['reminder'], spec['turn_in']
    p.topics = [
        ('topic', o['prompt'], list(o['lines']), (offer_code(spec, len(items)), props),
         [stage(NE, ACTIVE)] + offer_conditions),
        ('topic', r['prompt'], list(r['lines']), None, [stage(EQ, ACTIVE)]),
        ('topic', t['prompt'], list(t['lines']), (turn_in_code(spec, len(items)), props), [stage(EQ, ACTIVE)]),
    ]
    dq = spec.get('dialogue_quest_editor_id') or f"{npc.editor_id}DialogueQuest"
    if target.find_own(dq, ['QUST']) is None:
        dq = qid
    b.make_dialogue(plugin, {'editor_id': npc.editor_id, 'quest_editor_id': dq}, p, new_id, made, npc.form_id.value)

    plugin.header.next_object_id = b.next_id
    target.save()
    check = Plugin.load(str(path))
    return {'path': str(path), 'masters': list(plugin.header.masters), 'records': made,
            'reloaded_records': len(check.records)}
