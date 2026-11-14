---
name: skyrim-group-banter
description: "Use when giving a group of custom NPCs (a guild, meadhall, camp, shop staff, town) ambient social dialogue: opinions about each other, camaraderie, small grudges and intrigue, and 'Heard any rumors?' topics with several possible answers. Triggers: NPCs talk about each other, add rumors, add flavour dialogue to a guild, make the hall feel alive, what do you think of X, gossip, banter."
---

# Group banter, opinions and rumors

A group feels real when its members have views on each other and the place has small things going on. This is flavour, not plot: a player who asks a few questions should come away wanting to hear more, never feel buried. Pair with `skyrim-dialogue` (mechanics), `skyrim-lore-writing` (oaths, calendar, units) and `skyrim-npc-loot-and-flavor` (books and items).

## Plan the group first
Before writing lines, fill in a small web and keep it consistent across every NPC:
| Member | Role | Likes | Needles | Secret / worry |
|---|---|---|---|---|
| Bera | owner | Mirielle (reliable) | Calenhir (late with orders) | the guild is behind on rent |

- Every opinion is seen from **both** sides, and the two views may disagree in attitude (Bera calls Calenhir "slow but careful"; Calenhir calls himself "thorough" and Bera "a tyrant about dates") but not in facts.
- Give each NPC 1 to 3 views, not one on everybody. Mix warmth (respect, shared meals, old jokes), friction (habits, credit, money) and one or two hints of intrigue (a missing ledger page, someone who leaves at night, a customer nobody trusts).
- Keep it light: nobody is a villain, and intrigue stays a hint with no resolution so it works without a quest. If a hint may lead somewhere later, note it in the plan but write it to stand alone.
- Reuse one or two running threads across the group (a gossip, a feud over a stolen recipe) so overheard stories connect, and let NPCs disagree about the details.

## Topic types
| Prompt style | Purpose | Lines |
|---|---|---|
| `Heard any rumors around here?` | Small ambient talk; the workhorse | 1 to 3 short parts |
| `What do you make of <Name>?` | One NPC's view of a colleague | 2 to 3 sentences |
| `How's business?` / `How's the hall?` | Place and mood | 1 to 2 sentences |
| `Anything I should know about <place/trade>?` | Mild world detail or hook | 2 to 3 sentences |

- Prompts are the player's voice and short (under about 40 characters). Use an NPC's name in a prompt only if the player would plausibly know it; otherwise ask by role ("What about the master cutter?").
- A line says one thing: the opinion, a concrete detail, then stop. No speeches, no moral, no punchline every time.
- Voice per NPC: race, trade and temperament change how they gossip (blunt Orc, fussy Altmer, cheerful Breton). Do not give everyone the same cadence.
- Rumors may be unrelated to the group (a wolf near the road, the price of salt, the Jarl's steward) or lightly related. Most are about the world, some about the group, at most one in a few hints at a bigger event.
- Oaths and gods sparingly, as in `skyrim-dialogue`. Dates and money per `skyrim-lore-writing`.
- Talk about NPCs and events; never state what the player did or knows.

## How many topics
- Per NPC: one rumors topic plus 1 to 3 opinion topics. A group of 6 means about 15 to 25 topics, not 100.
- All members can share the same rumors prompt, each with its own answer (every topic is conditioned on its own NPC).
- Greeting or goodbye lines may carry a one-line mood note, but rarely.

## Multiple answers from one NPC
An entry takes either `lines` (spoken in sequence as **one** reply) or `answers`: a list of alternative replies, each a list of lines, written as several responses under **one** topic. `pick` is `"random"` (default, any answer each time, like vanilla rumors) or `"in_order"` (each answer once in order, then the last repeats, good for a story that unfolds; make the last answer a good fallback). `answers` can be combined with `give_items`/`code`; they run after whichever answer plays.
1. **Rumors:** one `Heard any rumors around here?` entry with 3 to 5 `answers` per NPC, each 1 to 2 sentences. A long answer can use several lines (spoken in sequence).
2. **Opinions** about a colleague: 2 or 3 `answers` at most, or a single `lines` reply.
3. Never repeat an identical prompt for the same NPC in separate entries; use `answers` instead.
4. Conditions apply to the whole topic, not per answer. For answers that unlock later (after a quest stage), use a second topic with a different prompt and a `GetStage` condition.

## Writing it with the tools
```
edit_npc("GCG_Bera", {"quest_editor_id": "GCG_Banter", "dialogue": [
  {"kind": "topic", "prompt": "Heard any rumors around here?", "pick": "random", "answers": [
     ["Somebody keeps leaving the back door unbarred. Calenhir sleeps lightly for a man who claims he heard nothing."],
     ["The Jarl's steward asked about a ruby. A big one. Pay it no mind."],
     ["Wolves took a cart horse on the road to Rorikstead.", "Salt's up two septims a pound because of it."]]},
  {"kind": "topic", "prompt": "What do you make of Mirielle?",
   "lines": ["Best hands in the shop and she knows it. Never late, never loud. I only wish she'd take a day off."]}
]}, "MyGuild.esp")
```
`add_npc_dialogue(draft_id, prompt=..., answers=[[...],[...]], pick="random")` does the same for drafts.
Reuse one banter `quest_editor_id` so every topic lands in one quest. Check ids and existing topics first (`search_npcs`, `get_npc_dialogue`).

## Review checklist
- Every opinion has a counterpart that agrees on facts and may differ on attitude.
- No two NPCs open the same way; only a couple of lines in the whole group use an oath or god.
- `lint_text` clean (no modern words, valid dates and units).
- Each NPC's `get_npc_dialogue` reads like a person: a few topics, varied length, nothing repeated.
- Then `validate_plugin`, `package_plugin`, `install_plugin`; if topics do not show, see `skyrim-quest-debugging`.
