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
    npcs: list = field(default_factory=list)  # absolute form IDs of the speaking NPCs
    bsa: tuple | None = None  # (bsa path, internal path) when the audio is inside an archive


def response_numbers(info):
    """[(response number, text subrecord data)] for an INFO, pairing each TRDT with the NAM1 that follows it."""
    out, num = [], 1
    for sr in info.subrecords:
        if sr.signature == 'TRDT' and len(sr.data) > 12:
            num = sr.data[12]
        elif sr.signature == 'NAM1':
            out.append((num, sr.data))
            num += 1
    return out


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


def load_lines(plugin_path, progress=lambda s: None, model=None):
    """(Model, [Line]) for every spoken line in the plugin's own INFO records."""
    model = model or Model([plugin_path], progress, resolve_refs=False)
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
                for num, data in response_numbers(info):
                    text = model.string(data, info) or ''
                    rows.append(Line(info, qname, topic, prompt, num, text, who, list(voices),
                                     voice_stem(qname, topic, info.form_id.value, num), owner, npcs=npcs))
    return model, rows


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
    """Point the plugin's own NPCs at a voice type (VTCK male and female). Returns the NPC editor IDs changed."""
    import struct
    from esplib import Plugin
    from tools.npcs.npc_builder import NPC_RANK, NPC_ORDER

    plugin = Plugin.load(str(plugin_path))
    changed = []
    for rec in plugin.records:
        if rec.signature != 'NPC_' or rec.form_id.value not in npc_form_ids:
            continue
        data = struct.pack('<II', vtyp_form_id, vtyp_form_id)
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


def shutil_copy(src, dest):
    import shutil
    shutil.copyfile(src, dest)


class Recorder:
    """Microphone capture to 16-bit mono WAV through the Windows MCI API (no extra packages)."""

    def __init__(self):
        import ctypes
        self.mci = ctypes.windll.winmm.mciSendStringW
        self.alias = 'voiceline'
        self.active = False

    def send(self, cmd):
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        err = self.mci(cmd, buf, 255, 0)
        if err:
            msg = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(err, msg, 255)
            raise RuntimeError(f"{msg.value} ({cmd})")
        return buf.value

    def start(self):
        self.send(f'open new type waveaudio alias {self.alias}')
        self.active = True
        try:
            self.send(f'set {self.alias} format tag pcm')
            self.send(f'set {self.alias} bitspersample 16 channels 1 samplespersec 44100 '
                      'alignment 2 bytespersec 88200')
            self.send(f'record {self.alias}')
        except RuntimeError:
            self.cancel()
            raise

    def stop(self, dest):
        """Stop and save to dest (parent folders created)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.send(f'stop {self.alias}')
            self.send(f'save {self.alias} "{dest}" wait')
        finally:
            self.cancel()

    def cancel(self):
        if self.active:
            try:
                self.send(f'close {self.alias}')
            except RuntimeError:
                pass
            self.active = False
