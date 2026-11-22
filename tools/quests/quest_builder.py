"""Author a complete fetch quest for an NPC: quest item, placed reference, QUST, dialogue and scripts."""
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID
from esplib.vmad import PROP_OBJECT, VmadData, VmadObject, VmadProperty, VmadScript

from tools.npcs import challenges
from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.papyrus.papyrus_build import compile_script
from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.quests.quest_core import (write_alias, write_header, write_next_alias_id, write_objective,
                                     write_stage)

GET_ITEM_COUNT, GET_STAGE = 47, 58
EQ, GE, LT = 0, 3, 4
SUBJECT, TARGET = 0, 1
QUEST_TYPE_MISC = 6
QUEST_OBJECT = 4
PLAYER_NPC = 7
REFUSED_STAGE = 5

REQUIRED = ('quest_editor_id', 'quest_name', 'item_editor_id', 'item_name', 'item_clone_from', 'placement',
            'log', 'objectives', 'offer', 'reminder', 'turn_in', 'done')


def check_spec(spec):
    """Return a list of problems with the spec (empty when it is valid)."""
    errors = [f"missing field {k!r}" for k in REQUIRED if k not in spec]
    for k in ('quest_editor_id', 'item_editor_id'):
        if k in spec and not re.fullmatch(r'[A-Za-z0-9_]+', str(spec[k])):
            errors.append(f"{k} may contain only letters, digits and underscores")
    for k in ('offer', 'reminder', 'turn_in', 'done'):
        d = spec.get(k)
        if k in spec and not (isinstance(d, dict) and d.get('prompt') and d.get('lines')):
            errors.append(f"{k} needs a prompt and lines")
    offer = spec.get('offer')
    if isinstance(offer, dict) and 'options' in offer:
        for k in ('options', 'accept', 'refusal', 'revisit'):
            if k not in offer:
                errors.append(f"offer with options also needs {k!r}")
        for o in list(offer.get('options') or []) + [offer.get('accept') or {}, offer.get('refusal') or {}]:
            if not (isinstance(o, dict) and o.get('prompt') and o.get('lines')):
                errors.append("each offer option, the accept and the refusal need a prompt and lines")
        if not (offer.get('revisit') or {}).get('lines'):
            errors.append("offer revisit needs lines")
    if isinstance(offer, dict):
        gate = (offer.get('accept') or {}) if 'options' in offer else offer
        if gate.get('challenge') is not None:
            errors += challenges.check(gate['challenge'], "the accepting reply's challenge")
    for k in ('10', '20', '30'):
        if 'log' in spec and k not in {str(i) for i in spec['log']}:
            errors.append(f"log needs an entry for stage {k}")
        if 'objectives' in spec and k != '30' and k not in {str(i) for i in spec['objectives']}:
            errors.append(f"objectives needs an entry for {k}")
    return errors


def pickup_source(name):
    """Papyrus source for the quest item: advances the quest to stage 20 when the player picks it up."""
    return (f"Scriptname {name} Extends ObjectReference\n\n"
            "Quest Property FetchQuest Auto\n\n"
            "Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)\n"
            "  If akNewContainer == Game.GetPlayer() && FetchQuest.GetStage() == 10\n"
            "    FetchQuest.SetObjectiveCompleted(10)\n"
            "    FetchQuest.SetObjectiveDisplayed(20)\n"
            "    FetchQuest.SetStage(20)\n"
            "  EndIf\n"
            "EndEvent\n")


def add_fetch_quest(b, identifier, spec, target_plugin):
    """Write everything into target_plugin; b is an NpcBuilder."""
    errors = check_spec({k: v for k, v in spec.items()})
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be added inline")
    qid, iid = spec['quest_editor_id'], spec['item_editor_id']
    for e in (qid, iid):
        if any((r.editor_id or '') .lower() == e.lower() for r in b.m.records.values()) or \
                any((r.editor_id or '').lower() == e.lower() for r in plugin.records):
            raise ValueError(f"editor_id {e!r} already exists")
    npc = target.find_own(identifier, ['NPC_'])
    if npc is None:
        raise ValueError(f"NPC {identifier!r} not found in {path.name}")
    pl = spec['placement']
    p = Plan()
    p.cell = b.cells.find(pl.get('cell'), pl.get('worldspace'), pl.get('grid_x'), pl.get('grid_y'))
    # a compass marker needs a quest alias, which needs a persistent reference
    persistent = bool(spec.get('marker', True) or pl.get('persistent', False))
    world = next((w for t, _, w in p.cell[1] if t == 1 and w is not None), None)
    if persistent and world is not None:
        p.cell = b.cells.persistent(world.editor_id)
    _, src = b.resolver.lookup(spec['item_clone_from'], ['MISC'])
    p.clone = src
    gold, _ = b.resolver.lookup('Gold001', ['MISC'])
    p.abs_ids.add(gold)
    offer_spec = spec['offer']
    gate = (offer_spec.get('accept') or {}) if 'options' in offer_spec else offer_spec
    found = challenges.forms(b.resolver) if gate.get('challenge') else None
    if found:
        p.abs_ids.update(found.values())
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
    scripts = path.parent / 'Scripts'
    quest_fid = b.own(new_id())
    item_fid = b.own(new_id())
    npc_fid = npc.form_id.value

    # quest item with a pickup script
    pname = f"{stem}_{qid}_Pickup"
    pex = compile_script(pname, pickup_source(pname), scripts)
    item = b.override(plugin, src)
    item.form_id = FormID(item_fid)
    item.subrecords[:] = [s for s in item.subrecords if s.signature not in ('EDID', 'FULL', 'VMAD')]
    item.add_subrecord('EDID', z(iid))
    item.add_subrecord('FULL', z(spec['item_name']))
    script = VmadScript(name=pname, flags=0, properties=[
        VmadProperty(name='FetchQuest', type=PROP_OBJECT, flags=1, value=VmadObject(form_id=quest_fid, alias=-1))])
    item.add_subrecord('VMAD', VmadData(version=5, obj_format=2, scripts=[script]).to_bytes('MISC'))
    item.subrecords.sort(key=lambda s: {'EDID': 0, 'VMAD': 1}.get(s.signature, 2))
    plugin.add_record(item)
    made.append(b.describe(item))
    made.append({'type': 'script', 'editor_id': pname, 'object_id': str(pex)})

    # placed reference
    ref = Record('REFR', FormID(b.own(new_id())), 0x400 if persistent else 0)
    ref.add_subrecord('EDID', z(iid + 'Ref'))
    ref.add_subrecord('NAME', fid_bytes(item_fid))
    if not spec.get('stolen', False):
        # an explicit player owner stops an owned cell (shop, inn) making the pickup theft
        ref.add_subrecord('XOWN', fid_bytes(PLAYER_NPC))
    ref.add_subrecord('DATA', struct.pack('<6f', pl['x'], pl['y'], pl['z'], 0.0, 0.0, 0.0))
    b.place_ref(plugin, p.cell, ref, persistent, made)

    # quest
    q = Record('QUST', FormID(quest_fid), 0)
    write_header(q, qid, spec['quest_name'], quest_type=QUEST_TYPE_MISC)
    log = {int(k): v for k, v in spec['log'].items()}
    if 'options' in spec['offer']:
        log[REFUSED_STAGE] = None  # hidden stage marking a declined offer, with no journal entry
    for idx, text in sorted(log.items()):
        write_stage(q, idx, text)
    for idx, text in sorted((int(k), v) for k, v in spec['objectives'].items()):
        targets = [(1, 0)] if idx == 10 and spec.get('marker', True) else [(0, 1)] if idx == 20 else []
        write_objective(q, idx, text, targets)
    write_next_alias_id(q, 2 if spec.get('marker', True) else 1)
    write_alias(q, 0, spec.get('alias_name') or 'QuestGiver', 2, [('ALUA', fid_bytes(npc_fid))])
    if spec.get('marker', True):
        write_alias(q, 1, 'QuestItem', 2 | QUEST_OBJECT, [('ALFR', fid_bytes(ref.form_id.value))])
    plugin.add_record(q)
    made.append(b.describe(q))

    # dialogue, driven by quest stage and the item in the player's inventory
    qprops = {'FetchQuest': -quest_fid}
    stage = lambda op, v: (GET_STAGE, -quest_fid, op, float(v), SUBJECT)
    has = lambda op, v: (GET_ITEM_COUNT, -item_fid, op, float(v), TARGET)
    reward = int(spec.get('reward_gold', 0))
    topics = []
    o, r, t, d = spec['offer'], spec['reminder'], spec['turn_in'], spec['done']
    start = ["Quest q = FetchQuest as Quest"]
    if spec.get('marker', True):
        # fall back to filling the alias from script, in case the engine left the ALFR fill empty
        start.append("(q.GetAlias(1) as ReferenceAlias).ForceRefTo(ItemRef as ObjectReference)")
    start += ["q.SetStage(10)", "q.SetObjectiveDisplayed(10)"]
    offer_props = [('FetchQuest', -quest_fid)] + ([('ItemRef', -ref.form_id.value)] if spec.get('marker', True) else [])
    def accepting(prompt, lines, script, conds, opts, challenge):
        """The topic that starts the quest; with a challenge it is gated by the check, and a failed check
        returns the player to the main menu to try again."""
        if not challenge:
            return ('topic', prompt, list(lines), script, conds, opts) if opts else \
                ('topic', prompt, list(lines), script, conds)
        prompt, variants = challenges.expand(challenge, prompt, list(lines), script, found)
        return ('topic', prompt, list(lines), None, conds, dict(opts, variants=variants))

    if 'options' in o:
        # roleplay stances: the pitch offers only the stance replies; every stance reply is followed by the accept and
        # refusal prompts, and accepting is what starts the quest. A refusal parks the quest at a hidden stage, and
        # the revisit pitch then offers accept and refusal directly
        refuse_code = ("Quest q = FetchQuest as Quest", f"q.SetStage({REFUSED_STAGE})")

        def decision(conds, parent, also_from=()):
            """Accept and refusal topics, offered after the topic at index `parent` (and every `also_from`)."""
            link = {'child_of': parent, 'also_from': list(also_from)}
            topics.append(accepting(o['accept']['prompt'], o['accept']['lines'], ("\n".join(start), offer_props),
                                    conds, dict(link), o['accept'].get('challenge')))
            topics.append(('topic', o['refusal']['prompt'], list(o['refusal']['lines']),
                           ("\n".join(refuse_code), [('FetchQuest', -quest_fid)]), conds,
                           dict(link, goodbye=True)))

        conds = [stage(EQ, 0)]
        pitch = len(topics)
        topics.append(('topic', o['prompt'], list(o['lines']), None, conds))
        first = len(topics)
        for opt in o['options']:
            topics.append(('topic', opt['prompt'], list(opt['lines']), None, conds, {'child_of': pitch}))
        decision(conds, first, range(first + 1, first + len(o['options'])))
        conds = [stage(EQ, REFUSED_STAGE)]
        pitch = len(topics)
        topics.append(('topic', o['revisit'].get('prompt', "About that errand..."), list(o['revisit']['lines']), None, conds))
        decision(conds, pitch)
    else:
        topics.append(accepting(o['prompt'], o['lines'], ("\n".join(start), offer_props), [stage(EQ, 0)], {},
                                o.get('challenge')))
    topics.append(('topic', r['prompt'], list(r['lines']), None, [stage(GE, 10), stage(LT, 30), has(LT, 1)]))
    code = ["Game.GetPlayer().RemoveItem(Item, 1)"]
    props = [('FetchQuest', -quest_fid), ('Item', -item_fid)]
    if reward:
        code.append(f"Game.GetPlayer().AddItem(Gold, {reward})")
        props.append(('Gold', gold))
    code += ["Quest q = FetchQuest as Quest", "q.SetObjectiveCompleted(10)",
             "q.SetObjectiveCompleted(20)", "q.SetStage(30)"]
    topics.append(('topic', t['prompt'], list(t['lines']), ("\n".join(code), props),
                   [stage(GE, 10), stage(LT, 30), has(GE, 1)]))
    topics.append(('topic', d['prompt'], list(d['lines']), None, [stage(GE, 30)]))
    p.topics = topics
    # topics live in the NPC's proven dialogue quest; the fetch quest itself only tracks stages and objectives
    dq = spec.get('dialogue_quest_editor_id') or f"{npc.editor_id}DialogueQuest"
    if target.find_own(dq, ['QUST']) is None:
        dq = qid
    b.target = target  # lets make_dialogue reuse the NPC's existing dialogue quest instead of adding a second one
    b.make_dialogue(plugin, {'editor_id': npc.editor_id, 'quest_editor_id': dq}, p, new_id, made, npc_fid)

    plugin.header.next_object_id = b.next_id
    target.save()
    check = Plugin.load(str(path))
    return {'path': str(path), 'masters': list(plugin.header.masters), 'records': made,
            'reloaded_records': len(check.records)}
