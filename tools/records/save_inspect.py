"""Check a plugin's quests against a Skyrim save: was the plugin loaded, and did its quests start."""
import os
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin

from tools.records import save_file
from tools.records.migrate import _oid, _own_records


def default_save_dirs(env=None):
    env = os.environ if env is None else env
    dirs = []
    if env.get("SKYRIM_SAVES_DIR"):
        dirs.append(Path(env["SKYRIM_SAVES_DIR"]))
    tail = Path("My Games") / "Skyrim Special Edition" / "Saves"
    if env.get("USERPROFILE"):
        dirs.append(Path(env["USERPROFILE"]) / "Documents" / tail)
    if env.get("OneDrive"):
        dirs.append(Path(env["OneDrive"]) / "Documents" / tail)
    return dirs


def find_save_dir(candidates):
    return next((Path(d) for d in candidates if Path(d).is_dir()), None)


def list_saves(save_dir, limit=10):
    out = []
    for p in save_file.find_saves(save_dir)[:limit]:
        try:
            h = save_file.read_header(p)
            out.append({"file": p.name, "player": h["player"], "level": h["level"], "location": h["location"],
                        "save_number": h["save_number"], "modified": p.stat().st_mtime})
        except (save_file.SaveError, OSError):
            out.append({"file": p.name, "error": "unreadable header"})
    return out


def _stage_indexes(rec):
    import struct
    return sorted({struct.unpack_from("<H", bytes(s.data))[0] for s in rec.subrecords if s.signature == "INDX"})


def inspect_plugin_in_save(plugin_path, save_path, quest=None):
    """Report whether the plugin is in the save's load order and the saved state of each of its quests."""
    plugin = Plugin.load(str(plugin_path))
    name = Path(plugin_path).name
    save = save_file.load_save(save_path)
    h = save["header"]
    lower = name.lower()
    regular = [p.lower() for p in save["plugins"]]
    light = [p.lower() for p in save["light_plugins"]]
    result = {"save": Path(save_path).name, "player": h["player"], "level": h["level"], "location": h["location"],
              "plugin": name, "plugin_in_save": lower in regular or lower in light, "quests": [], "notes": []}
    if not result["plugin_in_save"]:
        result["notes"].append(
            f"{name} was not in the load order when this save was made. Load the game with the plugin enabled "
            "and save again; quests cannot start in a save that never loaded the plugin.")
        return result
    if lower in regular:
        prefix, mask = regular.index(lower) << 24, 0xFFFFFF
        result["load_index"] = f"{regular.index(lower):02X}"
    else:
        idx = light.index(lower)
        prefix, mask = 0xFE000000 | (idx << 12), 0xFFF
        result["load_index"] = f"FE{idx:03X}"
    saved = save["quests"]
    wanted = quest.lower() if quest else None
    for rec in _own_records(plugin):
        if rec.signature != "QUST":
            continue
        edid = rec.editor_id or ""
        if wanted and wanted not in edid.lower():
            continue
        state = saved.get(prefix | (_oid(rec) & mask))
        defined = _stage_indexes(rec)
        row = {"quest": edid, "form_id": f"{prefix | (_oid(rec) & mask):08X}", "in_save": state is not None,
               "defined_stages": defined}
        if state:
            reached = [s["stage"] for s in state["stages"] if s["done"]]
            row.update({"flags": state["flags"], "stages_reached": reached,
                        "highest_stage": max(reached) if reached else None})
            if state.get("partial"):
                row["partial"] = True
        result["quests"].append(row)
    missing = [q["quest"] for q in result["quests"] if not q["in_save"]]
    if not result["quests"]:
        result["notes"].append("The plugin defines no quests" + (f" matching {quest!r}." if quest else "."))
    if missing:
        result["notes"].append(
            "No saved state for: " + ", ".join(missing) + ". A quest that never started leaves nothing in the "
            "save. Check start-game-enabled and the SEQ file (package_plugin writes it), or start it with "
            "startquest. If the plugin was rebuilt after this save, form IDs changed: make a new game save.")
    return result
