"""Build a custom NPC (plus optional outfit, placed reference and dialogue) into a new ESP.

An NPC is described by a plain `spec` dict (see FIELDS). `NpcBuilder.validate` resolves every
reference against the loaded Model and reports errors/warnings; `NpcBuilder.write` builds the
plugin with esplib and saves it. FormIDs are written with their final load-order index, so all
masters are decided before any record is created.
"""
import math
import re
import shutil
import struct
from pathlib import Path

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.npcs.commentary_presets import (main_quest_entries, place_entries, preset_keys, time_entries,
                                           weather_entries)
from tools.npcs.npc_tools import AGGRESSION, ASSISTANCE, CONFIDENCE, MORALITY, SKILLS
from tools.papyrus.papyrus_build import (compile_script, info_vmad, keep_up_source, npc_script_vmad, script_name,
                                         tif_source)
from tools.core.plugin_edit import Target, hi, new_plugin, resolve_target_path

FIELDS = {
    'plugin_name': 'Output file name, e.g. "MyNpc.esp" (".esp" is added if missing).',
    'editor_id': 'Unique editor ID of the new NPC_ record (required).',
    'name': 'In-game display name (required).',
    'short_name': 'Optional short name.',
    'clone_from': 'Existing NPC (editor ID, name, or form ID) to copy as the starting point.',
    'keep_scripts': 'Clone mode only: keep the source NPC VMAD scripts (default false).',
    'facegen': 'Clone mode only: copy the source NPC FaceGen mesh/tint as loose files for the new '
               'form ID (default true; skipped when appearance_from is set).',
    'appearance_from': 'NPC whose face/traits to inherit through a Traits template (avoids missing '
                       'FaceGen on a new form ID).',
    'race': 'Race (required unless cloning).',
    'voice_type': 'Voice type (VTYP).',
    'npc_class': 'Class (CLAS).',
    'hair_color': 'Hair color (CLFM), e.g. "HairColor12BlackTrue". Ignored by the game if a Traits '
                  'template (appearance_from) is used.',
    'combat_style': 'Combat style (CSTY).',
    'death_item': 'Death item (leveled item).',
    'female': 'true/false.',
    'essential': 'true/false.',
    'protected': 'true/false.',
    'unique': 'true/false (default true when building from scratch).',
    'respawn': 'true/false.',
    'level': 'Fixed level (1-65535).',
    'level_mult': 'Player-level multiplier; sets the PC Level Mult flag (overrides level).',
    'calc_min_level': 'Minimum level for level-scaled NPCs.',
    'calc_max_level': 'Maximum level for level-scaled NPCs (0 = unlimited).',
    'speed_multiplier': 'Speed multiplier percent (default 100).',
    'disposition_base': 'Base disposition (default 35).',
    'health_offset': 'Health offset (ACBS).',
    'magicka_offset': 'Magicka offset (ACBS).',
    'stamina_offset': 'Stamina offset (ACBS).',
    'health': 'Base health (DNAM).',
    'magicka': 'Base magicka (DNAM).',
    'stamina': 'Base stamina (DNAM).',
    'skills': 'Object of skill name -> value 0-100, e.g. {"OneHanded": 50}.',
    'height': 'Height multiplier (default 1.0).',
    'weight': 'Weight 0-100 (default 50).',
    'aggression': 'Unaggressive/Aggressive/Very Aggressive/Frenzied or 0-3.',
    'confidence': 'Cowardly/Cautious/Average/Brave/Foolhardy or 0-4.',
    'energy': 'AI energy level 0-100.',
    'morality': 'Any Crime/Violence Against Enemies/Property Crime Only/No Crime or 0-3.',
    'mood': 'AI mood 0-7.',
    'assistance': 'Helps Nobody/Helps Allies/Helps Friends and Allies or 0-2.',
    'factions': 'List of faction identifiers or {"faction": ..., "rank": n}; replaces the cloned list.',
    'spells': 'List of spell/shout identifiers; replaces the cloned list.',
    'perks': 'List of perk identifiers or {"perk": ..., "rank": n}; replaces the cloned list.',
    'keywords': 'List of keyword identifiers; replaces the cloned list.',
    'inventory': 'List of item identifiers or {"item": ..., "count": n}; replaces the cloned list.',
    'add_inventory': 'Same format as inventory, but appends to the NPC\'s existing items instead of '
                     'replacing them (edit_npc only).',
    'packages': 'List of AI package identifiers; replaces the cloned list.',
    'outfit': 'Either an existing outfit identifier, or a list of armor/leveled-item identifiers '
              'for a new OTFT record.',
    'placement': 'Object: {"cell": id or name} or {"worldspace", "grid_x", "grid_y"}, plus x, y, z, '
                 'rotation_z (degrees), persistent (default true for interiors).',
    'dialogue': 'List of topics: {"kind": "topic"|"greeting"|"goodbye"|"agree"|"refuse"|"show", "prompt": player text, '
                '"lines": [NPC lines spoken in sequence as one reply], or instead "answers": [[lines], [lines], ...] '
                'for several alternative replies to the same prompt with "pick": "random" (default) or '
                '"in_order" (each once, last repeats; give_items and code run after whichever answer plays), optional "give_items": [item id or {"item", "count"}] given to the '
                'player when the line ends, optional "code": Papyrus statements run then, with '
                '"properties": {name: form identifier} usable in the code as Form properties}. '
                'Scripts are compiled with the Creation Kit compiler into the output Scripts folder. '
                'Optional "blocking": true (prompt-less topic whose branch blocks other topics while its '
                'conditions hold) and "child_of": index of an earlier topic entry (offered only after its reply).',
    'follower': 'Hirelable follower: {"cost": gold (default 50)} or true. Adds dialogue to hire the NPC for '
                'that gold (using the vanilla follower quest), plus wait, follow and dismiss topics. '
                'Dialogue entries also accept "conditions": [{"function": GetItemCount|GetInFaction|'
                'GetGlobalValue|GetStage|GetStageDone, "form": id, "op": ==|!=|>|>=|<|<=, "value": n, '
                '"param2": integer second parameter (the stage for GetStageDone), '
                '"run_on": "subject"|"target"}] (target is the player).',
    'combat_lines': 'true adds engine-spoken combat reactions (attack, hit grunts, taunts, flee, bleedout, '
                    'death, combat/alert transitions) with several random lines each. Also available as '
                    'dialogue kinds: attack, power_attack, bash, hit, flee, bleedout, death, block, taunt, '
                    'normal_to_combat, normal_to_alert, alert_to_combat, combat_to_normal.',
    'ambient_lines': 'true adds engine-spoken ambient barks any NPC can have: noticing a corpse, watching a fight '
                     'and reacting to the player shouting (kinds notice_corpse, observe_combat, shout_reaction).',
    'travel_lines': 'true adds idle comments (kind idle) a hired companion makes on the road, in caves, ruins, '
                    'camps and cities, conditioned on CurrentHireling. Implied by follower.',
    'weather_lines': 'true adds hired-companion idles for rain and snow (outdoors only).',
    'time_lines': 'true adds hired-companion idles for dawn, dusk, night and the small hours (outdoors only).',
    'main_quest_lines': 'true adds hired-companion idles for main-quest milestones (MQ101, MQ103, MQ104, MQ105, '
                        'MQ201, MQ306), each shown between completing that quest and the next.',
    'place_lines': 'true adds hired-companion idles for inns, shops, temples, player houses, Orc strongholds, '
                   'dragon lairs and settlements (LocationHasKeyword).',
    'commentary_text': 'Optional {key: [lines]} replacing the preset lines of weather_lines, time_lines, '
                       'main_quest_lines and place_lines. Keys: rain, snow, dawn, dusk, night, small_hours, '
                       'MQ101, MQ103, MQ104, MQ105, MQ201, MQ306 and the LocType* keyword editor IDs.',
    'quest_editor_id': 'Editor ID of the dialogue quest (default <editor_id>DialogueQuest).',
    'keep_up': 'Hired-follower catch-up: true or {"distance": game units, default 3000}. Attaches an Actor script '
               'that teleports him next to the player when he falls further behind while in CurrentHireling, not '
               'in combat and not waiting. Needs the Creation Kit compiler.',
    'alias_name': 'Name of the quest alias that is filled with the NPC (default "Speaker").',
}

LIST_FIELDS = ('factions', 'spells', 'perks', 'keywords', 'inventory', 'packages')
BOOL_FLAGS = {'female': 0x1, 'essential': 0x2, 'respawn': 0x8, 'unique': 0x20, 'protected': 0x800}
ITEM_SIGS = ['WEAP', 'ARMO', 'ALCH', 'BOOK', 'MISC', 'AMMO', 'INGR', 'KEYM', 'SCRL', 'SLGM', 'APPA', 'LVLI']
REF_SIGS = {
    'race': ['RACE'], 'voice_type': ['VTYP'], 'npc_class': ['CLAS'], 'combat_style': ['CSTY'],
    'death_item': ['LVLI'], 'faction': ['FACT'], 'spell': ['SPEL', 'SHOU', 'LVSP'], 'perk': ['PERK'],
    'keyword': ['KYWD'], 'package': ['PACK'], 'item': ITEM_SIGS, 'outfit_item': ['ARMO', 'LVLI'],
    'outfit': ['OTFT'], 'npc': ['NPC_'], 'hair_color': ['CLFM'],
}
SKILL_ALIASES = {'archery': 'Marksman', '1h': 'OneHanded', '2h': 'TwoHanded'}

# Canonical NPC_ subrecord order; subrecords sharing a rank keep their relative order.
NPC_ORDER = ['EDID', 'VMAD', 'OBND', 'ACBS', 'SNAM', 'INAM', 'VTCK', 'TPLT', 'RNAM', 'SPCT', 'SPLO',
             'WNAM', 'ANAM', 'ATKR', 'ATKD', 'ATKE', 'SPOR', 'OCOR', 'GWOR', 'ECOR', 'PRKZ', 'PRKR',
             'COCT', 'CNTO', 'COED', 'AIDT', 'PKID', 'KSIZ', 'KWDA', 'CNAM', 'FULL', 'SHRT', 'DATA',
             'DNAM', 'PNAM', 'HCLF', 'ZNAM', 'GNAM', 'NAM5', 'NAM6', 'NAM7', 'NAM8', 'DOFT', 'SOFT',
             'DPLT', 'CRIF', 'FTST', 'QNAM', 'NAM9', 'NAMA']
NPC_RANK = {s: i for i, s in enumerate(NPC_ORDER)}
NPC_RANK['COED'] = NPC_RANK['CNTO']
NPC_RANK['ATKE'] = NPC_RANK['ATKD']

GET_IS_ID = 72
CONDITION_FUNCTIONS = {'GetItemCount': 47, 'GetStage': 58, 'GetInFaction': 71, 'GetGlobalValue': 74,
                       'GetActorValue': 14, 'IsDoingFavor': 635, 'GetInCurrentLoc': 359,
                       'GetInWorldspace': 310, 'LocationHasKeyword': 562, 'GetEquipped': 182,
                       'GetStageDone': 59, 'GetDistance': 1, 'IsSneaking': 286, 'GetRandomPercent': 77,
                       'IsInInterior': 300,
                       # state of the speaker or player
                       'GetDayOfWeek': 170, 'GetSitting': 159, 'IsInCombat': 289, 'IsTalking': 141,
                       'IsInDialogueWithPlayer': 249, 'IsRaining': 62, 'IsSnowing': 75, 'GetCurrentTime': 18,
                       'GetHealthPercentage': 430, 'IsRidingMount': 327, 'GetDisabled': 35,
                       'IsTrespassing': 145, 'IsMoving': 25, 'GetDead': 46, 'IsWeaponOut': 263, 'GetLevel': 80,
                       # take a form: quest, race, faction, voice type, spell, perk, cell, furniture, scene
                       'GetQuestRunning': 56, 'GetQuestCompleted': 543, 'GetIsRace': 69, 'GetFactionRank': 73,
                       'GetIsVoiceType': 426, 'HasSpell': 264, 'HasPerk': 448, 'GetInCell': 67,
                       'IsCurrentFurnitureObj': 163, 'IsScenePlaying': 248, 'GetInSameCell': 32}
NO_PARAM_FUNCTIONS = {'IsDoingFavor', 'IsSneaking', 'GetRandomPercent', 'IsInInterior', 'GetDayOfWeek',
                      'GetSitting', 'IsInCombat', 'IsTalking', 'IsInDialogueWithPlayer', 'IsRaining', 'IsSnowing',
                      'GetCurrentTime', 'GetHealthPercentage', 'IsRidingMount', 'GetDisabled', 'IsTrespassing',
                      'IsMoving', 'GetDead', 'IsWeaponOut', 'GetLevel'}
REFERENCE_FUNCTIONS = {'GetDistance', 'GetInSameCell'}  # param is a placed reference ('Skyrim.esm:013487')
WAITING_FOR_PLAYER = 0x5F  # actor value the vanilla follower script sets to 1 on FollowerWait
CONDITION_OPS = {'==': 0, '!=': 1, '>': 2, '>=': 3, '<': 4, '<=': 5}
RUN_ON = {'subject': 0, 'target': 1}


def follower_entries(cost, base=0):
    """Dialogue entries that make an NPC a cheap hireling through the vanilla DialogueFollower quest.
    base is the index of the first returned entry in the NPC's dialogue list (for child_of links)."""
    blocking = base + 7  # position of the blocking favor topic below
    hire = f"Would you travel with me? ({cost} gold)"
    props = {'FollowerQuest': 'DialogueFollower', 'HirelingFaction': 'CurrentHireling', 'Gold': 'Gold001'}
    hired = {'function': 'GetInFaction', 'form': 'CurrentHireling', 'op': '==', 'value': 1}
    free = dict(hired, value=0)
    waiting = lambda v: {'function': 'GetActorValue', 'actor_value': WAITING_FOR_PLAYER, 'op': '==', 'value': v}
    manager = "(FollowerQuest as Quest) as DialogueFollowerScript"
    favor = lambda v: {'function': 'IsDoingFavor', 'op': '==', 'value': v}
    return [
        {'prompt': hire, 'lines': [f"For {cost} gold I'll watch your back. Lead the way!"],
         'properties': props, 'conditions': [free, {'function': 'GetItemCount', 'form': 'Gold001',
                                                    'op': '>=', 'value': cost, 'run_on': 'target'}],
         'code': f"Game.GetPlayer().RemoveItem(Gold as MiscObject, {cost})\n"
                 f"akSpeaker.AddToFaction(HirelingFaction as Faction)\n"
                 f"({manager}).SetFollower(akSpeaker)"},
        {'prompt': hire, 'lines': [f"I'd love to, but a man's got to eat. {cost} gold and I'm yours."],
         'conditions': [free, {'function': 'GetItemCount', 'form': 'Gold001', 'op': '<',
                               'value': cost, 'run_on': 'target'}]},
        {'prompt': "Wait here.", 'lines': ["I'll be right here."], 'properties': props, 'goodbye': True,
         'conditions': [hired, waiting(0)], 'code': f"({manager}).FollowerWait()"},
        {'prompt': "Follow me.", 'lines': ["Right behind you."], 'properties': props, 'goodbye': True,
         'conditions': [hired, waiting(1)], 'code': f"({manager}).FollowerFollow()"},
        {'prompt': "I don't need you anymore.", 'lines': ["Understood. It was a pleasure."],
         'properties': props, 'conditions': [hired], 'goodbye': True,
         'code': f"({manager}).DismissFollower(0, 0)"},
        {'prompt': "Trade some items.", 'lines': ["Sure, let's see what you've got."],
         'conditions': [hired], 'code': "akSpeaker.OpenInventory(true)"},
        # vanilla favor flow (these INFOs end the conversation): start a favor; while it is active a
        # blocking topic offers continue or end
        {'prompt': "I need you to do something.", 'lines': ["What do you need?"], 'goodbye': True,
         'conditions': [hired, favor(0)], 'code': "akSpeaker.SetDoingFavor(true)"},
        {'blocking': True, 'lines': ["Still need me to do something?"], 'conditions': [hired, favor(1)]},
        {'prompt': "Yes. Let me show you.", 'child_of': blocking, 'goodbye': True,
         'answers': [["Let's see it."], ["Point the way."], ["Let us see it, then."], ["Right, then."]]},
        {'prompt': "No. Never mind.", 'child_of': blocking, 'goodbye': True,
         'code': "akSpeaker.SetDoingFavor(false)",
         'answers': [["All right."], ["As you wish."], ["Very well."], ["Fine."], ["Your call."]]},
        # engine-spoken reactions while doing a favor: accepting, refusing and showing a target
        {'kind': 'agree', 'conditions': [hired], 'answers': [["Got it."], ["Right."], ["On it."]]},
        {'kind': 'refuse', 'conditions': [hired], 'answers': [["I can't do that."], ["Not going to happen."]]},
        {'kind': 'show', 'conditions': [hired], 'answers': [["Here, take a look."], ["Found something."]]},
        # a travelling companion's hello (the normal greetings are hidden while he is hired)
        {'kind': 'greeting', 'conditions': [hired], 'answers': [
            ["Yes? What's the plan?"], ["Where to next?"], ["Ready when you are."], ["Lead on."],
            ["What do you need?"], ["Still with you."]]},
        {'kind': 'goodbye', 'conditions': [hired], 'answers': [
            ["Lead on."], ["I'm right behind you."], ["Whenever you're ready."], ["Onward, then."],
            ["I'll keep up."], ["Watch your step."]]},
    ]
SUBTYPES = {'topic': (b'CUST', b'\x00\x00\x00\x00'), 'greeting': (b'HELO', b'\x00\x07\x49\x00'),
            'goodbye': (b'GBYE', b'\x00\x07\x48\x00'),
            # spoken by the engine when a follower is given a favor target (vanilla DialogueFollower subtypes)
            'agree': (b'AGRE', b'\x00\x04\x10\x00'), 'refuse': (b'REFU', b'\x00\x04\x11\x00'),
            'show': (b'SHOW', b'\x00\x04\x0f\x00'),
            # combat reactions the engine speaks on its own (vanilla DialogueGeneric subtypes)
            'attack': (b'ATCK', b'\x00\x03\x14\x00'), 'power_attack': (b'POAT', b'\x00\x03\x15\x00'),
            'bash': (b'BASH', b'\x00\x03\x16\x00'), 'hit': (b'HIT_', b'\x00\x03\x17\x00'),
            'flee': (b'FLEE', b'\x00\x03\x18\x00'), 'bleedout': (b'BLED', b'\x00\x03\x19\x00'),
            'death': (b'DETH', b'\x00\x03\x1b\x00'), 'block': (b'BLOC', b'\x00\x03\x1d\x00'),
            'taunt': (b'TAUT', b'\x00\x03\x1e\x00'), 'normal_to_combat': (b'NOTC', b'\x00\x05\x35\x00'),
            'normal_to_alert': (b'NOTA', b'\x00\x05\x33\x00'), 'alert_to_combat': (b'ALTC', b'\x00\x05\x34\x00'),
            'combat_to_normal': (b'COTN', b'\x00\x05\x37\x00'),
            # ambient barks the engine speaks on its own (vanilla DialogueGeneric / DialogueFollower subtypes)
            'idle': (b'IDLE', b'\x00\x07\x58\x00'), 'notice_corpse': (b'NOTI', b'\x00\x07\x46\x00'),
            'observe_combat': (b'OBCO', b'\x00\x07\x45\x00'), 'shout_reaction': (b'PCSH', b'\x00\x07\x57\x00')}
INFO_IDLE_RESET = 0x0555  # reset-hours value vanilla uses on its follower idle comments
INFO_RANDOM, INFO_SAY_ONCE = 0x2, 0x4
COMBAT_LINES = {
    'normal_to_combat': ["You'll regret this!", "Here we go!", "Draw your weapon!"],
    'alert_to_combat': ["I knew it!", "There you are!"],
    'normal_to_alert': ["Who's there?", "I heard something."],
    'combat_to_normal': ["That's that, then.", "All clear."],
    'attack': ["Hyah!", "Take that!", "Have at you!", "Ha!"],
    'power_attack': ["Yaaah!", "Feel this!"],
    'bash': ["Back off!", "Get away from me!"],
    'block': ["Not today!", "Nice try!"],
    'hit': ["Argh!", "Ugh!", "That hurt!", "Agh!"],
    'taunt': ["Is that all you've got?", "You call that a fight?", "I've seen children fight better!"],
    'flee': ["I'm getting out of here!", "Run!"],
    'bleedout': ["I yield! I yield!", "Help me!"],
    'death': ["Aaargh...", "No... not like this..."],
}


def combat_entries():
    """Always-visible combat reaction lines, spoken by the engine like vanilla's generic combat dialogue."""
    return [{'kind': k, 'answers': [[x] for x in lines]} for k, lines in COMBAT_LINES.items()]


AMBIENT_LINES = {
    'notice_corpse': ["Gods, look at that.", "Someone's dead here.", "Poor soul."],
    'observe_combat': ["Someone's fighting over there.", "Keep your distance.", "That looks bad."],
    # vanilla's voice-neutral fallbacks (DialogueGeneric PCSH) plus a few that fit any voice
    'shout_reaction': ["What was that?", "How did you do that?", "By the gods, that voice!",
                       "Dragonborn!", "Not so loud!"],
}
# location keyword -> comments a travelling companion makes while the player is there
TRAVEL_KEYWORD_LINES = {
    'LocSetCave': ["Mind your head in here.", "It's cold and damp down here.", "I don't like caves much."],
    'LocSetNordicRuin': ["These halls are ancient.", "Keep an eye out for draugr."],
    'LocSetDwarvenRuin': ["Dwemer ruins. Watch for traps.", "Who built all this, I wonder?"],
    'LocTypeDraugrCrypt': ["The dead don't rest easy here.", "I don't like the look of these tombs."],
    'LocTypeBanditCamp': ["Bandits. Stay sharp.", "Probably more of them around."],
    'LocTypeMine': ["Hard work, mining.", "Smells of dust and sweat in here."],
    'LocTypeMilitaryFort': ["This fort has seen better days.", "Someone's been using this place."],
    'LocTypeVampireLair': ["Keep your wits about you. Vampires."],
    'LocTypeWarlockLair': ["Wizards. I'd rather not tangle with them."],
    'LocTypeForswornCamp': ["Forsworn. They won't take kindly to us."],
    'LocTypeDwarvenAutomatons': ["Those machines are still running. Careful."],
}
TRAVEL_CITY_LINES = {
    'WhiterunLocation': ["Whiterun. Always busy."],
    'WindhelmLocation': ["Windhelm is bitter cold."],
    'SolitudeLocation': ["Solitude. Quite the sight."],
    'MarkarthLocation': ["Markarth. Watch your step here."],
    'RiftenLocation': ["Riften. Keep a hand on your purse."],
    'FalkreathLocation': ["Falkreath. Quiet, isn't it?"],
}
TRAVEL_ROAD_LINES = ["Good to be on the road again.", "Where are we headed next?",
                     "Fine day for walking.", "Long way to go yet."]


def travel_entries(hired):
    """Idle comments a hired companion makes on the road and in caves, ruins, camps and cities."""
    def entry(cond, lines):
        return {'kind': 'idle', 'conditions': [hired, cond], 'answers': [[x] for x in lines]}
    out = [{'kind': 'idle', 'conditions': [hired], 'answers': [[x] for x in TRAVEL_ROAD_LINES]}]
    out += [entry({'function': 'LocationHasKeyword', 'form': k, 'op': '==', 'value': 1}, v)
            for k, v in TRAVEL_KEYWORD_LINES.items()]
    out += [entry({'function': 'GetInCurrentLoc', 'form': k, 'op': '==', 'value': 1}, v)
            for k, v in TRAVEL_CITY_LINES.items()]
    return out


def ambient_entries():
    """Engine-spoken reactions to a dead body or a nearby fight, the default bark any NPC can have."""
    return [{'kind': k, 'answers': [[x] for x in lines]} for k, lines in AMBIENT_LINES.items()]
INFO_GOODBYE = 0x1
PICK_MODES = ('random', 'in_order')


def answer_flags(pick, index, count):
    """INFO ENAM flags for answer `index` of `count`. random: any answer may play each time.
    in_order: each answer plays once in order, and the last one repeats."""
    if count < 2:
        return 0
    if pick == 'in_order':
        return INFO_SAY_ONCE if index < count - 1 else 0
    return INFO_RANDOM

_cell_plugins = {}


def z(text):
    """Null-terminated Windows-1252 bytes, the encoding of non-localized plugin strings."""
    return text.encode('cp1252') + b'\0'


def _patch_nif_blocks(data, old_plugin, new_plugin, old_id, new_id):
    """Rewrite length-prefixed texture paths stored inside NIF blocks, fixing block sizes."""
    pos = data.index(b'\n') + 1 + 4 + 1 + 4
    nb = struct.unpack_from('<I', data, pos)[0]
    pos += 8
    for _ in range(3):
        pos += 1 + data[pos]
    ntypes = struct.unpack_from('<H', data, pos)[0]
    pos += 2
    for _ in range(ntypes):
        pos += 4 + struct.unpack_from('<I', data, pos)[0]
    pos += 2 * nb
    sizes_off = pos
    sizes = list(struct.unpack_from(f'<{nb}I', data, pos))
    pos += 4 * nb
    nstr = struct.unpack_from('<I', data, pos)[0]
    pos += 8
    for _ in range(nstr):
        pos += 4 + struct.unpack_from('<I', data, pos)[0]
    pos += 4 + 4 * struct.unpack_from('<I', data, pos)[0]

    path_re = re.compile(rb'data\\[\x20-\x7e]*?' + re.escape(old_id.encode()) + rb'[\x20-\x7e]*?\.dds', re.I)
    hdr_end = pos
    blocks = []
    for size in sizes:
        blocks.append(data[pos:pos + size])
        pos += size
    tail = data[pos:]
    for i, blk in enumerate(blocks):
        def fix(m):
            text = m.group(0)
            return re.sub(re.escape(old_plugin.encode()), new_plugin.encode(),
                          re.sub(old_id.encode(), new_id.encode(), text, flags=re.I), flags=re.I)

        out, last = bytearray(), 0
        for m in path_re.finditer(blk):
            if m.start() < 4 or struct.unpack_from('<I', blk, m.start() - 4)[0] != len(m.group(0)):
                continue
            new = fix(m)
            out += blk[last:m.start() - 4] + struct.pack('<I', len(new)) + new
            last = m.end()
        if last:
            blocks[i] = bytes(out) + blk[last:]
            sizes[i] = len(blocks[i])
    head = data[:sizes_off] + struct.pack(f'<{nb}I', *sizes) + data[sizes_off + 4 * nb:hdr_end]
    return head + b''.join(blocks) + tail


def retarget_nif(data, old_plugin, new_plugin, old_id, new_id):
    """Rewrite plugin/form ID text in a NIF: header string table and texture paths in blocks."""
    data = _patch_nif_blocks(data, old_plugin, new_plugin, old_id, new_id)
    pat = re.compile(re.escape(old_plugin) + '|' + re.escape(old_id), re.I)

    def sub(s):
        return pat.sub(lambda m: new_id if m.group(0).lower() == old_id.lower() else new_plugin, s)

    def parse(start, count):
        pos, out = start, []
        for _ in range(count):
            if pos + 4 > len(data):
                return None
            n = struct.unpack_from('<I', data, pos)[0]
            if n > 1024 or pos + 4 + n > len(data):
                return None
            out.append(data[pos + 4:pos + 4 + n])
            pos += 4 + n
        return out, pos

    first = data.lower().find(old_id.lower().encode())
    for k in range(first, 8, -1):
        count, maxlen = struct.unpack_from('<II', data, k - 8)
        if not 0 < count < 4096 or not 0 < maxlen < 1024:
            continue
        got = parse(k, count)
        if got and max(len(s) for s in got[0]) == maxlen and any(old_id.encode().lower() in s.lower() for s in got[0]):
            strings, end = got
            new_strings = [sub(s.decode('latin-1')).encode('latin-1') for s in strings]
            table = b''.join(struct.pack('<I', len(s)) + s for s in new_strings)
            head = struct.pack('<II', count, max(len(s) for s in new_strings))
            return data[:k - 8] + head + table + data[end:]
    raise ValueError("could not locate the NIF string table")


def set_nif_hair_tint(data, rgb):
    """Set the hair tint color (shader type 6) in every BSLightingShaderProperty of a FaceGen NIF.

    Returns (new bytes, number of shaders changed). rgb is three 0-255 ints. Only floats are
    overwritten, so block sizes are unchanged.
    """
    buf = bytearray(data)
    i = buf.index(b'\n') + 1 + 1
    _, _, nb, _ = struct.unpack_from('<IIII', buf, i)
    i += 16
    for _ in range(3):
        i += 1 + buf[i]
    (nt,) = struct.unpack_from('<H', buf, i)
    i += 2
    types = []
    for _ in range(nt):
        (n,) = struct.unpack_from('<I', buf, i)
        types.append(bytes(buf[i + 4:i + 4 + n]).decode())
        i += 4 + n
    idx = struct.unpack_from('<%dH' % nb, buf, i)
    i += 2 * nb
    sizes = struct.unpack_from('<%dI' % nb, buf, i)
    i += 4 * nb
    (ns,) = struct.unpack_from('<I', buf, i)
    i += 8
    for _ in range(ns):
        (n,) = struct.unpack_from('<I', buf, i)
        i += 4 + n
    i += 4
    changed = 0
    for b in range(nb):
        if types[idx[b]] == 'BSLightingShaderProperty' and struct.unpack_from('<I', buf, i)[0] == 6:
            (extra,) = struct.unpack_from('<I', buf, i + 8)
            # fixed 100-byte shader body (verified on Sven's FaceGen), plus any extra-data refs
            tint = i + 100 + 4 * extra
            struct.pack_into('<3f', buf, tint, *(c / 255 for c in rgb))
            changed += 1
        i += sizes[b]
    return bytes(buf), changed


def fid_bytes(value):
    """Pack a form ID as a little-endian uint32 subrecord payload."""
    return struct.pack('<I', value)


def enum_value(value, names, label, errors):
    """Map a name (case-insensitive) or index to its index in names; appends to errors and returns 0 if invalid."""
    if isinstance(value, str):
        low = value.strip().lower()
        for i, n in enumerate(names):
            if n.lower() == low:
                return i
        errors.append(f"{label}: {value!r} is not one of {list(names)}")
        return 0
    if isinstance(value, int) and 0 <= value < len(names):
        return value
    errors.append(f"{label}: {value!r} is out of range 0-{len(names) - 1}")
    return 0


class Resolver:
    """Look up records in the Model by editor ID, name, form ID or 'plugin.esp:hexid'."""

    def __init__(self, model):
        self.m = model

    def pool(self, sigs):
        """Yield (absolute form ID, record) for every record of the given signatures."""
        for sig in sigs:
            yield from self.m.by_sig[sig].items()

    def lookup(self, query, sigs):
        """Return (absolute id, record). Raises ValueError with a helpful message."""
        q = str(query).strip()
        if not q:
            raise ValueError("empty identifier")
        pool = list(self.pool(sigs))
        file_part, _, hexpart = q.rpartition(':')
        if file_part and re.fullmatch(r'[0-9a-fA-F]{1,8}', hexpart):
            slot = self.m.slots.get(file_part.lower())
            if slot is not None:
                fid = (slot << 24) | (int(hexpart, 16) & 0xFFFFFF)
                if fid in dict(pool):
                    return fid, self.m.records[fid]
            raise ValueError(f"{q!r} not found among {'/'.join(sigs)} records")
        if q.lower().startswith('0x') or re.fullmatch(r'[0-9a-fA-F]{8}', q):
            raw = int(q, 16)
            by_id = dict(pool)
            if raw in by_id:
                return raw, by_id[raw]
            hits = [(f, r) for f, r in pool if f & 0xFFFFFF == raw & 0xFFFFFF]
            if len(hits) == 1:
                return hits[0]
            if hits:
                raise ValueError(f"form ID {q} is ambiguous: " + ', '.join(f"{f:08X}" for f, _ in hits[:8]))
        low = q.lower()
        for test in (lambda r: (r.editor_id or '').lower() == low,
                     lambda r: (r.full_name or '').lower() == low,
                     lambda r: low in (r.editor_id or '').lower() or low in (r.full_name or '').lower()):
            hits = [(f, r) for f, r in pool if test(r)]
            if len(hits) == 1:
                return hits[0]
            if hits:
                names = ', '.join(f"{r.editor_id or r.full_name} [{f:08X}]" for f, r in hits[:8])
                raise ValueError(f"{q!r} is ambiguous ({len(hits)} matches): {names}")
        raise ValueError(f"no {'/'.join(sigs)} record matches {q!r}")

    def search(self, sigs, query, limit=25):
        """Substring match on editor ID or name; returns dicts sorted by editor ID, capped at limit."""
        low = (query or '').strip().lower()
        out = []
        for fid, r in self.pool(sigs):
            if not low or low in (r.editor_id or '').lower() or low in (r.full_name or '').lower():
                out.append({'form_id': f"{fid:08X}", 'type': r.signature, 'editor_id': r.editor_id,
                            'name': r.full_name, 'plugin': r.plugin.file_path.name})
        out.sort(key=lambda d: (d['editor_id'] or '').lower())
        return out[:limit]


class CellFinder:
    """Locate CELL records (and the group chain that holds them) in the loaded plugins."""

    def __init__(self, model):
        self.m = model

    def plugins(self):
        """Loaded plugins re-read with only CELL/WRLD groups, cached across calls."""
        out = []
        for pl in self.m.plugins:
            key = str(pl.file_path)
            if key not in _cell_plugins:
                _cell_plugins[key] = Plugin.load_cached(key, only_signatures={'CELL', 'WRLD'})
            out.append(_cell_plugins[key])
        return out

    @staticmethod
    def walk(group, chain, match):
        """Depth-first search for a CELL accepted by match(cell, chain); returns (chain, cell) or None."""
        prev = None
        for item in group.records:
            if isinstance(item, GroupRecord):
                hit = CellFinder.walk(item, chain + [(item.group_type, item.label,
                                                      prev if item.group_type == 1 else None)], match)
                if hit:
                    return hit
            else:
                prev = item
                if item.signature == 'CELL' and match(item, chain):
                    return chain, item
        return None

    @staticmethod
    def grid(rec):
        """(x, y) from the cell's XCLC, or None for interiors without a grid."""
        sr = rec.get_subrecord('XCLC')
        return struct.unpack_from('<ii', sr.data) if sr is not None and len(sr.data) >= 8 else None

    def find(self, cell=None, worldspace=None, grid_x=None, grid_y=None):
        """Return (plugin, chain, cell record) or raise ValueError."""
        want = str(cell).strip().lower() if cell else None
        wrld = str(worldspace).strip().lower() if worldspace else None

        def match(rec, chain):
            if wrld is not None:
                world = next((p for t, _, p in chain if t == 1 and p is not None), None)
                if world is None or (world.editor_id or '').lower() != wrld:
                    return False
                # The worldspace's persistent cell has no exterior block groups.
                return any(t == 5 for t, _, _ in chain) and self.grid(rec) == (grid_x, grid_y)
            if want is None:
                return False
            if re.fullmatch(r'(0x)?[0-9a-fA-F]{8}', want):
                return (rec.form_id.value & 0xFFFFFF) == (int(want, 16) & 0xFFFFFF)
            return (rec.editor_id or '').lower() == want or (rec.full_name or '').lower() == want

        for pl in reversed(self.plugins()):
            for g in pl.groups:
                hit = self.walk(g, [(g.group_type, g.label, None)], match)
                if hit:
                    return pl, hit[0], hit[1]
        target = f"{worldspace} ({grid_x},{grid_y})" if wrld else cell
        raise ValueError(f"no cell matches {target!r}")

    def persistent(self, worldspace):
        """(plugin, chain, cell) of a worldspace's persistent cell, where persistent exterior refs live."""
        wrld = str(worldspace).strip().lower()

        def match(rec, chain):
            world = next((p for t, _, p in chain if t == 1 and p is not None), None)
            return (world is not None and (world.editor_id or '').lower() == wrld
                    and not any(t == 5 for t, _, _ in chain))

        for pl in reversed(self.plugins()):
            for g in pl.groups:
                hit = self.walk(g, [(g.group_type, g.label, None)], match)
                if hit:
                    return pl, hit[0], hit[1]
        raise ValueError(f"no persistent cell for worldspace {worldspace!r}")

    def search(self, query, limit=25):
        """Find cells whose name or editor ID contains query."""
        low = (query or '').strip().lower()
        out = []

        def visit(group, chain, pl):
            for item in group.records:
                if isinstance(item, GroupRecord):
                    visit(item, chain + [item], pl)
                elif item.signature == 'CELL':
                    nm, ed = item.full_name or '', item.editor_id or ''
                    if low and low not in nm.lower() and low not in ed.lower():
                        continue
                    world = next((c for c in chain if c.group_type == 1), None)
                    out.append({'form_id': f"{item.form_id.value & 0xFFFFFF:06X}", 'editor_id': ed,
                                'name': nm, 'plugin': pl.file_path.name,
                                'grid': self.grid(item) if world is not None else None,
                                'interior': world is None})

        for pl in self.plugins():
            for g in pl.groups:
                if g.group_type == 0 and g.label in ('CELL', 'WRLD'):
                    visit(g, [g], pl)
            if len(out) >= limit * 4:
                break
        return out[:limit]


class Plan:
    """Resolved form of a spec: absolute form IDs and normalised values."""

    def __init__(self):
        self.errors, self.warnings = [], []
        self.refs = {}
        self.clone = None
        self.cell = None
        self.abs_ids = set()


class NpcBuilder:
    """Plans a spec into resolved form IDs, then writes the NPC, placement and dialogue records to a plugin."""

    def __init__(self, model, output_dir, data_dir=None):
        self.m = model
        self.data_dir = Path(data_dir) if data_dir else None
        self.output_dir = Path(output_dir)
        self.resolver = Resolver(model)
        self.cells = CellFinder(model)
        self.names = {}
        for pl in model.plugins:
            self.names[pl.file_path.name.lower()] = pl.file_path.name
            for master in pl.header.masters:
                self.names.setdefault(master.lower(), master)
        self.slot_names = {slot: name for name, slot in model.slots.items()}
        self.target = None

    def check_keys(self, spec):
        return [f"unknown field {k!r}" for k in spec if k not in FIELDS]

    def resolve(self, plan, kind, query, label):
        """Look up a reference of the given kind and return its absolute form ID; 0 if empty or unresolved (error recorded)."""
        if query in (None, ''):
            return 0
        try:
            fid, _ = self.resolver.lookup(query, REF_SIGS[kind])
        except ValueError as e:
            plan.errors.append(f"{label}: {e}")
            return 0
        plan.abs_ids.add(fid)
        return fid

    def plan(self, spec, edit=False, target=None):
        """Validate a spec and resolve its references into a Plan; problems go in plan.errors/warnings, nothing is written."""
        self.slot_names = {slot: name for name, slot in self.m.slots.items()}
        p = Plan()
        p.errors.extend(self.check_keys(spec))
        eid, name = spec.get('editor_id'), spec.get('name')
        if edit and not eid:
            pass
        elif not eid or not re.fullmatch(r'[A-Za-z0-9_]+', str(eid)):
            p.errors.append("editor_id is required and may contain only letters, digits and underscores")
        elif any((r.editor_id or '').lower() == str(eid).lower() for r in self.m.records.values()):
            p.errors.append(f"editor_id {eid!r} already exists in the loaded plugins")
        elif target is not None and any((r.editor_id or '').lower() == str(eid).lower()
                                        for r in target.plugin.records):
            p.errors.append(f"editor_id {eid!r} already exists in {target.name}")
        if not name and not edit:
            p.errors.append("name is required")
        for key in ('name', 'short_name'):
            try:
                z(spec.get(key) or '')
            except UnicodeEncodeError:
                p.errors.append(f"{key} contains characters outside Windows-1252")

        if edit:
            if spec.get('clone_from'):
                p.errors.append("clone_from cannot be used when editing an existing NPC")
        elif spec.get('clone_from'):
            hits = NpcLookup(self.m, self.resolver).find(spec['clone_from'])
            if isinstance(hits, str):
                p.errors.append(f"clone_from: {hits}")
            else:
                p.clone = hits
        elif not spec.get('race'):
            p.errors.append("race is required when not cloning (set clone_from or race)")

        r = p.refs
        for key, kind in (('race', 'race'), ('voice_type', 'voice_type'), ('npc_class', 'npc_class'),
                          ('combat_style', 'combat_style'), ('death_item', 'death_item'),
                          ('appearance_from', 'npc'), ('hair_color', 'hair_color')):
            r[key] = self.resolve(p, kind, spec.get(key), key)
        r['factions'] = [(self.resolve(p, 'faction', e.get('faction') if isinstance(e, dict) else e,
                                       'factions'), int(e.get('rank', 0)) if isinstance(e, dict) else 0)
                         for e in spec.get('factions') or []]
        r['spells'] = [self.resolve(p, 'spell', e, 'spells') for e in spec.get('spells') or []]
        r['perks'] = [(self.resolve(p, 'perk', e.get('perk') if isinstance(e, dict) else e, 'perks'),
                       int(e.get('rank', 1)) if isinstance(e, dict) else 1) for e in spec.get('perks') or []]
        r['keywords'] = [self.resolve(p, 'keyword', e, 'keywords') for e in spec.get('keywords') or []]
        r['packages'] = [self.resolve(p, 'package', e, 'packages') for e in spec.get('packages') or []]
        r['inventory'] = [(self.resolve(p, 'item', e.get('item') if isinstance(e, dict) else e, 'inventory'),
                           int(e.get('count', 1)) if isinstance(e, dict) else 1)
                          for e in spec.get('inventory') or []]
        r['add_inventory'] = [(self.resolve(p, 'item', e.get('item') if isinstance(e, dict) else e,
                                            'add_inventory'),
                               int(e.get('count', 1)) if isinstance(e, dict) else 1)
                              for e in spec.get('add_inventory') or []]
        if spec.get('add_inventory') and not edit:
            p.errors.append("add_inventory is only for edit_npc; use inventory when creating an NPC")
        if spec.get('add_inventory') and spec.get('inventory') is not None:
            p.errors.append("give either inventory (replace) or add_inventory (append), not both")
        outfit = spec.get('outfit')
        r['outfit_items'], r['outfit_existing'] = [], 0
        if isinstance(outfit, str):
            r['outfit_existing'] = self.resolve(p, 'outfit', outfit, 'outfit')
        elif outfit:
            r['outfit_items'] = [self.resolve(p, 'outfit_item', e, 'outfit') for e in outfit]

        self.plan_stats(p, spec)
        self.plan_placement(p, spec)
        self.plan_dialogue(p, spec)

        if edit:
            pass
        elif not p.clone:
            if not r['voice_type']:
                p.warnings.append("no voice_type set; the NPC will have no voice")
            if not r['npc_class']:
                p.errors.append("npc_class is required when building from scratch")
            if not r['packages']:
                p.warnings.append("no AI packages; the NPC will stand idle")
            if not r['appearance_from']:
                p.warnings.append("from-scratch NPCs have no head parts or FaceGen; set appearance_from "
                                  "or expect an untextured face")
        elif not r['appearance_from']:
            p.warnings.append("cloned NPC has no FaceGen for its new form ID; use appearance_from or "
                              "generate FaceGen in the Creation Kit if the face renders dark")
        if p.clone is not None:
            p.abs_ids.add(self.m.rid(p.clone))
        return p

    def plan_stats(self, p, spec):
        """Range-check numeric stats and convert AI enums and skill names to the indices stored in p.stats."""
        e = p.errors
        p.stats = {}
        for key, lo, hi in (('level', 1, 65535), ('calc_min_level', 0, 65535), ('calc_max_level', 0, 65535),
                            ('speed_multiplier', 0, 65535), ('energy', 0, 100), ('mood', 0, 7),
                            ('health', 0, 65535), ('magicka', 0, 65535), ('stamina', 0, 65535)):
            if spec.get(key) is not None:
                v = spec[key]
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    e.append(f"{key} must be an integer in {lo}-{hi}")
        p.stats['aggression'] = None if spec.get('aggression') is None else \
            enum_value(spec['aggression'], AGGRESSION, 'aggression', e)
        p.stats['confidence'] = None if spec.get('confidence') is None else \
            enum_value(spec['confidence'], CONFIDENCE, 'confidence', e)
        p.stats['morality'] = None if spec.get('morality') is None else \
            enum_value(spec['morality'], MORALITY, 'morality', e)
        p.stats['assistance'] = None if spec.get('assistance') is None else \
            enum_value(spec['assistance'], ASSISTANCE, 'assistance', e)
        p.stats['skills'] = {}
        for k, v in (spec.get('skills') or {}).items():
            canon = SKILL_ALIASES.get(str(k).lower())
            canon = canon or next((s for s in SKILLS if s.lower() == str(k).lower()), None)
            if canon is None:
                e.append(f"skills: unknown skill {k!r}; use one of {list(SKILLS)}")
            elif not isinstance(v, int) or not 0 <= v <= 100:
                e.append(f"skills: {k} must be 0-100")
            else:
                p.stats['skills'][SKILLS.index(canon)] = v

    def plan_placement(self, p, spec):
        """Check placement keys and coordinates and locate the target cell into p.cell."""
        pl = spec.get('placement')
        if not pl:
            return
        bad = [k for k in pl if k not in ('cell', 'worldspace', 'grid_x', 'grid_y', 'x', 'y', 'z',
                                          'rotation_z', 'persistent')]
        if bad:
            p.errors.append(f"placement: unknown keys {bad}")
        if any(pl.get(k) is None for k in ('x', 'y', 'z')):
            p.errors.append("placement: x, y and z are required")
        try:
            p.cell = self.cells.find(pl.get('cell'), pl.get('worldspace'), pl.get('grid_x'), pl.get('grid_y'))
        except ValueError as ex:
            p.errors.append(f"placement: {ex}")
        if p.cell is not None and 'persistent' not in pl and any(t == 1 for t, _, _ in p.cell[1]):
            p.warnings.append("exterior placement defaults to a non-persistent reference")

    def plan_dialogue(self, p, spec):
        """Normalise dialogue entries into p.topics, adding hire/dismiss topics when a follower is requested."""
        p.topics = []
        entries = [dict(e) for e in (spec.get('dialogue') or [])]
        fol = spec.get('follower')
        if fol:
            not_hired = {'function': 'GetInFaction', 'form': 'CurrentHireling', 'op': '==', 'value': 0}
            for e in entries:
                if not e.pop('show_while_following', False):
                    e['conditions'] = list(e.get('conditions') or []) + [not_hired]
            cost = fol.get('cost', 50) if isinstance(fol, dict) else 50
            if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
                p.errors.append("follower: cost must be a non-negative whole number")
                cost = 50
            entries += follower_entries(cost, len(entries))
        if spec.get('travel_lines') or fol:
            entries += travel_entries({'function': 'GetInFaction', 'form': 'CurrentHireling', 'op': '==',
                                       'value': 1})
        if spec.get('combat_lines'):
            entries += combat_entries()
        ct = spec.get('commentary_text') or {}
        bad = sorted(set(ct) - preset_keys()) if isinstance(ct, dict) else None
        if bad is None or bad:
            p.errors.append(f"commentary_text: must be {{key: [lines]}} with keys from {sorted(preset_keys())}"
                            + (f"; unknown {bad}" if bad else ""))
            ct = {}
        for flag, make in (('weather_lines', weather_entries), ('time_lines', time_entries),
                           ('main_quest_lines', main_quest_entries), ('place_lines', place_entries)):
            if spec.get(flag):
                entries += make(ct)
        if spec.get('ambient_lines'):
            entries += ambient_entries()
        for i, t in enumerate(entries):
            kind = t.get('kind', 'topic')
            lines = t.get('lines') or ([t['line']] if t.get('line') else [])
            if isinstance(lines, str):
                lines = [lines]
            answers = t.get('answers')
            if answers is not None:
                if lines:
                    p.errors.append(f"dialogue[{i}]: use either lines or answers, not both")
                if not (isinstance(answers, list) and len(answers) > 0 and all(
                        isinstance(a, list) and a and all(isinstance(x, str) for x in a) for a in answers)):
                    p.errors.append(f"dialogue[{i}]: answers must be a non-empty list of non-empty line lists")
                    answers = None
                if answers:
                    lines = answers[0]
            if t.get('pick', 'random') not in PICK_MODES:
                p.errors.append(f"dialogue[{i}]: pick must be one of {list(PICK_MODES)}")
            if kind not in SUBTYPES:
                p.errors.append(f"dialogue[{i}]: kind must be one of {list(SUBTYPES)}")
            if kind == 'topic' and not t.get('prompt') and not t.get('blocking'):
                p.errors.append(f"dialogue[{i}]: a topic needs a prompt")
            parent = t.get('child_of')
            if parent is not None and not (isinstance(parent, int) and not isinstance(parent, bool)
                                           and 0 <= parent < i and kind == 'topic'):
                p.errors.append(f"dialogue[{i}]: child_of must be the index of an earlier topic entry")
                parent = None
            if not lines:
                p.errors.append(f"dialogue[{i}]: lines are required")
            try:
                for text in [t.get('prompt') or ''] + lines + [x for a in (answers or []) for x in a]:
                    z(text)
            except UnicodeEncodeError:
                p.errors.append(f"dialogue[{i}]: text contains characters outside Windows-1252")
            script = self.plan_script(p, t, i)
            entry = (kind, t.get('prompt'), lines, script, self.plan_conditions(p, t, i))
            opts = {}
            if answers and len(answers) > 1:
                opts.update(answers=answers, pick=t.get('pick', 'random'))
            if parent is not None:
                opts['child_of'] = parent
            if t.get('blocking'):
                opts['blocking'] = True
            if t.get('goodbye'):
                opts['goodbye'] = True
            if t.get('say_once'):
                opts['say_once'] = True
            if opts:
                entry += (opts,)
            p.topics.append(entry)
        if p.topics and not (spec.get('voice_type') or p.clone):
            p.warnings.append("dialogue will have no voice audio (subtitles only)")

    def reference_id(self, query):
        """Absolute form ID of a placed reference given as 'plugin.esm:hexid' or hex (an official master is assumed
        to be slot 0); placed references are not in the record index, so find them with search_references."""
        q = str(query).strip()
        file_part, _, hexpart = q.rpartition(':')
        if not re.fullmatch(r'(0x)?[0-9a-fA-F]{1,8}', hexpart or q):
            raise ValueError(f"{q!r}: give a reference as 'Skyrim.esm:013487' or its hex ID (see search_references)")
        slot = self.m.slots.get(file_part.lower() or 'skyrim.esm')
        if slot is None:
            raise ValueError(f"{q!r}: plugin {file_part!r} is not loaded")
        return (slot << 24) | (int(hexpart or q, 16) & 0xFFFFFF)

    def plan_conditions(self, p, t, i):
        """[(function index, absolute form id, operator, value, run-on)] from an entry's conditions."""
        out = []
        for n, c in enumerate(t.get('conditions') or []):
            label = f"dialogue[{i}].conditions[{n}]"
            func = CONDITION_FUNCTIONS.get(c.get('function'))
            op = CONDITION_OPS.get(c.get('op', '=='))
            run_on = RUN_ON.get(c.get('run_on', 'subject'))
            if func is None or op is None or run_on is None or (
                    'form' not in c and 'actor_value' not in c and c.get('function') not in NO_PARAM_FUNCTIONS):
                p.errors.append(f"{label}: needs function {list(CONDITION_FUNCTIONS)}, form (or actor_value "
                                f"for GetActorValue), op {list(CONDITION_OPS)} and run_on {list(RUN_ON)}")
                continue
            try:
                param2 = int(c.get('param2', 0))
            except (ValueError, TypeError):
                p.errors.append(f"{label}: param2 must be an integer (e.g. the stage for GetStageDone)")
                continue
            if c.get('function') in NO_PARAM_FUNCTIONS:
                out.append((func, 0, op, float(c.get('value', 1)), run_on, param2))
                continue
            if 'actor_value' in c:
                # actor value index, not a form; a negative id is written through unchanged
                out.append((func, -int(c['actor_value']), op, float(c.get('value', 1)), run_on, param2))
                continue
            try:
                if c.get('function') in REFERENCE_FUNCTIONS:
                    fid = self.reference_id(c['form'])
                else:
                    fid, _ = self.resolver.lookup(c['form'], list(self.m.by_sig))
                value = float(c.get('value', 1))
            except (ValueError, TypeError) as ex:
                p.errors.append(f"{label}: {ex}")
                continue
            p.abs_ids.add(fid)
            out.append((func, fid, op, value, run_on, param2))
        return out

    def plan_script(self, p, t, i):
        """(papyrus code, [(property name, absolute form id)]) run when the line ends, or None."""
        bad = [k for k in t if k not in ('kind', 'prompt', 'line', 'lines', 'give_items', 'code', 'properties',
                                         'conditions', 'show_while_following', 'answers', 'pick', 'child_of',
                                         'blocking', 'goodbye', 'say_once')]
        if bad:
            p.errors.append(f"dialogue[{i}]: unknown keys {bad}")
        code, props = [], []
        for n, entry in enumerate(t.get('give_items') or []):
            ident = entry.get('item') if isinstance(entry, dict) else entry
            count = int(entry.get('count', 1)) if isinstance(entry, dict) else 1
            fid = self.resolve(p, 'item', ident, f"dialogue[{i}].give_items")
            props.append((f"Give{n}", fid))
            code.append(f"Game.GetPlayer().AddItem(Give{n}, {count})")
        for name, ident in (t.get('properties') or {}).items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(name)):
                p.errors.append(f"dialogue[{i}]: bad property name {name!r}")
                continue
            try:
                fid, _ = self.resolver.lookup(ident, list(self.m.by_sig))
            except ValueError as ex:
                p.errors.append(f"dialogue[{i}].properties.{name}: {ex}")
                continue
            p.abs_ids.add(fid)
            props.append((name, fid))
        if t.get('code'):
            code.append(str(t['code']))
        elif t.get('properties'):
            p.errors.append(f"dialogue[{i}]: properties need code that uses them")
        return ("\n".join(code), props) if code else None

    def validate(self, spec):
        """Dry-run plan: {'ok', 'errors', 'warnings'} without writing anything."""
        p = self.plan(spec)
        return {'ok': not p.errors, 'errors': p.errors, 'warnings': p.warnings}

    def target_path(self, spec, output_dir=None):
        """Output plugin path from plugin_name (default <editor_id>.esp); rejects names containing a directory."""
        name = spec.get('plugin_name') or f"{spec.get('editor_id', 'CustomNpc')}.esp"
        if not name.lower().endswith(('.esp', '.esm', '.esl')):
            name += '.esp'
        if Path(name).name != name:
            raise ValueError("plugin_name must be a file name, not a path")
        return Path(output_dir or self.output_dir) / name

    # -- writing ----------------------------------------------------------------------------

    def write(self, spec, output_dir=None, overwrite=False, target_plugin=None):
        """Build the records for spec into a new plugin, or into an existing target_plugin; raises ValueError on plan errors."""
        target = None
        if target_plugin:
            path = resolve_target_path(target_plugin, output_dir or self.output_dir, self.data_dir)
            if path.exists():
                target = Target(self.m, self.names, path)
                if target.plugin.is_localized and (spec.get('name') or spec.get('dialogue')):
                    raise ValueError(f"{path.name} is localized; names and dialogue text cannot be added inline")
        p = self.plan(spec, target=target)
        if p.errors:
            raise ValueError("; ".join(p.errors))
        if target is None:
            path = path if target_plugin else self.target_path(spec, output_dir)
            if path.exists() and not overwrite:
                raise FileExistsError(f"{path} exists; pass overwrite=true to replace it")
            path.parent.mkdir(parents=True, exist_ok=True)
            plugin = new_plugin(path)
            self.next_id = 0x800
        else:
            plugin = target.plugin
        self.target = target
        self.add_masters(plugin, p)
        if target is not None:
            n = len(plugin.header.masters)
            used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
            self.next_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
        self.plugin = plugin
        made = []

        def new_id():
            n = self.next_id
            self.next_id += 1
            return n

        npc, outfit = self.make_npc(plugin, spec, p, new_id, made)
        npc_fid = npc.form_id.value
        if p.cell is not None:
            self.make_placement(plugin, spec, p, new_id, made, npc_fid)
        if p.topics:
            self.make_dialogue(plugin, spec, p, new_id, made, npc_fid)

        plugin.header.next_object_id = self.next_id
        plugin.save(path)
        check = Plugin.load(str(path))
        out = {'path': str(path), 'masters': list(plugin.header.masters), 'records': made,
               'reloaded_records': len(check.records), 'warnings': p.warnings}
        if p.clone is not None and not p.refs['appearance_from'] and spec.get('facegen', True):
            out['facegen'] = self.copy_facegen(p.clone, path, npc.form_id.value & 0xFFFFFF)
            tint = self.apply_hair_tint(p, path, npc.form_id.value & 0xFFFFFF)
            if tint:
                out['facegen']['hair_tint'] = tint
            if out['facegen']['copied']:
                out['warnings'] = [w for w in p.warnings if 'FaceGen' not in w]
            else:
                out['warnings'] = p.warnings + [f"FaceGen not copied: {out['facegen']['note']}"]
        return out

    def edit_npc(self, identifier, spec, target_plugin):
        """Change fields of an existing NPC in target_plugin (patch override or in-place edit)."""
        path = resolve_target_path(target_plugin, self.output_dir, self.data_dir)
        target = Target(self.m, self.names, path, create=True)
        if target.plugin.is_localized and (spec.get('name') or spec.get('short_name') or spec.get('dialogue')):
            raise ValueError(f"{path.name} is localized; names and dialogue text cannot be edited inline")
        p = self.plan(spec, edit=True, target=target)
        if p.errors:
            raise ValueError("; ".join(p.errors))
        rec = target.find_own(identifier, ['NPC_'])
        how = 'edited in place'
        if rec is None:
            fid, src = self.resolver.lookup(identifier, ['NPC_'])
            rec = target.find_matching(fid)
            how = 'edited existing override'
            if rec is None:
                rec, how = target.override(src), f"override of {src.plugin.file_path.name} record added"
        self.target, plugin = target, target.plugin
        self.add_masters(plugin, p)
        n = len(plugin.header.masters)
        used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
        self.next_id = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
        self.plugin = plugin
        made = []

        def new_id():
            v = self.next_id
            self.next_id += 1
            return v

        self.make_npc(plugin, spec, p, new_id, made, base=rec)
        ctx = dict(spec, editor_id=rec.editor_id)
        if p.cell is not None:
            self.make_placement(plugin, ctx, p, new_id, made, rec.form_id.value)
        if p.topics:
            self.make_dialogue(plugin, ctx, p, new_id, made, rec.form_id.value)
        plugin.header.next_object_id = self.next_id
        target.save()
        check = Plugin.load(str(path))
        out = {'path': str(path), 'record': how, 'masters': list(plugin.header.masters), 'records': made,
               'reloaded_records': len(check.records), 'warnings': p.warnings}
        if spec.get('hair_color'):
            tint = self.apply_hair_tint(p, path, rec.form_id.value & 0xFFFFFF)
            if tint:
                out['hair_tint'] = tint
            else:
                p.warnings.append("hair_color set, but no loose FaceGen mesh for this NPC next to the "
                                  "plugin; if the NPC has baked FaceGen its tint will still win")
        return out

    def _face_context(self, rec, plugin):
        """(race editor ID, {head part editor id lower: HDPT}) for an NPC record."""
        m = self.m
        rnam = rec.get_subrecord('RNAM')
        race = m.records.get(m.absid(plugin, struct.unpack('<I', bytes(rnam.data))[0])) if rnam else None
        parts = {}
        for s in rec.subrecords:
            if s.signature == 'PNAM':
                hd = m.records.get(m.absid(plugin, struct.unpack('<I', bytes(s.data))[0]))
                if hd is not None and hd.signature == 'HDPT':
                    parts[(hd.editor_id or '').lower()] = hd
        return (race.editor_id if race else ''), parts

    def get_face(self, identifier, target_plugin=None):
        """Face sliders, presets and head parts of an NPC (from target_plugin's copy if it has one)."""
        from tools.npcs import facegen
        rec = plugin = None
        if target_plugin:
            path = resolve_target_path(target_plugin, self.output_dir, self.data_dir)
            if path.exists():
                t = Target(self.m, self.names, path)
                rec, plugin = t.find_own(identifier, ['NPC_']), t.plugin
        if rec is None:
            _, rec = self.resolver.lookup(identifier, ['NPC_'])
            plugin = rec.plugin
        race, parts = self._face_context(rec, plugin)
        return {'npc': rec.editor_id, 'race': race, 'head_parts': sorted(parts),
                'face': facegen.decode_face(rec), 'slider_meanings': facegen.describe_sliders(),
                'note': 'sliders run -1..1 (positive = first meaning); nose_type/eye_type/lip_type are '
                        'chargen preset numbers (1-based, null = none); weight 0-100'}

    def edit_face(self, identifier, changes, target_plugin):
        """Change an NPC's face in target_plugin: NAM9/NAMA/NAM7 plus its loose FaceGen mesh."""
        from tools.npcs import facegen
        path = resolve_target_path(target_plugin, self.output_dir, self.data_dir)
        target = Target(self.m, self.names, path, create=True)
        rec = target.find_own(identifier, ['NPC_'])
        in_place = rec is not None
        if rec is None:
            fid, src = self.resolver.lookup(identifier, ['NPC_'])
            rec = target.find_matching(fid) or target.override(src)
        plugin = target.plugin
        race, parts = self._face_context(rec, plugin)
        if race.lower().startswith(('argonian', 'khajiit')):
            raise ValueError(f"{race}: beast race faces use different morph files and are not supported")
        old = facegen.decode_face(rec)
        changes = dict(changes or {})
        beard = changes.pop('beard', None)
        face, errors = facegen.merge_face(old, changes)
        if errors:
            raise ValueError("; ".join(errors))
        facegen.encode_face(rec, face, old)
        out = {'path': str(path), 'npc': rec.editor_id, 'warnings': []}
        mesh = (path.parent / 'Meshes' / 'Actors' / 'Character' / 'FaceGenData' / 'FaceGeom' / path.name
                / f"{rec.form_id.value & 0xFFFFFF:08X}.nif")
        if in_place and mesh.is_file():
            allparts = {(h.editor_id or '').lower(): h for h in self.m.by_sig['HDPT'].values()}

            def load(rel):
                return self.find_asset(rel)
            data, report = facegen.regenerate(mesh.read_bytes(), face, race, allparts, load, old_face=old)
            shutil.copyfile(mesh, str(mesh) + '.bak')
            mesh.write_bytes(data)
            out['facegen'] = {'mesh': str(mesh), 'shapes': report}
            if beard:
                out['facegen']['beard'] = self.set_beard(target, rec, beard, mesh)
        else:
            if beard:
                raise ValueError("beard needs a loose FaceGen mesh next to the plugin for this NPC")
            out['warnings'].append(
                "no loose FaceGen mesh next to the plugin for this NPC: the record changed but a baked "
                "face in the game files would still show the old shape; regenerate it in the Creation Kit "
                "(Ctrl+F4 in the NPC window)")
        target.save()
        out['face'] = face
        return out

    def find_beard_donor(self, hd):
        """A vanilla FaceGen mesh whose NPC wears this beard head part, as (bytes, shape name)."""
        from tools.npcs import facegen
        want, shape = self.m.rid(hd), hd.editor_id
        for npc in self.m.by_sig['NPC_'].values():
            for s in npc.subrecords:
                if s.signature == 'PNAM' and self.m.absid(npc.plugin, struct.unpack('<I', bytes(s.data))[0]) == want:
                    plugin, oid = self.facegen_sources(npc)
                    data = self.find_asset(f"Meshes\\Actors\\Character\\FaceGenData\\FaceGeom\\{plugin}\\{oid:08X}.nif")
                    if data and facegen._Layout(data).find(shape, ('BSDynamicTriShape',)) is not None:
                        return data, shape
        return None, shape

    def set_beard(self, target, rec, beard, mesh):
        """Give the NPC a facial hair head part: update PNAM and graft its shape into the FaceGen mesh."""
        from tools.npcs import facegen
        hd = next((h for h in self.m.by_sig['HDPT'].values()
                   if (h.editor_id or '').lower() == beard.lower()), None)
        if hd is None or struct.unpack('<I', bytes(hd.get_subrecord('PNAM').data))[0] != 4:
            raise ValueError(f"{beard!r} is not a facial hair head part; try search_forms HDPT HumanBeard")
        plugin = target.plugin
        current = None
        for s in rec.subrecords:
            if s.signature == 'PNAM':
                part = self.m.records.get(self.m.absid(plugin, struct.unpack('<I', bytes(s.data))[0]))
                if part is not None and part.signature == 'HDPT' and \
                        struct.unpack('<I', bytes(part.get_subrecord('PNAM').data))[0] == 4:
                    current = (s, part)
        if current is not None and current[1].get_subrecord('MODL') is not None \
                and current[1].editor_id != hd.editor_id:
            raise ValueError(f"NPC already has {current[1].editor_id}; replacing an existing beard is not supported")
        data = mesh.read_bytes()
        donor, shape = self.find_beard_donor(hd)
        if donor is None:
            raise ValueError(f"no vanilla FaceGen mesh found to copy {shape} from")
        if facegen._Layout(data).find(shape, ('BSDynamicTriShape',)) is None:
            data = facegen.add_shape(data, donor, shape)
        value = struct.pack('<I', target.local(self.m.rid(hd)))
        if current is not None:
            current[0].data = value
        else:
            rec.add_subrecord('PNAM', value)
        sub = rec.get_subrecord('HCLF')
        clfm = self.m.records.get(self.m.absid(plugin, struct.unpack('<I', bytes(sub.data))[0])) if sub else None
        cnam = clfm.get_subrecord('CNAM') if clfm else None
        if cnam is not None:
            data, _ = set_nif_hair_tint(data, tuple(bytes(cnam.data)[:3]))
        mesh.write_bytes(data)
        return shape

    def paint_face(self, identifier, target_plugin, mask, color, opacity=1.0):
        """Blend a colour through a vanilla tint mask into the NPC's loose FaceTint texture."""
        from tools.npcs import facegen
        path = resolve_target_path(target_plugin, self.output_dir, self.data_dir)
        rec = Target(self.m, self.names, path).find_own(identifier, ['NPC_']) if path.exists() else None
        if rec is None:
            raise ValueError(f"{identifier!r} is not an NPC in {path.name}")
        tint = (path.parent / 'Textures' / 'Actors' / 'Character' / 'FaceGenData' / 'FaceTint' / path.name
                / f"{rec.form_id.value & 0xFFFFFF:08X}.dds")
        if not tint.is_file():
            raise ValueError("no loose FaceTint texture for this NPC next to the plugin")
        mask_data = self.find_asset(f"Textures\\Actors\\Character\\Character Assets\\TintMasks\\{mask}.dds")
        if mask_data is None:
            raise ValueError(f"tint mask {mask!r} not found; names look like MaleHeadNordWarpaint_01")
        orig = Path(str(tint) + '.orig.bak')
        if not orig.exists():
            shutil.copyfile(tint, orig)
        tint.write_bytes(facegen.apply_paint(orig.read_bytes(), mask_data, facegen.parse_color(color), opacity))
        return {'texture': str(tint), 'mask': mask, 'color': facegen.parse_color(color), 'opacity': opacity}

    def facegen_sources(self, clone):
        """(origin plugin name, source object id) for a cloned NPC's face files."""
        origin = self.slot_names[self.m.rid(clone) >> 24]
        return self.names.get(origin, origin), clone.form_id.value & 0xFFFFFF

    def find_asset(self, rel):
        """Bytes of a game asset from loose files or the Data folder BSAs, else None."""
        loose = self.data_dir / rel
        if loose.is_file():
            return loose.read_bytes()
        from esplib.bsa import BsaReader
        key = rel.lower().replace('/', '\\')
        for bsa in sorted(self.data_dir.glob('*.bsa')):
            try:
                with BsaReader(bsa) as b:
                    if b.has_file(key):
                        return b.read_file(key)
            except Exception:
                continue
        return None

    def apply_hair_tint(self, plan, esp_path, object_id):
        """Bake the chosen hair color into the NPC's loose FaceGen mesh, which overrides HCLF in game."""
        fid = plan.refs.get('hair_color')
        rec = self.m.by_sig['CLFM'].get(fid) if fid else None
        sub = next((s for s in rec.subrecords if s.signature == 'CNAM'), None) if rec else None
        mesh = (esp_path.parent / 'Meshes' / 'Actors' / 'Character' / 'FaceGenData' / 'FaceGeom'
                / esp_path.name / f"{object_id:08X}.nif")
        if sub is None or not mesh.is_file():
            return None
        rgb = tuple(bytes(sub.data)[:3])
        data, n = set_nif_hair_tint(mesh.read_bytes(), rgb)
        mesh.write_bytes(data)
        return {'mesh': str(mesh.relative_to(esp_path.parent)), 'shaders': n, 'rgb': rgb}

    def copy_facegen(self, clone, esp_path, new_object_id):
        """Copy the source NPC's FaceGen mesh/tint as loose files for the new form ID."""
        if self.data_dir is None:
            return {'copied': [], 'note': 'no Data folder configured'}
        src_plugin, src_id = self.facegen_sources(clone)
        old, new = f"{src_id:08X}", f"{new_object_id:08X}"
        base = esp_path.parent
        copied = []
        mesh = f"Meshes\\Actors\\Character\\FaceGenData\\FaceGeom\\{src_plugin}\\{old}.nif"
        data = self.find_asset(mesh)
        if data is None:
            return {'copied': [], 'note': f"{src_plugin} has no FaceGen mesh for {old}"}
        dest = base / f"Meshes\\Actors\\Character\\FaceGenData\\FaceGeom\\{esp_path.name}\\{new}.nif"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(retarget_nif(data, src_plugin, esp_path.name, old, new))
        copied.append(str(dest.relative_to(base)))
        for rel, ext in ((f"Textures\\Actors\\Character\\FaceGenData\\FaceTint\\{src_plugin}\\{old}.dds", 'tint'),
                         (f"Textures\\Actors\\Character\\FaceMods\\{src_plugin}\\{old}_0.dds", 'mods')):
            tex = self.find_asset(rel)
            if tex is None:
                continue
            dest = base / rel.replace(src_plugin, esp_path.name).replace(old, new)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tex)
            copied.append(str(dest.relative_to(base)))
        note = 'files use the 00-prefixed form ID (%s)' % new
        return {'copied': copied, 'note': note}

    def master_names(self, p):
        """Lowercased plugin names the output must list as masters, taken from referenced form ID slots and the clone/cell sources."""
        names = set()
        for fid in p.abs_ids:
            slot = fid >> 24
            names.add(self.slot_names.get(slot, '').lower())
        sources = []
        if p.clone is not None:
            sources.append(p.clone.plugin)
        if p.cell is not None:
            sources.append(p.cell[0])
        for src in sources:
            names.update(m.lower() for m in src.header.masters)
            names.add(src.file_path.name.lower())
        names.discard('')
        return names

    def add_masters(self, plugin, p):
        """Add masters in load order so output form ID slots match the model's slots."""
        names = self.master_names(p)
        for name in sorted(names, key=lambda n: self.m.slots.get(n, 1 << 20)):
            if self.target is not None:
                self.target.ensure_master(self.names.get(name, name))
            else:
                plugin.add_master(self.names.get(name, name))

    def local(self, fid):
        """Absolute (model) form ID -> FormID value in the output plugin."""
        if not fid:
            return 0
        name = self.slot_names[fid >> 24]
        masters = [m.lower() for m in self.plugin.header.masters]
        if name not in masters and name == self.plugin.file_path.name.lower():
            return (len(masters) << 24) | (fid & 0xFFFFFF)
        return (masters.index(name) << 24) | (fid & 0xFFFFFF)

    def own(self, object_id):
        """FormID for a new record owned by the output plugin: master count in the top byte."""
        return (len(self.plugin.header.masters) << 24) | object_id

    def lid(self, fid):
        """Output-plugin form ID: a negative value is already local (used for records made in this run)."""
        return -fid if fid <= 0 else self.local(fid)

    def register(self, plugin, rec):
        """Append rec to the plugin and update its form ID, editor ID and signature indexes by hand."""
        rec.plugin = plugin
        plugin.records.append(rec)
        plugin._form_id_index[rec.form_id.value] = rec
        if rec.editor_id:
            plugin._editor_id_index[rec.editor_id.lower()] = rec
        plugin._signature_index.setdefault(rec.signature, []).append(rec)
        plugin.modified = True

    def override(self, plugin, src):
        """Copy src into plugin as an override, remapping its form IDs and delocalizing strings if needed."""
        rec = src.copy()
        rec.form_id = FormID(plugin.remap_formid(src.form_id.value, src.plugin))
        plugin._remap_subrecord_formids(rec, src.plugin)
        if not plugin.is_localized:
            plugin._delocalize_strings(rec, src.plugin, None)
        return rec

    def make_npc(self, plugin, spec, p, new_id, made, base=None):
        """Create the NPC_ record from a clone or blank (or edit base in place), applying spec fields; returns it."""
        r = p.refs
        if base is not None:
            rec = base
            acbs = list(struct.unpack('<IhhHHHHhHhH', rec.get_subrecord('ACBS').data[:24]))
            dnam = bytearray(rec.get_subrecord('DNAM').data) if rec.get_subrecord('DNAM') else \
                self.default_dnam()
            aidt = bytearray(rec.get_subrecord('AIDT').data) if rec.get_subrecord('AIDT') else \
                self.default_aidt()
        elif p.clone is not None:
            rec = p.clone.copy()
            rec.form_id = FormID(self.own(new_id()))
            plugin._remap_subrecord_formids(rec, p.clone.plugin)
            if not plugin.is_localized:
                plugin._delocalize_strings(rec, p.clone.plugin, None)
            if not spec.get('keep_scripts'):
                rec.remove_subrecords('VMAD')
            acbs = list(struct.unpack('<IhhHHHHhHhH', rec.get_subrecord('ACBS').data[:24]))
            dnam = bytearray(rec.get_subrecord('DNAM').data) if rec.get_subrecord('DNAM') else \
                self.default_dnam()
            aidt = bytearray(rec.get_subrecord('AIDT').data) if rec.get_subrecord('AIDT') else \
                self.default_aidt()
        else:
            rec = Record('NPC_', FormID(self.own(new_id())), 0)
            rec.add_subrecord('OBND', struct.pack('<6h', -22, -14, 0, 22, 14, 128))
            acbs = [0x10 | 0x20, 0, 0, 1, 1, 0, 100, 35, 0, 0, 0]
            dnam, aidt = self.default_dnam(), self.default_aidt()
            rec.add_subrecord('DATA', b'')
            rec.add_subrecord('NAM5', b'\xff\x00')
            rec.add_subrecord('NAM8', struct.pack('<I', 1))
            for sig, val in (('NAM6', 1.0), ('NAM7', 50.0)):
                rec.add_subrecord(sig, struct.pack('<f', val))

        def setsub(sig, data):
            rec.remove_subrecords(sig)
            rec.add_subrecord(sig, data)

        def setref(sig, key):
            if r.get(key):
                setsub(sig, fid_bytes(self.local(r[key])))

        if spec.get('editor_id') or base is None:
            setsub('EDID', z(spec['editor_id']))
        if spec.get('name') or base is None:
            setsub('FULL', z(spec['name']))
        if spec.get('short_name'):
            setsub('SHRT', z(spec['short_name']))
        setref('RNAM', 'race')
        setref('VTCK', 'voice_type')
        setref('CNAM', 'npc_class')
        setref('ZNAM', 'combat_style')
        setref('INAM', 'death_item')
        setref('HCLF', 'hair_color')

        for key, bit in BOOL_FLAGS.items():
            if spec.get(key) is not None:
                acbs[0] = acbs[0] | bit if spec[key] else acbs[0] & ~bit
        if spec.get('level_mult') is not None:
            acbs[0] |= 0x80
            acbs[3] = max(1, round(spec['level_mult'] * 1000))
        elif spec.get('level') is not None:
            acbs[0] &= ~0x80
            acbs[3] = spec['level']
        for idx, key in ((4, 'calc_min_level'), (5, 'calc_max_level'), (6, 'speed_multiplier'),
                         (7, 'disposition_base'), (1, 'magicka_offset'), (2, 'stamina_offset'),
                         (9, 'health_offset')):
            if spec.get(key) is not None:
                acbs[idx] = spec[key]
        if r.get('appearance_from'):
            setsub('TPLT', fid_bytes(self.local(r['appearance_from'])))
            acbs[0] |= 0x100
            acbs[8] |= 0x1
        setsub('ACBS', struct.pack('<IhhHHHHhHhH', *acbs))

        for i, v in p.stats['skills'].items():
            dnam[i] = v
        for off, key in ((36, 'health'), (38, 'magicka'), (40, 'stamina')):
            if spec.get(key) is not None:
                struct.pack_into('<H', dnam, off, spec[key])
        setsub('DNAM', bytes(dnam))
        for idx, key in ((0, 'aggression'), (1, 'confidence'), (3, 'morality'), (5, 'assistance')):
            if p.stats[key] is not None:
                aidt[idx] = p.stats[key]
        for idx, key in ((2, 'energy'), (4, 'mood')):
            if spec.get(key) is not None:
                aidt[idx] = spec[key]
        setsub('AIDT', bytes(aidt))
        if spec.get('height') is not None:
            setsub('NAM6', struct.pack('<f', spec['height']))
        if spec.get('weight') is not None:
            setsub('NAM7', struct.pack('<f', spec['weight']))

        def setlist(key, sigs, items):
            if key in spec and spec[key] is not None:
                for s in sigs:
                    rec.remove_subrecords(s)
                for sig, data in items:
                    rec.add_subrecord(sig, data)

        lc = self.local
        setlist('factions', ['SNAM'], [('SNAM', struct.pack('<Ib3x', lc(f), rk)) for f, rk in r['factions']])
        setlist('spells', ['SPCT', 'SPLO'], ([('SPCT', struct.pack('<I', len(r['spells'])))] if r['spells'] else [])
                + [('SPLO', fid_bytes(lc(s))) for s in r['spells']])
        setlist('perks', ['PRKZ', 'PRKR'], ([('PRKZ', struct.pack('<I', len(r['perks'])))] if r['perks'] else [])
                + [('PRKR', struct.pack('<IB3x', lc(pk), rk)) for pk, rk in r['perks']])
        setlist('keywords', ['KSIZ', 'KWDA'], ([('KSIZ', struct.pack('<I', len(r['keywords']))),
                                                ('KWDA', b''.join(fid_bytes(lc(k)) for k in r['keywords']))]
                                               if r['keywords'] else []))
        setlist('inventory', ['COCT', 'CNTO', 'COED'],
                ([('COCT', struct.pack('<I', len(r['inventory'])))] if r['inventory'] else [])
                + [('CNTO', struct.pack('<Ii', lc(it), n)) for it, n in r['inventory']])
        setlist('packages', ['PKID'], [('PKID', fid_bytes(lc(k))) for k in r['packages']])
        if r['add_inventory']:
            held = [s for s in rec.subrecords if s.signature == 'CNTO']
            rec.remove_subrecords('COCT')
            rec.add_subrecord('COCT', struct.pack('<I', len(held) + len(r['add_inventory'])))
            for it, n in r['add_inventory']:
                rec.add_subrecord('CNTO', struct.pack('<Ii', lc(it), n))

        outfit = None
        if r['outfit_items']:
            outfit = Record('OTFT', FormID(self.own(new_id())), 0)
            outfit.add_subrecord('EDID', z((rec.editor_id or spec.get('editor_id')) + 'Outfit'))
            outfit.add_subrecord('INAM', b''.join(fid_bytes(lc(i)) for i in r['outfit_items']))
            plugin.add_record(outfit)
            made.append(self.describe(outfit))
            setsub('DOFT', fid_bytes(outfit.form_id.value))
        elif r['outfit_existing']:
            setsub('DOFT', fid_bytes(lc(r['outfit_existing'])))

        keep_up = spec.get('keep_up')
        if keep_up:
            distance = float(keep_up.get('distance', 3000)) if isinstance(keep_up, dict) else 3000.0
            sname = script_name(plugin.file_path.name, 'NPC', rec.form_id.value & 0xFFFFFF)
            pex = compile_script(sname, keep_up_source(sname, distance), plugin.file_path.parent / 'Scripts')
            vmad = npc_script_vmad(rec, sname)
            rec.remove_subrecords('VMAD')
            rec.add_subrecord('VMAD', vmad)
            made.append({'type': 'script', 'editor_id': sname, 'object_id': str(pex)})

        rec.subrecords.sort(key=lambda s: NPC_RANK.get(s.signature, len(NPC_ORDER)))
        rec.modified = True
        if base is None:
            plugin.add_record(rec)
        made.append(self.describe(rec))
        return rec, outfit

    @staticmethod
    def default_dnam():
        return bytearray(struct.pack('<18B18B3HH f B3x', *([15] * 18), *([0] * 18), 100, 100, 100, 0, 0.0, 0))

    @staticmethod
    def default_aidt():
        return bytearray(struct.pack('<8B3I', 0, 2, 50, 0, 0, 1, 0, 0, 0, 0, 0))

    def describe(self, rec):
        """Summary dict for a created record; object_id is the low 24 bits as hex."""
        return {'type': rec.signature, 'editor_id': rec.editor_id,
                'object_id': f"{rec.form_id.value & 0xFFFFFF:06X}"}

    def make_placement(self, plugin, spec, p, new_id, made, npc_fid):
        """Place the NPC as an ACHR; defaults to persistent in interiors, not in exteriors. Rotation is given in degrees."""
        pl = spec['placement']
        exterior = any(t == 1 for t, _, _ in p.cell[1])
        persistent = pl.get('persistent', not exterior)
        achr = Record('ACHR', FormID(self.own(new_id())), 0x400 if persistent else 0)
        achr.add_subrecord('EDID', z(spec['editor_id'] + 'Ref'))
        achr.add_subrecord('NAME', fid_bytes(npc_fid))
        achr.add_subrecord('DATA', struct.pack('<6f', pl['x'], pl['y'], pl['z'], 0.0, 0.0,
                                               math.radians(pl.get('rotation_z', 0.0))))
        self.place_ref(plugin, p.cell, achr, persistent, made)

    def place_ref(self, plugin, cell_info, ref, persistent, made):
        """Put a REFR/ACHR into its cell's child group, creating the group chain and cell override."""
        src_plugin, chain, cell_src = cell_info

        top = next((g for g in plugin.groups if g.group_type == 0 and g.label == chain[0][1]), None)
        if top is None:
            top = GroupRecord(0, chain[0][1])
            plugin.groups.append(top)
        parent = top
        for gtype, label, prev in chain[1:]:
            if prev is not None:
                world = self.target.find_matching(self.m.rid(prev)) if self.target else None
                if world is None:
                    world = self.override(plugin, prev)
                    parent.records.append(world)
                    self.register(plugin, world)
                label = world.form_id.value
            group = next((g for g in parent.records if isinstance(g, GroupRecord)
                          and g.group_type == gtype and g.label == label), None)
            if group is None:
                group = GroupRecord(gtype, label)
                parent.records.append(group)
            parent = group
        cell = self.target.find_matching(self.m.rid(cell_src)) if self.target else None
        if cell is None:
            cell = self.override(plugin, cell_src)
            parent.records.append(cell)
            self.register(plugin, cell)
            made.append({'type': 'CELL (override)', 'editor_id': cell.editor_id or cell_src.full_name,
                         'object_id': f"{cell.form_id.value & 0xFFFFFF:06X}"})

        gt = 8 if persistent else 9
        children = next((g for g in parent.records if isinstance(g, GroupRecord) and g.group_type == 6
                         and g.label == cell.form_id.value), None)
        if children is None:
            children = GroupRecord(6, cell.form_id.value)
            parent.records.append(children)
        holder = next((g for g in children.records if isinstance(g, GroupRecord) and g.group_type == gt), None)
        if holder is None:
            holder = GroupRecord(gt, cell.form_id.value)
            children.records.append(holder)
        holder.records.append(ref)
        self.register(plugin, ref)
        made.append(self.describe(ref))

    def add_fetch_quest(self, identifier, spec, target_plugin):
        from tools.quests.quest_builder import add_fetch_quest
        return add_fetch_quest(self, identifier, spec, target_plugin)

    def add_kill_quest(self, identifier, spec, target_plugin):
        from tools.quests.kill_quest_builder import add_kill_quest
        return add_kill_quest(self, identifier, spec, target_plugin)

    def add_plant_quest(self, identifier, spec, target_plugin):
        from tools.quests.plant_quest_builder import add_plant_quest
        return add_plant_quest(self, identifier, spec, target_plugin)

    def add_radiant_quest(self, identifier, spec, target_plugin):
        from tools.quests.radiant_builder import add_radiant_quest
        return add_radiant_quest(self, identifier, spec, target_plugin)

    QUEST_KINDS = ('fetch', 'kill', 'plant', 'radiant')

    def add_quest(self, kind, identifier, spec, target_plugin):
        """Single entry point for the quest builders; kind picks the quest shape."""
        if kind not in self.QUEST_KINDS:
            raise ValueError(f"unknown quest kind {kind!r}; use one of {', '.join(self.QUEST_KINDS)}")
        return getattr(self, f"add_{kind}_quest")(identifier, spec, target_plugin)

    def make_dialogue(self, plugin, spec, p, new_id, made, npc_fid):
        """Create (or reuse) the dialogue quest with the NPC as its speaker alias, then add the planned topics."""
        qid = spec.get('quest_editor_id') or spec['editor_id'] + 'DialogueQuest'
        quest = self.target.find_own(qid, ['QUST']) if self.target else None
        if quest is None:
            quest = Record('QUST', FormID(self.own(new_id())), 0)
            quest.add_subrecord('EDID', z(qid))
            quest.add_subrecord('DNAM', struct.pack('<HBBII', 0x1, 50, 0, 0, 0))
            quest.add_subrecord('NEXT', b'')
            quest.add_subrecord('ANAM', struct.pack('<I', 1))
            quest.add_subrecord('ALST', struct.pack('<I', 0))
            quest.add_subrecord('ALID', z(spec.get('alias_name') or 'Speaker'))
            quest.add_subrecord('FNAM', struct.pack('<I', 2))
            quest.add_subrecord('ALUA', fid_bytes(npc_fid))
            quest.add_subrecord('VTCK', fid_bytes(0))
            quest.add_subrecord('ALED', b'')
            plugin.add_record(quest)
            made.append(self.describe(quest))
        existing = sum(1 for r in plugin.records if r.signature == 'DIAL'
                       and (r.editor_id or '').startswith(qid))

        top = next((g for g in plugin.groups if g.group_type == 0 and g.label == 'DIAL'), None)
        if top is None:
            top = GroupRecord(0, 'DIAL')
            plugin.groups.append(top)
        condition = struct.pack('<B3xfH2xIIIIi', 0, 1.0, GET_IS_ID, npc_fid, 0, 0, 0, -1)
        # topics may carry a sixth item {'child_of': index into p.topics}; children join the parent's branch
        # and are offered after the parent's response instead of in the top-level menu
        made_topics = {}
        for n, topic in enumerate(p.topics, existing + 1):
            kind, prompt, lines, script, extra = topic[:5]
            opts = topic[5] if len(topic) > 5 else {}
            parent_idx = opts.get('child_of')
            answers = opts.get('answers') or [lines]
            sub_name, data = SUBTYPES[kind]
            dial = Record('DIAL', FormID(self.own(new_id())), 0)
            dial.add_subrecord('EDID', z(f"{qid}{kind.capitalize()}{n}"))
            if prompt:
                dial.add_subrecord('FULL', z(prompt))
            dial.add_subrecord('PNAM', struct.pack('<f', 50.0))
            branch_fid = None
            if parent_idx is not None:
                branch_fid = made_topics[parent_idx][2]
                dial.add_subrecord('BNAM', fid_bytes(branch_fid))
            elif kind == 'topic':
                # player-selectable topics only show when they start a top-level branch
                branch = Record('DLBR', FormID(self.own(new_id())), 0)
                branch.add_subrecord('EDID', z(f"{qid}Branch{n}"))
                branch.add_subrecord('QNAM', fid_bytes(quest.form_id.value))
                branch.add_subrecord('TNAM', struct.pack('<I', 0))
                branch.add_subrecord('DNAM', struct.pack('<I', 2 if opts.get('blocking') else 1))
                branch.add_subrecord('SNAM', fid_bytes(dial.form_id.value))
                plugin.add_record(branch)
                branch_fid = branch.form_id.value
                dial.add_subrecord('BNAM', fid_bytes(branch.form_id.value))
            dial.add_subrecord('QNAM', fid_bytes(quest.form_id.value))
            dial.add_subrecord('DATA', data)
            dial.add_subrecord('SNAM', sub_name)
            dial.add_subrecord('TIFC', struct.pack('<I', len(answers)))
            plugin.add_record(dial)
            group = GroupRecord(7, dial.form_id.value)
            top.records.append(group)
            def make_info(a_lines, flags):
                if opts.get('goodbye'):
                    flags |= INFO_GOODBYE
                if opts.get('say_once'):
                    flags |= INFO_SAY_ONCE
                rec = Record('INFO', FormID(self.own(new_id())), 0)
                rec.add_subrecord('ENAM', struct.pack('<HH', flags, INFO_IDLE_RESET if kind == 'idle' else 0))
                rec.add_subrecord('CNAM', b'\x00')
                for i, text in enumerate(a_lines, 1):
                    rec.add_subrecord('TRDT', struct.pack('<II4xB3xIB3x', 0, 50, i, 0, 1))
                    rec.add_subrecord('NAM1', z(text))
                    rec.add_subrecord('NAM2', b'\x00')
                    rec.add_subrecord('NAM3', b'\x00')
                rec.add_subrecord('CTDA', condition)
                for func, fid, op, value, run_on, *rest in extra:
                    param2 = rest[0] if rest else 0
                    rec.add_subrecord('CTDA', struct.pack('<B3xfH2xIIIIi', op << 5, value, func,
                                                          self.lid(fid), param2, run_on, 0, -1))
                return rec
            def attach_script(rec):
                code, props = script
                sname = script_name(plugin.file_path.name, 'TIF', rec.form_id.value & 0xFFFFFF)
                pex = compile_script(sname, tif_source(sname, code, [(n_, 'Form') for n_, _ in props]),
                                     plugin.file_path.parent / 'Scripts')
                rec.add_subrecord('VMAD', info_vmad(sname, [(n_, self.lid(f)) for n_, f in props]))
                rec.subrecords.insert(0, rec.subrecords.pop())
                made.append({'type': 'script', 'editor_id': sname, 'object_id': str(pex)})
            info = make_info(answers[0], answer_flags(opts.get('pick'), 0, len(answers)))
            if script:
                attach_script(info)
            group.records.append(info)
            self.register(plugin, info)
            for a_i, a_lines in enumerate(answers[1:], 1):
                more = make_info(a_lines, answer_flags(opts.get('pick'), a_i, len(answers)))
                if script:
                    attach_script(more)
                group.records.append(more)
                self.register(plugin, more)
            made_topics[n - existing - 1] = (dial, info, branch_fid)
            if parent_idx is not None:
                pinfo = made_topics[parent_idx][1]
                pos = next(i for i, s in enumerate(pinfo.subrecords) if s.signature == 'CNAM') + 1
                pinfo.add_subrecord('TCLT', fid_bytes(dial.form_id.value))
                pinfo.subrecords.insert(pos, pinfo.subrecords.pop())
            made.extend([self.describe(dial), {'type': 'INFO', 'editor_id': None,
                                               'object_id': f"{info.form_id.value & 0xFFFFFF:06X}"}])


class NpcLookup:
    """Resolve an NPC_ record for cloning; returns the record or an error string."""

    def __init__(self, model, resolver):
        self.m, self.r = model, resolver

    def find(self, query):
        """Return the NPC_ record, or the lookup error message as a string."""
        try:
            return self.r.lookup(query, ['NPC_'])[1]
        except ValueError as e:
            return str(e)
