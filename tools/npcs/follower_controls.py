"""Follower controls: faction-backed settings (follow distance, fighting style) with the topics that change them and
the packages that act on them."""
import re
import struct

from esplib import Plugin
from esplib.record import Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, resolve_target_path
from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.papyrus.papyrus_build import compile_script, controller_source, quest_script_vmad, summon_spell_source
from tools.quests.chat_builder import ensure_alias
from tools.records.ai_records import (DONOR_FOLLOW_PACKAGE, make_formlist, clone_package, ctda, make_faction, _check_new,
                                      _next_id)

GET_ACTOR_VALUE, GET_IN_FACTION, GET_FACTION_RANK = 14, 71, 73
WAITING_FOR_PLAYER = 0x5F
CONTROL_PRIORITY = 70  # above the vanilla follower quest (50) so these packages win while their conditions hold
KEYS = {'npc', 'prefix', 'dialogue_quest_editor_id', 'follow_distance', 'fighting_style', 'combat_overrides',
        'horse_sharing', 'horse_radar', 'horse_gift', 'left_behind', 'map_marker', 'summon', 'summon_lines',
        'caught_up_lines', 'text'}
SUMMON_LINES = ["Well, that was sudden.", "I'm here. You might warn a person.", "One moment I was there, the next, here."]
CAUGHT_UP_LINES = ["There you are. I'd lost you.", "You walk fast. I had to run to catch up.",
                   "I lost the trail for a bit. I'm back now."]
GET_STAGE = 58
MARKER_OBJECTIVE, MARKER_ON_STAGE, MARKER_OFF_STAGE = 10, 10, 20
QUEST_TYPE_MISC = 6
DONOR_COMBAT_OVERRIDE = 'PlayerFollowerCombatOverridePackage'
# fighting-style rank -> (title, leash radius around the player during combat); rank 0 keeps vanilla behaviour
LEASHES = {1: ('Cautious', 300), 2: ('Reckless', 2500)}

# rank -> (title, near radius, far radius); rank 0 is the vanilla follow behaviour and gets no package
DISTANCES = [('Normal', None, None), ('Close', 128.0, 192.0), ('Far', 512.0, 768.0)]
# rank -> (title, Aggression, Confidence)
STYLES = [('Steady', 1, 3), ('Cautious', 1, 1), ('Reckless', 2, 4)]

TEXT = {
    'distance_menu': ("Let's talk about how you follow me.", "How would you like me to do it?"),
    'distance_1': ("Stay a bit closer.", "Right at your heel, then."),
    'distance_2': ("Give me some room.", "I'll keep my distance."),
    'distance_0': ("Follow at a normal distance.", "Back to how it was."),
    'style_menu': ("Let's talk about how you fight.", "What do you have in mind?"),
    'style_1': ("Be careful in a fight.", "I'll watch my step."),
    'style_0': ("Fight the way you normally do.", "As you say."),
    'style_2': ("Don't hold back in a fight.", "No mercy, then."),
    'exit': ("Never mind.", "As you wish."),
    'horse_gift': ("Take my horse. She's yours now.", "A fine gift. I'll look after her."),
    'horse_reclaim': ("I'd like my horse back.", "Of course. She was only ever on loan."),
    'horse_find': ("Keep an eye out for a horse you could ride.", "I'll see what's grazing nearby."),
    'horse_share': ("Take a horse and ride along with me.", "A horse, then. I'll find one."),
    'horse_walk': ("Walk with me instead of riding.", "On my own two feet, then."),
    'left_behind_ask': ("Where did you get to?", "I lost you for a while. I found you again, though."),
    'marker_on': ("I want to be able to find you on my map.", "Then I'll keep myself marked where you can see."),
    'marker_off': ("You don't need to show up on my map any more.", "Understood. I'll keep to myself."),
}


def check_spec(spec):
    """Error strings for a follower-controls spec; empty when usable."""
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    errors = [f"unknown key {k!r}" for k in spec if k not in KEYS]
    if not spec.get('npc'):
        errors.append("npc is required (the follower, defined in the target plugin)")
    for k in ('follow_distance', 'fighting_style', 'combat_overrides', 'horse_sharing', 'horse_radar', 'horse_gift',
              'left_behind', 'map_marker', 'summon'):
        if k in spec and not isinstance(spec[k], bool):
            errors.append(f"{k} must be true or false")
    for k in ('summon_lines', 'caught_up_lines'):
        v = spec.get(k)
        if v is None:
            continue
        if not (isinstance(v, list) and v and all(isinstance(s, str) and s.strip() for s in v)):
            errors.append(f"{k} must be a non-empty list of lines")
            continue
        try:
            [z(s) for s in v]
        except UnicodeEncodeError:
            errors.append(f"{k}: characters outside Windows-1252")
    text = spec.get('text') or {}
    if not isinstance(text, dict):
        errors.append("text must be an object")
        return errors
    for key, val in text.items():
        if key not in TEXT:
            errors.append(f"text.{key}: unknown key; use {sorted(TEXT)}")
        elif not (isinstance(val, list) and len(val) == 2 and all(isinstance(s, str) and s.strip() for s in val)):
            errors.append(f"text.{key}: give [prompt, reply]")
        else:
            try:
                [z(s) for s in val]
            except UnicodeEncodeError:
                errors.append(f"text.{key}: characters outside Windows-1252")
    return errors


def _entry(text, key, conds, parent=None, script=None, goodbye=False):
    prompt, reply = text[key]
    opts = {}
    if parent is not None:
        opts['child_of'] = parent
    if goodbye:
        opts['goodbye'] = True
    return ('topic', prompt, [reply], script, conds) + ((opts,) if opts else ())


def settings_topics(text, hired, faction, kind, count, base, extra_code=None):
    """Topic tuples for one setting: a menu topic (at index `base` of the dialogue list), one child per rank and an
    exit child. A child is hidden while its rank is the current one; its script sets the rank, applies any
    `extra_code[rank]`, then re-evaluates the actor's packages.
    """
    topics = [_entry(text, f"{kind}_menu", [hired])]
    for rank in range(count):
        code = f"akSpeaker.SetFactionRank(Control as Faction, {rank})\n"
        code += (extra_code or {}).get(rank, '') + "akSpeaker.EvaluatePackage()"
        topics.append(_entry(text, f"{kind}_{rank}", [hired, (GET_FACTION_RANK, -faction, 1, float(rank), 0)],
                             parent=base, script=(code, [('Control', -faction)]), goodbye=True))
    topics.append(_entry(text, 'exit', [hired], parent=base, goodbye=True))
    return topics

def add_follower_controls(b, spec, target_plugin):
    """Give a follower in target_plugin adjustable behaviour.

    Creates a hidden faction per setting (the NPC is a member at rank 0), dialogue topics that set the rank, and,
    for follow distance, a controller quest whose alias holds Follow packages for the non-default ranks.
    """
    errors = check_spec(spec)
    if errors:
        raise ValueError("; ".join(errors))
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; dialogue text cannot be added inline")
    npc = target.find_own(spec['npc'], ['NPC_'])
    if npc is None:
        raise ValueError(f"{spec['npc']!r} must be an NPC_ record in {path.name}; create it there first")
    prefix = spec.get('prefix') or re.sub(r'\W', '', npc.editor_id or 'Follower')
    want_distance, want_style = spec.get('follow_distance', True), spec.get('fighting_style', True)
    want_over, want_horse = spec.get('combat_overrides', False), spec.get('horse_sharing', False)
    want_radar, want_gift = spec.get('horse_radar', False), spec.get('horse_gift', False)
    want_more = want_radar or want_gift
    want_left, want_marker = spec.get('left_behind', False), spec.get('map_marker', False)
    want_summon = spec.get('summon', False)
    if not (want_distance or want_style or want_over or want_horse or want_more or want_left or want_marker
            or want_summon):
        raise ValueError("nothing to do: every option is false")
    horse_existing = target.find_own(f"{prefix}ShareHorseFaction", ['FACT'])
    left_existing = target.find_own(f"{prefix}LeftBehindFaction", ['FACT'])
    if want_summon and not (want_left or left_existing):
        raise ValueError("summon needs the left-behind faction and script; set left_behind true or run it first")
    if want_more and not (want_horse or horse_existing):
        raise ValueError("horse_radar and horse_gift need the horse-sharing faction and script; set horse_sharing true or run it first")
    style_existing = target.find_own(f"{prefix}FightingStyleFaction", ['FACT'])
    if want_over and not (want_style or style_existing):
        raise ValueError("combat_overrides needs the fighting-style faction; set fighting_style true or run it first")
    text = {**TEXT, **{k: tuple(v) for k, v in (spec.get('text') or {}).items()}}
    names = {'distance': f"{prefix}FollowDistanceFaction", 'style': f"{prefix}FightingStyleFaction",
             'quest': f"{prefix}FollowerControl", 'horse': f"{prefix}ShareHorseFaction",
             'script': f"{prefix}ControlScript", 'left': f"{prefix}LeftBehindFaction"}
    for key in ([k for k, on in (('distance', want_distance), ('style', want_style), ('horse', want_horse),
                                 ('left', want_left)) if on]):
        _check_new(b, plugin, names[key])

    p = Plan()
    hired_fid, _ = b.resolver.lookup('CurrentHireling', ['FACT'])
    p.abs_ids.add(hired_fid)
    donor_fid, donor = b.resolver.lookup(DONOR_FOLLOW_PACKAGE, ['PACK'])
    if want_distance:
        p.abs_ids.add(donor_fid)
    if want_over:
        over_fid, over_donor = b.resolver.lookup(DONOR_COMBAT_OVERRIDE, ['PACK'])
        p.abs_ids.add(over_fid)
    b.target, b.plugin = target, plugin
    b.add_masters(plugin, p)
    b.next_id = _next_id(b, plugin)
    made = []

    def new_id():
        v = b.next_id
        b.next_id += 1
        return v

    hired = (GET_IN_FACTION, hired_fid, 0, 1.0, 0)
    hired_local = b.local(hired_fid)
    npc_fid = npc.form_id.value
    factions, topics = {}, []

    def add_member(fid):
        subs = npc.subrecords
        if any(s.signature == 'SNAM' and struct.unpack_from('<I', bytes(s.data))[0] == fid for s in subs):
            return
        at = max((i for i, s in enumerate(subs) if s.signature == 'SNAM'),
                 default=next((i for i, s in enumerate(subs) if s.signature == 'ACBS'), len(subs) - 1))
        npc.add_subrecord('SNAM', struct.pack('<Ib3x', fid, 0))
        subs.insert(at + 1, subs.pop())

    if want_distance:
        fac = make_faction(b.own(new_id()), names['distance'], "Preferred follow distance",
                           [f"{t}" for t, _, _ in DISTANCES])
        plugin.add_record(fac)
        made.append(b.describe(fac))
        factions['distance'] = fac.form_id.value
        add_member(fac.form_id.value)
    if want_style:
        fac = make_faction(b.own(new_id()), names['style'], "Preferred fighting style", [t for t, _, _ in STYLES])
        plugin.add_record(fac)
        made.append(b.describe(fac))
        factions['style'] = fac.form_id.value
        add_member(fac.form_id.value)

    if want_over and 'style' not in factions:
        factions['style'] = style_existing.form_id.value
    if want_horse:
        fac = make_faction(b.own(new_id()), names['horse'], "Rides a horse with the player", ['Walks', 'Rides'])
        plugin.add_record(fac)
        made.append(b.describe(fac))
        factions['horse'] = fac.form_id.value
        add_member(fac.form_id.value)
    elif horse_existing:
        factions['horse'] = horse_existing.form_id.value
    if want_left:
        fac = make_faction(b.own(new_id()), names['left'], "Rejoined the player after being left behind",
                           ['Fine', 'Caught up'])
        plugin.add_record(fac)
        made.append(b.describe(fac))
        factions['left'] = fac.form_id.value
        add_member(fac.form_id.value)
    elif left_existing:
        factions['left'] = left_existing.form_id.value
    # controller quest with an alias for the follower; holds the distance packages, combat overrides and script
    if want_distance or want_over or want_horse or want_more or want_left or want_marker or want_summon:
        quest = target.find_own(names['quest'], ['QUST'])
        if quest is None:
            quest = Record('QUST', FormID(b.own(new_id())), 0)
            quest.add_subrecord('EDID', z(names['quest']))
            quest.add_subrecord('DNAM', struct.pack('<HBBII', 0x1, CONTROL_PRIORITY, 0, 0, 0))
            quest.add_subrecord('NEXT', b'')
            quest.add_subrecord('ANAM', struct.pack('<I', 0))
            plugin.add_record(quest)
            made.append(b.describe(quest))
        alias = ensure_alias(quest, npc_fid, 'Follower')
        pk_ids = []
        for rank, (title, near, far) in enumerate(DISTANCES if want_distance else []):
            if near is None:
                continue
            conds = [ctda(GET_FACTION_RANK, factions['distance'], 0, float(rank)),
                     ctda(GET_IN_FACTION, hired_local, 0, 1.0),
                     ctda(GET_ACTOR_VALUE, WAITING_FOR_PLAYER, 0, 0.0)]
            pk = clone_package(donor, b.own(new_id()), f"{prefix}Follow{title}Package", conds, [near, far])
            plugin.add_record(pk)
            made.append(b.describe(pk))
            pk_ids.append(pk.form_id.value)
        _attach_packages(quest, alias, pk_ids)
        if want_over:
            over_ids = []
            for rank, (title, leash) in LEASHES.items():
                conds = [ctda(GET_FACTION_RANK, factions['style'], 0, float(rank)),
                         ctda(GET_IN_FACTION, hired_local, 0, 1.0)]
                pk = clone_package(over_donor, b.own(new_id()), f"{prefix}Combat{title}Package", conds, leash=leash)
                plugin.add_record(pk)
                made.append(b.describe(pk))
                over_ids.append(pk.form_id.value)
            flst = make_formlist(b.own(new_id()), f"{prefix}CombatOverrideList", over_ids)
            plugin.add_record(flst)
            made.append(b.describe(flst))
            _attach_override_list(quest, alias, flst.form_id.value)
        if want_horse or want_more or want_left or want_summon:
            has_horse, has_left = 'horse' in factions, 'left' in factions
            objects = [('HiredFaction', hired_local)]
            aliases = [('Follower', alias)]
            if has_horse:
                objects.append(('ShareFaction', b.local(factions['horse'])))
                aliases.append(('Steed', ensure_empty_alias(quest, 'Steed')))
            if has_left:
                objects.append(('LeftBehindFaction', b.local(factions['left'])))
            sname = names['script']
            compile_script(sname, controller_source(sname, has_horse, has_left), plugin.file_path.parent / 'Scripts')
            vmad = quest_script_vmad(quest, sname, objects, aliases)
            quest.remove_subrecords('VMAD')
            quest.add_subrecord('VMAD', vmad)
            quest.subrecords.insert(1, quest.subrecords.pop())
            made.append({'type': 'script', 'editor_id': sname})
        if want_marker:
            who = npc.get_subrecord('FULL')
            who = bytes(who.data).rstrip(b'\0').decode('cp1252') if who is not None else prefix
            add_map_marker(quest, alias, who)
            made.append({'type': 'objective', 'editor_id': names['quest'], 'index': MARKER_OBJECTIVE})

    if want_left:
        topics.append(_entry(text, 'left_behind_ask', [hired, (GET_FACTION_RANK, -factions['left'], 0, 1.0, 0)],
                             script=("akSpeaker.SetFactionRank(Control as Faction, 0)", [('Control', -factions['left'])])))
    if want_marker:
        marker_props = [('Control', -quest.form_id.value)]
        on_code = (f"Quest q = Control as Quest\nq.SetStage({MARKER_ON_STAGE})\n"
                   f"q.SetObjectiveDisplayed({MARKER_OBJECTIVE}, True, True)")
        off_code = (f"Quest q = Control as Quest\nq.SetObjectiveDisplayed({MARKER_OBJECTIVE}, False)\n"
                    f"q.SetStage({MARKER_OFF_STAGE})")
        stage = lambda op, v: (GET_STAGE, -quest.form_id.value, op, float(v), 0)
        topics.append(_entry(text, 'marker_on', [hired, stage(1, MARKER_ON_STAGE)], script=(on_code, marker_props)))
        topics.append(_entry(text, 'marker_off', [hired, stage(0, MARKER_ON_STAGE)], script=(off_code, marker_props)))
    if want_horse or want_more:
        call = f"(Control as {names['script']})"
        props = [('Control', -quest.form_id.value)]
        share = (GET_FACTION_RANK, -factions['horse'], 0, 0.0, 0)
        riding = (GET_FACTION_RANK, -factions['horse'], 0, 1.0, 0)
        if want_gift:
            topics.append(_entry(text, 'horse_gift', [hired, share], script=(f"{call}.GiftHorse(akSpeaker)", props)))
            topics.append(_entry(text, 'horse_reclaim', [hired, riding],
                                 script=(f"{call}.ReclaimHorse(akSpeaker)", props)))
        if want_radar:
            topics.append(_entry(text, 'horse_find', [hired, share], script=(f"{call}.FindHorse(akSpeaker)", props)))
        if want_horse:
            topics.append(_entry(text, 'horse_share', [hired, share], script=(f"{call}.ShareHorse(akSpeaker)", props)))
            topics.append(_entry(text, 'horse_walk', [hired, riding], script=(f"{call}.StopSharing(akSpeaker)", props)))

    if want_distance:
        topics += settings_topics(text, hired, factions['distance'], 'distance', len(DISTANCES), len(topics))
    if want_style:
        extra = {i: f"akSpeaker.SetActorValue(\"Aggression\", {ag})\nakSpeaker.SetActorValue(\"Confidence\", {co})\n"
                 for i, (_, ag, co) in enumerate(STYLES)}
        topics += settings_topics(text, hired, factions['style'], 'style', len(STYLES), len(topics), extra)
    p.topics = topics
    dq = spec.get('dialogue_quest_editor_id') or f"{prefix}DialogueQuest"
    reactions = []
    summon_spell = None
    if want_summon:
        # engine-spoken reactions: the controller script sets the left-behind rank, then calls Say on the topic
        for rank, key, default in ((2, 'summon_lines', SUMMON_LINES), (3, 'caught_up_lines', CAUGHT_UP_LINES)):
            lines = spec.get(key) or default
            reactions.append(('idle', None, [lines[0]], None,
                              [hired, (GET_FACTION_RANK, -factions['left'], 0, float(rank), 0)],
                              {'answers': [[x] for x in lines], 'pick': 'random'}))
        reaction_base = len(topics)
        topics += reactions
    first_index = 1 + sum(1 for r in plugin.records if r.signature == 'DIAL' and (r.editor_id or '').startswith(dq))
    if topics:
        b.make_dialogue(plugin, {'editor_id': prefix, 'quest_editor_id': dq}, p, new_id, made, npc_fid)
    if want_summon:
        def reaction_topic(offset):
            edid = f"{dq}Idle{first_index + reaction_base + offset}"
            return next(r for r in plugin.records if r.signature == 'DIAL' and r.editor_id == edid)
        vmad = quest_script_vmad(quest, names['script'],
                                 [('SummonTopic', b.local(reaction_topic(0).form_id.value)),
                                  ('CaughtUpTopic', b.local(reaction_topic(1).form_id.value))], [])
        quest.remove_subrecords('VMAD')
        quest.add_subrecord('VMAD', vmad)
        quest.subrecords.insert(1, quest.subrecords.pop())
        qid_obj = quest.form_id.value & 0xFFFFFF
        spell_script = f"{prefix}SummonEffectScript"
        summon_spell = {'script_name': spell_script, 'plugin': path.name,
                        'source': summon_spell_source(spell_script, names['script'], path.name, qid_obj)}

    plugin.header.next_object_id = b.next_id
    target.save()
    check = Plugin.load(str(path))
    return {'path': str(path), 'factions': {k: names[k] for k in factions}, 'dialogue_quest': dq,
            'controller_quest': names['quest'] if (want_distance or want_over or want_horse or want_more or want_left
                                                   or want_marker or want_summon) else None,
            'summon_spell': summon_spell,
            'records': made,
            'masters': list(plugin.header.masters), 'reloaded_records': len(check.records)}


def add_map_marker(quest, alias, name):
    """Give the controller quest a journal entry and an objective that targets the follower alias, so showing the
    objective puts the follower on the map and compass. Stage MARKER_ON_STAGE/MARKER_OFF_STAGE only carry log text."""
    subs = quest.subrecords
    if any(s.signature == 'QOBJ' and struct.unpack('<H', bytes(s.data))[0] == MARKER_OBJECTIVE for s in subs):
        raise ValueError(f"{quest.editor_id} already has objective {MARKER_OBJECTIVE}; the map marker is already set up")
    dnam = next(s for s in subs if s.signature == 'DNAM')
    flags, prio, ver, unk, _ = struct.unpack('<HBBII', bytes(dnam.data)[:12])
    dnam.data = struct.pack('<HBBII', flags, prio, ver, unk, QUEST_TYPE_MISC)

    def put(items, before=None, after=None):
        n = len(subs)
        for sig, data in items:
            quest.add_subrecord(sig, data)
        new = subs[n:]
        del subs[n:]
        if before:
            at = next(i for i, s in enumerate(subs) if s.signature == before)
        else:
            at = max(i for i, s in enumerate(subs) if s.signature in after) + 1
        subs[at:at] = new

    if not any(s.signature == 'FULL' for s in subs):
        put([('FULL', z(f"Where is {name}?"))], after=('EDID', 'VMAD'))
    put([('INDX', struct.pack('<HBB', MARKER_ON_STAGE, 0, 0)), ('QSDT', b'\0'),
         ('CNAM', z(f"{name} is marked on my map, so I can always find them.")),
         ('INDX', struct.pack('<HBB', MARKER_OFF_STAGE, 0, 0)), ('QSDT', b'\0'),
         ('CNAM', z(f"{name} is no longer marked on my map."))], before='ANAM')
    put([('QOBJ', struct.pack('<H', MARKER_OBJECTIVE)), ('FNAM', struct.pack('<I', 0)), ('NNAM', z(name)),
         ('QSTA', struct.pack('<II', alias, 0))], before='ANAM')


def ensure_empty_alias(quest, name):
    """ID of the optional, unfilled reference alias `name` in quest (filled from script), adding it when missing."""
    subs, ids, cur = quest.subrecords, [], None
    for s in subs:
        if s.signature == 'ALST':
            cur = struct.unpack('<I', bytes(s.data))[0]
            ids.append(cur)
        elif s.signature == 'ALID' and bytes(s.data).rstrip(b'\0').decode('cp1252') == name:
            return cur
    new = max(ids, default=-1) + 1
    next(s for s in subs if s.signature == 'ANAM').data = struct.pack('<I', new + 1)
    quest.add_subrecord('ALST', struct.pack('<I', new))
    quest.add_subrecord('ALID', z(name))
    quest.add_subrecord('FNAM', struct.pack('<I', 0x2))
    quest.add_subrecord('VTCK', fid_bytes(0))
    quest.add_subrecord('ALED', b'')
    return new


def _attach_override_list(quest, alias, flst_fid):
    """Set the alias's combat override list (ECOR), placed before its ALFC/ALPC/VTCK entries."""
    subs = quest.subrecords
    start = next(i for i, s in enumerate(subs) if s.signature == 'ALST' and struct.unpack('<I', bytes(s.data))[0] == alias)
    end = next(i for i in range(start, len(subs)) if subs[i].signature == 'ALED')
    for s in subs[start:end]:
        if s.signature == 'ECOR':
            s.data = fid_bytes(flst_fid)
            return
    at = next(i for i in range(start, end) if subs[i].signature in ('ALFC', 'ALPC', 'VTCK'))
    quest.add_subrecord('ECOR', fid_bytes(flst_fid))
    subs.insert(at, subs.pop())


def _attach_packages(quest, alias, package_fids):
    """Add package_fids to the alias's package list (before its VTCK), skipping ones already listed."""
    subs = quest.subrecords
    start = next(i for i, s in enumerate(subs) if s.signature == 'ALST' and struct.unpack('<I', bytes(s.data))[0] == alias)
    end = next(i for i in range(start, len(subs)) if subs[i].signature == 'ALED')
    have = {bytes(s.data) for s in subs[start:end] if s.signature == 'ALPC'}
    at = next(i for i in range(start, end) if subs[i].signature == 'VTCK')
    for fid in package_fids:
        if fid_bytes(fid) in have:
            continue
        quest.add_subrecord('ALPC', fid_bytes(fid))
        subs.insert(at, subs.pop())
        at += 1
