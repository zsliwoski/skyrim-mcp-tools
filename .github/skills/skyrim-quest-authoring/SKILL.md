---
name: skyrim-quest-authoring
description: "Use when creating a new Skyrim quest, fetch quest, quest item or quest-driven NPC dialogue in a plugin: add_fetch_quest, create_record, search_references, placing an item in the world, stages, objectives, aliases, GetStage conditions. Triggers: add a quest to an NPC, find the X item quest, fetch quest, place an item by a landmark, create a MISC item, new QUST."
---

# Authoring quests

For reading existing quests see `skyrim-quest-structure`; for dialogue details `skyrim-dialogue`; for followers `skyrim-followers`.

## Workflow
1. **Find the location**: `search_references(base, worldspace, x, y, radius)` lists placed references of a base object (e.g. `WoodChoppingBlock` in `Tamriel`) with cell, grid and coordinates. Use `search_cells` for cell names. If several match, pick one and tell the user which.
2. **Fetch quest**: `add_fetch_quest(npc, spec, plugin)` builds everything in one call (the NPC must already be in the plugin):
   - a quest item (clone of a vanilla MISC, `item_clone_from`) with an `OnContainerChanged` script that advances the quest on pickup,
   - a reference at `placement` (put it ~40 units above the surface it rests on),
   - a QUST with stages 10/20/30, objectives 10/20 (20 targets the NPC alias),
   - four topics: offer (stage 0), reminder (stage 10-29 and no item), turn-in (has the item; takes it, pays `reward_gold`, completes the quest), done (stage 30).
   Quest topics stay visible while the NPC is a hired follower.
3. **Anything else**: `create_record(plugin, signature, editor_id, subrecords)` writes any record type from an ordered subrecord list. Run `get_record` on a similar vanilla record first and copy its subrecord order and sizes. Form references resolve by editor ID and add masters.
4. **Other quest shapes**: `add_quest(kind, npc, spec, plugin)` is the single entry point (`kind` = `fetch`, `kill` or `plant`; specs are on the per-kind tools).
   - `kill`: persistent actor with an `OnDeath` script, side quest, offer/during/turn-in/done topics, gold reward.
   - `plant`: custom book (with your text) whose script advances the quest when placed in a world container (`container_ref`, from `search_references`), a unique reward weapon with an existing enchantment, a nested `during` menu and a `lost_book` re-issue topic.
5. `validate_plugin`, then ALWAYS `package_plugin` and `install_plugin` together, package first, so the zip and the Data copy stay in sync (`install_plugin` copies plugin, scripts, FaceGen and a fresh SEQ into Data; the zip is for Vortex/MO2). Restart the MCP server to load new code. Use `quest_console_commands` for the in-game checks.
6. Cloning vanilla items: `clone_record(source, target_plugin, editor_id, name=, description=)` (or `clone_records` for several) writes inline text and adds masters; see `skyrim-record-cloning`. Books/items for NPCs and cell dressing: `skyrim-npc-loot-and-flavor`. If a quest does not behave in game see `skyrim-quest-debugging`.

## Rules learned the hard way
- A quest alias (and so a compass marker) only fills for a persistent reference. Interior containers/doors from Skyrim.esm are not persistent, and a script property pointing at one can resolve to None. Use `make_reference_persistent(reference, plugin)`; `add_plant_quest` does it for its container automatically. Never compare against a possibly-None property in a script; compare `GetFormID()` instead.
- Writing style: all in-world text (books, dialogue, journal entries) must be lore-friendly. No real-world references: use Tamrielic days (Morndas, Tirdas, Middas, Turdas, Fredas, Loredas, Sundas), months (Morning Star, Sun's Dawn, ...) and oaths: "By the Eight!" for speakers who follow the Imperial/White-Gold line (no Talos), "By the Nine!" for those who revere Talos (Nords, Stormcloaks); never "Oh my god". Pick the oath the speaker would actually believe, and use oaths rarely: most NPCs and lines should have none (see `skyrim-dialogue`, "Voice and religion").
- Papyrus script properties must not be named like a Papyrus type (`Book`, `Reward`, `Quest`); use `BookItem`, `RewardItem`, `PlantQuest`.
- `search_forms` resolves only the record types in `tools/core/quest_model.py` `LOAD_SIGS`; add a signature there (as done for ENCH, MGEF, GLOB, MESG, EQUP) if a lookup comes back empty.
- Quests need `start game enabled` (DNAM flag `0x1`) to run without a start script; dialogue only shows once the quest is running.
- Quest-stage Papyrus is avoided: stages are set from dialogue TIF fragments and from the item script. Scripts are compiled with the CK compiler (see `tools/papyrus/papyrus_build.py`) and shipped as loose `.pex` (`package_plugin` includes `TIF__<plugin>_*` and `<plugin>_*`).
- Start-game-enabled quests only start on an existing save if a `SEQ\<plugin>.seq` file exists; `package_plugin` now generates it. If a quest's topics still don't appear, use `sqv <QuestEditorId>` and `startquest <QuestEditorId>` in the console.
- Player topics for a quest NPC go in the NPC's existing, proven dialogue quest (`<Npc>DialogueQuest`), not in the fetch quest. A separate fetch quest with FULL, stages and objectives was running (stage 0) yet its topics never showed, although the records matched byte for byte. `add_fetch_quest` now does this by default (override with `dialogue_quest_editor_id`); the topics still test the fetch quest's stage via `GetStage` CTDAs.
- Keep the fetch quest for stages, objectives and aliases only.
- A `QuestItem` ALFR alias pointing at a persistent exterior ref did not fill in game. The offer topic's TIF script therefore fills it with `ForceRefTo`, then calls `SetStage(10)` and `SetObjectiveDisplayed(10)`. Aliases are optional (FNAM 2) so the quest still starts if a fill fails.
- Quest topics have no hired/follower condition, so they show while the NPC is a follower; add a GetInFaction condition to the topic only if that is unwanted.
- Debugging order when a topic is missing: `sqv <quest>`, `getstage <quest>`, check the NPC's other topics, then move the topic into the working dialogue quest. Test on a fresh save and reinstall the zip after every rebuild.
- Rebuilding a quest: records can't be deleted. Builder tools take a snapshot first (`pre_<kind>_quest`); `restore_snapshot(plugin, "pre_plant_quest")` then rerun the builder, then `install_plugin`.
- Plugin must not be localized: quest/objective/log/dialogue text is stored inline.
- Exterior references are non-persistent unless `marker` (default) or `placement.persistent` is set; persistent exterior refs go in the worldspace's persistent cell, which is overridden along with Tamriel's WRLD (large file).
- Exterior placements create a CELL override; patching a cell that other mods edit may conflict.
- Editor IDs must be unique across loaded plugins.
- The new-object-ID counter is not reliably stored by esplib; the builders recompute it from existing records.
- Keep the NPC's top-level speech options uncluttered: when a quest has several player topics (flavor questions, "where do I find...", etc.), show only one parent topic at the top level (the offer, or a "About the <quest>..." topic) and nest the rest as subtopics under it, shown only after the parent is chosen. Never list every quest-related line side by side in the initial menu. `add_kill_quest` (`during`) and `add_plant_quest` (`during`, `lost_book`) already nest their flavor topics; in custom dialogue use a `{'child_of': index}` tuple item in `make_dialogue`.

## Editing an existing quest
**Always prefer small in-place edits over a full rebuild.** A rebuild is slow (the guild plugin takes ~15 minutes), re-rolls form IDs and risks regressions. Use `edit_text`, `edit_condition`, `edit_script`, `edit_record` or `edit_npc` for any change to text, conditions, scripts or fields, even many of them in a row. Rebuild only when a record's structure must change and no edit tool can do it.
Edit in place first; rebuild only when records change shape.
- Text (book, dialogue, journal, names): `list_texts` then `edit_text`. Check lore with `lint_text` (`skyrim-lore-writing`).
- Conditions (e.g. stage a topic appears at): `list_conditions` then `edit_condition`.
- Scripts: `edit_script` (patch and recompile; `skyrim-script-patterns`).
- Markers: `make_reference_persistent`; check with `audit_plugin` and `validate_plugin`.
- Review changes with `diff_plugin` (vs latest snapshot), then `install_plugin`. Edit tools snapshot automatically; `restore_snapshot` undoes them.
- Snapshot, restore and rerun the builder only when a record's structure must change.

## Not yet automated
Scenes, quest start from a script, and alias scripts. Use `create_record` plus `papyrus_build` as building blocks, and test in game.

## Markers, aliases and stage scripts
- `add_fetch_quest` now defaults to `marker: true`: the item reference is persistent (placed in the worldspace's persistent cell) and a `QuestItem` alias is the compass target of objective 10. Pass `marker: false` for the old non-persistent behaviour.
- `add_quest_alias(quest, plugin, name, reference=<hex ref ID or ref editor ID> | npc=..., objectives=[n])` adds an alias and makes it the target of those objectives. Get reference IDs from `search_references`. Exterior references must be persistent for the marker to work.
- `add_quest_stage_script(quest, plugin, {"20": "SetObjectiveDisplayed(30)"}, properties={...})` compiles a `QF__` fragment script and attaches it to the QUST. Stages must exist; each call replaces the previous stage script, so pass all stages every time. Aliases become `Alias_<name>` ReferenceAlias properties.
- To rebuild a quest from scratch, restore the plugin's `.bak` (taken before each save) and run the tool again, since records can't be deleted.
