"""Edit existing quests: reference aliases with objective markers, and stage (QF) fragment scripts."""
import re
import struct

from esplib.vmad import (PROP_OBJECT, VmadData, VmadFragmentData, VmadObject, VmadProperty,
                         VmadQuestFragment, VmadScript)

from tools.npcs.npc_builder import Plan, fid_bytes, z
from tools.papyrus.papyrus_build import compile_script, script_name
from tools.core.plugin_edit import Target, resolve_target_path


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
        if s.signature == 'ALST':
            cur = [struct.unpack('<I', bytes(s.data)[:4])[0], '']
            out.append(cur)
        elif s.signature == 'ALID' and cur is not None:
            cur[1] = bytes(s.data).rstrip(b'\0').decode('cp1252')
    return [tuple(a) for a in out]


def add_quest_alias(b, quest, target_plugin, name, reference=None, reference_plugin='Skyrim.esm',
                    npc=None, objectives=()):
    """Add a quest alias filled with a placed reference (compass marker) or an NPC, and optionally make
    it the target of the given objectives.

    reference: editor ID of a reference in target_plugin, or a hex reference ID (e.g. from search_references)
    found in reference_plugin. The reference must be persistent for the marker to work.
    """
    if (reference is None) == (npc is None):
        raise ValueError("give exactly one of reference or npc")
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise ValueError("name may contain only letters, digits and underscores")
    target, q = _open(b, quest, target_plugin)
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
    else:
        p = Plan()
        own_npc = target.find_own(npc, ['NPC_'])
        if own_npc is not None:
            fill = ('ALUA', fid_bytes(own_npc.form_id.value))
        else:
            nfid, _ = b.resolver.lookup(npc, ['NPC_'])
            p.abs_ids.add(nfid)
            b.add_masters(plugin, p)
            fill = ('ALUA', fid_bytes(b.local(nfid)))

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
    q.add_subrecord('ALST', struct.pack('<I', new_id))
    q.add_subrecord('ALID', z(name))
    q.add_subrecord('FNAM', struct.pack('<I', 2 if fill[0] == 'ALUA' else 0))
    q.add_subrecord(*fill)
    q.add_subrecord('VTCK', fid_bytes(0))
    q.add_subrecord('ALED', b'')
    target.save()
    return {'path': str(target.path), 'alias_id': new_id, 'alias': name, 'objectives': list(objectives)}


def stage_source(name, stages, props, aliases):
    """Papyrus source for a quest script with a Fragment_<stage>() per stage, plus the given properties and alias properties."""
    decls = [f"{ptype} Property {pname} Auto" for pname, ptype in props]
    decls += [f"ReferenceAlias Property Alias_{n} Auto" for _, n in aliases]
    funcs = []
    for stage, code in sorted(stages.items()):
        body = "\n".join(f"  {line.strip()}" for line in str(code).strip().splitlines())
        funcs.append(f"Function Fragment_{stage}()\n{body}\nEndFunction\n")
    return f"Scriptname {name} Extends Quest Hidden\n\n" + "\n".join(funcs) + "\n" + "\n".join(decls) + "\n"


def add_quest_stage_script(b, quest, target_plugin, stages, properties=None):
    """Run Papyrus when quest stages are set: stages {stage number: code}, replacing any earlier stage script.

    code runs inside the quest (e.g. "SetObjectiveDisplayed(20)", "CompleteQuest()"). properties
    {name: form} become Form properties usable in code; every alias also gets a ReferenceAlias property
    named Alias_<alias name>. Stages must already exist in the quest.
    """
    target, q = _open(b, quest, target_plugin)
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
    pex = compile_script(name, stage_source(name, stages, [(n, 'Form') for n, _ in props], aliases),
                         target.path.parent / 'Scripts')
    vprops = [VmadProperty(name=n, type=PROP_OBJECT, flags=1, value=VmadObject(form_id=b.local(f), alias=-1))
              for n, f in props]
    vprops += [VmadProperty(name=f"Alias_{n}", type=PROP_OBJECT, flags=1,
                            value=VmadObject(form_id=qfid, alias=a)) for a, n in aliases]
    script = VmadScript(name=name, flags=0, properties=vprops)
    frags = [VmadQuestFragment(quest_stage=s, unknown2=0, quest_stage_index=0, unknown=1,
                               script_name=name, fragment_name=f"Fragment_{s}") for s in sorted(stages)]
    fd = VmadFragmentData(extra_bind_version=2, filename=name, fragments=frags, fragment_count=len(frags))
    old = VmadData.from_record(q, 'QUST') if q.get_subrecord('VMAD') is not None else None
    keep = [s for s in (old.scripts if old else []) if s.name != name]
    alias_scripts = old.alias_scripts if old else []
    vmad = VmadData(version=5, obj_format=2, scripts=[script] + keep, fragment_data=fd,
                    alias_scripts=alias_scripts).to_bytes('QUST')
    q.subrecords = [s for s in q.subrecords if s.signature != 'VMAD']
    q.add_subrecord('VMAD', vmad)
    q.subrecords.insert(1, q.subrecords.pop())
    target.save()
    return {'path': str(target.path), 'script': name, 'pex': str(pex), 'stages': sorted(stages)}
