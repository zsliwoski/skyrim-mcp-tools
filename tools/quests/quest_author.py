"""General quest authoring: create an empty quest, add stages and objectives, or build a whole quest from one spec."""
import re

from esplib.record import Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.quests.quest_core import (OBJECTIVE_TAIL, STAGE_TAIL, splice_block, write_header, write_next_alias_id,
                                     write_objective, write_stage)
from tools.quests.quest_edit import _aliases, _open, apply_alias, apply_stage_script
from tools.quests.quest_tools import QUEST_TYPES

QUEST_FLAGS = {
    'start_game_enabled': 0x0001, 'wilderness': 0x0004, 'allow_repeated_stages': 0x0008,
    'run_once': 0x0100, 'exclude_from_dialogue_export': 0x0200, 'warn_on_alias_fill_failure': 0x0400,
}
STAGE_FLAGS = {'start_up': 0x02, 'shut_down': 0x04, 'keep_instance_data': 0x08}


def quest_type_id(value):
    """Quest type from an int or a unique case-insensitive prefix of its name ('side', 'misc', 'thieves')."""
    if isinstance(value, int):
        if not 0 <= value < len(QUEST_TYPES):
            raise ValueError(f"quest_type {value} out of range 0-{len(QUEST_TYPES) - 1}")
        return value
    hits = [i for i, n in enumerate(QUEST_TYPES) if n.lower().startswith(str(value).lower().strip())]
    if len(hits) != 1:
        raise ValueError(f"quest_type {value!r} is not one unique type of {list(QUEST_TYPES)}")
    return hits[0]


def create_quest(b, target_plugin, editor_id, name, quest_type='Side Quests', priority=50, flags=None):
    """Create an empty quest (no stages, objectives or aliases). flags: names from QUEST_FLAGS."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', str(editor_id)):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    bits = 0
    for f in flags or []:
        if f not in QUEST_FLAGS:
            raise ValueError(f"unknown flag {f!r}; choose from {sorted(QUEST_FLAGS)}")
        bits |= QUEST_FLAGS[f]
    if not 0 <= int(priority) <= 255:
        raise ValueError("priority must be 0-255")
    qtype = quest_type_id(quest_type)
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=True)
    plugin = target.plugin
    if plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be written inline")
    low = editor_id.lower()
    if any((r.editor_id or '').lower() == low for r in b.m.records.values()) or \
            any((r.editor_id or '').lower() == low for r in plugin.records):
        raise ValueError(f"editor_id {editor_id!r} already exists")
    b.target, b.plugin = target, plugin
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    oid = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    q = Record('QUST', FormID(b.own(oid)), 0)
    write_header(q, editor_id, name, bits, int(priority), qtype)
    write_next_alias_id(q, 0)
    plugin.add_record(q)
    plugin.header.next_object_id = oid + 1
    return target, q


def apply_stage(q, stage, log=None, flags=None, complete_quest=False):
    bits = 0
    for f in flags or []:
        if f not in STAGE_FLAGS:
            raise ValueError(f"unknown stage flag {f!r}; choose from {sorted(STAGE_FLAGS)}")
        bits |= STAGE_FLAGS[f]
    stage = int(stage)
    if not 0 <= stage <= 0xFFFF:
        raise ValueError("stage must be 0-65535")
    splice_block(q, 'INDX', stage, STAGE_TAIL,
                 lambda r: write_stage(r, stage, log, bits, 1 if complete_quest else 0))


def apply_objective(q, index, text, targets=(), or_with_previous=False):
    index = int(index)
    ids = {n.lower(): a for a, n in _aliases(q)}
    pairs = []
    for t in targets:
        if str(t).lower() not in ids:
            raise ValueError(f"unknown alias {t!r}; quest has {sorted(ids)}")
        pairs.append((ids[str(t).lower()], 0))
    splice_block(q, 'QOBJ', index, OBJECTIVE_TAIL,
                 lambda r: write_objective(r, index, text, pairs, 1 if or_with_previous else 0))


def add_quest_stage(b, quest, target_plugin, stage, log=None, flags=None, complete_quest=False, code=None):
    """Add one stage in index order; code (Papyrus run when the stage is set) is merged into the stage script."""
    target, q = _open(b, quest, target_plugin)
    apply_stage(q, stage, log, flags, complete_quest)
    res = {'quest': quest, 'stage': int(stage)}
    if code:
        res['script'] = apply_stage_script(b, target, q, {int(stage): code}, merge=True)['script']
    target.save()
    res['path'] = str(target.path)
    return res


def add_quest_objective(b, quest, target_plugin, index, text, targets=(), or_with_previous=False):
    """Add one objective; targets are alias names whose references become its compass markers."""
    target, q = _open(b, quest, target_plugin)
    apply_objective(q, index, text, targets, or_with_previous)
    target.save()
    return {'path': str(target.path), 'quest': quest, 'objective': int(index), 'targets': list(targets)}


def build_quest(b, target_plugin, spec):
    """Build a whole quest in one save. spec: {editor_id, name, quest_type, priority, flags, stages: [{stage, log,
    flags, complete_quest, code}], objectives: [{index, text, or_with_previous}], aliases: [{name, reference |
    npc, reference_plugin, objectives}]}. Nothing is written if any part fails."""
    try:
        target, q = create_quest(b, target_plugin, spec['editor_id'], spec['name'], spec.get('quest_type', 'Side Quests'),
                                 spec.get('priority', 50), spec.get('flags'))
    except KeyError as e:
        raise ValueError(f"spec is missing {e.args[0]!r}")
    stages = spec.get('stages') or []
    for s in stages:
        apply_stage(q, s['stage'], s.get('log'), s.get('flags'), s.get('complete_quest', False))
    for o in spec.get('objectives') or []:
        apply_objective(q, o['index'], o['text'], (), o.get('or_with_previous', False))
    for a in spec.get('aliases') or []:
        apply_alias(b, target, q, a['name'], a.get('reference'), a.get('reference_plugin', 'Skyrim.esm'),
                    a.get('npc'), a.get('objectives') or [], a.get('flags'), a.get('location'), a.get('external'))
    code = {int(s['stage']): s['code'] for s in stages if s.get('code')}
    res = {'quest': spec['editor_id']}
    if code:
        res['script'] = apply_stage_script(b, target, q, code)['script']
    target.save()
    res.update(path=str(target.path), stages=sorted(int(s['stage']) for s in stages),
               objectives=sorted(int(o['index']) for o in spec.get('objectives') or []),
               aliases=[n for _, n in _aliases(q)])
    return res
