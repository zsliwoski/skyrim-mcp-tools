---
name: skyrim-plugin-refactoring
description: "Use when splitting, merging or cleaning up a Skyrim plugin you authored: pull a feature (spells, items, a guild) out into its own plugin, rename editor-ID prefixes or Papyrus scripts, find what depends on a set of records. Triggers: move X into its own plugin, split a plugin, extract the Midas stuff, rename a script, what uses this record, list what a plugin contains, list_records, find_dependents, move_records, rename_script."
---

# Splitting and refactoring plugins

All four tools work on plugins in the output folder (bare file names).

## Workflow
1. `list_records(plugin, query=, signature=, min_id=, max_id=)` shows what the plugin owns, with a count per type. Object-ID ranges often separate one feature from the rest.
2. `find_dependents(plugin, query="Midas")` (or `records=[...]`) returns:
   - `referenced_by`: other records holding the group's form IDs. These break if the group leaves.
   - `placed`: references in cells. They are deleted with the originals; place the items again afterwards.
   - `refers_to`: own records the group needs that are outside it. Move them too, or pass `include_dependencies=True` to grow the group to everything it needs.
   - `scripts`: `.psc` lines that look a record up by object ID, e.g. `Game.GetFormFromFile(0x000B2C, "Old.esp")`. `move_records` swaps the plugin file name in scripts it renames; any other script needs the same edit by hand (`edit_script`).
3. `move_records(source, target, query=/records=, rename_prefix={"from": "GCG_", "to": "MT_"}, script_renames={"OldScript": "NewScript"})` copies the records, keeps their object IDs, builds the renamed scripts for the target, then deletes the originals and their placements. A `query` skips placed references on its own. It refuses when:
   - a moved record needs one that stays behind (`allow_dangling` overrides);
   - the target already has the editor ID or form ID;
   - the master lists differ;
   - a record is a placed reference, cell or dialogue.
4. Re-place the items in the target with `place_item` / `place_items` (see `skyrim-item-placement`), `validate_plugin` both plugins, then `package_plugin(["Source.esp", "Target.esp"])` (or `install_plugin` with the same list).

## Rules
- Snapshots of both plugins are taken first; `restore_snapshot` undoes a bad move.
- The new plugin has new forms as far as the game is concerned: a save that already holds the old items keeps the old ones, so test on a save without them.
- Renaming scripts needs the Creation Kit compiler. Fragment scripts (`QF_`, `TIF_`, `PF_`, `SF_`) are named after their record and cannot be renamed.
- Use `rename_script(plugin, old, new)` rather than editing VMAD bytes: it updates every record, recompiles and deletes the old files.
- A record both plugins need (a keyword, a global) should stay in one plugin, with the other mastering it. `move_records` does not add masters.
- `output/` is gitignored. Two checkouts can hold different copies of the same plugin; the server reads `SKYRIM_OUTPUT_DIR`, or `output` next to `server.py`.
- Install both plugins afterwards (`install_plugin`); the Data copy of the old plugin keeps the moved records until then.
