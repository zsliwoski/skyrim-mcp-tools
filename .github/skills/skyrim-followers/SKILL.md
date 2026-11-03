---
name: skyrim-followers
description: "Use when making a custom or existing Skyrim NPC a follower, hireling or companion: hire for gold, follow me, wait here, dismiss, DialogueFollower quest, CurrentHireling faction, SetFollower, dialogue conditions. Triggers: make X a follower, hire an NPC, NPC should follow the player, follow me topic, edit_npc follower."
---

# Skyrim followers

Followers use the vanilla `DialogueFollower` quest (`000750BA`, script `DialogueFollowerScript`). It owns the follow package, the follower alias and the dismiss logic, so a mod only has to call it from dialogue.

## Make an NPC a hireling
Use the `follower` field of `create_npc` or `edit_npc` (see `skyrim-dialogue` for the rest of the dialogue system):

```
edit_npc("Nevs", {"follower": {"cost": 25}, "quest_editor_id": "NevsDialogueQuest"}, "Nevs.esp")
```
`follower: true` uses the default cost of 50 gold. It adds five topics for that NPC:

| Topic | Shown when | Effect |
|-------|-----------|--------|
| Would you travel with me? (N gold) | not hired, player has the gold | Takes the gold, adds him to `CurrentHireling`, calls `SetFollower` |
| Would you travel with me? (N gold) | not hired, player is short | Refusal line only |
| Wait here. | hired | `FollowerWait()` |
| Follow me. | hired | `FollowerFollow()` |
| I don't need you anymore. | hired | `DismissFollower(0, 0)` |

"Hired" means `GetInFaction CurrentHireling == 1`. Scripts are generated and compiled as in the dialogue skill, so run `package_plugin` afterwards and reinstall in Vortex.

## Hide the normal dialogue while he follows
When `follower` is set, every other `dialogue` entry in the same call (greeting, goodbye, chat topics, sweetroll...) automatically gets `GetInFaction CurrentHireling == 0`, so while he is hired only Wait / Follow / Dismiss and the vanilla follower lines show, and the hiring topics disappear. Give an entry `"show_while_following": true` to keep it visible.

Entries created in an earlier call are not changed. Fix them by adding a `GetInFaction CurrentHireling == 0` CTDA to each INFO (copy the CTDA from the "hire" refusal INFO: function 71, param1 = CurrentHireling, value 0), or recreate the NPC's dialogue with `follower` set in the same call. Nevs's existing INFOs `000804`-`00080E` were patched this way.

## Conditions on any dialogue entry
`conditions` is a list of `{"function", "form", "op", "value", "run_on"}`. Functions: `GetItemCount`, `GetInFaction`, `GetGlobalValue`, `GetStage`, `GetActorValue` (give `actor_value` as an index instead of `form`). Ops: `==  !=  >  >=  <  <=`. `run_on` is `subject` (the NPC, default) or `target` (the player). All conditions must be true, and the NPC's own `GetIsID` is always added.

## Checks and caveats
- The hiring Papyrus is `SetFollower` from `DialogueFollowerScript`; read it with `get_script("DialogueFollowerScript")`.
- Vanilla hirelings are also tracked by `HirelingQuest`; this approach skips the rehire tracking, so a dismissed NPC can be hired again at the same cost.
- A cloned NPC keeps the original's packages and factions; the follower alias package should take priority, but check `get_npc` if he does not follow.
- Wait and Follow are gated on the `WaitingForPlayer` actor value (index `0x5F`, set by `FollowerWait`/`FollowerFollow`): Wait shows at 0, Follow at 1. Use `{"function": "GetActorValue", "actor_value": 95, "op": "==", "value": 0}`. Nevs's existing INFOs `00081B` and `00081E` were patched. Untested in game.
- The vanilla `DialogueFollower` Wait/Follow/Dismiss/Trade topics are conditioned on specific NPC IDs or the housecarl faction, so they do not appear for a custom hireling; ours are the only ones.
- If the quest does not react, use the console: `sqv DialogueFollower` and `getav WaitingForPlayer` on the NPC.
- Untested in game with the LE-compiled `.pex` files in Skyrim SE.
