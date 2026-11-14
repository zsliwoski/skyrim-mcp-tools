"""MCP server exposing the Skyrim plugin tools."""
import functools
import os
import re
import threading
from pathlib import Path

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("skyrim-mcp-tools")

# Tools that read-modify-write plugin files. Concurrent calls (e.g. several clone_record calls in one
# batch) would load the same file, each add a record and overwrite each other, so they run one at a time.
WRITE_TOOLS = {
    'create_record', 'add_quest_alias', 'add_quest_stage_script', 'add_npc_chat', 'write_npc_plugin', 'create_npc',
    'edit_npc', 'add_fetch_quest', 'add_kill_quest', 'add_plant_quest', 'add_radiant_quest', 'add_quest',
    'clone_record', 'clone_records', 'create_cell', 'make_reference_persistent', 'snapshot_plugin',
    'restore_snapshot', 'install_plugin', 'edit_text', 'edit_condition', 'edit_script', 'edit_npc_face',
    'paint_npc_face', 'edit_record', 'package_plugin', 'place_item', 'edit_texts', 'edit_book',
    'create_enchantment', 'create_script_effect', 'enchant_item', 'create_spell', 'create_script',
    'create_shader', 'create_unique_item', 'deploy_plugin', 'copy_subrecords', 'remove_records',
    'drop_conditions', 'create_scroll', 'create_spell_tome', 'place_items', 'create_spell_set',
    'rename_script', 'move_records', 'create_script_effects', 'create_enchanted_item', 'build_lore_db', 'build_api_db',
}
_write_lock = threading.RLock()
_register_tool = mcp.tool


def _tool(*args, **kwargs):
    register = _register_tool(*args, **kwargs)

    def decorate(fn):
        if fn.__name__ in WRITE_TOOLS:
            @functools.wraps(fn)
            def locked(*a, **k):
                with _write_lock:
                    return fn(*a, **k)
            return register(locked)
        return register(fn)

    return decorate


mcp.tool = _tool

DATA_DIR = Path(os.environ.get(
    "SKYRIM_DATA_DIR", r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"))
DEFAULT_PLUGINS = [p for p in os.environ.get("SKYRIM_PLUGINS", "Skyrim.esm").split(os.pathsep) if p]

_models = {}
_stores = {}
_lock = threading.Lock()


def _plugin_path(name):
    """Absolute path for a plugin name: the output folder wins over the Data folder, so plugins being
    authored resolve by bare file name."""
    p = Path(name)
    if p.is_absolute():
        return str(p)
    return str(OUTPUT_DIR / p if (OUTPUT_DIR / p).exists() else DATA_DIR / p)


def _with_target(plugins, target_plugin):
    """Load list that also includes target_plugin when it exists, so records already in it can be referenced."""
    names = list(plugins or DEFAULT_PLUGINS)
    path = _plugin_path(target_plugin)
    if Path(path).exists() and all(_plugin_path(n).lower() != path.lower() for n in names):
        names.append(path)
    return names


def get_model(plugins):
    """Cached Model for a plugin list (masters in the Data folder load automatically)."""
    from tools.core.quest_model import Model

    paths = tuple(_plugin_path(p) for p in (plugins or DEFAULT_PLUGINS))
    with _lock:
        if paths not in _models:
            _models[paths] = Model(paths, lambda _msg: None, resolve_refs=False)
        return _models[paths]


def npc_tools(plugins):
    from tools.npcs.npc_tools import NpcTools
    return NpcTools(get_model(plugins))


def quest_tools(plugins):
    from tools.quests.quest_tools import QuestTools
    return QuestTools(get_model(plugins))


def script_tools(plugins):
    from tools.papyrus.pex_tools import ScriptStore, script_bsas
    from tools.papyrus.script_tools import ScriptTools
    m = get_model(plugins)
    key = tuple(str(p.file_path) for p in m.plugins)
    with _lock:
        if key not in _stores:
            _stores[key] = ScriptStore(DATA_DIR, script_bsas(key))
    return ScriptTools(m, _stores[key])


@mcp.tool()
def search_npcs(query: str, limit: int = 20, plugins: list[str] | None = None) -> list[dict]:
    """Find NPC_ records by editor ID, display name, or form ID (hex).

    plugins: plugin file names (relative to the Skyrim Data folder) or absolute paths, in load
    order; defaults to Skyrim.esm. Masters found in the same folder load automatically.
    """
    t = npc_tools(plugins)
    return [t.summary(r) for r in t.find(query, limit)]


@mcp.tool()
def get_npc(identifier: str, plugins: list[str] | None = None) -> dict:
    """Get an NPC's definition and associated data.

    identifier: editor ID (e.g. "Lydia"), display name, or form ID in hex. Returns stats and
    flags, race, class, voice, template, outfits, keywords, factions, spells, perks, AI packages,
    inventory, skills, AI settings and attached scripts (with properties), plus related data:
    quests whose aliases or scripts reference the NPC, dialogue topics they speak, and NPCs
    that use them as a template. If several NPCs match, a list of candidates is returned instead.
    plugins: plugin file names or absolute paths in load order; defaults to Skyrim.esm.
    """
    t = npc_tools(plugins)
    hits = t.find(identifier)
    if not hits:
        return {"error": f"no NPC matches {identifier!r}"}
    if len(hits) > 1:
        return {"ambiguous": True, "candidates": [t.summary(r) for r in hits]}
    return t.decode(hits[0])


@mcp.tool()
def get_npc_dialogue(identifier: str, limit: int = 500, include_generic: bool = True,
                     plugins: list[str] | None = None) -> dict:
    """Get every dialogue line (INFO response) an NPC speaks.

    identifier: NPC editor ID, display name, or form ID in hex. Lines are matched by the INFO
    speaker field, by GetIsID conditions on the NPC, and by GetIsAliasRef conditions for quest
    aliases that the NPC fills. Also returns lines from scene dialogue actions performed by those
    aliases ("scene") and, if include_generic, shared lines gated only by the NPC's voice type,
    race, or faction ("generic"; these are approximate and may be spoken by many NPCs).
    plugins: plugin file names or absolute paths in load order; defaults to Skyrim.esm.
    """
    from tools.dialogue.dialogue_tools import DialogueTools
    m = get_model(plugins)
    npcs = npc_tools(plugins)
    hits = npcs.find(identifier)
    if not hits:
        return {"error": f"no NPC matches {identifier!r}"}
    if len(hits) > 1:
        return {"ambiguous": True, "candidates": [npcs.summary(r) for r in hits]}
    res = DialogueTools(m).lines(hits[0], include_generic)
    out = {"npc": npcs.summary(hits[0]),
           "counts": {k: len(v) for k, v in res.items()}}
    for k, v in res.items():
        out[k] = v[:limit]
    return out


@mcp.tool()
def search_quests(query: str, limit: int = 20, plugins: list[str] | None = None) -> list[dict]:
    """Find quests (QUST) by editor ID, display name, or form ID (hex).

    plugins: plugin file names (relative to the Skyrim Data folder) or absolute paths, in load
    order; defaults to Skyrim.esm. Masters found in the same folder load automatically.
    """
    t = quest_tools(plugins)
    return [t.summary(r) for r in t.find(query, limit)]


@mcp.tool()
def get_quest(identifier: str, include_responses: bool = False, plugins: list[str] | None = None) -> dict:
    """Get a quest and its associated data.

    identifier: editor ID (e.g. "MQ101"), display name, or form ID in hex. Returns type, priority,
    scripts (with properties and stage fragments), aliases (what fills each, flags, alias
    scripts), stages with journal log text, objectives with their target aliases, scenes,
    dialogue topics with response counts and speakers, and the NPCs and locations involved.
    include_responses: also return the dialogue line text for every topic (can be large).
    If several quests match, a list of candidates is returned instead.
    plugins: plugin file names or absolute paths in load order; defaults to Skyrim.esm.
    """
    t = quest_tools(plugins)
    hits = t.find(identifier)
    if not hits:
        return {"error": f"no quest matches {identifier!r}"}
    if len(hits) > 1:
        return {"ambiguous": True, "candidates": [t.summary(r) for r in hits]}
    return t.decode(hits[0], include_responses)


@mcp.tool()
def get_quest_structure(identifier: str, plugins: list[str] | None = None) -> dict:
    """Get a quest's logic structure with its compiled Papyrus scripts interpreted.

    identifier: quest editor ID, display name, or form ID in hex. Returns stages (journal log plus
    the fragment function bound to each stage, with its calls and quest-state effects such as
    SetStage/SetObjectiveDisplayed/Start), stage_transitions (SetStage edges between stages),
    objectives with the stages that drive them, quest scripts, alias scripts and scene scripts/phase
    fragments (each analyzed: properties with their bound records, functions/events, calls,
    property reads/writes, inheritance), and dialogue topic-info fragments. Scripts are read from
    loose Scripts files and the BSAs named after the loaded plugins.
    plugins: plugin file names or absolute paths in load order; defaults to Skyrim.esm.
    """
    t = script_tools(plugins)
    hits = t.q.find(identifier)
    if not hits:
        return {"error": f"no quest matches {identifier!r}"}
    if len(hits) > 1:
        return {"ambiguous": True, "candidates": [t.q.summary(r) for r in hits]}
    return t.structure(hits[0])


@mcp.tool()
def get_script(name: str, include_source: bool = False, plugins: list[str] | None = None) -> dict:
    """Interpret a compiled Papyrus script (.pex) from loose files or the plugins' BSAs.

    name: script name (e.g. "QF_MQ101_0003372B" or "DialogueFollowerScript"). Returns what it
    extends (and the inheritance chain), variables, properties, states, and every function/event
    with parameters, calls made, property reads/writes and branch counts, plus the types it
    depends on. include_source: also return decompiled pseudo-source (bytecode steps with jump
    labels). plugins: plugin file names or absolute paths in load order; defaults to Skyrim.esm.
    """
    return script_tools(plugins).describe(name, include_source)


@mcp.tool()
def detect_skse() -> dict:
    """Check whether the Skyrim Script Extender (SKSE) is present, without changing anything.

    Reports the SKSE loader and runtime DLLs beside the game, SKSE plugins in Data/SKSE/Plugins, SKSE script
    files in Data/Scripts, whether the Papyrus compiler's source folders contain SKSE sources (needed to compile
    SKSE calls), and which scripts in the output folder already reference SKSE-only types. See the
    skyrim-skse-scripting skill: prefer vanilla Papyrus and ask the user before using SKSE.
    """
    from tools.papyrus.papyrus_build import SOURCE_CACHE, skse_source_dirs
    from tools.papyrus.skse import detect_skse as detect
    return detect(DATA_DIR, OUTPUT_DIR,
                  [SOURCE_CACHE / "Scripts" / "Source"] + skse_source_dirs(DATA_DIR))


OUTPUT_DIR = Path(os.environ.get("SKYRIM_OUTPUT_DIR", Path(__file__).resolve().parent / "output"))
_drafts = {}


def npc_builder(plugins):
    from tools.npcs.npc_builder import NpcBuilder
    return NpcBuilder(get_model(plugins), OUTPUT_DIR, DATA_DIR)


def _draft(draft_id):
    if draft_id not in _drafts:
        raise ValueError(f"no draft {draft_id!r}; open drafts: {sorted(_drafts)}")
    return _drafts[draft_id]


@mcp.tool()
def search_forms(signature: str, query: str = "", limit: int = 25,
                 plugins: list[str] | None = None) -> list[dict] | dict:
    """Find records to reference when building an NPC.

    signature: a record type such as RACE, VTYP (voice type), CLAS, FACT, SPEL, PERK, KYWD, PACK,
    OTFT (outfit), ARMO, WEAP, LVLI, CSTY, NPC_, QUST, MGEF, ENCH or EFSH (shader). An unsupported type returns
    an error listing the supported ones. query: substring of the editor ID or name
    (empty lists the first records). Identifiers returned here can be used in NPC draft fields.
    """
    from tools.core.quest_model import LOAD_SIGS
    builder = npc_builder(plugins)
    sig = signature.upper()
    if sig not in LOAD_SIGS:
        return {"error": f"{signature!r} records are not loaded; supported: {', '.join(sorted(LOAD_SIGS))}"}
    return builder.resolver.search([sig], query, limit)


def _cell_builder(plugins, target_plugin=None):
    """Builder for cell lookups; target_plugin (an output plugin being authored) is loaded too."""
    return npc_builder(_with_target(plugins, target_plugin) if target_plugin else plugins)


@mcp.tool()
def search_cells(query: str, limit: int = 25, plugins: list[str] | None = None,
                 target_plugin: str | None = None) -> list[dict]:
    """Find cells (interior or exterior) by editor ID or name, for placing a custom NPC.

    Returns form ID, editor ID, name, plugin, whether it is an interior and, for exterior cells,
    the grid. Use the editor ID/name as placement.cell, or worldspace plus grid_x/grid_y.
    target_plugin: an output plugin being authored, so its own cells are searched too.
    To list every cell a plugin defines, use list_records(plugin, signature="CELL").
    """
    return _cell_builder(plugins, target_plugin).cells.search(query, limit)


@mcp.tool()
def search_references(base: str, worldspace: str | None = None, cell: str | None = None,
                      x: float | None = None, y: float | None = None, radius: float | None = None,
                      limit: int = 25, plugins: list[str] | None = None) -> list[dict]:
    """Find placed references (REFR/ACHR) of a base form, e.g. every WoodChoppingBlock near the Riverwood mill.

    base: editor ID, name or form ID of the base object. Narrow with worldspace (e.g. Tamriel) or cell,
    and/or x, y, radius (game units); results sort by distance to x, y. Each hit gives the reference ID,
    plugin, worldspace, cell, grid and position, ready to use as placement coordinates.
    """
    from tools.cells.ref_search import search_references as run
    try:
        return run(npc_builder(plugins), base, worldspace, cell, x, y, radius, limit)
    except ValueError as e:
        return [{"error": str(e)}]


@mcp.tool()
def create_record(target_plugin: str, signature: str, editor_id: str, subrecords: list[dict],
                  plugins: list[str] | None = None) -> dict:
    """Create a new record of any type in target_plugin (created if missing).

    subrecords: ordered list of {"sig": "FULL", <one of text|hex|form|u32|u16|u8|i32|float|empty>: value}.
    EDID is added automatically. `form` resolves an editor ID, name or form ID and adds the master.
    Check an existing record of the same type with get_record first to copy its subrecord order and sizes.
    """
    try:
        from tools.records.record_create import create_record as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, signature, editor_id, subrecords)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_quest_alias(quest: str, target_plugin: str, name: str, reference: str | None = None,
                    npc: str | None = None, objectives: list[int] | None = None,
                    reference_plugin: str = "Skyrim.esm", plugins: list[str] | None = None) -> dict:
    """Add an alias to a quest in target_plugin, optionally making it the compass-marker target of objectives.

    Give either reference (editor ID of a reference in target_plugin, or a hex reference ID from
    search_references in reference_plugin; it should be persistent) or npc (editor ID/name/form ID).
    objectives: objective numbers whose target becomes this alias.
    """
    try:
        from tools.quests.quest_edit import add_quest_alias as run
        res = run(npc_builder(plugins), quest, target_plugin, name, reference, reference_plugin, npc,
                  objectives or [])
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_quest_stage_script(quest: str, target_plugin: str, stages: dict, properties: dict | None = None,
                           plugins: list[str] | None = None) -> dict:
    """Run Papyrus code when quest stages are set (a quest QF fragment script, compiled for you).

    stages: {"20": "SetObjectiveDisplayed(30)\\nCompleteQuest()"}; code runs inside the quest and the
    stages must exist. properties: {name: form} become Form properties usable in the code; each alias also
    gets a ReferenceAlias property Alias_<name>. Replaces the quest's earlier stage script. Run
    package_plugin afterwards.
    """
    try:
        from tools.quests.quest_edit import add_quest_stage_script as run
        res = run(npc_builder(plugins), quest, target_plugin, stages, properties)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_npc_chat(target_plugin: str, spec: dict, plugins: list[str] | None = None) -> dict:
    """Add a scripted conversation between a follower and another NPC, started by a line the follower says.

    spec: {scene_editor_id, quest_editor_id (chat quest, created if absent; each NPC gets an alias),
    speaker (the follower), partner (the other NPC), trigger_line or trigger_lines (what the follower says
    first), lines: [{who: "speaker"|"partner", text}], dialogue_quest_editor_id (where the trigger idle goes;
    default <speaker>DialogueQuest), partner_reference ('Skyrim.esm:013487' placed reference of the partner,
    gives a distance guard), distance (default 900), hired (only while following; default true),
    random_percent (default 10), once (play one time; default true), conditions (extra, as for edit_npc)}.
    Both NPCs must be in target_plugin or loaded plugins. Run validate_plugin, package_plugin and
    install_plugin afterwards. One call adds one scene; call again for more topics with the same pair.
    """
    try:
        from tools.quests.chat_builder import add_npc_chat as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def describe_npc_fields() -> dict:
    """List every field accepted by the NPC draft tools (create_npc_draft, update_npc_draft, create_npc)."""
    from tools.npcs.npc_builder import FIELDS
    return FIELDS


@mcp.tool()
def create_npc_draft(draft_id: str, fields: dict, plugins: list[str] | None = None) -> dict:
    """Start an in-memory draft of a custom NPC. Nothing is written until write_npc_plugin.

    draft_id: a name for this draft. fields: see describe_npc_fields. Clone an existing NPC with
    clone_from plus overrides (name, editor_id are required), or build from scratch with race,
    npc_class and the other fields. Outfit, placement and dialogue can be included here or added
    later with update_npc_draft / add_npc_dialogue. Returns validation results.
    """
    if draft_id in _drafts:
        return {"error": f"draft {draft_id!r} already exists; use update_npc_draft or pick another id"}
    _drafts[draft_id] = {"spec": dict(fields), "plugins": plugins}
    return {"draft_id": draft_id, **npc_builder(plugins).validate(fields)}


@mcp.tool()
def update_npc_draft(draft_id: str, fields: dict, remove: list[str] | None = None) -> dict:
    """Set fields on a draft (list fields such as factions replace the previous value).

    remove: field names to delete from the draft. Returns validation results.
    """
    d = _draft(draft_id)
    d["spec"].update(fields)
    for k in remove or []:
        d["spec"].pop(k, None)
    return {"draft_id": draft_id, **npc_builder(d["plugins"]).validate(d["spec"])}


@mcp.tool()
def add_npc_dialogue(draft_id: str, lines: list[str] | None = None, prompt: str = "", kind: str = "topic",
                     answers: list[list[str]] | None = None, pick: str = "random") -> dict:
    """Add a dialogue topic to a draft. The writer creates a quest, an alias for the NPC, topics and responses.

    kind: "topic" (needs prompt, the player's menu text), "greeting" (spoken on approach) or
    "goodbye". lines: the NPC's response lines, spoken in order as one reply.
    answers: instead of lines, several alternative replies to the same prompt (a list of line lists),
    e.g. for "Heard any rumors?". pick: "random" (any answer each time) or "in_order" (each once, then the
    last repeats).
    """
    d = _draft(draft_id)
    entry = {"kind": kind, "prompt": prompt}
    entry.update({"answers": answers, "pick": pick} if answers else {"lines": lines or []})
    d["spec"].setdefault("dialogue", []).append(entry)
    return {"draft_id": draft_id, **npc_builder(d["plugins"]).validate(d["spec"])}


@mcp.tool()
def get_npc_draft(draft_id: str) -> dict:
    """Return a draft's current fields, or the list of open drafts if draft_id is empty."""
    if not draft_id:
        return {"drafts": sorted(_drafts)}
    return _draft(draft_id)["spec"]


@mcp.tool()
def validate_npc_draft(draft_id: str) -> dict:
    """Resolve every reference in a draft and report errors and warnings."""
    d = _draft(draft_id)
    return npc_builder(d["plugins"]).validate(d["spec"])


@mcp.tool()
def write_npc_plugin(draft_id: str, output_dir: str | None = None, overwrite: bool = False,
                     target_plugin: str | None = None) -> dict:
    """Build the draft into an ESP and save it (default folder: output next to server.py, or SKYRIM_OUTPUT_DIR).

    Fails if validation reports errors or the file exists (unless overwrite). The draft stays open.
    With target_plugin (file name or absolute path), the NPC, placement and dialogue are added to that
    existing plugin instead (backed up to .bak first); official masters are refused.
    """
    d = _draft(draft_id)
    try:
        res = npc_builder(d["plugins"]).write(d["spec"], output_dir, overwrite, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def create_npc(fields: dict, output_dir: str | None = None, overwrite: bool = False,
               plugins: list[str] | None = None, target_plugin: str | None = None) -> dict:
    """Create a custom NPC plugin in one call (see describe_npc_fields for the fields).

    Validates, then writes the ESP immediately, returning its path, masters and the records
    created. Use the draft tools instead to build the NPC up step by step.
    With target_plugin (file name or absolute path), adds to that existing plugin instead of
    creating a new one; cells and dialogue groups are merged, editor IDs must stay unique.
    """
    try:
        res = npc_builder(plugins).write(fields, output_dir, overwrite, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def edit_npc(identifier: str, fields: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Change fields of an existing NPC, using the same fields as create_npc (only those given change).

    The NPC may be a vanilla/loaded record (an override is added to target_plugin, i.e. a patch) or a
    record already in target_plugin (edited in place). target_plugin is a file name (output folder,
    then Data folder) or absolute path, created if missing. List fields (factions, spells, perks,
    keywords, inventory, packages) replace the whole list. placement and dialogue add a new reference
    or topics for that NPC. clone_from is not allowed. The file is backed up to .bak.
    """
    try:
        res = npc_builder(_with_target(plugins, target_plugin)).edit_npc(identifier, fields, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_fetch_quest(identifier: str, spec: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Add a complete fetch quest to an NPC that already exists in target_plugin.

    Creates a quest item (cloned from a vanilla MISC) with a pickup script, a reference placed in a
    cell, a QUST (stages 10/20/30, objectives 10/20) and four dialogue topics (offer, reminder,
    turn-in with gold reward, afterwards). spec keys: quest_editor_id, quest_name, item_editor_id,
    item_name, item_clone_from, placement {worldspace, grid_x, grid_y or cell, x, y, z, persistent},
    log {"10","20","30": journal text}, objectives {"10","20": text}, offer/reminder/turn_in/done
    {prompt, lines}, reward_gold, stolen (default false). The item reference gets the player as owner so
    taking it from an owned cell is not theft (stolen: true keeps the cell owner), and the QuestItem alias
    carries the Quest Object flag so the item cannot be dropped. Quest dialogue stays available while the
    NPC is a follower.
    Run package_plugin afterwards.
    """
    try:
        _auto_snapshot(target_plugin, "pre_fetch_quest")
        res = npc_builder(plugins).add_fetch_quest(identifier, spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_kill_quest(identifier: str, spec: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Add a kill quest, unlocked by another quest, to an NPC that already exists in target_plugin.

    Places a persistent actor (target_base) with an OnDeath script, creates a side QUST (stages 10/20/30,
    objectives 10/20) and dialogue topics: offer (shown once prerequisite_quest reaches prerequisite_stage,
    default 30), optional "during" flavor topics, turn-in with reward_gold, and afterwards. spec keys:
    quest_editor_id, quest_name, prerequisite_quest, target_base, target_name, placement {worldspace,
    grid_x, grid_y or cell, x, y, z}, log {"10","20","30"}, objectives {"10","20"}, offer/turn_in/done
    {prompt, lines}, during {prompt, lines, topics: [{prompt, lines}]} (a parent topic with nested
    flavor subtopics), reward_gold. Run package_plugin afterwards.
    """
    try:
        _auto_snapshot(target_plugin, "pre_kill_quest")
        res = npc_builder(plugins).add_kill_quest(identifier, spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_plant_quest(identifier: str, spec: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Add a plant-the-item quest, unlocked by another quest, to an NPC that already exists in target_plugin.

    Creates a custom book (cloned from a vanilla BOOK, with your text) that is given to the player and a
    script that advances the quest when it is put into a Skyrim.esm container (container_ref, hex ref ID
    from search_references), a unique reward weapon (cloned from a vanilla WEAP with an existing ENCH), a
    side QUST (stages 10/20/30, objectives 10/20) and nested dialogue. spec keys: quest_editor_id,
    quest_name, prerequisite_quest, book {editor_id, name, text, clone_from}, container_ref,
    reward_weapon {editor_id, name, clone_from, enchantment, damage, value, charge}, log, objectives,
    offer/turn_in/done {prompt, lines}, during {prompt, lines, topics}, lost_book {prompt, lines},
    reward_gold. Run package_plugin afterwards.
    """
    try:
        _auto_snapshot(target_plugin, "pre_plant_quest")
        res = npc_builder(plugins).add_plant_quest(identifier, spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_radiant_quest(identifier: str, spec: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Add a repeatable collection quest to an NPC in target_plugin: each offer picks a random item and amount.

    identifier: the NPC (must be in target_plugin). spec keys: quest_editor_id, quest_name, items
    [{item, name (plural, shown in the objective), min, max, unit_reward (gold each)}], offer/reminder/
    turn_in {prompt, lines}, optional conditions (offer topic, same format as dialogue conditions),
    log {"10","20"}, properties {name: form} usable in code, on_accept / on_complete (Papyrus run on the
    offer / successful hand-in; `pl` is the player, e.g. to rank up a faction). The offer shows whenever the
    contract is not active, so it repeats; turn_in checks the inventory in script and pays. A snapshot is
    taken first. Run package_plugin afterwards.
    """
    try:
        _auto_snapshot(target_plugin, "pre_radiant_quest")
        res = npc_builder(plugins).add_radiant_quest(identifier, spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def add_quest(kind: str, identifier: str, spec: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Add a quest to an NPC in target_plugin with one tool; kind is 'fetch', 'kill', 'plant' or 'radiant'.

    The spec is the one documented on add_fetch_quest / add_kill_quest / add_plant_quest / add_radiant_quest. A snapshot of
    the plugin is taken first (see list_snapshots / restore_snapshot). Run validate_plugin afterwards.
    """
    try:
        _auto_snapshot(target_plugin, f"pre_{kind}_quest")
        res = npc_builder(plugins).add_quest(kind, identifier, spec, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


def _auto_snapshot(plugin, label):
    from tools.records.maintenance import snapshot
    from tools.core.plugin_edit import resolve_target_path
    path = resolve_target_path(plugin, OUTPUT_DIR, DATA_DIR)
    if path.exists():
        snapshot(path, label)


def _for_each(plugin, fn):
    """fn(plugin) for one plugin name, or {name: fn(name)} for a list of them."""
    if isinstance(plugin, str):
        return fn(plugin)
    if not plugin:
        return {"error": "give at least one plugin"}
    return {name: fn(name) for name in plugin}


def _out_path(plugin):
    from tools.core.plugin_edit import resolve_target_path
    return resolve_target_path(plugin, OUTPUT_DIR, DATA_DIR, must_exist=True)


@mcp.tool()
def create_spell(target_plugin: str, editor_id: str, name: str, effects: list[dict], kind: str = "ability",
                 delivery: str | None = None, cost: int = 0, cast_duration: float = 0.0, range: float = 0.0,
                 description: str = "", cast: str | None = None,
                 plugins: list[str] | None = None) -> dict:
    """Create a spell (SPEL) from existing magic effects.

    effects: [{"effect": MGEF editor ID/name/form ID, "magnitude": 0, "area": 0, "duration": 0}].
    kind: "ability" (permanent self effect, add with Actor.AddSpell; effects must be constant-self MGEFs such as
    EnchFortify<School>ConstantSelf), "spell", "power" or "lesser_power". delivery (non-abilities): self, touch,
    aimed, target_actor, target_location. cast: fire_and_forget (default), concentration or constant; the MGEF's
    own cast type and delivery must match. cost 0 = auto-calculated. See the skyrim-enchantments skill.
    """
    try:
        _auto_snapshot(target_plugin, "pre_spell")
        from tools.records.spell import create_spell as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, editor_id, name, effects, kind,
                  delivery, cost, cast_duration, range, description, cast)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def create_script(target_plugin: str, script_name: str, source: str, plugins: list[str] | None = None) -> dict:
    """Compile a Papyrus script into the Scripts folder beside target_plugin; the .psc is kept in Scripts/Source.

    source must start with `Scriptname <script_name> ...`. A compile error returns the compiler output. The
    script is not attached to any record; install_plugin ships it once a VMAD names it. For an enchantment
    effect script use create_script_effect.
    """
    try:
        from tools.records.spell import create_script as run
        return run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, script_name, source)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def create_script_effect(target_plugin: str, editor_id: str, name: str, script_name: str, source: str,
                         description: str = "", kind: str = "armor", enchantment_editor_id: str | None = None,
                         cost: int = 0, extra_effects: list[dict] | None = None, duration: int | None = None,
                         base_effect: str | None = None, drop_conditions: list[int] | None = None,
                         delivery: str | None = None, plugins: list[str] | None = None) -> dict:
    """Create a magic effect driven by a Papyrus script, and an enchantment that uses it, in one call.

    source: the full script, starting `Scriptname <script_name> Extends ActiveMagicEffect`; it is compiled
    first (a compile error changes nothing). Typical events: OnEffectStart(Actor akTarget, Actor akCaster),
    OnUpdate (RegisterForSingleUpdate), OnEffectFinish; undo any state there, because armor effects stop
    when the item is removed. kind: "armor" (constant, self), "weapon" (on hit) or "staff". The ENCH is named
    <editor_id>Ench unless enchantment_editor_id is given ("" skips it). extra_effects adds vanilla effects to
    the same enchantment, e.g. [{"effect": "EnchParalysisFFContact", "duration": 600}]. duration is the script
    effect's own (default 1 for weapons, 0 for armor). Apply it with enchant_item.
    base_effect: a vanilla MGEF to imitate (e.g. "EnchParalysisFFContact"); the new effect copies its data,
    keywords and conditions so weapons keep their charge bar and recharge option. drop_conditions: condition
    indexes of that copy to remove (see diff_records/list_conditions; vanilla paralysis has a 25% chance one).
    description is shown in the effect's tooltip. See the skyrim-enchantments skill.
    delivery: "touch" (a short-range ray: the script gets a distance check, as touch spells do not work with
    script effects), "aimed", "ray" (concentration beam) or "self": makes an effect for a spell, scroll or
    staff by copying the matching vanilla spell effect (no ENCH unless enchantment_editor_id is given).
    """
    try:
        _auto_snapshot(target_plugin, "pre_script_effect")
        from tools.records.enchant import create_script_effect as run
        def rebuild():
            with _lock:
                _models.clear()
            return npc_builder(_with_target(plugins, target_plugin))
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, editor_id, name, script_name,
                  source, description, kind, enchantment_editor_id, cost, rebuild, extra_effects, duration,
                  base_effect, drop_conditions, delivery)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def enchant_item(item: str, target_plugin: str, enchantment: str, charge: int | None = None,
                 replace: bool = False, plugins: list[str] | None = None) -> dict:
    """Attach an enchantment (ENCH) to a weapon or armor/jewelry in target_plugin.

    Writes EITM (and EAMT charge for weapons, default 500; worn items are constant so charge is refused).
    Refuses an already enchanted item unless replace is true. The item may be vanilla (an override is
    added) or already in target_plugin.
    """
    try:
        from tools.records.enchant import enchant_item as run
        res = run(_editor(_with_target(plugins, target_plugin)), item, target_plugin, enchantment, charge, replace)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


def _refresher(plugins, target_plugin):
    """Callable returning a fresh (builder, editor) that sees everything written so far."""
    def refresh():
        with _lock:
            _models.clear()
        loaded = _with_target(plugins, target_plugin)
        return npc_builder(loaded), _editor(loaded)
    return refresh


@mcp.tool()
def create_script_effects(target_plugin: str, items: list[dict], plugins: list[str] | None = None) -> list[dict]:
    """Create several script enchantments, and attach each to its item, in one call.

    items: list of dicts with the create_script_effect fields (editor_id, name, script_name, source and the
    optional description, kind, cost, extra_effects, ...) plus optional `item` (the ARMO/WEAP editor ID to
    enchant with the new ENCH) and `replace` (true to overwrite its current enchantment). One snapshot is
    taken first. Items are done in order; a failing one is reported and the rest still run (a compile error
    changes nothing for that item). Returns one result per item: the effect/enchantment, plus `enchanted`.
    """
    results = []
    _auto_snapshot(target_plugin, "pre_script_effects")
    for spec in items:
        spec = dict(spec)
        item, replace = spec.pop("item", None), spec.pop("replace", False)
        spec.pop("target_plugin", None)
        spec.pop("plugins", None)
        res = create_script_effect(target_plugin=target_plugin, plugins=plugins, **spec)
        if "error" not in res and item:
            ench = (res.get("enchantment") or {}).get("editor_id")
            if ench:
                res["enchanted"] = enchant_item(item=item, target_plugin=target_plugin, enchantment=ench,
                                                replace=replace, plugins=plugins)
            else:
                res["enchanted"] = {"error": "no enchantment was created to attach"}
        results.append(res)
    return results


@mcp.tool()
def create_enchanted_item(source: str, target_plugin: str, editor_id: str, name: str, script_name: str,
                          script_source: str, effect_description: str = "", description: str | None = None,
                          kind: str | None = None, cost: int = 200, charge: int | None = None,
                          extra_effects: list[dict] | None = None, duration: int | None = None,
                          cell: str | None = None, x: float | None = None, y: float | None = None,
                          z: float | None = None, near: str | None = None, rotation_z: float = 0.0,
                          plugins: list[str] | None = None) -> dict:
    """Make a new item with its own scripted enchantment (SKSE or vanilla Papyrus), and optionally place it.

    One call: compile-check script_source (nothing changes if it fails), clone `source` (a vanilla ARMO or
    WEAP) as `editor_id`/`name`, create the script effect <editor_id>Effect and enchantment
    <editor_id>EffectEnch, attach it, and when `cell` is given place the item (x/y/z, or near=something in
    the cell). kind is inferred from the source ("armor" or "weapon"); weapons take charge and duration
    (script effect length, default 1). cost is the enchantment's gold cost (200 avoids the zero-cost
    warning). script_source is the full ActiveMagicEffect script. Uses the same pieces as clone_record,
    create_script_effect, enchant_item and place_item, with one snapshot first. Check SKSE use with
    detect_skse and ask the user before using SKSE calls (see the skyrim-skse-scripting skill).
    """
    from tools.records.maintenance import check_script
    try:
        checked = check_script(script_source)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}
    if not checked.get("ok"):
        return {"error": "script does not compile; nothing was changed", "compiler": checked.get("output")}
    kind = kind or {"ARMO": "armor", "WEAP": "weapon"}.get(
        (resolve_form(source, plugins=plugins).get("matches") or [{}])[0].get("type"))
    if kind not in ("armor", "weapon"):
        return {"error": "source must be an ARMO or WEAP (or pass kind='armor' or 'weapon')"}
    _auto_snapshot(target_plugin, "pre_enchanted_item")
    steps = {}
    steps["item"] = clone_record(source=source, target_plugin=target_plugin, editor_id=editor_id, name=name,
                                 description=description, plugins=plugins)
    if "error" in steps["item"]:
        return {"error": steps["item"]["error"], "steps": steps}
    steps["effect"] = create_script_effect(target_plugin=target_plugin, editor_id=f"{editor_id}Effect",
                                           name=name, script_name=script_name, source=script_source,
                                           description=effect_description, kind=kind, cost=cost,
                                           extra_effects=extra_effects, duration=duration, plugins=plugins)
    ench = (steps["effect"].get("enchantment") or {}).get("editor_id")
    if "error" in steps["effect"] or not ench:
        return {"error": steps["effect"].get("error", "no enchantment was created"), "steps": steps}
    with _lock:
        _models.clear()
    steps["enchant"] = enchant_item(item=editor_id, target_plugin=target_plugin, enchantment=ench,
                                    charge=charge, plugins=plugins)
    if "error" in steps["enchant"]:
        return {"error": steps["enchant"]["error"], "steps": steps}
    with _lock:
        _models.clear()
    if cell:
        steps["placed"] = place_item(base=editor_id, target_plugin=target_plugin, cell=cell, x=x, y=y, z=z,
                                     rotation_z=rotation_z, editor_id=f"{editor_id}Ref", near=near,
                                     plugins=plugins)
        if "error" in steps["placed"]:
            return {"error": steps["placed"]["error"], "steps": steps}
    return {"item": editor_id, "enchantment": ench, "script": script_name, "steps": steps,
            "next": "validate_plugin, then deploy_plugin(mode='both') and restart Skyrim; "
                    f"enchantment_test_plan('{target_plugin}', '{editor_id}') lists the in-game checks."}


@mcp.tool()
def create_scroll(target_plugin: str, editor_id: str, name: str, effects: list[dict], delivery: str = "aimed",
                  description: str = "", cost: int = 0, plugins: list[str] | None = None) -> dict:
    """Create a scroll (SCRL) that casts the given effects once, modelled on a vanilla scroll.

    effects: [{"effect": MGEF, "magnitude": 0, "area": 0, "duration": 0}]; the MGEFs' own delivery must
    match delivery (self, touch, aimed, target_actor, target_location). Place it with place_item or
    add it with player.additem (item_console_commands).
    """
    try:
        _auto_snapshot(target_plugin, "pre_scroll")
        from tools.records.magic_items import create_scroll as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, editor_id, name, effects,
                  delivery, description, cost)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def create_spell_tome(target_plugin: str, editor_id: str, name: str, spell: str, text: str = "",
                      value: int | None = None, plugins: list[str] | None = None) -> dict:
    """Create a spell tome (BOOK) that teaches `spell`, a SPEL already in target_plugin, when read.

    text is the book body (see the skyrim-book-writing skill). value defaults to the vanilla tome's price.
    """
    try:
        _auto_snapshot(target_plugin, "pre_tome")
        from tools.records.magic_items import create_spell_tome as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, editor_id, name, spell,
                  text, value)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def create_shader(target_plugin: str, editor_id: str, color: str, source: str = "MagicArmorEbonyFleshFXS",
                  plugins: list[str] | None = None) -> dict:
    """Create an effect shader (EFSH) by cloning a vanilla one and recolouring it, e.g. a gold skin tint.

    color: '#RRGGBB'. source: the vanilla shader to copy (default Ebony Flesh; find others with
    search_forms("EFSH", ...)). Play it from a script with
    `(Game.GetFormFromFile(0x000B2C, "Plugin.esp") as EffectShader).Play(akTarget)` (local ID, no load-order
    byte). The colour bytes are a best guess, so check the look in game.
    """
    try:
        _auto_snapshot(target_plugin, "pre_shader")
        from tools.records.items import create_shader as run
        res = run(_refresher(plugins, target_plugin), target_plugin, editor_id, color, source)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def create_unique_item(source: str, target_plugin: str, editor_id: str, name: str,
                       description: str | None = None, enchantment: str | None = None,
                       charge: int | None = None, replace_enchantment: bool = False, cell: str | None = None,
                       x: float = 0.0, y: float = 0.0, z: float = 0.0, rotation_x: float = 0.0,
                       rotation_y: float = 0.0, rotation_z: float = 0.0, persistent: bool = False,
                       ref_editor_id: str | None = None, plugins: list[str] | None = None) -> dict:
    """Make a custom item in one call: clone a vanilla item, rename it, enchant it and place it in a cell.

    source: the vanilla WEAP/ARMO/MISC/BOOK to clone. enchantment (an ENCH editor ID, from create_enchantment
    or create_script_effect) is optional; weapons get `charge` (default 500) and spawn with full charge.
    Give cell and x/y/z to place it in the world (rotation_x 90 lays a dagger flat); omit cell to only create it.
    """
    try:
        _auto_snapshot(target_plugin, "pre_unique_item")
        from tools.records.items import create_unique_item as run
        res = run(_refresher(plugins, target_plugin), source, target_plugin, editor_id, name, description,
                  enchantment, charge, replace_enchantment, cell, x, y, z, rotation_x, rotation_y, rotation_z,
                  persistent, ref_editor_id)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def deploy_plugin(plugin: str, mode: str = "package", remove_loose: bool = False) -> dict:
    """Validate a plugin, then ship it: a Vortex zip (mode "package"), loose files in Data (mode "install")
    or both (mode "both": zip, then install loose copies into Data for testing).

    Validation errors stop the deploy. When packaging, loose copies already in the Data folder are listed
    (they override Vortex's files); remove_loose deletes them so the Vortex install is what runs.
    """
    from tools.records.maintenance import deploy_plugin as run
    try:
        return run(_out_path(plugin), DATA_DIR, mode, remove_loose)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def item_console_commands(plugin: str, query: str | None = None, sections: list[str] | None = None) -> dict:
    """In-game console commands to try a plugin's own items, spells, cells and NPCs (additem, addspell, coc...).

    query: only records whose editor ID contains it (e.g. "Aurelius"). sections: any of items, spells, cells,
    npcs, effects, placed (default: all but placed, the teleports to every named placed reference).
    Form IDs use XX for the load-order index; run `help <EditorID> 0` in the console to see the real prefix.
    For one enchanted item, enchantment_test_plan gives a fuller checklist.
    """
    from tools.records.maintenance import item_console_commands as run
    try:
        return run(_out_path(plugin), query, sections)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def enchantment_test_plan(plugin: str, item: str) -> dict:
    """In-game test checklist for an enchanted item (or an ENCH editor ID) in an output plugin.

    Lists the console commands to add and equip it, each effect with its magnitude, description and script,
    ability spells the script looks up, whether it needs SKSE (start via skse64_loader.exe) and where the
    Papyrus log is. Built from the plugin's records and the scripts' .psc sources.
    """
    from tools.records.maintenance import enchantment_test_plan as run
    try:
        return run(_out_path(plugin), item)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def check_script(source: str) -> dict:
    """Compile a Papyrus script as a dry run: returns ok and the compiler output; writes nothing to any plugin.

    source starts with `Scriptname <name> ...`. Uses the same include paths as create_script (SKSE sources
    first when installed, then vanilla), so it shows whether an idea compiles before any record is touched.
    """
    from tools.records.maintenance import check_script as run
    try:
        return run(source)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def resolve_form(identifier: str, signature: str | None = None, plugins: list[str] | None = None) -> dict:
    """Look up the exact form for an editor ID (e.g. JobJarlFaction) and return its IDs, ready for a script.

    Returns form_id (8 hex, load-order prefixed), local_id (6 hex, the value for Game.GetFormFromFile(0x...,
    "Plugin.esp")), type, name and plugin for exact editor ID matches, or the closest few names when there is
    none. signature narrows the type (FACT, KYWD, SPEL, ...). Use it instead of guessing Skyrim.esm IDs.
    """
    from tools.core.quest_model import LOAD_SIGS
    resolver = npc_builder(plugins).resolver
    sigs = [signature.upper()] if signature else sorted(LOAD_SIGS)
    found = resolver.search(sigs, identifier, 200)
    exact = [d for d in found if (d["editor_id"] or "").lower() == identifier.lower()]
    for d in exact:
        d["local_id"] = d["form_id"][2:]
    if exact:
        return {"matches": exact}
    return {"matches": [], "similar": [d["editor_id"] for d in found[:8]],
            "error": f"no record with editor ID {identifier!r}"}


@mcp.tool()
def create_enchantment(target_plugin: str, editor_id: str, name: str, effects: list[dict],
                       kind: str = "weapon", cost: int = 0, auto_calc: bool = True,
                       plugins: list[str] | None = None) -> dict:
    """Create a custom enchantment (ENCH) from existing magic effects (MGEF).

    effects: [{"effect": MGEF editor ID/name/form ID, "magnitude": 0, "area": 0, "duration": 0}].
    kind: "weapon" (fire-and-forget on hit, touch), "armor" (constant effect, self; duration must be 0) or
    "staff". Find effects with search_forms("MGEF", ...). Weapon effects must be touch/fire-and-forget MGEFs
    and armor effects constant/self ones. Then apply it with edit_record: EITM (the ENCH) on the WEAP/ARMO,
    plus EAMT (u16 charge) on weapons. A fully new effect needs a cloned MGEF (clone_record + edit_record),
    optionally with a Papyrus script. See the skyrim-enchantments skill.
    """
    try:
        from tools.records.enchant import create_enchantment as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), target_plugin, editor_id, name, effects,
                  kind, cost, auto_calc=auto_calc)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def clone_record(source: str, target_plugin: str, editor_id: str, name: str | None = None,
                 description: str | None = None, signature: str | None = None,
                 plugins: list[str] | None = None) -> dict:
    """Clone a vanilla record (BOOK, WEAP, MISC, ARMO...) into target_plugin under a new editor ID.

    Text is written inline (the plugin must not be localized), masters are added, scripts are removed.
    name sets FULL, description sets DESC (a book's text). signature narrows the lookup. Follow with
    edit_record to change stats or add e.g. EITM/EAMT. A snapshot is taken first.
    """
    from tools.records.clone import clone_record as run
    try:
        _auto_snapshot(target_plugin, "pre_clone")
        res = run(npc_builder(_with_target(plugins, target_plugin)), source, target_plugin, editor_id, name,
                  description, signature)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def clone_records(target_plugin: str, items: list[dict], plugins: list[str] | None = None) -> dict:
    """Clone several vanilla records into target_plugin in one call (safe to use instead of parallel clone_record calls).

    items: list of {source, editor_id, name?, description?, signature?}, same meaning as clone_record.
    One snapshot is taken first. A failing item is reported in `errors` and the others still run.
    """
    from tools.records.clone import clone_record as run
    created, errors = [], []
    try:
        _auto_snapshot(target_plugin, "pre_clone")
        for item in items:
            try:
                res = run(npc_builder(_with_target(plugins, target_plugin)), item["source"], target_plugin,
                          item["editor_id"], item.get("name"), item.get("description"), item.get("signature"))
                created.append(res["created"])
            except (ValueError, FileNotFoundError, KeyError) as e:
                errors.append({"editor_id": item.get("editor_id"), "error": str(e)})
            finally:
                _invalidate()
        return {"path": str(_out_path(target_plugin)), "created": created, "errors": errors}
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


def _spot_near(plugins, target_plugin, cell, near, clearance=40.0):
    """A free floor point beside the first reference in `cell` whose base editor ID or name matches `near`."""
    from tools.cells.cell_contents import find_free_spots as free, get_cell_contents as contents
    b = _cell_builder(plugins, target_plugin)
    seen = contents(b, cell, near, None, 5)
    refs = seen.get("references") or []
    if not refs:
        raise ValueError(f"nothing matching {near!r} in cell {cell!r}; use get_cell_contents to see what is there")
    ref = refs[0]
    spots = free(b, cell, ref["x"], ref["y"], ref["z"], 1, 30.0, clearance, 400.0).get("spots") or []
    if not spots:
        raise ValueError(f"no free floor spot within 400 units of {near!r}")
    return spots[0]["x"], spots[0]["y"], spots[0]["z"]


@mcp.tool()
def place_item(base: str, target_plugin: str, cell: str, x: float | None = None, y: float | None = None,
               z: float | None = None,
               rotation_z: float = 0.0, scale: float | None = None, persistent: bool = False,
               editor_id: str | None = None, rotation_x: float = 0.0, rotation_y: float = 0.0,
               charge: float | None = None, plugins: list[str] | None = None, near: str | None = None) -> dict:
    """Place one reference to any base object (book, armor, misc item, static...) in an existing cell.

    base: editor ID, name or form ID, including records already in target_plugin. cell: editor ID or name
    (see search_cells). x, y, z in game units, rotation_x/y/z in degrees (rotation_x 90 lays a dagger flat).
    Instead of coordinates, pass near: the editor ID or name of something already in the cell (an NPC, a
    table...) and the item goes on the nearest free floor spot beside it. Coordinates win when given.
    Use get_cell_contents for the cell's furniture and shelf positions, or search_references to copy
    coordinates from a nearby object. An enchanted weapon is placed with full charge (its EAMT); pass charge
    to override. Set persistent for items a script or quest must reach. The file is backed up to .bak.
    """
    try:
        if x is None or y is None or z is None:
            if not near:
                raise ValueError("give x, y and z, or near (something in the cell to place the item beside)")
            x, y, z = _spot_near(plugins, target_plugin, cell, near)
        from tools.cells.place_item import place_item as run
        res = run(npc_builder(_with_target(plugins, target_plugin)), base, target_plugin, cell, x, y, z,
                  rotation_z, scale, persistent, editor_id, rotation_x, rotation_y, charge)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}


@mcp.tool()
def get_cell_contents(cell: str, query: str | None = None, signature: str | None = None, limit: int = 100,
                      x: float | None = None, y: float | None = None, radius: float | None = None,
                      plugins: list[str] | None = None, target_plugin: str | None = None) -> dict:
    """List what is placed in a cell: each reference's base (type, editor ID, name) and position.

    target_plugin: the output plugin being authored; its cells, references and bases are included.

    cell: editor ID or name. query filters by base editor ID or name; signature by base type (STAT, FURN,
    CONT, BOOK...). x, y and radius keep only references within that horizontal distance, nearest first.
    Use it to find shelves, tables and floor spots before place_item (or use find_free_spots).
    Pass `plugins` with Skyrim.esm and the plugin being authored (e.g. ["Skyrim.esm", "My.esp"]) so its
    references and base records are included.
    """
    try:
        from tools.cells.cell_contents import get_cell_contents as run
        return run(_cell_builder(plugins, target_plugin), cell, query, signature, limit, x, y, radius)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def find_free_spots(cell: str, x: float, y: float, z: float, count: int = 1, spacing: float = 30.0,
                    clearance: float = 25.0, radius: float = 300.0, height: float = 120.0,
                    plugins: list[str] | None = None, target_plugin: str | None = None) -> dict:
    """Find free floor points near (x, y, z) in a cell, nearest first, on a grid of `spacing` units.

    A point is free when no reference within `height` of z lies closer than `clearance` (lights are ignored).
    Pass target_plugin (the plugin being authored) so its own cell and references count. Feed the points to
    place_item, or use place_items to do both; place_item(near=...) picks a spot for you.
    """
    try:
        from tools.cells.cell_contents import find_free_spots as run
        return run(_cell_builder(plugins, target_plugin), cell, x, y, z, count, spacing, clearance, radius, height)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def place_items(bases: list[str], target_plugin: str, cell: str, x: float, y: float, z: float,
                spacing: float = 30.0, clearance: float = 25.0, radius: float = 300.0,
                rotation_z: float = 0.0, plugins: list[str] | None = None) -> dict:
    """Place several items (books, scrolls, weapons, misc...) on free floor spots nearest (x, y, z) in a cell.

    bases: editor IDs of items in target_plugin or vanilla ones. Each reference is named <base>Ref. Existing
    objects (including ones in target_plugin) are avoided; the call fails if the radius holds too few spots.
    """
    try:
        _auto_snapshot(target_plugin, "pre_place_items")
        from tools.cells.place_items import place_items as run
        return run(_refresher(plugins, target_plugin), bases, target_plugin, cell, x, y, z, spacing, clearance,
                   radius, rotation_z)
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}
    finally:
        _invalidate()


@mcp.tool()
def create_spell_set(target_plugin: str, prefix: str, name: str, script_name: str, source: str,
                     description: str = "", variants: list[str] | None = None, duration: int = 10,
                     magnitude: float = 0, costs: dict | None = None, tome_texts: dict | None = None,
                     tomes: bool = True, scroll: bool = True, staff: bool = True,
                     staff_source: str = "StaffParalyze", staff_charge: int = 3000, cell: str | None = None,
                     x: float = 0.0, y: float = 0.0, z: float = 0.0, spacing: float = 30.0,
                     clearance: float = 25.0, radius: float = 300.0, plugins: list[str] | None = None) -> dict:
    """Build a spell family from one Papyrus effect script in a single call.

    Creates a script effect per delivery, a spell per variant (touch is a short-range ray; bolt, ray, burst, nova; default all),
    a spell tome per spell, a scroll and an enchanted staff, and, if cell is given, places the tomes, scroll
    and staff on free floor spots nearest x, y, z. source is the full ActiveMagicEffect script. costs maps a
    variant to its mana cost ({"ray": 40}); tome_texts maps a variant to the book body. Editor IDs derive from
    prefix: <prefix>TouchSpell, <prefix>TouchTome, <prefix>Scroll, <prefix>Staff... A snapshot is taken first.
    Concentration (ray) and self-area (nova) behaviour should be checked in game. See the skyrim-enchantments skill.
    """
    try:
        _auto_snapshot(target_plugin, "pre_spell_set")
        from tools.records.spell_set import create_spell_set as run
        return run(_refresher(plugins, target_plugin), target_plugin, prefix, name, script_name, source,
                   description, variants, duration, magnitude, costs, tome_texts, tomes, scroll, staff,
                   staff_source, staff_charge, cell, x, y, z, spacing, clearance, radius)
    except (ValueError, FileNotFoundError, KeyError) as e:
        return {"error": str(e)}
    finally:
        _invalidate()


@mcp.tool()
def create_cell(target_plugin: str, editor_id: str, name: str | None = None, source: str | None = None,
                can_travel: bool = True, copy_references: bool = True, plugins: list[str] | None = None) -> dict:
    """Create a new interior cell in target_plugin, from scratch or by cloning an existing interior cell.

    source: editor ID, name or 8-digit form ID of an interior cell to clone (its lighting, music, location
    and other settings are copied). With copy_references (default) its placed objects are copied too, minus
    actors, navmesh, door teleports and scripts. Without source a lit, empty cell is made and name is
    required. Text is written inline (the plugin must not be localized). The new cell can be used as
    placement.cell by NPC/quest tools. A snapshot is taken first.
    """
    from tools.cells.cell_create import create_cell as run
    try:
        _auto_snapshot(target_plugin, "pre_cell")
        res = run(npc_builder(plugins), target_plugin, editor_id, name, source, can_travel, copy_references)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def make_reference_persistent(reference: str, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Make a placed Skyrim.esm reference (hex ID from search_references) persistent in target_plugin.

    A quest alias can only fill, and show a compass marker for, a persistent reference; interior containers,
    doors and items are normally not. Writes a persistent override of the reference into its cell. A snapshot
    is taken first. add_plant_quest does this for its container automatically.
    """
    from tools.cells.persist import make_reference_persistent as run
    try:
        _auto_snapshot(target_plugin, "pre_persist")
        res = run(npc_builder(plugins), reference, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def snapshot_plugin(plugin: str, label: str | None = None) -> dict:
    """Save a timestamped copy of an output plugin so a quest build can be undone (records can't be deleted)."""
    from tools.records.maintenance import snapshot
    try:
        return {"snapshot": snapshot(_out_path(plugin), label).name}
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def list_snapshots(plugin: str) -> list[dict]:
    """List the saved snapshots of an output plugin, oldest first."""
    from tools.records.maintenance import list_snapshots as run
    try:
        return run(_out_path(plugin))
    except (ValueError, FileNotFoundError) as e:
        return [{"error": str(e)}]


@mcp.tool()
def restore_snapshot(plugin: str, snapshot: str = "latest") -> dict:
    """Restore an output plugin from a snapshot (a file name, a label such as pre_plant_quest, or 'latest').

    The current plugin is snapshotted first as pre_restore, so a restore can be undone. Rebuild the quest
    afterwards, then package or install.
    """
    from tools.records.maintenance import restore
    try:
        res = restore(_out_path(plugin), snapshot)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def install_plugin(plugin: str | list[str], dry_run: bool = False) -> dict:
    """Copy an output plugin, its scripts, FaceGen files and a regenerated SEQ file into the Skyrim Data folder.

    Reports each file as new/changed/same. dry_run only reports. Use this instead of Vortex for fast
    testing; it avoids a stale Data copy or SEQ file (the usual reason a new quest does not load).
    Pass a list of plugins to install several at once; the result is keyed by plugin, and one failing
    plugin does not stop the others.
    """
    from tools.records.maintenance import install

    def one(name):
        try:
            return install(_out_path(name), DATA_DIR, dry_run)
        except (ValueError, FileNotFoundError) as e:
            return {"error": str(e)}

    return _for_each(plugin, one)


@mcp.tool()
def validate_plugin(plugin: str) -> dict:
    """Check an output plugin before testing: masters present, duplicate editor IDs, form IDs in range,
    own-record references, compiled .pex for every attached script, objective alias targets, a current
    SEQ file for start-game quests, and whether the Data folder copy matches the output copy.
    """
    from tools.records.maintenance import validate
    try:
        return validate(_out_path(plugin), DATA_DIR)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def quest_console_commands(plugin: str, quest: str) -> dict:
    """In-game console commands for checking and driving a quest from a plugin (sqv, getstage, setstage...)."""
    from tools.records.maintenance import quest_console_commands as run
    try:
        return run(_out_path(plugin), quest)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


def _guarded(fn, snapshot_label=None, plugin=None):
    """Run an edit on an output plugin (snapshotting first) and turn expected errors into {'error': ...}."""
    try:
        if snapshot_label:
            _auto_snapshot(plugin, snapshot_label)
        return fn()
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}
    except Exception as e:  # compile failures and similar
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def list_texts(plugin: str, query: str | None = None, limit: int = 50) -> list[dict] | dict:
    """List the inline text (book text, dialogue lines, prompts, journal entries, names) of an output
    plugin's own records, optionally filtered by a substring. Use the record and sig/index with edit_text."""
    from tools.records.edit_tools import list_texts as run
    return _guarded(lambda: run(_out_path(plugin), query, limit))


@mcp.tool()
def edit_text(plugin: str, record: str, sig: str, text: str | None = None, find: str | None = None,
              replace: str | None = None, index: int = 0, verbose: bool = False) -> dict:
    """Change one text subrecord of an output plugin's own record in place (no rebuild needed).

    record: editor ID or hex object ID (dialogue responses have no editor ID; see list_texts). sig: FULL
    (name/prompt), DESC (book text), NAM1 (dialogue line), CNAM (journal entry), NNAM (objective)... index
    picks among several of that sig (e.g. the 2nd line of a response). Give text to replace all of it, or
    find+replace to patch a substring. Returns character/word counts; verbose=True also returns the full
    before/after text. A snapshot is taken first; reinstall afterwards. For several edits use edit_texts.
    """
    from tools.records.edit_tools import edit_text as run
    return _guarded(lambda: run(_out_path(plugin), record, sig, text, find, replace, index, verbose),
                    "pre_edit_text", plugin)


@mcp.tool()
def edit_texts(plugin: str, items: list[dict], verbose: bool = False) -> list[dict] | dict:
    """Apply many text edits to one plugin with a single snapshot, load and save.

    items: list of {record, sig, text | find + replace, index?} (same fields as edit_text). Everything is
    validated first: if any item fails, nothing is written and the error names the item. Prefer this over
    several edit_text calls when rewriting a set of books or lines.
    """
    from tools.records.edit_tools import edit_texts as run
    return _guarded(lambda: run(_out_path(plugin), items, verbose), "pre_edit_texts", plugin)


@mcp.tool()
def list_book_models(plugins: list[str] | None = None) -> list[dict] | dict:
    """Vanilla book models you can give a custom book, by kind (note, journal, book): model path, the
    inventory-art STAT (INAM), bounds, typical value/weight and how long vanilla texts using it are
    (median and p10-p90 word counts) with example books. Use with edit_book and the skyrim-book-writing skill."""
    from tools.books.books import model_table
    return _guarded(lambda: model_table(get_model(plugins)))


@mcp.tool()
def read_book(identifier: str, plugin: str | None = None, plugins: list[str] | None = None) -> dict:
    """Read a book's text (vanilla localized text included) with its model, kind and word count, to study
    how vanilla books are written and formatted. identifier: editor ID, name or form ID. With `plugin`,
    reads the record from that plugin file in the output or Data folder."""
    from tools.books.books import book_text, kind_of_model, word_count
    from tools.npcs.npc_builder import Resolver

    def run():
        m = get_model(_with_target(plugins, plugin) if plugin else plugins)
        if plugin:
            from tools.core.plugin_edit import RecordEditor
            ed = RecordEditor(m, Resolver(m), OUTPUT_DIR, DATA_DIR)
            from tools.core.plugin_edit import Target
            t = Target(m, ed.names, ed.path(plugin, must_exist=True))
            rec = t.find_own(identifier, ['BOOK'])
            if rec is None:
                raise ValueError(f"{identifier!r} not found in {plugin}")
        else:
            _, rec = Resolver(m).lookup(identifier, ['BOOK'])
        modl = rec.get_subrecord('MODL')
        path = modl.get_string() if modl is not None else None
        text = book_text(rec.plugin, rec) or ''
        return {'editor_id': rec.editor_id, 'name': rec.full_name, 'model': path, 'kind': kind_of_model(path),
                'words': word_count(text), 'text': text}
    return _guarded(run)


@mcp.tool()
def edit_book(plugin: str, record: str, kind: str | None = None, variant: str | None = None,
              text: str | None = None, title: str | None = None, author: str | None = None,
              value: int | None = None, weight: float | None = None, reformat: bool = False,
              plugins: list[str] | None = None) -> dict:
    """Make a BOOK in an output plugin look and read like a vanilla book of a given kind in one call.

    kind: note (letters, scraps), journal (diaries, ledgers) or book (printed). variant: a specific model, e.g.
    Note03, Journal02, BasicBook05a (see list_book_models); defaults to a good one per kind. Sets the model,
    texture hashes, bounds, inventory art and value/weight copied from a vanilla book of that model.
    text: optional new body (plain paragraphs separated by blank lines); it is wrapped in vanilla markup:
    notes/journals in the handwriting font, printed books with [pagebreak], a centred title page (title, author)
    and an illuminated first letter. Pass a complete marked-up text through edit_text instead to skip this.
    reformat=True re-wraps the book's existing text instead (printed books: first line = title, optional
    'by Author' line, then the body), e.g. to convert plain `<p>` books to vanilla layout.
    """
    from tools.books.books import KINDS, VARIANTS, format_book, kind_of_model, book_edits, variant_model, split_book

    def run():
        k, v = kind, variant
        if v is None and k is None:
            raise ValueError("give kind or variant")
        if v is None:
            if k not in KINDS:
                raise ValueError(f"kind must be one of {', '.join(KINDS)}")
            v = KINDS[k]['default']
        k = kind_of_model(variant_model(v))
        edits = book_edits(get_model(plugins), v, value, weight)
        res = _editor(_with_target(plugins, plugin)).edit_record(record, plugin, edits, None, None, None, 'BOOK')
        out = {'record': res['editor_id'], 'kind': k, 'variant': v, 'model': variant_model(v), 'changes': res['changes']}
        new_text = None
        if text is not None:
            new_text = format_book(text, k, title, author)
        elif reformat:
            from tools.records.edit_tools import list_texts
            cur = next(t['text'] for t in list_texts(_out_path(plugin), record, 100000)
                       if t['record'] == res['editor_id'] and t['sig'] == 'DESC')
            if k == 'book':
                t0, a0, body = split_book(cur)
                new_text = format_book(body, k, title or t0, author or a0)
            else:
                new_text = format_book(re.sub(r'<[^>]+>', '', cur.replace('\r\n', '\n')).strip(), k)
        if new_text is not None:
            from tools.records.edit_tools import edit_text as run_text
            out['text'] = run_text(_out_path(plugin), record, 'DESC', new_text)
        _invalidate()
        return out
    return _guarded(run, "pre_edit_book", plugin)


@mcp.tool()
def lore_search(query: str, limit: int = 8, source: str | None = None, mode: str = "auto") -> list[dict] | dict:
    """Search the local Elder Scrolls lore database for facts about people, places, factions, history and
    in-game books. Returns snippets with page title, section, url and license; cite them and write original
    text rather than copying. source: 'uesp' or 'game'. mode: 'auto' (hybrid keyword+semantic when vectors
    were built, else keyword), 'keyword' (best for exact names), 'semantic' (best for concepts and
    paraphrase) or 'hybrid'. If no database exists the error says how the user can build one
    (`python -m tools.lore build ...`)."""
    from tools.lore import db, vectors
    return _guarded(lambda: vectors.search(db.connect(), query, max(1, min(limit, 50)), source, mode))


@mcp.tool()
def lore_get(title: str, section: str | None = None, source: str | None = None) -> list[dict] | dict:
    """Full text of one lore page (or one section) from the local lore database, following redirects.
    title: a page title as returned by lore_search, e.g. 'Lore:Dwemer'."""
    from tools.lore import db

    def run():
        rows = db.get_page(db.connect(), title, section, source)
        return rows if rows else {"error": f"no lore page {title!r}; try lore_search"}
    return _guarded(run)


@mcp.tool()
def lore_status() -> dict:
    """Show which lore sources are indexed (license, attribution, build date, size) in the local database."""
    from tools.lore import db
    return _guarded(lambda: db.status(db.connect()))


@mcp.tool()
def build_lore_db(source: str, file: str | None = None, namespaces: list[str] | None = None,
                  plugins: list[str] | None = None) -> dict:
    """Build or refresh one source of the local lore database (replaces that source's rows).
    source 'game-books': index book text from the loaded plugins (defaults to Skyrim.esm).
    source 'uesp-dump': import a UESP MediaWiki XML export (.xml/.gz/.bz2) the user downloaded, given as
    file; namespaces defaults to Lore and Skyrim. The UESP API crawl is only available from the command
    line (`python -m tools.lore build uesp-crawl`) because it is slow.
    source 'vectors': embed chunks for semantic search (needs `pip install fastembed`; downloads a small
    model on first use; can take minutes for the full wiki, so prefer the command line for that)."""
    from tools.lore import db, uesp, vectors

    def run():
        con = db.connect(create=True)
        if source == 'game-books':
            from tools.lore.game import index_books
            n = index_books(con, get_model(plugins))
        elif source == 'uesp-dump':
            if not file:
                raise ValueError("uesp-dump needs file")
            n = uesp.import_dump(con, file, namespaces or uesp.DEFAULT_NAMESPACES)
        elif source == 'vectors':
            n = vectors.build(con)
        else:
            raise ValueError("source must be 'game-books', 'uesp-dump' or 'vectors'")
        return {"source": source, "chunks": n, **db.status(con)}
    return _guarded(run)


@mcp.tool()
def api_search(query: str, limit: int = 8, source: str | None = None, mode: str = "auto") -> list[dict] | dict:
    """Search the local Papyrus API database (separate from lore) for script functions, events, properties,
    actor values and Creation Kit wiki notes. Use it BEFORE writing Papyrus to confirm a hook or function
    really exists. Prefer api_get for an exact script or member name; use this for intent ('detect a sale',
    'restore health over time'). source: 'psc' (signatures), 'actor-values' or 'ckwiki'. mode as in
    lore_search. Members marked [SKSE] need the Script Extender (see detect_skse). If no database exists
    the error says how to build one (`python -m tools.papyrus_api build ...`)."""
    from tools.papyrus_api import api
    return _guarded(lambda: api.search(api.connect(), query, max(1, min(limit, 50)), source, mode))


@mcp.tool()
def api_get(script: str, member: str | None = None) -> dict:
    """Exact Papyrus API lookup from the local API database. api_get('Actor') returns the script header,
    its extends chain and its own functions/events/properties with signatures. api_get('Actor', 'OnHit')
    finds that member on the script or the nearest parent that declares it (e.g. 'DamageActorValue' on Actor,
    'RegisterForSingleUpdate' on Form) with its exact signature. An empty result means the member does
    not exist, except non-native events such as OnInit, which are not indexed (check those with
    check_script). skse=true means it needs SKSE."""
    from tools.papyrus_api import api

    def run():
        con = api.connect()
        out = api.get_script(con, script, member)
        if out is None:
            return {"error": f"no script {script!r}", "similar": api.suggest(con, script)}
        if member and not out['members']:
            out["similar"] = api.suggest(con, member)
        return out
    return _guarded(run)


@mcp.tool()
def api_status() -> dict:
    """Show which Papyrus API sources are indexed (scripts, actor values, CK wiki) with license and size."""
    from tools.papyrus_api import api
    return _guarded(lambda: api.status(api.connect()))


@mcp.tool()
def build_api_db(source: str, file: str | None = None, plugins: list[str] | None = None) -> dict:
    """Build or refresh one source of the local Papyrus API database (replaces that source's rows).
    source 'scripts': index the .psc sources (CK sources extracted for the compiler, plus SKSE sources when
    found); rerun after installing SKSE or changing its version. source 'actor-values': index ActorValueInfo
    records of the loaded plugins (defaults to Skyrim.esm). source 'ckwiki-dump': import a Creation Kit wiki
    XML export given as file. source 'vectors': embed chunks for semantic search (needs fastembed)."""
    from tools.papyrus_api import api
    from tools.papyrus_api.cli import default_script_dirs
    from tools.knowledge import vectors

    def run():
        con = api.connect(create=True)
        if source == 'scripts':
            n = api.index_scripts(con, default_script_dirs())
        elif source == 'actor-values':
            n = api.index_actor_values(con, get_model(plugins))
        elif source == 'ckwiki-dump':
            if not file:
                raise ValueError("ckwiki-dump needs file")
            n = api.import_ckwiki_dump(con, file)
        elif source == 'vectors':
            n = vectors.build(con)
        else:
            raise ValueError("source must be 'scripts', 'actor-values', 'ckwiki-dump' or 'vectors'")
        return {"source": source, "chunks": n, **api.status(con)}
    return _guarded(run)


@mcp.tool()
def lint_text(plugin: str | None = None, text: str | None = None) -> list[dict] | dict:
    """Check text for lore problems (real-world weekdays/months, 'oh my god', 'okay', miles, dollars...)
    and suggest Tamrielic replacements. Pass plugin to lint all its text, or text for a single string."""
    from tools.books.lore import lint_plugin, lint_text as run
    if (plugin is None) == (text is None):
        return {"error": "give plugin or text"}
    return _guarded(lambda: lint_plugin(_out_path(plugin)) if plugin else run(text))


@mcp.tool()
def list_conditions(plugin: str, record: str) -> list[dict] | dict:
    """Show a record's CTDA conditions (operator, value, function, params), e.g. a dialogue response's
    quest-stage checks. record: editor ID or hex object ID."""
    from tools.records.edit_tools import list_conditions as run
    return _guarded(lambda: run(_out_path(plugin), record))


@mcp.tool()
def edit_condition(plugin: str, record: str, index: int, operator: str | None = None,
                   value: float | None = None) -> dict:
    """Change one condition's operator (==, !=, >, >=, <, <=) and/or value in place, e.g. make a topic show
    from stage 20 instead of 10. Use list_conditions for the index. A snapshot is taken first."""
    from tools.records.edit_tools import edit_condition as run
    return _guarded(lambda: run(_out_path(plugin), record, index, operator, value), "pre_edit_condition", plugin)


@mcp.tool()
def edit_script(plugin: str, script: str, source: str | None = None, find: str | None = None,
                replace: str | None = None) -> dict:
    """Read, replace or patch the Papyrus source of any script in an output plugin, then recompile its .pex.

    Works for every script with a .psc in the plugin's Scripts/Source, including enchantment effect scripts
    (e.g. GemCuttersGuild_NotePrice), not only generated fragments. With only script: returns the current
    source. With source: replaces it. With find+replace: patches it in place, so a one-line change needs no
    resend of the whole script. The old source is restored if compilation fails. The MGEF keeps its script
    attachment, so no record edit is needed. Reinstall afterwards (install_plugin).
    """
    from tools.records.edit_tools import edit_script as run
    return _guarded(lambda: run(_out_path(plugin), script, source, find, replace))


@mcp.tool()
def diff_plugin(plugin: str, against: str = "latest") -> dict:
    """Compare an output plugin with a snapshot (file name, label or 'latest'): records added, removed and
    changed, with before/after for changed subrecords (text shown readably)."""
    from tools.records.edit_tools import diff_plugins
    from tools.records.maintenance import list_snapshots as snaps, snapshot_dir

    def run():
        path = _out_path(plugin)
        found = snaps(path)
        if not found:
            raise ValueError(f"no snapshots for {path.name}")
        pick = found[-1] if against == "latest" else next(
            (s for s in reversed(found) if s["name"] == against or s["name"].endswith(f".{against}{path.suffix}")), None)
        if pick is None:
            raise ValueError(f"no snapshot matches {against!r}")
        return {"against": pick["name"], **diff_plugins(path, snapshot_dir(path) / pick["name"])}
    return _guarded(run)


@mcp.tool()
def audit_plugin(plugin: str) -> dict:
    """Design audit for fragile quests: objectives with no alias target (no marker), script properties
    pointing at non-persistent references (can be None at runtime), and unguarded script comparisons."""
    from tools.records.maintenance import audit_plugin as run
    return _guarded(lambda: run(_out_path(plugin), DATA_DIR))


@mcp.tool()
def get_npc_face(identifier: str, target_plugin: str | None = None, plugins: list[str] | None = None) -> dict:
    """Read an NPC's face: 18 named sliders (-1..1), nose/eye/lip presets, weight, race and head parts.

    Each slider's meaning is returned (e.g. jaw_height: jaw lower (longer face) / higher). With
    target_plugin, reads that plugin's copy of the NPC if it has one.
    """
    try:
        return npc_builder(plugins).get_face(identifier, target_plugin)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def edit_npc_face(identifier: str, changes: dict, target_plugin: str, plugins: list[str] | None = None) -> dict:
    """Change an NPC's face, e.g. {"nose_length": -0.6, "jaw_height": 0.5} for a small nose and longer face.

    changes: absolute values by name (see get_npc_face for the slider names and meanings; sliders
    -1..1, nose_type/eye_type/lip_type preset numbers, weight 0-100). Updates the NPC record in
    target_plugin and regenerates the baked FaceGen mesh next to it (if present; .bak saved). Hair
    and hairline are untouched. Human and elf races only. Run package_plugin afterwards.
    """
    try:
        res = npc_builder(plugins).edit_face(identifier, changes, target_plugin)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def paint_npc_face(identifier: str, target_plugin: str, mask: str, color: str, opacity: float = 1.0,
                   plugins: list[str] | None = None) -> dict:
    """Add face paint/warpaint to an NPC by blending a colour through a vanilla tint mask into its FaceTint texture.

    mask: a name from Textures\\Actors\\Character\\Character Assets\\TintMasks, e.g.
    MaleHeadNordWarpaint_01 (also Breton/DarkElf/WoodElf variants, Female...). color: #rrggbb or a
    name (blood red, black, white, blue...). opacity 0-1. Always repaints from the original texture
    (kept as .orig.bak), so repeat calls replace the previous paint. Run package_plugin afterwards.
    """
    try:
        return npc_builder(plugins).paint_face(identifier, target_plugin, mask, color, opacity)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


def _editor(plugins):
    from tools.npcs.npc_builder import Resolver
    from tools.core.plugin_edit import RecordEditor
    m = get_model(plugins)
    return RecordEditor(m, Resolver(m), OUTPUT_DIR, DATA_DIR)


def _invalidate():
    with _lock:
        _models.clear()


def _read_path(plugin):
    """Path of a plugin to read (output folder, then Data folder), official plugins included."""
    path = Path(_plugin_path(plugin))
    if not path.is_file():
        raise FileNotFoundError(f"{plugin} not found in the output or Data folder")
    return path


@mcp.tool()
def diff_records(plugin_a: str, record_a: str, plugin_b: str, record_b: str) -> dict:
    """Compare two records field by field, e.g. a custom magic effect against the vanilla one it imitates.

    Plugins are file names (output folder, then Data folder). Records are editor IDs or hex object IDs.
    Reports subrecords on only one side, flag differences and, for same-size data blobs, the differing
    4-byte words (MGEF DATA words are named, e.g. 'flags', 'base cost', 'archetype'); conditions are decoded
    with function names. Use it first when a custom item behaves unlike a vanilla one.
    """
    from tools.records.record_tools import diff_records as run
    return _guarded(lambda: run(_read_path(plugin_a), record_a, _read_path(plugin_b), record_b))


@mcp.tool()
def copy_subrecords(target_plugin: str, record: str, source_record: str, sigs: list[str] | None = None,
                    skip: list[str] | None = None, keep: list[str] | None = None,
                    source_plugin: str = "Skyrim.esm") -> dict:
    """Replace subrecords of one of your records with those of a vanilla record of the same type.

    Use it to make a custom record behave like a vanilla one (e.g. give a script MGEF the data, keywords,
    sounds and conditions of EnchParalysisFFContact). sigs: only these signatures (default: all except EDID
    and keep). keep: signatures of your record to leave alone (default EDID, VMAD, FULL). skip: never copy
    these. source_plugin must be the target's first master (Skyrim.esm). A snapshot is taken first.
    """
    from tools.records.record_tools import copy_subrecords as run
    return _guarded(lambda: run(_out_path(target_plugin), record, _read_path(source_plugin), source_record,
                                sigs, skip or (), keep or ("EDID", "VMAD", "FULL")),
                    "pre_copy_subrecords", target_plugin)


@mcp.tool()
def drop_conditions(plugin: str, record: str, indexes: list[int]) -> dict:
    """Remove conditions (CTDA) from your record by index (see list_conditions), e.g. the 25% random-chance
    condition vanilla paralysis carries. Returns the conditions removed. A snapshot is taken first."""
    from tools.records.record_tools import drop_conditions as run
    return _guarded(lambda: run(_out_path(plugin), record, indexes), "pre_drop_conditions", plugin)


@mcp.tool()
def remove_records(plugin: str, records: list[str]) -> dict:
    """Delete your own records (editor IDs or hex IDs) and every placed reference to them, e.g. diagnostic
    test items. Reports other records that still point at them. A snapshot is taken first."""
    from tools.records.record_tools import remove_records as run
    return _guarded(lambda: run(_out_path(plugin), records), "pre_remove_records", plugin)


@mcp.tool()
def list_records(plugin: str, query: str | None = None, signature: str | None = None, min_id: int | None = None,
                 max_id: int | None = None, limit: int = 100) -> dict:
    """List the records a plugin in the output folder defines itself: form ID, type, editor ID and name.

    query matches editor ID or name (substring); signature limits the type (SPEL, BOOK...); min_id/max_id
    bound the object ID (decimal, e.g. 2857 for 0xB29). Also returns a count per type. Use it to see what a
    plugin holds before splitting it (see skyrim-plugin-refactoring).
    """
    from tools.records.migrate import list_records as run
    return _guarded(lambda: run(_out_path(plugin), query, signature, min_id, max_id, limit))


@mcp.tool()
def find_dependents(plugin: str, records: list[str] | None = None, query: str | None = None,
                    include_dependencies: bool = False) -> dict:
    """Find everything tied to a group of your own records: what points at them and what they point at.

    Pick the group with records (editor IDs or hex IDs) and/or query (editor-ID substring, e.g. "Midas").
    Returns referenced_by (other records holding their form IDs), placed (references in cells), refers_to
    (own records the group needs that are outside it) and scripts (.psc lines that look them up by object
    ID, e.g. GetFormFromFile). include_dependencies grows the group to everything it needs. Run it before
    move_records or remove_records.
    """
    from tools.records.migrate import find_dependents as run
    return _guarded(lambda: run(_out_path(plugin), records, query, include_dependencies))


@mcp.tool()
def rename_script(plugin: str, old: str, new: str) -> dict:
    """Rename a Papyrus script used by an output plugin: every VMAD that attaches it, the compiled .pex and
    the .psc in Scripts/Source (the old files are removed). A snapshot is taken first; reinstall afterwards.
    Fragment scripts (QF_/TIF_/PF_/SF_) cannot be renamed. Needs the Creation Kit compiler."""
    from tools.records.migrate import rename_script as run
    return _guarded(lambda: run(_out_path(plugin), old, new), "pre_rename_script", plugin)


@mcp.tool()
def move_records(source_plugin: str, target_plugin: str, records: list[str] | None = None,
                 query: str | None = None, rename_prefix: dict | None = None,
                 script_renames: dict | None = None, remove_from_source: bool = True,
                 allow_dangling: bool = False) -> dict:
    """Move own records (spells, items, effects...) from one output plugin to another, e.g. to split a mod.

    Pick them with records and/or query (editor-ID substring). Form IDs are kept. target_plugin is created if
    missing; both plugins must have the same master list. rename_prefix {"from": "GCG_", "to": "MT_"} changes
    editor IDs; script_renames {"OldScript": "NewScript"} renames attached scripts and recompiles them for
    the target (plugin file names inside the source are swapped too). remove_from_source deletes the originals
    and every placed reference to them; place the items again with place_item(s) in the target. Placed
    references, cells and dialogue cannot be moved. Fails if a moved record needs one that stays behind
    (see find_dependents) unless allow_dangling. Snapshots of both plugins are taken first.
    """
    from tools.core.plugin_edit import resolve_target_path
    from tools.records.migrate import move_records as run
    try:
        src = _out_path(source_plugin)
        dst = resolve_target_path(target_plugin, OUTPUT_DIR, DATA_DIR)
        _auto_snapshot(target_plugin, "pre_move_records")
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}
    return _guarded(lambda: run(src, dst, records, query, rename_prefix, script_renames, remove_from_source,
                                allow_dangling), "pre_move_records", source_plugin)


@mcp.tool()
def get_record(identifier: str, plugin: str | None = None, signature: str | None = None, full: bool = False,
               plugins: list[str] | None = None) -> dict:
    """Show a record's raw subrecords (hex plus light decoding) to plan an edit_record call.

    identifier: editor ID, name, form ID or 'plugin.esp:hexid'. With `plugin` set, reads the record
    from that plugin file (output folder or Data folder) instead of the loaded winner. full returns the
    whole hex of long subrecords instead of the first 96 bytes. Magic effects, spells, scrolls, enchantments,
    books, weapon charge (EAMT) and placed-charge (XCHG) also get a `fields` dict with named values
    (cast_type, delivery, cost...), which edit_record accepts as {field, value}.
    """
    try:
        return _editor(plugins).get_record(identifier, plugin, signature, full)
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def edit_record(identifier: str, target_plugin: str, edits: list[dict] | None = None,
                remove: list | None = None, editor_id: str | None = None,
                record_flags: int | None = None, signature: str | None = None,
                plugins: list[str] | None = None) -> dict:
    """Spot-edit one record in a plugin. Works as a patch or an in-place edit.

    target_plugin: file name (output folder, then Data folder) or absolute path; created if missing.
    If the record already exists in the target (own record or override) it is edited in place;
    otherwise an override of the loaded record is added to the target (a patch plugin).
    Official masters cannot be targeted. The existing file is backed up to .bak on save.

    edits: list of {sig, <value>, index?, offset?, append?} where <value> is exactly one of
      string, uint8, int8, uint16, int16, uint32, int32, float, hex, or form (an identifier resolved to a
      form ID; optional form_type limits the record signature). offset patches bytes inside the
      subrecord (e.g. ACBS level at offset 8 as uint16); append adds another subrecord of that sig.
    remove: list of 'SIG' or {sig, index}. Use get_record first to see the subrecords. At least one of edits,
    remove, editor_id or record_flags is required (an empty call is an error). For a text subrecord such as an
    effect's description (DNAM) or a name (FULL), edit_text is simpler: edit_text(plugin, record, "DNAM", text).
    Named fields: {field, value} edits a decoded field of MGEF (cast_type, delivery, base_cost, flags...),
      SPEL/SCRL (cost, cast_type, delivery, charge_time...), ENCH (cost, amount, cast_type, delivery),
      BOOK (value, weight), WEAP (charge) or REFR (charge); value is a number or a name such as
      'concentration' or 'touch'. get_record lists them under `fields`.
    """
    try:
        res = _editor(_with_target(plugins, target_plugin)).edit_record(identifier, target_plugin, edits, remove,
                                                                        editor_id, record_flags, signature)
        _invalidate()
        return res
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}


@mcp.tool()
def package_plugin(plugin: str | list[str], zip_path: str | None = None) -> dict:
    """Zip a plugin from the output folder with its loose FaceGen files, ready to install in Vortex.

    In Vortex use "Install From File" (or drag the zip in), then enable the mod and the plugin.
    Re-run after each edit and reinstall/replace the mod. Pass a list of plugins to package several at
    once (one zip each in output/packages; the result is keyed by plugin, and one failing plugin does not
    stop the others); zip_path applies to a single plugin only.
    """
    from tools.core.plugin_edit import package_plugin as pack, resolve_target_path
    if isinstance(plugin, list) and zip_path:
        return {"error": "zip_path can only be used with a single plugin"}

    def one(name):
        try:
            return pack(resolve_target_path(name, OUTPUT_DIR, DATA_DIR, must_exist=True), zip_path)
        except (ValueError, FileNotFoundError) as e:
            return {"error": str(e)}

    return _for_each(plugin, one)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
