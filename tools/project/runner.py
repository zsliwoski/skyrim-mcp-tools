"""Migration runner: ordered Python patches under output/<Plugin>/migrations, applied once each and recorded in a ledger."""
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.core import idregistry
from tools.project import compat

NAME = re.compile(r'^(\d{4})_[A-Za-z0-9_]+\.py$')


def migrations_dir(plugin_path):
    return idregistry.project_dir(plugin_path) / 'migrations'


def ledger_path(plugin_path):
    return migrations_dir(plugin_path) / 'ledger.json'


def load_ledger(plugin_path):
    path = ledger_path(plugin_path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'applied': []}


def _save_ledger(plugin_path, ledger):
    ledger_path(plugin_path).write_text(json.dumps(ledger, indent=2) + '\n', encoding='utf-8')


def _digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def discover(plugin_path):
    folder = migrations_dir(plugin_path)
    files = sorted(f for f in folder.glob('*.py') if NAME.match(f.name)) if folder.is_dir() else []
    numbers = [NAME.match(f.name).group(1) for f in files]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two migrations share a number; numbers must be unique")
    return files


def status(plugin_path):
    """Applied, pending and tampered migrations, plus the registry drift and the latest baseline."""
    plugin_path = Path(plugin_path)
    files = {f.name: f for f in discover(plugin_path)}
    applied = load_ledger(plugin_path)['applied']
    done = {a['name'] for a in applied}
    changed = [a['name'] for a in applied if a['name'] in files and _digest(files[a['name']]) != a['sha256']]
    missing = [a['name'] for a in applied if a['name'] not in files]
    pending = [n for n in files if n not in done]
    out = {'applied': [a['name'] for a in applied], 'pending': pending,
           'edited_after_applied': changed, 'missing_files': missing,
           'baselines': compat.list_baselines(plugin_path)}
    if plugin_path.is_file() and idregistry.registry_path(plugin_path).exists():
        out['id_drift'] = idregistry.drift(plugin_path)
    return out


class Context:
    """What a migration's apply(ctx) receives: tools bound to the plugin, by name."""

    def __init__(self, server, plugin_path):
        self.server, self.path, self.plugin = server, Path(plugin_path), Path(plugin_path).name

    def tool(self, name, *args, **kwargs):
        """Call a server tool with the plugin file name as its first argument; raise if it reports an error."""
        result = getattr(self.server, name)(self.plugin, *args, **kwargs)
        if isinstance(result, dict) and result.get('error'):
            raise RuntimeError(f"{name}: {result['error']}")
        return result

    def call(self, name, *args, **kwargs):
        """Call a server tool without adding the plugin argument."""
        result = getattr(self.server, name)(*args, **kwargs)
        if isinstance(result, dict) and result.get('error'):
            raise RuntimeError(f"{name}: {result['error']}")
        return result

    def __getattr__(self, name):
        if name.startswith('_') or not hasattr(self.server, name):
            raise AttributeError(name)
        return lambda *a, **k: self.tool(name, *a, **k)


def _load(file):
    spec = importlib.util.spec_from_file_location(f"migration_{file.stem}", file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, 'apply', None)):
        raise ValueError(f"{file.name} has no apply(ctx)")
    return mod


def run(plugin_path, server, dry_run=False, limit=None):
    """Apply pending migrations in order. Each is snapshotted first; a failure restores the snapshot and stops."""
    from tools.records.maintenance import restore, snapshot
    plugin_path = Path(plugin_path)
    st = status(plugin_path)
    if st['edited_after_applied'] or st['missing_files']:
        raise ValueError("applied migrations were edited or removed (" +
                         ", ".join(st['edited_after_applied'] + st['missing_files']) +
                         "); add a new migration instead of changing history")
    pending = st['pending'][:limit] if limit else st['pending']
    if dry_run or not pending:
        return {'applied': [], 'would_apply': pending, 'status': st}
    files = {f.name: f for f in discover(plugin_path)}
    ledger = load_ledger(plugin_path)
    done = []
    for name in pending:
        mod = _load(files[name])
        label = f"pre_{name[:-3]}"
        had = plugin_path.is_file()
        if had:
            snapshot(plugin_path, label)
        try:
            mod.apply(Context(server, plugin_path))
        except Exception as e:
            if had:
                restore(plugin_path, label)
                server._invalidate()
            return {'applied': done, 'failed': name, 'error': f"{type(e).__name__}: {e}",
                    'note': 'plugin restored to its state before the failed migration' if had else ''}
        server._models.clear()
        ledger['applied'].append({'name': name, 'sha256': _digest(files[name]),
                                  'at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        _save_ledger(plugin_path, ledger)
        done.append(name)
    result = {'applied': done}
    if idregistry.registry_path(plugin_path).exists() or done:
        result['registry'] = idregistry.sync(plugin_path)
    result['compat'] = compat.check(plugin_path)
    return result
