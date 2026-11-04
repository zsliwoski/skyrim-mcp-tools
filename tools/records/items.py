"""Higher-level item builders: recoloured effect shaders and a one-call unique (enchanted, placed) item."""
import re

from tools.cells.place_item import place_item
from tools.records.clone import clone_record
from tools.records.enchant import enchant_item

# EFSH DATA offsets (400 bytes): fill colour key 1 (RGBA) and the main colour (RGB + pad).
FILL_KEY_OFFSET = 16
COLOR_OFFSET = 56
SHADER_TEMPLATE = 'MagicArmorEbonyFleshFXS'


def parse_color(color):
    """'#RRGGBB' (or 'RRGGBB') to an (r, g, b) tuple."""
    m = re.fullmatch(r'#?([0-9a-fA-F]{6})', str(color).strip())
    if not m:
        raise ValueError("color must look like '#FFC832'")
    v = int(m.group(1), 16)
    return v >> 16, (v >> 8) & 0xFF, v & 0xFF


def shader_edits(color):
    """edit_record edits that recolour a cloned EFSH: the main colour and a darker fill key."""
    r, g, b = parse_color(color)
    dark = bytes(int(c * 0.7) for c in (r, g, b)) + b'\0'
    return [{'sig': 'DATA', 'offset': FILL_KEY_OFFSET, 'hex': dark.hex()},
            {'sig': 'DATA', 'offset': COLOR_OFFSET, 'hex': bytes((r, g, b, 0)).hex()}]


def create_shader(refresh, target_plugin, editor_id, color, source=SHADER_TEMPLATE):
    """Clone an effect shader (default: Ebony Flesh, a skin-tint shader) and recolour it.

    refresh() returns a fresh (builder, editor) so the clone is visible to the edit that follows."""
    builder, _ = refresh()
    res = clone_record(builder, source, target_plugin, editor_id, signature='EFSH')
    _, editor = refresh()
    editor.edit_record(editor_id, target_plugin, shader_edits(color), signature='EFSH')
    return {'shader': res['created'], 'color': '#%02X%02X%02X' % parse_color(color)}


def create_unique_item(refresh, source, target_plugin, editor_id, name, description=None, enchantment=None,
                       charge=None, replace_enchantment=False, cell=None, x=0.0, y=0.0, z=0.0,
                       rotation_x=0.0, rotation_y=0.0, rotation_z=0.0, persistent=False, ref_editor_id=None):
    """Clone an item (weapon, armor, misc...), optionally enchant it and place it in a cell, in one call.

    refresh() returns a fresh (builder, editor) after each step. Weapons placed in the world get full charge."""
    out = {}
    builder, _ = refresh()
    out['item'] = clone_record(builder, source, target_plugin, editor_id, name, description)['created']
    if enchantment:
        _, editor = refresh()
        out['enchanted'] = enchant_item(editor, editor_id, target_plugin, enchantment, charge,
                                        replace_enchantment)['changes']
    if cell:
        builder, _ = refresh()
        out['placed'] = place_item(builder, editor_id, target_plugin, cell, x, y, z, rotation_z, None, persistent,
                                   ref_editor_id or f"{editor_id}Ref", rotation_x, rotation_y)
    return out
