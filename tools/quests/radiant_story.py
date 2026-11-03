"""Radiant quests driven by the Story Manager: the game picks a quest and fills its location and container aliases.

Layout copied from vanilla MGR20 ("Fetch me that Book!") and the Mages Guild story nodes:
a script story event (SendStoryEvent on a keyword) -> branch node -> quest node (keyword condition) -> quests.
Each quest finds a dungeon (location alias), a boss chest in it (reference alias) and puts the gems there.
"""
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID

from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.quests.quest_edit import add_quest_stage_script

GET_STAGE = 58
EQ, NE = 0, 1
SUBJECT = 0
ACTIVE, DONE = 10, 20
SCRIPT_EVENT_NODE = 0x1379A  # vanilla SMEN for "started by a script"; the Mages Guild radiant branch hangs here
LOC_TYPE_DUNGEON = 0x130DB
BOSS_CONTAINER = 0x130F8
GET_EVENT_DATA = 0x240
REQUIRED = ('quest_editor_id', 'quest_name', 'items', 'offer', 'reminder', 'turn_in')
ITEM_KEYS = ('item', 'name', 'count', 'reward')
GIVER, LOCATION, CHEST, LOOT = 0, 1, 2, 3


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
        elif int(it['count']) < 1:
            errors.append(f"items[{n}]: count must be at least 1")
    return errors


def offer_code(n):
    """Papyrus fragment for the offer topic: resets all n quests, then sends the story event so the Story Manager picks one."""
    lines = [f"(Q{i} as Quest).Reset()" for i in range(n)]
    lines.append("(EventKw as Keyword).SendStoryEvent(None, None, None, 0, 0)")
    return "\n".join(lines)


def turn_in_code(spec, it):
    """Papyrus fragment for the turn-in topic: takes the item, pays the reward, completes and stops the contract, or tells the player they are short."""
    lines = ["Actor pl = Game.GetPlayer()", "Quest q = Contract as Quest", "Form want = Item as Form",
             f"If pl.GetItemCount(want) >= {int(it['count'])}",
             f"  pl.RemoveItem(want, {int(it['count'])})", f"  pl.AddItem(Gold, {int(it['reward'])})",
             f"  Debug.Notification(\"Contract complete: {int(it['reward'])} gold\")"]
    if spec.get('on_complete'):
        lines += ["  " + ln for ln in spec['on_complete'].splitlines()]
    lines += ["  q.CompleteAllObjectives()", f"  q.SetStage({DONE})", "  q.Stop()", "Else",
              "  Debug.Notification(\"You are still short of what was asked.\")", "EndIf"]
    return "\n".join(lines)


def event_condition(keyword_fid):
    """CTDA: story event data keyword 1 == the event keyword (as vanilla MGRUragNode)."""
    return struct.pack('<B3xfHH4sIIIi', 0, 1.0, GET_EVENT_DATA, 0, b'\0\0K1', keyword_fid, 0, 0, -1)


def location_conditions(keyword_fid, reftype_fid):
    """CTDAs for the location alias: location has keyword, and has a boss-container reference type."""
    return [struct.pack('<B3xfHHIIIII', 0, 1.0, func, 0, fid, 0, 0, 0, 0xFFFFFFFF)
            for func, fid in ((0x232, keyword_fid), (0x233, reftype_fid))]


def add_story_radiant_quest(b, identifier, spec, target_plugin):
    """Write one quest per item, the story event keyword and nodes, loot lists and the NPC's dialogue."""
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be added inline")
    prefix = spec['quest_editor_id']
    items = spec['items']
    editor_ids = [f"{prefix}_Event", f"{prefix}_Branch", f"{prefix}_Node"]
    editor_ids += [f"{prefix}_{i}" for i in range(len(items))] + [f"{prefix}_{i}_Loot" for i in range(len(items))]
    existing = {(r.editor_id or '').lower() for r in b.m.records.values()} | \
               {(r.editor_id or '').lower() for r in plugin.records}
    clash = [e for e in editor_ids if e.lower() in existing]
    if clash:
        raise ValueError(f"editor_id(s) already exist: {clash}")
    npc = target.find_own(identifier, ['NPC_'])
    if npc is None:
        raise ValueError(f"NPC {identifier!r} not found in {path.name}")

    p = Plan()
    item_ids = []
    for it in items:
        fid, _ = b.resolver.lookup(it['item'], ['MISC', 'ALCH', 'INGR', 'ARMO', 'WEAP', 'BOOK', 'AMMO', 'SLGM'])
        p.abs_ids.add(fid)
        item_ids.append(fid)
    gold, _ = b.resolver.lookup('Gold001', ['MISC'])
    loc_kw = LOC_TYPE_DUNGEON
    if spec.get('location_keyword'):
        loc_kw, _ = b.resolver.lookup(spec['location_keyword'], ['KYWD'])
    p.abs_ids.update({gold, loc_kw, BOSS_CONTAINER, SCRIPT_EVENT_NODE})
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

    kw_fid = b.own(new_id())
    kw = Record('KYWD', FormID(kw_fid), 0)
    kw.add_subrecord('EDID', z(f"{prefix}_Event"))
    plugin.add_record(kw)
    made.append(b.describe(kw))

    quest_fids, quest_ids = [], []
    for i, it in enumerate(items):
        lvli_fid = b.own(new_id())
        lv = Record('LVLI', FormID(lvli_fid), 0)
        lv.add_subrecord('EDID', z(f"{prefix}_{i}_Loot"))
        lv.add_subrecord('OBND', b'\0' * 12)
        lv.add_subrecord('LVLD', b'\0')
        lv.add_subrecord('LVLF', b'\0')
        lv.add_subrecord('LLCT', struct.pack('<B', 1))
        lv.add_subrecord('LVLO', struct.pack('<HHIHH', 1, 0, b.local(item_ids[i]), int(it['count']), 0))
        plugin.add_record(lv)
        made.append(b.describe(lv))

        qfid = b.own(new_id())
        quest_fids.append(qfid)
        quest_ids.append(f"{prefix}_{i}")
        q = Record('QUST', FormID(qfid), 0)
        q.add_subrecord('EDID', z(f"{prefix}_{i}"))
        q.add_subrecord('FULL', z(spec['quest_name']))
        q.add_subrecord('DNAM', struct.pack('<HBBII', 0, 50, 0, 0, 6))
        q.add_subrecord('ENAM', b'SCPT')
        q.add_subrecord('NEXT', b'')
        log = spec.get('log') or {}
        for idx, flags, qsdt, text in (
                (0, 2, 0, None),
                (ACTIVE, 0, 0, log.get('10', "I have taken a contract. The stones are in a chest deep in a dungeon.")),
                (DONE, 0, 1, log.get('20', "I delivered the stones and was paid."))):
            q.add_subrecord('INDX', struct.pack('<HBB', idx, flags, 0))
            q.add_subrecord('QSDT', struct.pack('<B', qsdt))
            if text:
                q.add_subrecord('CNAM', z(text))
        q.add_subrecord('QOBJ', struct.pack('<H', ACTIVE))
        q.add_subrecord('FNAM', struct.pack('<I', 0))
        q.add_subrecord('NNAM', z(f"Recover {int(it['count'])} {it['name']} from the chest in <Alias=GemLocation>"))
        q.add_subrecord('QSTA', struct.pack('<II', CHEST, 0))
        q.add_subrecord('ANAM', struct.pack('<I', 4))

        q.add_subrecord('ALST', struct.pack('<I', GIVER))
        q.add_subrecord('ALID', z('Questgiver'))
        q.add_subrecord('FNAM', struct.pack('<I', 0))
        q.add_subrecord('ALUA', fid_bytes(npc.form_id.value))
        q.add_subrecord('VTCK', fid_bytes(0))
        q.add_subrecord('ALED', b'')

        q.add_subrecord('ALLS', struct.pack('<I', LOCATION))
        q.add_subrecord('ALID', z('GemLocation'))
        q.add_subrecord('FNAM', struct.pack('<I', 0x100))
        for c in location_conditions(b.local(loc_kw), b.local(BOSS_CONTAINER)):
            q.add_subrecord('CTDA', c)
        q.add_subrecord('ALED', b'')

        q.add_subrecord('ALST', struct.pack('<I', CHEST))
        q.add_subrecord('ALID', z('GemContainer'))
        q.add_subrecord('FNAM', struct.pack('<I', 0))
        q.add_subrecord('ALFA', struct.pack('<I', LOCATION))
        q.add_subrecord('ALRT', fid_bytes(b.local(BOSS_CONTAINER)))
        q.add_subrecord('VTCK', fid_bytes(0))
        q.add_subrecord('ALED', b'')

        q.add_subrecord('ALST', struct.pack('<I', LOOT))
        q.add_subrecord('ALID', z('Gems'))
        q.add_subrecord('FNAM', struct.pack('<I', 0x104))
        q.add_subrecord('ALCO', fid_bytes(lvli_fid))
        q.add_subrecord('ALCA', struct.pack('<HH', CHEST, 0x8000))
        q.add_subrecord('ALCL', struct.pack('<I', 0))
        q.add_subrecord('VTCK', fid_bytes(0))
        q.add_subrecord('ALED', b'')
        plugin.add_record(q)
        made.append(b.describe(q))

    branch_fid, node_fid = b.own(new_id()), b.own(new_id())
    zero4 = struct.pack('<I', 0)
    br = Record('SMBN', FormID(branch_fid), 0)
    br.add_subrecord('EDID', z(f"{prefix}_Branch"))
    br.add_subrecord('PNAM', fid_bytes(b.local(SCRIPT_EVENT_NODE)))
    br.add_subrecord('SNAM', zero4)
    br.add_subrecord('CITC', zero4)
    br.add_subrecord('DNAM', zero4)
    br.add_subrecord('XNAM', zero4)
    plugin.add_record(br)
    made.append(b.describe(br))
    nd = Record('SMQN', FormID(node_fid), 0)
    nd.add_subrecord('EDID', z(f"{prefix}_Node"))
    nd.add_subrecord('PNAM', fid_bytes(branch_fid))
    nd.add_subrecord('SNAM', zero4)
    nd.add_subrecord('CITC', struct.pack('<I', 1))
    nd.add_subrecord('CTDA', event_condition(kw_fid))
    nd.add_subrecord('DNAM', struct.pack('<HH', 1, 1))
    nd.add_subrecord('XNAM', zero4)
    nd.add_subrecord('QNAM', struct.pack('<I', len(items)))
    for qf in quest_fids:
        nd.add_subrecord('NNAM', fid_bytes(qf))
    plugin.add_record(nd)
    made.append(b.describe(nd))

    props = [('EventKw', -kw_fid), ('Gold', gold)]
    props += [(f"Q{i}", -qf) for i, qf in enumerate(quest_fids)]
    stage = lambda qf, op, v: (GET_STAGE, -qf, op, float(v), SUBJECT)
    o, r, t = spec['offer'], spec['reminder'], spec['turn_in']
    p.topics = [('topic', o['prompt'], list(o['lines']), (offer_code(len(items)), props),
                 [stage(qf, NE, ACTIVE) for qf in quest_fids] + offer_conditions)]
    for i, it in enumerate(items):
        own_props = [('Contract', -quest_fids[i]), ('Item', item_ids[i]), ('Gold', gold)] + extra_props
        have = lambda op: (47, item_ids[i], op, float(int(it['count'])), 1)  # GetItemCount on the player
        p.topics.append(('topic', r['prompt'], list(r['lines']), None,
                         [stage(quest_fids[i], EQ, ACTIVE), have(4)]))
        p.topics.append(('topic', t['prompt'], list(t['lines']), (turn_in_code(spec, it), own_props),
                         [stage(quest_fids[i], EQ, ACTIVE), have(3)]))
    dq = spec.get('dialogue_quest_editor_id') or f"{npc.editor_id}DialogueQuest"
    if target.find_own(dq, ['QUST']) is None:
        raise ValueError(f"dialogue quest {dq!r} not found in {path.name}")
    b.make_dialogue(plugin, {'editor_id': npc.editor_id, 'quest_editor_id': dq}, p, new_id, made, npc.form_id.value)

    plugin.header.next_object_id = b.next_id
    target.save()
    for i, qid in enumerate(quest_ids):
        add_quest_stage_script(b, qid, target_plugin,
                               {0: "SetStage(10)", ACTIVE: "SetObjectiveDisplayed(10, True)"})
    check = Plugin.load(str(path))
    return {'path': str(path), 'masters': list(plugin.header.masters), 'records': made,
            'reloaded_records': len(check.records)}
