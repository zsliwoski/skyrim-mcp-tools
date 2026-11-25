"""Save-compatibility check: compare a plugin with a released baseline and say what would break existing saves."""
import shutil
from pathlib import Path

from esplib import Plugin
from esplib.vmad import VmadData

from tools.core import idregistry
from tools.core.plugin_edit import hi
from tools.records.maintenance import all_records

BREAKING, RISKY = 'breaking', 'risky'


def baselines_dir(plugin_path):
    return idregistry.project_dir(plugin_path) / 'baselines'


def list_baselines(plugin_path):
    """Released baselines, oldest first (by modification time)."""
    folder = baselines_dir(plugin_path)
    files = sorted(folder.glob('*.es[mpl]'), key=lambda f: f.stat().st_mtime) if folder.is_dir() else []
    return [f.stem for f in files]


def baseline_path(plugin_path, version='latest'):
    plugin_path = Path(plugin_path)
    found = list_baselines(plugin_path)
    if not found:
        return None
    name = found[-1] if version in (None, 'latest') else version
    path = baselines_dir(plugin_path) / f"{name}{plugin_path.suffix}"
    if not path.is_file():
        raise ValueError(f"no baseline {version!r}; have {', '.join(found)}")
    return path


def _own(plugin):
    own = len(plugin.header.masters)
    return {rec.form_id.value & 0xFFFFFF: rec for rec in all_records(plugin) if hi(rec.form_id.value) == own}


def _scripts(rec):
    """{script name: {property: type}} the record attaches (empty if none or unreadable)."""
    sub = rec.get_subrecord('VMAD')
    if sub is None:
        return {}
    try:
        vm = VmadData.parse(bytes(sub.data), rec.signature)
    except Exception:
        return {}
    scripts = list(vm.scripts) + [s for a in vm.alias_scripts for s in a.scripts]
    return {s.name: {p.name: p.type for p in getattr(s, 'properties', [])} for s in scripts}


def compare(baseline, current, retired=()):
    """Findings between two loaded plugins: [{level, kind, id, editor_id, detail}]."""
    out = []
    retired = {str(r).upper().lstrip('0') for r in retired}
    old, new = _own(baseline), _own(current)

    def add(level, kind, oid, rec, detail):
        out.append({'level': level, 'kind': kind, 'id': f"{oid:06X}", 'editor_id': (rec.editor_id or '') if rec else '',
                    'detail': detail})

    for oid, rec in old.items():
        cur = new.get(oid)
        if cur is None:
            if f"{oid:X}" in retired:
                add(RISKY, 'retired_removed', oid, rec,
                    f"{rec.signature} was removed on purpose; saves holding it lose it")
            else:
                add(BREAKING, 'removed', oid, rec, f"{rec.signature} is gone; saves that reference it can break")
            continue
        if cur.signature != rec.signature:
            add(BREAKING, 'type_changed', oid, rec, f"{rec.signature} became {cur.signature}")
            continue
        if rec.editor_id and cur.editor_id != rec.editor_id:
            add(RISKY, 'renamed', oid, rec, f"editor ID {rec.editor_id} became {cur.editor_id}; scripts and "
                                            "console commands using the old name stop working")
        was, now = _scripts(rec), _scripts(cur)
        for name, props in was.items():
            if name not in now:
                add(BREAKING, 'script_removed', oid, rec, f"script {name} is no longer attached")
                continue
            for prop, kind in props.items():
                if prop not in now[name]:
                    add(RISKY, 'property_removed', oid, rec, f"{name}.{prop} was removed; saved values are dropped")
                elif now[name][prop] != kind:
                    add(BREAKING, 'property_retyped', oid, rec, f"{name}.{prop} changed type")
    top = max([baseline.header.next_object_id] + [oid + 1 for oid in old])
    for oid, rec in new.items():
        if oid not in old and oid < top:
            add(BREAKING, 'id_reused', oid, rec, "an ID below the baseline's highest was used for a new record")
    base_masters, cur_masters = list(baseline.header.masters), list(current.header.masters)
    for m in base_masters:
        if m not in cur_masters:
            add(BREAKING, 'master_removed', 0, None, f"master {m} was removed")
    kept = [m for m in base_masters if m in cur_masters]
    if kept != [m for m in cur_masters if m in kept]:
        add(RISKY, 'master_reordered', 0, None, "the master list order changed")
    return out


def check(plugin_path, baseline='latest'):
    """Compare the plugin with a baseline. Returns the findings with a verdict: ok, risky or breaking."""
    plugin_path = Path(plugin_path)
    base = baseline_path(plugin_path, baseline)
    if base is None:
        return {'baseline': None, 'verdict': 'no_baseline', 'findings': [],
                'note': 'no released baseline yet; use release_plugin to create one'}
    retired = idregistry.load(plugin_path).get('retired', [])
    findings = compare(Plugin.load(str(base)), Plugin.load(str(plugin_path)), retired)
    levels = {f['level'] for f in findings}
    verdict = 'breaking' if BREAKING in levels else 'risky' if RISKY in levels else 'ok'
    return {'baseline': base.stem, 'verdict': verdict, 'findings': findings}


def release(plugin_path, version, force=False):
    """Record the plugin as a released baseline. Refuses a breaking change unless force is set."""
    plugin_path = Path(plugin_path)
    if not version or not version.replace('.', '').replace('-', '').replace('_', '').isalnum():
        raise ValueError("version may contain only letters, digits, dots, dashes and underscores")
    folder = baselines_dir(plugin_path)
    if (folder / f"{version}{plugin_path.suffix}").exists():
        raise ValueError(f"baseline {version} already exists")
    result = check(plugin_path)
    if result['verdict'] == 'breaking' and not force:
        raise ValueError(f"{len(result['findings'])} findings against baseline {result['baseline']} include "
                         "breaking changes; fix them or pass force")
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plugin_path, folder / f"{version}{plugin_path.suffix}")
    synced = idregistry.sync(plugin_path)
    return {'version': version, 'previous': result['baseline'], 'verdict': result['verdict'],
            'findings': len(result['findings']), 'registry': synced}
