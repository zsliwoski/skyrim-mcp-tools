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

## Supported features

### Reading and search
- `search_npcs`, `get_npc`, `get_npc_dialogue` – find NPCs and inspect their data and dialogue
- `search_quests`, `get_quest`, `get_quest_structure` – quest stages, objectives, aliases, scenes, topics
- `get_script` – decompile `.pex` scripts to pseudo-source
- `search_forms`, `search_cells`, `search_references`, `get_cell_contents`, `get_record`
- `get_npc_face`, `read_book`, `list_book_models`, `list_texts`, `list_conditions`

### NPC authoring
- Draft workflow: `describe_npc_fields`, `create_npc_draft`, `update_npc_draft`, `add_npc_dialogue`,
  `get_npc_draft`, `validate_npc_draft`, `write_npc_plugin`
- One-shot `create_npc`, and `edit_npc` for existing NPCs (including follower/hireling setup)
- Face shape and colour: `edit_npc_face`, `paint_npc_face`

### Quest authoring
- `add_quest` with kinds fetch, kill, plant and radiant: `add_fetch_quest`, `add_kill_quest`,
  `add_plant_quest`, `add_radiant_quest`
- `add_quest_alias`, `add_quest_stage_script`, `search_quests`, `quest_console_commands`

### Records, items and world
- `create_record`, `clone_record`, `clone_records`, `edit_record`
- `place_item`, `create_cell`, `make_reference_persistent`
- Books: `edit_book` (text and in-world model)

### Text, conditions and scripts
- `edit_text`, `edit_texts`, `lint_text`, `edit_condition`, `edit_script`

### Plugin lifecycle
- `snapshot_plugin`, `list_snapshots`, `restore_snapshot`, `diff_plugin`
- `validate_plugin`, `audit_plugin`
- `install_plugin` (copy to Data, with dry run) and `package_plugin` (zip)

`get_weather` is a placeholder tool.

## Project layout

- [server.py](server.py) – MCP tool definitions
- [tools/](tools) – everything `server.py` relies on, grouped by form category:
  [core/](tools/core) (plugin model and editor shared by everything), [quests/](tools/quests), [npcs/](tools/npcs) (including face data),
  [dialogue/](tools/dialogue), [papyrus/](tools/papyrus) (compile and decompile), [books/](tools/books), [cells/](tools/cells)
  (cells, references, item placement), [records/](tools/records) (cloning, generic edits, text, snapshots)
- [esplib/](esplib) – bundled, modified plugin-parsing library (MPL 2.0)
- [tests/](tests) – pytest suite
- [.github/skills/](.github/skills) – assistant guidance for common Skyrim modding workflows
- [misc/](misc) – standalone helper scripts (graph/tree viewers, reports)
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

## License

Licensed under the [Mozilla Public License 2.0](LICENSE). The bundled [esplib/](esplib) is a modified
copy of esplib, under the same license ([esplib/LICENSE](esplib/LICENSE)).

Skyrim is a trademark of Bethesda Softworks; this project is unaffiliated.
