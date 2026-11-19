---
name: skyrim-speech-nord
description: "Use when writing dialogue, greetings, barks, books or notes for a Nord NPC: how Nords talk, what they bring up, and what they never say. Triggers: write a Nord, Nord guard, Nord innkeeper, jarl or housecarl lines, Companions or Stormcloak voice, make this NPC sound Skyrimish, race=NordRace."
---

# Speaking as a Nord

Derived from sampled vanilla lines (Ysolda, Balgruuf, Ulfric, Gerdur, Hadring, Aela, Brynjolf, Hulda, Hrongar). Race is a baseline; the NPC's job and temperament override it. Authoring mechanics are in `skyrim-dialogue`; ambient reactions in `skyrim-dialogue-context`. Check text with `lint_text`.

## Voice
- Plain, blunt, concrete. Short sentences, active verbs, few abstractions. A Nord says what they mean and stops.
- Commoners use contractions and dropped endings ("ain't", "nosin'", "ya", "holler"). Jarls, stewards and priests drop the slang but keep the bluntness.
- Humour is dry and teasing; pride is loud; insults are direct ("fool", "old fool").
- Warmth shows as hospitality and practical help: food, a bed, a job at the mill, "speak to Hod".
- Addresses: "friend", "traveler", "lass", "lad", "outlander" (cool), "elf" (often a slur), "Dragonborn" only once known. Equals get first names.
- Oaths and sayings: Talos, Ysgramor, Shor, Sovngarde, "Skyrim belongs to the Nords", "my word is my bond".

## What they talk about
- The civil war and which side the speaker is on; the ban on Talos worship; the Thalmor as unwelcome meddlers.
- Their hold, their jarl, and local rivalry (Whiterun v Windhelm, Solitude v Riften).
- Work and land: mills, farms, mines, hunting, smithing, mead and ale, the inn.
- Honour, battle, a good death, Sovngarde, Ysgramor and the Companions, the Greybeards, dragons.
- Cold, hard winters, wolves, bears, bandits; "we're a small village, we take care of our own".
- Suspicion of strangers, mages and outsiders; special distrust of Dunmer and Altmer, grudging respect for those who prove themselves.

## Variation by role
| Role | Adjust |
|------|--------|
| Farmer, labourer, innkeeper | Slang, wry gossip, complaints about taxes, drink and the war |
| Guard | Clipped, bored or suspicious, weary understatement |
| Warrior, Companion | Boastful, bonded, "shield-brother/sister", challenges and jokes |
| Jarl, steward, housecarl | Formal but forthright; politics, loyalty, "the hold", duty |
| Stormcloak | Freedom, "true Nord", Talos, anger at the Empire |
| Imperial-leaning Nord | Order, trade, "the Empire built this country" |
| Bard, scholar | Longer, rhythmic, saga-like phrasing |

## Avoid
- Flowery or abstract diction ("indeed, one must consider..."): that is an Altmer or Breton register.
- Elvish, Khajiiti or Orcish exclamations.
- Fantasy "thee/thou" and "milord"; use "my Jarl", "my lady", "sir".
- Sounding eager to please a stranger without a reason.

## Examples (original, for tone)
- "Cold's got teeth tonight. Sit by the fire, there's stew if you've coin."
- "Ulfric or the Emperor, the barley still needs cutting."
- "You've the look of someone who's seen a fight. Good. We could use one more who has."
