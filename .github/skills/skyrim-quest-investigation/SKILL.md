---
name: skyrim-quest-investigation
description: "Use when asked to summarize or explain a Skyrim quest end to end, including its stages, dialogue, scenes, sub-quests and underlying Papyrus. Triggers: summarize the X quest and how it works, explain related functionality, quest walkthrough from data, trace how a quest advances."
---

# Skyrim quest investigation

End-to-end workflow that combines the quest and script tools. See [skyrim-quest-structure](../skyrim-quest-structure/SKILL.md) and [skyrim-papyrus-structure](../skyrim-papyrus-structure/SKILL.md) for the details.

## Procedure
1. **Locate**: `search_quests` for the quest. If the story name gives no hit, try editor-ID prefixes (Daedric `DA##`, Dark Brotherhood `DB##`, Main `MQ###`, Civil War `CW*`, bounties `BQ##`) or find an NPC with `search_npcs`; its editor ID often shares the quest prefix (for example `DA15Sheogorath`).
2. **Structure**: `get_quest_structure` for the quest. Record stages, objectives, aliases, scenes, and the sub-quests that stage fragments start or set.
3. **Scripts**: use `script_definitions` from step 2 for quest and alias scripts. Call `get_script` for anything referenced but not included, such as scene fragments (`SF_*`).
4. **Dialogue**: only if dialogue content matters, call `get_quest(include_responses=true)` or `get_npc_dialogue` for a key speaker.
5. **Related quests**: for each other quest named in `effects` (`X.SetStage`, `X.Start`), repeat steps 2-3 at summary level.
6. **Write up** in this order:
   - Purpose and quest type
   - Flow by stage, each with what sets it
   - Key actors, items and locations (aliases)
   - What each script does
   - Related quests and systems
   - Gaps (what could not be confirmed from the data)

## Rules
- Prefer facts from tool output; mark guesses and unread pieces as unverified.
- Keep dialogue summaries short unless the user asks for lines.
- When comparing sibling quests (`BQ01`-`BQ04`, `CWResetGarrison*`), read one fully and diff the rest.
- Restart the MCP server after changing tool code, since models are cached in the process.
