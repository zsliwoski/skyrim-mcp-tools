---
name: skyrim-record-cloning
description: "Use when creating a new Skyrim item (book, weapon, armor, misc item) by cloning a vanilla record, giving it custom text, stats or an enchantment: clone_record, create_record, edit_record, FULL/DESC, EITM/EAMT, non-localized plugins, subrecord order. Triggers: make a custom book, unique axe, clone a weapon, add an enchantment to a weapon, new MISC item."
---

# Cloning vanilla records

Use `clone_record(source, target_plugin, editor_id, name=, description=, signature=)`. It copies the record under a new form ID in your plugin, adds the masters it needs, drops attached scripts and writes text inline. For more than one record use `clone_records(target_plugin, items=[{source, editor_id, name, description, signature}, ...])`: one snapshot, and a failing item is reported in `errors` while the rest still run.

## Rules
- Plugin tools that write files are serialised by the server, so parallel calls are now safe; still prefer `clone_records` for batches (one snapshot, one result).
- The plugin file must already exist (create it first with `create_npc`, `create_cell` or `create_record`).
- Plugins being authored resolve by bare file name (the output folder wins over Data). Records already in `target_plugin` are visible to `edit_npc`, `edit_record`, `create_record`, `clone_record(s)` and `place_item` without listing it in `plugins`.
- The plugin must not be localized: FULL/DESC are stored as inline z-strings, not string-table IDs. A leftover 4-byte lstring (e.g. BOOK `CNAM`) would be read as text, so the tool drops it.
- `description` sets `DESC`, which is a book's readable text (HTML-like `<p align=...>` markup is allowed). A cloned book keeps the source's model; for notes, journals or longer books use `edit_book` (sets `MODL`/`MODT`/`INAM`/`OBND`/`DATA` together and formats the text) and write the text as described in `skyrim-book-writing`.
- Pass `signature` (BOOK, WEAP, MISC...) when an editor ID could match several types.
- Look up enchantments and effects with `search_forms("ENCH", ...)` / `("MGEF", ...)`. If a type returns nothing, add it to `LOAD_SIGS` in `tools/core/quest_model.py`.
- Weapon enchantment: add `EITM` (form of the ENCH) then `EAMT` (uint16 charge), both before `ETYP`. `add_plant_quest` does this for `reward_weapon.enchantment`.
- WEAP `DATA` is `<IfH` (value, weight, damage); BOOK `DATA` holds flags and value. Check with `get_record` before patching bytes with `edit_record`.
- Subrecord order matters: copy it from the vanilla record; builder code re-sorts with an explicit order list.
- A script that must fire for any instance of a cloned item (`OnContainerChanged`) attaches to the base form's `VMAD`, not to a reference.
- Editor IDs must be unique across all loaded plugins; `clone_record` refuses duplicates.
- A snapshot is taken first, so `restore_snapshot` undoes a bad clone.

## Good clone sources
- Plain circlets with no enchantment: `ClothesCirclet01`-`ClothesCirclet10` (jade/emerald, silver/moonstone, gold/ruby, copper/sapphire, copper/onyx, silver/sapphire, jade/sapphire, gold/emerald, copper/moonstone, copper/ruby). Later `Circlet of ...` records are enchanted: avoid them for unenchanted uniques.
- Rings and necklaces: `JewelryRing{Gold,Silver}{,Diamond,Emerald,Sapphire,Amethyst,Garnet,Ruby}`, `JewelryNecklace{Gold,Silver}{,Diamond,Ruby,Emerald,Sapphire,Gems}`.
- Plain readable book: `Book1CheapHorkers` (BOOK). Search `search_forms("BOOK", "Book1")` for more.
- Always `get_record` the source first; an `EITM` subrecord means it is enchanted.

## Giving a clone to an NPC or placing it
- NPC: `edit_npc(npc, {"add_inventory": [{"item": editor_id, "count": 1}]}, target_plugin)` appends; `inventory` replaces the whole list.
- Cell: `place_item(base, target_plugin, cell, x, y, z, rotation_z=, persistent=)`; find coordinates with `get_cell_contents(cell, signature="FURN")`.
