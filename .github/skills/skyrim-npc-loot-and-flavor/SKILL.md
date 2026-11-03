---
name: skyrim-npc-loot-and-flavor
description: "Use when adding flavour to a custom Skyrim NPC group or location: a themed book and a unique item per NPC, personal effects, items on shelves and tables in a cell. Triggers: add custom books and items to each NPC, unique jewelry or circlets without enchantments, dress the cell, give every NPC a book, flavour items for a guild."
---

# Per-NPC books, unique items and cell dressing

Recipe for giving each NPC of a custom group (guild, shop, camp) their own book and unique item, then dressing the cell. Tools: `search_npcs`, `get_npc`, `clone_records`, `edit_npc`, `get_cell_contents`, `place_item`, `validate_plugin`. See `skyrim-record-cloning` for source records.

## Steps
1. **List the NPCs and cell.** `search_npcs("<prefix>", plugins=["Skyrim.esm", "<Plugin>.esp"])`, `search_cells`, then `get_npc` for race, faction rank and job so the flavour fits (an Orc smith gets a heavy band, a Bosmer a leaf circlet).
2. **Plan one table** of NPC, item source, new name, book title, tone. Keep names personal ("Stonehand's Onyx Circlet"), not generic.
3. **Clone everything in one call.** `clone_records(plugin, items)` with all books (BOOK, `description` = text) and all unique items (ARMO, `name` only). Prefix editor IDs per plugin: `GCG_Book_<Npc><Topic>`, `GCG_Jewel_<Npc><Kind>`. Check `errors` in the result.
4. **Give them out.** One `edit_npc(npc, {"add_inventory": [...]}, plugin)` per NPC. Use `add_inventory`, not `inventory`, so existing items are kept.
5. **Dress the cell.** `get_cell_contents(cell, signature="FURN", plugins=["Skyrim.esm", "<Plugin>.esp"])` (also CONT, STAT) to find tables and shelves; `place_item` a few books or items with a small z offset above the surface. Check again with `get_cell_contents(cell, query="<editor id prefix>")`.
6. **Validate and install.** `validate_plugin`, then `package_plugin` followed by `install_plugin` (always both, package first, so the zip and Data copy stay in sync). A "Data differs from the output copy" warning means the install step is still pending.

## Writing the books
- Follow `skyrim-book-writing`: real prose, not poetry or two-line sayings. Match length and in-world model to the kind of text (a 60-word letter is a `Note0x`, a 150-400 word diary or ledger is a `JournalLowPoly0x`, a longer treatise is a `BasicBook0x`), in the owner's voice, with lore-appropriate terms (see `skyrim-lore-writing`); use `edit_book` per book (or `edit_texts` for batches) and run `lint_text`.
- `description` supports `<p align='left'>` paragraphs; no real-world references.
- Make a few books mention each other's NPCs or the guild's rules so the set feels connected.

## Unique items without enchantments
- Clone plain circlets, rings or necklaces and only change `name`; keep the vanilla value and keywords so merchants and the jewelry slot behave normally.
- Do not clone `Circlet of ...` or other records with `EITM`.
- Scripts are dropped on clone, which is what you want for pure flavour items.

## Pitfalls
- Writing tools are serialised by the server, but a batch tool (`clone_records`) is still faster and takes one snapshot.
- `get_cell_contents` with only the plugin (no Skyrim.esm) returns nothing: base records cannot be resolved.
- Items placed in an exterior cell default to non-persistent references; use `persistent=True` only when a quest or script needs the reference.
