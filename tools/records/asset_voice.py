"""Asset duplication and voice-file tools, wrapping the helpers in misc/ (the GUI editors' data layer)."""
import re
import sys
from pathlib import Path

_MISC = str(Path(__file__).resolve().parents[2] / 'misc')
if _MISC not in sys.path:
    sys.path.insert(0, _MISC)

import assets_model as am  # noqa: E402
import voice_lines_model as vl  # noqa: E402
from esplib import Plugin  # noqa: E402


def _dirs(plugin_path, data_dir):
    data = Path(data_dir) if data_dir and Path(data_dir).is_dir() else None
    return [Path(plugin_path).parent] + ([data] if data else []), data


def _find_plugin(plugin_path):
    path = Path(plugin_path)
    if not path.is_file():
        raise FileNotFoundError(f"plugin not found: {path}")
    return path


def duplicate_assets(plugin_path, source, dest, editor_id=None, mode=None, model_name=None, data_dir=None):
    """Copy a record's mesh(es) and textures into the plugin's folders under `dest` and point a record at them.

    source: editor ID (or 8-digit form ID) of any record with a model path, or a texture set. Files go to
    Meshes\\<dest>\\ and Textures\\<dest>\\ next to the plugin; texture paths inside the copied NIFs are rewritten.
    mode: clone (new record under editor_id; the default when editor_id is given), repoint (edit the record, or
    override a vanilla one) or files (copy files only).
    """
    plugin_path = _find_plugin(plugin_path)
    dirs, data = _dirs(plugin_path, data_dir)
    model, rows = am.load_assets(plugin_path, master_dirs=[data] if data else ())
    low = str(source).lower()
    hits = [a for a in rows if a.edid.lower() == low or f'{a.fid:08x}' == low or f'{a.fid & 0xFFFFFF:06x}' == low]
    if not hits:
        raise ValueError(f"no record with a model or texture set matches {source!r}")
    if len(hits) > 1:
        raise ValueError(f"{source!r} is ambiguous: " + ', '.join(f'{a.sig} {a.edid} {a.fid:08X}' for a in hits[:8]))
    archives = am.archive_index(dirs, plugin_path.name)
    return am.duplicate_asset(model, plugin_path, hits[0], dest, editor_id, dirs, archives, data, mode, model_name)


def create_voice_type(plugin_path, editor_id, female=False, allow_default=True, data_dir=None):
    """Add a voice type (VTYP) to the plugin; record it as the NPC's voice with assign_voice_type."""
    return vl.create_voice_type(_find_plugin(plugin_path), editor_id, female, allow_default)


def assign_voice_type(plugin_path, npcs, voice_type, data_dir=None):
    """Set the voice type of NPCs defined in the plugin. npcs: editor IDs; voice_type: a VTYP editor ID (own or a
    master's, which must already be a master of the plugin)."""
    plugin_path = _find_plugin(plugin_path)
    _, data = _dirs(plugin_path, data_dir)
    model = vl.Model([plugin_path], lambda s: None, resolve_refs=False, master_dirs=[data] if data else ())
    plugin = Plugin.load(str(plugin_path))
    vt = [(f, r) for f, r in model.by_sig['VTYP'].items() if (r.editor_id or '').lower() == voice_type.lower()]
    if not vt:
        raise ValueError(f"no voice type {voice_type!r}")
    local = vl.local_form_id(model, plugin, vt[0][0])
    own = {(r.editor_id or '').lower(): r.form_id.value for r in plugin.records if r.signature == 'NPC_'}
    missing = [n for n in npcs if n.lower() not in own]
    if missing:
        raise ValueError(f"not NPCs defined in {plugin_path.name}: {', '.join(missing)}")
    changed = vl.assign_voice_type(plugin_path, {own[n.lower()] for n in npcs}, local)
    return {'voice_type': voice_type, 'npcs': changed}


def _pick_line(lines, line):
    low = str(line).lower()
    hits = [ln for ln in lines if ln.stem.lower() == low] or \
           [ln for ln in lines if low in ln.text.lower() or low in ln.stem.lower()]
    if len(hits) != 1:
        what = "matches no spoken line" if not hits else "is ambiguous"
        raise ValueError(f"{line!r} {what}" + (": " + '; '.join(f'{ln.stem} "{ln.text[:40]}"' for ln in hits[:8])
                                               if hits else ''))
    return hits[0]


def import_voice_audio(plugin_path, line, audio, voice_type=None, fuz=False, data_dir=None):
    """Put an audio file in as the voice of one of the plugin's dialogue lines.

    line: the voice file stem or part of the line's text (must pick one line). audio: a .wav/.fuz/.xwm, or any
    format ffmpeg reads (converted to 44.1 kHz mono WAV). voice_type defaults to the speaker's. fuz: also build the
    lip-synced .fuz (needs the Skyrim SE Tools folder next to Data). The tool does not synthesize speech.
    """
    plugin_path = _find_plugin(plugin_path)
    if not Path(audio).is_file():
        raise FileNotFoundError(f"audio file not found: {audio}")
    _, data = _dirs(plugin_path, data_dir)
    _, lines = vl.load_lines(plugin_path, master_dirs=[data] if data else ())
    ln = _pick_line(lines, line)
    vt = voice_type or (ln.voice_types[0] if ln.voice_types else None)
    if not vt:
        raise ValueError("this line's speaker has no voice type; pass voice_type (see create_voice_type)")
    if not re.fullmatch(r'[A-Za-z0-9_]+', vt):
        raise ValueError("voice_type may contain only letters, digits and underscores")
    dest = vl.import_audio(ln, audio, plugin_path, vt)
    out = {'line': ln.stem, 'text': ln.text, 'voice_type': vt, 'file': str(dest)}
    if fuz:
        tools = vl.find_voice_tools(data or plugin_path.parent)
        ln.audio = dest
        out['file'] = str(vl.convert_to_fuz(ln, plugin_path, tools))
    return out
