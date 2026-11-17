"""Optional player challenges on a dialogue prompt: a speech check, an intimidation check, a brawl or a gold price.

A challenge turns one prompt into two responses under the same topic, tried in order like vanilla's: the first
(conditioned on the check) is the success the entry's own lines/code describe, the second is the failure. Most NPCs
should have none; they suit a personality (a proud captain, a bully, a mercenary), not every quest giver.
"""
GET_ITEM_COUNT, GET_ACTOR_VALUE, GET_RANDOM_PERCENT, GET_INTIMIDATE_SUCCESS = 47, 14, 77, 655
EQ, GE, LE = 0, 3, 5
SUBJECT, TARGET = 0, 1
SPEECHCRAFT = 17  # actor value index

DIFFICULTIES = {'very_easy': 'SpeechVeryEasy', 'easy': 'SpeechEasy', 'average': 'SpeechAverage',
                'hard': 'SpeechHard', 'very_hard': 'SpeechVeryHard'}
TYPES = ('persuade', 'intimidate', 'brawl', 'price', 'skill')
ALIASES = {'gold': 'price', 'bribe': 'price', 'speech': 'persuade'}
KEYS = ('type', 'difficulty', 'luck', 'amount', 'failure', 'brawl_on_failure', 'victory', 'skill', 'rank')
# actor value index = 6 + position in the NPC skill order (Speech is 17, matching SPEECHCRAFT)
SKILLS = ('OneHanded', 'TwoHanded', 'Archery', 'Block', 'Smithing', 'HeavyArmor', 'LightArmor', 'Pickpocket',
          'Lockpicking', 'Sneak', 'Alchemy', 'Speech', 'Alteration', 'Conjuration', 'Destruction', 'Illusion',
          'Restoration', 'Enchanting')
SKILL_ALIASES = {'marksman': 'Archery', 'speechcraft': 'Speech'}


def skill_of(challenge):
    """Canonical skill name for a skill challenge, or None."""
    name = str(challenge.get('skill', '')).replace(' ', '').lower()
    name = SKILL_ALIASES.get(name, name).lower()
    return next((s for s in SKILLS if s.lower() == name), None)
VICTORY_PROMPT = "That was a good fight."
VICTORY_LINES = ["You fight well. Well enough that I'll call it even."]
VICTORY_PAID = ["Fair and square. Here, your winnings."]
FAVOR = "((FavorQuest as Quest) as FavorDialogueScript)"
PAY = ("Game.GetPlayer().RemoveItem(Gold as MiscObject, {n})\n"
       "akSpeaker.AddItem(Gold as MiscObject, {n})")


def kind_of(challenge):
    """Canonical challenge type for a spec value (aliases allowed), or None."""
    t = str(challenge.get('type', '')).lower()
    t = ALIASES.get(t, t)
    return t if t in TYPES else None


def check(challenge, label):
    """Problems with a challenge spec."""
    if not isinstance(challenge, dict):
        return [f"{label}: challenge must be an object"]
    errors = [f"{label}: unknown keys {sorted(set(challenge) - set(KEYS))}"] if set(challenge) - set(KEYS) else []
    kind = kind_of(challenge)
    if kind is None:
        return errors + [f"{label}: type must be one of {list(TYPES)} (gold and bribe mean price)"]
    amount = challenge.get('amount')
    if kind == 'price' and not (isinstance(amount, int) and not isinstance(amount, bool) and amount > 0):
        errors.append(f"{label}: price needs amount, a positive whole number of gold")
    if kind == 'brawl' and amount is not None and not (
            isinstance(amount, int) and not isinstance(amount, bool) and amount > 0):
        errors.append(f"{label}: brawl amount must be a positive whole number of gold")
    if kind == 'persuade' and challenge.get('difficulty', 'average') not in DIFFICULTIES:
        errors.append(f"{label}: difficulty must be one of {list(DIFFICULTIES)}")
    if kind == 'skill':
        rank = challenge.get('rank')
        if skill_of(challenge) is None:
            errors.append(f"{label}: skill must be one of {list(SKILLS)}")
        if not (isinstance(rank, int) and not isinstance(rank, bool) and 1 <= rank <= 100):
            errors.append(f"{label}: skill needs rank, the skill level (1-100) the player must have")
    luck = challenge.get('luck', 0)
    if not (isinstance(luck, int) and not isinstance(luck, bool) and 0 <= luck <= 100):
        errors.append(f"{label}: luck must be a whole percent 0-100 (chance a too-low skill still succeeds)")
    victory = challenge.get('victory')
    if victory is not None:
        if not _fights(challenge) and kind != 'brawl':
            errors.append(f"{label}: victory only applies to a brawl (or intimidate with brawl_on_failure)")
        elif not isinstance(victory, dict) or set(victory) - {'prompt', 'lines'} or (
                'lines' in victory and not (isinstance(victory['lines'], list) and victory['lines'])):
            errors.append(f"{label}: victory is {{prompt, lines}}, what the player says and the NPC answers "
                          "once the fight is won")
    needs_failure = kind in ('persuade', 'intimidate', 'price', 'skill') or (kind == 'brawl' and amount)
    failure = challenge.get('failure')
    if needs_failure and not (isinstance(failure, dict) and failure.get('lines')):
        errors.append(f"{label}: failure needs lines, what the NPC says when the check fails")
    return errors


def forms(resolver):
    """Absolute form IDs of the vanilla records the challenges use."""
    out = {'gold': resolver.lookup('Gold001', ['MISC'])[0],
           'favor': resolver.lookup('DialogueFavorGeneric', ['QUST'])[0],
           'brawl_quest': resolver.lookup('DGIntimidateQuest', ['QUST'])[0],
           'victory_dial': resolver.lookup('DGIntimidateVictoryYes', ['DIAL'])[0]}
    for key, edid in DIFFICULTIES.items():
        out[key] = resolver.lookup(edid, ['GLOB'])[0]
    return out


def expand(challenge, prompt, lines, script, f):
    """(prompt, variants) for an entry. variants is a list of {lines, script, extra, goodbye} in the order the
    responses are tried. `script` is the entry's own (code, [(name, form id)]) or None; `f` comes from forms()."""
    kind, amount = kind_of(challenge), challenge.get('amount')
    code, props = (script[0], list(script[1])) if script else ('', [])
    extra, goodbye, tag, run = [], False, '', []
    if kind == 'persuade':
        tag = '(Persuade)'
        luck = int(challenge.get('luck', 0))
        extra.append((GET_ACTOR_VALUE, -SPEECHCRAFT, GE, 0.0, TARGET, 0,
                      f[challenge.get('difficulty', 'average')], bool(luck)))
        if luck:
            extra.append((GET_RANDOM_PERCENT, 0, LE, float(luck), SUBJECT))
        run, props = [f"{FAVOR}.Persuade(akSpeaker)"], props + [('FavorQuest', f['favor'])]
    elif kind == 'skill':
        skill, rank = skill_of(challenge), int(challenge['rank'])
        tag, luck = f"({skill} {rank})", int(challenge.get('luck', 0))
        extra.append((GET_ACTOR_VALUE, -(6 + SKILLS.index(skill)), GE, float(rank), TARGET, 0, None, bool(luck)))
        if luck:
            extra.append((GET_RANDOM_PERCENT, 0, LE, float(luck), SUBJECT))
    elif kind == 'intimidate':
        tag = '(Intimidate)'
        extra.append((GET_INTIMIDATE_SUCCESS, 0, EQ, 1.0, SUBJECT))
        run, props = [f"{FAVOR}.Intimidate(akSpeaker)"], props + [('FavorQuest', f['favor'])]
    else:
        if amount:
            tag = f"({amount} gold)" if kind == 'price' else f"(Brawl - {amount} gold)"
            extra.append((GET_ITEM_COUNT, f['gold'], GE, float(amount), TARGET))
            run, props = [PAY.format(n=amount)], props + [('Gold', f['gold'])]
        else:
            tag = '(Brawl)'
        if kind == 'brawl':
            run.append(f"{FAVOR}.Brawl(akSpeaker, None)")
            props, goodbye = props + [('FavorQuest', f['favor'])], True
    shown = prompt if tag.lower() in (prompt or '').lower() else f"{prompt} {tag}"
    win_code = "\n".join(run + ([code] if code else []))
    win = {'lines': lines, 'script': (win_code, _unique(props)) if win_code else None, 'extra': extra,
           'goodbye': goodbye}
    variants = [win]
    fail = challenge.get('failure')
    if fail:
        fail_run, fail_props = [], []
        if kind == 'intimidate' and challenge.get('brawl_on_failure'):
            fail_run, fail_props = [f"{FAVOR}.Brawl(akSpeaker, None)"], [('FavorQuest', f['favor'])]
        variants.append({'lines': list(fail['lines']), 'extra': [],
                         'script': ("\n".join(fail_run), fail_props) if fail_run else None,
                         'goodbye': bool(fail.get('goodbye', kind == 'intimidate' and
                                                  challenge.get('brawl_on_failure', False)))})
    return shown, variants


def victory(challenge, f):
    """The reply that ends a won brawl, or None for non-brawls: {prompt, lines, script}.

    Vanilla ends the fight through DGIntimidateVictoryYes, which only answers the NPCs it names, so a custom NPC
    needs its own reply there or the fight never finishes and she stays down and silent."""
    if kind_of(challenge) != 'brawl' and not _fights(challenge):
        return None
    custom, amount = challenge.get('victory') or {}, challenge.get('amount')
    code = ["(DGIntimidateQuest as Quest).SetStage(100)"]
    props = [('DGIntimidateQuest', f['brawl_quest'])]
    if amount:
        code.insert(0, f"Game.GetPlayer().AddItem(Gold as MiscObject, {amount * 2})")
        props.append(('Gold', f['gold']))
    return {'dial': f['victory_dial'], 'prompt': custom.get('prompt', VICTORY_PROMPT),
            'lines': custom.get('lines') or (VICTORY_PAID if amount else VICTORY_LINES),
            'script': ("\n".join(code), props)}


def _fights(challenge):
    """True when failing the check starts a brawl."""
    return kind_of(challenge) == 'intimidate' and bool(challenge.get('brawl_on_failure'))


def _unique(props):
    """Drop repeated property names (the same form listed twice)."""
    seen, out = set(), []
    for name, fid in props:
        if name not in seen:
            seen.add(name)
            out.append((name, fid))
    return out
