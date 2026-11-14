---
name: skyrim-followers
description: "Use when making a custom or existing Skyrim NPC a follower, hireling or companion: basic (vanilla style) or dynamic follower, hire for gold, follow me, wait here, dismiss, DialogueFollower quest, CurrentHireling faction, SetFollower, dialogue conditions. Triggers: make X a follower, hire an NPC, NPC should follow the player, follow me topic, edit_npc follower, upgrade a follower."
---

# Skyrim followers

Followers use the vanilla `DialogueFollower` quest (`000750BA`, script `DialogueFollowerScript`). It owns the follow package, the follower alias and the dismiss logic, so a mod only has to call it from dialogue.

## Start here: basic or dynamic?
Before building anything for a fresh follower, or before changing an existing one, use `ask_user` to ask which kind they want:
- **Basic (vanilla style):** the hire / wait / follow / dismiss topics from "Make an NPC a hireling" below, plus optionally `travel_lines`, `combat_lines` and the context presets (immersion layers 1-3 in `skyrim-dialogue`). No controller quest or script is needed.
- **Dynamic:** everything in basic plus the controller quest, scripts and player-adjustable topics from `add_follower_controls`, and the social and full-coverage layers (4-5).

If they choose dynamic, ask a second multi-select question for the features to include, and set up the quests, scripts and topics for the chosen ones in a single pass:
- Follow distance and fighting style (settings topics, packages, Aggression/Confidence)
- Combat overrides (leash packages)
- Horse sharing, horse radar and horse gifting
- Left-behind tracking and caught-up reaction
- Map marker
- Summon spell and reactions (needs left-behind)
- Context commentary: `weather_lines`, `time_lines`, `main_quest_lines`, `place_lines`
- Scene chats with neighbours (`add_npc_chat`)

Recommend the whole set when they have no preference, and mention the features marked untested in game.

### Existing follower: audit first
When the NPC already exists, do not ask what to add until you know what is there:
1. `get_npc` and `get_npc_dialogue` for the follower: check he is in `CurrentHireling` after hiring, has hire / wait / follow / dismiss topics with the expected conditions (wait and follow on `WaitingForPlayer`), and that the other topics are hidden while hired.
2. `validate_plugin` and `list_records` on the plugin: look for the controller quest (`<prefix>FollowerControl`), its script, the `<prefix>...Faction` records, `SummonTopic` and `CaughtUpTopic` on the script, the summon spell, and which commentary or bark topics exist. Features found here must not be offered again, and the ones the builder refuses to repeat (`map_marker`) would error.
3. Report anything broken (missing script `.pex`, stale SEQ file, a property that is not filled in, a duplicate or unreachable topic) and offer to fix it before adding anything.
4. Then summarise what is already present and ask the basic-or-dynamic question, showing only the features that are still missing.

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

## Settings the player can change (follow distance, fighting style)
A controller is three parts: a hidden faction whose rank is the setting, something that reads the rank, and topics that set it.

`add_follower_controls(target_plugin, spec)` builds all three:

```
add_follower_controls("MyPlugin.esp", {"npc": "MyNpc"})
```
- A hidden `<prefix>FollowDistanceFaction` (Normal / Close / Far) and `<prefix>FightingStyleFaction` (Steady / Cautious / Reckless); the NPC joins both at rank 0.
- Menu topics "talk about how you follow / fight" shown while hired, each with one child topic per rank (hidden while that rank is current) and a "Never mind" child. The fragments call `SetFactionRank` and `EvaluatePackage`. Fighting style also sets the `Aggression` and `Confidence` actor values. Override any line with `text: {"distance_1": ["prompt", "reply"]}`; keys are `distance_menu`, `distance_0..2`, `style_menu`, `style_0..2`, `exit`.
- Follow distance is read by packages: a controller quest `<prefix>FollowerControl` (priority 70) has an alias for the NPC whose package list holds a Follow package per non-default rank, conditioned on that rank, hired, and `WaitingForPlayer == 0`. Normal distance falls through to the vanilla follower quest (priority 50), so Wait still works.
- Existing saves need `startquest <prefix>FollowerControl` (or a new game) because the quest starts with the game.

Building blocks, for custom controllers:
- `create_faction(target_plugin, editor_id, name, ranks, hidden)`.
- `create_package(target_plugin, editor_id, donor, conditions, radii, combat_style)` clones a `Skyrim.esm` package. The default donor `PlayerFollowerPackage` uses the Follow template, whose first two float parameters are the minimum and maximum follow distance; `radii` replaces them. `conditions` may name a faction made in the same plugin. A package only runs once it is on an actor, so put it on an alias package list of a quest with a priority above the vanilla follower quest.
- Read existing packages with `list_conditions(plugin, "<package>")`, which decodes package conditions (`get_record` shows raw hex). `IsPlayerTeammate` is function 453 in the plugin data.
- Combat overrides are packages in a FormList referenced by the alias's `ECOR`; they only run during combat. Pass `combat_overrides: true` to `add_follower_controls` to clone the vanilla override package (a hold-near-the-player location) once per non-default fighting style, with the location radius set to a leash (Cautious tight, Reckless wide) and conditioned on the style rank. It can be run alone on a plugin that already has the controls (`follow_distance` and `fighting_style` false). Untested in game. Large mods also use extra procedure trees, which the builder does not create.
- Horse sharing: `horse_sharing: true` adds a hidden ShareHorse faction (Walks/Rides), an optional unfilled `Steed` alias on the controller quest, a quest script `<prefix>ControlScript` and two topics. "Take a horse" calls `ShareHorse(akSpeaker)`, which picks a free horse near him (not the player's last ridden one) into the Steed alias and sets the faction rank; a 3 s update loop then mounts him (`horse.Activate(follower)`) when the player is mounted and dismounts him (`Actor.Dismount()`) when the player is not, and skips combat. "Walk with me" calls `StopSharing`. Everything uses vanilla Papyrus (`GetDisplayName` is SKSE-only). Dialogue fragments call the controller with `(Control as <ScriptName>).Function()` on a Form property, so the controller script is compiled before the topics. Untested in game. Horse choice (`PickHorse`) samples 40 random actors within 3000 units, keeps those of the horse race that are alive, not the player's last-ridden horse, and have no faction owner and either no owner or the player as actor owner, then takes the closest. It cannot see whether another NPC is riding it. `horse_radar: true` adds a topic that runs this pick ahead of time (`FindHorse`) and rerunning it refreshes the controller script, so script changes reach older plugins that way.
- The controller script is the place for future features (left-behind tracking, mount radar): add functions or extend `OnUpdate` in `controller_source`.
- Horse gifting: `horse_gift: true` adds "take my horse" (`GiftHorse`: the player's last-ridden horse gets the follower as owner via `SetActorOwner`, joins the Steed alias and he starts riding it) and "I'd like my horse back" (`ReclaimHorse`: owner back to the player, he dismounts). Like the radar it reruns the controller script build, so it can be added to an existing plugin. Untested in game; the horse may be far away, in which case the update loop moves it next to him. Not covered yet: riding without the player.
- Left-behind tracking: `left_behind: true` makes the controller script's 3-second update loop watch a hired follower who is not waiting and not in combat. After about 15 s out of reach (over 3500 units or 3D unloaded) he is dismounted, moved behind the player, and his `<prefix>LeftBehindFaction` rank becomes 1; a notification shows and a "Where did you get to?" topic (shown only at rank 1) resets it. Use `GetFactionRank <prefix>LeftBehindFaction == 1` as a condition on your own lines to have him comment on it. The loop starts from the quest's `OnInit`, so on an existing save run `startquest <prefix>FollowerControl` or use a new game. Untested in game.
- Map marker: `map_marker: true` gives the controller quest a name, journal log entries and objective 10 targeting the follower alias. "I want to be able to find you on my map" sets stage 10 and displays the objective (marker on the map and compass); "You don't need to show up on my map any more" hides it (stage 20). The topics are chosen by `GetStage != 10` / `== 10`. It can only be added once per controller quest. Untested in game.
- Summon and reactions: `summon: true` (needs `left_behind`) adds two engine-spoken `idle` topics gated on the left-behind rank (2 = summoned, 3 = caught up), wired to the controller script's `SummonTopic` and `CaughtUpTopic` properties. The script sets the rank, calls `Say` on the topic, waits, then restores the rank, so a follower who catches up now speaks too. `Summon()` dismounts him, moves him behind the player and plays the summon reaction. `summon_lines` and `caught_up_lines` replace the default lines. Whether `Say` plays a custom voice-less idle topic is unverified in game.
  The player triggers `Summon()` with a self-cast spell. The result's `summon_spell` gives the script name and source; then (1) `create_script_effect(plugin, "<Prefix>SummonEffect", "Call <Name>", script_name, source, delivery="self")`, (2) `create_spell(plugin, "<Prefix>SummonSpell", "Call <Name>", [{"effect": "<Prefix>SummonEffect"}], kind="spell", delivery="self")`, (3) grant it from a topic with `edit_npc`: `{"kind": "topic", "prompt": "...", "line": "...", "properties": {"Summon": "<Prefix>SummonSpell"}, "code": "Game.GetPlayer().AddSpell(Summon as Spell)", "conditions": [hired, {"function": "HasSpell", "form": "<Prefix>SummonSpell", "op": "==", "value": 0, "run_on": "target"}]}` so it disappears once learned. Restart the MCP server first if the builder code changed.
- Commentary presets: `weather_lines`, `time_lines`, `main_quest_lines` and `place_lines` on `edit_npc` add hired-gated idle comments (see `skyrim-dialogue-context`). Override lines per key with `commentary_text`. Main-quest lines are a milestone chain keyed on `GetQuestCompleted`, so only one set shows at a time.
- Scene chats with neighbours use `add_npc_chat` (find partner references with `search_references(base=...)`); write lines that fit the follower and partner and say that each chat has a random chance to start.

Studying an existing controller: `find_dependents(plugin, records=[<faction>])` lists every package, topic and quest that reads it (note: `plugin` and `records` are both required); then `list_conditions` on each package shows the rank tests, and `get_quest_structure` shows the controller quest's script and properties.

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
