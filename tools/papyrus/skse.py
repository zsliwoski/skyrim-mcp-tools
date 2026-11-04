"""Detect whether the Skyrim Script Extender (SKSE) is present in the game and the script environment."""
from pathlib import Path

LOADERS = ("skse64_loader.exe", "sksevr_loader.exe", "skse_loader.exe")
# Script names SKSE ships that vanilla does not, so seeing one means SKSE scripts are installed or used.
SKSE_SCRIPTS = ("ModEvent", "StringUtil", "UI", "UICallback", "SKSE", "NetImmerse", "WornObject")


def _find_sources(dirs, name):
    return [str(d) for d in dirs if (Path(d) / f"{name}.psc").is_file()]


def detect_skse(data_dir, output_dir, source_dirs=()):
    """Report SKSE evidence: loader and runtime DLLs beside the game, SKSE plugins in Data/SKSE/Plugins,
    SKSE script files in Data/Scripts, whether the compiler's source folders can see SKSE sources, and
    which scripts in the output folder already reference SKSE-only script types."""
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    root = data_dir.parent
    loader = [n for n in LOADERS if (root / n).is_file()]
    runtime = sorted(p.name for p in root.glob("skse*.dll")) if root.is_dir() else []
    plugins_dir = data_dir / "SKSE" / "Plugins"
    plugins = sorted(p.name for p in plugins_dir.glob("*.dll")) if plugins_dir.is_dir() else []
    scripts_dir = data_dir / "Scripts"
    pex = [n for n in SKSE_SCRIPTS if (scripts_dir / f"{n}.pex").is_file()]
    compile_sources = _find_sources([Path(d) for d in source_dirs], "ModEvent")

    users = []
    out_scripts = output_dir / "Scripts"
    if out_scripts.is_dir():
        names = ("ModEvent", "StringUtil", "UICallback", "NetImmerse", "WornObject")
        for f in sorted(out_scripts.glob("*.pex")):
            data = f.read_bytes().lower()
            hits = [n for n in names if n.lower().encode() in data]
            if hits:
                users.append({"script": f.stem, "references": hits})

    installed = bool(loader and runtime) or bool(pex)
    notes = []
    if installed and not compile_sources:
        notes.append("SKSE is installed but its script sources (ModEvent.psc etc.) were not found, so SKSE calls "
                     "will not compile; put them in Data/Scripts/Source or a folder named in SKYRIM_SKSE_SOURCES")
    if not installed and users:
        notes.append("scripts in the output folder reference SKSE types but SKSE was not found")
    return {
        "installed": installed,
        "loader": loader,
        "runtime_dlls": runtime,
        "plugins": plugins,
        "skse_scripts_in_data": pex,
        "compiler_sees_skse_sources": compile_sources,
        "output_scripts_using_skse": users,
        "notes": notes,
    }
