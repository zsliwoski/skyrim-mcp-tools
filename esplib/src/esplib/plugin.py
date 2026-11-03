"""Main Plugin class for reading and modifying Bethesda plugin files."""

import logging
import struct
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Union, Dict, Iterator, Any
from .record import Record, SubRecord, GroupRecord
from .utils import (FormID, BaseFormID, AbsoluteFormID, BinaryReader,
                    BinaryWriter, is_readable_file)
from .exceptions import PluginError, ParseError, ValidationError


_log = logging.getLogger(__name__)


class PluginHeader:
    """Represents the TES4 header record of a plugin."""

    def __init__(self):
        self.version: float = 1.0
        self.num_records: int = 0
        self.next_object_id: int = 0x800
        self.author: str = ""
        self.description: str = ""
        self.masters: List[str] = []
        self.master_sizes: List[int] = []
        self.is_esm: bool = False
        self.is_esl: bool = False
        self.is_localized: bool = False
        self.override_records: List[FormID] = []
        self.interior_cell_count: int = 0          # Starfield's INCC
        # Preserve the raw TES4 record for round-trip fidelity
        self._raw_record: Optional[Record] = None

    @classmethod
    def from_record(cls, record: Record) -> 'PluginHeader':
        header = cls()
        header._raw_record = record

        # Parse header flags
        header.is_esm = record.flags.Master
        header.is_esl = record.flags.Light
        header.is_localized = record.flags.Localized

        for subrecord in record.subrecords:
            if subrecord.signature == "HEDR":
                if subrecord.size >= 12:
                    reader = subrecord.get_reader()
                    header.version = reader.read_float()
                    header.num_records = reader.read_uint32()
                    header.next_object_id = reader.read_uint32()

            elif subrecord.signature == "CNAM":
                header.author = subrecord.get_string()

            elif subrecord.signature == "SNAM":
                header.description = subrecord.get_string()

            elif subrecord.signature == "MAST":
                header.masters.append(subrecord.get_string())

            elif subrecord.signature == "DATA":
                if subrecord.size >= 8:
                    header.master_sizes.append(
                        struct.unpack('<Q', subrecord.data[:8])[0])

            elif subrecord.signature == "ONAM":
                header.override_records = subrecord.get_form_id_array()

        return header

    def to_record(self) -> Record:
        """Convert header back to TES4 record.

        If we loaded from a file, reuse the raw record to preserve
        any subrecords we don't explicitly model (INTV, INCC, etc.)
        Only update the fields we track.
        """
        if self._raw_record is not None:
            record = self._raw_record

            # Update flags
            record.flags.Master = self.is_esm
            record.flags.Light = self.is_esl
            record.flags.Localized = self.is_localized

            # Update HEDR
            hedr = record.get_subrecord("HEDR")
            if hedr:
                writer = BinaryWriter()
                writer.write_float(self.version)
                writer.write_uint32(self.num_records)
                writer.write_uint32(self.next_object_id)
                hedr.data = writer.get_bytes()
            # Update masters when they changed since load
            current = [sr.get_string() for sr in record.subrecords if sr.signature == "MAST"]
            if current != list(self.masters):
                subs = record.subrecords
                pos = next((i for i, sr in enumerate(subs) if sr.signature == "MAST"), None)
                if pos is None:
                    pos = 1 + sum(1 for sr in subs[1:] if sr.signature in ("CNAM", "SNAM"))
                subs[:] = [sr for sr in subs if sr.signature not in ("MAST", "DATA")]
                added = []
                for i, master in enumerate(self.masters):
                    added.append(record.add_subrecord("MAST"))
                    added[-1].set_string(master)
                    size = self.master_sizes[i] if i < len(self.master_sizes) else 0
                    added.append(record.add_subrecord("DATA"))
                    added[-1].data = struct.pack('<Q', size)
                del subs[len(subs) - len(added):]
                subs[pos:pos] = added

            return record

        # Build from scratch
        record = Record("TES4", FormID(0))
        record.flags.Master = self.is_esm
        record.flags.Light = self.is_esl
        record.flags.Localized = self.is_localized

        hedr = record.add_subrecord("HEDR")
        writer = BinaryWriter()
        writer.write_float(self.version)
        writer.write_uint32(self.num_records)
        writer.write_uint32(self.next_object_id)
        hedr.data = writer.get_bytes()

        # Starfield (0.96): masters are bare MASTs, no DATA (master size) after each, and xEdit
        # requires CNAM (author) and INCC (interior cell count) -- every CK-made Starfield
        # plugin reads HEDR CNAM [SNAM] MAST... INCC.
        starfield = 0.955 <= self.version < 0.965

        if self.author or starfield:
            record.add_subrecord("CNAM").set_string(self.author)

        if self.description:
            record.add_subrecord("SNAM").set_string(self.description)

        for i, master in enumerate(self.masters):
            record.add_subrecord("MAST").set_string(master)
            if starfield:
                continue
            size = self.master_sizes[i] if i < len(self.master_sizes) else 0
            data_rec = record.add_subrecord("DATA")
            data_rec.data = struct.pack('<Q', size)

        if self.override_records:
            onam = record.add_subrecord("ONAM")
            onam.set_form_id_array(self.override_records)

        if starfield:
            # The count of the plugin's interior cells. esplib doesn't count them; a plugin that
            # adds interior cells must set interior_cell_count itself.
            record.add_subrecord("INCC").data = struct.pack('<I', self.interior_cell_count)

        return record


class Plugin:
    """Represents a complete Bethesda plugin file (ESP/ESM/ESL)."""

    def __init__(self, file_path: Union[str, Path, None] = None):
        self.file_path: Optional[Path] = Path(file_path) if file_path else None
        self.header: PluginHeader = PluginHeader()
        self.groups: List[GroupRecord] = []
        self.records: List[Record] = []
        self.load_order: int = -1
        self.modified = False
        # True if loaded with only_signatures (a partial parse — most record
        # types were skipped). Callers must not save a partial plugin.
        self.partial_load = False
        self._game_registry = None
        self._string_tables = None  # Optional[StringTableManager], loaded lazily
        self._string_tables_loaded = False
        self.string_search_dirs: list = []  # Additional dirs to search for .STRINGS files
        self.plugin_set = None  # Optional[PluginSet] — set when loaded via PluginSet
        self._local_formid_fixups = []  # list[(SubRecord, offset)] for save-time fixup
        # RLock so the same thread can re-enter (copy_record calls
        # add_record under lock; save() may invoke helpers that also
        # touch the structures). The lock guards all mutations
        # (add_record, copy_record, remove_record) and serialization
        # (save, to_bytes), so concurrent writers — e.g. the GUI's
        # preview-bake thread and run thread sharing one patch — can't
        # interleave a partial mutation into a save's snapshot.
        self._lock = threading.RLock()

        # Indexes for fast lookups
        self._new_records: List[Record] = []  # Records created by add_record with fresh FormIDs
        self._form_id_index: Dict[int, Record] = {}
        self._editor_id_index: Dict[str, Record] = {}
        self._signature_index: Dict[str, List[Record]] = {}

        # is_readable_file, not exists(): under MO2's VFS a mod-supplied
        # plugin opens fine but stats as absent. See esplib.utils.
        if file_path and is_readable_file(file_path):
            self._load()

    @classmethod
    def load(cls, file_path: Union[str, Path],
             only_signatures: Optional[set] = None) -> 'Plugin':
        """Load a plugin from disk and return it.

        `only_signatures` (a set of 4-char record signatures, e.g. {'NPC_'})
        parses only the matching top-level groups and seeks past the rest —
        a fast partial load for enumerating one record type without paying to
        parse the whole file. The TES4 header is always parsed.
        """
        # Construct WITHOUT a path so the constructor's auto-load doesn't run
        # (that would fully parse the file, defeating only_signatures); then
        # load explicitly with the partial-parse option.
        plugin = cls()
        plugin.file_path = Path(file_path)
        plugin._load(only_signatures=only_signatures)
        return plugin

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('_lock', None)
        state['_game_registry'] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._lock = threading.RLock()
        self.auto_detect_game()

    @classmethod
    def load_cached(cls, file_path: Union[str, Path],
                    only_signatures: Optional[set] = None,
                    cache_dir: Optional[Union[str, Path]] = None) -> 'Plugin':
        """Like `load`, but reuses a pickled copy from a previous run.

        The cache is keyed on the path, `only_signatures` and the esplib
        version, and is discarded when the file's size or mtime changes.
        Localized string tables are loaded and cached too. Set the
        ESPLIB_CACHE_DIR environment variable or pass `cache_dir` to choose
        where it lives (default: ~/.cache/esplib). Changes to external
        string files or BSAs aren't detected.
        """
        import hashlib
        import os
        import pickle
        from . import __version__

        file_path = Path(file_path).resolve()
        st = file_path.stat()
        stamp = (st.st_size, st.st_mtime_ns, __version__)
        sigs = sorted(only_signatures) if only_signatures is not None else None
        key = hashlib.sha1(
            repr((str(file_path).lower(), sigs)).encode()).hexdigest()[:20]
        cache_dir = Path(cache_dir or os.environ.get('ESPLIB_CACHE_DIR')
                         or Path.home() / '.cache' / 'esplib')
        cache_file = cache_dir / f"{file_path.stem}-{key}.pkl"

        try:
            with open(cache_file, 'rb') as f:
                cached_stamp, plugin = pickle.load(f)
            if cached_stamp == stamp:
                return plugin
        except Exception:
            pass  # missing, stale or corrupt cache: fall through and rebuild

        plugin = cls.load(file_path, only_signatures=only_signatures)
        plugin.string_tables  # force the lazy load so it's cached too
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, 'wb') as f:
                pickle.dump((stamp, plugin), f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cache_file)
        except Exception as e:
            logging.getLogger(__name__).warning("Could not write cache: %s", e)
        return plugin

    def _load(self, file_path: Optional[Union[str, Path]] = None,
              only_signatures: Optional[set] = None) -> None:
        if file_path:
            self.file_path = Path(file_path)

        if not self.file_path or not is_readable_file(self.file_path):
            raise PluginError(f"Plugin file not found: {self.file_path}")

        with open(self.file_path, 'rb') as f:
            data = f.read()

        reader = BinaryReader(data)
        self._parse_plugin(reader, only_signatures=only_signatures)
        self._link_records()
        self._string_tables = None
        self._string_tables_loaded = False
        self._build_indexes()
        self.modified = False
        self.auto_detect_game()
        self.partial_load = only_signatures is not None

    def _parse_plugin(self, reader: BinaryReader,
                      only_signatures: Optional[set] = None) -> None:
        self.groups.clear()
        self.records.clear()

        if reader.remaining() < 24:
            raise ParseError("File too small to contain valid plugin header")

        header_record = Record.from_bytes(reader)
        if header_record.signature != "TES4":
            raise ParseError(f"Expected TES4 header, got {header_record.signature}")

        self.header = PluginHeader.from_record(header_record)

        while not reader.at_end():
            if reader.remaining() < 4:
                break

            signature = reader.data[reader.position:reader.position + 4]

            if signature == b'GRUP':
                # For a partial load, peek the top-level group's label (a
                # record signature for group_type 0) and seek past whole
                # groups we don't care about without parsing their records.
                if only_signatures is not None:
                    start = reader.position
                    group_size = struct.unpack_from(
                        '<I', reader.data, start + 4)[0]
                    label = reader.data[start + 8:start + 12]
                    group_type = struct.unpack_from(
                        '<i', reader.data, start + 12)[0]
                    if (group_type == 0
                            and label.rstrip(b'\x00').decode(
                                'ascii', 'replace') not in only_signatures):
                        reader.seek(start + group_size)
                        continue
                group = GroupRecord.from_bytes(reader)
                self.groups.append(group)
                self._collect_records_from_group(group)
            else:
                record = Record.from_bytes(reader)
                self.records.append(record)

    def _collect_records_from_group(self, group: GroupRecord) -> None:
        for item in group.records:
            if isinstance(item, Record):
                self.records.append(item)
            else:
                self._collect_records_from_group(item)

    def _link_records(self) -> None:
        """Set back-references so records can resolve localized strings."""
        for record in self.records:
            record.plugin = self

    @property
    def string_tables(self):
        """String tables for a localized plugin, parsed on first access."""
        if not self._string_tables_loaded:
            self._string_tables_loaded = True
            self._load_string_tables()
        return self._string_tables

    @string_tables.setter
    def string_tables(self, value) -> None:
        self._string_tables = value
        self._string_tables_loaded = True

    def _load_string_tables(self) -> None:
        """Load string tables if the plugin is localized."""
        if not self.header.is_localized or not self.file_path:
            return
        from .strings import StringTableManager
        mgr = StringTableManager()
        mgr.load_for_plugin(self.file_path,
                            search_dirs=self.string_search_dirs)
        if mgr.strings or mgr.dlstrings or mgr.ilstrings:
            self._string_tables = mgr

    def resolve_string(self, string_id: int) -> Optional[str]:
        """Resolve a localized string ID to its text."""
        if self.string_tables:
            return self.string_tables.get_string(string_id)
        return None

    def _build_indexes(self) -> None:
        self._form_id_index.clear()
        self._editor_id_index.clear()
        self._signature_index.clear()

        for record in self.records:
            self._form_id_index[record.form_id.value] = record

            if record.editor_id:
                self._editor_id_index[record.editor_id.lower()] = record

            if record.signature not in self._signature_index:
                self._signature_index[record.signature] = []
            self._signature_index[record.signature].append(record)

    def set_game(self, game_id: str) -> None:
        """Bind record schemas from a game registry.

        Valid game IDs: 'tes5' (Skyrim LE/SE), 'fo4' (Fallout 4), 'sf1' (Starfield).
        After calling this, records support typed field access via
        record['DATA']['damage'] etc.
        """
        from .defs.game import GameRegistry
        resolved = self._GAME_ALIASES.get(game_id, game_id)
        registry = GameRegistry.get_game(resolved)
        if registry is None:
            raise PluginError(f"Unknown game: {game_id}")
        self._game_registry = registry
        self._bind_schemas()

    def auto_detect_game(self) -> None:
        """Auto-detect game from header version and bind schemas."""
        from .defs.game import GameRegistry
        registry = GameRegistry.detect_game(self.header.version)
        if registry is not None:
            self._game_registry = registry
            self._bind_schemas()

    def _bind_schemas(self) -> None:
        """Bind schema definitions to all records based on their signatures."""
        if self._game_registry is None:
            return
        for record in self.records:
            schema = self._game_registry.get(record.signature)
            if schema is not None:
                record.bind_schema(schema)

    def get_record_by_form_id(self, form_id: Union[BaseFormID, int]) -> Optional[Record]:
        if isinstance(form_id, BaseFormID):
            form_id = form_id.value
        return self._form_id_index.get(form_id)

    def get_record_by_editor_id(self, editor_id: str) -> Optional[Record]:
        return self._editor_id_index.get(editor_id.lower())

    def get_records_by_signature(self, signature: str):
        """Iterate over records matching the given signature."""
        return iter(self._signature_index.get(signature, []))

    def new_record(self, signature: str, edid: str = None,
                   form_id: int = None, flags: int = 0) -> Record:
        """Create a new record owned by this plugin.

        form_id is the object_index only — the plugin's file index is
        prepended automatically. If omitted, a fresh local FormID is
        assigned via get_next_form_id().
        """
        if form_id is not None:
            self_index = len(self.header.masters)
            fid = FormID((self_index << 24) | form_id)
        else:
            fid = FormID(0)  # add_record will assign via get_next_form_id()

        record = Record(signature, fid, flags)
        if edid is not None:
            record.add_subrecord('EDID', edid)
        self.add_record(record)
        return record


    def add_record(self, record: Record, group_signature: Optional[str] = None) -> None:
        with self._lock:
            record.plugin = self
            if record.form_id.value == 0:
                record.form_id = self.get_next_form_id()
            if record.form_id.file_index == self._LOCAL_SENTINEL:
                self._new_records.append(record)
            if self._game_registry:
                if record.version == 44:
                    # Set record version to match the game
                    record.version = self._RECORD_VERSIONS.get(
                        self._game_registry.game_id, 44)
                if record.schema is None:
                    schema = self._game_registry.get(record.signature)
                    if schema is not None:
                        record.bind_schema(schema)
            self.records.append(record)

            target_group = None
            group_sig = group_signature or record.signature

            for group in self.groups:
                if (group.group_type == 0 and
                        isinstance(group.label, str) and
                        group.label == group_sig):
                    target_group = group
                    break

            if not target_group:
                target_group = GroupRecord(0, group_sig)
                self.groups.append(target_group)

            target_group.add_record(record)

        self._form_id_index[record.form_id.value] = record
        if record.editor_id:
            self._editor_id_index[record.editor_id.lower()] = record

        if record.signature not in self._signature_index:
            self._signature_index[record.signature] = []
        self._signature_index[record.signature].append(record)

        self.modified = True

    def remove_record(self, record: Record) -> bool:
        with self._lock:
            if record not in self.records:
                return False

            self.records.remove(record)

            for group in self.groups:
                if self._remove_record_from_group(group, record):
                    break

            self._form_id_index.pop(record.form_id.value, None)
            if record.editor_id:
                self._editor_id_index.pop(record.editor_id.lower(), None)

            if record.signature in self._signature_index:
                try:
                    self._signature_index[record.signature].remove(record)
                    if not self._signature_index[record.signature]:
                        del self._signature_index[record.signature]
                except ValueError:
                    pass

            self.modified = True
            return True

    def _remove_record_from_group(self, group: GroupRecord, record: Record) -> bool:
        if record in group.records:
            group.remove_record(record)
            return True
        for item in group.records:
            if isinstance(item, GroupRecord):
                if self._remove_record_from_group(item, record):
                    return True
        return False

    _GAME_VERSIONS = {  # Plugin header version
        'tes5': 1.71,
        'tes5le': 0.94,
        # FO4 must be 1.0, not the old 0.95: a 0.95 header is treated as
        # pre-ESL, so the game/xEdit cannot resolve references to light (ESL)
        # masters and blank the master-index byte to 0. Every vanilla/mod
        # FO4 plugin and every xEdit-authored file uses 1.0.
        'fo4': 1.0,
        'sf1': 0.96,
    }
    _RECORD_VERSIONS = {  # Default form version for new records
        'tes5': 44,
        'tes5le': 40,
        'fo4': 131,
        'sf1': 148,
    }
    # Aliases for set_game() -- tes5le uses the same schemas as tes5
    _GAME_ALIASES = {
        'tes5le': 'tes5',
    }

    @classmethod
    def new_plugin(cls, file_path: Union[str, Path],
                   masters: Optional[List[str]] = None,
                   game: str = 'tes5',
                   is_esm: bool = False) -> 'Plugin':
        """Create a new empty plugin with the given masters.

        game: 'tes5' (Skyrim LE/SE), 'fo4' (Fallout 4), 'sf1' (Starfield).
        Also accepts 'tes5le' to create with LE header version (0.94).
        Sets header version and binds schemas automatically.
        """
        plugin = cls()
        plugin.file_path = Path(file_path)
        plugin.header.version = cls._GAME_VERSIONS.get(game, 1.71)
        plugin.header.is_esm = is_esm
        if masters:
            plugin.header.masters = list(masters)
            plugin.header.master_sizes = [0] * len(masters)
        plugin.set_game(game)
        return plugin

    def normalize_form_id(self, form_id: Union[FormID, int]) -> AbsoluteFormID:
        """Convert a FormID from this plugin's master-list indexing to
        load-order indexing.

        If no PluginSet is available, wraps the value as AbsoluteFormID
        unchanged (the master list acts as the load order).
        """
        if isinstance(form_id, int):
            form_id = FormID(form_id)
        if self.plugin_set is None:
            return AbsoluteFormID(form_id.value)

        file_idx = form_id.file_index
        masters = self.header.masters

        if file_idx == self._LOCAL_SENTINEL:
            # Local record with sentinel file index — preserve as-is.
            # The sentinel will be resolved at save time.
            return AbsoluteFormID(form_id.value)

        is_self_ref = file_idx >= len(masters)

        if not is_self_ref:
            master_name = masters[file_idx]
        elif self.file_path:
            master_name = self.file_path.name
        else:
            return AbsoluteFormID(form_id.value)

        # Map through the load order's game-style index so light (ESL/ESPFE)
        # masters land in the 0xFE namespace and full masters are counted on
        # their own — a raw list position overflows once there are >255 entries.
        abs_fid = self.plugin_set.absolute_form_id(
            master_name, form_id.object_index)
        if abs_fid is None:
            if is_self_ref:
                # One of OUR OWN records, in a plugin the load order
                # doesn't list — a patch being built before it is
                # injected into the PluginSet, most commonly. The self
                # index is meaningless outside this file, so hand back
                # the local sentinel: it is what the write path already
                # knows how to stamp with the real index at save time.
                #
                # Returning the raw value here silently corrupted
                # references. After a save stamps a local record's
                # sentinel with the real self index, that index is a
                # MASTER-LIST position — but the raw value leaks out of
                # here as though it were already load-order-indexed, so
                # denormalize reads it as a load-order slot and resolves
                # it to whatever unrelated plugin sits there. A patch
                # with 7 masters had its own subrace 0x07000806 come back
                # out as CellanRace.esp's 0x04000806.
                return AbsoluteFormID(
                    (self._LOCAL_SENTINEL << 24) | form_id.object_index)
            import logging
            logging.getLogger(__name__).error(
                "Plugin %s references master %r which is not in the "
                "load order — FormID 0x%08X will not be remapped correctly",
                self.file_path.name if self.file_path else '(unknown)',
                master_name, form_id.value)
            return AbsoluteFormID(form_id.value)

        return abs_fid


    def denormalize_form_id(self, form_id: Union[BaseFormID, int]) -> int:
        """Convert a load-order-indexed FormID to this plugin's master-list
        indexing for writing into subrecord data.

        Accepts AbsoluteFormID (preferred), LocalFormID, or raw int.
        The high byte is treated as a load-order index regardless of type.

        For local FormIDs (sentinel 0xFF), returns the sentinel value
        unchanged — the caller should register it on the fixup list.
        """
        if isinstance(form_id, int):
            form_id = AbsoluteFormID(form_id)

        hi_byte = (form_id.value >> 24) & 0xFF

        if hi_byte == self._LOCAL_SENTINEL:
            # Local record — return sentinel, caller adds to fixup list
            return form_id.value

        if self.plugin_set is None:
            # No PluginSet — assume already in master-list indexing
            return form_id.value

        # Decode which load-order plugin this absolute FormID refers to,
        # honouring the 0xFE light namespace, and the object index it carries
        # (12-bit for light refs, 24-bit otherwise).
        master_name = self.plugin_set.plugin_name_for_absolute(form_id.value)
        if master_name is None:
            return form_id.value  # out of range, return as-is
        object_index = self.plugin_set.object_index_for_absolute(form_id.value)

        # A reference to one of our OWN records. Not a master (a plugin
        # cannot master itself, and add_master refuses the self-add), so
        # return the local sentinel and let write_form_id register a
        # save-time fixup that stamps the real self index.
        #
        # Falling through to the lazy-add below silently produced
        # `len(masters) - 1` — the last *existing* master — so a
        # self-reference came back pointing at an unrelated plugin. Live
        # symptom: after a patch was saved (which stamps local FormIDs
        # from the sentinel to a concrete self index), re-furrifying an
        # NPC wrote its RNAM as a reference into whatever plugin sat
        # last in the master list, and the facegen bake produced a
        # headless nif with no error anywhere.
        if (self.file_path is not None
                and master_name.lower() == self.file_path.name.lower()):
            return (self._LOCAL_SENTINEL << 24) | object_index

        # Find or add this master in our master list
        for i, m in enumerate(self.header.masters):
            if m.lower() == master_name.lower():
                return (i << 24) | object_index

        # Not found — add it lazily. add_master can decline (it refuses
        # a self-add), so confirm it landed rather than trusting
        # len(masters) - 1.
        self.add_master(master_name)
        for i, m in enumerate(self.header.masters):
            if m.lower() == master_name.lower():
                return (i << 24) | object_index
        return form_id.value


    def write_form_id(self, sr: 'SubRecord', offset: int,
                      form_id: Union[BaseFormID, int]) -> None:
        """Write a FormID into subrecord data, converting to master-list
        indexing via denormalize_form_id.

        Accepts AbsoluteFormID (preferred), LocalFormID, or raw int.

        For local FormIDs (sentinel 0xFF), writes the sentinel and
        registers the location on the fixup list for save-time
        correction.
        """
        import struct as _struct
        if isinstance(form_id, int):
            form_id = AbsoluteFormID(form_id)

        raw = self.denormalize_form_id(form_id)

        if offset + 4 > len(sr.data):
            # Extend if needed
            sr.data = bytearray(sr.data) + bytearray(offset + 4 - len(sr.data))

        new_data = bytearray(sr.data)
        _struct.pack_into('<I', new_data, offset, raw)
        sr.data = new_data
        sr.modified = True

        # Queue on what was actually WRITTEN, not on the input. A
        # self-reference arrives as an AbsoluteFormID carrying a
        # load-order index, and denormalize turns it into the sentinel —
        # keying off the input would miss it and leave the sentinel on
        # disk, unstamped.
        if (raw >> 24) & 0xFF == self._LOCAL_SENTINEL:
            self._local_formid_fixups.append((sr, offset))


    def add_master(self, master_name: str) -> None:
        """Add a master dependency if not already present. A plugin
        cannot master itself — silently ignore the self-add, since the
        lazy-add path in denormalize_form_id can be reached with the
        plugin's own name when its records' form_ids round-trip through
        an injected-into-plugin_set load order (the patch ends up at
        plugins_list[hi_byte] for hi_byte = post-finalize local index).
        Without this guard, xEdit reports a circular reference."""
        if (self.file_path is not None
                and master_name.lower() == self.file_path.name.lower()):
            return
        if master_name.lower() not in [m.lower() for m in self.header.masters]:
            self.header.masters.append(master_name)
            self.header.master_sizes.append(0)

    def add_recursive_masters(self, source_plugin: 'Plugin') -> None:
        """Add a plugin and all its masters as dependencies."""
        # Add the source's masters first (in order)
        for master in source_plugin.header.masters:
            self.add_master(master)
        # Then add the source plugin itself
        if source_plugin.file_path:
            self.add_master(source_plugin.file_path.name)

    def remap_formid(self, form_id: int,
                     source_plugin: 'Plugin') -> int:
        """Remap a FormID from a source plugin's master list to this plugin's.

        The high byte of a FormID is the master index. This translates it
        from the source's master ordering to the destination's. Lazily
        adds the referenced master if not already present.
        """
        master_idx = (form_id >> 24) & 0xFF
        obj_id = form_id & 0x00FFFFFF

        # Determine the master name in the source
        src_masters = source_plugin.header.masters
        if master_idx < len(src_masters):
            master_name = src_masters[master_idx]
        elif source_plugin.file_path:
            # Index == len(masters) means the source file itself
            master_name = source_plugin.file_path.name
        else:
            return form_id  # Can't remap

        # Find that master's index in our master list
        for i, m in enumerate(self.header.masters):
            if m.lower() == master_name.lower():
                return (i << 24) | obj_id

        # Not found — add it lazily
        self.add_master(master_name)
        new_idx = len(self.header.masters) - 1
        return (new_idx << 24) | obj_id

    # Subrecord signatures that contain localized string IDs
    _LOCALIZED_STRING_SIGS = {'FULL', 'SHRT', 'DESC', 'NNAM', 'ITXT'}

    # Fallback set for records without a schema.
    _FORMID_SUBRECORD_SIGS_FALLBACK = frozenset({
        'LNAM', 'KWDA', 'RNAM', 'PNAM', 'DOFT', 'FTST', 'TPLT',
        'ZNAM', 'WNAM', 'INAM', 'VTCK', 'CNAM', 'ECOR', 'SPLO',
        'RPRM', 'RPRF', 'AHCM', 'AHCF', 'FTSM', 'FTSF', 'DFTM',
        'DFTF', 'MPAI', 'TIND', 'TINC', 'HEAD', 'NAM8', 'MODL',
        'EITM', 'BAMT', 'BIDS', 'ETYP', 'NAM4', 'NAM5', 'YNAM',
        'CRDT', 'EFID', 'ATKR', 'HCLF', 'DPLT', 'SOFT',
    })

    def copy_record(self, record: 'Record',
                    source_plugin: Optional['Plugin'] = None,
                    new_form_id: bool = False) -> 'Record':
        """Deep-copy a record into this plugin.

        Remaps all FormIDs from the source plugin's master-list indexing
        to this plugin's. Uses the schema to find FormID positions.

        When copying into a non-localized plugin, resolves string table
        IDs to inline null-terminated strings, falling back through the
        override chain if needed.

        By default the copy is an OVERRIDE — it keeps the source's FormID
        (denormalized into this plugin). Pass `new_form_id=True` to instead
        mint a FRESH local FormID, producing an independent NEW record rather
        than an override (e.g. duplicating a template into appearance variants).
        Subrecord FormIDs are remapped either way; give the copy a distinct
        EditorID afterward so it doesn't collide with the source.

        Returns the new record.
        """
        with self._lock:
            source = source_plugin or record.plugin
            ps = source.plugin_set if source else None
            new_record = record.copy()

            if source is not None:
                if new_form_id:
                    # Leave FormID 0 so add_record assigns a fresh local one.
                    new_record.form_id = FormID(0)
                elif self.plugin_set is not None:
                    # New path: normalize to load-order, then denormalize to patch
                    norm_fid = source.normalize_form_id(record.form_id)
                    new_record.form_id = FormID(
                        self.denormalize_form_id(norm_fid))
                else:
                    # Legacy path: direct remap for bare plugins without PluginSet
                    new_record.form_id = FormID(
                        self.remap_formid(record.form_id.value, source))

                # Remap FormIDs inside subrecords
                self._remap_subrecord_formids(new_record, source)

            if not self.is_localized:
                # Pass the original record's normalized FormID for override
                # chain lookup — new_record.form_id is in patch master-list
                # space which won't match the load-order-indexed chain.
                orig_norm = None
                if source is not None and ps is not None:
                    orig_norm = source.normalize_form_id(record.form_id)
                self._delocalize_strings(new_record, source, ps,
                                         chain_form_id=orig_norm)

            self.add_record(new_record)
            return new_record

    def _remap_subrecord_formids(self, record: 'Record',
                                 source: Optional['Plugin'] = None,
                                 remap_fn=None) -> None:
        """Remap master indices in subrecords that contain FormIDs.

        Uses the record's schema to identify FormID subrecords when
        available; falls back to a hardcoded set otherwise.

        Two callers share this FormID-location walk:
          - copy (default): `source` given, `remap_fn` None — each located
            FormID is normalized through `source` then denormalized into this
            plugin's master indexing.
          - permute: `remap_fn(old_fid) -> new_fid` given — each located FormID
            is passed through `remap_fn` verbatim (used by `sort_masters` to
            shift high bytes when the master list is reordered).
        """
        import struct as _struct
        from .defs.types import (
            EspFormID, EspStruct, EspArray, EspAlternateTextures)
        schema = record.schema
        for sr in record.subrecords:
            if sr.size < 4:
                continue

            if schema is not None:
                member = schema.get_member(sr.signature)
                if member is None:
                    continue
                vdef = member.value_def

                if isinstance(vdef, EspFormID):
                    self._remap_at(sr, 0, source, _struct, remap_fn)

                elif isinstance(vdef, EspArray):
                    if isinstance(vdef.element, EspFormID):
                        # Array of FormIDs (e.g. KWDA)
                        for offset in range(0, sr.size, 4):
                            self._remap_at(sr, offset, source, _struct, remap_fn)

                elif isinstance(vdef, EspStruct):
                    self._remap_struct_formids(
                        sr, vdef, source, _struct, EspFormID, remap_fn,
                        record)

                elif isinstance(vdef, EspAlternateTextures):
                    self._remap_alternate_textures(sr, source, _struct, remap_fn)

            else:
                # No schema — fall back to hardcoded set
                if (sr.signature in self._FORMID_SUBRECORD_SIGS_FALLBACK
                        and sr.size == 4):
                    self._remap_at(sr, 0, source, _struct, remap_fn)

                # Alternate textures without schema
                if sr.signature in ('MO2S', 'MO3S', 'MO4S', 'MO5S'):
                    self._remap_alternate_textures(sr, source, _struct, remap_fn)

                # Conditions without schema. Records that carry them are
                # not all defined (MGEF is not), and a condition's
                # FormIDs need remapping just the same — so apply the
                # shared definition directly rather than skipping.
                if sr.signature == 'CTDA':
                    from .defs.common import CONDITION
                    self._remap_struct_formids(
                        sr, CONDITION, source, _struct, EspFormID,
                        remap_fn, record)

        # VMAD: parse, remap embedded FormIDs, rewrite
        self._remap_vmad(record, source, remap_fn)


    def _remap_struct_formids(self, sr, struct_def, source, _struct,
                              EspFormID, remap_fn=None, record=None) -> None:
        """Walk struct members and remap any FormID fields.

        A member may be an EspUnion, in which case whether it holds a
        FormID depends on the subrecord's own contents (CTDA's parameters
        are the case that matters). Resolving one needs the parsed
        sibling fields, so the struct is parsed first — but only when it
        actually contains a union, since most do not.
        """
        from .defs.types import EspUnion
        from .defs.context import EspContext

        ctx = None
        if any(isinstance(f, EspUnion) for f in struct_def.members):
            game_id = self._walk_game_id(source, record)
            try:
                parsed = struct_def.from_bytes(
                    BinaryReader(bytes(sr.data)),
                    EspContext(game=game_id), available=sr.size)
            except Exception as exc:
                # Malformed or truncated data: leave it verbatim rather
                # than remapping slots we could not identify.
                _log.debug(
                    f"could not parse {sr.signature} to resolve its unions "
                    f"({exc}); leaving its FormIDs unremapped")
                return
            ctx = EspContext(game=game_id).for_struct(parsed)

        offset = 0
        for field in struct_def.members:
            resolved = field
            if isinstance(field, EspUnion):
                if ctx is None:
                    break
                resolved = field.resolve(ctx)
            if isinstance(resolved, EspFormID):
                self._remap_at(sr, offset, source, _struct, remap_fn)
            size = getattr(field, 'byte_size', None)
            if size is None:
                size = getattr(field, 'size', None)
            if size is None:
                break  # variable-size field, stop
            offset += size

    def _walk_game_id(self, source: Optional['Plugin'], record) -> str:
        """Game id for schema decisions during a copy or permute.

        Prefer this plugin's own registry, then the source's, then the
        record's. A union decider that gets '' falls back to leaving the
        bytes alone.
        """
        for owner in (self, source):
            registry = getattr(owner, '_game_registry', None)
            if registry is not None:
                return registry.game_id
        return getattr(record, 'game_id', '') if record is not None else ''


    def _remap_at(self, sr, offset: int, source: Optional['Plugin'],
                  _struct, remap_fn=None) -> None:
        """Remap a FormID at a byte offset within a subrecord."""
        if offset + 4 > sr.size:
            return
        old_fid = _struct.unpack_from('<I', sr.data, offset)[0]
        if remap_fn is not None:
            # Permutation path: shift the high byte directly (sort_masters).
            new_fid = remap_fn(old_fid)
            if new_fid != old_fid:
                new_data = bytearray(sr.data)
                _struct.pack_into('<I', new_data, offset, new_fid)
                sr.data = new_data
                sr.modified = True
            return
        if self.plugin_set is not None:
            # New path: normalize then write via write_form_id
            norm_fid = source.normalize_form_id(FormID(old_fid))
            self.write_form_id(sr, offset, norm_fid)
        else:
            # Legacy path: direct remap
            new_fid = self.remap_formid(old_fid, source)
            if new_fid != old_fid:
                new_data = bytearray(sr.data)
                _struct.pack_into('<I', new_data, offset, new_fid)
                sr.data = new_data
                sr.modified = True

    def _remap_alternate_textures(self, sr, source, _struct,
                                  remap_fn=None) -> None:
        """Remap FormIDs inside alternate texture data (MO2S/MO3S/MO4S/MO5S).

        Format: uint32 count, then repeated:
            uint32 name_length
            char[name_length] name (null-terminated)
            uint32 FormID (TXST reference)
            uint32 3D index
        """
        if sr.size < 4:
            return
        count = _struct.unpack_from('<I', sr.data, 0)[0]
        offset = 4
        for _ in range(count):
            if offset + 4 > len(sr.data):
                break
            name_len = _struct.unpack_from('<I', sr.data, offset)[0]
            offset += 4 + name_len
            if offset + 4 > len(sr.data):
                break
            # FormID is here
            self._remap_at(sr, offset, source, _struct, remap_fn)
            offset += 4
            # 3D index
            offset += 4


    def _remap_vmad(self, record: 'Record', source: Optional['Plugin'] = None,
                    remap_fn=None) -> None:
        """Remap FormIDs inside a VMAD subrecord (Papyrus script data).

        The VMAD parser models the Skyrim property-type set; FO4 (and some
        Skyrim mods) use property types it doesn't understand yet, which make
        parsing raise. Rather than fail the whole copy, fall back to leaving
        the VMAD bytes verbatim. That is correct whenever the source's master
        ordering is preserved in this plugin (the common case — copying a
        vanilla record into a patch that masters the same file at the same
        index); embedded FormIDs that *would* move are the rare exception and
        are logged so the gap is visible.
        """
        from .vmad import VmadData
        vmad_sr = record.get_subrecord('VMAD')
        if vmad_sr is None:
            return
        try:
            vmad = VmadData.parse(vmad_sr.data, record.signature)
        except Exception as e:
            _log.warning(
                "VMAD on %s %s not remapped (parser: %s); preserving bytes "
                "verbatim", record.signature, record.editor_id or
                hex(record.form_id.value), e)
            return

        def _remap(fid: int) -> int:
            if remap_fn is not None:
                return remap_fn(fid)
            if self.plugin_set is not None:
                norm = source.normalize_form_id(FormID(fid))
                return self.denormalize_form_id(norm)
            else:
                return self.remap_formid(fid, source)

        vmad.remap_form_ids(_remap)
        vmad_sr.data = bytearray(vmad.to_bytes(record.signature))
        vmad_sr.modified = True


    def _canonical_master_order(self) -> list:
        """Master indices reordered into canonical Bethesda order: master-flagged
        files (ESM + ESL/light) first, then ESPs, each subgroup in load order.

        FO4 (and the CK/xEdit) require this ordering. A light (ESL) master left
        stranded in the ESP section makes the engine and xEdit fail to resolve
        references to it — they fall back to a base-game record. Master flags and
        load-order positions come from the PluginSet when present; without one we
        fall back to the filename extension and the existing order.
        """
        masters = self.header.masters
        ps = self.plugin_set

        def is_master_flagged(name: str) -> bool:
            if ps is not None:
                pl = ps.get_plugin(name)
                if pl is not None:
                    return pl.is_esm or pl.is_esl
            n = name.lower()
            return n.endswith('.esm') or n.endswith('.esl')

        def lo_pos(name: str, orig: int) -> int:
            if ps is not None:
                i = ps.load_order.index_of(name)
                if i >= 0:
                    return i
            return orig

        return sorted(
            range(len(masters)),
            key=lambda i: (0 if is_master_flagged(masters[i]) else 1,
                           lo_pos(masters[i], i)))


    def sort_masters(self) -> bool:
        """Reorder the master list into canonical order and remap every FormID's
        high byte to match. Returns True if anything changed.

        esplib adds masters in first-referenced order; FO4 requires them sorted
        (master-flagged before ESP), and a stranded ESL otherwise mis-resolves
        (see `_canonical_master_order`). Call once after the plugin is fully
        built, just before save. Records' own FormIDs and every FormID inside
        their subrecords are shifted through the same schema-driven walk used by
        `copy_record`, so nothing is missed.
        """
        n = len(self.header.masters)
        if n == 0:
            return False
        order = self._canonical_master_order()
        if order == list(range(n)):
            return False  # already canonical

        # old master index -> new master index
        new_pos = [0] * n
        for new_i, old_i in enumerate(order):
            new_pos[old_i] = new_i

        def remap_fn(fid: int) -> int:
            hi = (fid >> 24) & 0xFF
            if hi < n:  # a master reference; self-index (== n) and 0xFF untouched
                return (new_pos[hi] << 24) | (fid & 0xFFFFFF)
            return fid

        with self._lock:
            for rec in self.records:
                hi = (rec.form_id.value >> 24) & 0xFF
                if hi < n:
                    rec.form_id = FormID(remap_fn(rec.form_id.value))
                    rec.modified = True
                self._remap_subrecord_formids(rec, remap_fn=remap_fn)

            self.header.masters = [self.header.masters[i] for i in order]
            self.header.master_sizes = [
                self.header.master_sizes[i] for i in order]
        return True


    def _delocalize_strings(self, record: 'Record',
                            source: Optional['Plugin'],
                            plugin_set=None,
                            chain_form_id=None) -> None:
        """Convert localized string IDs to inline strings.

        For each 4-byte string subrecord (FULL, SHRT, DESC, etc.),
        determines whether it's a string table ID by checking the
        base record's plugin in the override chain. If the base
        plugin is localized, the value is a string ID and we resolve
        it from that plugin's string tables. Otherwise it's a real
        inline string and left alone.

        chain_form_id: normalized FormID for override chain lookup.
            When copying records, the record's FormID is already in
            the patch's master-list space and won't match the
            load-order-indexed override chain.
        """
        import logging as _logging
        for sr in record.subrecords:
            if sr.signature not in self._LOCALIZED_STRING_SIGS:
                continue
            if sr.size != 4:
                continue
            string_id = sr.get_uint32()
            if string_id == 0:
                # Null string ID — if the source plugin is localized,
                # this 4-byte zero is a string table ID, not inline text.
                # Replace with a single null byte (empty inline string).
                if source is not None and source.is_localized:
                    sr.data = bytearray(b'\x00')
                    sr.modified = True
                continue

            # Walk the override chain to find a localized plugin
            # that can resolve this string ID. The base record
            # defines the string; overrides may carry the same ID
            # or a different one. Try each until one resolves.
            resolved = None
            chain = None
            if plugin_set is not None:
                lookup_fid = chain_form_id or record.form_id
                chain = plugin_set.get_override_chain(lookup_fid)

            if chain:
                # Walk chain in reverse (winner first, then back to base)
                # trying each record's string ID against its plugin
                for chain_rec in reversed(list(chain)):
                    cp = chain_rec.plugin
                    if cp is None or not cp.is_localized:
                        continue
                    if not cp.string_tables:
                        continue
                    # Try this record's own string ID first
                    chain_sr = chain_rec.get_subrecord(sr.signature)
                    if chain_sr and chain_sr.size == 4:
                        chain_sid = chain_sr.get_uint32()
                        if chain_sid and cp.resolve_string(chain_sid):
                            resolved = cp.resolve_string(chain_sid)
                            break
            elif source is not None and source.is_localized and source.string_tables:
                resolved = source.resolve_string(string_id)

            if resolved is not None:
                text = resolved.rstrip('\x00')
                sr.data = bytearray(
                    text.encode('cp1252', errors='replace') + b'\x00')
                sr.modified = True
            else:
                # Determine if any plugin in the chain was localized
                # (meaning this is a real string ID, not inline text)
                has_localized = False
                if chain:
                    has_localized = any(
                        r.plugin and r.plugin.is_localized
                        for r in chain)
                elif source and source.is_localized:
                    has_localized = True

                if has_localized:
                    _logging.getLogger(__name__).error(
                        "Unresolved string ID 0x%08X in %s.%s "
                        "-- string ID not found in any string table",
                        string_id, record.editor_id, sr.signature)
                    sr.data = bytearray(b'\x00')
                    sr.modified = True

    # Sentinel file index for new local records. Using 0xFF avoids
    # collisions with master indices (which grow from 0). The real
    # file index (len(masters)) is assigned at save time.
    _LOCAL_SENTINEL = 0xFF

    def get_next_form_id(self) -> FormID:
        """Allocate a new local FormID for this plugin.

        Uses a sentinel file index (0xFF) so the FormID remains valid
        even if more masters are added later. The real file index is
        set at save time by _finalize_local_form_ids.
        """
        next_id = self.header.next_object_id
        self.header.next_object_id = next_id + 1
        return FormID((self._LOCAL_SENTINEL << 24) | next_id)

    def _finalize_local_form_ids(self) -> None:
        """Replace sentinel file indices on record-level FormIDs.

        Called at save time, after the master list is finalized.
        Subrecord-level sentinel FormIDs are handled by
        _apply_local_formid_fixups via the write_form_id fixup list.
        """
        local_index = len(self.header.masters)
        sentinel = self._LOCAL_SENTINEL

        for record in getattr(self, '_new_records', ()):
            if record.form_id.file_index == sentinel:
                record.form_id = FormID(
                    (local_index << 24) | record.form_id.object_index)

    def _count_records_and_groups(self) -> int:
        """Count all records and groups (HEDR num_records includes both)."""
        count = 0
        for group in self.groups:
            count += self._count_in_group(group)
        return count

    def _count_in_group(self, group: GroupRecord) -> int:
        """Recursively count a group + all its contents."""
        count = 1  # the group itself
        for item in group.records:
            if isinstance(item, GroupRecord):
                count += self._count_in_group(item)
            else:
                count += 1
        return count

    def _debug_log_save_counts(self) -> None:
        """Emit a DEBUG-level breakdown of what _count_records_and_groups
        sees on the in-memory structure right before serialization.

        Cross-checks self.records (flat list) against the per-group walk;
        a mismatch suggests records were added to one but not the other.
        """
        if not _log.isEnabledFor(logging.DEBUG):
            return
        name = self.file_path.name if self.file_path else '<unsaved>'
        total = self.header.num_records
        _log.debug(
            'save(%s): HEDR.num_records=%d  top_groups=%d  '
            'self.records=%d (flat)',
            name, total, len(self.groups), len(self.records))
        flat_in_groups = 0
        for g in self.groups:
            n = self._count_in_group(g)
            recs = n - 1  # subtract the group itself
            flat_in_groups += self._flat_record_count(g)
            label = g.label if isinstance(g.label, str) else hex(g.label)
            _log.debug(
                '  group %-4s type=%d: %d items (group + nested) = '
                '%d records',
                label, g.group_type, n, recs)
        if flat_in_groups != len(self.records):
            _log.warning(
                'save(%s): records-in-groups (%d) != self.records (%d) '
                '— mismatch of %d',
                name, flat_in_groups, len(self.records),
                flat_in_groups - len(self.records))

    def _flat_record_count(self, group: GroupRecord) -> int:
        """Count records (not groups) reachable from a group, recursively."""
        n = 0
        for item in group.records:
            if isinstance(item, GroupRecord):
                n += self._flat_record_count(item)
            else:
                n += 1
        return n

    def _debug_verify_written_count(self, payload: bytes) -> None:
        """Byte-walk the just-written payload, compare to HEDR.num_records.

        If they disagree, the count we wrote into the header doesn't
        match the body — which is exactly the bug we're hunting.
        """
        if not _log.isEnabledFor(logging.DEBUG):
            return
        name = self.file_path.name if self.file_path else '<unsaved>'
        records = 0
        groups = 0

        def walk(start: int, end: int) -> None:
            nonlocal records, groups
            p = start
            while p < end - 7:
                sig = payload[p:p+4]
                size = struct.unpack_from('<I', payload, p+4)[0]
                if sig == b'GRUP':
                    groups += 1
                    walk(p + 24, p + size)  # GRUP size includes 24-byte header
                    p += size
                else:
                    records += 1
                    p += 24 + size  # record header(24) + body
        try:
            tes4_data_size = struct.unpack_from('<I', payload, 4)[0]
            walk(24 + tes4_data_size, len(payload))
        except Exception as e:
            _log.debug('save(%s): byte-walk verify failed: %s', name, e)
            return
        actual = records + groups
        stored = self.header.num_records
        if actual != stored:
            _log.warning(
                'save(%s): HEDR.num_records=%d but body contains '
                '%d records + %d groups = %d (delta %+d)',
                name, stored, records, groups, actual,
                actual - stored)
        else:
            _log.debug(
                'save(%s): post-write verify OK '
                '(%d records + %d groups = %d == HEDR)',
                name, records, groups, actual)

    def _apply_local_formid_fixups(self) -> None:
        """Walk the fixup list and set the file index byte to the
        final local index on every registered local FormID location."""
        fixups = getattr(self, '_local_formid_fixups', None)
        if not fixups:
            return
        import struct as _struct
        local_index = len(self.header.masters)
        for sr, offset in fixups:
            if offset + 4 <= len(sr.data):
                fid = _struct.unpack_from('<I', sr.data, offset)[0]
                if (fid >> 24) == self._LOCAL_SENTINEL:
                    new_fid = (local_index << 24) | (fid & 0x00FFFFFF)
                    new_data = bytearray(sr.data)
                    _struct.pack_into('<I', new_data, offset, new_fid)
                    sr.data = new_data
        fixups.clear()


    # Vanilla Skyrim top-level group order; the CK resolves base objects in file order.
    _SKYRIM_GROUP_ORDER = (
        "GMST KYWD LCRT AACT TXST GLOB CLAS FACT HDPT EYES RACE SOUN ASPC MGEF SCPT LTEX ENCH SPEL "
        "SCRL ACTI TACT ARMO BOOK CONT DOOR INGR LIGH MISC APPA STAT SCOL MSTT GRAS TREE FLOR FURN "
        "WEAP AMMO NPC_ LVLN KEYM ALCH IDLM COBJ PROJ HAZD SLGM LVLI WTHR CLMT SPGD RFCT REGN NAVI "
        "CELL WRLD DIAL QUST IDLE PACK CSTY LSCR LVSP ANIO WATR EFSH EXPL DEBR IMGS IMAD FLST PERK "
        "BPTD ADDN AVIF CAMS CPTH VTYP MATT IPCT IPDS ARMA ECZN LCTN MESG RGDL DOBJ LGTM MUSC FSTP "
        "FSTS SMBN SMQN SMEN DLBR MUST DLVW WOOP SHOU EQUP RELA SCEN ASTP OTFT ARTO MATO MOVT SNDR "
        "DUAL SNCT SOPM COLL CLFM REVB").split()

    def _sort_top_groups(self) -> None:
        order = {s: i for i, s in enumerate(self._SKYRIM_GROUP_ORDER)}
        if self.header.version < 1.6 or self.header.version > 1.8:
            return

        def key(g):
            label = g.label
            if isinstance(label, bytes):
                label = label.decode('latin-1')
            return order.get(label, len(order)) if g.group_type == 0 else len(order)
        self.groups.sort(key=key)

    def save(self, file_path: Optional[Union[str, Path]] = None) -> None:
        with self._lock:
            if file_path:
                self.file_path = Path(file_path)

            if not self.file_path:
                raise PluginError("No file path specified for saving")

            self._sort_top_groups()

            # Replace sentinel file indices with the real local index.
            self._finalize_local_form_ids()
            self._apply_local_formid_fixups()

            # Update header record count (includes both records and groups)
            self.header.num_records = self._count_records_and_groups()

            self._debug_log_save_counts()

            # Create backup if file exists
            if self.file_path.exists():
                backup_path = self.file_path.with_suffix(self.file_path.suffix + '.bak')
                if not backup_path.exists():
                    shutil.copy2(self.file_path, backup_path)

            # Write plugin data
            writer = BinaryWriter()
            header_record = self.header.to_record()
            writer.write_bytes(header_record.to_bytes())

            for group in self.groups:
                writer.write_bytes(group.to_bytes())

            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            payload = writer.get_bytes()
            with open(self.file_path, 'wb') as f:
                f.write(payload)

            self._debug_verify_written_count(payload)

            self.modified = False

    def save_as(self, file_path: Union[str, Path]) -> None:
        old_path = self.file_path
        try:
            self.save(file_path)
        finally:
            self.file_path = old_path

    def to_bytes(self) -> bytes:
        """Serialize the entire plugin to bytes without writing to disk.

        Refreshes HEDR.num_records and applies any pending sentinel
        FormID fixups so the bytes match what `save()` would write.
        """
        # getattr fallback: tests construct Plugin via __new__ and may
        # skip __init__ entirely. Guard the lock attr too.
        lock = getattr(self, '_lock', None)
        if lock is not None:
            lock.acquire()
        try:
            self._finalize_local_form_ids()
            self._apply_local_formid_fixups()
            self.header.num_records = self._count_records_and_groups()
            writer = BinaryWriter()
            header_record = self.header.to_record()
            writer.write_bytes(header_record.to_bytes())
            for group in self.groups:
                writer.write_bytes(group.to_bytes())
            return writer.get_bytes()
        finally:
            if lock is not None:
                lock.release()

    @property
    def is_esm(self) -> bool:
        return self.header.is_esm

    @property
    def is_esl(self) -> bool:
        return self.header.is_esl

    @property
    def is_esp(self) -> bool:
        return not self.header.is_esm and not self.header.is_esl

    @property
    def is_localized(self) -> bool:
        return self.header.is_localized

    def validate(self) -> List[str]:
        issues = []

        seen_form_ids = set()
        for record in self.records:
            if record.form_id.value in seen_form_ids:
                issues.append(f"Duplicate FormID: {record.form_id}")
            seen_form_ids.add(record.form_id.value)

        # This plugin's own records: its own file index, or the sentinel a new record carries
        # until save() assigns that index.
        own = {len(self.header.masters), self._LOCAL_SENTINEL}
        if self.header.is_esl:
            esl_records = [r for r in self.records if r.form_id.file_index in own]
            if len(esl_records) > 2048:
                issues.append(
                    f"ESL file has {len(esl_records)} records, maximum is 2048")
            for record in esl_records:
                if record.form_id.object_index >= 0x1000:
                    issues.append(f"ESL FormID out of range: {record.form_id}")

        for record in self.records:
            if record.form_id.file_index not in own \
                    and record.form_id.file_index > len(self.header.masters):
                issues.append(
                    f"FormID references unknown master file: {record.form_id}")

        return issues

    def get_statistics(self) -> Dict[str, Any]:
        stats = {
            'file_path': str(self.file_path) if self.file_path else None,
            'file_type': 'ESM' if self.is_esm else 'ESL' if self.is_esl else 'ESP',
            'total_records': self._count_records_and_groups(),
            'total_groups': len(self.groups),
            'masters': self.header.masters[:],
            'author': self.header.author,
            'description': self.header.description,
            'version': self.header.version,
            'next_object_id': f"0x{self.header.next_object_id:06X}",
            'is_localized': self.header.is_localized,
        }

        record_types = {}
        for record in self.records:
            record_types[record.signature] = record_types.get(record.signature, 0) + 1
        stats['record_types'] = record_types

        return stats

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def __str__(self) -> str:
        file_name = self.file_path.name if self.file_path else "<unsaved>"
        file_type = 'ESM' if self.is_esm else 'ESL' if self.is_esl else 'ESP'
        return f"Plugin({file_name}, {file_type}, {len(self.records)} records)"

    def __repr__(self) -> str:
        return f"Plugin(file_path={self.file_path!r}, records={len(self.records)})"
