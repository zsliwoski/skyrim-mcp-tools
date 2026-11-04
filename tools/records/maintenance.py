"""Plugin housekeeping: snapshots, install into Data, validation and in-game console helpers."""
import hashlib
import re
import shutil
import struct
import time
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.record import GroupRecord

from tools.core.plugin_edit import OFFICIAL, hi, plugin_files, vmad_script_names

# Subrecords that always hold a single form ID and must point at a real master or own record.
FORM_SUBS = {'ALFR', 'ALUA', 'EITM', 'NAME', 'TCLT', 'QNAM', 'XOWN'}
SCRIPT_PREFIXES = ('TIF__', 'QF__', 'SF__', 'PF__')


def snapshot_dir(path):
    """The snapshots folder beside the plugin."""
    return Path(path).parent / 'snapshots'


def snapshot(path, label=None):
    """Copy the plugin to snapshots/<stem>.<timestamp>[.<label>].esp and return its name."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"plugin not found: {path}")
    d = snapshot_dir(path)
    d.mkdir(exist_ok=True)
    tag = re.sub(r'[^A-Za-z0-9_-]', '', label or '')
    dest = d / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}{'.' + tag if tag else ''}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def list_snapshots(path):
    """Name, size and modified time of each snapshot of this plugin, oldest first."""
    path = Path(path)
    d = snapshot_dir(path)
    found = sorted(d.glob(f"{path.stem}.*{path.suffix}")) if d.is_dir() else []
    return [{'name': f.name, 'size': f.stat().st_size,
             'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(f.stat().st_mtime))} for f in found]


def restore(path, which='latest'):
    """Restore a snapshot (a name, a label or 'latest'); the current plugin is snapshotted first."""
    path = Path(path)
    snaps = list_snapshots(path)
    if not snaps:
        raise ValueError(f"no snapshots for {path.name}")
    if which == 'latest':
        pick = snaps[-1]
    else:
        pick = next((s for s in reversed(snaps) if s['name'] == which or s['name'].endswith(f".{which}{path.suffix}")), None)
        if pick is None:
            raise ValueError(f"no snapshot matches {which!r}; have {[s['name'] for s in snaps]}")
    saved = snapshot(path, 'pre_restore') if path.exists() else None
    shutil.copy2(snapshot_dir(path) / pick['name'], path)
    return {'restored': pick['name'], 'previous_state_saved_as': saved.name if saved else None}


def digest(p):
    """SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def install(path, data_dir, dry_run=False):
    """Copy the plugin and its loose files into the Data folder (regenerating SEQ) and report what changed."""
    path, data_dir = Path(path), Path(data_dir)
    if not data_dir.is_dir():
        raise ValueError(f"Data folder not found: {data_dir}")
    base = path.parent
    rows = []
    for f in plugin_files(path):
        rel = f.relative_to(base)
        dest = data_dir / rel
        if not dest.exists():
            state = 'new'
        else:
            state = 'same' if digest(f) == digest(dest) else 'changed'
        if state != 'same' and not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        rows.append({'file': rel.as_posix(), 'state': state})
    changed = [r for r in rows if r['state'] != 'same']
    return {'data_dir': str(data_dir), 'dry_run': dry_run, 'files': rows,
            'summary': f"{len(changed)} of {len(rows)} files {'would be ' if dry_run else ''}updated",
            'note': "Restart Skyrim; for a mod manager install, copy via the zip instead or the manager may overwrite these."}


def all_records(plugin):
    """Yield every record in the plugin, descending through nested groups."""
    def walk(items):
        for it in items:
            if isinstance(it, GroupRecord):
                yield from walk(it.records)
            else:
                yield it
    yield from walk(plugin.records)


PERSISTENT = 0x400


_ref_flag_cache = {}


def master_ref_flags(master):
    """{object id: persistent} for every REFR/ACHR in a master, built once per file and file version."""
    st = master.stat()
    key = (str(master), st.st_size, st.st_mtime_ns)
    if key not in _ref_flag_cache:
        pl = Plugin.load_cached(str(master), only_signatures={'CELL', 'WRLD', 'REFR', 'ACHR'})
        own = len(pl.header.masters)
        _ref_flag_cache[key] = {r.form_id.value & 0xFFFFFF: bool(int(r.flags) & PERSISTENT)
                                for r in all_records(pl)
                                if r.signature in ('REFR', 'ACHR') and hi(r.form_id.value) == own}
    return _ref_flag_cache[key]


def ref_persistent(plugin, recs, ref, data_dir=None):
    """True/False whether a placed reference is persistent; None when it cannot be determined."""
    own = len(plugin.header.masters)
    for r in recs:
        if r.form_id.value == ref and r.signature in ('REFR', 'ACHR'):
            return bool(int(r.flags) & PERSISTENT)
    if hi(ref) == own or not data_dir or hi(ref) >= own:
        return None
    master = Path(data_dir) / plugin.header.masters[hi(ref)]
    if not master.is_file():
        return None
    return master_ref_flags(master).get(ref & 0xFFFFFF)


def validate(path, data_dir=None):
    """Static checks that catch the usual in-game failures. Returns {'errors': [...], 'warnings': [...]}."""
    path = Path(path)
    base = path.parent
    errors, warnings = [], []
    plugin = Plugin.load(str(path))
    masters = list(plugin.header.masters)
    own = len(masters)
    stem = re.sub(r'[^A-Za-z0-9]', '', path.stem)[:16]

    for m in masters:
        found = (Path(data_dir) / m).exists() if data_dir else True
        if not found and m.lower() not in OFFICIAL:
            errors.append(f"master {m} is not in the Data folder")
        elif not found:
            errors.append(f"official master {m} is missing from the Data folder")
    if plugin.is_localized:
        warnings.append("plugin is localized; inline text added by the builders will not display")

    recs = list(all_records(plugin))
    own_ids = {r.form_id.value for r in recs if hi(r.form_id.value) == own}
    seen = {}
    for r in recs:
        fid = r.form_id.value
        if hi(fid) > own:
            errors.append(f"{r.signature} {fid:08X} has master index {hi(fid)} but only {own} masters exist")
        if hi(fid) == own and r.editor_id:
            low = r.editor_id.lower()
            if low in seen:
                errors.append(f"duplicate editor ID {r.editor_id} ({seen[low]:08X} and {fid:08X})")
            seen[low] = fid
        for s in r.subrecords:
            if s.signature in FORM_SUBS and len(s.data) == 4:
                ref = struct.unpack('<I', bytes(s.data))[0]
                if ref and hi(ref) > own:
                    errors.append(f"{r.editor_id or r.signature} {s.signature} points at master index {hi(ref)}, beyond {own}")
                elif ref and hi(ref) == own and ref not in own_ids:
                    errors.append(f"{r.editor_id or r.signature} {s.signature} points at missing own record {ref:08X}")

    script_dirs = [base / 'Scripts'] + ([Path(data_dir) / 'Scripts'] if data_dir else [])
    needed = set()
    for r in recs:
        v = r.get_subrecord('VMAD')
        if v is None:
            continue
        for name in re.findall(rb'[A-Za-z][A-Za-z0-9_]{3,}', bytes(v.data)):
            n = name.decode()
            if n.startswith(tuple(f"{p}{stem}_" for p in SCRIPT_PREFIXES)) or n.startswith(f"{stem}_"):
                needed.add(n)
    # A script with a source here was written for this plugin, so it needs its .pex whatever its name.
    needed.update(n for n in vmad_script_names(plugin) if (base / 'Scripts' / 'Source' / f"{n}.psc").is_file())
    for n in sorted(needed):
        if not any((d / f"{n}.pex").exists() for d in script_dirs):
            errors.append(f"script {n} is attached but {n}.pex was not found")

    for q in (r for r in recs if r.signature == 'QUST'):
        if hi(q.form_id.value) != own:
            continue
        alias_ids, objectives, targets = set(), set(), set()
        for s in q.subrecords:
            d = bytes(s.data)
            if s.signature == 'ALST' and len(d) >= 4:
                alias_ids.add(struct.unpack('<I', d[:4])[0])
            elif s.signature == 'QOBJ' and len(d) >= 2:
                objectives.add(struct.unpack('<H', d[:2])[0])
            elif s.signature == 'QSTA' and len(d) >= 4:
                targets.add(struct.unpack('<I', d[:4])[0])
        for t in targets - alias_ids:
            errors.append(f"quest {q.editor_id}: objective target alias {t} does not exist")
        for s in q.subrecords:
            if s.signature == 'ALFR' and len(s.data) == 4:
                ref = struct.unpack('<I', bytes(s.data))[0]
                if ref and ref_persistent(plugin, recs, ref, data_dir) is False:
                    warnings.append(f"quest {q.editor_id}: alias reference {ref:08X} is not persistent, so the "
                                    "alias will not fill and no marker shows; use make_reference_persistent")
        dnam = q.get_subrecord('DNAM')
        if dnam is not None and len(dnam.data) >= 2 and not struct.unpack_from('<H', bytes(dnam.data))[0] & 1 \
                and q.get_subrecord('ENAM') is None:  # an ENAM story event means the Story Manager starts it
            warnings.append(f"quest {q.editor_id} is not start-game-enabled; it needs a start script")

    seq = base / 'SEQ' / f"{path.stem}.seq"
    expected = {r.form_id.value & 0xFFFFFF for r in recs if r.signature == 'QUST' and hi(r.form_id.value) == own
                and r.get_subrecord('DNAM') is not None
                and struct.unpack_from('<H', bytes(r.get_subrecord('DNAM').data))[0] & 1}
    have = set(struct.unpack(f'<{seq.stat().st_size // 4}I', seq.read_bytes())) if seq.exists() else set()
    if expected and have != expected:
        errors.append(f"SEQ file is stale (has {sorted(have)}, quests are {sorted(expected)}); run package_plugin or install")

    if data_dir:
        for f in plugin_files(path, regen_seq=False):
            dest = Path(data_dir) / f.relative_to(base)
            if not dest.exists():
                warnings.append(f"{f.relative_to(base).as_posix()} is not installed in Data")
            elif digest(f) != digest(dest):
                warnings.append(f"{f.relative_to(base).as_posix()} in Data differs from the output copy")
    if abs(plugin.header.version - 1.7) > 0.001:
        warnings.append(f"plugin header version is {plugin.header.version:g}, not 1.70; the game ignores armor "
                        "slots and addons, so worn items get no model")
    enchanted = {}
    for r in recs:
        if hi(r.form_id.value) != own:
            continue
        if r.signature == 'ENCH':
            enit = r.get_subrecord('ENIT')
            if enit is not None and len(enit.data) >= 16 and struct.unpack_from('<i', bytes(enit.data))[0] == 0 \
                    and not struct.unpack_from('<I', bytes(enit.data), 4)[0] & 1:
                warnings.append(f"enchantment {r.editor_id}: auto-calculated ENIT cost is 0; weapons using it can "
                                "show no charge bar or recharge option (use a vanilla-like cost, e.g. 200)")
        elif r.signature == 'WEAP' and r.get_subrecord('EITM') is not None:
            enchanted[r.form_id.value] = r.editor_id
            if r.get_subrecord('EAMT') is None:
                warnings.append(f"weapon {r.editor_id} is enchanted but has no EAMT (maximum charge)")
    for r in recs:
        name = r.get_subrecord('NAME') if r.signature == 'REFR' else None
        if name is not None and len(name.data) == 4 and r.get_subrecord('XCHG') is None \
                and struct.unpack('<I', bytes(name.data))[0] in enchanted:
            warnings.append(f"placed enchanted weapon {enchanted[struct.unpack('<I', bytes(name.data))[0]]} "
                            f"({r.form_id.value:08X}) has no XCHG, so it spawns uncharged; use place_item")
    from tools.books.lore import lint_plugin
    for issue in lint_plugin(path, limit=20):
        warnings.append(f"lore: {issue['record']} {issue['sig']}: '{issue['match']}' - {issue['suggestion']}")
    return {'plugin': path.name, 'records': len(recs), 'errors': errors, 'warnings': warnings,
            'ok': not errors}


OBJECT_REF_PROP = re.compile(r'^\s*ObjectReference\s+Property\s+(\w+)', re.I | re.M)
IF_COMPARE = re.compile(r'^\s*(?:Else)?If\s+(.*)$', re.I | re.M)


def audit_scripts(path):
    """Fragile Papyrus patterns in the plugin's script sources: unguarded comparisons with reference properties."""
    out = []
    for psc in sorted((Path(path).parent / 'Scripts' / 'Source').glob('*.psc')):
        src = psc.read_text(encoding='cp1252', errors='replace')
        props = set(OBJECT_REF_PROP.findall(src))
        for line in IF_COMPARE.findall(src):
            for p in props:
                if re.search(rf'==\s*{p}\b|\b{p}\s*==', line) and not re.search(r'!=\s*None', line):
                    out.append(f"{psc.name}: '{line.strip()}' compares with property {p}, which may be None "
                               "(unloaded reference); guard with '!= None' or compare GetFormID()")
    return out


def audit_plugin(path, data_dir=None):
    """Design checks for fragile quests: objectives with no marker alias, script properties on
    non-persistent references, unguarded script comparisons. Complements validate_plugin."""
    from esplib.vmad import VmadData
    path = Path(path)
    plugin = Plugin.load(str(path))
    recs = list(all_records(plugin))
    own = len(plugin.header.masters)
    issues = []
    for q in (r for r in recs if r.signature == 'QUST' and hi(r.form_id.value) == own):
        current, has_target = None, False
        for s in list(q.subrecords) + [None]:
            if s is None or s.signature == 'QOBJ':
                if current is not None and not has_target:
                    issues.append(f"quest {q.editor_id}: objective {current} has no alias target, so no compass marker")
                if s is not None:
                    current, has_target = struct.unpack_from('<H', bytes(s.data))[0], False
            elif s.signature == 'QSTA':
                has_target = True
    for r in recs:
        if r.get_subrecord('VMAD') is None or hi(r.form_id.value) != own:
            continue
        try:
            vmad = VmadData.from_record(r)
        except Exception:
            continue
        for script in (vmad.scripts if vmad else []):
            for p in script.properties:
                fid = getattr(p.value, 'form_id', None)
                if fid and ref_persistent(plugin, recs, fid, data_dir) is False:
                    issues.append(f"{script.name}.{p.name} points at non-persistent reference {fid:08X}; it can "
                                  "resolve to None at runtime")
    issues += audit_scripts(path)
    return {'plugin': path.name, 'issues': issues, 'ok': not issues}


def deploy_plugin(path, data_dir, mode='package', remove_loose=False):
    """Validate, then ship the plugin: a Vortex zip (mode 'package'), loose files in Data (mode 'install') or
    both (mode 'both': zip first, then loose files).

    Validation errors stop the deploy. When packaging, loose copies of the plugin's files already in Data are
    reported (they override the mod manager's files and hide changes); remove_loose deletes them. In mode
    'both' the loose copies are the install, so they are kept."""
    path, data_dir = Path(path), Path(data_dir)
    if mode not in ('package', 'install', 'both'):
        raise ValueError("mode must be 'package', 'install' or 'both'")
    report = validate(path, data_dir)
    out = {'plugin': path.name, 'mode': mode, 'errors': report['errors'], 'warnings': report['warnings'],
           'deployed': False}
    if report['errors']:
        return out
    if mode in ('package', 'both'):
        from tools.core.plugin_edit import package_plugin
        out.update(package_plugin(path))
    if mode in ('install', 'both'):
        installed = install(path, data_dir)
        out.update({'install': installed} if mode == 'both' else installed)
    elif mode == 'package':
        loose = [data_dir / f for f in out['files'] if (data_dir / f).is_file()]
        out['loose_in_data'] = [str(f) for f in loose]
        if remove_loose:
            for f in loose:
                f.unlink()
            out['removed_loose'] = len(loose)
        elif loose:
            out['note'] = ("Loose copies in Data override the mod manager's files; delete them or call again "
                           "with remove_loose=true before testing the Vortex install.")
    out['deployed'] = True
    return out


ITEM_SIGS = {'WEAP', 'ARMO', 'AMMO', 'ALCH', 'BOOK', 'MISC', 'INGR', 'KEYM', 'SCRL', 'SLGM'}


def item_console_commands(path, query=None, sections=None):
    """Console commands to check the plugin's own records in game: additem, addspell, coc, placeatme.

    query: only records whose editor ID contains it. sections: subset of items, spells, cells, npcs, effects,
    placed (default: all but placed, which lists teleports to every named placed reference)."""
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    names = ['items', 'spells', 'cells', 'npcs', 'effects', 'placed']
    wanted = list(sections) if sections else [n for n in names if n != 'placed']
    unknown = [s for s in wanted if s not in names]
    if unknown:
        raise ValueError(f"unknown sections {unknown}; choose from {names}")
    low = (query or '').lower()
    out = {n: [] for n in wanted}
    for r in all_records(plugin):
        if hi(r.form_id.value) != own:
            continue
        fid, edid = f"XX{r.form_id.value & 0xFFFFFF:06X}", r.editor_id or ''
        if low and low not in edid.lower():
            continue
        if r.signature == 'REFR':
            if edid and 'placed' in out:
                out['placed'].append(f"player.moveto {fid}    ; {edid} (teleports you to the placed item)")
        elif r.signature in ITEM_SIGS:
            if 'items' in out:
                out['items'].append(f"player.additem {fid} 1    ; {edid}")
        elif r.signature == 'SPEL':
            if 'spells' in out:
                out['spells'].append(f"player.addspell {fid}    ; {edid}")
        elif r.signature == 'CELL' and edid:
            if 'cells' in out:
                out['cells'].append(f"coc {edid}")
        elif r.signature == 'NPC_':
            if 'npcs' in out:
                out['npcs'].append(f"player.placeatme {fid} 1    ; {edid}")
        elif r.signature in ('ENCH', 'MGEF'):
            if 'effects' in out:
                out['effects'].append(f"{r.signature} {fid} {edid}")
    out['note'] = ("Replace XX with the plugin's load-order index in hex; `help <EditorID> 0` in the console prints "
                   "the full form ID. Effects are listed for reference only.")
    return out


SKSE_CALLS = re.compile(r'\b(RegisterForMenu|RegisterForKey|RegisterForModEvent|SendModEvent|GetDialogueTarget|RegisterForAnimationEvent|'
                        r'StringUtil|UICallback|ModEvent|NetImmerse|WornObject)\b')
PAPYRUS_LOG = r"Documents\My Games\Skyrim Special Edition\Logs\Script\Papyrus.0.log"


def enchantment_test_plan(path, item):
    """Checklist for trying an enchanted item (or a bare ENCH) in game, built from the plugin's own records:
    commands to get and equip it, each effect with its script and description, SKSE use, where to look."""
    from esplib.vmad import VmadData
    path = Path(path)
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    recs = {r.form_id.value: r for r in all_records(plugin) if hi(r.form_id.value) == own}
    by_edid = {(r.editor_id or '').lower(): r for r in recs.values()}
    rec = by_edid.get(item.lower())
    if rec is None:
        raise ValueError(f"{item!r} is not a record of {path.name}")
    holder = None
    if rec.signature == 'ENCH':
        ench = rec
    else:
        eitm = rec.get_subrecord('EITM')
        if eitm is None:
            raise ValueError(f"{item} has no enchantment (EITM); use enchant_item first")
        holder = rec
        ench = recs.get(struct.unpack_from('<I', bytes(eitm.data))[0])
        if ench is None:
            raise ValueError(f"{item}'s enchantment is not in {path.name}")
    ids = [struct.unpack_from('<I', bytes(s.data))[0] for s in ench.subrecords if s.signature == 'EFID']
    stats = [bytes(s.data) for s in ench.subrecords if s.signature == 'EFIT']
    effects, scripts, skse, spells = [], [], [], []
    source_dir = path.parent / 'Scripts' / 'Source'
    for i, fid in enumerate(ids):
        mag = struct.unpack_from('<f', stats[i])[0] if i < len(stats) else 0.0
        mgef = recs.get(fid)
        if mgef is None:
            effects.append({'effect': f"{fid:08X}", 'magnitude': mag, 'origin': 'vanilla or another plugin'})
            continue
        dnam = mgef.get_subrecord('DNAM')
        entry = {'effect': mgef.editor_id, 'name': mgef.full_name, 'magnitude': mag,
                 'description': bytes(dnam.data).rstrip(b'\0').decode('cp1252', 'replace') if dnam else ''}
        try:
            vm = VmadData.from_record(mgef) if mgef.get_subrecord('VMAD') is not None else None
        except Exception:
            vm = None
        for s in (vm.scripts if vm else []):
            entry.setdefault('scripts', []).append(s.name)
            scripts.append(s.name)
            psc = source_dir / f"{s.name}.psc"
            if psc.is_file():
                text = psc.read_text(encoding='cp1252', errors='replace')
                skse += [f"{s.name}: {c}" for c in sorted(set(SKSE_CALLS.findall(text)))]
                spells += [f"{int(m, 16):06X}" for m in re.findall(r'GetFormFromFile\(\s*(0x[0-9A-Fa-f]+)', text)]
        effects.append(entry)
    target = holder or ench
    name = target.editor_id
    commands = ([f"player.additem XX{holder.form_id.value & 0xFFFFFF:06X} 1    ; {name}",
                 f"player.equipitem XX{holder.form_id.value & 0xFFFFFF:06X}"] if holder else [])
    plan = {
        'item': name, 'enchantment': ench.editor_id, 'effects': effects, 'commands': commands,
        'expect': ["The item's tooltip lists each effect above (script effects show their description).",
                   "Constant effects start on equip and stop on unequip: unequip and re-equip after any change.",
                   "Script effects log to the Papyrus log; no entry means the script never started."],
        'where_to_look': [f"%USERPROFILE%\\{PAPYRUS_LOG} (needs bEnableLogging=1 and bEnableTrace=1 under [Papyrus] "
                          "in Skyrim.ini)", "Magic menu > Active Effects for effects and ability spells."],
    }
    if scripts:
        first = scripts[0]
        plan['debug_tip'] = (f'To see it start without the log, add Debug.Notification("{first} running") to '
                             f'OnEffectStart (edit_script find/replace), reinstall, and remove it afterwards; '
                             f'or Debug.Trace("{first}: started") and search the Papyrus log for it.')
    if spells:
        plan['ability_spells_looked_up'] = sorted(set(spells))
        plan['expect'].append("Ability spells the script adds or removes appear and disappear in Active Effects "
                              "as their conditions change.")
    if skse:
        plan['requires_skse'] = skse
        plan['expect'].append("Start the game through skse64_loader.exe or these scripts do nothing.")
    return plan


def check_script(source):
    """Compile source in a scratch folder (nothing is written next to a plugin). Returns ok and the compiler output."""
    import tempfile
    from tools.papyrus.papyrus_build import CompileError, compile_script
    m = re.search(r'^\s*scriptname\s+([A-Za-z][A-Za-z0-9_]*)', source, re.I | re.M)
    if not m:
        raise ValueError("source must start with 'Scriptname <name> ...'")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            compile_script(m.group(1), source, tmp)
        except CompileError as e:
            return {'script': m.group(1), 'ok': False, 'output': str(e)}
    return {'script': m.group(1), 'ok': True, 'output': ''}


def quest_console_commands(path, quest):
    """Console commands for checking and driving a quest in the plugin."""
    plugin = Plugin.load(str(path))
    q = next((r for r in all_records(plugin) if r.signature == 'QUST'
              and (r.editor_id or '').lower() == quest.lower()), None)
    if q is None:
        raise ValueError(f"quest {quest!r} not found in {Path(path).name}")
    edid = q.editor_id
    stages, objectives, aliases = [], [], []
    for s in q.subrecords:
        d = bytes(s.data)
        if s.signature == 'INDX':
            stages.append(struct.unpack_from('<H', d)[0])
        elif s.signature == 'QOBJ':
            objectives.append(struct.unpack_from('<H', d)[0])
        elif s.signature == 'ALID':
            aliases.append(d.rstrip(b'\0').decode('latin-1'))
    return {
        'quest': edid,
        'check': [f"sqv {edid}", f"getstage {edid}", f"getquestcompleted {edid}"],
        'start': [f"startquest {edid}", f"resetquest {edid}"],
        'advance': [f"setstage {edid} {n}" for n in sorted(set(stages))],
        'objectives': [f"setobjectivedisplayed {edid} {n} 1" for n in sorted(set(objectives))],
        'complete': [f"completequest {edid}"],
        'aliases': aliases,
        'tips': ["sqv lists stages, aliases and what each alias is filled with; an empty alias means no marker.",
                 "If dialogue is missing, run getstage on the prerequisite quest and on this one.",
                 "setstage skips scripts' side effects (items given by dialogue), so test the real flow once."],
    }
