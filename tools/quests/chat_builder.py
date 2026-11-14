"""NPC-to-NPC scene chats: a follower says a trigger line, then holds a short scripted conversation with another NPC."""
import re
import struct

from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.npcs.npc_builder import GET_IS_ID, Plan, fid_bytes, z

SCENE_FLAGS = 0x10
ACTOR_FLAGS = 0x2A
ACTION_FLAGS = 0x8000
PHASE_TIMER = 200
ALIAS_OPTIONAL_REUSE = 0x2 | 0x8
SCENE_TOPIC_DATA = b'\x00\x02\x0e\x00'
SPEAKER, PARTNER = 'speaker', 'partner'
KEYS = {'scene_editor_id', 'quest_editor_id', 'speaker', 'partner', 'lines', 'trigger_lines', 'trigger_line',
        'dialogue_quest_editor_id', 'partner_reference', 'distance', 'hired', 'random_percent', 'once',
        'conditions'}


def check_spec(spec):
    """Error strings for a chat spec; empty when it is usable."""
    errors = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    errors += [f"unknown key {k!r}" for k in spec if k not in KEYS]
    for k in ('scene_editor_id', 'quest_editor_id'):
        if not re.fullmatch(r'[A-Za-z0-9_]+', str(spec.get(k, ''))):
            errors.append(f"{k} is required (letters, digits and underscores)")
    for k in ('speaker', 'partner'):
        if not spec.get(k):
            errors.append(f"{k} is required (an NPC editor ID, name or form ID)")
    lines = spec.get('lines')
    if not (isinstance(lines, list) and lines):
        errors.append("lines must be a non-empty list of {who: 'speaker'|'partner', text}")
    else:
        for i, ln in enumerate(lines):
            if not (isinstance(ln, dict) and ln.get('who') in (SPEAKER, PARTNER)
                    and isinstance(ln.get('text'), str) and ln['text'].strip()):
                errors.append(f"lines[{i}]: needs who ('speaker' or 'partner') and text")
    trig = spec.get('trigger_lines') or ([spec['trigger_line']] if spec.get('trigger_line') else [])
    if isinstance(trig, str):
        trig = [trig]
    if not (trig and all(isinstance(t, str) and t.strip() for t in trig)):
        errors.append("trigger_line (or trigger_lines) is required: what the follower says before the chat starts")
    texts = [ln.get('text', '') for ln in (lines or []) if isinstance(ln, dict)] + list(trig)
    try:
        for t in texts:
            z(str(t))
    except UnicodeEncodeError:
        errors.append("text contains characters outside Windows-1252")
    for k in ('distance', 'random_percent'):
        v = spec.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            errors.append(f"{k} must be a non-negative number")
    if spec.get('conditions') is not None and not isinstance(spec['conditions'], list):
        errors.append("conditions must be a list")
    return errors


def trigger_conditions(spec):
    """Condition dicts guarding the trigger line so the chat only starts at a sensible moment."""
    conds = []
    if spec.get('hired', True):
        conds.append({'function': 'GetInFaction', 'form': 'CurrentHireling', 'op': '==', 'value': 1})
    conds += [{'function': 'IsSneaking', 'op': '==', 'value': 0},
              {'function': 'IsInCombat', 'op': '==', 'value': 0},
              {'function': 'IsInDialogueWithPlayer', 'op': '==', 'value': 0}]
    if spec.get('partner_reference'):
        conds.append({'function': 'GetDistance', 'form': spec['partner_reference'], 'op': '<',
                      'value': spec.get('distance', 900), 'run_on': 'target'})
    pct = spec.get('random_percent', 10)
    if pct:
        conds.append({'function': 'GetRandomPercent', 'op': '<=', 'value': pct})
    return conds + list(spec.get('conditions') or [])


def ensure_alias(quest, npc_fid, name):
    """Alias ID in quest filled by npc_fid as a unique actor, adding the alias when it is missing."""
    cur, ids, found = None, [], None
    for s in quest.subrecords:
        if s.signature == 'ALST':
            cur = struct.unpack('<I', bytes(s.data))[0]
            ids.append(cur)
        elif s.signature == 'ALUA' and struct.unpack('<I', bytes(s.data))[0] == npc_fid and found is None:
            found = cur
    if found is not None:
        return found
    new = max(ids, default=-1) + 1
    anam = next((s for s in quest.subrecords if s.signature == 'ANAM'), None)
    if anam is None:
        raise ValueError("chat quest has no ANAM (next alias ID)")
    anam.data = struct.pack('<I', new + 1)
    quest.add_subrecord('ALST', struct.pack('<I', new))
    quest.add_subrecord('ALID', z(name))
    quest.add_subrecord('FNAM', struct.pack('<I', ALIAS_OPTIONAL_REUSE))
    quest.add_subrecord('ALUA', fid_bytes(npc_fid))
    quest.add_subrecord('VTCK', fid_bytes(0))
    quest.add_subrecord('ALED', b'')
    return new


def scene_record(fid, editor_id, quest_fid, aliases, actions):
    """SCEN with one phase per action. aliases: the two actor alias IDs; actions: [(alias, topic fid, head-track alias)]."""
    rec = Record('SCEN', FormID(fid), 0)
    rec.add_subrecord('EDID', z(editor_id))
    rec.add_subrecord('FNAM', struct.pack('<I', SCENE_FLAGS))
    for _ in actions:
        for sig, data in (('HNAM', b''), ('NAM0', b'\0'), ('NEXT', b''), ('NEXT', b''),
                          ('WNAM', struct.pack('<I', PHASE_TIMER)), ('HNAM', b'')):
            rec.add_subrecord(sig, data)
    for alias in aliases:
        rec.add_subrecord('ALID', struct.pack('<I', alias))
        rec.add_subrecord('LNAM', struct.pack('<I', 0))
        rec.add_subrecord('DNAM', struct.pack('<I', ACTOR_FLAGS))
    for i, (alias, topic, listener) in enumerate(actions):
        rec.add_subrecord('ANAM', struct.pack('<H', 0))
        rec.add_subrecord('NAM0', b'\0')
        rec.add_subrecord('ALID', struct.pack('<I', alias))
        rec.add_subrecord('INAM', struct.pack('<I', i + 1))
        rec.add_subrecord('FNAM', struct.pack('<I', ACTION_FLAGS))
        rec.add_subrecord('SNAM', struct.pack('<I', i))
        rec.add_subrecord('ENAM', struct.pack('<I', i))
        rec.add_subrecord('DATA', fid_bytes(topic))
        rec.add_subrecord('HTID', struct.pack('<I', listener))
        rec.add_subrecord('DMAX', struct.pack('<f', 10.0))
        rec.add_subrecord('DMIN', struct.pack('<f', 1.0))
        rec.add_subrecord('DEMO', struct.pack('<I', 0))
        rec.add_subrecord('DEVA', struct.pack('<I', 0))
        rec.add_subrecord('ANAM', b'')
    rec.add_subrecord('PNAM', fid_bytes(quest_fid))
    rec.add_subrecord('INAM', struct.pack('<I', len(actions)))
    rec.add_subrecord('VNAM', struct.pack('<4I', 3, 3, 3, 3))
    return rec


def add_npc_chat(b, spec, target_plugin):
    """Add a scene in which a follower and another NPC talk, started by a trigger line the follower says.

    Creates (or extends) the chat quest with an alias per NPC, one scene-line topic per line, the SCEN, and a
    trigger idle line on the follower's dialogue quest whose fragment starts the scene.
    """
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; dialogue text cannot be added inline")
    sid, qid = spec['scene_editor_id'], spec['quest_editor_id']
    if any((r.editor_id or '').lower() == sid.lower() for r in b.m.records.values()) or \
            any((r.editor_id or '').lower() == sid.lower() for r in plugin.records):
        raise ValueError(f"editor_id {sid!r} already exists")

    p = Plan()
    npcs = {}
    for role in (SPEAKER, PARTNER):
        ident = spec[role]
        own = target.find_own(ident, ['NPC_'])
        if own is not None:
            npcs[role] = (own.form_id.value, own.editor_id or re.sub(r'\W', '', str(ident)) or role, True)
        else:
            fid, _ = b.resolver.lookup(ident, ['NPC_'])
            p.abs_ids.add(fid)
            npcs[role] = (fid, re.sub(r'\W', '', str(ident)) or role, False)
    if npcs[SPEAKER][0] == npcs[PARTNER][0]:
        raise ValueError("speaker and partner must be different NPCs")
    cond_spec = {'conditions': trigger_conditions(spec)}
    conds = b.plan_conditions(p, cond_spec, 0)
    if p.errors:
        raise ValueError("; ".join(p.errors))

    b.target = target
    b.plugin = plugin
    b.add_masters(plugin, p)
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    b.next_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    made = []

    def new_id():
        v = b.next_id
        b.next_id += 1
        return v

    local = {role: (fid if own else b.local(fid)) for role, (fid, _, own) in npcs.items()}

    quest = target.find_own(qid, ['QUST'])
    if quest is None:
        quest = Record('QUST', FormID(b.own(new_id())), 0)
        quest.add_subrecord('EDID', z(qid))
        quest.add_subrecord('DNAM', struct.pack('<HBBII', 0x1, 60, 0, 0, 0))
        quest.add_subrecord('NEXT', b'')
        quest.add_subrecord('ANAM', struct.pack('<I', 0))
        plugin.add_record(quest)
        made.append(b.describe(quest))
    alias = {role: ensure_alias(quest, local[role], npcs[role][1]) for role in (SPEAKER, PARTNER)}
    quest_fid = quest.form_id.value

    top = next((g for g in plugin.groups if g.group_type == 0 and g.label == 'DIAL'), None)
    if top is None:
        top = GroupRecord(0, 'DIAL')
        plugin.groups.append(top)
    actions = []
    for i, ln in enumerate(spec['lines'], 1):
        who = ln['who']
        other = PARTNER if who == SPEAKER else SPEAKER
        dial = Record('DIAL', FormID(b.own(new_id())), 0)
        dial.add_subrecord('EDID', z(f"{sid}Line{i}"))
        dial.add_subrecord('PNAM', struct.pack('<f', 50.0))
        dial.add_subrecord('QNAM', fid_bytes(quest_fid))
        dial.add_subrecord('DATA', SCENE_TOPIC_DATA)
        dial.add_subrecord('SNAM', b'SCEN')
        dial.add_subrecord('TIFC', struct.pack('<I', 1))
        plugin.add_record(dial)
        group = GroupRecord(7, dial.form_id.value)
        top.records.append(group)
        info = Record('INFO', FormID(b.own(new_id())), 0)
        info.add_subrecord('ENAM', struct.pack('<HH', 0, 0))
        info.add_subrecord('CNAM', b'\x00')
        info.add_subrecord('TRDT', struct.pack('<II4xB3xIB3x', 0, 50, 1, 0, 1))
        info.add_subrecord('NAM1', z(ln['text']))
        info.add_subrecord('NAM2', b'\x00')
        info.add_subrecord('NAM3', b'\x00')
        info.add_subrecord('CTDA', struct.pack('<B3xfH2xIIIIi', 0, 1.0, GET_IS_ID, local[who], 0, 0, 0, -1))
        group.records.append(info)
        b.register(plugin, info)
        actions.append((alias[who], dial.form_id.value, alias[other]))
    scene = scene_record(b.own(new_id()), sid, quest_fid, sorted(set(alias.values())), actions)
    plugin.add_record(scene)
    made.append(b.describe(scene))

    trig = spec.get('trigger_lines') or [spec['trigger_line']]
    if isinstance(trig, str):
        trig = [trig]
    opts = {}
    if len(trig) > 1:
        opts.update(answers=[[t] for t in trig], pick='random')
    if spec.get('once', True):
        opts['say_once'] = True
    code = "Scene chat = ChatScene as Scene\nchat.Start()"
    topic = ('idle', None, [trig[0]], (code, [('ChatScene', -scene.form_id.value)]), conds, opts)
    p.topics = [topic]
    dq = spec.get('dialogue_quest_editor_id') or f"{npcs[SPEAKER][1]}DialogueQuest"
    b.make_dialogue(plugin, {'editor_id': npcs[SPEAKER][1], 'quest_editor_id': dq}, p, new_id, made,
                    local[SPEAKER])

    plugin.header.next_object_id = b.next_id
    target.save()
    check = Plugin.load(str(path))
    return {'path': str(path), 'quest': qid, 'scene': sid, 'aliases': alias, 'lines': len(actions),
            'trigger_quest': dq, 'masters': list(plugin.header.masters), 'records': made,
            'reloaded_records': len(check.records)}
