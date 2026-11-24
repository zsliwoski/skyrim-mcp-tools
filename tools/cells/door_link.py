"""Link two cells with a pair of load doors (teleport doors), so a custom interior is reachable without the console."""
import math
import re
import struct

from esplib import Plugin

from tools.cells.place_item import place_item
from tools.core.plugin_edit import resolve_target_path
from tools.records.maintenance import all_records

# Vanilla door base records by look. A preset is just an editor ID; any DOOR editor ID or form ID also works.
DOOR_PRESETS = {
    'house': 'ImpDoorSingleLoad01',
    'house_interior': 'SHouseIntDoor01',
    'shack': 'WRShackDoor01',
    'farmhouse': 'FarmhouseDoor01',
    'nordic': 'NorWoodDoorLoad01',
    'nordic_iron': 'NorDoorSmLoad01',
    'dwemer': 'DweDoorSmall01Load',
    'mine': 'MineDoor01Load',
    'cave': 'CaveDoorLoad01',
    'orc': 'OrcDoorLoad01',
    'passage': 'AutoLoadDoor01',
}
# Vanilla pairs put the arrival point about this far behind the destination door, facing away from it.
ARRIVAL_DISTANCE = 80.0


def arrival_point(door, distance=ARRIVAL_DISTANCE):
    """(x, y, z, rotation_z in radians) where a player appears after coming through a door placed at `door`."""
    rz = math.radians(door.get('rotation_z', 0.0))
    return (door['x'] - distance * math.sin(rz), door['y'] - distance * math.cos(rz), door['z'], rz + math.pi)


def xtel(dest_ref, arrive, flags=0):
    x, y, z, rz = arrive
    return struct.pack('<I3f3fI', dest_ref, x, y, z, 0.0, 0.0, rz, flags)


def _end(spec, label):
    for key in ('x', 'y', 'z'):
        if spec.get(key) is None:
            raise ValueError(f"{label} needs x, y and z (use get_cell_contents or find_free_spots to pick them)")
    if not spec.get('cell') and not spec.get('worldspace'):
        raise ValueError(f"{label} needs a cell, or worldspace with grid_x and grid_y for an exterior cell")
    return spec


def link_cells(refresh, target_plugin, prefix, a, b, door='house', door_b=None, distance=ARRIVAL_DISTANCE):
    """Place a load door in each of two cells and point each at the other.

    a and b: {"cell": name or editor ID (or worldspace, grid_x, grid_y for an exterior), "x", "y", "z",
    "rotation_z" (degrees; the door faces along it)}. The doors are <prefix>DoorA and <prefix>DoorB. `door` and
    `door_b` are a preset name (see DOOR_PRESETS) or any DOOR editor ID; door_b defaults to `door`.
    """
    if not re.fullmatch(r'[A-Za-z0-9_]+', prefix or ''):
        raise ValueError("prefix may contain only letters, digits and underscores")
    ends = [_end(dict(a), 'a'), _end(dict(b), 'b')]
    bases = [DOOR_PRESETS.get(d, d) for d in (door, door_b or door)]
    ids = [f"{prefix}DoorA", f"{prefix}DoorB"]
    for end, base, edid in zip(ends, bases, ids):
        builder, _ = refresh()
        place_item(builder, base, target_plugin, end.get('cell'), end['x'], end['y'], end['z'],
                   end.get('rotation_z', 0.0), None, False, edid, worldspace=end.get('worldspace'),
                   grid_x=end.get('grid_x'), grid_y=end.get('grid_y'), kinds=['DOOR'])
    builder, _ = refresh()
    path = resolve_target_path(target_plugin, builder.output_dir, builder.data_dir)
    plugin = Plugin.load(str(path))
    refs = {r.editor_id: r for r in all_records(plugin) if r.signature == 'REFR' and r.editor_id in ids}
    if len(refs) != 2:
        raise ValueError("the placed doors were not found after writing; check the plugin")
    for src, dst, dst_end in ((ids[0], ids[1], ends[1]), (ids[1], ids[0], ends[0])):
        ref = refs[src]
        ref.subrecords[:] = [s for s in ref.subrecords if s.signature != 'XTEL']
        at = next(i for i, s in enumerate(ref.subrecords) if s.signature == 'DATA')
        sub = type(ref.subrecords[0])('XTEL', xtel(refs[dst].form_id.value, arrival_point(dst_end, distance)))
        ref.subrecords.insert(at, sub)  # XTEL precedes DATA
    plugin.modified = True
    plugin.save(str(path))
    return {'path': str(path), 'doors': [{'editor_id': e, 'form_id': f"{refs[e].form_id.value:08X}",
                                           'base': base, 'cell': end.get('cell') or
                                           f"{end.get('worldspace')} ({end.get('grid_x')},{end.get('grid_y')})"}
                                          for e, base, end in zip(ids, bases, ends)],
            'notes': ['Each door leads to a point about 80 units behind the other door, facing away from it; '
                      'check the doors face the right way in game and adjust rotation_z if not.',
                      'NPCs will not path through these doors unless the navmeshes are linked.']}
