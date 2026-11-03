---
name: skyrim-script-patterns
description: Use when writing or debugging Papyrus scripts for quest plugins: OnContainerChanged, comparing references, ForceRefTo aliases, property naming, None references. Triggers: script compares a property, reference is None, book dropped completes quest, edit_script, audit_plugin.
---

# Papyrus patterns for generated quests

- **Never compare against a possibly-None property.** An `ObjectReference Property` pointing at a non-persistent (interior or unloaded) reference resolves to None, so `akNewContainer == Chest` is `None == None` when the item is dropped. Compare IDs and guard: `akNewContainer != None && akNewContainer.GetFormID() == <id>`. The builder bakes the id in as a decimal literal (0xCC0FA = 835834 with Skyrim.esm at index 0).
- **OnContainerChanged(akNewContainer, akOldContainer)**: akNewContainer is None when the item is dropped into the world. Check the destination, not just that the item left the inventory. Also check the quest stage.
- **Aliases**: fill with `Alias.ForceRefTo(ref)` from a fragment when an ALFR alias does not fill. Alias and marker targets need a *persistent* reference (`make_reference_persistent`).
- **Property names must not match Papyrus types** (`Book`, `Reward`, `Quest`, `Actor`...); use `PlantQuest`, `BookItem`, etc.
- **Fragments**: TIF scripts are `TIF__<Plugin>_<INFO id>`; other scripts `<Plugin>_<QuestEditorId>_<Kind>`. Properties are attached via VMAD.
- **Editing**: `edit_script(plugin, script)` shows the source; add `find`+`replace` to patch and recompile (old source is restored if compile fails). Then `install_plugin`.
- **Auditing**: `audit_plugin` flags unguarded property comparisons, objectives without alias targets, and VMAD properties on non-persistent refs.
