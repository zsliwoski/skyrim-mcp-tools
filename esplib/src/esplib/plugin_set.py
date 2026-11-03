"""PluginSet -- loads multiple plugins with master chains and override resolution."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from .plugin import Plugin
from .record import Record
from .utils import FormID, BaseFormID, AbsoluteFormID
from .load_order import LoadOrder
from .exceptions import PluginError


log = logging.getLogger(__name__)


class OverrideChain:
    """Ordered list of records for a single FormID across the load order.

    chain[0]  = base record (from the earliest/master plugin)
    chain[-1] = winning record (last override)
    """

    def __init__(self):
        self._entries: List[Tuple[str, Record]] = []  # (plugin_name, record)

    def add(self, plugin_name: str, record: Record) -> None:
        self._entries.append((plugin_name, record))

    @property
    def records(self) -> List[Record]:
        return [r for _, r in self._entries]

    @property
    def plugin_names(self) -> List[str]:
        return [n for n, _ in self._entries]

    def __getitem__(self, index) -> Record:
        return self._entries[index][1]

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.records)

    def __repr__(self) -> str:
        names = ', '.join(self.plugin_names)
        return f"OverrideChain([{names}])"


class PluginSet:
    """Manages a set of plugins loaded according to a load order.

    Supports:
    - Lazy loading (header-only by default, full on demand)
    - FormID resolution across master chains
    - Override chain queries
    """

    def __init__(self, load_order: LoadOrder):
        self.load_order = load_order
        self.string_search_dirs: List[str] = []
        self._plugins: Dict[str, Optional[Plugin]] = {}
        self._loaded_full: Dict[str, bool] = {}
        # FormID -> list of (plugin_name, record) in load order
        self._override_index: Optional[Dict[int, OverrideChain]] = None
        # Cached game-style load-order index (see _build_game_index).
        self._game_index: Optional[tuple] = None


    @classmethod
    def from_plugin(cls, plugin_path: Union[str, Path],
                    data_dir: Union[str, Path, None] = None,
                    game_id: str = 'tes5') -> 'PluginSet':
        """Load a plugin and all its masters into a PluginSet.

        Reads the plugin's master list from its header, recursively
        gathers transitive masters, builds a load order, and loads
        everything.
        """
        plugin_path = Path(plugin_path)
        if data_dir is None:
            data_dir = plugin_path.parent
        else:
            data_dir = Path(data_dir)

        # Gather all masters recursively
        def _gather_masters(name: str, seen: set, order: list):
            if name.lower() in seen:
                return
            seen.add(name.lower())
            path = data_dir / name
            if not path.exists():
                return
            p = Plugin(path)
            for master in p.header.masters:
                _gather_masters(master, seen, order)
            order.append(name)

        order: list = []
        seen: set = set()
        _gather_masters(plugin_path.name, seen, order)

        lo = LoadOrder.from_list(order, data_dir=data_dir, game_id=game_id)
        ps = cls(lo)
        ps.load_all()
        return ps

    def _resolve_plugin_path(self, name: str) -> Optional[Path]:
        """Find the file path for a plugin name."""
        return self.load_order.plugin_path(name)

    def invalidate(self) -> None:
        """Drop every index derived from the load order.

        Call this after mutating `load_order.plugins` or `_plugins`
        directly — adding an in-memory plugin, removing one, or changing
        a plugin's Light flag. Both indexes must go together:

        - `_override_index` maps absolute FormID -> override chain.
        - `_game_index` maps plugin name -> load-order slot, and backs
          `absolute_form_id()`. A plugin that isn't in it has no slot,
          so `absolute_form_id()` returns None and every FormID that
          plugin defines becomes unresolvable — including the ones it
          references itself.

        Clearing only the first is the subtle failure: override lookups
        for records from *other* plugins keep working, so the breakage
        shows up only for the newly-added plugin's own records.
        """
        self._override_index = None
        self._game_index = None

    def load_plugin(self, name: str, full: bool = True,
                    only_signatures: Optional[set] = None) -> Optional[Plugin]:
        """Load a single plugin by name.

        Args:
            name: Plugin filename (e.g. 'Skyrim.esm')
            full: If True, load all records. If False, header only.
            only_signatures: If given, parse only these top-level record
                groups (a fast partial load — see Plugin.load).
        """
        if name in self._plugins and self._loaded_full.get(name, False) >= full:
            return self._plugins[name]

        path = self._resolve_plugin_path(name)
        if path is None:
            # Not a hard error — a stale load-order entry is normal —
            # but say which directories were searched. Callers that
            # care report the tally; this is what makes the tally
            # actionable when the file is somewhere else entirely.
            searched = [str(d) for d in (self.load_order.data_dir,
                                         self.load_order.fallback_dir) if d]
            log.debug("Plugin file not found: %s (searched: %s)",
                      name, "; ".join(searched) or "<no data dir set>")
            self._plugins[name] = None
            return None

        try:
            plugin = Plugin()
            plugin.string_search_dirs = list(self.string_search_dirs)
            plugin._load(path, only_signatures=only_signatures)
            plugin.plugin_set = self
            self._plugins[name] = plugin
            self._loaded_full[name] = True
            # Invalidate derived indexes when a new plugin is loaded — a
            # plugin's Light flag (read from its header) feeds the game index.
            self.invalidate()
            return plugin
        except Exception as exc:
            # A plugin that's present but unreadable is a genuinely
            # different problem from one that isn't there, and used to
            # be indistinguishable: both ended as a silent None, so a
            # parser bug on one file read to the caller as "missing".
            # Warn — a file we found and couldn't parse is never
            # routine — and keep the traceback for --debug.
            log.warning("Failed to load plugin %s: %s: %s",
                        path, type(exc).__name__, exc)
            log.debug("Traceback for %s", path, exc_info=True)
            self._plugins[name] = None
            return None

    def load_all(self, only_signatures: Optional[set] = None) -> int:
        """Load all plugins in the load order. Returns count of successfully
        loaded. `only_signatures` does a fast partial parse (see Plugin.load)."""
        count = 0
        for name in self.load_order:
            if self.load_plugin(name, only_signatures=only_signatures) is not None:
                count += 1
        return count

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get a loaded plugin by name, loading it if not already loaded."""
        if name not in self._plugins:
            self.load_plugin(name)
        return self._plugins.get(name)

    def _build_override_index(self) -> None:
        """Build the FormID -> override chain index.

        Built into a local dict and published in one assignment at the
        end. Assigning `self._override_index = {}` up front and filling
        it in place made the half-built index visible to other threads
        the moment it was created: a concurrent reader would see a
        non-None index, look up a FormID that hadn't been added yet, and
        get None back. Nothing raised — the caller just saw an
        unresolvable reference.

        That is not hypothetical. Previewing an NPC while a Run's
        FaceGen phase was in flight (separate QThreads, one shared
        PluginSet) resolved the NPC's race but not the head parts that
        race's Head Data points at, so the bake produced a nif with
        eyes and hair and no head.

        Two threads racing here now both build a complete index and the
        last assignment wins — duplicated work, never a partial read.
        """
        index: Dict[int, OverrideChain] = {}

        for plugin_name in self.load_order:
            plugin = self._plugins.get(plugin_name)
            if plugin is None:
                continue

            for record in plugin.records:
                # Resolve the FormID to an absolute form:
                # The file_index byte maps to the plugin's master list.
                abs_fid = self._resolve_absolute_form_id(
                    record.form_id, plugin)
                if abs_fid is None:
                    continue

                fid_val = abs_fid.value

                if fid_val not in index:
                    index[fid_val] = OverrideChain()
                index[fid_val].add(plugin_name, record)

        self._override_index = index

    # 0xFF is the save-time sentinel for "this plugin's own new record"
    # used by Plugin.get_next_form_id() — the real file index gets
    # stamped in at save. For in-memory resolution we treat it the
    # same as file_idx == num_masters (self-reference).
    _LOCAL_SENTINEL = 0xFF

    def _is_light_plugin(self, name: str) -> bool:
        """Whether a load-order entry occupies the 0xFE light namespace.

        Light = the TES4 Light flag (covers ESPFE: an .esp with the flag) or,
        for entries that aren't loaded, the .esl extension (which the engine
        always treats as light).
        """
        p = self._plugins.get(name)
        if p is not None:
            return p.header.is_esl
        return name.lower().endswith('.esl')

    def _build_game_index(self) -> tuple:
        """Map the load order to game-style indices, modelling the ESL scheme.

        Full (non-light) plugins take indices 0x00..0xFD in load order; every
        light (ESL/ESPFE) plugin shares high byte 0xFE with a 12-bit light
        index assigned in load order among the light plugins. This is what
        lets a real >255-plugin modlist address correctly — the raw position
        in the list is NOT a usable master index once lights are present.

        Returns (name_to_slot, full_by_ord, light_by_ord) where name_to_slot
        maps name.lower() -> (is_light, ordinal).
        """
        name_to_slot: Dict[str, tuple] = {}
        full_by_ord: Dict[int, str] = {}
        light_by_ord: Dict[int, str] = {}
        full_ord = 0
        light_ord = 0
        for name in self.load_order:
            if self._is_light_plugin(name):
                name_to_slot[name.lower()] = (True, light_ord)
                light_by_ord[light_ord] = name
                light_ord += 1
            else:
                name_to_slot[name.lower()] = (False, full_ord)
                full_by_ord[full_ord] = name
                full_ord += 1
        return name_to_slot, full_by_ord, light_by_ord

    def _game_index_maps(self) -> tuple:
        if self._game_index is None:
            self._game_index = self._build_game_index()
        return self._game_index

    def absolute_form_id(self, plugin_name: str,
                         object_index: int) -> Optional[AbsoluteFormID]:
        """Compose the absolute (load-order) FormID for a record defined in
        `plugin_name` with the given object index. None if the plugin isn't in
        the load order. Light plugins yield 0xFE | lightidx<<12 | obj(12-bit)."""
        name_to_slot, _, _ = self._game_index_maps()
        slot = name_to_slot.get(plugin_name.lower())
        if slot is None:
            return None
        is_light, ordinal = slot
        if is_light:
            return AbsoluteFormID(
                (0xFE << 24) | ((ordinal & 0xFFF) << 12) | (object_index & 0xFFF))
        return AbsoluteFormID((ordinal << 24) | (object_index & 0xFFFFFF))

    def plugin_name_for_absolute(self, value: int) -> Optional[str]:
        """Inverse of absolute_form_id: which load-order plugin an absolute
        FormID's high portion refers to (decoding the 0xFE light namespace)."""
        _, full_by_ord, light_by_ord = self._game_index_maps()
        hi = (value >> 24) & 0xFF
        if hi == 0xFE:
            return light_by_ord.get((value >> 12) & 0xFFF)
        return full_by_ord.get(hi)

    @staticmethod
    def object_index_for_absolute(value: int) -> int:
        """The object index carried by an absolute FormID — 12 bits for a light
        (0xFE) reference, 24 bits otherwise."""
        if ((value >> 24) & 0xFF) == 0xFE:
            return value & 0xFFF
        return value & 0xFFFFFF

    def _resolve_absolute_form_id(self, form_id: FormID,
                                   plugin: Plugin) -> Optional[AbsoluteFormID]:
        """Resolve a plugin-local FormID to an absolute FormID.

        The file_index byte of a FormID indexes into the plugin's master
        list. Index 0 = first master, index N = the plugin itself (where
        N = number of masters). The sentinel 0xFF is also treated as
        "this plugin's own record" — this covers in-memory records
        created via Plugin.get_next_form_id() before save-time fixup.
        """
        file_idx = form_id.file_index
        masters = plugin.header.masters
        num_masters = len(masters)

        if file_idx < num_masters:
            # References a master -- map to its game-style load-order index.
            return self.absolute_form_id(masters[file_idx],
                                         form_id.object_index)
        elif file_idx == num_masters or file_idx == self._LOCAL_SENTINEL:
            # This plugin's own record (either the real post-save
            # file_idx or the pre-save sentinel).
            plugin_name = plugin.file_path.name if plugin.file_path else None
            if not plugin_name:
                return None
            return self.absolute_form_id(plugin_name, form_id.object_index)
        else:
            # Invalid file index
            return None

    def get_override_chain(self, form_id: Union[BaseFormID, int]) -> Optional[OverrideChain]:
        """Get the override chain for a FormID.

        Returns an OverrideChain where [0] is the base and [-1] is the winner.
        Returns None if the FormID is not found in any loaded plugin.
        """
        # Read the index into a local before using it. Another thread
        # can call invalidate() between the None-check and the lookup,
        # which would otherwise be an AttributeError on None.
        index = self._override_index
        if index is None:
            self._build_override_index()
            index = self._override_index
            if index is None:      # invalidated again mid-build
                return None

        fid_val = form_id.value if isinstance(form_id, BaseFormID) else form_id
        return index.get(fid_val)

    def overridden_records(self):
        """Iterate all FormIDs that have more than one record (overrides).

        Yields (absolute_form_id: int, chain: OverrideChain) tuples.
        """
        # Same local-read discipline as get_override_chain: iterating
        # self._override_index directly would raise if another thread
        # invalidated it mid-iteration.
        index = self._override_index
        if index is None:
            self._build_override_index()
            index = self._override_index or {}

        for fid, chain in index.items():
            if len(chain) > 1:
                yield fid, chain

    def resolve_form_id(self, form_id: BaseFormID,
                        source_plugin: Plugin = None) -> Optional[Record]:
        """Resolve a FormID reference to the winning record.

        AbsoluteFormID: looked up directly (source_plugin not needed).
        LocalFormID: requires source_plugin to normalize first.
        """
        if isinstance(form_id, AbsoluteFormID):
            abs_val = form_id.value
        else:
            if source_plugin is None:
                raise TypeError(
                    "source_plugin is required when resolving a LocalFormID")
            abs_fid = self._resolve_absolute_form_id(form_id, source_plugin)
            if abs_fid is None:
                return None
            abs_val = abs_fid.value

        chain = self.get_override_chain(abs_val)
        if chain and len(chain) > 0:
            return chain[-1]  # Winner
        return None

    def resolve_reference(self, record: Record,
                          subrecord_sig: str) -> Optional[Record]:
        """Resolve a FormID subrecord to the record it references.

        Reads the FormID from the named subrecord on `record`, then
        resolves it through the override chain to the winning record.
        Returns None if the subrecord is missing or the target is not found.
        """
        sr = record.get_subrecord(subrecord_sig)
        if sr is None:
            return None
        form_id = sr.get_form_id()
        source_plugin = record.plugin
        if source_plugin is None:
            return None
        return self.resolve_form_id(form_id, source_plugin)

    def get_record_by_edid(self, signature: str,
                           editor_id: str) -> Optional[Record]:
        """Find the winning override of a record by signature and EditorID.

        Searches all loaded plugins in load order and returns the last
        (winning) copy found.  Returns None if no match exists.
        """
        winner = None
        for plugin in self:
            for record in plugin.get_records_by_signature(signature):
                if record.editor_id == editor_id:
                    winner = record
        return winner


    def __iter__(self):
        """Iterate over loaded plugins in load order."""
        for name in self.load_order:
            plugin = self._plugins.get(name)
            if plugin is not None:
                yield plugin

    def __len__(self) -> int:
        return sum(1 for p in self._plugins.values() if p is not None)

    def __repr__(self) -> str:
        loaded = sum(1 for p in self._plugins.values() if p is not None)
        return f"PluginSet({loaded}/{len(self.load_order)} loaded)"
