"""NPC-to-NPC scene chats: a follower says a trigger line, then holds a short scripted conversation with another NPC."""
import re
import struct
from pathlib import Path

from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.npcs.npc_builder import GET_IS_ID, Plan, fid_bytes, z
from tools.papyrus.papyrus_build import script_name
from tools.records.maintenance import all_records

SCENE_FLAGS = 0x10
ACTOR_FLAGS = 0x2A
ACTION_FLAGS = 0x8000
PHASE_TIMER = 200
ALIAS_OPTIONAL_REUSE = 0x2 | 0x8
EMPTY_SCHR = bytes(18) + b'\x01\x00'  # the empty script header the CK writes for every scene begin/end script
SCENE_TOPIC_DATA = b'\x00\x02\x0e\x00'
SPEAKER, PARTNER = 'speaker', 'partner'
GET_IS_SEX = 70
SEX_PARAM = {'male': 0, 'female': 1}
KEYS = {'scene_editor_id', 'quest_editor_id', 'speaker', 'partner', 'lines', 'trigger_lines', 'trigger_line',
        'dialogue_quest_editor_id', 'partner_reference', 'distance', 'hired', 'random_percent', 'once',
        'conditions'}
FIGHT_KEYS = {'prompt', 'timeout', 'stop_on_player', 'unarmed', 'bleedout'}
FIGHT_BLEEDOUT = 5
FIGHT_TIMEOUT = 90


def check_spec(spec, fight=False):
    """Error strings for a chat spec (or a staged-fight spec); empty when it is usable."""
    errors = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    errors += [f"unknown key {k!r}" for k in spec if k not in KEYS | (FIGHT_KEYS if fight else set())]
    if fight:
        for k in ('timeout',):
            v = spec.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 1):
                errors.append(f"{k} must be a number of seconds, at least 1")
        if spec.get('prompt') is not None and not (isinstance(spec['prompt'], str) and spec['prompt'].strip()):
            errors.append("prompt must be a non-empty string")
    for k in ('scene_editor_id', 'quest_editor_id'):
        if not re.fullmatch(r'[A-Za-z0-9_]+', str(spec.get(k, ''))):
            errors.append(f"{k} is required (letters, digits and underscores)")
    for k in ('speaker', 'partner'):
        if not spec.get(k):
            errors.append(f"{k} is required (an NPC editor ID, name or form ID)")
    lines = spec.get('lines')
    if fight and lines is None:
        lines = []
    if not (isinstance(lines, list) and (lines or fight)):
        errors.append("lines must be a non-empty list of {who: 'speaker'|'partner', text}")
    else:
        for i, ln in enumerate(lines):
            pt = ln.get('player_text') if isinstance(ln, dict) else None
            has_text = isinstance(ln, dict) and isinstance(ln.get('text'), str) and ln['text'].strip()
            variants_ok = (isinstance(pt, dict) and set(pt) == {'male', 'female'}
                           and all(isinstance(v, str) and v.strip() for v in pt.values()))
            if not (isinstance(ln, dict) and ln.get('who') in (SPEAKER, PARTNER) and (bool(has_text) != bool(pt))
                    and (pt is None or variants_ok)):
                errors.append(f"lines[{i}]: needs who ('speaker' or 'partner') and either text or player_text "
                              "{male, female} (for lines that refer to the player with a gendered word)")
    trig = spec.get('trigger_lines') or ([spec['trigger_line']] if spec.get('trigger_line') else [])
    if isinstance(trig, str):
        trig = [trig]
    if not (trig and all(isinstance(t, str) and t.strip() for t in trig)):
        errors.append("trigger_line (or trigger_lines) is required: what the follower says before the chat starts")
    texts = list(trig)
    for ln in lines or []:
        if isinstance(ln, dict):
            texts += [ln.get('text', '')] + list((ln.get('player_text') or {}).values() if isinstance(
                ln.get('player_text'), dict) else [])
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
                      'value': spec.get('distance', 900), 'run_on': 'player'})
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
    def script_stub():
        rec.add_subrecord('SCHR', EMPTY_SCHR)
        rec.add_subrecord('QNAM', fid_bytes(quest_fid))

    for _ in actions:
        rec.add_subrecord('HNAM', b'')
        rec.add_subrecord('NAM0', b'\0')
        for _ in range(2):
            rec.add_subrecord('NEXT', b'')
            script_stub()
        rec.add_subrecord('WNAM', struct.pack('<I', PHASE_TIMER))
        rec.add_subrecord('HNAM', b'')
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
    script_stub()
    rec.add_subrecord('NEXT', b'')
    script_stub()
    rec.add_subrecord('PNAM', fid_bytes(quest_fid))
    rec.add_subrecord('INAM', struct.pack('<I', len(actions)))
    rec.add_subrecord('VNAM', struct.pack('<4I', 3, 3, 3, 3))
    return rec


def scene_lines(b, plugin, new_id, quest_fid, sid, lines, local, alias):
    """Scene-line DIALs and INFOs for lines [{who, text|player_text}]; returns the actions for scene_record."""
    top = next((g for g in plugin.groups if g.group_type == 0 and g.label == 'DIAL'), None)
    if top is None:
        top = GroupRecord(0, 'DIAL')
        plugin.groups.append(top)
    actions = []
    for i, ln in enumerate(lines, 1):
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
        variants = ([(None, ln['text'])] if ln.get('text') else
                    [(sex, ln['player_text'][sex]) for sex in ('male', 'female')])
        for sex, text in variants:
            info = Record('INFO', FormID(b.own(new_id())), 0)
            info.add_subrecord('ENAM', struct.pack('<HH', 0, 0))
            info.add_subrecord('CNAM', b'\x00')
            info.add_subrecord('TRDT', struct.pack('<II4xB3xIB3x', 0, 50, 1, 0, 1))
            info.add_subrecord('NAM1', z(text))
            info.add_subrecord('NAM2', b'\x00')
            info.add_subrecord('NAM3', b'\x00')
            info.add_subrecord('CTDA', struct.pack('<B3xfH2xIIIIi', 0, 1.0, GET_IS_ID, local[who], 0, 0, 0, -1))
            if sex:
                info.add_subrecord('CTDA', struct.pack('<B3xfH2xIIIIi', 0, 1.0, GET_IS_SEX, SEX_PARAM[sex], 0, 1, 0,
                                                       -1))
            group.records.append(info)
            b.register(plugin, info)
        actions.append((alias[who], dial.form_id.value, alias[other]))
    return actions


def unarm_code(var, tag):
    """Papyrus that moves an actor's weapons into a hidden empty chest and unequips hand spells, and the matching restore.

    Unequipping is not enough: the combat AI re-equips any weapon still carried, so the weapons leave the inventory."""
    off = (f"Weapon {tag}r = {var}.GetEquippedWeapon(false)\nWeapon {tag}l = {var}.GetEquippedWeapon(true)\n"
           f"Spell {tag}sr = {var}.GetEquippedSpell(1)\nSpell {tag}sl = {var}.GetEquippedSpell(0)\n"
           f"If {tag}sr\n{var}.UnequipSpell({tag}sr, 1)\nEndIf\n"
           f"If {tag}sl\n{var}.UnequipSpell({tag}sl, 0)\nEndIf\n"
           f"ObjectReference {tag}b = {var}.PlaceAtMe(Game.GetForm(0xF8478), 1, false, true)\nForm[] {tag}f = new Form[32]\nint[] {tag}c = new int[32]\nint {tag}n = 0\n"
           f"int {tag}i = {var}.GetNumItems()\nWhile {tag}i > 0\n{tag}i -= 1\n"
           f"Form {tag}x = {var}.GetNthForm({tag}i)\n"
           f"If {tag}x as Weapon && {tag}n < 32\n{tag}f[{tag}n] = {tag}x\n{tag}c[{tag}n] = {var}.GetItemCount({tag}x)\n"
           f"{tag}n += 1\nEndIf\nEndWhile\n"
           f"{tag}i = 0\nWhile {tag}i < {tag}n\n{var}.RemoveItem({tag}f[{tag}i], {tag}c[{tag}i], true, {tag}b)\n"
           f"{tag}i += 1\nEndWhile")
    # RemoveAllItems also returns any weapon the guard below took off the actor mid-fight
    on = (f"{tag}b.RemoveAllItems({var}, false, true)\n{tag}b.Delete()\n"
          f"If {tag}r\n{var}.EquipItem({tag}r, false, true)\nEndIf\n"
          f"If {tag}l\n{var}.EquipItem({tag}l, false, true)\nEndIf\n"
          f"If {tag}sr\n{var}.EquipSpell({tag}sr, 1)\nEndIf\n"
          f"If {tag}sl\n{var}.EquipSpell({tag}sl, 0)\nEndIf")
    # the AI can still pick up and draw a loose weapon, so the fight loop strips anything drawn
    guard = (f"Weapon {tag}g = {var}.GetEquippedWeapon(false)\n"
             f"If {tag}g\n{var}.RemoveItem({tag}g, {var}.GetItemCount({tag}g), true, {tag}b)\nEndIf\n"
             f"{tag}g = {var}.GetEquippedWeapon(true)\n"
             f"If {tag}g\n{var}.RemoveItem({tag}g, {var}.GetItemCount({tag}g), true, {tag}b)\nEndIf")
    return off, on, guard

def fight_code(a, b_alias, timeout, scene_alias_prop, stop_on_player=True, unarmed=True, bleedout=5):
    """Papyrus for a non-lethal fight between two quest aliases, ending in the after-fight scene (if any)."""
    (off_a, on_a, g_a), (off_b, on_b, g_b) = unarm_code('fa', 'wa'), unarm_code('fb', 'wb')
    arm_off = f"{off_a}\n{off_b}" if unarmed else ''
    arm_on = f"{on_a}\n{on_b}" if unarmed else ''
    arm_guard = f"{g_a}\n{g_b}" if unarmed else ''
    code = f"""
Quest fq = FightQuest as Quest
Actor fa = (fq.GetAlias({a}) as ReferenceAlias).GetActorReference()
Actor fb = (fq.GetAlias({b_alias}) as ReferenceAlias).GetActorReference()
ActorBase ba = fa.GetActorBase()
ActorBase bb = fb.GetActorBase()
bool ea = ba.IsEssential()
bool eb = bb.IsEssential()
ba.SetEssential(true)
bb.SetEssential(true)
fa.SetNoBleedoutRecovery(true)
fb.SetNoBleedoutRecovery(true)
Actor pl = Game.GetPlayer()
{arm_off}
fa.StartCombat(fb)
fb.StartCombat(fa)
bool joined = false
int waited = 0
While waited < {timeout} && !fa.IsBleedingOut() && !fb.IsBleedingOut() && !joined
Utility.Wait(0.5)
waited += 1
{arm_guard}
{'joined = fa.GetCombatTarget() == pl || fb.GetCombatTarget() == pl' if stop_on_player else ''}
EndWhile
fa.StopCombat()
fb.StopCombat()
fa.StopCombatAlarm()
fb.StopCombatAlarm()
int held = 0
While (fa.IsBleedingOut() || fb.IsBleedingOut()) && held < {bleedout * 2}
Utility.Wait(0.5)
held += 1
EndWhile
fa.SetNoBleedoutRecovery(false)
fb.SetNoBleedoutRecovery(false)
int getUp = 0
While (fa.IsBleedingOut() || fb.IsBleedingOut()) && getUp < 120
Utility.Wait(0.5)
getUp += 1
EndWhile
fa.ResetHealthAndLimbs()
fb.ResetHealthAndLimbs()
ba.SetEssential(ea)
bb.SetEssential(eb)
{arm_on}"""
    if scene_alias_prop:
        code += f"\nIf !joined\nScene after = {scene_alias_prop} as Scene\nafter.Start()\nEndIf"
    return code


def remove_chat(path, scene, data_dir=None):
    """Remove a chat or staged fight built by add_npc_chat/staged_fight as one unit.

    scene is the SCEN editor ID: the scene, its line topics, the trigger topic whose fragment starts it (with its
    branch) and the compiled fragment scripts all go. A trigger topic's editor ID also works, for a staged fight
    with no after-fight scene, and removes just that topic. Installed copies of the scripts in data_dir/Scripts
    are deleted too."""
    path = Path(path)
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    q = scene.strip().lower()
    recs = [r for r in all_records(plugin) if hi(r.form_id.value) == own]
    hit = next((r for r in recs if (r.editor_id or '').lower() == q and r.signature in ('SCEN', 'DIAL')), None)
    if hit is None:
        raise ValueError(f"no scene or topic {scene!r} in {path.name}")
    dials = {}
    top = next((g for g in plugin.groups if g.group_type == 0 and g.label == 'DIAL'), None)
    for g in (top.records if top else []):
        if isinstance(g, GroupRecord) and g.group_type == 7:
            dials[g.label] = g
    by_id = {r.form_id.value: r for r in recs}
    topics = []
    if hit.signature == 'DIAL':
        topics.append(hit)
    else:
        line_name = re.compile(rf"{re.escape(hit.editor_id)}Line\d+", re.I)
        topics += [r for r in recs if r.signature == 'DIAL' and line_name.fullmatch(r.editor_id or '')]
        scene_ref = struct.pack('<I', hit.form_id.value)
        for fid, g in dials.items():
            if fid in by_id and by_id[fid] not in topics and any(
                    i.signature == 'INFO' and i.get_subrecord('VMAD') is not None
                    and scene_ref in bytes(i.get_subrecord('VMAD').data) for i in g.records):
                topics.append(by_id[fid])
    doomed, scripts = [hit] if hit.signature == 'SCEN' else [], []
    for d in topics:
        for i in (dials[d.form_id.value].records if d.form_id.value in dials else []):
            doomed.append(i)
            if i.get_subrecord('VMAD') is not None:
                scripts.append(script_name(path.name, 'TIF', i.form_id.value & 0xFFFFFF))
        doomed.append(d)
        doomed += [r for r in recs if r.signature == 'DLBR' and r.get_subrecord('SNAM') is not None
                   and struct.unpack('<I', bytes(r.get_subrecord('SNAM').data))[0] == d.form_id.value]
    removed = [f"{r.signature} {r.editor_id or ''} {r.form_id.value:08X}".replace('  ', ' ') for r in doomed]
    for r in doomed:
        plugin.remove_record(r)
    gone = {r.form_id.value for r in doomed}
    dangling = sorted({f"{r.editor_id or r.signature} {s.signature}" for r in all_records(plugin)
                       for s in r.subrecords if len(s.data) == 4 and struct.unpack('<I', bytes(s.data))[0] in gone})
    plugin.header.version = 1.7
    plugin.modified = True
    plugin.save(str(path))
    deleted = []
    for folder in [path.parent / 'Scripts'] + ([Path(data_dir) / 'Scripts'] if data_dir else []):
        for name in scripts:
            f = folder / f"{name}.pex"
            if f.is_file():
                f.unlink()
                deleted.append(str(f))
    return {'removed': removed, 'scripts_deleted': deleted, 'still_referenced_by': dangling}


def add_npc_chat(b, spec, target_plugin, fight=False):
    """Add a scene in which a follower and another NPC talk, started by a trigger line the follower says.

    Creates (or extends) the chat quest with an alias per NPC, one scene-line topic per line, the SCEN, and a
    trigger idle line on the follower's dialogue quest whose fragment starts the scene.

    With fight=True the trigger first runs a non-lethal brawl between the two NPCs and then plays the (optional)
    lines as the after-fight scene.
    """
    errors = check_spec(spec, fight)
    if errors:
        raise ValueError("; ".join(errors))
    if fight:
        spec = {'hired': False, 'random_percent': 0 if spec.get('prompt') else 10, **spec}
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
    cond_spec = {'conditions': list(spec.get('conditions') or []) if fight and spec.get('prompt')
                 else trigger_conditions(spec)}
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
        quest.add_subrecord('DNAM', struct.pack('<HBBII', 0x11, 60, 0, 0, 0))
        quest.add_subrecord('NEXT', b'')
        quest.add_subrecord('ANAM', struct.pack('<I', 0))
        plugin.add_record(quest)
        made.append(b.describe(quest))
    alias = {role: ensure_alias(quest, local[role], npcs[role][1]) for role in (SPEAKER, PARTNER)}
    quest_fid = quest.form_id.value

    actions = scene_lines(b, plugin, new_id, quest_fid, sid, spec.get('lines') or [], local, alias)
    scene = None
    if actions:
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
    if fight:
        code = fight_code(alias[SPEAKER], alias[PARTNER],         spec.get('timeout', FIGHT_TIMEOUT) * 2,
                                  'AfterScene' if scene else None, spec.get('stop_on_player', True), spec.get('unarmed', True),
                          spec.get('bleedout', FIGHT_BLEEDOUT))
        props = [('FightQuest', -quest_fid)] + ([('AfterScene', -scene.form_id.value)] if scene else [])
        opts['goodbye'] = True
    else:
        code = "Scene chat = ChatScene as Scene\nchat.Start()"
        props = [('ChatScene', -scene.form_id.value)]
    prompt = spec.get('prompt') if fight else None
    topic = ('topic' if prompt else 'idle', prompt, [trig[0]], (code, props), conds, opts)
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
