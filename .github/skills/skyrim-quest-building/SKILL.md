---
name: skyrim-quest-building
description: "Use when building a Skyrim quest from scratch with stages, objectives, aliases, scripts and gated dialogue, or copying the shape of a vanilla questline: guild chain, investigation or mystery, two-quest handoff. Triggers: create a quest, build_quest, add a stage, add an objective, quest with several steps, guild questline, murder mystery, clues, handoff between quests, create_quest, add_quest_stage."
---

# Building quests from parts

Use this when no `add_fetch_quest` / `add_kill_quest` / `add_plant_quest` shape fits. For reading vanilla quests first see `skyrim-quest-structure` and `skyrim-quest-investigation`; for the shortcuts and in-game rules see `skyrim-quest-authoring`.

## Which tool
| Need | Tool |
|---|---|
| Whole quest in one atomic save | `build_quest(plugin, spec)`: `{editor_id, name, quest_type, priority, flags, stages, objectives, aliases}` |
| Grow a quest step by step | `create_quest`, `add_quest_stage`, `add_quest_objective`, `add_quest_alias` |
| Change or drop an alias | `edit_quest_alias` (flags), `remove_quest_alias` |
| Code on a stage | `add_quest_stage` `code=`, or `add_quest_stage_script` (all stages at once) |
| Script on an alias or the quest | `attach_alias_script`, `attach_quest_script` (see `skyrim-alias-scripts`) |
| Clues the player finds | `add_clue_set` |
| NPC lines gated on a stage | `add_quest_dialogue` (see `skyrim-dialogue`) |

Prefer `build_quest`: nothing is written if any part fails. Every tool snapshots first (`pre_<tool>`).

## Conventions
- Stages: 0 offer/hidden, then 10, 20, 30 ... in steps of 10 so steps can be inserted. 5 is a hidden "declined" stage. Finish with a high stage (e.g. 200) that has `complete_quest: true`. Add stages in ascending order.
- One objective per player-visible task, numbered like the stage that shows it. Advance from stage code: `SetObjectiveCompleted(10)` and `SetObjectiveDisplayed(20)`.
- Give every non-hidden stage a journal `log`. Keep titles short (see `skyrim-quest-authoring`).
- Aliases: `npc` for a unique actor, `reference` (persistent!) for items and doors, `location` for a LCTN, `external` to reuse another quest's alias. Mark the compass target with `objectives=[n]`. Use `quest_object` only while the item is a quest item.
- Quest type: pass `quest_type` ("thieves", "dark", "companion", "main", ...) rather than editing DNAM.
- The quest must be running before its topics show: start it from dialogue (`set_stage`) or give it `start_game_enabled`.

## Shapes
- **Guild chain**: one quest per step, stage 200 completes it. The giver's `add_quest_dialogue` topic for step N+1 is gated with `GetStageDone` of step N's finishing stage (set `quest=` per topic) and starts it with `set_stage` on the new quest. Reuse the giver's own dialogue quest.
- **Investigation**: one quest, one stage per clue (10, 20, 30 ...) and a "solved" stage. `add_clue_set` turns persistent references into aliases whose activation sets each stage once, then `done_stage` after all of them. Suspect dialogue is gated with `stage_min` / `stage_done`; the accusation topic uses `set_stage` to finish.
- **Two-quest handoff**: quest A's final stage code starts quest B (a quest property plus `attach_quest_script`), or B's giver topic is gated on `GetStageDone` of A. Share an NPC with the `external` alias fill.

## After building
`get_quest_structure` to confirm stages, objectives and aliases; `validate_plugin`; `package_plugin` then `install_plugin`; `quest_console_commands` for in-game checks; `skyrim-quest-debugging` if it misbehaves.

## Verified in game
A two-quest chain with a clue set, stage-gated topics, `set_stage` and a stock alias script all worked (QuestPoC.esp, built by `output/build_questpoc.py`; use it as a worked example).

## Unverified in game
Checked against vanilla `Skyrim.esm` data: every alias flag bit up to 0x10000, the `ALEQ`/`ALEA` external-alias layout (`ALST ALID FNAM ALEQ ALEA VTCK ALED`, `ALEA` an alias ID), the location alias layout (`ALLS ALID FNAM ALFL ALED`) and stage flags 0x1 (complete) and 0x2 (start up) all appear and match what the tools write. Not seen in vanilla: stage flags `shut_down` (0x04) and `keep_instance_data` (0x08), so treat those as unconfirmed. None of these were exercised in game, only compared with the data.
