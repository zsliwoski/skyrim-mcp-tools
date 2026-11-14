"""Preset idle comments for a hired companion, each gated on the situation the engine is in when it picks an idle.

Every entry is an `idle` dialogue entry (see npc_builder SUBTYPES) conditioned on CurrentHireling, not sneaking and a
random throttle, so they stay occasional. Lines are neutral; override them per key with the `commentary_text` spec.
"""

CURRENT_HIRELING = 'CurrentHireling'
THROTTLE = 40  # GetRandomPercent upper bound; the engine rolls idles often, so this keeps comments rare

WEATHER = {
    'rain': ("IsRaining", ["Rain again. My boots will never dry.", "Wet weather. It gets into everything.",
                           "I'd rather be under a roof than out in this."]),
    'snow': ("IsSnowing", ["Snow's coming down hard.", "My fingers are going numb.",
                           "Cold enough to freeze the words in your throat."]),
}

# (key, lower hour inclusive or None, upper hour exclusive or None, lines); outdoors only, GetCurrentTime is 0-24
TIME_OF_DAY = [
    ('dawn', 5, 8, ["Early light. A good time to be walking.", "The day's only just starting."]),
    ('dusk', 18, 21, ["Sun's going down. We should think about a fire.", "Evening already. The day got away from us."]),
    ('night', 21, 24, ["It's late. Mind where you step.", "Dark as a tomb out here."]),
    ('small_hours', 0, 5, ["Nobody with sense is awake at this hour.", "The small hours. I could sleep on my feet."]),
]

# main quest milestones, in the order a player reaches them: each line set plays after that quest is completed and
# before the next one is (the last has no upper bound)
MAIN_QUEST = [
    ('MQ101', ["Helgen is behind us, but I still see it burning.", "Dragons. Real dragons. I'm still not used to it."]),
    ('MQ103', ["That barrow got to us all. Whatever you took from it, keep it safe.",
               "The Greybeards, the Jarl, the dragon stone... it keeps growing, doesn't it?"]),
    ('MQ104', ["You killed a dragon. I still can't quite believe it.", "They say you took its soul. Is that true?"]),
    ('MQ105', ["Thu'um. A voice like thunder. I felt that one in my chest.",
               "The Greybeards called you. You're something else, aren't you?"]),
    ('MQ201', ["Elves and their diplomacy. I'd rather face a dragon.", "That embassy gave me the shivers."]),
    ('MQ306', ["It's over. You did the thing nobody thought possible.", "The world's still here. Because of you."]),
]

# place keyword -> lines, in/out of doors where the wording differs
PLACE_KEYWORDS = {
    'LocTypeInn': ["A warm fire and a mug would suit me fine.", "Good place to hear the news."],
    'LocTypeStore': ["Mind your purse in here.", "See anything worth buying?"],
    'LocTypeTemple': ["Quiet in here. It feels strange to talk.", "I don't pray much, but this place makes me wish I did."],
    'LocTypePlayerHouse': ["Your own roof. Not bad.", "Feels like home, a bit."],
    'LocTypeOrcStronghold': ["Orcs. Be polite and mind your manners.", "They don't care much for outsiders."],
    'LocTypeDragonLair': ["I don't like the look of those bones.", "Dragons nest here. Quietly now."],
    'LocTypeHabitation': ["Settlers. At least there's a hearth.", "Folk live out here. Brave of them."],
}


def _cond(function, form=None, op='==', value=1, **extra):
    c = {'function': function, 'op': op, 'value': value, **extra}
    if form is not None:
        c['form'] = form
    return c


def _base():
    return [_cond('GetInFaction', CURRENT_HIRELING), _cond('IsSneaking', value=0),
            _cond('GetRandomPercent', op='<=', value=THROTTLE)]


def _entry(conditions, lines):
    return {'kind': 'idle', 'conditions': _base() + conditions, 'answers': [[x] for x in lines]}


def weather_entries(text=None):
    text = text or {}
    return [_entry([_cond(fn), _cond('IsInInterior', value=0)], text.get(key, lines))
            for key, (fn, lines) in WEATHER.items()]


def time_entries(text=None):
    text = text or {}
    out = []
    for key, low, high, lines in TIME_OF_DAY:
        conds = [_cond('IsInInterior', value=0), _cond('GetCurrentTime', op='>=', value=low)]
        if high < 24:
            conds.append(_cond('GetCurrentTime', op='<', value=high))
        out.append(_entry(conds, text.get(key, lines)))
    return out


def main_quest_entries(text=None):
    text = text or {}
    out = []
    for i, (quest, lines) in enumerate(MAIN_QUEST):
        conds = [_cond('GetQuestCompleted', quest)]
        if i + 1 < len(MAIN_QUEST):
            conds.append(_cond('GetQuestCompleted', MAIN_QUEST[i + 1][0], value=0))
        out.append(_entry(conds, text.get(quest, lines)))
    return out


def place_entries(text=None):
    text = text or {}
    return [_entry([_cond('LocationHasKeyword', k)], text.get(k, lines)) for k, lines in PLACE_KEYWORDS.items()]


def preset_keys():
    """Every key `commentary_text` accepts, for validation."""
    return (set(WEATHER) | {k for k, *_ in TIME_OF_DAY} | {q for q, _ in MAIN_QUEST} | set(PLACE_KEYWORDS))
