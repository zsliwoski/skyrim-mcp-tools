---
name: skyrim-item-placement
description: "Use when putting items into a vanilla Skyrim cell: spawn books, scrolls, weapons or loot in a shop, house or shelf, e.g. in the Riverwood Trader. Triggers: place the items in a shop, have the items spawn in X, put it on the shelf, where can I put items in this cell, place_item, place_items, find_free_spots, get_cell_contents."
---

# Placing items in a cell

1. `search_cells("Riverwood")` for the cell's editor ID (`RiverwoodRiverwoodTrader`).
2. `get_cell_contents(cell, signature="BOOK")` or `query="shelf"` lists references with positions. Pass `x`, `y`, `radius` for the nearest ones. Output is long: filter by `signature` or `query` instead of reading the whole cell.
   - Vanilla books and goods sitting on a shelf give its height: the Trader's shelf books are at z about 305 to 317, so items go at z about 305.
   - Furniture such as `CounterLeanMarker` or a chest gives a table or counter position.
3. `place_items(bases, target_plugin, cell, x, y, z, spacing=30)` lays the items on a grid of free points nearest (x, y, z), skipping existing references (including your plugin's). `find_free_spots` shows the points first. It checks clearance only, not that a surface is there, so choose z from a neighbouring item and keep the radius small enough to stay on the shelf.
4. `place_item` places one with rotation (`rotation_x=90` lays a dagger flat), `persistent=True` for items a script reaches, and full charge for enchanted weapons.

## Rules
- Pass `plugins=["Skyrim.esm", "<Mod>.esp"]` to the lookup tools so the plugin's own references count as blockers.
- `target_plugin` must already exist and contain the base records; the references go in that plugin as overrides of the vanilla cell, so two mods that place items in the same cell do not conflict, but a mod that replaces the whole cell can hide them.
- Each reference is named `<base>Ref`.
- After placing, check in game: `item_console_commands(plugin)` gives `player.moveto` lines for each item, and `coc <cell>` enters the room.
- To move items to another plugin, see `skyrim-plugin-refactoring`; placements are deleted with their base records and must be placed again.
