---
name: skyrim-lore-research
description: Use before writing books, dialogue or quest text that depends on Elder Scrolls facts (people, places, factions, history, religion). Triggers: what is X in lore, who is, lore check, lore_search, lore_get, lore database, build_lore_db.
---

# Lore research

1. `lore_status` shows whether the local lore database exists. If it errors, tell the user how to build it
   (README, "Lore database"): `python -m tools.lore build game-books` is quick; `uesp-dump` with a UESP XML export
   gives the wiki. Do not try to fetch wiki pages yourself.
2. `lore_search` with proper nouns first (names rank best), then `lore_get` on the best page title for detail.
   For concept questions use `mode='semantic'` (needs the optional `build vectors` step; `lore_status` shows
   whether embeddings exist). `source='game'` limits to in-game books, which outrank wiki text when they disagree.
3. Cite the page title or book name when reporting a fact, and say when the database has nothing.
4. Write original text. UESP text is CC BY-SA and game text is Bethesda's, so use them as reference only.
   Then run `lint_text` (see `skyrim-lore-writing`).
