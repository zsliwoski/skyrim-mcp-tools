"""A tiny stand-in for Skyrim.esm so the tools can be tested without the game installed.

It holds only the vanilla records the tests clone or look up, under their real editor IDs, with
non-localized text and the same subrecord layouts as the game. Add a record here when a test needs one.
"""
import struct
from pathlib import Path

from esplib import Plugin
from esplib.record import GroupRecord, Record
from esplib.utils import FormID

from tools.records.enchant import MGEF_DATA

NON_PLAYABLE = 0x4
OBND = bytes(12)


def z(text):
    return text.encode("cp1252") + b"\0"


def add(plugin, sig, form_id, edid, subs, flags=0):
    rec = plugin.new_record(sig, edid, form_id=form_id, flags=flags)
    for s, data in subs:
        rec.add_subrecord(s, data)
    return rec


def ctda(function, value=0.0, op=0x00, param1=0):
    return ("CTDA", struct.pack("<B3xfH2xIIIIi", op, value, function, param1, 0, 0, 0, -1))


def mgef(plugin, form_id, edid, name, cast=1, delivery=1, base_cost=1.0, archetype=0, flags=None, conditions=()):
    data = bytearray(MGEF_DATA)
    struct.pack_into("<f", data, 4, base_cost)
    struct.pack_into("<I", data, 64, archetype)
    struct.pack_into("<II", data, 80, cast, delivery)
    if flags is not None:
        struct.pack_into("<I", data, 0, flags)
    add(plugin, "MGEF", form_id, edid, [("FULL", z(name)), ("DATA", bytes(data)), *conditions])


def package(plugin, form_id, edid, floats=(), location=None):
    """A Follow-style PACK: a header, no conditions, then float data inputs the tools can overwrite."""
    subs = [("PKDT", struct.pack("<IHHBBH", 0, 0, 0, 0, 0, 0)), ("PSDT", bytes(12)), ("PTDA", bytes(12))]
    if location is not None:
        subs.append(("PLDT", struct.pack("<iIi", 0, location, 0)))
    subs.append(("PKCU", struct.pack("<III", len(floats), 1, 0)))
    for value in floats:
        subs += [("ANAM", b"Float\0"), ("CNAM", struct.pack("<f", value))]
    subs.append(("UNAM", b"\0"))
    add(plugin, "PACK", form_id, edid, subs)


def exterior_world(plugin, edid, grid, base_edid, ref_pos):
    """A worldspace with a persistent cell and one exterior cell at `grid` holding a placed reference of base_edid."""
    wid, pers_id, cell_id, base_id, ref_id = 0x03C, 0x0D3C95, 0x0D3C96, 0x0D3C97, 0x0D3C98
    x, y = grid
    add(plugin, "STAT", base_id, base_edid, [("OBND", OBND)])

    def rec(sig, fid, subs):
        r = Record(sig, FormID(fid), 0)
        for s, data in subs:
            r.add_subrecord(s, data)
        plugin.records.append(r)
        r.plugin = plugin
        plugin._form_id_index[fid] = r
        if r.editor_id:
            plugin._editor_id_index[r.editor_id.lower()] = r
        plugin._signature_index.setdefault(sig, []).append(r)
        return r

    world = rec("WRLD", wid, [("EDID", z(edid)), ("FULL", z(edid))])
    pers = rec("CELL", pers_id, [("DATA", struct.pack("<H", 0x0))])
    cell = rec("CELL", cell_id, [("DATA", struct.pack("<H", 0x0)), ("XCLC", struct.pack("<iiI", x, y, 0))])
    ref = rec("REFR", ref_id, [("NAME", struct.pack("<I", base_id)),
                               ("DATA", struct.pack("<6f", *ref_pos, 0.0, 0.0, 0.0))])
    label = lambda a, b: ((a & 0xFFFF) << 16) | (b & 0xFFFF)
    temp = GroupRecord(9, cell_id)
    temp.records.append(ref)
    cell_children = GroupRecord(6, cell_id)
    cell_children.records.append(temp)
    sub = GroupRecord(5, label(x >> 3, y >> 3))
    sub.records += [cell, cell_children]
    block = GroupRecord(4, label(x >> 5, y >> 5))
    block.records.append(sub)
    wg = GroupRecord(1, wid)
    wg.records += [pers, block]
    top = GroupRecord(0, "WRLD")
    top.records += [world, wg]
    plugin.groups.append(top)


def build(path):
    """Write the fixture master to `path` (named Skyrim.esm) and return it."""
    p = Plugin.new_plugin(str(path), masters=[], game="tes5", is_esm=True)

    add(p, "MISC", 0x06F993, "Firewood01", [
        ("OBND", OBND), ("FULL", z("Firewood")), ("DATA", struct.pack("<If", 5, 5.0))])
    add(p, "WEAP", 0x01397E, "IronDagger", [
        ("OBND", OBND), ("FULL", z("Iron Dagger")), ("DATA", struct.pack("<IfH", 20, 2.0, 4))])
    add(p, "WEAP", 0x012EB7, "IronSword", [
        ("OBND", OBND), ("FULL", z("Iron Sword")), ("DATA", struct.pack("<IfH", 25, 9.0, 7)),
        ("DNAM", struct.pack("<B3xff", 1, 1.0, 1.0) + bytes(88))])
    add(p, "ARMO", 0x012E4B, "ArmorIronBoots", [
        ("OBND", OBND), ("FULL", z("Iron Boots")), ("DATA", struct.pack("<If", 25, 6.0)),
        ("DNAM", struct.pack("<i", 1000))])
    add(p, "ARMO", 0x0F1E8B, "ArmorThievesGuildHelmet", [
        ("OBND", OBND), ("FULL", z("Thieves Guild Hood")), ("DATA", struct.pack("<If", 50, 1.0))],
        flags=NON_PLAYABLE)
    add(p, "FACT", 0x050920, "JobJarlFaction", [("FULL", z("Jarl Faction")), ("DATA", bytes(4))])

    mgef(p, 0x10A7E1, "EnchFireDamageFFContact", "Fire Damage", base_cost=1.0, flags=0x00000002)
    mgef(p, 0x10A7E2, "EnchParalysisFFContact", "Paralysis", base_cost=0.0, archetype=0x15, flags=0x00401403,
         conditions=[ctda(77, 25.0, op=0x04), ctda(286, 0.0), ctda(286, 0.0), ctda(286, 0.0)])
    mgef(p, 0x10A7E3, "EnchFortifyDestructionConstantSelf", "Fortify Destruction", cast=0, delivery=0)
    mgef(p, 0x10A7E4, "ParalysisFFAimed", "Paralysis", cast=1, delivery=2, archetype=0x15)
    mgef(p, 0x10A7E5, "ParalysisFFSelfArea", "Paralysis", cast=1, delivery=0, archetype=0x15)
    effect = [("EFID", struct.pack("<I", 0x10A7E4)), ("EFIT", struct.pack("<fII", 0.0, 0, 5))]

    add(p, "ARMO", 0x0016B0, "ClothesCirclet01", [
        ("OBND", OBND), ("FULL", z("Circlet")), ("DATA", struct.pack("<If", 30, 0.5))])
    add(p, "BOOK", 0x0A26F8, "SpellTomeParalyze", [
        ("OBND", OBND), ("FULL", z("Spell Tome: Paralyze")), ("DESC", z("")),
        ("DATA", struct.pack("<BBxxIIf", 4, 0, 0, 500, 1.0))])
    add(p, "SCRL", 0x0A2EC2, "ParalyzeScroll", [
        ("OBND", OBND), ("FULL", z("Scroll of Paralyze")), ("DESC", z("")),
        ("SPIT", struct.pack("<IIIfIIffI", 0, 0, 0, 0.0, 3, 2, 0.0, 0.0, 0)), *effect])
    add(p, "WEAP", 0x0A26F9, "StaffParalyze", [
        ("OBND", OBND), ("FULL", z("Staff of Paralysis")), ("DESC", z("")),
        ("DATA", struct.pack("<IfH", 500, 8.0, 8)), ("EAMT", struct.pack("<H", 1000))])

    add(p, "EFSH", 0x0A7F2F, "MagicArmorEbonyFleshFXS", [("DATA", bytes(400))])

    add(p, "BOOK", 0x10F7F4, "Book1CheapHorkers", [
        ("OBND", OBND), ("FULL", z("Cheap Horkers")), ("DESC", z("Text.")),
        ("DATA", struct.pack("<BBxxIIf", 0, 0, 0, 5, 1.0))])
    add(p, "MISC", 0x00000F, "Gold001", [("OBND", OBND), ("FULL", z("Gold")), ("DATA", struct.pack("<If", 1, 0.0))])
    add(p, "DOOR", 0x0270A1, "ImpDoorSingleLoad01", [("OBND", OBND), ("FULL", z("Door"))])
    add(p, "STAT", 0x000010, "MapMarker", [("OBND", OBND)])
    add(p, "KYWD", 0x0130DB, "LocTypeDungeon", [])
    for n, edid in enumerate(("CraftingSmithingForge", "CraftingSmithingSharpeningWheel",
                              "CraftingSmithingArmorTable")):
        add(p, "KYWD", 0x088100 + n, edid, [])
    add(p, "MISC", 0x05AD93, "IngotIron", [("OBND", OBND), ("FULL", z("Iron Ingot")),
                                           ("DATA", struct.pack("<If", 1, 1.0))])
    add(p, "LVLI", 0x0DD000, "LItemTestLoot", [("OBND", bytes(12)), ("LVLD", b"\0"), ("LVLF", b"\x01"),
                                              ("LLCT", b"\x01"), ("LVLO", struct.pack("<HHIHH", 1, 0, 0x01397E, 1, 0))])
    add(p, "ACTI", 0x0FA000, "TestActivator", [("OBND", OBND), ("FULL", z("Lever")),
                                              ("MODL", z("Clutter\\Lever.nif")), ("MODT", bytes(12))])
    add(p, "SNDR", 0x0FA001, "TestSoundDescriptor", [])
    add(p, "SPEL", 0x0FA002, "TestAbilitySpell", [("FULL", z("Test Ability"))])
    add(p, "FACT", 0x0BD7AA, "CurrentHireling", [("FULL", z("Hireling")), ("DATA", bytes(4))])
    add(p, "QUST", 0x0750BA, "DialogueFollower", [("FULL", z("Follower")), ("DNAM", struct.pack("<HBBII", 0x11, 50, 0, 0, 0))])
    add(p, "QUST", 0x05A6DC, "DialogueFavorGeneric", [("FULL", z("Favor")), ("DNAM", struct.pack("<HBBII", 0x11, 50, 0, 0, 0))])
    add(p, "QUST", 0x047AE6, "DGIntimidateQuest", [("FULL", z("Brawl")), ("DNAM", struct.pack("<HBBII", 0x11, 50, 0, 0, 0))])
    add(p, "DIAL", 0x047AC5, "DGIntimidateVictoryYes", [("PNAM", struct.pack("<f", 50.0)),
        ("QNAM", struct.pack("<I", 0x047AE6)), ("DATA", bytes(4)), ("SNAM", b"CUST"), ("TIFC", bytes(4))])
    for n, (edid, level) in enumerate((("SpeechVeryEasy", 0), ("SpeechEasy", 25), ("SpeechAverage", 50),
                                       ("SpeechHard", 75), ("SpeechVeryHard", 100))):
        add(p, "GLOB", 0x0D16A3 + n, edid, [("FNAM", b"f"), ("FLTV", struct.pack("<f", float(level)))])

    add(p, "RACE", 0x013746, "NordRace", [("FULL", z("Nord")), ("DESC", z("Nord"))])

    from tools.npcs import commentary_presets as presets, npc_builder
    next_id = iter(range(0x300000, 0x310000))
    for edid in (*npc_builder.TRAVEL_KEYWORD_LINES, *presets.PLACE_KEYWORDS):
        add(p, "KYWD", next(next_id), edid, [])
    for edid in npc_builder.TRAVEL_CITY_LINES:
        add(p, "LCTN", next(next_id), edid, [("FULL", z(edid))])
    for edid, _ in presets.MAIN_QUEST:
        add(p, "QUST", next(next_id), edid, [("FULL", z(edid)), ("DNAM", struct.pack("<HBBII", 0, 50, 0, 0, 0))])
    add(p, "VTYP", 0x013AE5, "MaleNord", [("DNAM", bytes(1))])
    add(p, "CLAS", 0x01C000, "Citizen", [("FULL", z("Citizen")), ("DATA", bytes(28))])
    for form_id, edid, name, female in ((0x01347F, "Sven", "Sven", False), (0x0A2C8E, "Lydia", "Lydia", True)):
        add(p, "NPC_", form_id, edid, [
            ("OBND", OBND),
            ("ACBS", struct.pack("<IhhHHHHhHhH", 1 if female else 0, 0, 0, 1, 0, 0, 0, 100, 0, 0, 0)),
            ("VTCK", struct.pack("<I", 0x013AE5)), ("RNAM", struct.pack("<I", 0x013746)),
            ("AIDT", bytes(20)), ("CNAM", struct.pack("<I", 0x01C000)), ("FULL", z(name)), ("DATA", b""),
            ("DNAM", bytes(52)), ("NAM6", struct.pack("<f", 1.0)), ("NAM7", struct.pack("<f", 50.0)),
            ("NAM9", struct.pack("<19f", *([0.0] * 19))), ("NAMA", struct.pack("<4I", *([0xFFFFFFFF] * 4)))])

    package(p, 0x0F4F65, "PlayerFollowerPackage", floats=(128.0, 256.0))
    package(p, 0x0F4F66, "PlayerFollowerCombatOverridePackage", location=0)
    for pid, pname in ((0x0F4F67, "DefaultSandboxCurrentLocation1024"), (0x0F4F68, "DefaultSleepPackageAnyTime"),
                       (0x0F4F69, "DefaultEatMorning7x1")):
        package(p, pid, pname, location=0)
    exterior_world(p, "Tamriel", (4, -11), "WoodChoppingBlock", (18900.0, -43500.0, -100.0))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    p.save(str(path))
    return Path(path)
