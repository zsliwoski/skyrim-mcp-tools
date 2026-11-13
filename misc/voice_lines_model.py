"""Data side of voice_lines_gui: list a plugin's spoken dialogue lines with speaker and voice file lookup,
and read/write the loose voice files under Sound\\Voice\\<plugin>\\<voice type>\\."""
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.core.quest_model import Model, u32
from tools.dialogue.dialogue_tools import FN_GET_IS_ALIAS_REF, FN_GET_IS_ID, FN_GET_IS_VOICE_TYPE, conditions

AUDIO_EXTS = ('.wav', '.fuz', '.xwm')


@dataclass
class Line:
    info: object
    quest: str
    topic: str
    prompt: str
    response: int
    text: str
    speaker: str
    voice_types: list = field(default_factory=list)  # candidate voice type editor IDs
    stem: str = ''  # voice file name without extension
    plugin_dir: str = ''  # Sound\Voice\<this> folder name (the plugin that owns the INFO)
    audio: Path | None = None  # the file found, or None
    emotion: int = 0
    emotion_value: int = 50
    use_emotion_anim: bool = False
    speaker_idle: int = 0  # absolute form ID of the speaker's body animation (IDLE), 0 for none
    listener_idle: int = 0
    editable: bool = False  # the INFO is defined in the loaded plugin
    index: int = 0  # position of the response within its INFO
    npcs: list = field(default_factory=list)  # absolute form IDs of the speaking NPCs
    bsa: tuple | None = None  # (bsa path, internal path) when the audio is inside an archive


EMOTIONS = ['Neutral', 'Anger', 'Disgust', 'Fear', 'Sad', 'Happy', 'Surprise', 'Puzzled']


def response_groups(subrecords):
    """[[subrecord, ...]] one list per response: a TRDT and the subrecords that follow it up to the next TRDT."""
    groups = []
    for sr in subrecords:
        if sr.signature == 'TRDT':
            groups.append([])
        if groups and sr.signature in ('TRDT', 'NAM1', 'NAM2', 'NAM3', 'NAM4', 'SNAM', 'LNAM'):
            groups[-1].append(sr)
    return groups


def response_numbers(info):
    """[(response number, text subrecord data, group)] for an INFO's responses."""
    out = []
    for n, group in enumerate(response_groups(info.subrecords), 1):
        trdt = group[0].data
        nam1 = next((s for s in group if s.signature == 'NAM1'), None)
        if nam1 is not None:
            out.append((trdt[12] if len(trdt) > 12 else n, nam1.data, group))
    return out


def response_fields(model, info, group):
    """Emotion, body animation and flags of one response, with idle form IDs made absolute."""
    trdt = bytes(group[0].data).ljust(24, b'\0')
    idle = {s.signature: model.absid(info.plugin, u32(s.data)) for s in group
            if s.signature in ('SNAM', 'LNAM') and len(s.data) == 4}
    return {'emotion': struct.unpack_from('<I', trdt, 0)[0], 'emotion_value': struct.unpack_from('<I', trdt, 4)[0],
            'use_emotion_anim': bool(trdt[20]), 'speaker_idle': idle.get('SNAM', 0),
            'listener_idle': idle.get('LNAM', 0)}


def name_of(model, fid):
    rec = model.get(fid)
    if rec is None:
        return f"{fid:08X}"
    return rec.full_name or rec.editor_id or f"{fid:08X}"


def quest_aliases(model, qrec):
    """{alias id: label} for a quest: the NPC filling it as a unique actor, else the alias name."""
    out, cur = {}, None
    for sr in qrec.subrecords:
        if sr.signature == 'ALST':
            cur = u32(sr.data)
            out[cur] = f"alias {cur}"
        elif sr.signature == 'ALID' and cur is not None:
            out[cur] = sr.data.rstrip(b'\0').decode('cp1252', 'replace')
        elif sr.signature == 'ALUA' and cur is not None:
            out[cur] = name_of(model, model.absid(qrec.plugin, u32(sr.data)))
    return out


def npc_voice(model, npc_fid):
    npc = model.get(npc_fid)
    vtck = npc.get_subrecord('VTCK') if npc else None
    vt = model.get(model.absid(npc.plugin, u32(vtck.data))) if vtck else None
    return vt.editor_id if vt and vt.editor_id else None


def speakers(model, info, dial, qrec, aliases):
    """(speaker label, voice type candidates, speaker NPC form IDs) for an INFO from its speaker, GetIsID, alias and voice type conditions."""
    names, voices, npcs = [], [], []
    spk = info.get_subrecord('ANAM')
    if spk and u32(spk.data):
        fid = model.absid(info.plugin, u32(spk.data))
        names.append(name_of(model, fid))
        voices.append(npc_voice(model, fid))
        npcs.append(fid)
    for rec in (info, dial):
        for fn, p1, eq in conditions(rec):
            if not eq:
                continue
            if fn == FN_GET_IS_ID:
                fid = model.absid(rec.plugin, p1)
                names.append(name_of(model, fid))
                voices.append(npc_voice(model, fid))
                npcs.append(fid)
            elif fn == FN_GET_IS_ALIAS_REF and p1 in aliases:
                names.append(aliases[p1])
            elif fn == FN_GET_IS_VOICE_TYPE:
                vt = model.get(model.absid(rec.plugin, p1))
                if vt and vt.editor_id:
                    voices.append(vt.editor_id)
    names = list(dict.fromkeys(names))
    voices = list(dict.fromkeys(v for v in voices if v))
    label = ', '.join(names) or (f"anyone ({', '.join(voices)})" if voices else 'anyone')
    return label, voices, list(dict.fromkeys(npcs))


def voice_stem(quest, topic, info_id, response):
    """CK voice file name: <quest, 10 chars>_<topic, 15 chars>_<INFO form id, load order byte zeroed>_<response number>."""
    return f"{quest[:10]}_{topic[:15]}_{info_id & 0xFFFFFF:08x}_{response}"


def owner_name(info):
    """Plugin whose Sound\\Voice folder holds the INFO's lines: the master that defines it, else the plugin itself."""
    masters = info.plugin.header.masters
    idx = info.form_id.value >> 24
    return masters[idx] if idx < len(masters) else info.plugin.file_path.name


def load_lines(plugin_path, progress=lambda s: None, model=None, master_dirs=()):
    """(Model, [Line]) for every spoken line in the plugin's own INFO records."""
    model = model or Model([plugin_path], progress, resolve_refs=False, master_dirs=master_dirs)
    target = Path(plugin_path).name.lower()
    quest_alias = {}
    rows = []
    for qfid, topics in model.dialogue.items():
        qrec = model.get(qfid)
        qname = (qrec.editor_id if qrec else None) or f"{qfid:08X}"
        if qfid not in quest_alias:
            quest_alias[qfid] = quest_aliases(model, qrec) if qrec else {}
        for dial, infos in topics:
            topic = dial.editor_id or f"{model.rid(dial):08X}"
            prompt = dial.full_name or ''
            for info in infos:
                if info.plugin.file_path.name.lower() != target:
                    continue
                who, voices, npcs = speakers(model, info, dial, qrec, quest_alias[qfid])
                owner = owner_name(info)
                for index, (num, data, group) in enumerate(response_numbers(info)):
                    text = model.string(data, info) or ''
                    rows.append(Line(info, qname, topic, prompt, num, text, who, list(voices),
                                     voice_stem(qname, topic, info.form_id.value, num), owner, npcs=npcs,
                                     editable=True, index=index, **response_fields(model, info, group)))
    return model, rows


def all_idles(model):
    """{editor ID: absolute form ID} of every loaded IDLE (body animation) record."""
    return {r.editor_id: fid for fid, r in model.by_sig['IDLE'].items() if r.editor_id}


def local_form_id(model, plugin, fid):
    """Absolute form ID -> form ID as stored in `plugin`; the defining plugin must already be a master."""
    if not fid:
        return 0
    rec = model.get(fid)
    if rec is None:
        raise ValueError(f"form {fid:08X} is not loaded")
    owner = owner_name(rec).lower()
    masters = [m.lower() for m in plugin.header.masters]
    if owner == plugin.file_path.name.lower():
        return (len(masters) << 24) | (fid & 0xFFFFFF)
    if owner not in masters:
        raise ValueError(f"{rec.editor_id or hex(fid)} comes from {owner_name(rec)}, which is not a master of "
                         f"{plugin.file_path.name}")
    return (masters.index(owner) << 24) | (fid & 0xFFFFFF)


def edit_response(model, plugin_path, line, text, emotion, emotion_value, use_emotion_anim, speaker_idle, listener_idle):
    """Rewrite one response of an INFO owned by the plugin: its text, emotion (type and value), emotion animation flag and
    speaker/listener body animations (absolute IDLE form IDs, 0 for none). A snapshot is taken first."""
    from esplib import Plugin
    from esplib.record import Record
    from tools.records.maintenance import all_records, snapshot

    plugin = Plugin.load(str(plugin_path))
    if getattr(plugin, 'is_localized', False):
        raise ValueError("this plugin uses string tables; edit the text in the Creation Kit or xEdit")
    own = len(plugin.header.masters)
    target = next((r for r in all_records(plugin) if r.signature == 'INFO'
                   and r.form_id.value == line.info.form_id.value), None)
    groups = response_groups(target.subrecords) if target is not None else []
    if not line.editable or target is None or line.index >= len(groups):
        raise ValueError("this line's INFO is not defined in the plugin; only its own lines can be edited")
    if not 0 <= emotion < len(EMOTIONS) or not 0 <= emotion_value <= 100:
        raise ValueError("emotion must be one of the listed types and its value 0-100")
    ids = {'SNAM': local_form_id(model, plugin, speaker_idle), 'LNAM': local_form_id(model, plugin, listener_idle)}
    snapshot(plugin_path, 'pre_dialogue_edit')

    group = groups[line.index]
    trdt = bytearray(bytes(group[0].data).ljust(24, b'\0'))
    struct.pack_into('<II', trdt, 0, emotion, emotion_value)
    trdt[20] = 1 if use_emotion_anim else 0
    group[0].data = bytearray(trdt)
    group[0].modified = True
    nam1 = next(s for s in group if s.signature == 'NAM1')
    nam1.data = bytearray(text.encode('cp1252', 'replace') + b'\0')
    nam1.modified = True
    for sig in ('SNAM', 'LNAM'):
        existing = next((s for s in group if s.signature == sig and len(s.data) == 4), None)
        if existing is not None and ids[sig]:
            existing.data = bytearray(struct.pack('<I', ids[sig]))
            existing.modified = True
        elif existing is not None:
            target.subrecords.remove(existing)
            group.remove(existing)
        elif ids[sig]:
            keep = ('NAM1', 'NAM2', 'NAM3', 'NAM4', 'SNAM')
            pos = max(target.subrecords.index(s) for s in group if s.signature in keep) + 1
            target.insert_subrecord(pos, sig, struct.pack('<I', ids[sig]))
            group[:] = response_groups(target.subrecords)[line.index]
    target.modified = True
    plugin.save(str(plugin_path))
    line.text, line.emotion, line.emotion_value = text, emotion, emotion_value
    line.use_emotion_anim, line.speaker_idle, line.listener_idle = use_emotion_anim, speaker_idle, listener_idle


def suggest_emotion(text):
    """(emotion index into EMOTIONS, value 0-100) guessed from the words of a line with NRCLex, or None when nothing
    stands out. NRC has no 'puzzled', so a question with no emotional words becomes Puzzled."""
    import re
    try:
        from nrclex import NRCLex
    except ImportError:
        raise RuntimeError("NRCLex is not installed; run: pip install nrclex")
    nrc_to_skyrim = {'anger': 1, 'disgust': 2, 'fear': 3, 'sadness': 4, 'joy': 5, 'surprise': 6}
    lex = NRCLex()
    lex.load_token_list(re.findall(r"[a-z']+", text.lower()))
    scores = {k: v for k, v in lex.raw_emotion_scores.items() if k in nrc_to_skyrim}
    if not scores:
        return (7, 50) if '?' in text else None
    best = max(scores.values())
    top = next(k for k in nrc_to_skyrim if scores.get(k) == best)
    return nrc_to_skyrim[top], min(100, 40 + 20 * best)


def set_emotions(plugin_path, changes):
    """Set (emotion, value) on many of the plugin's own lines at once: changes is [(line, emotion, value)].
    One snapshot is taken first. Returns the number of lines changed."""
    from esplib import Plugin
    from tools.records.maintenance import all_records, snapshot

    plugin = Plugin.load(str(plugin_path))
    targets = {r.form_id.value: r for r in all_records(plugin) if r.signature == 'INFO'}
    done = []
    for line, emotion, value in changes:
        target = targets.get(line.info.form_id.value)
        groups = response_groups(target.subrecords) if target is not None else []
        if not line.editable or line.index >= len(groups):
            continue
        done.append((line, target, groups[line.index][0], emotion, value))
    if not done:
        return 0
    snapshot(plugin_path, 'pre_emotion_suggest')
    for line, target, trdt_sub, emotion, value in done:
        trdt = bytearray(bytes(trdt_sub.data).ljust(24, b'\0'))
        struct.pack_into('<II', trdt, 0, emotion, value)
        trdt_sub.data = trdt
        trdt_sub.modified = True
        target.modified = True
    plugin.save(str(plugin_path))
    for line, _, _, emotion, value in done:
        line.emotion, line.emotion_value = emotion, value
    return len(done)


def all_voice_types(model):
    return sorted(r.editor_id for r in model.by_sig['VTYP'].values() if r.editor_id)


def voice_root(plugin_path, plugin_dir):
    return Path(plugin_path).parent / 'Sound' / 'Voice' / plugin_dir


def bsa_index(dirs, plugin_name):
    """{(voice folder, file stem): [(voice type, bsa path, internal path)]} for archives that could hold the
    plugin's voices (named after the plugin, or any archive with 'voices' in its name)."""
    from esplib.bsa import BsaReader
    index = {}
    stem = Path(plugin_name).stem.lower()
    seen = set()
    for d in dirs:
        for b in Path(d).glob('*.bsa'):
            if b.resolve() in seen or not (b.stem.lower().startswith(stem) or 'voices' in b.stem.lower()):
                continue
            seen.add(b.resolve())
            try:
                reader = BsaReader(str(b))
                reader.open()
                for f in reader.list_files():
                    parts = f.replace('/', '\\').split('\\')
                    if len(parts) == 5 and parts[0].lower() == 'sound' and parts[1].lower() == 'voice':
                        name = Path(parts[4])
                        if name.suffix.lower() in AUDIO_EXTS:
                            index.setdefault((parts[2].lower(), name.stem.lower()), []).append((parts[3], str(b), f))
                reader.close()
            except Exception:
                continue
    return index


def find_audio(line, plugin_path, search_dirs=(), bsas=None, voice_type=None):
    """Set line.audio / line.bsa from the voice folder (own output, Data folders, archives) holding the line.
    The given or known voice types are tried first, then any other voice type folder; a voice type found this way
    is added to line.voice_types."""
    line.audio = line.bsa = None
    vts = [voice_type] if voice_type else list(line.voice_types)
    low = [v.lower() for v in vts]
    ranked = lambda items: sorted(items, key=lambda it: (low.index(it[0].lower()) if it[0].lower() in low else len(low)))
    for root in [Path(plugin_path).parent, *map(Path, search_dirs)]:
        base = root / 'Sound' / 'Voice' / line.plugin_dir
        if not base.is_dir():
            continue
        for vt, folder in ranked([(d.name, d) for d in base.iterdir() if d.is_dir()]):
            for ext in AUDIO_EXTS:
                f = folder / (line.stem + ext)
                if f.is_file():
                    line.audio = f
                    if vt not in line.voice_types:
                        line.voice_types.append(vt)
                    return line
    hits = (bsas or {}).get((line.plugin_dir.lower(), line.stem.lower()))
    if hits:
        vt, bsa, inner = ranked(hits)[0]
        line.bsa = (bsa, inner)
        if vt not in line.voice_types:
            line.voice_types.append(vt)
    return line


def find_ffmpeg():
    import shutil
    return shutil.which('ffmpeg')


def split_fuz(data):
    """(lip bytes, xwm bytes) of a .fuz file."""
    if data[:4] != b'FUZE':
        raise ValueError("not a FUZE file")
    lip_size = struct.unpack_from('<I', data, 8)[0]
    return data[12:12 + lip_size], data[12 + lip_size:]


def playable_wav(line):
    """Path to a WAV the player can play for the line's audio, decoding fuz/xwm through ffmpeg; None when no audio."""
    if line.audio is None and line.bsa is None:
        return None
    if line.audio is not None:
        name, data = line.audio.name, None
        if line.audio.suffix.lower() == '.wav':
            return line.audio
    else:
        from esplib.bsa import BsaReader
        reader = BsaReader(line.bsa[0])
        reader.open()
        data, name = reader.read_file(line.bsa[1]), line.bsa[1]
        reader.close()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to play .fuz/.xwm voice files; install it and add it to PATH")
    if data is None:
        data = line.audio.read_bytes()
    if name.lower().endswith('.fuz'):
        data = split_fuz(data)[1]
    tmp = Path(tempfile.gettempdir()) / f"voice_preview_{line.stem}.wav"
    src = tmp.with_suffix('.xwm')
    src.write_bytes(data)
    done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(src), str(tmp)],
                          capture_output=True, text=True)
    src.unlink(missing_ok=True)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "ffmpeg could not decode the file")
    return tmp


def line_wav_path(line, plugin_path, voice_type):
    return voice_root(plugin_path, line.plugin_dir) / voice_type / f"{line.stem}.wav"


def create_voice_type(plugin_path, editor_id, female=False, allow_default=True):
    """Add a VTYP record to the plugin (a snapshot is taken first) and return its editor ID and local form ID."""
    import re
    from esplib import Plugin
    from esplib.record import Record
    from esplib.utils import FormID
    from tools.core.plugin_edit import hi
    from tools.records.maintenance import snapshot

    if not re.fullmatch(r'[A-Za-z0-9_]+', editor_id or ''):
        raise ValueError("editor ID may contain only letters, digits and underscores")
    plugin = Plugin.load(str(plugin_path))
    if any((r.editor_id or '').lower() == editor_id.lower() for r in plugin.records):
        raise ValueError(f"{editor_id} already exists in {Path(plugin_path).name}")
    snapshot(plugin_path, 'pre_voice_type')
    own = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == own]
    oid = max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])
    rec = Record('VTYP', FormID((own << 24) | oid), 0)
    rec.add_subrecord('EDID', editor_id.encode('cp1252') + b'\0')
    rec.add_subrecord('DNAM', bytes([(1 if allow_default else 0) | (2 if female else 0)]))
    plugin.add_record(rec)
    plugin.header.next_object_id = oid + 1
    plugin.save(str(plugin_path))
    return {'editor_id': editor_id, 'form_id': (own << 24) | oid}


def assign_voice_type(plugin_path, npc_form_ids, vtyp_form_id):
    """Point the plugin's own NPCs at a voice type (NPC_ VTCK is a single 4-byte form ID). Returns the NPC editor IDs changed."""
    import struct
    from esplib import Plugin
    from tools.npcs.npc_builder import NPC_RANK, NPC_ORDER

    plugin = Plugin.load(str(plugin_path))
    changed = []
    for rec in plugin.records:
        if rec.signature != 'NPC_' or rec.form_id.value not in npc_form_ids:
            continue
        data = struct.pack('<I', vtyp_form_id)
        sr = rec.get_subrecord('VTCK')
        if sr is not None:
            sr.data = bytearray(data)
            sr.modified = True
        else:
            rec.add_subrecord('VTCK', data)
            rec.subrecords.sort(key=lambda s: NPC_RANK.get(s.signature, len(NPC_ORDER)))
        rec.modified = True
        changed.append(rec.editor_id or f"{rec.form_id.value:08X}")
    if changed:
        from tools.records.maintenance import snapshot
        snapshot(plugin_path, 'pre_voice_assign')
        plugin.save(str(plugin_path))
    return changed


def import_audio(line, src, plugin_path, voice_type):
    """Copy an audio file in as the line's voice file. .wav/.fuz/.xwm are copied as they are; anything else is
    converted to 44.1 kHz 16-bit mono WAV with ffmpeg. Any other format of the same line in that folder is removed."""
    import shutil
    src = Path(src)
    ext = src.suffix.lower()
    dest = voice_root(plugin_path, line.plugin_dir) / voice_type / f"{line.stem}{ext if ext in AUDIO_EXTS else '.wav'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for other in AUDIO_EXTS:
        (dest.parent / f"{line.stem}{other}").unlink(missing_ok=True)
    if ext in AUDIO_EXTS:
        shutil.copyfile(src, dest)
        return dest
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(f"ffmpeg is needed to convert {ext} files; use a .wav, .fuz or .xwm, or install ffmpeg")
    done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(src), '-ar', '44100', '-ac', '1',
                           '-sample_fmt', 's16', str(dest)], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "ffmpeg could not convert the file")
    return dest


def find_voice_tools(data_dir):
    """{'xwmaencode', 'lipgenerator', 'lipfuzer'} paths from the Skyrim SE Tools folder next to Data (or SKYRIM_TOOLS_DIR);
    missing ones are left out."""
    import os
    roots = [Path(os.environ['SKYRIM_TOOLS_DIR'])] if os.environ.get('SKYRIM_TOOLS_DIR') else []
    roots.append(Path(data_dir).parent / 'Tools')
    names = {'xwmaencode': 'Audio/xwmaencode.exe', 'lipgenerator': 'LipGen/LipGenerator/LipGenerator.exe',
             'lipfuzer': 'LipGen/LipFuzer/LIPFuzer.exe'}
    return {k: root / rel for k, rel in names.items() for root in roots if (root / rel).is_file()}


def convert_to_fuz(line, plugin_path, tools):
    """Turn the line's loose WAV into a .fuz (xWMA audio + generated lip sync), delete the WAV and return the .fuz path."""
    missing = [k for k in ('xwmaencode', 'lipgenerator', 'lipfuzer') if k not in tools]
    if missing:
        raise RuntimeError(f"missing tools: {', '.join(missing)} (need the Skyrim SE Tools folder)")
    wav = line.audio
    if wav is None or wav.suffix.lower() != '.wav':
        raise RuntimeError("the line has no loose .wav recording to convert")
    ffmpeg = find_ffmpeg()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / f"{line.stem}.wav"
        # LipGenerator and xWMAEncode want plain 16-bit PCM
        if ffmpeg:
            done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(wav), '-ar', '44100', '-ac', '1',
                                   '-sample_fmt', 's16', str(src)], capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(done.stderr.strip() or "ffmpeg could not prepare the WAV")
        else:
            src.write_bytes(wav.read_bytes())

        def run(cmd, what):
            done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=td)
            if done.returncode:
                raise RuntimeError(f"{what} failed: {(done.stdout + done.stderr).strip()[:400]}")

        text = ' '.join(line.text.replace('"', "'").split()) or ' '
        run([tools['lipgenerator'], src, text], 'LipGenerator')
        lip = td / f"{line.stem}.lip"
        if not lip.is_file():
            raise RuntimeError("LipGenerator did not produce a .lip file")
        run([tools['xwmaencode'], '-b', '64000', src, td / f"{line.stem}.xwm"], 'xWMAEncode')
        run([tools['lipfuzer'], '-s', td, '-d', td, '--norec', '-v', '1'], 'LIPFuzer')
        fuz = td / f"{line.stem}.fuz"
        if not fuz.is_file():
            raise RuntimeError("LIPFuzer did not produce a .fuz file")
        dest = wav.with_suffix('.fuz')
        shutil_copy(fuz, dest)
    wav.unlink(missing_ok=True)
    line.audio = dest
    return dest


def original_backup_path(plugin_path, audio):
    """Where the untouched copy of a voice file lives: <plugin folder>/voice_originals/<same relative path>.
    Outside Sound/, so it is never packaged or installed."""
    base = Path(plugin_path).parent
    return base / 'voice_originals' / Path(audio).relative_to(base)


def drop_originals(plugin_path, line):
    """Forget saved originals of a line whose audio was just replaced (re-record/upload), so Restore can't bring back stale audio."""
    root = Path(plugin_path).parent / 'voice_originals'
    if root.is_dir():
        for f in root.rglob(f"{line.stem}.*"):
            if f.suffix.lower() in AUDIO_EXTS:
                f.unlink(missing_ok=True)


def restore_original(plugin_path, line):
    """Put back the file saved before the first volume/pitch adjust. False when the line has no saved original."""
    import shutil
    if line.audio is None:
        return False
    backup = original_backup_path(plugin_path, line.audio)
    if not backup.is_file():
        return False
    shutil.copyfile(backup, line.audio)
    backup.unlink()
    return True


def adjust_audio(line, plugin_path, gain_db=0.0, semitones=0.0, tools=None):
    """Change a loose voice file's volume (dB) and pitch (semitones, duration kept so the lip sync still fits) in place.
    .wav is rewritten directly; .fuz/.xwm are decoded, adjusted and re-encoded with xwmaencode (keeping the .fuz lip data).
    The file as it was before the first adjust is saved once (see original_backup_path) so restore_original can undo
    any number of stacked adjusts."""
    path = line.audio
    if path is None:
        raise RuntimeError("the line has no loose audio file")
    ext = path.suffix.lower()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to adjust volume and pitch; install it and add it to PATH")
    if ext != '.wav' and 'xwmaencode' not in (tools or {}):
        raise RuntimeError("xwmaencode (Skyrim SE Tools) is needed to re-encode .fuz/.xwm files")
    original = path.read_bytes()
    factor = 2 ** (semitones / 12)
    filters = ['aresample=44100']
    if semitones:
        filters += [f'asetrate={44100 * factor:.3f}', 'aresample=44100', f'atempo={1 / factor:.6f}']
    if gain_db:
        filters += [f'volume={gain_db}dB', 'alimiter=limit=0.97']
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / 'in.wav' if ext == '.wav' else td / 'in.xwm'
        lip = None
        if ext == '.wav':
            src.write_bytes(original)
        elif ext == '.fuz':
            lip, xwm = split_fuz(original)
            src.write_bytes(xwm)
        else:
            src.write_bytes(original)
        out = td / 'out.wav'
        done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', str(src), '-af', ','.join(filters),
                               '-ac', '1', '-ar', '44100', '-sample_fmt', 's16', str(out)], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or "ffmpeg could not process the audio")
        if ext == '.wav':
            result = out.read_bytes()
        else:
            xwm_out = td / 'out.xwm'
            enc = subprocess.run([str(tools['xwmaencode']), '-b', '64000', str(out), str(xwm_out)],
                                 capture_output=True, text=True, cwd=td)
            if enc.returncode or not xwm_out.is_file():
                raise RuntimeError(f"xWMAEncode failed: {(enc.stdout + enc.stderr).strip()[:400]}")
            xwm_bytes = xwm_out.read_bytes()
            result = xwm_bytes if ext == '.xwm' else (
                original[:8] + struct.pack('<I', len(lip)) + lip + xwm_bytes)
    backup = original_backup_path(plugin_path, path)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
    path.write_bytes(result)
    return path


def shutil_copy(src, dest):
    import shutil
    shutil.copyfile(src, dest)


def list_input_devices():
    """Recording devices as [(waveIn id, name)]; names are truncated to 31 characters by the winmm API."""
    import ctypes
    from ctypes import wintypes

    class CAPS(ctypes.Structure):
        _fields_ = [('mid', wintypes.WORD), ('pid', wintypes.WORD), ('ver', wintypes.UINT),
                    ('name', ctypes.c_wchar * 32), ('formats', wintypes.DWORD),
                    ('channels', wintypes.WORD), ('res', wintypes.WORD)]

    winmm = ctypes.windll.winmm
    out = []
    for i in range(winmm.waveInGetNumDevs()):
        caps = CAPS()
        if winmm.waveInGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.name))
    return out


class Recorder:
    """Microphone capture to 16-bit mono 44.1 kHz WAV through the Windows waveIn API (no extra packages).
    device is a waveIn id from list_input_devices(), or None for the system default."""

    RATE = 44100
    BUF_BYTES = 8820
    BUF_COUNT = 16

    def __init__(self, device=None):
        self.device = device
        self.active = False

    def start(self):
        import ctypes
        import threading
        from ctypes import wintypes

        class FMT(ctypes.Structure):
            _pack_ = 1
            _fields_ = [('tag', wintypes.WORD), ('channels', wintypes.WORD), ('rate', wintypes.DWORD),
                        ('avg', wintypes.DWORD), ('align', wintypes.WORD), ('bits', wintypes.WORD),
                        ('cb', wintypes.WORD)]

        class HDR(ctypes.Structure):
            _fields_ = [('data', ctypes.c_void_p), ('length', wintypes.DWORD), ('recorded', wintypes.DWORD),
                        ('user', ctypes.c_size_t), ('flags', wintypes.DWORD), ('loops', wintypes.DWORD),
                        ('next', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]

        self._HDR = HDR
        self.winmm = winmm = ctypes.windll.winmm
        winmm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, ctypes.POINTER(FMT),
                                     ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
        for fn in (winmm.waveInPrepareHeader, winmm.waveInUnprepareHeader, winmm.waveInAddBuffer):
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(HDR), wintypes.UINT]
        for fn in (winmm.waveInStart, winmm.waveInStop, winmm.waveInReset, winmm.waveInClose):
            fn.argtypes = [ctypes.c_void_p]

        fmt = FMT(1, 1, self.RATE, self.RATE * 2, 2, 16, 0)
        self.handle = ctypes.c_void_p()
        dev = 0xFFFFFFFF if self.device is None else self.device
        err = winmm.waveInOpen(ctypes.byref(self.handle), dev, ctypes.byref(fmt), 0, 0, 0)
        if err:
            raise RuntimeError(f"Could not open the recording device (waveIn error {err})")
        self.active = True
        self.chunks = []
        self.stopping = False
        self._bufs = [ctypes.create_string_buffer(self.BUF_BYTES) for _ in range(self.BUF_COUNT)]
        self._hdrs = []
        for b in self._bufs:
            h = HDR(ctypes.cast(b, ctypes.c_void_p), self.BUF_BYTES)
            winmm.waveInPrepareHeader(self.handle, ctypes.byref(h), ctypes.sizeof(h))
            winmm.waveInAddBuffer(self.handle, ctypes.byref(h), ctypes.sizeof(h))
            self._hdrs.append(h)
        if winmm.waveInStart(self.handle):
            self.cancel()
            raise RuntimeError("Could not start recording")
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _drain(self, requeue):
        import ctypes
        for h in self._hdrs:
            if h.flags & 1:  # WHDR_DONE
                self.chunks.append(ctypes.string_at(h.data, h.recorded))
                h.flags &= ~1
                h.recorded = 0
                if requeue:
                    self.winmm.waveInAddBuffer(self.handle, ctypes.byref(h), ctypes.sizeof(h))

    def _pump(self):
        import time
        while not self.stopping:
            self._drain(True)
            time.sleep(0.03)

    def stop(self, dest):
        """Stop and save to dest (parent folders created)."""
        import wave
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.stopping = True
            self._thread.join()
            self.winmm.waveInStop(self.handle)
            self.winmm.waveInReset(self.handle)
            self._drain(False)
            data = b''.join(self.chunks)
        finally:
            self.cancel()
        with wave.open(str(dest), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(data)

    def cancel(self):
        import ctypes
        if self.active:
            self.stopping = True
            self.winmm.waveInReset(self.handle)
            for h in self._hdrs:
                self.winmm.waveInUnprepareHeader(self.handle, ctypes.byref(h), ctypes.sizeof(h))
            self.winmm.waveInClose(self.handle)
            self.active = False