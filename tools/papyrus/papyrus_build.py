"""Compile Papyrus scripts with the Creation Kit compiler and build INFO fragment VMAD data."""
import os
import re
import subprocess
from pathlib import Path

from esplib.vmad import (PROP_OBJECT, VmadData, VmadFragment, VmadFragmentData, VmadObject,
                         VmadProperty, VmadScript)

CK_DIR = Path(os.environ.get("SKYRIM_CK_DIR", r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim"))
SOURCE_CACHE = Path(os.environ.get("SKYRIM_PAPYRUS_SOURCES", Path(__file__).resolve().parents[2] / "papyrus_src"))


class CompileError(ValueError):
    """Raised when the CK compiler or its source files are missing, or a compile produces no .pex."""


def skse_source_dirs(data_dir=None):
    """Folders holding SKSE script sources (recognised by ModEvent.psc): SKYRIM_SKSE_SOURCES (path-separated),
        then Data/Scripts/Source of the game. They go before the vanilla sources on the compiler's include path
        because SKSE ships extended copies of Form.psc, Actor.psc and others."""
    dirs = [Path(p) for p in os.environ.get("SKYRIM_SKSE_SOURCES", "").split(os.pathsep) if p]
    data = Path(data_dir or os.environ.get(
        "SKYRIM_DATA_DIR", r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"))
    dirs.append(data / "Scripts" / "Source")
    return [d for d in dirs if (d / "ModEvent.psc").is_file()]


def ensure_sources(ck_dir=CK_DIR, cache=SOURCE_CACHE):
    """Extract the vanilla script sources from the CK's Scripts.rar once; return the Source folder."""
    src = Path(cache) / "Scripts" / "Source"
    if (src / "TESV_Papyrus_Flags.flg").is_file():
        return src
    rar = Path(ck_dir) / "Data" / "Scripts.rar"
    if not rar.is_file():
        raise CompileError(f"{rar} not found; set SKYRIM_CK_DIR to the Creation Kit install folder")
    Path(cache).mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["tar", "-xf", str(rar), "-C", str(cache)], capture_output=True, text=True)
    if not (src / "TESV_Papyrus_Flags.flg").is_file():
        raise CompileError(f"could not extract {rar}: {res.stderr.strip()}")
    return src


def compile_script(name, source, scripts_dir, ck_dir=CK_DIR, cache=SOURCE_CACHE):
    """Write <name>.psc under scripts_dir/Source and compile it to scripts_dir/<name>.pex."""
    compiler = Path(ck_dir) / "Papyrus Compiler" / "PapyrusCompiler.exe"
    if not compiler.is_file():
        raise CompileError(f"{compiler} not found; set SKYRIM_CK_DIR to the Creation Kit install folder")
    vanilla = ensure_sources(ck_dir, cache)
    scripts_dir = Path(scripts_dir)
    mine = scripts_dir / "Source"
    mine.mkdir(parents=True, exist_ok=True)
    psc = mine / f"{name}.psc"
    psc.write_text(source, encoding="cp1252")
    pex = scripts_dir / f"{name}.pex"
    pex.unlink(missing_ok=True)
    includes = ";".join([str(mine.resolve())] + [str(d) for d in skse_source_dirs()] + [str(vanilla)])
    cmd = [str(compiler), f"{name}.psc", f"-f={vanilla / 'TESV_Papyrus_Flags.flg'}",
           f"-i={includes}", f"-o={scripts_dir.resolve()}"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=mine)
    out = (res.stdout + res.stderr).strip()
    if not pex.is_file():
        raise CompileError(f"Papyrus compile of {name} failed:\n{out}")
    return pex


def script_name(plugin_name, kind, object_id):
    """Fragment script name like TIF__MyPlugin_001A2B: plugin stem stripped to 16 alphanumerics, 6-digit hex object id."""
    stem = re.sub(r"[^A-Za-z0-9]", "", Path(plugin_name).stem)[:16]
    return f"{kind}__{stem}_{object_id:06X}"


def tif_source(name, code, props):
    """Source for a TopicInfo fragment that runs `code` when the line finishes. props: [(name, 'Form')]."""
    body = "\n".join(f"  {line.strip()}" for line in code.strip().splitlines())
    decls = "\n".join(f"{ptype} Property {pname} Auto" for pname, ptype in props)
    return (f"Scriptname {name} Extends TopicInfo Hidden\n\n"
            f"Function Fragment_0(ObjectReference akSpeakerRef)\n"
            f"  Actor akSpeaker = akSpeakerRef as Actor\n{body}\nEndFunction\n\n{decls}\n")


def info_vmad(name, props):
    """VMAD bytes for an INFO running Fragment_0 of script `name` at the end of the line.

    props: [(property name, local form ID)] — all Form properties.
    """
    script = VmadScript(name=name, flags=0, properties=[
        VmadProperty(name=n, type=PROP_OBJECT, flags=1, value=VmadObject(form_id=fid, alias=-1))
        for n, fid in props])
    frag = VmadFragmentData(extra_bind_version=2, filename=name, flags=2,
                            fragments=[VmadFragment(script_name=name, fragment_name="Fragment_0", unknown=1)])
    return VmadData(version=5, obj_format=2, scripts=[script], fragment_data=frag).to_bytes("INFO")


CURRENT_HIRELING = "0x000BD738"  # vanilla CurrentHireling faction in Skyrim.esm
KEEP_UP_SECONDS = 5.0


def keep_up_source(name, distance):
    """Actor script that moves a hired follower next to the player when he lags more than `distance` units behind.

    Skips combat, waiting (WaitingForPlayer) and anyone not in CurrentHireling.
    """
    return f"""Scriptname {name} Extends Actor

Event OnInit()
  RegisterForSingleUpdate({KEEP_UP_SECONDS})
EndEvent

Event OnLoad()
  RegisterForSingleUpdate({KEEP_UP_SECONDS})
EndEvent

Event OnUpdate()
  Actor player = Game.GetPlayer()
  Faction hired = Game.GetFormFromFile({CURRENT_HIRELING}, "Skyrim.esm") as Faction
  If IsInFaction(hired) && !IsDead() && !IsInCombat() && GetActorValue("WaitingForPlayer") == 0
    If !Is3DLoaded() || GetDistance(player) > {float(distance)}
      float behind = player.GetAngleZ() + 180.0
      MoveTo(player, 150.0 * Math.Sin(behind), 150.0 * Math.Cos(behind), 0.0)
    EndIf
  EndIf
  RegisterForSingleUpdate({KEEP_UP_SECONDS})
EndEvent
"""


HORSE_RACE = "0x000131FD"  # vanilla HorseRace in Skyrim.esm
CONTROL_TICK_SECONDS = 3.0


LEFT_BEHIND_DISTANCE = 3500.0
LEFT_BEHIND_TICKS = 5  # consecutive ticks too far away before he is brought back


def controller_source(name, horse_sharing=True, left_behind=False):
    """Quest script for a follower controller quest. Other scripts (dialogue fragments) call its functions.

    Properties: Follower and Steed (ReferenceAlias), HiredFaction and ShareFaction (Faction). With horse_sharing it
    provides ShareHorse(Actor) / StopSharing(Actor), and while the share faction rank is 1 an update loop mounts the
    follower on the steed whenever the player is mounted and dismounts him when the player is not.
    With left_behind (property LeftBehindFaction) the same loop notices a hired, non-waiting follower that stays out of
    reach, moves him to the player, and sets his LeftBehindFaction rank to 1 so dialogue can react to it.
    New controller features belong in the update loop or as more functions here.
    """
    head = f"Scriptname {name} Extends Quest\n\nReferenceAlias Property Follower Auto\n"
    if horse_sharing:
        head += "ReferenceAlias Property Steed Auto\n"
    head += "Faction Property HiredFaction Auto\n"
    if horse_sharing:
        head += "Faction Property ShareFaction Auto\n"
    if left_behind:
        head += "Faction Property LeftBehindFaction Auto\nTopic Property CaughtUpTopic Auto\nTopic Property SummonTopic Auto\nInt lostTicks = 0\n"
    if not (horse_sharing or left_behind):
        return head
    body = _HORSE_FUNCTIONS.format(tick=CONTROL_TICK_SECONDS, horse=HORSE_RACE) if horse_sharing else ""
    if left_behind:
        body += f"""
; Brings a hired follower back once he has been out of reach for a while
Function CheckLeftBehind(Actor f)
  Actor player = Game.GetPlayer()
  If !f.IsInFaction(HiredFaction) || f.IsDead() || f.IsInCombat() || f.GetActorValue("WaitingForPlayer") != 0
    lostTicks = 0
    Return
  EndIf
  If !f.Is3DLoaded() || f.GetDistance(player) > {LEFT_BEHIND_DISTANCE}
    lostTicks += 1
    If lostTicks >= {LEFT_BEHIND_TICKS}
      lostTicks = 0
      If f.IsOnMount()
        f.Dismount()
      EndIf
      Float behind = player.GetAngleZ() + 180.0
      f.MoveTo(player, 200.0 * Math.Sin(behind), 200.0 * Math.Cos(behind), 0.0)
      Debug.Notification(f.GetActorBase().GetName() + " has caught up with you.")
      React(f, CaughtUpTopic, 3, 1)
    EndIf
  Else
    lostTicks = 0
  EndIf
EndFunction

; Speaks a reaction topic while his LeftBehindFaction rank is akState (dialogue conditions read it), then sets akAfter
Function React(Actor f, Topic akTopic, Int akState, Int akAfter)
  If akTopic
    f.SetFactionRank(LeftBehindFaction, akState)
    f.Say(akTopic)
    Utility.Wait(1.0)
  EndIf
  f.SetFactionRank(LeftBehindFaction, akAfter)
EndFunction

; Called by a summon spell or any other trigger: teleports a hired follower to the player and lets him react
Function Summon()
  Actor f = Follower.GetActorReference()
  If !f || f.IsDead() || !f.IsInFaction(HiredFaction)
    Return
  EndIf
  Actor player = Game.GetPlayer()
  If f.IsOnMount()
    f.Dismount()
  EndIf
  Float behind = player.GetAngleZ() + 180.0
  f.MoveTo(player, 150.0 * Math.Sin(behind), 150.0 * Math.Cos(behind), 0.0)
  React(f, SummonTopic, 2, 0)
EndFunction
"""
    body += "\nEvent OnInit()\n  RegisterForSingleUpdate(" + str(CONTROL_TICK_SECONDS) + ")\nEndEvent\n\nEvent OnUpdate()\n"
    body += "  Actor f = Follower.GetActorReference()\n  If f\n"
    if left_behind:
        body += "    CheckLeftBehind(f)\n"
    if horse_sharing:
        body += "    HorseTick(f)\n"
    body += f"  EndIf\n  RegisterForSingleUpdate({CONTROL_TICK_SECONDS})\nEndEvent\n"
    return head + "\n" + body


_HORSE_FUNCTIONS = """Bool Function FindHorse(Actor akFollower)
  Actor ride = PickHorse(akFollower, True)
  If !ride
    Debug.Notification("There is no horse nearby for " + akFollower.GetActorBase().GetName() + " to ride.")
    Return False
  EndIf
  Steed.ForceRefTo(ride)
  Debug.Notification(akFollower.GetActorBase().GetName() + " has picked out a horse.")
  Return True
EndFunction

Bool Function ShareHorse(Actor akFollower)
  Actor ride = PickHorse(akFollower, False)
  If !ride
    Debug.Notification("There is no horse nearby for " + akFollower.GetActorBase().GetName() + " to ride.")
    Return False
  EndIf
  Steed.ForceRefTo(ride)
  akFollower.SetFactionRank(ShareFaction, 1)
  RegisterForSingleUpdate({tick})
  Return True
EndFunction

Bool Function GiftHorse(Actor akFollower)
  Actor mine = Game.GetPlayersLastRiddenHorse()
  If !mine || mine.IsDead()
    Debug.Notification("You have no horse to give.")
    Return False
  EndIf
  mine.SetActorOwner(akFollower.GetActorBase())
  Steed.ForceRefTo(mine)
  akFollower.SetFactionRank(ShareFaction, 1)
  RegisterForSingleUpdate({tick})
  Return True
EndFunction

Function ReclaimHorse(Actor akFollower)
  Actor ride = Steed.GetActorReference()
  If ride
    ride.SetActorOwner(Game.GetPlayer().GetActorBase())
  EndIf
  StopSharing(akFollower)
EndFunction

Function StopSharing(Actor akFollower)
  akFollower.SetFactionRank(ShareFaction, 0)
  If akFollower.IsOnMount()
    akFollower.Dismount()
  EndIf
  Steed.Clear()
EndFunction

; The current steed if it is still alive (unless akRescan), else the closest unowned or player-owned horse
Actor Function PickHorse(Actor akFollower, Bool akRescan)
  Actor current = Steed.GetActorReference()
  If current && !current.IsDead() && !akRescan
    Return current
  EndIf
  Race horseRace = Game.GetFormFromFile({horse}, "Skyrim.esm") as Race
  Actor mine = Game.GetPlayersLastRiddenHorse()
  ActorBase playerBase = Game.GetPlayer().GetActorBase()
  Actor best = None
  Float bestDistance = 100000.0
  Int tries = 0
  While tries < 40
    Actor found = Game.FindRandomActorFromRef(akFollower, 3000.0)
    If found && found != mine && found != current && found.GetRace() == horseRace && !found.IsDead()
      ActorBase owner = found.GetActorOwner()
      If !found.GetFactionOwner() && (!owner || owner == playerBase)
        Float d = found.GetDistance(akFollower)
        If d < bestDistance
          best = found
          bestDistance = d
        EndIf
      EndIf
    EndIf
    tries += 1
  EndWhile
  If !best && current && !current.IsDead()
    Return current
  EndIf
  Return best
EndFunction

Function HorseTick(Actor f)
  Actor ride = Steed.GetActorReference()
  If !ride
    Return
  EndIf
  If f.GetFactionRank(ShareFaction) != 1 || !f.IsInFaction(HiredFaction)
    If f.GetFactionRank(ShareFaction) == 1
      StopSharing(f)
    EndIf
    Return
  EndIf
  Actor player = Game.GetPlayer()
  If !f.IsDead() && !f.IsInCombat()
    If player.IsOnMount() && !f.IsOnMount()
      If ride.GetDistance(f) > 600.0
        ride.MoveTo(f, 150.0, 0.0, 0.0)
      EndIf
      ride.Activate(f)
    ElseIf !player.IsOnMount() && f.IsOnMount()
      f.Dismount()
    EndIf
  EndIf
EndFunction
"""


def summon_spell_source(script, controller, plugin_file, quest_object_id):
    """ActiveMagicEffect source for a self-cast spell that calls Summon() on the controller quest's script."""
    return (f"Scriptname {script} Extends ActiveMagicEffect\n\n"
            "Event OnEffectStart(Actor akTarget, Actor akCaster)\n"
            f"  Quest q = Game.GetFormFromFile({quest_object_id:#x}, \"{plugin_file}\") as Quest\n"
            f"  (q as {controller}).Summon()\n"
            "EndEvent\n")


def quest_script_vmad(rec, name, object_props, alias_props):
    """VMAD bytes attaching script `name` to a QUST (existing scripts and alias scripts are kept, a same-name script
    is replaced but keeps properties it already had that are not given again).

    object_props: [(property name, local form ID)]; alias_props: [(property name, alias ID)] bound to this quest's aliases.
    """
    props = [VmadProperty(name=n, type=PROP_OBJECT, flags=1, value=VmadObject(form_id=fid, alias=-1))
             for n, fid in object_props]
    props += [VmadProperty(name=n, type=PROP_OBJECT, flags=1,
                           value=VmadObject(form_id=rec.form_id.value, alias=a)) for n, a in alias_props]
    old = VmadData.from_record(rec, "QUST") if rec.get_subrecord("VMAD") is not None else None
    keep = [s for s in (old.scripts if old else []) if s.name != name]
    # properties set by an earlier run (e.g. topics filled in after dialogue exists) survive a rebuild
    given = {p.name for p in props}
    props += [p for s in (old.scripts if old else []) if s.name == name for p in s.properties if p.name not in given]
    return VmadData(version=5, obj_format=2, scripts=keep + [VmadScript(name=name, flags=0, properties=props)],
                    fragment_data=old.fragment_data if old else None,
                    alias_scripts=old.alias_scripts if old else []).to_bytes("QUST")


def npc_script_vmad(rec, name):
    """VMAD bytes for an NPC_ record with script `name` attached (existing scripts are kept)."""
    old = VmadData.from_record(rec, "NPC_") if rec.get_subrecord("VMAD") is not None else None
    keep = [s for s in (old.scripts if old else []) if s.name != name]
    return VmadData(version=5, obj_format=2, scripts=keep + [VmadScript(name=name, flags=0, properties=[])]
                    ).to_bytes("NPC_")
