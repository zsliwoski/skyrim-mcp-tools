"""Generate esplib's condition-function table from xEdit's definitions.

CTDA stores a condition as an opaque 32-byte blob whose Parameter #1 and
Parameter #2 are FormIDs for some functions and plain integers/enums for
others. Deciding which is which needs the game's condition-function
table, which xEdit already maintains in Pascal.

Rather than hand-transcribing ~900 rows across two games, parse them out
of the xEdit sources and emit a Python module.

This is a MAINTENANCE TOOL, not part of the esplib package. esplib never
reads xEdit at runtime — it imports the generated module like any other
Python file, exactly as `defs/tes5.py` and `defs/fo4.py` (both also
transcribed from xEdit) are imported. Run this only to refresh the table
against a newer xEdit, then commit the result.

Usage:
    python devtools/gen_condition_functions.py path/to/xEdit/Core

The path is required: pointing at a specific xEdit checkout is a
deliberate act, and a baked-in default would make esplib look as though
it depends on one being present.
"""

import re
import sys
from pathlib import Path

# game_id -> (definitions file, expected entry count sanity anchor)
GAMES = {
    'tes5': 'wbDefinitionsTES5.pas',
    'fo4': 'wbDefinitionsFO4.pas',
}

OUTPUT = (Path(__file__).resolve().parent.parent
          / 'src' / 'esplib' / 'defs' / 'condition_functions.py')

# The parameter-type enum is ordered Misc, then Enums, then FormIDs.
# Everything from ptActor onward is a form reference; deriving the
# boundary from the enum itself keeps this correct when xEdit adds
# types (FO4, for instance, puts ptActorValue in the FormID block
# because AVIF is a real record there, while Skyrim treats it as an int).
FORM_BLOCK_START = 'ptActor'

ENUM_RE = re.compile(
    r'TConditionParameterType\s*=\s*\((.*?)\n\s*\);', re.DOTALL)
ENUM_ENTRY_RE = re.compile(r'\{\s*(\d+)\s*\}\s*(pt[A-Za-z]+)')

TABLE_RE = re.compile(
    r'wbConditionFunctions\s*:\s*array\[0\.\.(\d+)\]\s*of\s*'
    r'TConditionFunction\s*=\s*\((.*?)\n\s*\);', re.DOTALL)
# xEdit is inconsistent about the casing of ParamType/Paramtype, and the
# record type declares a Desc field that the literals never populate.
ENTRY_RE = re.compile(
    r"\(\s*Index:\s*(\d+)\s*;\s*Name:\s*'([^']*)'"
    r"(?:\s*;\s*Desc:\s*'[^']*')?"
    r"(?:\s*;\s*Paramtype1:\s*(pt[A-Za-z]+))?"
    r"(?:\s*;\s*Paramtype2:\s*(pt[A-Za-z]+))?"
    r"(?:\s*;\s*Paramtype3:\s*(pt[A-Za-z]+))?\s*;?\s*\)",
    re.IGNORECASE)


def parse_form_types(source: str) -> set:
    """Return the set of pt* names that denote a FormID."""
    match = ENUM_RE.search(source)
    if match is None:
        raise SystemExit("could not locate TConditionParameterType enum")
    entries = ENUM_ENTRY_RE.findall(match.group(1))
    by_index = {int(idx): name for idx, name in entries}
    starts = [i for i, name in by_index.items() if name == FORM_BLOCK_START]
    if not starts:
        raise SystemExit(f"enum has no {FORM_BLOCK_START} entry")
    first = min(starts)
    return {name for i, name in by_index.items() if i >= first}


def parse_functions(source: str):
    """Return (entries, form_types) for one game's definitions."""
    form_types = parse_form_types(source)
    match = TABLE_RE.search(source)
    if match is None:
        raise SystemExit("could not locate wbConditionFunctions table")
    upper_bound = int(match.group(1))
    rows = ENTRY_RE.findall(match.group(2))
    if len(rows) != upper_bound + 1:
        raise SystemExit(
            f"parsed {len(rows)} entries but the array is declared "
            f"[0..{upper_bound}] -- the parser is out of step with xEdit")

    lowered = {name.lower() for name in form_types}
    entries = {}
    for index, name, p1, p2, _p3 in rows:
        index = int(index)
        if index in entries:
            raise SystemExit(f"duplicate function index {index}")
        entries[index] = (
            name,
            (p1 or None) and (p1.lower() in lowered),
            (p2 or None) and (p2.lower() in lowered),
        )
    return entries, form_types


def render(tables: dict) -> str:
    out = [
        '"""Condition (CTDA) function tables.',
        '',
        'GENERATED FILE -- do not edit by hand.',
        'Regenerate with:',
        '    python devtools/gen_condition_functions.py <xEdit Core dir>',
        '',
        "Maps a condition function's index to its name and to whether each",
        'of its first two parameters holds a FormID. CTDA stores conditions',
        'as opaque bytes, so this table is what lets `copy_record` find the',
        'FormIDs inside one and remap them across differing master lists.',
        '"""',
        '',
        'from typing import Dict, Optional, Tuple',
        '',
        '# index -> (name, param1_is_form, param2_is_form)',
        'ConditionFunction = Tuple[str, bool, bool]',
        '',
    ]
    for game, (entries, form_types) in sorted(tables.items()):
        n_form = sum(1 for _, p1, p2 in entries.values() if p1 or p2)
        out.append(f'# {len(entries)} functions, {n_form} taking a form '
                   f'parameter.')
        out.append(f'{game.upper()}_CONDITION_FUNCTIONS'
                   f': Dict[int, ConditionFunction] = {{')
        for index in sorted(entries):
            name, p1, p2 = entries[index]
            out.append(f'    {index}: ({name!r}, {bool(p1)}, {bool(p2)}),')
        out.append('}')
        out.append('')

    out += [
        'CONDITION_FUNCTIONS: Dict[str, Dict[int, ConditionFunction]] = {',
    ]
    for game in sorted(tables):
        out.append(f"    {game!r}: {game.upper()}_CONDITION_FUNCTIONS,")
    out += [
        '}',
        '',
        '',
        'def get_condition_function(game_id: str,',
        '                           index: int) -> Optional[ConditionFunction]:',
        '    """Look up a condition function, or None if unknown.',
        '',
        '    An unknown function is not an error -- mods can carry',
        '    conditions from script extenders that xEdit does not know.',
        '    Callers must leave such parameters untouched rather than',
        '    guessing that they are FormIDs.',
        '    """',
        '    table = CONDITION_FUNCTIONS.get(game_id)',
        '    if table is None:',
        '        return None',
        '    return table.get(index)',
        '',
    ]
    return '\n'.join(out)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python devtools/gen_condition_functions.py "
            "<path to xEdit Core directory>")
    core = Path(sys.argv[1])
    if not core.is_dir():
        raise SystemExit(f"xEdit Core directory not found: {core}")

    tables = {}
    for game, filename in GAMES.items():
        path = core / filename
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        source = path.read_text(encoding='utf-8', errors='replace')
        entries, form_types = parse_functions(source)
        tables[game] = (entries, form_types)
        n_form = sum(1 for _, p1, p2 in entries.values() if p1 or p2)
        print(f"{game}: {len(entries)} functions, "
              f"{n_form} with a form parameter, "
              f"{len(form_types)} form parameter types")

    OUTPUT.write_text(render(tables), encoding='utf-8', newline='\n')
    print(f"wrote {OUTPUT}")


if __name__ == '__main__':
    main()
