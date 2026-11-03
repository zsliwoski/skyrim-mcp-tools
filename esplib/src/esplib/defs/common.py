"""Shared record element definitions used across all games.

These correspond to the commonly reused definitions in wbDefinitionsCommon.pas
and shared variables in wbDefinitionsTES5.pas (wbEDID, wbFULL, etc.).
"""

from .types import (
    IntType, EspFlags, EspEnum,
    EspInteger, EspFloat, EspString, EspFormID, EspByteArray,
    EspAlternateTextures,
    EspStruct, EspArray, EspUnion, EspSubRecord,
)
from .context import EspContext


# ---------------------------------------------------------------------------
# Common subrecord definitions -- reusable building blocks
# ---------------------------------------------------------------------------

# Editor ID -- null-terminated string, present on almost every record
EDID = EspSubRecord.new('EDID', 'Editor ID',
                         EspString.new('edid', 'zstring'))

# Full Name -- localized string.
# In localized plugins: 4-byte uint32 string table ID.
# In non-localized plugins: inline null-terminated string (zstring).
# xEdit calls this kind of field "lstring" but that's the abstract
# may-be-localized concept, not the EspString uint16-length-prefix
# encoding -- Bethesda writes inline FULL/DESC/NAM1 as zstrings.
FULL = EspSubRecord.new('FULL', 'Name', EspUnion.new(
    'name',
    decider=lambda ctx: 0 if ctx.extra.get('subrecord_size', 0) == 4 else 1,
    members=[
        EspInteger.new('string_id', IntType.U32),
        EspString.new('name', 'zstring'),
    ],
))

DESC = EspSubRecord.new('DESC', 'Description', EspUnion.new(
    'desc',
    decider=lambda ctx: 0 if ctx.extra.get('subrecord_size', 0) == 4 else 1,
    members=[
        EspInteger.new('string_id', IntType.U32),
        EspString.new('desc', 'zstring'),
    ],
))

# Object Bounds -- 6 x int16 bounding box
OBND = EspSubRecord.new('OBND', 'Object Bounds', EspStruct.new('obnd', [
    EspInteger.new('x1', IntType.S16),
    EspInteger.new('y1', IntType.S16),
    EspInteger.new('z1', IntType.S16),
    EspInteger.new('x2', IntType.S16),
    EspInteger.new('y2', IntType.S16),
    EspInteger.new('z2', IntType.S16),
]))

# Keywords -- KSIZ (count) + KWDA (FormID array)
KSIZ = EspSubRecord.new('KSIZ', 'Keyword Count',
                         EspInteger.new('count', IntType.U32))

KWDA = EspSubRecord.new('KWDA', 'Keywords',
                         EspArray.new('keywords', EspFormID.new('keyword')))

# Model -- MODL (filename string)
MODL = EspSubRecord.new('MODL', 'Model Filename',
                         EspString.new('model', 'zstring'))

# Model texture data -- raw bytes, we don't interpret
MODT = EspSubRecord.new('MODT', 'Model Texture Data',
                         EspByteArray.new('data'))

# Icon filename
ICON = EspSubRecord.new('ICON', 'Icon Filename',
                         EspString.new('icon', 'zstring'))

# Sound references
YNAM = EspSubRecord.new('YNAM', 'Sound - Pick Up',
                         EspFormID.new('pickup_sound', ['SNDR']))

ZNAM = EspSubRecord.new('ZNAM', 'Sound - Put Down',
                         EspFormID.new('putdown_sound', ['SNDR']))

# Equipment Type
ETYP = EspSubRecord.new('ETYP', 'Equipment Type',
                         EspFormID.new('equip_type', ['EQUP']))

# Enchantment
EITM = EspSubRecord.new('EITM', 'Enchantment',
                         EspFormID.new('enchantment', ['ENCH']))

# Condition -- a fixed 32-byte block in which four slots change type
# depending on other fields of the same block. The layout is identical in
# Skyrim and Fallout 4; only the condition-function table differs, and
# the deciders pick the right one from ctx.game.
#
#   offset  field             varies with
#        4  Comparison Value  Type & 0x04 ("use global") -> GLOB ref
#       12  Parameter #1      the Function's declared parameter type
#       16  Parameter #2      the Function's declared parameter type
#       24  Reference         Run On == 2 ("Reference") -> REFR-ish ref
#
# Getting these wrong is silent either way: remapping a slot that holds
# an actor-value index corrupts the condition, and failing to remap one
# that holds a FormID retargets it at whatever the destination plugin's
# master list has at that index.
_CTDA_USE_GLOBAL = 0x04
_CTDA_RUN_ON_REFERENCE = 2


def _condition_parameter_decider(which: int):
    """Decide whether Parameter #1 (which=1) or #2 (which=2) is a form.

    An unknown function -- a script-extender addition, or a game with no
    table -- resolves to the plain-integer branch. That preserves the
    bytes verbatim, which is the safe answer: guessing "FormID" would
    rewrite an integer parameter.
    """
    def decide(ctx: EspContext) -> int:
        from .condition_functions import get_condition_function
        entry = get_condition_function(ctx.game, ctx.fields.get('function', -1))
        if entry is None:
            return 0
        return 1 if entry[which] else 0
    return decide


CONDITION = EspStruct.new('condition', [
    EspInteger.new('type', IntType.U8, formatter=None),
    EspByteArray.new('unused1', 3),
    EspUnion.new(
        'comparison',
        decider=lambda ctx: (
            1 if ctx.fields.get('type', 0) & _CTDA_USE_GLOBAL else 0),
        members=[
            EspFloat.new('comparison_value'),
            EspFormID.new('comparison_global', ['GLOB']),
        ]),
    EspInteger.new('function', IntType.U16),
    EspByteArray.new('unused2', 2),
    EspUnion.new(
        'param1',
        decider=_condition_parameter_decider(1),
        members=[
            EspInteger.new('param1', IntType.S32),
            EspFormID.new('param1'),
        ]),
    EspUnion.new(
        'param2',
        decider=_condition_parameter_decider(2),
        members=[
            EspInteger.new('param2', IntType.S32),
            EspFormID.new('param2'),
        ]),
    EspInteger.new('run_on', IntType.U32),
    EspUnion.new(
        'reference',
        decider=lambda ctx: (
            1 if ctx.fields.get('run_on', 0) == _CTDA_RUN_ON_REFERENCE else 0),
        members=[
            EspInteger.new('reference', IntType.U32),
            EspFormID.new('reference'),
        ]),
    EspInteger.new('param3', IntType.S32),
])

CTDA = EspSubRecord.new('CTDA', 'Condition', CONDITION)

# VMAD -- Virtual Machine Adapter (script data)
# This is extremely complex internally; treat as raw bytes for now.
VMAD = EspSubRecord.new('VMAD', 'Script Data',
                         EspByteArray.new('vmad'))

# Destructible -- simplified as raw bytes
DEST = EspSubRecord.new('DEST', 'Destructible Header',
                         EspByteArray.new('dest'))

# Template
TNAM_FORMID = lambda name, refs: EspSubRecord.new('TNAM', name,
                                                    EspFormID.new('template', refs))


# ---------------------------------------------------------------------------
# Common enums
# ---------------------------------------------------------------------------

WeaponAnimTypeEnum = EspEnum.new({
    0: 'HandToHandMelee',
    1: 'OneHandSword',
    2: 'OneHandDagger',
    3: 'OneHandAxe',
    4: 'OneHandMace',
    5: 'TwoHandSword',
    6: 'TwoHandAxe',
    7: 'Bow',
    8: 'Staff',
    9: 'Crossbow',
})

ArmorTypeEnum = EspEnum.new({
    0: 'Light Armor',
    1: 'Heavy Armor',
    2: 'Clothing',
})

ActorValueEnum = EspEnum.new({
    0: 'Aggression', 1: 'Confidence', 2: 'Energy', 3: 'Morality',
    4: 'Mood', 5: 'Assistance', 6: 'OneHanded', 7: 'TwoHanded',
    8: 'Archery', 9: 'Block', 10: 'Smithing', 11: 'HeavyArmor',
    12: 'LightArmor', 13: 'Pickpocket', 14: 'Lockpicking',
    15: 'Sneak', 16: 'Alchemy', 17: 'Speech', 18: 'Alteration',
    19: 'Conjuration', 20: 'Destruction', 21: 'Illusion',
    22: 'Restoration', 23: 'Enchanting',
    24: 'Health', 25: 'Magicka', 26: 'Stamina',
    27: 'HealRate', 28: 'MagickaRate', 29: 'StaminaRate',
    30: 'SpeedMult', 31: 'InventoryWeight', 32: 'CarryWeight',
    33: 'CriticalChance', 34: 'MeleeDamage', 35: 'UnarmedDamage',
    36: 'Mass', 37: 'VoicePoints', 38: 'VoiceRate',
    39: 'DamageResist', 40: 'PoisonResist', 41: 'ResistFire',
    42: 'ResistShock', 43: 'ResistFrost', 44: 'ResistMagic',
    45: 'ResistDisease', 46: 'Unknown 46', 47: 'Unknown 47',
    48: 'Unknown 48', 49: 'Unknown 49', 50: 'Unknown 50',
    51: 'Unknown 51', 52: 'Unknown 52', 53: 'Paralysis',
    54: 'Invisibility', 55: 'NightEye', 56: 'DetectLifeRange',
    57: 'WaterBreathing', 58: 'WaterWalking', 59: 'Unknown 59',
    60: 'Fame', 61: 'Infamy', 62: 'JumpingBonus', 63: 'WardPower',
    64: 'RightItemCharge', 65: 'ArmorPerks', 66: 'ShieldPerks',
    67: 'WardDeflection', 68: 'Variable01', 69: 'Variable02',
    70: 'Variable03', 71: 'Variable04', 72: 'Variable05',
    73: 'Variable06', 74: 'Variable07', 75: 'Variable08',
    76: 'Variable09', 77: 'Variable10',
})

SoundLevelEnum = EspEnum.new({
    0: 'Loud',
    1: 'Normal',
    2: 'Silent',
    3: 'Very Loud',
})

SkillEnum = EspEnum.new({
    -1: 'None',
    6: 'One Handed', 7: 'Two Handed', 8: 'Archery', 9: 'Block',
    10: 'Smithing', 11: 'Heavy Armor', 12: 'Light Armor',
    13: 'Pickpocket', 14: 'Lockpicking', 15: 'Sneak',
    16: 'Alchemy', 17: 'Speech', 18: 'Alteration',
    19: 'Conjuration', 20: 'Destruction', 21: 'Illusion',
    22: 'Restoration', 23: 'Enchanting',
})


# ---------------------------------------------------------------------------
# Common flags
# ---------------------------------------------------------------------------

WeaponFlags = EspFlags.new({
    0: 'Ignores Normal Weapon Resistance',
    1: 'Automatic',
    2: 'Has Scope',
    3: "Can't Drop",
    4: 'Hide Backpack',
    5: 'Embedded Weapon',
    6: "Don't Use 1st Person IS Anim",
    7: 'Non-Playable',
})

WeaponFlags2 = EspFlags.new({
    0: 'Player Only',
    1: 'NPCs Use Ammo',
    2: 'No Fixed Duration',
    3: 'No Enchantment',
    4: 'Minor Crime',
    5: 'Ranges Fixed',
    6: 'Not Used in Normal Combat',
    7: 'Unknown 8',
    8: 'No 3rd Person IS Anim',
    9: 'Burst Shot',
    10: 'Alternate Rumble',
    11: 'Long Bursts',
    12: 'Non-Hostile',
    13: 'Bound Weapon',
})

FactionFlags = EspFlags.new({
    0: 'Hidden From NPC',
    1: 'Special Combat',
    6: 'Track Crime',
    7: 'Ignore Crimes: Murder',
    8: 'Ignore Crimes: Assault',
    9: 'Ignore Crimes: Stealing',
    10: 'Ignore Crimes: Trespass',
    11: 'Do Not Report Crimes Against Members',
    12: 'Crime Gold - Use Defaults',
    13: 'Ignore Crimes: Pickpocket',
    14: 'Vendor',
    15: 'Can Be Owner',
    16: 'Ignore Crimes: Werewolf',
})

LeveledItemFlags = EspFlags.new({
    0: 'Calculate from all levels <= player level',
    1: 'Calculate for each item in count',
    2: 'Use All',
    3: 'Special Loot',
})
