"""Reports on the compiled Papyrus scripts found in the game's BSA archives."""
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from esplib.bsa import BsaReader

DATA = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data")
BSAS = ["Skyrim - Misc.bsa"]  # vanilla scripts live here; add mod BSAs to include them
SHOW_PER_GROUP = 8


class PexReader:
    """Big-endian reader over a compiled Papyrus (.pex) file."""

    def __init__(self, data):
        self.d, self.p = data, 0

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from('>H', self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from('>I', self.d, self.p)[0]
        self.p += 4
        return v

    def u64(self):
        v = struct.unpack_from('>Q', self.d, self.p)[0]
        self.p += 8
        return v

    def string(self):
        n = self.u16()
        s = self.d[self.p:self.p + n].decode('cp1252', 'replace')
        self.p += n
        return s


def parse_pex(data):
    """Return header info and the main object's parent class (the `extends`)."""
    r = PexReader(data)
    if r.u32() != 0xFA57C0DE:
        raise ValueError("not a PEX file")
    major, minor, game_id = r.u8(), r.u8(), r.u16()
    compiled = r.u64()
    source, user, computer = r.string(), r.string(), r.string()
    strings = [r.string() for _ in range(r.u16())]
    functions = 0
    if r.u8():  # debug info
        r.u64()
        functions = r.u16()
        for _ in range(functions):
            r.u16(); r.u16(); r.u16(); r.u8()
            for _ in range(r.u16()):
                r.u16()
    for _ in range(r.u16()):  # user flags
        r.u16(); r.u8()
    object_count = r.u16()
    name = parent = None
    if object_count:
        name = strings[r.u16()]
        r.u32()  # object size
        parent = strings[r.u16()]
    return {
        'name': name, 'parent': parent, 'source': source, 'user': user,
        'computer': computer, 'compiled': compiled, 'game_id': game_id,
        'functions': functions, 'size': len(data),
    }


FRAGMENT_PREFIXES = [
    (r'^qf_', 'Quest fragment'), (r'^tif_+', 'Dialogue (topic info) fragment'),
    (r'^sf_', 'Scene fragment'), (r'^pf_', 'AI package fragment'),
    (r'^prkf_', 'Perk fragment'), (r'^tf_', 'Terminal fragment'),
]


def pattern_kind(name):
    n = re.sub(r'^(dlc\d+|cc\w{3,}?|dbm_)', '', name.lower())
    for rx, label in FRAGMENT_PREFIXES:
        if re.match(rx, n):
            return label
    return None


def origin(name):
    n = name.lower()
    if n.startswith('dlc1'):
        return 'Dawnguard'
    if n.startswith('dlc2'):
        return 'Dragonborn'
    if n.startswith('dlc01'):
        return 'Hearthfire'
    if n.startswith('cc'):
        return 'Creation Club'
    if n.startswith(('mq', 'c0', 'c1', 'c2', 'ms', 'dg', 'dun', 'tg', 'fol')):
        return 'Vanilla (quest/faction content)'
    return 'Vanilla / other'


# Native Papyrus root types, grouped into broader families.
FAMILIES = {
    'Quest': 'Quests', 'ReferenceAlias': 'Quest aliases', 'Alias': 'Quest aliases',
    'LocationAlias': 'Quest aliases', 'ActiveMagicEffect': 'Magic effects',
    'ObjectReference': 'Placed objects / references', 'Actor': 'Actors',
    'Form': 'Forms (generic)', 'ActorBase': 'Actors', 'Package': 'AI packages',
    'TopicInfo': 'Dialogue', 'Scene': 'Scenes', 'Perk': 'Perks', 'Spell': 'Magic',
    'Utility': 'Utility / static libraries', 'Game': 'Utility / static libraries',
    'Debug': 'Utility / static libraries', 'Math': 'Utility / static libraries',
    'Input': 'Utility / static libraries', 'UI': 'Utility / static libraries',
    'Cell': 'Cells', 'Door': 'Placed objects / references',
    'Container': 'Placed objects / references', 'Armor': 'Items', 'Weapon': 'Items',
    'Potion': 'Items', 'Book': 'Items', 'MiscObject': 'Items', 'Ingredient': 'Items',
    'Furniture': 'Placed objects / references', 'Activator': 'Placed objects / references',
    'Location': 'Locations', 'Race': 'Races', 'Faction': 'Factions',
    'Keyword': 'Keywords', 'Message': 'Messages', 'Sound': 'Sounds',
    'Weather': 'Weather', 'Worldspace': 'Worldspaces', 'Enchantment': 'Magic',
    'MagicEffect': 'Magic', 'Shout': 'Magic', 'WordOfPower': 'Magic',
    'Projectile': 'Projectiles', 'Hazard': 'Hazards', 'Explosion': 'Hazards',
    'Light': 'Lighting', 'Idle': 'Animation', 'Terminal': 'Terminals',
}
FAMILIES_LC = {k.lower(): v for k, v in FAMILIES.items()}


def load_scripts():
    scripts = {}
    for bsa in BSAS:
        with BsaReader(DATA / bsa) as b:
            for path in b.list_files():
                if path.lower().endswith('.pex'):
                    scripts[Path(path).stem.lower()] = (bsa, b.read_file(path))
    for path in (DATA / 'Scripts').glob('*.pex'):
        scripts[path.stem.lower()] = ('loose', path.read_bytes())
    return scripts


def root_type(name, info):
    """Follow `extends` upward to the first native Papyrus type."""
    chain, cur = [], name.lower()
    while cur in info and cur not in chain:
        chain.append(cur)
        if cur in FAMILIES_LC:
            return info[cur]['name']
        parent = info[cur]['parent']
        if not parent:
            break
        cur = parent.lower()
    if cur in FAMILIES_LC:
        return info[cur]['name'] if cur in info else cur
    # Unknown native type: the topmost script below Form
    below_form = [c for c in chain if c != 'form']
    return info[below_form[-1]]['name'] if below_form else None


def main():
    raw = load_scripts()
    info, errors = {}, []
    for key, (src, data) in raw.items():
        try:
            info[key] = {**parse_pex(data), 'bsa': src}
        except Exception as e:
            errors.append((key, repr(e)))
    print(f"{len(info)} scripts parsed ({len(errors)} errors)")
    sources = Counter(i['bsa'] for i in info.values())
    print("by origin file:", dict(sources))

    by_family, by_root, by_origin = (defaultdict(list) for _ in range(3))
    for key, i in info.items():
        root = root_type(key, info) or i['parent'] or '(none)'
        kind = pattern_kind(i['name'])
        i['root'] = root
        by_root[root].append(i['name'])
        fam = kind or FAMILIES_LC.get(root.lower(), f"Other ({root})")
        by_family[fam].append(i['name'])
        by_origin[origin(i['name'])].append(i['name'])

    def show(title, groups, limit=None):
        print(f"\n===== {title} =====")
        for label, names in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
            sample = ', '.join(sorted(names, key=str.lower)[:SHOW_PER_GROUP])
            print(f"{len(names):>6}  {label}\n         e.g. {sample}")

    show("By purpose (family)", by_family)
    show("By native root type (what the script ultimately extends)", by_root, 25)
    show("By origin (name prefix heuristic)", by_origin)

    big = sorted(info.values(), key=lambda i: -i['size'])[:10]
    print("\n===== Largest scripts (bytes, functions) =====")
    for i in big:
        print(f"{i['size']:>8}  {i['functions']:>4} fn  {i['name']} extends {i['parent']}")

    if errors:
        print("\nParse errors:", errors[:5])


if __name__ == '__main__':
    main()
