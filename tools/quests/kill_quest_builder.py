"""Author a kill quest unlocked by another quest: a placed target actor with an OnDeath script, a QUST and dialogue."""
import math
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID
from esplib.vmad import PROP_OBJECT, VmadData, VmadObject, VmadProperty, VmadScript

from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.papyrus.papyrus_build import compile_script
from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.quests.quest_builder import EQ, GE, LT, GET_STAGE, SUBJECT

QUEST_TYPE_SIDE = 8

REQUIRED = ('quest_editor_id', 'quest_name', 'target_base', 'placement', 'prerequisite_quest',
            'log', 'objectives', 'offer', 'turn_in', 'done')


def check_spec(spec):
    """Return a list of problems with the spec (empty when it is valid)."""
    errors = [f"missing field {k!r}" for k in REQUIRED if k not in spec]
    if 'quest_editor_id' in spec and not re.fullmatch(r'[A-Za-z0-9_]+', str(spec['quest_editor_id'])):
        errors.append("quest_editor_id may contain only letters, digits and underscores")
    for k in ('offer', 'turn_in', 'done'):
        d = spec.get(k)
        if k in spec and not (isinstance(d, dict) and d.get('prompt') and d.get('lines')):
            errors.append(f"{k} needs a prompt and lines")
    for x in [spec['during']] if 'during' in spec else []:
        if not (isinstance(x, dict) and x.get('prompt') and x.get('lines') and x.get('topics')
                and all(c.get('prompt') and c.get('lines') for c in x['topics'])):
            errors.append("during needs a parent prompt and lines plus topics, each with a prompt and lines")
    for k in ('10', '20', '30'):
        if 'log' in spec and k not in {str(i) for i in spec['log']}:
            errors.append(f"log needs an entry for stage {k}")
    for k in ('10', '20'):
        if 'objectives' in spec and k not in {str(i) for i in spec['objectives']}:
            errors.append(f"objectives needs an entry for {k}")
    return errors


def death_source(name):
    """Papyrus source for the target actor's OnDeath: completes objective 10 and moves the quest to stage 20."""
    return (f"Scriptname {name} Extends Actor\n\n"
            "Quest Property KillQuest Auto\n\n"
            "Event OnDeath(Actor akKiller)\n"
            "  If KillQuest.GetStage() == 10\n"
            "    KillQuest.SetObjectiveCompleted(10)\n"
            "    KillQuest.SetObjectiveDisplayed(20)\n"
            "    KillQuest.SetStage(20)\n"
            "  EndIf\n"
            "EndEvent\n")


def add_kill_quest(b, identifier, spec, target_plugin):
    """Write the target actor, quest and dialogue into target_plugin; b is an NpcBuilder."""
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be added inline")
    qid = spec['quest_editor_id']
    if any((r.editor_id or '').lower() == qid.lower() for r in b.m.records.values()) or \
            any((r.editor_id or '').lower() == qid.lower() for r in plugin.records):
        raise ValueError(f"editor_id {qid!r} already exists")
    npc = target.find_own(identifier, ['NPC_'])
    if npc is None:
        raise ValueError(f"NPC {identifier!r} not found in {path.name}")
    pre = target.find_own(spec['prerequisite_quest'], ['QUST'])
    if pre is None:
        raise ValueError(f"prerequisite quest {spec['prerequisite_quest']!r} not found in {path.name}")

    pl = spec['placement']
    p = Plan()
    p.cell = b.cells.find(pl.get('cell'), pl.get('worldspace'), pl.get('grid_x'), pl.get('grid_y'))
    world = next((w for t, _, w in p.cell[1] if t == 1 and w is not None), None)
    if world is not None:
        p.cell = b.cells.persistent(world.editor_id)
    base_fid, _ = b.resolver.lookup(spec['target_base'], ['NPC_'])
    p.abs_ids.add(base_fid)
    gold, _ = b.resolver.lookup('Gold001', ['MISC'])
    p.abs_ids.add(gold)

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

    stem = re.sub(r'[^A-Za-z0-9]', '', path.stem)[:16]
    quest_fid = b.own(new_id())
    npc_fid = npc.form_id.value
    pre_fid = pre.form_id.value

    # the target actor, persistent so the quest alias can mark it, with an OnDeath script
    sname = f"{stem}_{qid}_Death"
    pex = compile_script(sname, death_source(sname), path.parent / 'Scripts')
    script = VmadScript(name=sname, flags=0, properties=[
        VmadProperty(name='KillQuest', type=PROP_OBJECT, flags=1, value=VmadObject(form_id=quest_fid, alias=-1))])
    achr = Record('ACHR', FormID(b.own(new_id())), 0x400)
    achr.add_subrecord('EDID', z(qid + 'Target'))
    achr.add_subrecord('VMAD', VmadData(version=5, obj_format=2, scripts=[script]).to_bytes('ACHR'))
    achr.add_subrecord('NAME', fid_bytes(b.local(base_fid)))
    achr.add_subrecord('DATA', struct.pack('<6f', pl['x'], pl['y'], pl['z'], 0.0, 0.0,
                                           math.radians(pl.get('rotation_z', 0.0))))
    b.place_ref(plugin, p.cell, achr, True, made)
    made.append({'type': 'script', 'editor_id': sname, 'object_id': str(pex)})

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
    q.add_subrecord('ALID', z('Target'))
    q.add_subrecord('FNAM', struct.pack('<I', 2))
    q.add_subrecord('ALFR', fid_bytes(achr.form_id.value))
    q.add_subrecord('VTCK', fid_bytes(0))
    q.add_subrecord('ALED', b'')
    plugin.add_record(q)
    made.append(b.describe(q))

    # topics: unlocked by the prerequisite quest's last stage, then driven by this quest's stage
    qstage = lambda op, v: (GET_STAGE, -quest_fid, op, float(v), SUBJECT)
    unlocked = (GET_STAGE, -pre_fid, GE, float(spec.get('prerequisite_stage', 30)), SUBJECT)
    reward = int(spec.get('reward_gold', 0))
    topics = []
    o, t, d = spec['offer'], spec['turn_in'], spec['done']
    topics.append(('topic', o['prompt'], list(o['lines']),
                   ("Quest q = KillQuest as Quest\n"
                    "(q.GetAlias(1) as ReferenceAlias).ForceRefTo(TargetRef as ObjectReference)\n"
                    "q.SetStage(10)\nq.SetObjectiveDisplayed(10)",
                    [('KillQuest', -quest_fid), ('TargetRef', -achr.form_id.value)]),
                   [unlocked, qstage(EQ, 0)]))
    # one parent topic holds the flavor questions so the main menu stays short
    if spec.get('during'):
        dur = spec['during']
        during_conds = [qstage(GE, 10), qstage(LT, 20)]
        parent_idx = len(topics)
        topics.append(('topic', dur['prompt'], list(dur['lines']), None, during_conds))
        for x in dur['topics']:
            topics.append(('topic', x['prompt'], list(x['lines']), None, during_conds,
                           {'child_of': parent_idx}))
    code = ["Quest q = KillQuest as Quest"]
    props = [('KillQuest', -quest_fid)]
    if reward:
        code.append(f"Game.GetPlayer().AddItem(Gold, {reward})")
        props.append(('Gold', gold))
    code += ["q.SetObjectiveCompleted(20)", "q.SetStage(30)"]
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
