---
name: skyrim-dialogue-context
description: "Use when making an NPC react to its surroundings or the player: location, travelling, a body or fight nearby, the player's gear, faction or quest progress, combat grunts, and the conditions vanilla uses for them. Triggers: NPC says something in a cave, comment on the road, guard remarks about the player, notice a corpse, react to quest progress or armor, what conditions does vanilla use, ambient barks, idle lines."
---

# Context-specific dialogue

Vanilla NPCs speak without being asked. These lines are normal INFOs with a bark `kind` and conditions that describe the situation; the engine picks one that passes. Authoring details are in `skyrim-dialogue`; hired companions are in `skyrim-followers`.

## Bark options
| Option | Kinds | When it plays |
|--------|-------|---------------|
| `combat_lines: true` | `attack`, `power_attack`, `bash`, `block`, `hit`, `taunt`, `flee`, `bleedout`, `death` and the combat/alert transitions | Fighting, grunts, jeers |
| `ambient_lines: true` | `notice_corpse`, `observe_combat`, `shout_reaction` | Any NPC finding a body, watching a fight or hearing a shout |
| `travel_lines: true` | `idle` gated on `CurrentHireling` | A hired companion on the road and in places (implied by `follower`) |
| `follower` | `agree`, `refuse`, `show` | Favor reactions |
| by hand | `greeting` with `conditions` | Remarks about the player |

Vanilla gates many of these by voice type, so a custom voice type hears none unless the NPC carries its own lines. Whether bark text shows without audio depends on the General Subtitles setting (unverified).

## Conditions by situation
| Situation | Condition | Notes |
|-----------|-----------|-------|
| Cave, ruin, camp, mine, crypt, fort | `LocationHasKeyword` + `LocSetCave`, `LocSetNordicRuin`, `LocTypeBanditCamp`, `LocTypeDraugrCrypt`, `LocTypeMine`, `LocTypeMilitaryFort`... | Function 562. Pair with `IsInInterior == 0` for an approach line, `== 1` for an inside line |
| A named place | `GetInCurrentLoc` + `WhiterunLocation`, `RiftenLocation`, ... | Function 359 |
| Interior vs outdoors | `GetInWorldspace` + `Tamriel` | Function 310 |
| On the road | no place condition | Vanilla's unconditioned follower idles |
| Quest progress | `GetStage` + quest, op `>=`, or `GetStageDone` + quest with `param2` = stage, `value` 1 | e.g. `MQ104` >= 80 dragon killed, `MQ306` > 0 victory, `DB01` >= 200 |
| World event | `GetGlobalValue` + global | e.g. `TGCrimeWhiterunTaken` |
| Player's gear | `GetEquipped` + item/formlist, `run_on: "target"` | e.g. `DA07MehrunesRazor`, `ArmorStormcloakCuirass` |
| Speaker's faction | `GetInFaction` + faction | `CurrentHireling`, guard factions |
| Favor active | `IsDoingFavor` | no `form` |
| Companion state | `GetActorValue`, `actor_value: 95` | Wait/Follow |
| Near a landmark or a person he knows | `GetDistance` + a placed reference as `Skyrim.esm:013487` (hex ID from `search_references`; editor IDs like `LucanValeriusREF` are not accepted), op `<`, `value` in game units | Only when that reference is in the same loaded area. Use a persistent actor, door or marker reference |
| Where someone lives or works | `GetInCurrentLoc` + their town or location (e.g. `RiverwoodLocation`) | Fires whenever he is in that place, with no need for the person to be close |
| Sneaking, random variety, interior | `IsSneaking == 0` (vanilla's hirelings stay quiet while sneaking), `GetRandomPercent <= N`, `IsInInterior` | No `form` |

Conditions accept an optional integer `param2` (the second CTDA parameter, e.g. the stage for `GetStageDone`); `form` is still the first.

## Landmark and acquaintance comments
Vanilla's `HirelingIdles` mostly keys on places (caves, ruins, named locations) and only rarely on a specific actor, so "knowing someone" in practice means the line fires in their town. Two patterns work:

- **Place knowledge:** `idle` + `CurrentHireling == 1` + `GetInCurrentLoc` on the location. e.g. in Riverwood: "Lucan keeps the Trader going on his own, you know."
- **Person nearby:** `idle` + `CurrentHireling == 1` + `GetDistance` to the person's reference `< 1500`. e.g. "That's Faendal. He and Sven both fancy the same girl."
- **Run `GetDistance` on the player (`run_on: "target"`)** when the line is about the player walking up to a landmark or person (the distance is then player to that reference). Without it the distance is measured from the speaker. A line meant to fire when the player is right beside the person uses a small radius (about 100); a general "he's in view" line can use ~1500.
- **Cave approach vs inside:** split cave comments by `IsInInterior`. `LocationHasKeyword LocSetCave` + `IsInInterior == 0` is the approach line outside the entrance ("Looks like a cave up ahead"); `IsInInterior == 1` is the inside line ("Mind your head in here"). Without the interior check the outside wording fires inside too.
- **Random throttle:** add `GetRandomPercent <= N` (e.g. 30) so a landmark line doesn't fire every time the engine rolls an idle. Stack two `GetRandomPercent` conditions on nearly every idle to keep them rare; one upper bound does the same job here.
- Add `IsSneaking == 0` and keep several variants in one `answers` list so they rotate; `idle` entries carry vanilla's reset hours so they don't spam.
- Write these from the companion's own history: who he'd know, what he'd think of them.

## What follower-style mods condition on
Observed in a full-featured follower mod (hundreds of INFOs, grouped by what they test). The builder's `conditions` support the items marked ✔; the rest need a new function added to `CONDITION_FUNCTIONS` first.

| Situation | Conditions the mod uses | Builder |
|-----------|------------------------|---------|
| Weather | Idle + `IsRaining == 1` or `IsSnowing == 1`, both with `IsInInterior == 0` | `IsRaining`/`IsSnowing` not yet; `IsInInterior` ✔ |
| Time of day | Greetings by `GetCurrentTime` ranges (5-12, 12-18, 18-22); night idle: `GetGlobalValue GameHour < 6` or `> 18` with `IsInInterior == 0`. The two GameHour tests are OR'd | `GetGlobalValue` ✔ |
| Main quest | `GetStage` ranges on `MQ102` (== 30), `MQ103` (>= 50 and < 180, and `>= 180`), `MQ104` (48-65 and 85-110), `MQ105` (10-20) | ✔ |
| Side quests and own quest | `GetStage`, `GetStageDone` with `param2`, `GetQuestCompleted` on the mod's own quest | `GetStage`, `GetStageDone` ✔ |
| Place | `GetInCurrentLoc` for hubs, inns, shops, temples; `GetInWorldspace` for cities; `GetInCell` for single interiors (Dragonsreach, Jorrvaskr); `LocationHasKeyword` for cave, ruin, inn, store, temple, player house, mine, Orc stronghold | `GetInCurrentLoc`, `GetInWorldspace`, `LocationHasKeyword` ✔; `GetInCell` no |
| Cave interior vs approach | `LocSetCave` + `IsInInterior == 1` (12 lines) and `== 0` (7 lines), plus other `LocSet*` keywords with and without `IsInInterior` | ✔ |
| Person nearby | `GetDistance` to a placed ref (Nazeem, Faendal, Farkas, Aela, Cicero, Enthir, Heimskr, the housecarl), `< 100`, run on the player | ✔ |
| Player's race and sex | `GetIsRace` and `GetIsSex` on the player (greetings and goodbyes keyed to the player's race) | no |
| Player's level and skills | `GetLevel >= 5/10/12`, `GetActorValue` (index) `> 50`/`> 70` on skill AVs, on the player | `GetActorValue` ✔ with `run_on: "target"` |
| Player's gear | Full armour sets (four `GetEquipped` conditions: helmet, gauntlets, boots, cuirass) for daedric, draugr, dwarven, iron, steel plate; `WornHasKeyword` for "wearing no body armour" | `GetEquipped` ✔; `WornHasKeyword` no |
| Player's inventory | `GetItemCount` for Skooma (>= 1 or >= 5), Nirnroot, Dwarven oil, `Gold001 >= 15000` | ✔ with `run_on: "target"` |
| Player's active magic | `HasMagicEffect` for summoned flame, frost and storm atronachs | no |
| Crafting | `IsCurrentFurnitureObj` on the player with a crafting keyword (forge, alchemy, enchanting, cooking pot, tanning rack, grindstone) | no |
| Player is a werewolf | `GetIsRace WerewolfBeastRace` | no |
| Hearing a hit, taunt, noticing creatures | `HIT_`, `TAUT`, `ZKEY`, `NOTC` topics: mostly `IsSneaking == 0`, `GetRandomPercent` and, for `NOTC`, `GetIsRace` run on the target (dragon, giant, wolf, draugr, falmer, skeever...) | kinds ✔; race no |
| Follower state | `GetInFaction CurrentFollowerFaction`, `DismissedFollowerFaction`, `GetActorValue 95` (WaitingForPlayer), a mod global flag | ✔ |

Throttling is uniform: nearly every `IDLE` carries `IsSneaking == 0` and two stacked `GetRandomPercent` conditions, `GREETING`/`GBYE` mostly just `IsSneaking == 0` and sometimes `GetRandomPercent < 5-30`, and `NOTC` `GetRandomPercent < 20`. Weather, time and nearby-person lines should also keep `IsInInterior`, `IsSneaking` and a random cap, so they stay rare.

## How a deep, main-character-style follower comments (observed in a large follower mod)
Scale: thousands of INFOs. Most are plain `IDLE` and `CUST` lines, with many `NOTC`/`ATCK`/`COTN`/`HIT_` combat lines, `GBYE`/`HELO` and a few dozen `FEXT`/`AGRE`/`REFU`/`SHOW`/`TAUT`. Almost every INFO carries the speaker `GetIsID`, and most carry a `GetStage` / `GetStageDone` on a progress quest.

**Keys used on ambient lines (most common first)**
- Place: `LocationHasKeyword` (by far the most used; hold, city, dungeon type, inn), `GetInCurrentLoc`, `GetInCell`, `GetInWorldspace`.
- Player or follower state: `IsSneaking`, `GetSitting`, `IsCurrentFurnitureObj`, `IsRidingMount`, `GetEquipped`, `GetHealthPercentage`, `IsInCombat`, `IsMoving`.
- Time: `GetDayOfWeek` (one INFO per day), `GetCurrentTime` window, `GetRandomPercent` throttle.
- Progress: `GetStage`/`GetStageDone`, `GetQuestCompleted`, `GetQuestRunning`, `GetPCMiscStat`.
- Who: `GetIsRace`, `GetIsVoiceType`, `GetDistance` to a named NPC, `GetFactionRank`, `HasSpell`/`HasMagicEffect`/`HasPerk`/`HasKeyword`.
- Not talking: `IsTalking == 0` and `IsInDialogueWithPlayer == 0` (or an OR-block run on the target) so an ambient line never talks over a conversation.

**Comments on other NPCs: three layers**
1. Short bark. `GetDistance` to the NPC (run on the player), `GetRandomPercent` throttle, `GetStageDone` of a "has met" stage to pick a first-meeting line versus a repeat.
2. Scene chat. A follower IDLE INFO whose fragment starts a two-actor scene. The scene is alternating-speaker Dialogue actions, one DIAL topic per line, with head-tracking. Each named NPC has a forced-reference alias with `GetIsID`, flagged Optional and Allow Reuse, plus an alias for the follower. One scene per topic (first meeting, a hobby, an insult, an in-joke), so a pair of characters can talk about several things over time.
3. Memory. Stage fragments on the chat quest set a "talked to" tracking quest, so later lines and later scenes can branch on whom he has already met.

Guards on the line that starts a scene: other NPC `GetDisabled == 0`, `GetDistance` to the player under about 900, `WaitingForPlayer == 0`, `IsSneaking == 0`, both actors `IsInCombat == 0`, `GetHealthPercentage` near full, player not `IsTrespassing`, not riding, `GetRandomPercent <= ~10`, a time window, the scene not already played (`GetStageDone == 0`), and excluded locations.

**Other behaviours worth copying**
- Player-started topics. A chain of questions the player asks (prompt, answer, branching replies, "let's change the subject"); each finished chain sets a stage that unlocks the next, so conversations unfold in order.
- Event-driven quests (`ADIA` event with the follower as a forced alias and the other speaker "from event") for reactions to what the player just did: giving gold to a beggar, ordering a drink at an inn.
- Commenting on the player's journey: a progress quest with stage-gated lines and a main-quest commentary quest that keys on main-quest stages.
- Summon reactions, picked locks, dragon encounters (voice-type gated), horse use and being left behind.
- Control quests: fighting style, follow distance, horse sharing and a "summonable" faction.

**Builder support today:** barks via `idle`/`custom` entries with the conditions in `CONDITION_FUNCTIONS`, which now include `GetDayOfWeek`, `GetSitting`, `IsInCombat`, `IsTalking`, `IsInDialogueWithPlayer`, `IsRaining`, `IsSnowing`, `GetCurrentTime`, `GetHealthPercentage`, `IsRidingMount`, `GetDisabled`, `IsTrespassing`, `IsMoving`, `GetDead`, `IsWeaponOut`, `GetLevel` (no parameter) and `GetQuestRunning`, `GetQuestCompleted`, `GetIsRace`, `GetFactionRank`, `GetIsVoiceType`, `HasSpell`, `HasPerk`, `GetInCell`, `IsCurrentFurnitureObj`, `IsScenePlaying` (a form), `GetInSameCell` (a placed reference like `GetDistance`). An entry can set `"say_once": true` for a first-meeting line. Scene chats between two NPCs use `add_npc_chat` (see `skyrim-followers`). Not yet supported: `GetIsAliasRef`, `GetIsSex`, `GetPCMiscStat`.

## Vanilla examples
- **Follower travel comments:** `DialogueFollower` topic `HirelingIdles` (`IDLE`, 247 INFOs), gated by speaker, `CurrentFollowerFaction`, place and often voice-type lists. `HirelingCommentNextAllowed` and reset hours `0x0555` throttle it; the tool copies the reset for `idle`.
- **Guards and quest news:** `DialogueGuardsGeneral` hello `0002BDDD` (24 lines), keyed on quest progress, hold and player gear. Also `PCSH` (shouts), `WTCR` (crimes).
- **Bodies and fights:** `NOTI`, `OBCO` in `DialogueGeneric`.

## Rules of thumb
- Everyone gets the generic set; companions need more location and travel lines because they repeat quickly.
- Write place lines in the NPC's own voice and history.
- Make remarks conditional so they stay fresh.
- Use `CurrentHireling == 0` for greetings that stop while he follows, and `== 1` for companion barks.
