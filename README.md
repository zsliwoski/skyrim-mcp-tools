# skyrim-mcp-tools

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant read, author and maintain
**Skyrim Special Edition** plugins (`.esp`). It can inspect vanilla and modded data (NPCs, quests,
dialogue, Papyrus scripts, cells) and build new content: NPCs, quests, dialogue, books, items, cells
and face data.

Built on `esplib` for plugin parsing and the Python `mcp` SDK (`MCPServer`).

## Requirements

- Python 3.13
- Skyrim Special Edition (the `Data` folder is read for masters and scripts)
- Python packages from [requirements.txt](requirements.txt): `mcp`, `Pillow` and `numpy` (face painting), `pytest` (tests)
- Optional: [requirements-lore.txt](requirements-lore.txt) (`fastembed`) for semantic lore search
- `esplib`, bundled in [esplib/](esplib) (modified copy; it requires `lz4`). `requirements.txt` installs it, or install it alone with:

```powershell
pip install -e ./esplib
```

## Running

```powershell
python server.py
```

Register it in your MCP client as a stdio server running `python server.py` from this folder.

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SKYRIM_DATA_DIR` | `C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data` | Skyrim `Data` folder |
| `SKYRIM_PLUGINS` | `Skyrim.esm` | Default plugin load list (`;`-separated on Windows) |
| `SKYRIM_OUTPUT_DIR` | `.\output` | Where authored plugins, scripts, snapshots and packages are written |
| `SKYRIM_CK_DIR` | `C:\Program Files (x86)\Steam\steamapps\common\Skyrim` | Creation Kit install folder (Papyrus compiler and `Data\Scripts.rar`) |
| `SKYRIM_PAPYRUS_SOURCES` | `.\papyrus_src` | Cache of vanilla Papyrus sources used when compiling |
| `SKYRIM_SKSE_SOURCES` | (none) | Extra folders of SKSE `.psc` sources (`Data\Scripts\Source` is checked automatically) |
| `SKYRIM_TOOLS_DIR` | `Tools` next to `Data` | Skyrim SE `Tools` folder (voice editor `.fuz` conversion) |
| `SKYRIM_LORE_DB` | `.\lore_db\lore.db` | Local lore database (see [Lore database](#lore-database-optional-built-locally)) |
| `SKYRIM_API_DB` | `.\api_db\api.db` | Local Papyrus API database (see [Papyrus API database](#papyrus-api-database-optional-built-locally-separate-from-lore)) |

Plugin names resolve to the output folder first, then the Data folder. Absolute paths also work.
Tools that modify plugins are serialized with a lock, so batched calls are safe.

### Papyrus sources and compiler

Tools that compile scripts (quest stage scripts, dialogue fragments) need the Creation Kit installed.
The vanilla `.psc` sources in `papyrus_src/` are not part of this repo (they are Bethesda's content and
are git-ignored). On first compile they are extracted automatically from the CK's `Data\Scripts.rar`
using `tar`, so no manual setup is needed beyond installing the Creation Kit and setting
`SKYRIM_CK_DIR` if it is not in the default location. To prepare them ahead of time, run:

```powershell
python -c "from authoring.papyrus_build import ensure_sources; print(ensure_sources())"
```

If SKSE is installed, its `.psc` sources in `Data\Scripts\Source` (or the folders listed in
`SKYRIM_SKSE_SOURCES`) are put ahead of the vanilla ones when compiling, so scripts can call SKSE functions such
as `RegisterForMenu` or `RegisterForAnimationEvent`. Vanilla Papyrus is preferred, but an approved behaviour is
never swapped for a vanilla approximation without asking the user first; use `detect_skse` and `api_get` to check
what a plan needs, and see the `skyrim-skse-scripting` skill.

## Supported features

### Reading and search
- `search_npcs`, `get_npc`, `get_npc_dialogue` – find NPCs and inspect their data and dialogue
- `search_quests`, `get_quest`, `get_quest_structure` – quest stages, objectives, aliases, scenes, topics
- `get_script` – decompile `.pex` scripts to pseudo-source
- `api_get`, `api_search`, `api_status` – look up Papyrus functions and events (see the API database section)
- `detect_skse` – report whether SKSE is installed, whether the compiler can see its sources, and which output scripts use it
- `search_forms`, `search_cells`, `search_references`, `get_cell_contents`, `get_record`
- `get_npc_face`, `read_book`, `list_book_models`, `list_texts`, `list_conditions`

### NPC authoring
- Draft workflow: `describe_npc_fields`, `create_npc_draft`, `update_npc_draft`, `add_npc_dialogue`,
  `get_npc_draft`, `validate_npc_draft`, `write_npc_plugin`
- One-shot `create_npc`, and `edit_npc` for existing NPCs (including follower/hireling setup)
- Face shape and colour: `edit_npc_face`, `paint_npc_face`

### Quest authoring
- `add_quest` with kinds fetch, kill, plant and radiant: `add_fetch_quest`, `add_kill_quest`,
  `add_plant_quest`, `add_radiant_quest`. A fetch quest's item is owned by the player (no theft bounty;
  `stolen: true` keeps the cell owner) and its alias carries the Quest Object flag so it cannot be dropped.
- `add_quest_alias`, `add_quest_stage_script`, `search_quests`, `quest_console_commands`
- `package_plugin` writes a SEQ file so start-game-enabled quests start on existing saves; the quest-authoring
  skill also describes a bootstrap quest that starts a plugin's quests when it is installed mid-playthrough

### Records, items and world
- `create_record`, `clone_record`, `clone_records`, `edit_record` (raw bytes or named fields such as
  `{"sig": "ENCH", "field": "cost", "value": 250}`, or `record_flags`), `copy_subrecords`, `drop_conditions`,
  `remove_records`. `clone_record` clears the Non-Playable flag on ARMO and WEAP clones so they show in the inventory.
- `diff_records` (field-aware comparison of two records) and `get_record(full=...)` with decoded `fields`
- Splitting and refactoring plugins: `list_records`, `find_dependents` (what points at a group of records),
  `move_records` (move records to another plugin, renaming editor-ID prefixes and scripts) and `rename_script`
- `place_item` (coordinates, or `near="<something in the cell>"` to pick a free spot beside it), `place_items`
  (several items on free floor spots), `find_free_spots`, `create_cell`, `make_reference_persistent`;
  `get_cell_contents` takes `x`, `y`, `radius` to list the nearest objects. `search_cells`, `get_cell_contents`
  and `find_free_spots` take `target_plugin` to include the plugin you are authoring
  (`list_records(plugin, signature="CELL")` lists its cells)
- Books: `edit_book` (text and in-world model)
- `create_shader`, `create_unique_item` (clone an item with its own name, enchantment and full charge),
  `item_console_commands` (`player.additem` / `player.addspell` lines for a plugin's items; filter with `query` and `sections`)

### Magic: enchantments, spells and scripts
- `create_enchantment`, `enchant_item`, `create_script_effect` (Papyrus-driven effect with a `delivery` of
  touch, aimed, ray or self; touch is a short-range ray), `create_script_effects` (several effects, each
  attached to its item, in one call), `create_script`, `check_script` (dry-run compile, writes nothing),
  `edit_script` (patch a script's source with find/replace and recompile),
  `enchantment_test_plan` (in-game checklist for an enchanted item), `resolve_form` (editor ID to exact IDs),
  `create_enchanted_item` (clone boots/weapon, scripted enchantment, attach and place, in one call)
- `create_spell`, `create_spell_tome`, `create_scroll`
- `create_spell_set`: one script backing a whole spell family (spells, tomes, scroll, staff) placed in a cell

### Text, conditions and scripts
- `edit_text`, `edit_texts`, `lint_text`, `edit_condition`, `edit_script`

### Lore database (optional, built locally)
- `lore_search`, `lore_get`, `lore_status`, `build_lore_db`

This repo ships no lore text. You build a SQLite full-text index on your own machine, and it is git-ignored
(`lore_db/lore.db`, or set `SKYRIM_LORE_DB`). Sources, each stored with its license and page URL:

| Source | Command | License of the text |
|---|---|---|
| `game`: books in your installed plugins | `python -m tools.lore build game-books` | your own game data, local use only |
| `uesp`: UESP wiki Lore/Skyrim pages, from an XML export you download | `python -m tools.lore build uesp-dump <file.xml.bz2>` | CC BY-SA 2.5, attribute UESP |
| `uesp`: the same through the API (slow, 1 request per second) | `python -m tools.lore build uesp-crawl --limit 500` | CC BY-SA 2.5, attribute UESP |

Then `python -m tools.lore status` and `python -m tools.lore search "Dwemer"` check it. Building a source
replaces that source's rows.

**Optional semantic search.** Keyword search is best for exact names; vectors help with concepts and paraphrase
("who opposes the Thalmor"). Run `pip install -r requirements-lore.txt` (fastembed: CPU only, no PyTorch), then
`python -m tools.lore build vectors`. The model (`BAAI/bge-small-en-v1.5`, about 130 MB) downloads on first
use and everything stays local. Embedding is CPU-bound, so a full UESP import takes a long while (the 1,500
game books alone took several minutes); it is incremental, so an interrupted run resumes and only new chunks
are embedded. Once vectors exist, `lore_search` defaults to a hybrid of keyword and semantic results
(`mode` can force `keyword`, `semantic` or `hybrid`). Rebuilding a source drops its vectors; rerun `build vectors`.

Use the results as reference and cite them; do not paste wiki or game text into
a published plugin or redistribute the database.

### Papyrus API database (optional, built locally, separate from lore)
- `api_get`, `api_search`, `api_status`, `build_api_db`

Functional facts for writing Papyrus live in their own SQLite file (`api_db/api.db`, or set `SKYRIM_API_DB`),
so lore queries never return script text and script lookups never return flavour. Both databases share the
same store and embedding code in [knowledge/](tools/knowledge); `lore_*` tools only touch the lore file and `api_*`
tools only touch this one. Use `api_get(script, member)` for exact lookups and `api_search` for intent. Only
`native` members are indexed, so a missing member means the call does not exist **unless** it is a
non-native engine event such as `OnInit` (declared in the base script type, not natively); those return empty
even though they are valid, so confirm them against the compiler with `check_script`. Each result carries
`skse: true` for members that need the Script Extender (for example `RegisterForKey`, `OnKeyDown`,
`Actor.IsSwimming`), and `api_get` on a member searches the script's parent chain (`OnPlayerLoadGame` is on
`ReferenceAlias`, `Actor` and `ActiveMagicEffect`, not `Form`). Verify every call of a planned script this way
before building, and ask the user before substituting a different behaviour (see the `skyrim-skse-scripting` skill).

| Source | Command | Notes |
|---|---|---|
| `psc`: functions, events and properties of every script | `python -m tools.papyrus_api build scripts` | your own sources (extracted CK sources plus SKSE sources found by `SKYRIM_SKSE_SOURCES` / `Data\Scripts\Source`); members only in the SKSE copy are flagged `[SKSE]`; rerun after changing SKSE. Only scripts declaring `native` members are indexed (engine API); pass `--all-scripts` for everything. Actor values drop the `AV` EditorID prefix (`AVHealth` → `Health`) |
| `actor-values`: ActorValueInfo records | `python -m tools.papyrus_api build actor-values` | your own game data |
| `ckwiki`: Creation Kit wiki pages | `python -m tools.papyrus_api build ckwiki-dump <file.xml.bz2>` or `ckwiki-crawl --limit 500` | CC BY-SA 3.0, attribute the CK wiki |

`python -m tools.papyrus_api get Actor DamageActorValue` and `... search "restore health"` check it. Vectors are
optional and work the same way as for lore (`python -m tools.papyrus_api build vectors`, same model, separate
embeddings).

**Quick setup**
1. Make sure the Papyrus sources exist: install the Creation Kit (`SKYRIM_CK_DIR`) and let a first compile extract
   `Data\Scripts.rar` into `papyrus_src/` (see [Papyrus sources and compiler](#papyrus-sources-and-compiler)). If SKSE
   is installed, its `.psc` files in `Data\Scripts\Source` are picked up automatically.
2. `python -m tools.papyrus_api build scripts` (the essential step; add `--all-scripts` to index non-native
   scripts too), then `python -m tools.papyrus_api build actor-values`.
3. Check it with `python -m tools.papyrus_api status`, or the `api_status` tool.
4. Rerun `build scripts` after installing or updating SKSE, otherwise SKSE-only members are not flagged `[SKSE]`.
   The `build_api_db` tool does the same from inside a session. Restart the MCP server if it was running with no database.

### Plugin lifecycle
- `snapshot_plugin`, `list_snapshots`, `restore_snapshot`, `diff_plugin`
- `validate_plugin` (also warns about zero-cost enchantments and old header versions), `audit_plugin`
- `install_plugin` (copy to Data, with dry run), `deploy_plugin` (validate, then `mode` "package", "install" or
  "both" for zip plus install; can remove stale loose copies) and
  `package_plugin` (zip); `install_plugin` and `package_plugin` also take a list of plugins

### Voice line editor (`misc/voice_lines_gui.py`)
A Tkinter app for the voiced side of a plugin. Run it from the repo root:

```powershell
python misc/voice_lines_gui.py path\to\Plugin.esp
```

- Lists every spoken dialogue line in the plugin with quest, topic, speaker, voice type and whether audio exists,
  with a text filter and a "Missing audio only" toggle. Audio is found as loose files (output folder, then Data)
  and inside BSAs (the plugin's own archive and any `*voices*` archive in Data).
- **Play** plays `.wav` directly and decodes `.fuz`/`.xwm` (including from BSAs) through ffmpeg.
- **Record** captures the microphone to a 16-bit mono 44.1 kHz WAV; **Upload audio...** copies in a `.wav`, `.fuz` or
  `.xwm`, or converts `.mp3`/`.ogg`/`.flac`/`.m4a` to WAV.
- **To .fuz** / **All WAVs to .fuz** generate lip sync from the line text, encode xWMA, and pack a `.fuz`
  (the WAV is then removed), so the line lip-syncs in game. A loose WAV also plays in Skyrim SE but with a still face.
- **New voice type...** adds a VTYP record to the plugin (after a snapshot) and can assign it to the line's speaker
  NPCs that are defined in the plugin.
- **Pack zip** runs `package_plugin`; **Install to Data** copies the plugin and its files into Data.

Voice files are written to `<plugin folder>\Sound\Voice\<Plugin.esp>\<voice type>\<quest>_<topic>_<info id>_<n>`
using the Creation Kit naming (quest cut to 10 characters, topic to 15, load-order byte of the info ID zeroed).
`package_plugin`, `install_plugin` and `validate_plugin` include the `Sound` folder for the plugin.

Requirements:
- Windows (microphone recording uses the built-in MCI API; playback uses `winsound`), and Python's `tkinter`.
- Skyrim SE `Data` folder (`SKYRIM_DATA_DIR`) for masters and existing audio.
- ffmpeg on `PATH` for playing `.fuz`/`.xwm` and for converting non-WAV uploads. Plain WAV needs nothing extra.
- For `.fuz` conversion: the Skyrim SE `Tools` folder next to `Data` (installed with the Creation Kit), containing
  `Audio\xwmaencode.exe`, `LipGen\LipGenerator\LipGenerator.exe` (with `FonixData.cdf`) and
  `LipGen\LipFuzer\LIPFuzer.exe`. Set `SKYRIM_TOOLS_DIR` if it lives elsewhere. Lip sync is generated for English.
- A voice type must be chosen for each line before recording or uploading, because the folder is named after it.
  Create one with **New voice type...** if the NPC has none.

## Project layout

- [server.py](server.py) – MCP tool definitions
- [tools/](tools) – everything `server.py` relies on, grouped by form category:
  [core/](tools/core) (plugin model and editor shared by everything), [quests/](tools/quests), [npcs/](tools/npcs) (including face data),
  [dialogue/](tools/dialogue), [papyrus/](tools/papyrus) (compile and decompile), [books/](tools/books), [cells/](tools/cells)
  (cells, references, item placement), [records/](tools/records) (cloning, generic edits, text, snapshots),
  [lore/](tools/lore) (local lore database builder and search),
  [papyrus_api/](tools/papyrus_api) (local Papyrus API database: scripts, actor values, CK wiki),
  [knowledge/](tools/knowledge) (SQLite/FTS store, embeddings and MediaWiki import shared by both)
- [esplib/](esplib) – bundled, modified plugin-parsing library (MPL 2.0)
- [tests/](tests) – pytest suite
- [.github/skills/](.github/skills) – assistant guidance for common Skyrim modding workflows
- [misc/](misc) – standalone helper scripts (graph/tree viewers, reports, the voice line editor)
- `output/` – generated plugins and sample builds

## Testing

```powershell
pip install -r requirements.txt
python -m pytest
```

GitHub Actions ([tests.yml](.github/workflows/tests.yml)) runs only the tests that don't need game files
(`pytest -m "not game and not compiler"`). Tests marked `game` need Skyrim SE (they load `Skyrim.esm`, about
30 s), and tests marked `compiler` also need the Creation Kit Papyrus compiler, so run those locally with
`SKYRIM_DATA_DIR` and `SKYRIM_CK_DIR` set if your install isn't in the default Steam location:

```powershell
python -m pytest -m game
python -m pytest -m compiler
```

Without `Skyrim.esm` in the Data folder, `game` tests are skipped automatically.

The slow plugin-writing tests are split by area so you can run only what you changed:
`tests/test_npc_tools.py`, `test_record_tools.py`, `test_quest_tools.py`, `test_package_tools.py`,
`test_enchant_tools.py` (enchantments, script effects, items) and `test_spell_set_tools.py`. Shared fixtures
live in `tests/conftest.py` and shared helpers in `tests/helpers.py`. The CI matrix groups the files the same
way (core, spells, authoring); add new test files to a group in the workflow.

## License

Licensed under the [Mozilla Public License 2.0](LICENSE). The bundled [esplib/](esplib) is a modified
copy of esplib, under the same license ([esplib/LICENSE](esplib/LICENSE)).

Skyrim is a trademark of Bethesda Softworks; this project is unaffiliated.
