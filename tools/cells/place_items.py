"""Place several items on free floor spots near a point, instead of guessing coordinates one by one."""
from tools.cells.cell_contents import find_free_spots
from tools.cells.place_item import place_item


def place_items(refresh, bases, target_plugin, cell, x, y, z, spacing=30.0, clearance=25.0, radius=300.0,
                rotation_z=0.0, ref_suffix='Ref'):
    """Place each base (editor ID of an item in the plugin or a vanilla one) on the nearest free spots to (x, y, z).

    refresh() returns a fresh (builder, editor) so references written so far count as blockers. Each reference
    gets the editor ID <base><ref_suffix>. Enchanted weapons spawn fully charged."""
    builder, _ = refresh()
    spots = find_free_spots(builder, cell, x, y, z, len(bases), spacing, clearance, radius)['spots']
    placed = []
    for base, spot in zip(bases, spots):
        builder, _ = refresh()
        place_item(builder, base, target_plugin, cell, spot['x'], spot['y'], spot['z'], rotation_z, None, False,
                   f"{base}{ref_suffix}")
        placed.append({'base': base, 'ref': f"{base}{ref_suffix}", **spot})
    return {'cell': cell, 'placed': placed}
