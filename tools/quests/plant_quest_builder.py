"""Author a plant-the-item quest: a custom book is given to the player, placed in a world container, and a unique weapon rewarded."""
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID
from esplib.vmad import PROP_OBJECT, VmadData, VmadObject, VmadProperty, VmadScript

from tools.records.clone import clone_into
from tools.cells.persist import persist_reference
from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.papyrus.papyrus_build import compile_script
from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.quests.quest_builder import EQ, GE, LT, GET_STAGE, GET_ITEM_COUNT, SUBJECT, TARGET

QUEST_TYPE_SIDE = 8
BOOK_ORDER = ['EDID', 'VMAD', 'OBND', 'FULL', 'MODL', 'MODT', 'DESC', 'KSIZ', 'KWDA', 'DATA', 'INAM', 'CNAM']

REQUIRED = ('quest_editor_id', 'quest_name', 'prerequisite_quest', 'book', 'container_ref', 'reward_weapon',
            'log', 'objectives', 'offer', 'turn_in', 'done')


def check_spec(spec):
    """Return a list of problems with the spec (empty when it is valid)."""
    errors = [f"missing field {k!r}" for k in REQUIRED if k not in spec]
    if 'quest_editor_id' in spec and not re.fullmatch(r'[A-Za-z0-9_]+', str(spec['quest_editor_id'])):
        errors.append("quest_editor_id may contain only letters, digits and underscores")
    for k, keys in (('book', ('editor_id', 'name', 'text', 'clone_from')),
                    ('reward_weapon', ('editor_id', 'name', 'clone_from', 'enchantment'))):
        d = spec.get(k)
        if k in spec and not (isinstance(d, dict) and all(d.get(x) for x in keys)):
            errors.append(f"{k} needs {', '.join(keys)}")
    for k in ('offer', 'turn_in', 'done'):
        d = spec.get(k)
        if k in spec and not (isinstance(d, dict) and d.get('prompt') and d.get('lines')):
            errors.append(f"{k} needs a prompt and lines")
    dur = spec.get('during')
    if dur and not (dur.get('prompt') and dur.get('lines') and dur.get('topics')
                    and all(c.get('prompt') and c.get('lines') for c in dur['topics'])):
        errors.append("during needs a parent prompt and lines plus topics, each with a prompt and lines")
    if spec.get('lost_book') and not (spec['lost_book'].get('prompt') and spec['lost_book'].get('lines')):
        errors.append("lost_book needs a prompt and lines")
    for k in ('10', '20', '30'):
        if 'log' in spec and k not in {str(i) for i in spec['log']}:
            errors.append(f"log needs an entry for stage {k}")
    for k in ('10', '20'):
        if 'objectives' in spec and k not in {str(i) for i in spec['objectives']}:
            errors.append(f"objectives needs an entry for {k}")
    return errors


def plant_source(name, chest_id):
    """Papyrus source for the book: advances the quest once it lands in the container with form id chest_id."""
    return (f"Scriptname {name} Extends ObjectReference\n\n"
            "Quest Property PlantQuest Auto\n"
            "ObjectReference Property ChestRef Auto\n\n"
            "Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)\n"
            f"  If akNewContainer != None && akNewContainer.GetFormID() == {chest_id} && PlantQuest.GetStage() == 10\n"
            "    PlantQuest.SetObjectiveCompleted(10)\n"
            "    PlantQuest.SetObjectiveDisplayed(20)\n"
            "    PlantQuest.SetStage(20)\n"
            "  EndIf\n"
            "EndEvent\n")


def add_plant_quest(b, identifier, spec, target_plugin):
    """Write the book, reward weapon, quest and dialogue into target_plugin; b is an NpcBuilder."""
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; text cannot be added inline")
    qid, book, weap = spec['quest_editor_id'], spec['book'], spec['reward_weapon']
    for e in (qid, book['editor_id'], weap['editor_id']):
        if any((r.editor_id or '').lower() == e.lower() for r in b.m.records.values()) or \
                any((r.editor_id or '').lower() == e.lower() for r in plugin.records):
            raise ValueError(f"editor_id {e!r} already exists")
    npc = target.find_own(identifier, ['NPC_'])
    if npc is None:
        raise ValueError(f"NPC {identifier!r} not found in {path.name}")
    pre = target.find_own(spec['prerequisite_quest'], ['QUST'])
    if pre is None:
        raise ValueError(f"prerequisite quest {spec['prerequisite_quest']!r} not found in {path.name}")

    p = Plan()
    _, book_src = b.resolver.lookup(book['clone_from'], ['BOOK'])
    _, weap_src = b.resolver.lookup(weap['clone_from'], ['WEAP'])
    ench_fid, _ = b.resolver.lookup(weap['enchantment'], ['ENCH'])
    if not re.fullmatch(r'(0x)?[0-9a-fA-F]{1,8}', str(spec['container_ref'])):
        raise ValueError("container_ref must be a hex reference ID from Skyrim.esm (see search_references)")
    chest_abs = int(str(spec['container_ref']), 16) & 0xFFFFFF
    p.abs_ids.update({ench_fid, chest_abs})
    if int(spec.get('reward_gold', 0)):
        p.abs_ids.add(b.resolver.lookup('Gold001', ['MISC'])[0])
    p.clone = book_src

    b.target = target
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    b.next_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    b.plugin = plugin
    made = []
    # the alias can only fill (and show a marker) for a persistent reference
    persist_reference(b, chest_abs, made)

    def new_id():
        """Hand out the next free form id from the builder."""
        v = b.next_id
        b.next_id += 1
        return v

    stem = re.sub(r'[^A-Za-z0-9]', '', path.stem)[:16]
    scripts = path.parent / 'Scripts'
    quest_fid = b.own(new_id())
    book_fid = b.own(new_id())
    weap_fid = b.own(new_id())
    npc_fid = npc.form_id.value
    pre_fid = pre.form_id.value
    chest_fid = b.local(chest_abs)

    # the book carries the script that notices it being placed in the container
    sname = f"{stem}_{qid}_Plant"
    pex = compile_script(sname, plant_source(sname, chest_abs), scripts)
    script = VmadScript(name=sname, flags=0, properties=[
        VmadProperty(name='PlantQuest', type=PROP_OBJECT, flags=1, value=VmadObject(form_id=quest_fid, alias=-1)),
        VmadProperty(name='ChestRef', type=PROP_OBJECT, flags=1, value=VmadObject(form_id=chest_fid, alias=-1))])
    brec = clone_into(b, plugin, book_src, book_fid, book['editor_id'], ('FULL', 'DESC', 'CNAM', 'VMAD'),
                      [('FULL', z(book['name'])), ('DESC', z(book['text'])),
                       ('VMAD', VmadData(version=5, obj_format=2, scripts=[script]).to_bytes('BOOK'))])
    brec.subrecords.sort(key=lambda s: BOOK_ORDER.index(s.signature) if s.signature in BOOK_ORDER else 99)
    plugin.add_record(brec)
    made.append(b.describe(brec))
    made.append({'type': 'script', 'editor_id': sname, 'object_id': str(pex)})

    # the reward weapon: a poor axe with a stamina-draining enchantment
    wrec = clone_into(b, plugin, weap_src, weap_fid, weap['editor_id'], ('FULL', 'DESC', 'VMAD'),
                      [('FULL', z(weap['name'])), ('EITM', fid_bytes(b.local(ench_fid))),
                       ('EAMT', struct.pack('<H', int(weap.get('charge', 500))))])
    if 'damage' in weap or 'value' in weap:
        data = bytearray(wrec.get_subrecord('DATA').data)
        value, weight, damage = struct.unpack('<IfH', bytes(data))
        data = struct.pack('<IfH', int(weap.get('value', value)), weight, int(weap.get('damage', damage)))
        wrec.get_subrecord('DATA').data = data
    order = ['EDID', 'VMAD', 'OBND', 'FULL', 'MODL', 'MODT', 'EITM', 'EAMT', 'ETYP']
    wrec.subrecords.sort(key=lambda s: order.index(s.signature) if s.signature in order else 99)
    plugin.add_record(wrec)
    made.append(b.describe(wrec))

    q = Record('QUST', FormID(quest_fid), 0)
    q.add_subrecord('EDID', z(qid))
    q.add_subrecord('FULL', z(spec['quest_name']))
    q.add_subrecord('DNAM', struct.pack('<HBBII', 0x1, 50, 0, 0, QUEST_TYPE_SIDE))
    q.add_subrecord('NEXT', b'')
    for idx, text in sorted((int(k), v) for k, v in spec['log'].items()):
        q.add_subrecord('INDX', struct.pack('<HBB', idx, 0, 0))
        q.add_subrecord('QSDT', struct.pack('<B', 1 if idx >= 30 else 0))
        q.add_subrecord('CNAM', z(text))
    for idx, text in sorted((int(k), v) for k, v in spec['objectives'].items()):
        q.add_subrecord('QOBJ', struct.pack('<H', idx))
        q.add_subrecord('FNAM', struct.pack('<I', 0))
        q.add_subrecord('NNAM', z(text))
        q.add_subrecord('QSTA', struct.pack('<II', 1, 0) if idx == 10 else struct.pack('<II', 0, 1))
    q.add_subrecord('ANAM', struct.pack('<I', 2))
    q.add_subrecord('ALST', struct.pack('<I', 0))
    q.add_subrecord('ALID', z('QuestGiver'))
    q.add_subrecord('FNAM', struct.pack('<I', 2))
    q.add_subrecord('ALUA', fid_bytes(npc_fid))
    q.add_subrecord('VTCK', fid_bytes(0))
    q.add_subrecord('ALED', b'')
    q.add_subrecord('ALST', struct.pack('<I', 1))
    q.add_subrecord('ALID', z('Container'))
    q.add_subrecord('FNAM', struct.pack('<I', 2))
    q.add_subrecord('ALFR', fid_bytes(chest_fid))
    q.add_subrecord('VTCK', fid_bytes(0))
    q.add_subrecord('ALED', b'')
    plugin.add_record(q)
    made.append(b.describe(q))

    qstage = lambda op, v: (GET_STAGE, -quest_fid, op, float(v), SUBJECT)
    has_book = lambda op, v: (GET_ITEM_COUNT, -book_fid, op, float(v), TARGET)
    unlocked = (GET_STAGE, -pre_fid, GE, float(spec.get('prerequisite_stage', 30)), SUBJECT)
    topics = []
    o, t, d = spec['offer'], spec['turn_in'], spec['done']
    topics.append(('topic', o['prompt'], list(o['lines']),
                   ("Quest q = PlantQuest as Quest\n"
                    "Game.GetPlayer().AddItem(BookItem, 1)\n"
                    "(q.GetAlias(1) as ReferenceAlias).ForceRefTo(ChestRef as ObjectReference)\n"
                    "q.SetStage(10)\nq.SetObjectiveDisplayed(10)",
                    [('PlantQuest', -quest_fid), ('BookItem', -book_fid), ('ChestRef', chest_abs)]),
                   [unlocked, qstage(EQ, 0)]))
    active = [qstage(GE, 10), qstage(LT, 20)]
    dur = spec.get('during')
    if dur or spec.get('lost_book'):
        parent_idx = len(topics)
        parent = dur or {'prompt': spec['offer']['prompt'], 'lines': ['Yes?']}
        topics.append(('topic', parent['prompt'], list(parent['lines']), None, active))
        for x in (dur or {}).get('topics', []):
            topics.append(('topic', x['prompt'], list(x['lines']), None, active, {'child_of': parent_idx}))
        if spec.get('lost_book'):
            lb = spec['lost_book']
            topics.append(('topic', lb['prompt'], list(lb['lines']),
                           ("Game.GetPlayer().AddItem(BookItem, 1)", [('BookItem', -book_fid)]),
                           active + [has_book(LT, 1)], {'child_of': parent_idx}))
    code = ["Game.GetPlayer().AddItem(RewardItem, 1)"]
    props = [('PlantQuest', -quest_fid), ('RewardItem', -weap_fid)]
    if int(spec.get('reward_gold', 0)):
        gold, _ = b.resolver.lookup('Gold001', ['MISC'])
        code.append(f"Game.GetPlayer().AddItem(Gold, {int(spec['reward_gold'])})")
        props.append(('Gold', gold))
    code += ["Quest q = PlantQuest as Quest", "q.SetObjectiveCompleted(20)", "q.SetStage(30)"]
    topics.append(('topic', t['prompt'], list(t['lines']), ("\n".join(code), props),
                   [qstage(GE, 20), qstage(LT, 30)]))
    topics.append(('topic', d['prompt'], list(d['lines']), None, [qstage(GE, 30)]))
    p.topics = topics
    dq = spec.get('dialogue_quest_editor_id') or f"{npc.editor_id}DialogueQuest"
    if target.find_own(dq, ['QUST']) is None:
        dq = qid
    b.make_dialogue(plugin, {'editor_id': npc.editor_id, 'quest_editor_id': dq}, p, new_id, made, npc_fid)

    plugin.header.next_object_id = b.next_id
    target.save()
    check = Plugin.load(str(path))
    return {'path': str(path), 'masters': list(plugin.header.masters), 'records': made,
            'reloaded_records': len(check.records)}
