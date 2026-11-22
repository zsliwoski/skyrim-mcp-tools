"""Stage-gated NPC dialogue: topics offered only at certain stages of a quest, optionally advancing it."""

PROP = 'StoryQuest'


def expand_topic(topic, default_quest, index):
    """Turn the quest sugar of one topic into plain dialogue fields: stage_min / stage_max / stage_done /
    not_stage_done become GetStage / GetStageDone conditions on `quest`; set_stage becomes code run when the line
    ends; on_say is code."""
    t = dict(topic)
    quest = t.pop('quest', None) or default_quest
    sugar = {k: t.pop(k) for k in ('stage_min', 'stage_max', 'stage_done', 'not_stage_done', 'set_stage')
             if k in t}
    if 'on_say' in t:
        if 'code' in t:
            raise ValueError(f"topics[{index}]: give code or on_say, not both")
        t['code'] = t.pop('on_say')
    if not sugar:
        return t
    if not quest:
        raise ValueError(f"topics[{index}]: {sorted(sugar)} need a quest (on the topic or the tool)")
    conds = list(t.get('conditions') or [])
    if 'stage_min' in sugar:
        conds.append({'function': 'GetStage', 'form': quest, 'op': '>=', 'value': sugar['stage_min']})
    if 'stage_max' in sugar:
        conds.append({'function': 'GetStage', 'form': quest, 'op': '<=', 'value': sugar['stage_max']})
    if 'stage_done' in sugar:
        conds.append({'function': 'GetStageDone', 'form': quest, 'op': '==', 'value': 1,
                      'param2': sugar['stage_done']})
    if 'not_stage_done' in sugar:
        conds.append({'function': 'GetStageDone', 'form': quest, 'op': '==', 'value': 0,
                      'param2': sugar['not_stage_done']})
    if conds:
        t['conditions'] = conds
    if 'set_stage' in sugar:
        props = dict(t.get('properties') or {})
        props[PROP] = quest
        t['properties'] = props
        step = f"({PROP} as Quest).SetStage({int(sugar['set_stage'])})"
        t['code'] = f"{t['code']}\n{step}" if t.get('code') else step
    return t


def add_quest_dialogue(b, target_plugin, npc, topics, quest=None, dialogue_quest=None):
    """Add topics to an existing NPC (vanilla or in target_plugin) through edit_npc. Topics take every
    dialogue field of create_npc plus the quest sugar of expand_topic. Topics land in the NPC's dialogue quest
    (created if missing) or in dialogue_quest."""
    if not topics:
        raise ValueError("give at least one topic")
    spec = {'dialogue': [expand_topic(t, quest, i) for i, t in enumerate(topics)]}
    if dialogue_quest:
        spec['quest_editor_id'] = dialogue_quest
    res = b.edit_npc(npc, spec, target_plugin)
    made = res.get('records', [])
    return {'path': res['path'], 'npc': npc, 'warnings': res.get('warnings', []),
            'topics': [r for r in made if r.get('type') == 'DIAL'],
            'infos': [r['object_id'] for r in made if r.get('type') == 'INFO'],
            'scripts': [r['editor_id'] for r in made if r.get('type') == 'script']}
