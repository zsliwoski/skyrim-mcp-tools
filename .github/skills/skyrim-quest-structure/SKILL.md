---
name: skyrim-quest-structure
description: "Use when reading, explaining or summarizing the structure of a Skyrim QUST record: stages, objectives, aliases, scenes, dialogue topics, fragments, quest types and how a quest progresses. Triggers: quest flow, quest stages, quest aliases, what does quest X do, summarize quest, get_quest, get_quest_structure."
---

# Skyrim quest structure

How to read a quest with the `skyrim-mcp-tools` MCP server and interpret what comes back.

## Tools
- `search_quests(query)`: find by editor ID, display name, or hex form ID. Story names like "Sheogorath" may not match; search the quest's title words or its editor-ID prefix (`DA15`, `BQ01`, `MQ101`).
- `get_quest(id, include_responses=false)`: record data. Stages with journal log, objectives, aliases, scenes, dialogue topics.
- `get_quest_structure(id)`: the same plus interpreted Papyrus. Per-stage fragment effects, `stage_transitions`, objectives with `driven_by`, `script_definitions`, scene phase fragments and dialogue-info fragments.
- Start with `get_quest_structure`. Use `get_quest(include_responses=true)` only when the actual dialogue text matters, since it is large.

## Anatomy of a quest
| Part | Meaning |
|------|---------|
| Stage (`INDX`) | Numbered state. Journal log text is optional; many stages are silent logic steps. |
| Fragment | Papyrus function bound to a stage (`QF_<Editor>_<FormID>.Fragment_N`). Runs when the stage is set. |
| Objective (`QOBJ`) | Journal task. Shown/completed by `SetObjectiveDisplayed` / `SetObjectiveCompleted` calls in stage fragments. Targets are aliases. |
| Alias | Named slot filled at runtime: reference (`ALST`) or location (`ALLS`). Fill types: forced reference, unique actor, created actor, from event, location ref type, find-matching. Flags: optional, quest object, allow reuse, essential. |
| Alias script | Script on one alias (extends `ReferenceAlias`). Typically event handlers that set quest stages. |
| Scene (`SCEN`) | Choreographed dialogue/packages. Phase fragments run script code at phase start or end. |
| Dialogue topic (`DIAL`/`INFO`) | Quest-owned dialogue. Info fragments (`TIF__<FormID>`) usually just call `GetOwningQuest().SetStage(n)`. |
| Quest type | None, Main, Mages Guild, Thieves Guild, Dark Brotherhood, Companions, Miscellaneous, Daedric, Side, Civil War, DLC. |

## Recurring patterns (from a random sample of 40 vanilla quests)
- **Dialogue-only quests** (`Dialogue*`, `CreatureDialogue*`, `*Scene<N>`): no stages or scripts. Just 0-4 aliases and one scene, or a topic container. Nothing to trace; summarize by speakers and topics.
- **Stage-driven quests** (`Freeform*`, `DB*`, `DA*`, `MQ*`, `dun*`): one fragment script plus an optional quest script. A typical stage fragment calls `SetObjectiveCompleted`, then `SetObjectiveDisplayed`, `Start` another quest, or `SetStage`; the last stage calls `CompleteAllObjectives` and `Stop`.
- **Dialogue drives progress**: stages advance from `TIF__` fragments (topic info) or alias-script events (`OnDeath`, `OnContainerChanged`, `OnTriggerEnter`), not from the quest script itself.
- **Multi-quest systems** (`CW*`, `WE*`, `BQ*`): many aliases (up to ~70) and a shared quest script. Behavior comes from properties set per quest, so compare siblings rather than reading each in full.
- **Parent and child quests**: a stage fragment calls `OtherQuest.SetStage(n)` or `.Start()`. Follow those edges (see `stage_transitions` and the `effects` list) to describe the whole quest line.

## Procedure
1. Find the quest ID with `search_quests`, then call `get_quest_structure`.
2. Order stages numerically; list each stage's log text and its fragment `effects`. A stage with no log and a `SetStage` effect is a relay.
3. Map each objective to the stages that display and complete it (`driven_by`).
4. Note aliases that carry scripts or hold the actors, items and locations the player touches.
5. Identify what actually triggers each stage: dialogue fragment, alias event, scene phase, or another quest.
6. Follow every `SetStage` / `Start` on a different quest and repeat for that quest.
7. Summarize in order: purpose, flow by stage, key actors and locations, related quests.

## Caveats
- `stage_transitions` only shows `SetStage` calls found in stage fragments. Stages set from dialogue, alias scripts, or the quest script will not appear there.
- A stage with no fragment and no log (for example 160 in DA15) is usually a dialogue-condition gate only.
- Completion stages (like "congratulations" stage 40 in the DA15 trials) are often set by alias triggers that need the script bodies to confirm.
