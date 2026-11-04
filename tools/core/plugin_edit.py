"""Edit existing plugins: load/create a target ESP, add overrides, and apply spot edits to subrecords."""
import re
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.record import GroupRecord
from esplib.utils import FormID
from esplib.vmad import VmadData

OFFICIAL = {'skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm', 'dragonborn.esm', 'skyrim.esl'}
# Records that live inside nested groups (cells, topics); overriding them needs the group chain.
NESTED = {'CELL', 'REFR', 'ACHR', 'ACRE', 'PGRE', 'PMIS', 'PARW', 'PBEA', 'PFLA', 'PHZD', 'PCON',
          'NAVM', 'LAND', 'INFO'}
REF_SIGS = {'NAME', 'XOWN', 'XLCN', 'XTNM', 'XEZN', 'XMRK'}
LOCALIZED_SIGS = {'FULL', 'SHRT', 'DESC', 'NNAM', 'ITXT', 'NAM1'}
INT_KINDS = {'uint8': 'B', 'int8': 'b', 'uint16': 'H', 'int16': 'h', 'uint32': 'I', 'int32': 'i', 'float': 'f'}
VALUE_KINDS = set(INT_KINDS) | {'string', 'form', 'hex'}
EDIT_KEYS = {'sig', 'index', 'offset', 'append', 'field', 'value'} | VALUE_KINDS | {'form_type'}


class EditError(ValueError):
    """Raised for bad edit requests: unknown plugin, ambiguous record, unencodable value, unsafe master change."""


def resolve_target_path(name, output_dir, data_dir, must_exist=False):
    """Plugin path for a bare name (output folder first, then Data) or an absolute path."""
    p = Path(name)
    if p.is_absolute():
        cand = p
    else:
        if p.name != name:
            raise EditError("plugin name must be a file name or an absolute path")
        cands = [Path(output_dir) / name] + ([Path(data_dir) / name] if data_dir else [])
        cand = next((c for c in cands if c.exists()), cands[0])
    if cand.suffix.lower() not in ('.esp', '.esm', '.esl'):
        cand = cand.with_name(cand.name + '.esp')
    if cand.name.lower() in OFFICIAL:
        raise EditError(f"{cand.name} is an official plugin; edit it through a patch plugin instead")
    if must_exist and not cand.exists():
        raise FileNotFoundError(f"plugin not found: {cand}")
    return cand


def vmad_script_names(plugin):
    """Names of every Papyrus script a plugin's records attach (scripts, fragment scripts, alias scripts)."""
    names = set()

    def walk(items):
        for it in items:
            if isinstance(it, GroupRecord):
                yield from walk(it.records)
            else:
                yield it

    for r in walk(plugin.records):
        v = r.get_subrecord('VMAD')
        if v is None:
            continue
        data = bytes(v.data)
        try:
            vm = VmadData.parse(data, r.signature)
            names.update(s.name for s in vm.scripts)
            for a in vm.alias_scripts:
                names.update(s.name for s in a.scripts)
            fd = vm.fragment_data
            if fd is not None:
                names.add(fd.filename)
                names.update(f.script_name for f in fd.fragments)
        except Exception:
            names.update(n.decode() for n in re.findall(rb'[A-Za-z][A-Za-z0-9_]{3,}', data))
    names.discard('')
    return names


def plugin_files(path, regen_seq=True):
    """The plugin plus every loose file it needs (FaceGen, scripts, regenerated SEQ), as absolute paths."""
    path = Path(path)
    base = path.parent
    files = [path]
    for top in ('Meshes', 'Textures'):
        root = base / top
        if root.is_dir():
            files += [f for f in root.rglob('*')
                      if f.is_file() and f.suffix.lower() != '.bak'
                      and path.name.lower() in (p.lower() for p in f.relative_to(base).parts)]
    stem = re.sub(r'[^A-Za-z0-9]', '', path.stem)[:16]
    scripts = {}
    for pattern in (f"TIF__{stem}_*.pex", f"QF__{stem}_*.pex", f"{stem}_*.pex"):
        for f in (base / 'Scripts').glob(pattern):
            scripts[f.name.lower()] = f
    # Any script the plugin attaches that was compiled here ships too, whatever it is called.
    if (base / 'Scripts').is_dir():
        for name in vmad_script_names(Plugin.load(str(path))):
            f = base / 'Scripts' / f"{name}.pex"
            if f.is_file():
                scripts.setdefault(f.name.lower(), f)
    files += list(scripts.values())
    seq = write_seq(path) if regen_seq else path.parent / 'SEQ' / f"{path.stem}.seq"
    if regen_seq and seq:
        files.append(seq)
    elif not regen_seq and seq.exists():
        files.append(seq)
    return files


def package_plugin(path, dest=None):
    """Zip a plugin and its loose FaceGen files (Data-relative paths) for Vortex or manual install."""
    import zipfile
    path = Path(path)
    base = path.parent
    dest = Path(dest) if dest else base / 'packages' / f"{path.stem}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = plugin_files(path)
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(base).as_posix())
    return {'zip': str(dest), 'files': [f.relative_to(base).as_posix() for f in files]}


def write_seq(path):
    """Write SEQ/<plugin>.seq listing the plugin's start-game-enabled quests (needed to start them on existing saves)."""
    import struct
    from esplib import Plugin
    plugin = Plugin.load(str(path))
    own = len(plugin.header.masters)
    ids = []
    for r in plugin.records:
        dnam = r.get_subrecord('DNAM') if r.signature == 'QUST' else None
        if dnam is not None and len(dnam.data) >= 2 and struct.unpack_from('<H', bytes(dnam.data))[0] & 1 \
                and hi(r.form_id.value) == own:
            ids.append(r.form_id.value & 0xFFFFFF)
    seq = path.parent / 'SEQ' / f"{path.stem}.seq"
    if not ids:
        seq.unlink(missing_ok=True)
        return None
    seq.parent.mkdir(exist_ok=True)
    seq.write_bytes(b''.join(struct.pack('<I', i) for i in sorted(ids)))
    return seq


def new_plugin(path):
    """Empty Skyrim SE plugin. esplib defaults to header version 1.71, which makes the game ignore the biped
    slots and armor addons of records made here (circlets show no model and stack), so write 1.70 like the CK."""
    plugin = Plugin.new_plugin(path, masters=[], game='tes5')
    plugin.header.version = 1.7
    return plugin


def hi(value):
    """Top byte of a form ID: the master index within a plugin."""
    return (value >> 24) & 0xFF


class Target:
    """A plugin being written: an existing file loaded in full, or a new one."""

    def __init__(self, model, names, path, create=False):
        self.m = model
        self.names = names
        self.path = Path(path)
        self.existed = self.path.exists()
        if self.existed:
            self.plugin = Plugin.load(str(self.path))
        elif create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.plugin = new_plugin(self.path)
        else:
            raise FileNotFoundError(f"plugin not found: {self.path}")
        self.slot_names = {slot: name for name, slot in model.slots.items()}

    @property
    def name(self):
        return self.path.name

    def ensure_master(self, name):
        """Add a master if missing. Records that used the old own-plugin index are renumbered to the new one."""
        pl = self.plugin
        low = name.lower()
        if low == self.name.lower() or low in [m.lower() for m in pl.header.masters]:
            return
        n = len(pl.header.masters)
        if pl.records:
            self.check_renumber_safe(n)
        pl.header.masters.append(self.names.get(low, name))
        pl.header.master_sizes.append(0)
        if not pl.records:
            return

        def shift(fid):
            return ((n + 1) << 24) | (fid & 0xFFFFFF) if hi(fid) == n else fid

        for rec in pl.records:
            if hi(rec.form_id.value) == n:
                rec.form_id = FormID(shift(rec.form_id.value))
                rec.modified = True
            pl._remap_subrecord_formids(rec, remap_fn=shift)
            if rec.schema is None:
                for sr in rec.subrecords:
                    if sr.signature in REF_SIGS and sr.size == 4:
                        v = struct.unpack('<I', sr.data)[0]
                        sr.data = bytearray(struct.pack('<I', shift(v)))
                        sr.modified = True

        def walk(group):
            if group.group_type in (1, 6, 7, 8, 9) and isinstance(group.label, int):
                group.label = shift(group.label)
            for item in group.records:
                if isinstance(item, GroupRecord):
                    walk(item)

        for g in pl.groups:
            walk(g)
        pl._form_id_index = {r.form_id.value: r for r in pl.records}
        pl.modified = True

    def check_renumber_safe(self, n):
        """Adding a master moves the plugin's own file index; refuse if records without a schema
        might hold self-references we cannot rewrite."""
        risky = set()
        for rec in self.plugin.records:
            if rec.schema is not None:
                continue
            for sr in rec.subrecords:
                if sr.signature in ('EDID', 'FULL', 'DESC', 'MODL', 'NAM1', 'VMAD') or sr.signature in REF_SIGS:
                    continue
                data = bytes(sr.data)
                for off in range(0, len(data) - 3, 4):
                    v = struct.unpack_from('<I', data, off)[0]
                    if hi(v) == n and (v & 0xFFFFFF) >= 0x800:
                        risky.add(rec.signature)
        if risky:
            raise EditError(
                f"adding a master would renumber this plugin's own FormIDs, but {sorted(risky)} records "
                f"have no schema and may reference them. Add the master in xEdit/CK first.")

    def local(self, fid):
        """Model (absolute) form ID -> form ID value in this plugin."""
        if not fid:
            return 0
        name = self.slot_names.get(fid >> 24)
        if name is None:
            raise EditError(f"form ID {fid:08X} is from an unknown plugin")
        if name == self.name.lower():
            return (len(self.plugin.header.masters) << 24) | (fid & 0xFFFFFF)
        self.ensure_master(name)
        masters = [m.lower() for m in self.plugin.header.masters]
        return (masters.index(name) << 24) | (fid & 0xFFFFFF)

    def origin(self, rec):
        """(plugin name lowercase, object id) identifying which game record a record is."""
        masters = self.plugin.header.masters
        idx = hi(rec.form_id.value)
        name = masters[idx] if idx < len(masters) else self.name
        return name.lower(), rec.form_id.value & 0xFFFFFF

    def find_own(self, identifier, sigs=None):
        """Record in this plugin by editor ID, or by form ID (hex, object id match)."""
        q = str(identifier).strip()
        raw = None
        if re.fullmatch(r'(0x)?[0-9a-fA-F]{6,8}', q) and (q.lower().startswith('0x') or len(q) == 8):
            raw = int(q, 16) & 0xFFFFFF
        hits = []
        for r in self.plugin.records:
            if sigs and r.signature not in sigs:
                continue
            if (raw is not None and (r.form_id.value & 0xFFFFFF) == raw) or \
                    (r.editor_id or '').lower() == q.lower():
                hits.append(r)
        if len(hits) > 1:
            raise EditError(f"{q!r} matches several records in {self.name}: "
                            + ', '.join(f"{r.signature} {r.editor_id}" for r in hits[:6]))
        return hits[0] if hits else None

    def find_matching(self, abs_fid):
        """This plugin's override of the record with the given model (absolute) form ID, or None."""
        slot = self.slot_names.get(abs_fid >> 24)
        key = (slot, abs_fid & 0xFFFFFF)
        return next((r for r in self.plugin.records if self.origin(r) == key), None)

    def override(self, src):
        """Add an override of a record from another plugin and return it."""
        pl = self.plugin
        if src.signature in NESTED:
            raise EditError(f"{src.signature} records live in nested groups; overriding them is not supported")
        if pl.is_localized:
            raise EditError(f"{self.name} is localized; overrides of other plugins' records need a "
                            f"non-localized patch plugin")
        sp = src.plugin
        for master in sp.header.masters:
            self.ensure_master(master)
        self.ensure_master(sp.file_path.name)
        rec = src.copy()
        rec.form_id = FormID(pl.remap_formid(src.form_id.value, sp))
        pl._remap_subrecord_formids(rec, sp)
        pl._delocalize_strings(rec, sp, None)
        pl.add_record(rec)
        return rec

    def save(self):
        self.plugin.save(self.path)


# -- generic subrecord edits ------------------------------------------------------------------

def encode_value(item, target, resolver, all_sigs):
    """(kind, bytes) for an edit item holding exactly one value key; 'form' values become local little-endian form IDs, 'string' is NUL-terminated cp1252."""
    kinds = [k for k in VALUE_KINDS if k in item]
    if len(kinds) != 1:
        raise EditError(f"edit {item.get('sig')}: give exactly one of {sorted(VALUE_KINDS)}")
    kind = kinds[0]
    v = item[kind]
    try:
        if kind == 'string':
            return kind, str(v).encode('cp1252') + b'\0'
        if kind == 'hex':
            return kind, bytes.fromhex(str(v).replace(' ', ''))
        if kind == 'form':
            sigs = item.get('form_type')
            sigs = [sigs] if isinstance(sigs, str) else (sigs or all_sigs)
            fid, _ = resolver.lookup(v, sigs)
            return kind, struct.pack('<I', target.local(fid))
        return kind, struct.pack('<' + INT_KINDS[kind], v)
    except (struct.error, ValueError, UnicodeEncodeError) as e:
        if isinstance(e, EditError):
            raise
        raise EditError(f"edit {item.get('sig')}: bad {kind} value {v!r}: {e}")


def apply_edits(rec, target, resolver, all_sigs, edits=None, remove=None):
    """Apply subrecord edits/removals to a record. Returns a list of human-readable changes."""
    log = []
    for item in remove or []:
        sig = item if isinstance(item, str) else item.get('sig')
        idx = None if isinstance(item, str) else item.get('index')
        subs = [s for s in rec.subrecords if s.signature == sig]
        if idx is not None:
            subs = subs[idx:idx + 1]
        for s in subs:
            rec.subrecords.remove(s)
        rec.modified = True
        log.append(f"removed {len(subs)} {sig}")
    for item in edits or []:
        if 'field' in item:
            from tools.records.fields import encode_field
            if set(item) - {'field', 'value', 'index'} or 'value' not in item:
                raise EditError(f"edit {item}: a named field edit takes only 'field', 'value' and 'index'")
            try:
                sub, offset, raw = encode_field(rec.signature, item['field'], item['value'])
            except ValueError as e:
                raise EditError(str(e))
            item = {'sig': sub, 'offset': offset, 'hex': raw.hex(), 'index': item.get('index', 0)}
        bad = [k for k in item if k not in EDIT_KEYS]
        if bad or 'sig' not in item:
            raise EditError(f"edit {item}: needs 'sig' and only keys {sorted(EDIT_KEYS)}")
        sig = item['sig']
        kind, data = encode_value(item, target, resolver, all_sigs)
        if target.plugin.is_localized and sig in LOCALIZED_SIGS and kind == 'string':
            raise EditError(f"{target.name} is localized; {sig} text lives in string tables and cannot be "
                            f"edited here")
        subs = [s for s in rec.subrecords if s.signature == sig]
        idx = item.get('index', 0)
        offset = item.get('offset')
        if item.get('append') or not subs:
            if offset:
                data = bytes(offset) + data
            rec.add_subrecord(sig, data)
            log.append(f"added {sig}")
        else:
            if idx >= len(subs):
                raise EditError(f"{sig}[{idx}] does not exist ({len(subs)} present)")
            sr = subs[idx]
            if offset is not None:
                buf = bytearray(sr.data)
                if len(buf) < offset + len(data):
                    buf.extend(bytes(offset + len(data) - len(buf)))
                buf[offset:offset + len(data)] = data
                data = bytes(buf)
            sr.data = bytearray(data)
            sr.modified = True
            log.append(f"set {sig}[{idx}]" + (f" @+{offset}" if offset is not None else ''))
        rec.modified = True
    return log


def describe_subrecords(rec, model, limit=96):
    """Raw view of a record's subrecords with light decoding."""
    out, counts = [], {}
    for sr in rec.subrecords:
        i = counts[sr.signature] = counts.get(sr.signature, -1) + 1
        data = bytes(sr.data)
        d = {'sig': sr.signature, 'index': i, 'size': len(data), 'hex': data[:limit].hex()}
        if len(data) > limit:
            d['hex'] += '...'
        if data.endswith(b'\0') and len(data) > 1 and all(32 <= c < 127 for c in data[:-1]):
            d['text'] = data[:-1].decode('ascii')
        elif len(data) == 4:
            v = struct.unpack('<I', data)[0]
            d['uint32'] = v
            d['float'] = struct.unpack('<f', data)[0]
            ref = model.get(model.absid(rec.plugin, v)) if v else None
            if ref is not None:
                d['form'] = f"{ref.signature} {ref.editor_id or ref.full_name or ''}".strip()
        from tools.records.fields import decode_fields
        named = decode_fields(rec.signature, sr.signature, data)
        if named:
            d['fields'] = named
        out.append(d)
    return out


class RecordEditor:
    """Reads and edits raw records by editor ID or form ID, writing results into a target plugin."""
    def __init__(self, model, resolver, output_dir, data_dir):
        self.m, self.resolver = model, resolver
        self.names = {p.file_path.name.lower(): p.file_path.name for p in model.plugins}
        self.output_dir, self.data_dir = Path(output_dir), data_dir

    def all_sigs(self):
        return list(self.m.by_sig.keys())

    def path(self, name, must_exist=False):
        return resolve_target_path(name, self.output_dir, self.data_dir, must_exist)

    def get_record(self, identifier, plugin=None, signature=None, full=False):
        """Raw dump of a record: from the named plugin's own records if given, else the loaded load order."""
        sigs = [signature.upper()] if signature else self.all_sigs()
        if plugin:
            t = Target(self.m, self.names, self.path(plugin, must_exist=True))
            rec = t.find_own(identifier, sigs if signature else None)
            if rec is None:
                raise EditError(f"{identifier!r} not found in {t.name}")
            source = t.name
        else:
            fid, rec = self.resolver.lookup(identifier, sigs)
            source = rec.plugin.file_path.name
        return {'signature': rec.signature, 'editor_id': rec.editor_id, 'name': rec.full_name,
                'plugin': source, 'form_id': f"{rec.form_id.value:08X}",
                'flags': f"0x{int(rec.flags):08X}", 'subrecords': describe_subrecords(rec, self.m, 1 << 30 if full else 96)}

    def locate(self, t, identifier, sigs=None):
        """Record to edit in target t: its own copy, an existing override, or a new override."""
        own = t.find_own(identifier, sigs)
        if own is not None:
            return own, 'edited in place'
        fid, src = self.resolver.lookup(identifier, sigs or self.all_sigs())
        existing = t.find_matching(fid)
        if existing is not None:
            return existing, 'edited existing override'
        return t.override(src), f"override of {src.plugin.file_path.name} record added"

    def edit_record(self, identifier, target_plugin, edits=None, remove=None, editor_id=None,
                    record_flags=None, signature=None):
        """Edit a record in the target plugin (created if missing), overriding it first when it lives elsewhere; saves and returns a change report."""
        path = self.path(target_plugin)
        t = Target(self.m, self.names, path, create=True)
        sigs = [signature.upper()] if signature else None
        rec, how = self.locate(t, identifier, sigs)
        log = apply_edits(rec, t, self.resolver, self.all_sigs(), edits, remove)
        if editor_id:
            log += apply_edits(rec, t, self.resolver, self.all_sigs(),
                               [{'sig': 'EDID', 'string': editor_id}])
        if record_flags is not None:
            rec.flags = type(rec.flags)(record_flags) if not isinstance(rec.flags, int) else record_flags
            log.append(f"record flags = 0x{int(record_flags):08X}")
        t.save()
        return {'path': str(t.path), 'created_plugin': not t.existed, 'record': how,
                'signature': rec.signature, 'editor_id': rec.editor_id,
                'form_id': f"{rec.form_id.value:08X}", 'changes': log,
                'masters': list(t.plugin.header.masters)}
