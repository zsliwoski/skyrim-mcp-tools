"""Read Skyrim's Papyrus runtime log (Papyrus.0.log) and group repeated errors."""
import os
import re
from pathlib import Path

LEVELS = ("error", "warning", "info")
_ENTRY = re.compile(r"^\[(\d\d/\d\d/\d{4} - \d\d:\d\d:\d\d[AP]M)\]\s*(?:(error|warning|info):\s*)?(.*)$", re.I)
_FORM_ID = re.compile(r"\(([0-9A-Fa-f]{8})\)")

# (pattern, advice) pairs; the first match wins.
HINTS = (
    (re.compile(r"on a None object|cannot access .* on a none", re.I),
     "A property, alias or form lookup is empty. Check the property is filled (attach_*_script properties), the alias "
     "has a reference filled before the script runs, and Game.GetForm targets a form that exists in the load order."),
    (re.compile(r"unable to find script|cannot open store for class|missing script|no script named", re.I),
     "The compiled .pex is not where the game looks. Check the script was compiled, is in the package/install, and "
     "that the name matches the plugin's script reference."),
    (re.compile(r"has no property|property .* not (?:found|present)|unable to find property", re.I),
     "The plugin's script property list does not match the compiled script. Recompile and re-attach the script."),
    (re.compile(r"cannot cast|cannot convert", re.I),
     "A form was cast to the wrong type (for example a Sound property must point at a SOUN, not a SNDR)."),
    (re.compile(r"state .* not found|function .* not found|unable to call|no such function", re.I),
     "A function or state was called that the script does not define. Check the script version in game is current."),
    (re.compile(r"stack is (?:too deep|overflow)|suspended stack", re.I),
     "Papyrus is overloaded or a script loops. Look for OnUpdate or event handlers that re-trigger themselves."),
)


def default_log_dirs(env=None):
    """Candidate Papyrus log folders, most specific first."""
    env = os.environ if env is None else env
    dirs = []
    if env.get("SKYRIM_LOG_DIR"):
        dirs.append(Path(env["SKYRIM_LOG_DIR"]))
    tail = Path("My Games") / "Skyrim Special Edition" / "Logs" / "Script"
    if env.get("USERPROFILE"):
        dirs.append(Path(env["USERPROFILE"]) / "Documents" / tail)
    if env.get("OneDrive"):  # Documents is redirected into OneDrive on some installs
        dirs.append(Path(env["OneDrive"]) / "Documents" / tail)
    return dirs


def find_log_dir(candidates):
    for d in candidates:
        if Path(d).is_dir():
            return Path(d)
    return None


def list_logs(log_dir):
    """The log files in the folder, newest first."""
    files = [p for p in Path(log_dir).glob("*.log") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"file": p.name, "bytes": p.stat().st_size, "modified": p.stat().st_mtime} for p in files]


def parse_entries(text):
    """Split log text into entries; indented or unstamped lines belong to the previous entry's stack."""
    entries = []
    for line in text.splitlines():
        m = _ENTRY.match(line)
        if m:
            stamp, level, msg = m.groups()
            entries.append({"time": stamp, "level": (level or "info").lower(), "message": msg.strip(), "stack": []})
        elif entries and line.strip():
            entries[-1]["stack"].append(line.strip())
    return entries


def _group_key(entry):
    return (entry["level"], _FORM_ID.sub("(########)", entry["message"]))


def hint_for(message):
    for pattern, advice in HINTS:
        if pattern.search(message):
            return advice
    return None


def summarize(entries, text_filter=None, level=None, max_groups=40, max_stack=6):
    """Filter entries and fold repeats into groups with a count, first/last time and one example stack."""
    needle = text_filter.lower() if text_filter else None
    wanted = LEVELS[: LEVELS.index(level) + 1] if level else None  # level=warning also keeps errors
    counts = {l: 0 for l in LEVELS}
    groups = {}
    for e in entries:
        if wanted and e["level"] not in wanted:
            continue
        if needle and needle not in (e["message"] + " " + " ".join(e["stack"])).lower():
            continue
        counts[e["level"]] = counts.get(e["level"], 0) + 1
        g = groups.setdefault(_group_key(e), {
            "level": e["level"], "message": e["message"], "count": 0,
            "first": e["time"], "last": e["time"], "stack": e["stack"][:max_stack],
        })
        g["count"] += 1
        g["last"] = e["time"]
    ordered = sorted(groups.values(), key=lambda g: (LEVELS.index(g["level"]) if g["level"] in LEVELS else 3, -g["count"]))
    out = []
    for g in ordered[:max_groups]:
        h = hint_for(g["message"])
        if h:
            g["hint"] = h
        out.append(g)
    return {"counts": counts, "groups": out, "groups_total": len(ordered), "truncated": len(ordered) > max_groups}


def read_log(log_dir, text_filter=None, level=None, log=0, tail_lines=20000, max_groups=40):
    """Summarize Papyrus.<log>.log (0 is the current session, 1 the previous one, and so on)."""
    if level is not None and level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    path = Path(log_dir) / f"Papyrus.{int(log)}.log"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cut = len(lines) > tail_lines
    entries = parse_entries("\n".join(lines[-tail_lines:] if tail_lines else lines))
    result = summarize(entries, text_filter, level, max_groups)
    result.update({"file": str(path), "modified": path.stat().st_mtime, "lines_read": min(len(lines), tail_lines or len(lines)),
                   "lines_total": len(lines), "tail_cut": cut})
    if not entries:
        result["note"] = ("The log has no entries. Papyrus logging is off by default: set bEnableLogging=1 and "
                          "bEnableTrace=1 under [Papyrus] in SkyrimCustom.ini, then reproduce the problem.")
    return result
