---
name: skyrim-dialogue
description: "Use when reading, writing or debugging Skyrim NPC dialogue: DIAL/INFO topics, greetings, goodbyes, dialogue quests, conditions, TIF__ fragment scripts, giving items from dialogue, and why a custom NPC has no dialogue in game. Triggers: add dialogue to an NPC, make an NPC say X, give an item when talking, topic not showing, dialogue quest not starting, get_npc_dialogue, edit_npc dialogue."
---

# Skyrim dialogue

How to read, author and debug dialogue with the `skyrim-mcp-tools` MCP server.

## Anatomy
| Part | Meaning |
|------|---------|
| Quest (`QUST`) | Owns every topic. It must be running for its topics to appear. |
| Topic (`DIAL`) | One player choice or an NPC-initiated line. `QNAM` is the owning quest, `FULL` the prompt the player picks, `SNAM` the subtype: `CUST` custom topic, `HELO` greeting, `GBYE` goodbye. |
| Response (`INFO`) | Child of a topic, in a type 7 group labelled with the topic's form ID. Holds the NPC line(s) (`NAM1`), conditions (`CTDA`) and an optional `VMAD` fragment. |
| Condition | Decides who says it. A `GetIsID` (function 72) on the NPC base record limits a line to one NPC. |
| Fragment | `TIF__<name>` script, `Extends TopicInfo`, with `Fragment_0` run when the line begins or ends (INFO fragment flags: 1 begin, 2 end). |

## Reading dialogue
- `get_npc_dialogue(npc)` lists the topics and lines an NPC can say, with the quest and conditions.
- `get_quest(id, include_responses=true)` shows a quest's topics and text; `get_quest_structure(id)` adds the interpreted fragments.
- `get_record(id)` shows the raw subrecords of any DIAL, INFO or QUST when something looks wrong.

## Writing dialogue
Dialogue goes in the `dialogue` list of `create_npc`, `edit_npc` (existing NPC, e.g. `MyNpc`) or `add_npc_dialogue` (draft). Each entry:
- `kind`: `topic`, `greeting`, `goodbye`, or an engine-spoken bark kind (see "Engine-spoken lines"). A topic needs a `prompt`.
- `lines`: the NPC's reply, or several lines spoken in sequence as one reply; plain text, Windows-1252 only.
- `answers` (instead of `lines`): several alternative replies to the same prompt, e.g. `[["Rumor one."], ["Rumor two."]]`, written as several responses under one topic. `pick` is `random` (default) or `in_order` (each once, last repeats). Combinable with `give_items`/`code`, which run after whichever answer plays. See `skyrim-group-banter`.
- `blocking`: `true` makes a prompt-less topic in a blocking branch (while its conditions hold, only its `child_of` follow-ups are offered, like vanilla favor mode). `child_of`: index of an earlier topic entry in the same `dialogue` list; the topic is offered only after that entry's reply instead of in the menu. `goodbye`: `true` sets the INFO "Goodbye" flag so the conversation closes after the reply (vanilla does this for Wait, Follow, Dismiss, favor start, Yes and No). See `skyrim-followers`.
- `give_items`: items the player receives when the line ends, as ids or `{"item", "count"}`.
- `code` and `properties`: Papyrus statements run when the line ends, and a `{name: form}` map usable as `Form` properties in that code.

```
edit_npc("MyNpc", {"quest_editor_id": "MyNpcDialogueQuest", "dialogue": [
  {"kind": "topic", "prompt": "Got anything to eat?",
   "lines": ["Here, take a sweetroll."], "give_items": [{"item": "Sweetroll", "count": 1}]}]},
  "MyPlugin.esp")
```

Rules the tools follow:
- Re-using `quest_editor_id` adds topics to the existing quest instead of making a new one.
- Scripts are compiled with the Creation Kit compiler (`SKYRIM_CK_DIR`, default the `Skyrim` Steam folder) into `Scripts` beside the plugin; `package_plugin` includes them.
- Names, prompts and lines are inline text, so a localized plugin (STRINGS files) is refused.
- Every topic is conditioned on that NPC only. There is no voice audio, so lines show as subtitles with no lip sync.
- Item ids need an exact editor ID when a name is ambiguous. Use `search_forms("ALCH", "sweetroll")` first (sweet roll is `FoodSweetroll`, an `ALCH`).

## Engine-spoken lines (barks)
Besides menu topics, the engine speaks lines by itself when something happens. Vanilla picks them by voice type, so a custom voice type (e.g. `MyVoice`) hears none of them unless the NPC has its own. Add them with a `kind` and no `prompt`; each entry may use `answers` for several random variants. Lines are subtitles only.

| Option | Kinds | When |
|--------|-------|------|
| `combat_lines: true` | `attack`, `power_attack`, `bash`, `block`, `hit`, `taunt`, `flee`, `bleedout`, `death`, `normal_to_combat`, `normal_to_alert`, `alert_to_combat`, `combat_to_normal` | Fighting, grunts and jeers |
| `ambient_lines: true` | `notice_corpse`, `observe_combat`, `shout_reaction` | Any NPC: finding a dead body, watching a fight nearby, hearing the player shout |
| `travel_lines: true` (implied by `follower`) | `idle` conditioned on `CurrentHireling` | A hired companion on the road and in places |
| `follower` | `agree`, `refuse`, `show` | Favor reactions, see `skyrim-followers` |

Every NPC should get `combat_lines` and `ambient_lines`; hirelings get the travel set too. Fixed rule: the more time the player spends with an NPC, the more location and travel lines he needs, because he will repeat himself quickly. A shopkeeper can keep the generic set; a companion should have many `idle` entries and a distinct set per location.

How vanilla does location comments (`DialogueFollower` topic `HirelingIdles`, 247 INFOs, subtype `IDLE`): each line is an INFO conditioned on the speaker, being a follower, and a place. Places are checked with these `conditions` functions:
- `LocationHasKeyword` (function 562) with a location keyword: `LocSetCave`, `LocSetNordicRuin`, `LocSetDwarvenRuin`, `LocTypeBanditCamp`, `LocTypeDraugrCrypt`, `LocTypeMine`, `LocTypeMilitaryFort`, `LocTypeVampireLair`, `LocTypeWarlockLair`, `LocTypeForswornCamp`, `LocTypeDwarvenAutomatons`.
- `GetInCurrentLoc` (359) with a named location: `WhiterunLocation`, `WindhelmLocation`, `SolitudeLocation`, `MarkarthLocation`, `RiftenLocation`, `FalkreathLocation`, `HighHrothgarLocation`, `IvarsteadLocation`, `KarthwastenLocation` and others; a named ruin such as `MzinchaleftLocation` gets its own lines.
- `GetInWorldspace` (310): vanilla uses `Tamriel == 0` for interiors.
- `GetStage` on a quest: vanilla adds a few comments tied to a stage (`DialogueFollower` 997 and 999, `HirelingQuest` 998), and lines with no place condition are the roadside comments.
- Personality: vanilla also gates variants by voice-type formlists (`MaleYoungEager`, `MaleNord`, `MaleBrute`, `FemaleDarkElf`), so the same place gets a different line per character. Our generic lines are written voice-neutral; write specific ones in the NPC's own voice.
- Throttle: vanilla also sets reset hours (`0x0555`, copied by the tool for `idle`) and a `HirelingCommentNextAllowed` global so comments do not fire back to back.
Other vanilla barks not yet generated: `ACYI`, `LOIL`/`ALIL`, `TRES` and per-quest `IDLE` topics (towns such as Whiterun, Windhelm, Solitude have their own).

`shout_reaction` (`PCSH`) is vanilla's reaction to the player using a shout. `DialogueGeneric` has 29 lines, one or two per voice type (voice-type formlist `GetIsVoiceType`, function 426, which the tool does not support) plus two unconditioned fallbacks ("What was that?", "How did you do that?"). Guards (`DialogueGuardsGeneral`, 5 lines, also keyed on the player's sex) and a few story characters (Cicero, Esbern) have their own. A custom voice type matches only the unconditioned lines, so give a character with a distinct personality his own `shout_reaction` entry (e.g. an eager young man: "Wow! That must be the Voice!").

Write the lines for the NPC, not from the vanilla list: keep the generic defaults for the baseline, then add several `idle` lines per place that reflect the NPC's history and tastes (a Riverwood man in a cave, a priest at a crypt).

## Remarks about the player (guards and townsfolk)
The full catalogue of context conditions is in the `skyrim-dialogue-context` skill.
Guards and some townsfolk greet the player with a comment that depends on who the player is. These are not a separate system: they are `HELO` greetings (`kind: "greeting"`) with several INFOs, each guarded by conditions, and the engine picks any that pass at random. In `Skyrim.esm` the `DialogueGuardsGeneral` quest (topic `0002BDDD`, 24 lines) is the model:
- **Quest progress:** `GetStage` on a main or guild quest, e.g. a guard says "You killed a dragon" once `MQ104` is past stage 80, news of a murder after `DB01`, "the Thieves Guild is back" from a global like `TGCrimeWhiterunTaken`. Vanilla also uses `GetStageDone` (`form` = quest, `param2` = stage number, `value` 1 for done); `GetStage` with `>=` is the simpler alternative. Use these so towns react to what the player has done.
- **Faction and place:** the speaker's own faction (a city guard faction) and `GetInCurrentLoc`, so the line is only said by guards of that hold.
- **Appearance:** `GetEquipped` on the player (`run_on: target`) with a weapon, armor or an amulet, e.g. "Who did you have to kill to get that blade?" for Mehrunes' Razor, a challenge for Stormcloak armor in an Imperial hold, a remark on an Amulet of Mara (`RelationshipMarriage`).
- **Other triggers:** guards also react to shouts (`PCSH`, 5 lines) and witnessing crimes (`WTCR`).

To give a custom NPC this, add `greeting` entries (or `answers` for variants) with `conditions` such as:
```
{"kind": "greeting", "lines": ["Killed a dragon, did you? Well done."],
 "conditions": [{"function": "GetStage", "form": "MQ104", "op": ">=", "value": 80}]}
```
A guard-like or public-facing NPC should have a few of these (quest progress, a notable item, his own faction); a private character needs none. Keep them occasional: a greeting that never changes feels dead, but a remark on every meeting gets tiring, so make each one conditional.

## Voice and religion
- Don't make every NPC invoke the gods. Most Skyrim speech has no oath at all, and a trade or hobby topic (gem cutting, smithing, farming) is not a religious context.
- Per NPC, at most an occasional oath or one passing mention. Across a cast, most lines and most NPCs should have none, so oaths mean something when used. Never open or close lines with one by habit.
- Give each NPC a different interest instead: craft detail, history and provenance, local rumours, rivalries, money, family, grudges.
- For topics with a spiritual side (such as gems and stones), lean on the setting's own magic: standing stones, soul gems, Dwemer and Ayleid ruins, Aedric and Daedric lore, mages and alchemists. Show curiosity, not sermons.
- Keep the oath rules in `skyrim-lore-writing` for the lines that do have one.
- For groups (guilds, halls, towns), add opinions about each other and "Heard any rumors?" flavour as described in `skyrim-group-banter`.

## Writing fragments by hand
A fragment script has this shape; the tool generates it, but custom `code` must fit inside:
```
Scriptname TIF__X Extends TopicInfo Hidden
Function Fragment_0(ObjectReference akSpeakerRef)
  Actor akSpeaker = akSpeakerRef as Actor
  ; code
EndFunction
Form Property Give0 Auto
```
INFO `VMAD`: version 5, object format 2, the script with its properties, then fragment data with `filename` equal to the script name, flags 2, and one fragment `Fragment_0` (`unknown` 1). `VMAD` is the first subrecord of the INFO.

## Debugging "no dialogue in game"
Check in this order, using the in-game console:
1. `help <QuestEditorId> 0` lists the quest and topic records. Nothing means the plugin did not load or the records are missing.
2. `sqv <QuestEditorId>` shows whether the quest is running and its alias fill. A quest that is not running shows no topics. Try `startquest <QuestEditorId>`, then talk again.
3. If it works after `startquest`, the quest is not starting by itself. A "Start Game Enabled" quest may need a `.seq` file to start on an existing save; also try a new save.
4. Greeting plays but the topic menu is empty: player topics (`CUST`) need a top-level dialogue branch. Each topic needs a `DLBR` (`QNAM` quest, `TNAM` 0, `DNAM` 1 = top-level, `SNAM` the topic) and a `BNAM` on the DIAL pointing back at it. The builder now writes these. Otherwise compare the DIAL and INFO subrecords with a vanilla topic using `get_record`.
5. A cloned NPC inherits the original's packages and factions, which can change who can talk to it. Check `get_npc`.
6. Script not firing: confirm the `.pex` is in the mod's `Scripts` folder and the script name matches the INFO `VMAD`.

## Known gaps
No branching or follow-up topics, no per-answer conditions, no voice files, no scenes, no quest stages from dialogue, and no `.seq` generation. State these rather than guessing when asked to do them.
