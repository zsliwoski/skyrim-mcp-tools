---
name: skyrim-speech-altmer
description: "Use when writing dialogue, greetings, barks, books or notes for an Altmer (High Elf) NPC: how Altmer talk, what they bring up, and what they never say. Triggers: write an Altmer, High Elf, Thalmor agent or justiciar, snobbish elf shopkeeper, Summerset wizard, race=HighElfRace."
---

# Speaking as an Altmer

Derived from sampled vanilla lines (Ancano, Elenwen, Ondolemar, Calcelmo, Endarie, Taarie, Niranye). Individual personality overrides race. Mechanics: `skyrim-dialogue`; ambient reactions: `skyrim-dialogue-context`. Check with `lint_text`.

## Voice
- Polished, precise, formal. Complete grammatical sentences, minimal contractions, no slang.
- Condescension is the default, delivered politely: "I'm afraid", "I trust", "naturally", "my dear". The insult is in the courtesy.
- Veiled threats and compliments share a sentence (Elenwen's "I trust this is the beginning of a long and mutually beneficial relationship").
- Sarcasm in service roles (Endarie: "Oh, what delight. Another charming customer...").
- Irritated impatience at interruptions ("What is it this time?").
- Calls other races "humans", "men", or "the lesser races"; uses "Mer" for elves in general.

## What they talk about
- The Aldmeri Dominion and the Thalmor: order, "saving Mer", the Great War, the White-Gold Concordat, the Talos heresy.
- Summerset Isles, ancestry, culture, art, wine and elegance.
- Magic and scholarship: Dwemer research (Calcelmo), rigorous methods, honours, the proper order of things.
- The shortcomings of Skyrim and its "barbaric" people; politics from superiority.
- Commerce and status: fashion and taste (Radiant Raiment), investments, favours.

## Variation by role
| Role | Adjust |
|------|--------|
| Thalmor official | Smooth menace, euphemism, legalism, "the Dominion requires" |
| Justiciar | Cold, doctrinaire, contemptuous, zealous |
| Scholar | Pedantic, proud of reputation, short-tempered |
| Shopkeeper | Elegant, sarcastic or snobbish, grudgingly trades |
| Fence or criminal Altmer (Niranye) | Smooth, calculating, ingratiating, cynical |

## Avoid
- Warm, folksy or slangy diction.
- Shouting. Altmer contempt is quiet.
- Making every Altmer Thalmor; many are merchants, scholars and exiles.

## Examples (original, for tone)
- "How refreshing. A local who can string a sentence together."
- "The Dominion is patient. It will outlast your little war."
- "If you must handle that, do so gently. It is older than your country."
