---
name: skyrim-quest-debugging
description: "Use when a custom Skyrim quest does not start, its dialogue topic is missing, a quest marker does not show, a script does not fire or a reward is not given: validate_plugin, install_plugin, quest_console_commands, sqv, getstage, SEQ file, stale Data copy. Triggers: quest doesn't load, quest not running, topic not showing, no marker, how can I check the quest is running."
---

# Debugging a custom quest

Work through these in order; most failures are in the first three.

1. `validate_plugin(plugin)`: reports missing masters, duplicate editor IDs, dangling references, attached scripts with no `.pex`, objective targets with no alias, a stale `SEQ` file, and whether the Data folder copy differs from the output copy.
2. `package_plugin(plugin)` first, then `install_plugin(plugin)` (always both, so the zip and Data copy stay in sync; or `dry_run=True` to see what differs). A stale `Data\<plugin>.esp` or `SEQ\<plugin>.seq` was the cause of "the quest doesn't load". Restart the game after installing; a plugin with new start-game quests needs the SEQ file to start them on existing saves.
3. `quest_console_commands(plugin, quest)` gives the console commands. In game:
   - `sqv <Quest>`: shows whether the quest is running, its stages, and what each alias is filled with. An empty alias means no marker.
   - `getstage <Quest>` on this quest and on the prerequisite quest (offer topics usually need the previous quest at stage 30).
   - `startquest <Quest>` if it is not running; `setstage <Quest> <n>` to skip ahead (this skips dialogue scripts, so item gifts will not happen).
4. Topic missing: quest running? prerequisite stage reached? the topic's own stage condition (the offer needs stage 0)? Is it nested under a parent topic that is not currently shown? Topics live in `<Npc>DialogueQuest`, not the quest itself.
5. Marker missing: the objective `QSTA` must target an existing alias; persistent exterior refs need `ForceRefTo` from the offer TIF script, and interior refs may need the same. Check the alias in `sqv`.
6. Script not firing: the `.pex` ships next to the plugin, property names match (they must not clash with Papyrus types), and the event matches (item script `OnContainerChanged`, actor script `OnDeath`). Enable Papyrus logging in `Skyrim.ini` and read `Logs\Script\Papyrus.0.log` under `Documents\My Games\Skyrim Special Edition`.
7. Reward missing: the turn-in topic's TIF script gives it; confirm the stage condition and that its `.pex` exists.
8. Rebuild cleanly: `restore_snapshot(plugin, "pre_<kind>_quest")`, rerun the builder, `validate_plugin`, `install_plugin`.
