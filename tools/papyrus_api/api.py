"""Papyrus API database: exact script signatures, actor values and Creation Kit wiki text, separate from lore."""
import os
import re
from pathlib import Path

from tools.knowledge import db as _kb
from tools.knowledge import mediawiki
from tools.knowledge import vectors as _kv
from tools.knowledge.db import Writer, get_page, status  # noqa: F401  (re-exported for callers)

from . import psc

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "api_db" / "api.db"
SCRIPTS = "psc"
ACTOR_VALUES = "actor-values"
SCRIPT_LICENSE = "user's own game data"
SCRIPT_ATTRIBUTION = "Signatures read from the user's installed Papyrus sources (.psc); local reference only"
CKWIKI = mediawiki.Wiki("ckwiki", "CC BY-SA 3.0",
                        "Text from the Creation Kit wiki (https://ck.uesp.net), CC BY-SA 3.0",
                        "https://ck.uesp.net/w/api.php", "https://ck.uesp.net/wiki/",
                        "skyrim-mcp-tools-api/1.0 (local research index; respects rate limits)")
EXTENDS = re.compile(r'\bextends (\w+)', re.I)
KEYWORDS = "Keywords: "


def db_path():
    return Path(os.environ.get("SKYRIM_API_DB") or DEFAULT_PATH)


def connect(path=None, create=False):
    return _kb.connect(path or db_path(), create, "Papyrus API", "python -m tools.papyrus_api build ...")


MEMBER_PAGE = re.compile(r'^[A-Za-z_]\w*(?: - \w+)?$')


def _is_api_hit(row):
    """Script members and CK wiki Papyrus reference pages ('OnHit - ObjectReference') rather than general CK pages."""
    if row['source'] == SCRIPTS:
        return bool(row['section'])
    if row['source'] == CKWIKI.source:
        t = row['title']
        return bool(MEMBER_PAGE.match(t)) and (' - ' in t or t != t.lower() and not t.istitle())
    return row['source'] == ACTOR_VALUES


EVENT_INTENT = re.compile(r"\b(when|whenever|detect|detects|notice|notified|react|reacts|hook|trigger|triggers|fires?)\b",
                          re.I)


def _is_event(row):
    return row['title'].split(' - ')[0].startswith('On') and row['title'].split(' - ')[0][2:3].isupper() \
        or row['section'].startswith('On') and row['section'][2:3].isupper()


STOP = set("a an the to of in on is are be it its that this when whenever detect detects notice notified react "
           "reacts hook trigger triggers fire fires player something someone actor object for with and or then do "
           "run code know get my an".split())


def _stem(w):
    return re.sub(r'(ing|ed|es|s)$', '', w) if len(w) > 3 else w


def _keyword_matches(con, query, source, limit=3):
    """Members and actor values whose Keywords line shares at least two stems with the query (one for a
    one-word query), e.g. 'player is hit' only matches OnHit through its 'hit' keyword, which embeddings dilute."""
    if source not in (None, SCRIPTS, ACTOR_VALUES):
        return []
    q = {_stem(w) for w in re.findall(r'[a-z]+', query.lower()) if w not in STOP}
    if not q:
        return []
    scored = []
    rows = con.execute("SELECT c.id, c.source, c.title, c.section, c.url, c.revision, c.text, s.license FROM chunks c "
                       "JOIN sources s ON s.name=c.source WHERE c.source IN (?, ?) AND "
                       "(c.source=? OR c.section != '')", (SCRIPTS, ACTOR_VALUES, ACTOR_VALUES)).fetchall()
    for r in rows:
        if source and r['source'] != source:
            continue
        kw = next((l for l in r['text'].split('\n') if l.startswith(KEYWORDS)), '')
        have = {_stem(w) for w in kw[len(KEYWORDS):].split()}
        hits = len(q & have)
        if hits >= min(2, len(q)):
            d = dict(r)
            words = d.pop('text').split()
            d['snippet'] = ' '.join(words[:60]) + (' ...' if len(words) > 60 else '')
            scored.append((-hits, r['title'], d))
    scored.sort(key=lambda s: s[:2])
    return [s[2] for s in scored[:limit]]


def search(con, query, limit=10, source=None, mode="auto", embedder=None):
    """Hybrid search that puts concrete API entries (script members, actor values, CK wiki Papyrus reference
    pages) ahead of general CK pages, drops non-English wiki translations and, for 'when ...' style intent,
    lists events (OnHit, OnEffectFinish...) first."""
    pool = _kv.search(con, query, max(limit * 5, 30), source, mode, embedder,
                      "python -m tools.papyrus_api build vectors")
    pool = [r for r in pool if r['title'].isascii()]
    events = bool(EVENT_INTENT.search(query))
    have = {r['id'] for r in pool}
    pool = [r for r in _keyword_matches(con, query, source) if r['id'] not in have] + pool
    pool.sort(key=lambda r: (not _is_api_hit(r), not (events and _is_event(r))))
    return pool[:limit]


INTENT = {
    'onhit': 'hit damaged attacked struck takes damage weapon spell projectile',
    'oneffectfinish': 'spell effect ends expires finished over wears off',
    'oneffectstart': 'spell effect begins starts applied',
    'onmagiceffectapply': 'magic effect applied hit by spell',
    'ondeath': 'dies killed dead',
    'ondying': 'dying about to die',
    'onitemadded': 'item added picked up received inventory',
    'onitemremoved': 'item removed dropped lost inventory',
    'onequipped': 'equipped worn put on',
    'onunequipped': 'unequipped removed taken off',
    'onactivate': 'activated used clicked interacted',
    'onupdate': 'every few seconds timer repeating periodic',
    'onupdategametime': 'every few game hours timer repeating periodic',
    'oncellload': 'cell loaded entered area',
    'onload': 'object loaded 3d loaded',
    'onenterbleedout': 'bleedout knocked down',
    'oncombatstatechanged': 'combat starts ends enters leaves fight',
    'onlocationchange': 'player enters new location travels',
    'onplayerloadgame': 'game loaded save loaded',
    'onsleepstart': 'player sleeps goes to bed',
    'onsleepstop': 'player wakes up',
    'onstoryaddtoplayer': 'story manager event',
    'onraceswitchcomplete': 'race changed',
    'onlevelup': 'level up',
    'registerforupdate': 'run code every few seconds timer repeat',
    'registerforsingleupdate': 'run code once after delay wait timer',
    'registerforkey': 'key pressed button input',
    'registerformodevent': 'listen custom event',
    'sendmodevent': 'broadcast custom event signal',
    'onstoryincreaselevel': 'player levels up level increased gains level',
    'onstoryincreaseskill': 'skill increased skill level up trained',
    'setplayerlevel': 'level up change player level set',
    'incrementskill': 'level up skill raise train increase skill',
    'incrementskillby': 'level up skill raise train increase skill',
    'getlevel': 'actor level player level',
    'carryweight': 'increase carry weight capacity burden encumbered inventory limit',
    'health': 'hit points hp life damage heal',
    'magicka': 'mana spell points',
    'stamina': 'sprint power attack endurance fatigue',
    'speedmult': 'movement speed run faster slower',
    'weaponspeedmult': 'attack speed swing faster attacks quicker',
    'unarmeddamage': 'fist damage punch',
    'damageresist': 'armor rating physical resistance',
    'healrate': 'health regeneration regenerate regenerates regen rate faster',
    'magickarate': 'magicka regeneration regenerate regenerates regen rate faster',
    'staminarate': 'stamina regeneration regenerate regenerates regen rate faster',
    'addperk': 'give perk grant perk unlock add perk',
    'addspell': 'give spell teach spell grant',
    'removespell': 'take away spell forget unlearn',
    'additem': 'give item put in inventory grant',
    'removeitem': 'take item remove from inventory delete',
    'equipitem': 'put on wear force equip',
    'setactorvalue': 'set stat attribute skill value',
    'modactorvalue': 'increase stat attribute skill permanent buff',
    'forceactorvalue': 'set stat attribute value override',
    'enable': 'show make appear spawn visible',
    'disable': 'hide make vanish remove from world',
    'placeatme': 'spawn create object actor next to',
    'moveto': 'teleport relocate move object actor marker',
    'onobjectequipped': 'weapon armor item equipped put on wears',
    'onobjectunequipped': 'weapon armor item unequipped taken off removed',
    'kill': 'slay murder make dead',
    'resurrect': 'revive bring back to life',
    'pushactoraway': 'knock back ragdoll shove',
    'startcombat': 'attack make hostile fight',
    'stopcombat': 'calm end fight peaceful',
    'sendassaultalarm': 'crime alarm guards bounty',
    'addtofaction': 'join faction',
    'setrelationshiprank': 'friend enemy relationship',
    'notification': 'show message top left text on screen',
    'messagebox': 'popup dialog message',
    'trace': 'log print debug output papyrus log',
    'forcefirstperson': 'camera view change',
    'getcurrentlocation': 'where is player current place area',
    'isinterior': 'inside indoors cell',
    'getdistance': 'how far distance between',
    'wait': 'wait pause delay sleep moment second',
}


def _words(name):
    """'OnMagicEffectApply' -> 'on magic effect apply', so intent queries match member names."""
    return ' '.join(re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', name)).lower()


def _is_engine_api(parsed):
    return any('native' in {f.lower() for f in m['flags']} for m in parsed['members'])


def index_scripts(con, dirs, native_only=True):
    """Index .psc files in dirs, a list of (origin, folder) such as ('skyrim', ...) and ('skse', ...).
    native_only keeps the engine API (scripts declaring native functions: Actor, Form, Game, Debug, ModEvent...)
    and drops
    the thousands of quest fragments and content scripts, which would drown real API in search results.
    When a script exists in several origins the 'skse' copy wins, and members that only it declares are
    flagged [SKSE] because they need the Script Extender at runtime. Returns the number of chunks."""
    found = {}
    for origin, folder in dirs:
        for f in sorted(Path(folder).glob('*.psc')):
            parsed = psc.parse(f.read_text(encoding='cp1252', errors='replace'))
            if parsed and (not native_only or _is_engine_api(parsed)):
                found.setdefault(parsed['name'].lower(), {})[origin] = (parsed, f)
    writer = Writer(con, SCRIPTS, SCRIPT_LICENSE, SCRIPT_ATTRIBUTION)
    for by_origin in found.values():
        origin = 'skse' if 'skse' in by_origin else next(iter(by_origin))
        parsed, path = by_origin[origin]
        base = {(m['kind'], m['name'].lower()) for o, (p, _) in by_origin.items() if o != 'skse'
                for m in p['members']}
        whole_script_skse = origin == 'skse' and len(by_origin) == 1
        head = f"ScriptName {parsed['name']}" + (f" extends {parsed['extends']}" if parsed['extends'] else '')
        lines = [' '.join([head] + parsed['flags'])] + ([parsed['doc']] if parsed['doc'] else [])
        if whole_script_skse:
            lines.append("[SKSE]")
        pieces = [('', '\n'.join(lines))]
        for m in parsed['members']:
            skse = whole_script_skse or (origin == 'skse' and (m['kind'], m['name'].lower()) not in base)
            text = '\n'.join([psc.signature(m)] + ([m['doc']] if m['doc'] else []) +
                             [f"{KEYWORDS}{_words(parsed['name'])} {_words(m['name'])} {INTENT.get(m['name'].lower(), '')}".rstrip()] + (["[SKSE]"] if skse else []))
            pieces.append((m['name'], text))
        writer.page(parsed['name'], pieces, str(path), origin)
    return writer.close()


def index_actor_values(con, model):
    """Index ActorValueInfo records of the model's loaded plugins. Papyrus takes the name without the 'AV'
    prefix of the EditorID ('AVHealth' -> 'Health')."""
    writer = Writer(con, ACTOR_VALUES, SCRIPT_LICENSE,
                    "Actor values read from the user's installed plugins; local reference only")
    for rec in model.by_sig['AVIF'].values():
        edid = rec.editor_id or ''
        name = edid[2:] if re.match(r'AV[A-Z0-9]', edid) else edid
        if not name:
            continue
        text = (f"Actor value {name}" + (f" ({rec.full_name})" if rec.full_name else '') +
                f"\nPass \"{name}\" as the name string to GetActorValue, SetActorValue, ModActorValue, "
                "DamageActorValue, RestoreActorValue, ForceActorValue and GetActorValuePercentage."
                f"\n{KEYWORDS}{_words(name)} {INTENT.get(name.lower(), '')}".rstrip())
        writer.page(f"ActorValue {name}", [('', text)], None, edid)
    return writer.close()


def import_ckwiki_dump(con, path, namespaces=('',), progress=None):
    return mediawiki.import_dump(con, CKWIKI, path, namespaces, progress)


def crawl_ckwiki(con, namespaces=('',), delay=1.0, limit=None, progress=None):
    return mediawiki.crawl(con, CKWIKI, namespaces, delay, limit, progress)


def _script_chunks(con, script):
    return [dict(r) for r in con.execute(
        "SELECT title, section, text, url, revision FROM chunks WHERE source=? AND title=? COLLATE NOCASE "
        "ORDER BY ord", (SCRIPTS, script))]


def chain(con, script):
    """[script, parent, grandparent, ...] following ScriptName ... extends ...; stops at an unknown script."""
    out, seen = [], set()
    while script and script.lower() not in seen:
        chunks = _script_chunks(con, script)
        if not chunks:
            break
        out.append(chunks[0]['title'])
        seen.add(script.lower())
        m = EXTENDS.search(chunks[0]['text'].split('\n', 1)[0])
        script = m.group(1) if m else None
    return out


def _body(text):
    lines = [x for x in text.split('\n')[1:] if not x.startswith(KEYWORDS)]
    return '\n'.join(lines).replace('[SKSE]', '').strip()


def _member(chunk):
    first = chunk['text'].split('\n', 1)[0]
    kind = re.search(r'\b(function|event|property)\b', first, re.I)
    return {'name': chunk['section'], 'kind': kind.group(1).lower() if kind else '', 'signature': first,
            'doc': _body(chunk['text']), 'skse': '[SKSE]' in chunk['text']}


def get_script(con, script, member=None):
    """Exact API lookup. Without member: the script header, inheritance chain and its own members. With member
    (case-insensitive): the declaration, searched on the script and then up its parents. None if the script
    is unknown."""
    names = chain(con, script)
    if not names:
        return None
    head = _script_chunks(con, names[0])
    if member is None:
        return {'script': names[0], 'declaration': head[0]['text'].split('\n')[0], 'doc': _body(head[0]['text']),
                'skse_only': '[SKSE]' in head[0]['text'], 'extends_chain': names[1:], 'file': head[0]['url'],
                'members': [_member(c) for c in head[1:]]}
    for name in names:
        hits = [_member(c) for c in _script_chunks(con, name)[1:] if c['section'].lower() == member.lower()]
        if hits:
            return {'script': names[0], 'defined_in': name, 'members': hits}
    return {'script': names[0], 'defined_in': None, 'members': []}


def suggest(con, name, limit=8):
    """Script and member names containing the text, for when an exact lookup misses."""
    like = f"%{name}%"
    return [dict(r) for r in con.execute(
        "SELECT title, section FROM chunks WHERE source=? AND (title LIKE ? OR section LIKE ?) "
        "ORDER BY length(title)+length(section) LIMIT ?", (SCRIPTS, like, like, limit))]
