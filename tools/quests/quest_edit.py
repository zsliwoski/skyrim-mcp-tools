"""Edit existing quests: reference aliases with objective markers, and stage (QF) fragment scripts."""
import re
import struct

from esplib.vmad import (PROP_OBJECT, VmadData, VmadFragmentData, VmadObject, VmadProperty,
                         VmadQuestFragment, VmadScript)

from tools.npcs.npc_builder import Plan, fid_bytes
from tools.papyrus.papyrus_build import compile_script, script_name
from tools.core.plugin_edit import Target, resolve_target_path
from tools.quests.quest_core import write_alias


def _open(b, quest, target_plugin):
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=False)
    if target.plugin.is_localized:
        raise ValueError(f"{path.name} is localized; quest text cannot be edited inline")
    rec = target.find_own(quest, ['QUST'])
    if rec is None:
        raise ValueError(f"quest {quest!r} not found in {path.name}")
    b.target, b.plugin = target, target.plugin
    return target, rec


def _aliases(rec):
    """[(alias id, name)] in file order."""
    out, cur = [], None
    for s in rec.subrecords:
        if s.signature in ('ALST', 'ALLS'):
            cur = [struct.unpack('<I', bytes(s.data)[:4])[0], '']
            out.append(cur)
        elif s.signature == 'ALID' and cur is not None:
            cur[1] = bytes(s.data).rstrip(b'\0').decode('cp1252')
    return [tuple(a) for a in out]


ALIAS_FLAG_BITS = {
    'reserved': 0x1, 'optional': 0x2, 'quest_object': 0x4, 'allow_reuse': 0x8, 'allow_dead': 0x10,
    'in_loaded_area': 0x20, 'essential': 0x40, 'allow_disabled': 0x80, 'stores_text': 0x100,
    'allow_reserved': 0x200, 'protected': 0x400,
}


def alias_flag_bits(names):
    bits = 0
    for n in names:
        if n not in ALIAS_FLAG_BITS:
            raise ValueError(f"unknown alias flag {n!r}; choose from {sorted(ALIAS_FLAG_BITS)}")
        bits |= ALIAS_FLAG_BITS[n]
    return bits


def _form_local(b, target, ident, sigs, own_sigs=None):
    """Local form ID of ident: a record in the target plugin, else one resolved from the loaded plugins."""
    own = target.find_own(ident, own_sigs or sigs)
    if own is not None:
        return own.form_id.value
    p = Plan()
    fid, _ = b.resolver.lookup(ident, sigs)
    p.abs_ids.add(fid)
    b.add_masters(target.plugin, p)
    return b.local(fid)


def apply_alias(b, target, q, name, reference=None, reference_plugin='Skyrim.esm', npc=None, objectives=(),
                flags=None, location=None, external=None):
    """Add a quest alias and optionally make it the target of the given objectives. Edits q in memory;
    the caller saves target. Give exactly one fill:

    reference: editor ID of a reference in target_plugin, or a hex reference ID (e.g. from search_references)
    found in reference_plugin. The reference must be persistent for the marker to work.
    npc: a unique actor. location: a LCTN, making a location alias.
    external: {quest, alias} reusing an alias of another quest (alias is a name or number).
    flags: names from ALIAS_FLAG_BITS; default is optional for an npc, none otherwise.
    """
    if sum(x is not None for x in (reference, npc, location, external)) != 1:
        raise ValueError("give exactly one of reference, npc, location or external")
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise ValueError("name may contain only letters, digits and underscores")
    plugin = target.plugin
    if any(n.lower() == name.lower() for _, n in _aliases(q)):
        raise ValueError(f"alias {name!r} already exists")
    if reference is not None:
        own = target.find_own(reference, ['REFR', 'ACHR'])
        if own is not None:
            fid = own.form_id.value
        else:
            if not re.fullmatch(r'(0x)?[0-9a-fA-F]{1,8}', str(reference)):
                raise ValueError(f"reference {reference!r} is neither a reference in {target.name} nor a hex ID")
            target.ensure_master(b.names.get(reference_plugin.lower(), reference_plugin))
            masters = [m.lower() for m in plugin.header.masters]
            fid = (masters.index(reference_plugin.lower()) << 24) | (int(str(reference), 16) & 0xFFFFFF)
        fill = ('ALFR', fid_bytes(fid))
        fills, start = [fill], 'ALST'
    elif npc is not None:
        fills, start = [('ALUA', fid_bytes(_form_local(b, target, npc, ['NPC_'])))], 'ALST'
    elif location is not None:
        fills, start = [('ALFL', fid_bytes(_form_local(b, target, location, ['LCTN'])))], 'ALLS'
    else:
        if not isinstance(external, dict) or 'quest' not in external or 'alias' not in external:
            raise ValueError("external must be {quest, alias}")
        qfid = _form_local(b, target, external['quest'], ['QUST'])
        ext = external['alias']
        if not str(ext).isdigit():
            src = target.find_own(external['quest'], ['QUST'])
            if src is None:
                src = b.resolver.lookup(external['quest'], ['QUST'])[1]
            found = [a for a, n in _aliases(src) if n.lower() == str(ext).lower()]
            if not found:
                raise ValueError(f"quest {external['quest']!r} has no alias {ext!r}")
            ext = found[0]
        fills, start = [('ALEQ', fid_bytes(qfid)), ('ALEA', struct.pack('<I', int(ext)))], 'ALST'
    default_flags = 2 if npc is not None else 0
    flag_bits = default_flags if flags is None else alias_flag_bits(flags)

    ids = [a for a, _ in _aliases(q)]
    new_id = max(ids, default=-1) + 1
    anam = next((s for s in q.subrecords if s.signature == 'ANAM'), None)
    if anam is None:
        raise ValueError("quest has no ANAM (next alias ID)")
    anam.data = struct.pack('<I', new_id + 1)

    subs = q.subrecords
    for idx in objectives:
        pos = next((i for i, s in enumerate(subs) if s.signature == 'QOBJ'
                    and struct.unpack('<H', bytes(s.data)[:2])[0] == int(idx)), None)
        if pos is None:
            raise ValueError(f"quest has no objective {idx}")
        end = pos + 1
        while end < len(subs) and subs[end].signature in ('FNAM', 'NNAM', 'QSTA'):
            end += 1
        q.add_subrecord('QSTA', struct.pack('<II', new_id, 0))
        q.subrecords.insert(end, q.subrecords.pop())
    write_alias(q, new_id, name, flag_bits, fills, start=start, vtck=start == 'ALST' and external is None)
    q.modified = True
    return {'path': str(target.path), 'alias_id': new_id, 'alias': name, 'objectives': list(objectives)}


def add_quest_alias(b, quest, target_plugin, name, reference=None, reference_plugin='Skyrim.esm',
                    npc=None, objectives=(), flags=None, location=None, external=None):
    """Open the quest, apply_alias, save."""
    target, q = _open(b, quest, target_plugin)
    res = apply_alias(b, target, q, name, reference, reference_plugin, npc, objectives, flags, location, external)
    target.save()
    return res


def _alias_span(q, name):
    subs = q.subrecords
    for i, (aid, n) in enumerate(_aliases(q)):
        if n.lower() == str(name).lower():
            start = next(j for j, s in enumerate(subs) if s.signature in ('ALST', 'ALLS')
                         and struct.unpack('<I', bytes(s.data)[:4])[0] == aid)
            end = next(j for j in range(start, len(subs)) if subs[j].signature == 'ALED')
            return aid, start, end
    raise ValueError(f"quest has no alias {name!r}")


def edit_quest_alias(b, quest, target_plugin, alias, flags):
    """Replace an alias's flags (names from ALIAS_FLAG_BITS)."""
    target, q = _open(b, quest, target_plugin)
    aid, start, end = _alias_span(q, alias)
    bits = alias_flag_bits(flags)
    fnam = next(s for s in q.subrecords[start:end] if s.signature == 'FNAM')
    fnam.data = struct.pack('<I', bits)
    q.modified = True
    target.save()
    return {'path': str(target.path), 'alias': alias, 'flags': list(flags)}


def remove_quest_alias(b, quest, target_plugin, alias):
    """Remove an alias, the objective targets that point at it and its alias scripts. A stage script that
    references it must be rebuilt with add_quest_stage_script."""
    target, q = _open(b, quest, target_plugin)
    aid, start, end = _alias_span(q, alias)
    subs = q.subrecords
    del subs[start:end + 1]
    q.subrecords = [s for s in subs if not (s.signature == 'QSTA'
                                            and struct.unpack('<I', bytes(s.data)[:4])[0] == aid)]
    dropped = 0
    if q.get_subrecord('VMAD') is not None:
        vm = VmadData.from_record(q, 'QUST')
        kept = [a for a in vm.alias_scripts if a.alias_obj.alias != aid]
        dropped = len(vm.alias_scripts) - len(kept)
        if dropped:
            vm.alias_scripts = kept
            q.get_subrecord('VMAD').data = vm.to_bytes('QUST')
    q.modified = True
    target.save()
    return {'path': str(target.path), 'removed': alias, 'alias_scripts_removed': dropped}


def stage_source(name, stages, props, aliases):
    """Papyrus source for a quest script with a Fragment_<stage>() per stage, plus the given properties and alias properties."""
    decls = [f"{ptype} Property {pname} Auto" for pname, ptype in props]
    decls += [f"ReferenceAlias Property Alias_{n} Auto" for _, n in aliases]
    funcs = []
    for stage, code in sorted(stages.items()):
        body = "\n".join(f"  {line.strip()}" for line in str(code).strip().splitlines())
        funcs.append(f"Function Fragment_{stage}()\n{body}\nEndFunction\n")
    return f"Scriptname {name} Extends Quest Hidden\n\n" + "\n".join(funcs) + "\n" + "\n".join(decls) + "\n"


FRAGMENT = re.compile(r'^Function Fragment_(\d+)\(\)\n(.*?)\nEndFunction$', re.MULTILINE | re.DOTALL)


def stage_code_from_source(text):
    """{stage: code} read back from a source written by stage_source."""
    return {int(n): "\n".join(line.strip() for line in body.splitlines()) for n, body in FRAGMENT.findall(text)}


def apply_stage_script(b, target, q, stages, properties=None, merge=False):
    """Compile the quest's stage script and attach it to q in memory; the caller saves target.

    merge keeps the code and Form properties of the existing stage script (read back from its .psc), so
    stages and properties given now are added to it instead of replacing it.
    """
    plugin = target.plugin
    have = {struct.unpack('<H', bytes(s.data)[:2])[0] for s in q.subrecords if s.signature == 'INDX'}
    stages = {int(k): v for k, v in (stages or {}).items()}
    missing = sorted(set(stages) - have)
    if not stages or missing:
        raise ValueError(f"stages must be given and exist in the quest (missing {missing}; has {sorted(have)})")
    p, props = Plan(), []
    for pname, ident in (properties or {}).items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', pname):
            raise ValueError(f"bad property name {pname!r}")
        fid, _ = b.resolver.lookup(ident, list(b.m.by_sig))
        p.abs_ids.add(fid)
        props.append((pname, fid))
    b.add_masters(plugin, p)
    aliases = _aliases(q)
    qfid = q.form_id.value
    name = script_name(plugin.file_path.name, 'QF', qfid & 0xFFFFFF)
    scripts_dir = target.path.parent / 'Scripts'
    old = VmadData.from_record(q, 'QUST') if q.get_subrecord('VMAD') is not None else None
    vprops = [VmadProperty(name=n, type=PROP_OBJECT, flags=1, value=VmadObject(form_id=b.local(f), alias=-1))
              for n, f in props]
    if merge:
        psc = scripts_dir / 'Source' / f"{name}.psc"
        prior = stage_code_from_source(psc.read_text(encoding='cp1252')) if psc.is_file() else {}
        if not prior and old is not None and old.fragment_data is not None and old.fragment_data.fragments:
            raise ValueError(f"cannot merge: the stage script of {q.editor_id} has no source at {psc}")
        stages = {**prior, **stages}
        mine = next((s for s in old.scripts if s.name == name), None) if old else None
        given = {n for n, _ in props}
        for sp in (mine.properties if mine else []):
            if sp.type == PROP_OBJECT and not sp.name.startswith('Alias_') and sp.name not in given:
                vprops.append(sp)
                props.append((sp.name, None))
    pex = compile_script(name, stage_source(name, stages, [(n, 'Form') for n, _ in props], aliases), scripts_dir)
    vprops += [VmadProperty(name=f"Alias_{n}", type=PROP_OBJECT, flags=1,
                            value=VmadObject(form_id=qfid, alias=a)) for a, n in aliases]
    script = VmadScript(name=name, flags=0, properties=vprops)
    frags = [VmadQuestFragment(quest_stage=s, unknown2=0, quest_stage_index=0, unknown=1,
                               script_name=name, fragment_name=f"Fragment_{s}") for s in sorted(stages)]
    fd = VmadFragmentData(extra_bind_version=2, filename=name, fragments=frags, fragment_count=len(frags))
    keep = [s for s in (old.scripts if old else []) if s.name != name]
    alias_scripts = old.alias_scripts if old else []
    vmad = VmadData(version=5, obj_format=2, scripts=[script] + keep, fragment_data=fd,
                    alias_scripts=alias_scripts).to_bytes('QUST')
    q.subrecords = [s for s in q.subrecords if s.signature != 'VMAD']
    q.add_subrecord('VMAD', vmad)
    q.subrecords.insert(1, q.subrecords.pop())
    return {'path': str(target.path), 'script': name, 'pex': str(pex), 'stages': sorted(stages)}


def add_quest_stage_script(b, quest, target_plugin, stages, properties=None, merge=False):
    """Run Papyrus when quest stages are set: stages {stage number: code}, replacing any earlier stage script
    (or adding to it when merge is set).

    code runs inside the quest (e.g. "SetObjectiveDisplayed(20)", "CompleteQuest()"). properties
    {name: form} become Form properties usable in code; every alias also gets a ReferenceAlias property
    named Alias_<alias name>. Stages must already exist in the quest.
    """
    target, q = _open(b, quest, target_plugin)
    res = apply_stage_script(b, target, q, stages, properties, merge)
    target.save()
    return res
