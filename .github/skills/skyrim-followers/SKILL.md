---
name: skyrim-followers
description: "Use when making a custom or existing Skyrim NPC a follower, hireling or companion: hire for gold, follow me, wait here, dismiss, DialogueFollower quest, CurrentHireling faction, SetFollower, dialogue conditions. Triggers: make X a follower, hire an NPC, NPC should follow the player, follow me topic, edit_npc follower."
---

# Skyrim followers

Followers use the vanilla `DialogueFollower` quest (`000750BA`, script `DialogueFollowerScript`). It owns the follow package, the follower alias and the dismiss logic, so a mod only has to call it from dialogue.

## Make an NPC a hireling
Use the `follower` field of `create_npc` or `edit_npc` (see `skyrim-dialogue` for the rest of the dialogue system):

```
edit_npc("MyNpc", {"follower": {"cost": 25}, "quest_editor_id": "MyNpcDialogueQuest"}, "MyPlugin.esp")
```
`follower: true` uses the default cost of 50 gold. It adds these topics for that NPC:

| Topic | Shown when | Effect |
|-------|-----------|--------|
| Would you travel with me? (N gold) | not hired, player has the gold | Takes the gold, adds him to `CurrentHireling`, calls `SetFollower` |
| Would you travel with me? (N gold) | not hired, player is short | Refusal line only |
| Wait here. | hired | `FollowerWait()` |
| Follow me. | hired | `FollowerFollow()` |
| I don't need you anymore. | hired | `DismissFollower(0, 0)` |
| Trade some items. | hired | `OpenInventory(true)` |
| I need you to do something. | hired, not `IsDoingFavor` | `SetDoingFavor(true)` (vanilla favor mode) |
| (blocking, no prompt) "Still need me to do something?" | hired, `IsDoingFavor` | Blocking branch like vanilla `DialogueFollowerDoingFavorBlockingTopic`: while a favor is active it replaces the normal topic list and offers only the two below |
| Yes. Let me show you. | offered by the blocking topic | Reply only; favor continues |
| No. Never mind. | offered by the blocking topic | `SetDoingFavor(false)` |

Vanilla has no "refusal" topic for favors: those lines are engine-played voice lines, not dialogue.

**Favor reactions.** While a favor is active the engine speaks "Got it" / "I can't do that" / "Here, take a look" lines from vanilla `DialogueFollower` topics with subtypes `AGRE`, `REFU` and `SHOW` (`26EC9`, `26EC3`, `26ED0`). Their INFOs are gated by `GetIsVoiceType` on the formlists `VoicesFollowerNeutral` etc., so a custom voice type never matches them while a vanilla one such as `MaleYoungEager` does. The follower template adds `kind: "agree" | "refuse" | "show"` entries conditioned on the hireling faction; the lines are subtitle-only like the rest of the NPC's dialogue.

The blocking topic uses generic dialogue entry keys (see `skyrim-dialogue`): `"blocking": true`, `"child_of": <index>` and `"goodbye": true`. Vanilla INFOs for Wait, Follow, Dismiss, favor start, Yes and No all carry the Goodbye flag (ENAM bit 1), so the conversation closes after them; Trade and the blocking line do not. Checked against vanilla INFOs `0005C83B`, `0007A1EB`, `0005C826`, `000D8DC2`, `000D8DCC`, `000D8E14` and DLBR `00027096`. An NPC edited without `follower` can reuse the keys: a hired NPC's existing "I need you to do something." needs no change, since the blocking branch hides it during a favor.

"Hired" means `GetInFaction CurrentHireling == 1`. Scripts are generated and compiled as in the dialogue skill, so run `package_plugin` afterwards and reinstall in Vortex.

## Hide the normal dialogue while he follows
When `follower` is set, every other `dialogue` entry in the same call (greeting, goodbye, chat topics, sweetroll...) automatically gets `GetInFaction CurrentHireling == 0`, so while he is hired only Wait / Follow / Dismiss and the vanilla follower lines show, and the hiring topics disappear. Give an entry `"show_while_following": true` to keep it visible.

Entries created in an earlier call are not changed. Fix them by adding a `GetInFaction CurrentHireling == 0` CTDA to each INFO (copy the CTDA from the "hire" refusal INFO: function 71, param1 = CurrentHireling, value 0), or recreate the NPC's dialogue with `follower` set in the same call.

## Keep him close and safe
- `follower` also adds a hired greeting (`greeting` with `CurrentHireling == 1`) and a hired goodbye (`goodbye` with `CurrentHireling == 1`), because the normal ones are hidden while he follows. Add them by hand to an NPC made before this existed.
- `keep_up: true` (or `{"distance": 3000}`) attaches an Actor script that checks every 5 seconds and moves him next to the player when he is further behind than that distance. It skips combat, Wait (`WaitingForPlayer`) and non-hired states. Vanilla has no script for this (the engine's follow package handles it), so the distance is our choice.
- `protected: true` makes only the player able to kill him: others cannot kill him, he bleeds out and recovers. Use `essential: true` for a follower who can never die. Neither is limited to combat, so a protected NPC can still be killed by the player at any time.

## Layout of an in-depth follower
A rich follower is several quests, not one:
- a core dialogue quest holding the hire/greeting/goodbye/combat/ambient lines;
- a player-questions quest (ordered chains of topics the player can ask);
- a progress and main-quest commentary quest keyed on stages;
- a chat quest with one alias per named NPC and one scene per topic, plus a "talked to" tracker;
- personal story quests with their own objectives and aliases;
- small event quests for one-off reactions;
- control quests/factions for fighting style, follow distance and horse use.

Start with the core quest, then add layers as needed. See `skyrim-dialogue-context` for how each layer is conditioned.

## Scene chats with another NPC
`add_npc_chat(target_plugin, spec)` adds one two-person conversation:

```
add_npc_chat("MyPlugin.esp", {
  "scene_editor_id": "MyNpcChatLydia1", "quest_editor_id": "MyNpcChatQuest",
  "speaker": "MyNpc", "partner": "Lydia", "partner_reference": "Skyrim.esm:0A2C94",
  "trigger_lines": ["Is that Lydia?", "Look who it is."],
  "lines": [{"who": "speaker", "text": "..."}, {"who": "partner", "text": "..."}]})
```
- It creates (or extends) the chat quest with an alias for each NPC, one scene-line topic per line, the scene (one phase per line, speakers head-track each other), and an idle line on the follower's dialogue quest (`<speaker>DialogueQuest` unless `dialogue_quest_editor_id`). When the follower finishes the trigger line, its fragment starts the scene.
- Guards added to the trigger: hired (`hired: false` to drop it), not sneaking, not in combat, not in dialogue with the player, `GetDistance` to `partner_reference` under `distance` (default 900; find the reference with `search_references`), `GetRandomPercent <= random_percent` (default 10), plus any extra `conditions`. It plays once (`once: false` to allow repeats).
- Call it again with a new `scene_editor_id` for another topic with the same pair, or with another partner to build up a set of chats. Several `trigger_lines` are picked at random.
- Both NPCs must be unique actors. The partner need not be loaded, but the scene only looks right when the partner is near, so always give `partner_reference`.
- Run `validate_plugin`, then `package_plugin`/`install_plugin`. Untested in game.

## Conditions on any dialogue entry
`conditions` is a list of `{"function", "form", "op", "value", "run_on"}`. Functions: `GetItemCount`, `GetInFaction`, `GetGlobalValue`, `GetStage`, `GetActorValue` (give `actor_value` as an index instead of `form`), `IsDoingFavor` (no form), `GetInCurrentLoc`, `GetInWorldspace`, `LocationHasKeyword`, `GetEquipped`, `GetStageDone` (stage in `param2`). Location, travel and quest-based conditions are catalogued in `skyrim-dialogue-context`. Ops: `==  !=  >  >=  <  <=`. `run_on` is `subject` (the NPC, default) or `target` (the player). All conditions must be true, and the NPC's own `GetIsID` is always added.

## Checks and caveats
- The hiring Papyrus is `SetFollower` from `DialogueFollowerScript`; read it with `get_script("DialogueFollowerScript")`.
- Vanilla hirelings are also tracked by `HirelingQuest`; this approach skips the rehire tracking, so a dismissed NPC can be hired again at the same cost.
- A cloned NPC keeps the original's packages and factions; the follower alias package should take priority, but check `get_npc` if he does not follow.
- Wait and Follow are gated on the `WaitingForPlayer` actor value (index `0x5F`, set by `FollowerWait`/`FollowerFollow`): Wait shows at 0, Follow at 1. Use `{"function": "GetActorValue", "actor_value": 95, "op": "==", "value": 0}`. Untested in game.
- The vanilla `DialogueFollower` Wait/Follow/Dismiss/Trade topics are conditioned on specific NPC IDs or the housecarl faction, so they do not appear for a custom hireling; ours are the only ones.
- If the quest does not react, use the console: `sqv DialogueFollower` and `getav WaitingForPlayer` on the NPC.
- Untested in game with the LE-compiled `.pex` files in Skyrim SE.
