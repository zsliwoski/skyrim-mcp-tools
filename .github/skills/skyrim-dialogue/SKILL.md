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
- `list_texts(plugin, quest=..., sig="NAM1", offset=...)` lists a quest's replies with their INFO IDs, topic and prompt; `audit_dialogue_length` flags prompts over 1 sentence and replies over 3.
- `edit_texts` items may use `topic` (+`response` index, `sig` FULL for the prompt or NAM1 for a reply) or `match` (every record with that exact text) instead of `record`.
- `get_npc_dialogue(npc)` (or `identifier`) lists the topics and lines an NPC can say, with the quest and conditions.
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

Cloning and factions: vanilla hangs many topics on factions (e.g. Sven can be asked about the Bards College, and so can every member of that faction). A clone inherits its source's factions, so it can also inherit unwanted topics. After creating a clone, check `get_npc` and drop or replace factions you don't want (`factions` replaces the whole list), and talk to the NPC in game (or `get_npc_dialogue`) to catch stray topics.

### Keep prompts and replies short
- A player prompt is at most one sentence.
- An NPC response is at most three sentences. If more needs saying, split it across follow-up topics (nested `child_of` questions) instead of one long reply.
- This applies to every line the builders write: offers, stance replies, reminders, turn-ins and barks.

### Keep the top-level menu short: nest related prompts
Do not put every prompt in the main conversation list. When three or more prompts belong together, make one parent topic and hang the rest off it with `child_of`, so the player sees one entry instead of many:
- **Quest info:** one entry per quest, e.g. "About the sweet roll thief...", with the questions under it ("Where do I find this giant?"). Give the parent and its children the same stage conditions. Quest actions (ask for the job, turn it in, "any update") can stay top-level.
- **Follower options:** one parent per theme, as `add_follower_controls` does for follow distance and fighting style ("Let's talk about how you follow me.", "...how you fight."). Put the horse topics under "About your horse..." (hired condition on the parent, each child keeps its own rank condition).
- **Always add a "Never mind." child** (condition-matched to the parent) so the player can back out.
- A parent's reply should be a short invitation ("What would you like to know?"). Children are offered only after it, not in the main list.
- **Closing vs returning:**
  - Settings and action children use `goodbye: true`, like vanilla, since the player is done.
  - Info branches should let the player ask several questions. Each question's INFO links back to its siblings and to "Never mind." (TCLT entries on the INFO), and "Never mind." has no `goodbye` and no links, which returns the player to the main conversation options.
- In the builder, `child_of` links children from the parent. Set `"loop": true` on the parent so every child also links to its siblings (the player can ask several questions), and `"ends_branch": true` on the "Never mind." child so it has no links and returns to the main options. Add `"goodbye": true` instead if Never mind should end the conversation. Branches that already exist from earlier calls need raw INFO `TCLT` edits.
- Children are not shown in the top-level menu and need no `DLBR` of their own; they take the parent's branch in `BNAM`. When moving an existing topic under a parent, repoint its `BNAM` at the parent's `DLBR` and delete its old `DLBR`.

### Roleplay-friendly quest replies
When an NPC explains their predicament or goal (the quest pitch), don't offer only "I'll help" / "Not interested". Give the player replies that let them play different characters, each with its own in-voice prompt and an NPC response that reacts to that stance, before the quest is accepted. Offer three or four archetypes that suit the NPC, but offer varied player expression in the prompts. Example for a pirate hunting treasure:
- **Innocent:** good-natured, hopes he will mend his ways.
- **Kindred Spirit:** a fellow rogue, in for the adventure.
- **Rival:** a skeptic who will challenge him every step of the way.
- **Wicked:** amoral, serves their own ends.

Adapt the archetypes to the NPC and quest (a healer's plea might offer the Compassionate, the Pragmatist, the Mercenary and the Skeptic). These names are for the author only: never put the archetype label in a player prompt or an NPC line. Write each prompt as the in-world words that character would say, so the choice reads as roleplay, not a menu of labels.

Lean into each archetype. Prompts that are all polite variations of "tell me more" make the stances feel the same, so give each its own attitude, stakes and edge, and have the NPC's answer react to it. For the healer's plea:
- **Compassionate:** "If people are dying, tell me where to find this tome." (urgent, moved by the suffering; she is relieved)
- **Pragmatist:** "What does Belethor want for it, and what if he says no?" (practical, about logistics and risk; she gives straight answers)
- **Mercenary:** "I don't run errands for blessings, so name a price." (open about wanting pay; she haggles or bristles)
- **Skeptic:** "A fever that conveniently needs a stranger's help sounds like a scheme." (suspicious, challenges her; she is stung and has to justify herself)

The stance replies are not the accept button. They are things the player can say on the way to a decision, and the pitch offers only them. The NPC answers each one, and only after that answer are the plain accept ("I'll fetch the tome.") and refuse ("I can't help you right now.") prompts offered; accepting alone starts the quest. After a refusal, the revisit line ("Have you given it more thought?") offers accept and refuse straight away, without the stance replies. Player prompts should generally be one sentence: put any extra attitude in the NPC's reply, not in a second prompt sentence. Nest them as sibling choices (see the nesting rules above). Where later lines allow it, let the NPC refer back to the stance chosen.

The player must also be able to refuse, and a refusal is not final. Refusing sets a hidden quest stage (5, no journal entry). The pitch topic is then replaced by a second one, conditioned on that stage, with a short, to-the-point prompt ("About that tome...") instead of repeating the opener. Its NPC line is the revisit ("Have you given it more thought?"), followed straight away by accept and refuse. This is the standard flow for a fetch-quest errand and `add_fetch_quest` builds it (see `skyrim-quest-authoring`); for other quest shapes build it the same way with `GetStage` conditions and a `SetStage` on refusal.

Rules the tools follow:

- Re-using `quest_editor_id` adds topics to the existing quest instead of making a new one.
- Scripts are compiled with the Creation Kit compiler (`SKYRIM_CK_DIR`, default the `Skyrim` Steam folder) into `Scripts` beside the plugin; `package_plugin` includes them.
- Names, prompts and lines are inline text, so a localized plugin (STRINGS files) is refused.
- Every topic is conditioned on that NPC only. There is no voice audio, so lines show as subtitles with no lip sync.
- Item ids need an exact editor ID when a name is ambiguous. Use `search_forms("ALCH", "sweetroll")` first (sweet roll is `FoodSweetroll`, an `ALCH`).

### Optional checks: speech, intimidation, brawl, gold
Some NPCs may make the player earn what they want, based on personality. This is optional: most NPCs (and most quests) should have none, and a plain accept/refuse is right for a kind or neutral NPC. Reserve checks for characters whose nature calls for it: a proud merchant or guard captain (speech), a bully or hardened warrior (intimidation or a brawl), a greedy or mercenary type (gold). Offer one or two per NPC as extra prompts beside the normal ones, and never make a quest depend on a check the player cannot retry or buy past: a failed check returns to the menu and the player can try again (a failed intimidation can start a fight, which is the point).

Add `"challenge"` to a player topic (in `create_npc`/`edit_npc` dialogue, or to the accepting reply of `add_fetch_quest`: `offer.challenge`, or `offer.accept.challenge` with roleplay options). The topic's own `lines`, `code` and `give_items` run on **success**; `challenge.failure.lines` is what the NPC says otherwise. The prompt gets the vanilla tag appended, so write it without one.
- `{"type": "persuade", "difficulty": "hard", "failure": {...}}`: passes when the player's Speechcraft is at least the vanilla global (`very_easy`, `easy`, `average`, `hard`, `very_hard`; default average). `"luck": 10` adds a 10% chance a too-low skill still passes. Prompt shows `(Persuade)`. Success also credits Speechcraft XP through the vanilla favor script.
- `{"type": "intimidate", "brawl_on_failure": true, "failure": {...}}`: passes on the engine's intimidation roll (`GetIntimidateSuccess`). With `brawl_on_failure` a failure ends the conversation and starts a brawl. Prompt shows `(Intimidate)`.
- `{"type": "skill", "skill": "Lockpicking", "rank": 50, "luck": 10, "failure": {...}}`: passes when the player's level in any skill (`OneHanded`, `TwoHanded`, `Archery`, `Block`, `Smithing`, `HeavyArmor`, `LightArmor`, `Pickpocket`, `Lockpicking`, `Sneak`, `Alchemy`, `Speech`, `Alteration`, `Conjuration`, `Destruction`, `Illusion`, `Restoration`, `Enchanting`) is at least `rank`; `luck` is optional as for persuade. Prompt shows `(Lockpicking 50)`. Use it for an NPC that tests a craft ("Show me you can pick this lock"), not as a second speech check.
- `{"type": "price", "amount": 50, "failure": {...}}` (also `gold`, `bribe`): needs that much gold, takes it and gives it to the NPC. Prompt shows `(50 gold)`.
- `{"type": "brawl", "amount": 100, "failure": {...}, "victory": {"prompt": "...", "lines": ["..."]}}`: a fist fight, optionally for a stake. With `amount` the player must carry the gold (taken when the fight starts) and `failure` says "come back when you have it"; without it no `failure` is needed. Prompt shows `(Brawl - 100 gold)`. The lines are the NPC's last words before it starts and the conversation ends. When the player wins, the NPC yields and the player gets one reply to pick (`victory.prompt`, one sentence) that the NPC answers with `victory.lines`; a stake is paid back doubled. It is built automatically (defaults are generic, so write your own in the NPC's voice). It must exist: vanilla's victory reply (`DGIntimidateVictoryYes`) only answers the NPCs it names, and without it the fight never finishes and the NPC stays down and silent. A fight only starts for an NPC the vanilla brawl quest accepts: its voice type must be in the `VoicesBrawling` list (most commoner, nord, soldier, brute and orc voices are; drunk is not), and it must not be an Invulnerable or Unaggressive clone source (Mikael is both, so cloning him gives an NPC who never fights back). Clone a plain Aggressive NPC (Uthgerd, Torvar) and set `voice_type` if needed.

How vanilla builds them (see `DA07` Jorgen, `Favor017` Uthgerd, `DA11`): a normal topic whose prompt carries the tag, with two INFOs tried in order. The first is conditioned on the check, and its TIF script calls the vanilla `DialogueFavorGeneric` (`FavorDialogueScript`): `Persuade`, `Intimidate` or `Brawl(akSpeaker, None)`. The second has no check and is the failure line. Brawl for gold (Uthgerd) is the first INFO requiring `GetItemCount(Gold001) >= 100`, removing the gold and starting the fight. To build anything else by hand use the dialogue `conditions` extras: `"value_global": "SpeechHard"` (compare to a global, with `GetActorValue` and `"actor_value": 17` for Speechcraft), `"or": true`, and the no-parameter `GetIntimidateSuccess`, `GetBribeSuccess`, `IsIntimidatedbyPlayer`, `IsBribedbyPlayer`.

The intimidation roll is the engine's: it weighs the player's Speechcraft and level against the NPC's level and confidence (a Brave, low-level-range clone fails often for a low-Speech player; the exact formula is not in our data). Persuade is separate and compares Speechcraft to the difficulty global.

Building several challenge NPCs into one plugin: pass `fields` (not `spec`) to `create_npc`; the first call uses `overwrite=true`, later NPCs and `add_fetch_quest` pass `target_plugin` so they append. Delete stale `TIF__<Plugin>_*.pex` from the output `Scripts` folder before a full rebuild, and run `package_plugin` (it regenerates the SEQ that `validate_plugin` flags as stale). `packages: []` clears a clone's inherited AI packages (Torvar's Companions routine). Untested in game unless noted: victory replies and the Gorm-style failed-intimidation brawl.

An NPC built with the vanilla follower setup (`follower`, which reuses the vanilla follower quest's hire, wait, follow and dismiss topics) should not also get our custom speech options (persuade, intimidate, brawl, gold challenges): the hire price is already their gold gate, and extra checks clash with the vanilla follower dialogue (see `skyrim-followers`).

Not every NPC needs these, and the same NPC should not offer all four. Hireling prices (`skyrim-followers`) are the same gold gate. Failures should be in character and short, and one sentence for the prompt still applies.

## Engine-spoken lines (barks)

Besides menu topics, the engine speaks lines by itself when something happens. Vanilla picks them by voice type, so a custom voice type (e.g. `MyVoice`) hears none of them unless the NPC has its own. Add them with a `kind` and no `prompt`; each entry may use `answers` for several random variants. Lines are subtitles only.

| Option | Kinds | When |
|--------|-------|------|
| `combat_lines: true` | `attack`, `power_attack`, `bash`, `block`, `hit`, `taunt`, `flee`, `bleedout`, `death`, `normal_to_combat`, `normal_to_alert`, `alert_to_combat`, `combat_to_normal` | Fighting, grunts and jeers |
| `ambient_lines: true` | `notice_corpse`, `observe_combat`, `shout_reaction` | Any NPC: finding a dead body, watching a fight nearby, hearing the player shout |
| `travel_lines: true` (implied by `follower`) | `idle` conditioned on `CurrentHireling` | A hired companion on the road and in places |
| `weather_lines` / `time_lines` / `main_quest_lines` / `place_lines: true` | `idle` conditioned on `CurrentHireling` plus the situation | Context commentary, see `skyrim-dialogue-context` |
| `follower` | `agree`, `refuse`, `show` | Favor reactions, see `skyrim-followers` |

Every NPC should get `combat_lines` and `ambient_lines`; hirelings get the travel set too. Fixed rule: the more time the player spends with an NPC, the more location and travel lines he needs, because he will repeat himself quickly. A shopkeeper can keep the generic set; a companion should have many `idle` entries and a distinct set per location.

### Immersion layers
Ask the user (or infer from the NPC's role) how much reaction depth the NPC should have, and build the layers up to that level. Each layer includes the ones before it.

| Layer | Contents | Tools |
|-------|----------|-------|
| 1. Base (a vanilla townsperson) | Pain grunts and combat sayings (`combat_lines`), a few passive `idle` sayings, reactions to a body and to shouts (`ambient_lines`), one or two generic conditions such as weather or time of day | `edit_npc` bark flags; `weather_lines` / `time_lines` for hirelings |
| 2. Aware of the player | Comments on the player's gear, accomplishments, guild or faction standing, quest progress and reputation | `greeting`/`idle` entries with conditions (`GetStage`, `GetQuestCompleted`, `GetFactionRank`, `GetEquipped`, `GetGlobalValue`); `main_quest_lines` |
| 3. Aware of the world (mostly followers) | Lines for each kind of place the player visits and each hold or named location | `place_lines`, `travel_lines`, `LocationHasKeyword` / `GetInCurrentLoc` entries; `commentary_text` to replace presets |
| 4. Social | Scenes with other NPCs where this NPC drives the conversation, plus opinions of neighbours | `add_npc_chat`; see `skyrim-group-banter` |
| 5. Full coverage | A wide suite of reactions across many situations (weather, time, combat events, summons, being left behind, quest stages, places, other NPCs), with several variants per situation | all of the above plus `add_follower_controls` options such as `left_behind` and `summon`, see `skyrim-followers` |

Make the content fit the NPC. Before writing, check where the NPC lives and works (`get_npc`, `get_cell_contents`, the home location's keywords) and what they would plausibly meet: a farmer remarks on the weather, crops and bandits, not on dragon lairs or the Thieves Guild; a guard talks about crime, the jarl and the war; a hireling who travels needs places across the whole map. Skip presets that make no sense for the NPC (no `place_lines` for someone who never leaves a shop), keep voice and vocabulary consistent with their background, and avoid spoilers for quests the NPC could not know about.

### Player pronouns and gendered words
Never avoid writing a line because it refers to the player as "he", "she", "sir", "madam", "lad", "lass", "brother", "sister" and so on. But whenever a line (or a prompt or reply) does use a gendered word for the player, write its counterpart as well and gate the pair on the player's sex, so each player hears the matching version:
- Give each version the same other conditions, one with `{"function": "GetIsSex", "sex": "male", "op": "==", "value": 1, "run_on": "target"}` and one with `"sex": "female"`. `run_on` must be `target` (the player); on the subject it would test the NPC.
- Use `answers` only for variants that are all valid for the same player; a gendered variant is a separate entry with its own `GetIsSex` condition.
- Keep the two versions equivalent in tone and length. Lines that need no gendered word can stay unconditioned; prefer neutral wording when it reads naturally ("friend", "traveller").
- Scene chat lines (`add_npc_chat`, including the partner's lines) follow the same rule: give a line `player_text: {male, female}` instead of `text` and it is built as two INFOs gated on the player's sex.
- Check this before finishing: list the NPC's dialogue (`get_npc_dialogue`), scan for gendered words about the player, and add any missing counterpart. Words about the NPC or a third party do not need a pair.

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

## Plain speech
Skyrim is not refined. Unless the NPC is an Altmer, a noble or a scholar, write the way the rest of the town talks: contractions ("I'm", "don't", "it's"), short plain words, and no archaic or formal phrasing.
- Avoid: "Splendid", "at your service", "do come back", "I shall", "I am afraid", "lack", "savour", "stint", "possessive of". Prefer "Good", "Come back", "I'll", "I'm afraid", "need", "take it slow", "skimp".
- Keep one or two characterful phrases per NPC (a cook's "discerning nose", a Nord's "cold as a Nord's welcome"), not a whole formal register.
- Only Altmer, and the odd Thalmor or court wizard, should sound formal; see `skyrim-speech-altmer` and the other race skills.

## Voice and religion
- Don't make every NPC invoke the gods. Most Skyrim speech has no oath at all, and a trade or hobby topic (gem cutting, smithing, farming) is not a religious context.
- Per NPC, at most an occasional oath or one passing mention. Across a cast, most lines and most NPCs should have none, so oaths mean something when used. Never open or close lines with one by habit.
- Give each NPC a different interest instead: craft detail, history and provenance, local rumours, rivalries, money, family, grudges.
- For topics with a spiritual side (such as gems and stones), lean on the setting's own magic: standing stones, soul gems, Dwemer and Ayleid ruins, Aedric and Daedric lore, mages and alchemists. Show curiosity, not sermons.
- Keep the oath rules in `skyrim-lore-writing` for the lines that do have one.
- For the speaking style, vocabulary and typical topics of an NPC's race, use the race skill: `skyrim-speech-nord`, `-imperial`, `-breton`, `-redguard`, `-altmer`, `-bosmer`, `-dunmer`, `-orsimer`, `-khajiit`, `-argonian`. The oaths listed there are options, not defaults; the rule above on oaths still applies.
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
3. If it works after `startquest`, the quest is not starting by itself. A "Start Game Enabled" quest needs a correct `.seq` file (full form IDs with the master byte, as xEdit writes) for its dialogue to work on a new game; also try a new save.
4. Greeting plays but the topic menu is empty: player topics (`CUST`) need a top-level dialogue branch. Each topic needs a `DLBR` (`QNAM` quest, `TNAM` 0, `DNAM` 1 = top-level, `SNAM` the topic) and a `BNAM` on the DIAL pointing back at it. The builder now writes these. Otherwise compare the DIAL and INFO subrecords with a vanilla topic using `get_record`.
5. A cloned NPC inherits the original's packages and factions, which can change who can talk to it. Check `get_npc`.
6. Script not firing: confirm the `.pex` is in the mod's `Scripts` folder and the script name matches the INFO `VMAD`.

## Known gaps
No per-answer conditions, no voice files, no scenes, no quest stages from dialogue, and no `.seq` generation. A challenge (see "Optional checks") only gates a topic; it does not drive quest stages beyond the accepting reply of `add_fetch_quest`. State these rather than guessing when asked to do them.
