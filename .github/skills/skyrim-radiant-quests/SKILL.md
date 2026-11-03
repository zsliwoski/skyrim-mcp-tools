---
name: skyrim-radiant-quests
description: Use when an NPC should hand out repeatable, randomised collection jobs (a different item and amount each time), optionally promoting the player in a faction. Triggers: radiant quest, repeatable quest, random fetch, guild contract, rank up per quest, add_radiant_quest, create_cell for a guild hall.
---

# Radiant (repeatable) quests

`add_radiant_quest(identifier, spec, target_plugin)` (or `add_quest(kind="radiant", ...)`) adds a repeatable collection job to an NPC that already exists in the plugin.

## What it builds
- Two GLOBs, `<quest>_Count` and `<quest>_Pick`, and a start-game-enabled QUST. Stage 10 means active, stage 20 means done. There is one objective per item, shown as "Collect <count> <name>".
- Three topics in the NPC's dialogue quest:
  - `offer`: shown while the quest is not active, plus optional conditions. Its script resets the quest, picks a random item and amount, sets the globals and starts stage 10.
  - `reminder`: shown at stage 10.
  - `turn_in`: shown at stage 10. It checks the inventory, removes the items, pays `amount * unit_reward` gold, runs `on_complete`, and sets stage 20. If the player is short, it shows a notification.

## Spec
```
{
 "quest_editor_id": "X_Contract", "quest_name": "...",
 "items": [{"item": "<MISC editor ID>", "name": "Ruby", "min": 2, "max": 4, "unit_reward": 40}],
 "offer": {"prompt": "...", "lines": ["..."]},
 "reminder": {...}, "turn_in": {...},
 "conditions": [(func, fid, op, value, run_on)],   # optional, on the offer only
 "on_accept": "<papyrus>", "on_complete": "<papyrus>"   # optional; `pl` is the player
}
```

## Faction rank-up
Use `on_complete` Papyrus plus `conditions` on later NPCs' offers. For example, keep a progress GLOB and gate NPC k with `GetGlobalValue >= k`. Promote only when the global equals k, so repeated jobs don't over-promote.

## Cells
`create_cell(target_plugin, editor_id, name, source=<cell>, copy_references=True)` clones a cell (use an editor ID or an 8-digit hex form ID). It does not copy actors, navmesh, door teleports or scripts. The cell has no entrance, so use `coc <editor_id>`.

## Story Manager mode (the game's own radiant system)
Set `"mode": "story_manager"` in the spec. Each item becomes its own quest, so `items` take `item`, `name`, `count`, `reward` (no min/max) and the NPC's dialogue quest must exist (`dialogue_quest_editor_id`).
- Builds a story event KYWD, a branch node (under vanilla SMEN 0x1379A, "started by script") and a quest node whose condition matches the keyword; the node lists the item quests.
- The offer topic calls `SendStoryEvent` on the keyword; the game picks one quest at random and fills its aliases: a dungeon (`LocTypeDungeon`, override with `location_keyword`), a boss chest in it (`BossContainer`), and the gems, created inside the chest from a leveled list. The objective has the chest's compass marker and text "Recover N X from the chest in <dungeon>".
- Structure copied from vanilla MGR20 and the Mages Guild nodes; startup stage 0 sets stage 10 via a QF fragment. Turn-in is a per-quest topic at stage 10 that checks the inventory, pays gold, runs `on_complete`, sets stage 20 and stops the quest. The offer topic resets every item quest first so they can be picked again.
- Untested in game: if a quest never starts, check `sqv <quest>`, that the NPC's offer topic ran, and the SMQN condition.

## Limits
- Default (Papyrus) mode: "radiant" means a Papyrus-randomised item and amount per offer, with no location or reference aliases and no quest markers. Use story mode for those.
- The dialogue lines play whether or not the hand-in succeeds; the notification shows the outcome.
- Restart the MCP server to load new tools, then `validate_plugin` and `package_plugin`. See `skyrim-quest-debugging` if topics are missing.
