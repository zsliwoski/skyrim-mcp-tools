---
name: skyrim-papyrus-structure
description: "Use when interpreting compiled Papyrus (.pex) scripts for Skyrim: quest scripts, alias scripts, QF_/TIF__/SF_ fragment scripts, script properties, events, states, inheritance and decompiled pseudo-source. Triggers: what does this script do, decompile pex, fragment script, alias script, OnDeath handler, get_script."
---

# Skyrim Papyrus structure

How to read compiled scripts with `get_script` and understand what they do.

## Tool
`get_script(name, include_source=false)` returns, from the compiled `.pex` in loose Scripts or the plugin's BSAs (`Skyrim - Misc.bsa` plus BSAs named after loaded plugins):
- `inheritance`: parent chain (for example `ReferenceAlias`, `Alias`).
- `objects[]`: `variables`, `properties` (auto/get/set, types), `functions` with `params`, `returns`, `calls`, `property_reads`, `property_writes`, `branches`, `events`, and `depends_on` types.
- `include_source=true` adds pseudo-source: bytecode steps in temp variables with `L<n>` jump labels, not true Papyrus. Use it only when you need exact conditions or control flow.

Property values bound by the record (which form each property points to) come from `get_quest_structure` (`bound_properties`), not from `get_script`.

## Script kinds and what they do
| Kind | Name pattern | Extends | Role |
|------|--------------|---------|------|
| Quest fragment | `QF_<Quest>_<FormID>` | `Quest` | One `Fragment_N` per stage; bound by the quest's VMAD. Has `Alias_*` and bound properties for forms. |
| Dialogue fragment | `TIF__<FormID>` | `TopicInfo` | Runs when a dialogue line is spoken. Usually `GetOwningQuest().SetStage(n)` or one action. |
| Scene fragment | `SF_<Scene>_<FormID>` | `Scene` | Runs at scene phases; commonly `SetStage`, `PlaceAtMe` effects, `Moveto`, `Utility.Wait`. |
| Quest script | `<Quest>Script`, `BQScript`, `WEScript` | `Quest` | Shared helpers and logic that fragments call through the cast quest. |
| Alias script | `BQBountyScript`, `BQPlayerScript` | `ReferenceAlias` | Event handlers on an alias target. |
| Generic helper | `defaultSetStageAliasScript` | `ReferenceAlias` | Sets a quest stage when its alias triggers; configured by properties (`stage`, `TriggerType`, `prereqStageOPT`). |

## Events seen in vanilla quest alias scripts
`OnHit`, `OnDeath`, `OnContainerChanged`, `OnInit`, `OnCombatStateChanged`, `OnEquipped`, `OnActivate`, `OnCellLoad`, `OnEnterBleedout`, `OnOpen`, `OnTriggerEnter`, `OnRead`, `OnLoad`, `OnUpdate`, `OnCellDetach`, `OnLocationChange`. Typical use: detect what the player does to the alias, then call `GetOwningQuest().SetStage(n)`.

## Reading what is returned
- **Boilerplate**: `GotoState` and `GetState` appear in every script; ignore them.
- **Casting the quest**: `GetOwningQuest()` followed by a cast to the quest script and a function call means an alias script is calling back into the quest script. Find that function in the quest script.
- **Temps**: `::tempN` and `::NoneVar` are compiler temporaries. The tool folds simple ones into call expressions (for example `game.GetPlayer().AddItem(Gold001, RewardAmount, False)`).
- **Alias properties**: `Alias_<Name>` and `::Alias_X_var` link to quest aliases; `Alias_Hold.GetLocation()` means the location in the `Hold` alias.
- **State machines**: a `GotoState("hasBeenTriggered")` after firing means a once-only trigger.
- **Static calls**: `game.GetPlayer()`, `utility.Wait()`, `debug.trace()` are engine globals.
- **Quest-effect calls**: `SetStage`, `SetObjectiveDisplayed/Completed/Failed`, `CompleteAllObjectives`, `Start`, `Stop`, `Reset`.

## Procedure
1. Identify the script kind from its name and parent.
2. List properties and what they refer to, noting which are record-bound (forms, aliases, globals, keywords).
3. For each event or function, state its trigger and its effect in one line, using `calls` and `property_writes`.
4. Trace cross-script calls: alias script to quest script function, fragment to another quest's stage.
5. Pull `include_source=true` only for branching logic (`branches > 0`) whose conditions matter.
6. Summarize as trigger → effect, grouped by script.

## Limits
- Pseudo-source is a reconstruction. Conditions are bytecode comparisons with jumps, not If/Else blocks, and casts print as `as <cast>`.
- Native engine functions have no body; only their name and signature are shown.
- Scripts not in Misc.bsa or the plugins' BSAs return `compiled script (.pex) not found`. Add the BSA or loose file for that DLC or mod.
