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
Dialogue goes in the `dialogue` list of `create_npc`, `edit_npc` (existing NPC, e.g. `Nevs`) or `add_npc_dialogue` (draft). Each entry:
- `kind`: `topic`, `greeting` or `goodbye`. A topic needs a `prompt`.
- `lines`: the NPC's reply, or several lines spoken in sequence as one reply; plain text, Windows-1252 only.
- `answers` (instead of `lines`): several alternative replies to the same prompt, e.g. `[["Rumor one."], ["Rumor two."]]`, written as several responses under one topic. `pick` is `random` (default) or `in_order` (each once, last repeats). Not combinable with `give_items`/`code`. See `skyrim-group-banter`.
- `give_items`: items the player receives when the line ends, as ids or `{"item", "count"}`.
- `code` and `properties`: Papyrus statements run when the line ends, and a `{name: form}` map usable as `Form` properties in that code.

```
edit_npc("Nevs", {"quest_editor_id": "NevsDialogueQuest", "dialogue": [
  {"kind": "topic", "prompt": "Got anything to eat?",
   "lines": ["Here, take a sweetroll."], "give_items": [{"item": "Sweetroll", "count": 1}]}]},
  "Nevs.esp")
```

Rules the tools follow:
- Re-using `quest_editor_id` adds topics to the existing quest instead of making a new one.
- Scripts are compiled with the Creation Kit compiler (`SKYRIM_CK_DIR`, default the `Skyrim` Steam folder) into `Scripts` beside the plugin; `package_plugin` includes them.
- Names, prompts and lines are inline text, so a localized plugin (STRINGS files) is refused.
- Every topic is conditioned on that NPC only. There is no voice audio, so lines show as subtitles with no lip sync.
- Item ids need an exact editor ID when a name is ambiguous. Use `search_forms("ALCH", "sweetroll")` first (sweet roll is `FoodSweetroll`, an `ALCH`).

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
