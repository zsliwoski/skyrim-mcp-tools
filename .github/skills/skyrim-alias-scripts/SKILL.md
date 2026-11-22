---
name: skyrim-alias-scripts
description: "Use when attaching Papyrus to a quest alias or quest: react to a death, pickup, read, activation, trigger or location change and advance a quest; stock scripts like defaultSetStageAliasScript; script properties. Triggers: when the player picks up X advance the quest, when the NPC dies, alias script, attach_alias_script, attach_quest_script, OnDying, OnContainerChanged, OnRead, OnActivate, OnTriggerEnter, OnLocationChange."
---

# Alias and quest scripts

Check `skyrim-skse-scripting` before writing new Papyrus. Quest design is in `skyrim-quest-building`; reading compiled scripts is in `skyrim-papyrus-structure`.

## Tools
- `attach_alias_script(quest, plugin, alias, script, properties, source)` and `attach_quest_script(quest, plugin, script, properties, source)`.
- `source` is Papyrus (the `Scriptname` must equal `script`) and is compiled for you. Omit it to bind an existing script (stock vanilla, or a `.pex` already shipped).
- Each property must be declared by the script. Values: bool/int/float/str as is, a list of ints or strs for arrays, `{"form": id}` for a record, `{"alias": name}` for an alias of this quest.
- Existing scripts, stage fragments and properties are kept. Calling again rebinds only the properties you pass.
- Property names must not equal a Papyrus type or script name (`Count`, `Door`, `Book`, `Quest` fail to compile).
- Run `package_plugin` afterwards so the `.pex` ships.

## Event to pattern
| Want | Alias fill | Event |
|---|---|---|
| NPC or creature dies | npc / persistent ref | `Event OnDying(Actor akKiller)` |
| Player picks up an item | persistent item ref | `Event OnContainerChanged(ObjectReference akNew, ObjectReference akOld)`, check `akNew == Game.GetPlayer()` |
| Player reads a book | book ref | `Event OnRead()` |
| Player activates a thing | persistent ref | `Event OnActivate(ObjectReference akActionRef)`, check it is the player |
| Player enters a volume | trigger ref | `Event OnTriggerEnter(ObjectReference akActionRef)` |
| Player arrives somewhere | player alias | `Event OnLocationChange(Location akOld, Location akNew)` |

Compare references with `GetFormID()`, not against a possibly-None property.

## Fire-once pattern
```
Scriptname MyQuestDeathScript extends ReferenceAlias
Quest Property OwnerQuest Auto
Int Property TargetStage Auto
Bool done = false
Event OnDying(Actor akKiller)
    If !done && OwnerQuest.GetStage() < TargetStage
        done = true
        OwnerQuest.SetStage(TargetStage)
    EndIf
EndEvent
```
Bind with `properties={"OwnerQuest": {"form": "MyQuest"}, "TargetStage": 30}`.

## Stock scripts
`defaultSetStageAliasScript` has `stage`, `prereqStageOPT` and `TriggerType`; bind it without `source`. For others, find a vanilla quest that uses one (`get_quest_structure`) and read it with `get_script`.

## Clues
`add_clue_set` generates one shared `<QuestEditorId>ClueScript` (a ReferenceAlias with `OnActivate`) with `OwnerQuest`, `ClueStage`, `AllClueStages` and `DoneStage`; prefer it to hand-writing one script per clue.

## Check
`get_quest_structure` shows scripts on the quest and aliases. If a script does not fire, see `skyrim-quest-debugging`.
