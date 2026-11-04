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
