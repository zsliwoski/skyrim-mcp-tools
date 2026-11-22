"""Attach Papyrus scripts to quests and quest aliases, and the clue-set helper built on alias scripts."""
import re
from pathlib import Path

from esplib.vmad import (PROP_BOOL, PROP_FLOAT, PROP_INT32, PROP_INT32_ARRAY, PROP_OBJECT, PROP_STRING,
                         PROP_STRING_ARRAY, VmadAliasScripts, VmadData, VmadFragmentData, VmadObject, VmadProperty,
                         VmadScript)

from tools.papyrus.papyrus_build import CompileError, compile_script, ensure_sources
from tools.quests.quest_author import apply_stage
from tools.quests.quest_core import has_index
from tools.quests.quest_edit import _alias_span, _aliases, _form_local, _open, apply_alias

NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def build_properties(b, target, q, properties):
    """VmadProperty list from {name: value}. A bool, int, float or str binds that type; a list of ints or strs
    binds an array; {"form": ident} binds a record; {"alias": name} binds an alias of this quest."""
    out = []
    for name, v in (properties or {}).items():
        if not NAME.fullmatch(name):
            raise ValueError(f"bad property name {name!r}")
        if isinstance(v, dict) and 'form' in v:
            ident = v['form']
            fid = _form_local(b, target, ident, list(b.m.by_sig))
            out.append(VmadProperty(name=name, type=PROP_OBJECT, flags=1, value=VmadObject(form_id=fid, alias=-1)))
        elif isinstance(v, dict) and 'alias' in v:
            aid = next((a for a, n in _aliases(q) if n.lower() == str(v['alias']).lower()), None)
            if aid is None:
                raise ValueError(f"property {name}: quest has no alias {v['alias']!r}")
            out.append(VmadProperty(name=name, type=PROP_OBJECT, flags=1,
                                    value=VmadObject(form_id=q.form_id.value, alias=aid)))
        elif isinstance(v, bool):
            out.append(VmadProperty(name=name, type=PROP_BOOL, flags=1, value=v))
        elif isinstance(v, int):
            out.append(VmadProperty(name=name, type=PROP_INT32, flags=1, value=v))
        elif isinstance(v, float):
            out.append(VmadProperty(name=name, type=PROP_FLOAT, flags=1, value=v))
        elif isinstance(v, str):
            out.append(VmadProperty(name=name, type=PROP_STRING, flags=1, value=v))
        elif isinstance(v, list) and v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            out.append(VmadProperty(name=name, type=PROP_INT32_ARRAY, flags=1, value=list(v)))
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            out.append(VmadProperty(name=name, type=PROP_STRING_ARRAY, flags=1, value=list(v)))
        else:
            raise ValueError(f"property {name}: unsupported value {v!r}")
    return out


def ensure_script(b, target, script, source, props):
    """Compile source into the plugin's Scripts folder, or check that script already exists (compiled next to the
    plugin, in the game's Data/Scripts, or as a vanilla source, i.e. a stock script). Every property given must be
    declared by the script when its source is known."""
    if not NAME.fullmatch(script):
        raise ValueError(f"bad script name {script!r}")
    scripts_dir = target.path.parent / 'Scripts'
    text = None
    if source:
        if not re.search(rf'^\s*Scriptname\s+{re.escape(script)}\b', source, re.I | re.M):
            raise ValueError(f"source must declare Scriptname {script}")
        compile_script(script, source, scripts_dir)
        text = source
    else:
        psc = scripts_dir / 'Source' / f'{script}.psc'
        if psc.is_file():
            text = psc.read_text(encoding='cp1252')
        try:
            vanilla = ensure_sources() / f'{script}.psc'
        except CompileError:
            vanilla = None
        if text is None and vanilla is not None and vanilla.is_file():
            text = vanilla.read_text(encoding='cp1252', errors='replace')
        elif text is None and not (scripts_dir / f'{script}.pex').is_file() \
                and not (Path(b.data_dir) / 'Scripts' / f'{script}.pex').is_file():
            raise ValueError(f"script {script!r} is neither compiled nor a stock script; give source")
    if text is not None:
        declared = {m.lower() for m in re.findall(r'\bProperty\s+(\w+)', text, re.I)}
        for p in props:
            if p.name.lower() not in declared:
                raise ValueError(f"script {script} declares no property {p.name!r}")


def _merge_script(scripts, script, props):
    """scripts with `script` replaced by a new VmadScript, keeping the properties it already had that are not
    given again."""
    old = next((s for s in scripts if s.name.lower() == script.lower()), None)
    given = {p.name.lower() for p in props}
    merged = list(props) + [p for p in (old.properties if old else []) if p.name.lower() not in given]
    new = VmadScript(name=script, flags=0, properties=merged)
    if old is None:
        return scripts + [new]
    return [new if s is old else s for s in scripts]


def _read_vmad(q):
    return VmadData.from_record(q, 'QUST') if q.get_subrecord('VMAD') is not None else VmadData()


def _write_vmad(q, vm):
    fd = vm.fragment_data or VmadFragmentData(extra_bind_version=2, filename='', fragments=[], fragment_count=0)
    data = VmadData(version=5, obj_format=2, scripts=vm.scripts, fragment_data=fd,
                    alias_scripts=vm.alias_scripts).to_bytes('QUST')
    q.subrecords = [s for s in q.subrecords if s.signature != 'VMAD']
    q.add_subrecord('VMAD', data)
    q.subrecords.insert(1, q.subrecords.pop())
    q.modified = True


def apply_alias_script(b, target, q, alias, script, properties=None, source=None):
    aid, _, _ = _alias_span(q, alias)
    props = build_properties(b, target, q, properties)
    ensure_script(b, target, script, source, props)
    vm = _read_vmad(q)
    block = next((a for a in vm.alias_scripts if a.alias_obj.alias == aid), None)
    if block is None:
        block = VmadAliasScripts(alias_obj=VmadObject(form_id=q.form_id.value, alias=aid))
        vm.alias_scripts.append(block)
    block.scripts = _merge_script(block.scripts, script, props)
    _write_vmad(q, vm)


def apply_quest_script(b, target, q, script, properties=None, source=None):
    props = build_properties(b, target, q, properties)
    ensure_script(b, target, script, source, props)
    vm = _read_vmad(q)
    vm.scripts = _merge_script(vm.scripts, script, props)
    _write_vmad(q, vm)


def attach_alias_script(b, quest, target_plugin, alias, script, properties=None, source=None):
    """Bind a script (compiled from source, or an existing/stock one) to a quest alias. Quest scripts, stage
    fragments and other alias scripts are kept; properties already on this script survive unless given again."""
    target, q = _open(b, quest, target_plugin)
    apply_alias_script(b, target, q, alias, script, properties, source)
    target.save()
    return {'path': str(target.path), 'quest': quest, 'alias': alias, 'script': script}


def attach_quest_script(b, quest, target_plugin, script, properties=None, source=None):
    """Bind a script to a quest. Calling it again with new properties rebinds those; the stage fragment script
    and alias scripts are kept."""
    target, q = _open(b, quest, target_plugin)
    apply_quest_script(b, target, q, script, properties, source)
    target.save()
    return {'path': str(target.path), 'quest': quest, 'script': script}


def clue_source(script):
    return f"""Scriptname {script} Extends ReferenceAlias

Quest Property OwnerQuest Auto
Int Property ClueStage Auto
Int[] Property AllClueStages Auto
Int Property DoneStage Auto

Bool Found = False

Event OnActivate(ObjectReference akActionRef)
  If Found || akActionRef != Game.GetPlayer()
    Return
  EndIf
  Found = True
  OwnerQuest.SetStage(ClueStage)
  If DoneStage > 0 && !OwnerQuest.GetStageDone(DoneStage)
    Int i = 0
    While i < AllClueStages.Length
      If !OwnerQuest.GetStageDone(AllClueStages[i])
        Return
      EndIf
      i += 1
    EndWhile
    OwnerQuest.SetStage(DoneStage)
  EndIf
EndEvent
"""


def add_clue_set(b, quest, target_plugin, clues, done_stage=None, done_log=None, script=None):
    """Make persistent references into clues: activating one sets its stage (once). When done_stage is given it
    is set after every clue stage is done. clues: [{alias, reference, reference_plugin, stage, log, flags}];
    a stage that does not exist is created with its log."""
    if not clues:
        raise ValueError("give at least one clue")
    target, q = _open(b, quest, target_plugin)
    script = script or f"{q.editor_id}ClueScript"
    stages = [int(c['stage']) for c in clues]
    if len(set(stages)) != len(stages):
        raise ValueError("each clue needs its own stage")
    for c in clues:
        for k in ('alias', 'reference', 'stage'):
            if k not in c:
                raise ValueError(f"clue is missing {k!r}")
        apply_alias(b, target, q, c['alias'], c['reference'], c.get('reference_plugin', 'Skyrim.esm'),
                    flags=c.get('flags'))
        if not has_index(q, 'INDX', int(c['stage'])):
            apply_stage(q, c['stage'], c.get('log'))
    if done_stage is not None and not has_index(q, 'INDX', int(done_stage)):
        apply_stage(q, done_stage, done_log)
    source = clue_source(script)
    for i, c in enumerate(clues):
        props = {'OwnerQuest': {'form': q.editor_id}, 'ClueStage': int(c['stage']),
                 'AllClueStages': stages, 'DoneStage': int(done_stage or 0)}
        apply_alias_script(b, target, q, c['alias'], script, props, source if i == 0 else None)
    target.save()
    return {'path': str(target.path), 'quest': quest, 'script': script, 'aliases': [c['alias'] for c in clues],
            'stages': stages}
