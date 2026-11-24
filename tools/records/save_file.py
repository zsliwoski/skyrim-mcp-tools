"""Read the parts of a Skyrim SE save (.ess) that show whether a plugin's quests took effect.

Only the header, plugin lists, form ID array and the quest change forms are decoded; the rest of the
(large, mostly undocumented) body is skipped.
"""
import struct
import zlib
from pathlib import Path

QUST_TYPE = 8
# Quest change-form flags; the layouts were checked against real SE saves.
CHANGE_QUEST_FLAGS = 0x2
CHANGE_QUEST_SCRIPT_DELAY = 0x4
CHANGE_QUEST_STAGES = 0x80000000


class SaveError(ValueError):
    pass


class _Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def take(self, n):
        if self.p + n > len(self.d):
            raise SaveError("save file is truncated or has an unexpected layout")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def f32(self): return struct.unpack("<f", self.take(4))[0]

    def wstring(self):
        return self.take(self.u16()).decode("utf-8", errors="replace")


def _decode_ref(raw, form_ids):
    """Turn a 3-byte RefID into a full form ID using the save's form ID array."""
    kind, value = raw[0] >> 6, ((raw[0] & 0x3F) << 16) | (raw[1] << 8) | raw[2]
    if kind == 0:
        return 0 if value == 0 or value > len(form_ids) else form_ids[value - 1]
    if kind == 1:
        return value
    return 0xFF000000 | value


def read_header(path):
    """Header only: cheap enough to run on every save in the folder."""
    with open(path, "rb") as f:
        head = f.read(4096 * 4)
    return _header(_Reader(head))[0]


def _header(r):
    if r.take(13) != b"TESV_SAVEGAME":
        raise SaveError("not a Skyrim save file")
    size = r.u32()
    start = r.p
    h = {"version": r.u32(), "save_number": r.u32()}
    h["player"] = r.wstring()
    h["level"] = r.u32()
    h["location"] = r.wstring()
    h["game_date"] = r.wstring()
    h["race"] = r.wstring()
    r.u16(); r.f32(); r.f32()  # sex, exp, exp needed
    r.take(8)  # FILETIME
    w, hgt = r.u32(), r.u32()
    h["compression"] = r.u16() if h["version"] >= 12 else 0
    r.p = start + size
    return h, w, hgt


def _decompress(body, kind, raw_len):
    if kind == 0:
        return body
    if kind == 1:
        return zlib.decompress(body)
    if kind == 2:
        import lz4.block
        return lz4.block.decompress(body, uncompressed_size=raw_len)
    raise SaveError(f"unsupported save compression {kind}")


def load_save(path):
    """Decode header, plugin lists, form ID array and quest change forms of a save."""
    data = Path(path).read_bytes()
    r = _Reader(data)
    h, w, hgt = _header(r)
    r.take((4 if h["version"] >= 12 else 3) * w * hgt)
    if h["compression"]:
        raw_len, comp_len = r.u32(), r.u32()
        body = _decompress(r.take(comp_len), h["compression"], raw_len)
    else:
        body = data[r.p:]
    b = _Reader(body)
    b.u8()  # formVersion
    info_end = b.u32()
    info_end += b.p
    plugins = [b.wstring() for _ in range(b.u8())]
    light = [b.wstring() for _ in range(b.u16())] if h["version"] >= 12 else []
    b.p = info_end
    (form_id_off, _unk3, g1_off, _g2, cf_off, _g3, _c1, _c2, _c3, cf_count) = struct.unpack("<10I", b.take(40))
    b.take(60)
    # Table offsets count from the start of the file; global data table 1 follows the table directly.
    shift = g1_off - b.p
    form_ids = _read_form_ids(body, form_id_off - shift)
    quests = _read_quests(body, cf_off - shift, cf_count, form_ids)
    return {"header": h, "plugins": plugins, "light_plugins": light, "quests": quests}


def _read_form_ids(body, off):
    n = struct.unpack_from("<I", body, off)[0]
    return list(struct.unpack_from(f"<{n}I", body, off + 4))


def _read_quests(body, off, count, form_ids):
    r = _Reader(body, off)
    found = {}
    for _ in range(count):
        ref = r.take(3)
        flags = r.u32()
        t = r.u8()
        r.u8()  # change form version
        kind = t >> 6
        length1 = (r.u8, r.u16, r.u32)[kind]()
        length2 = (r.u8, r.u16, r.u32)[kind]()
        raw = r.take(length1)
        if t & 0x3F != QUST_TYPE:
            continue
        if length2:
            raw = zlib.decompress(raw)
        fid = _decode_ref(ref, form_ids)
        found[fid] = _decode_quest(raw, flags)
    return found


def _vsval(r):
    """Variable-size count: the low two bits of the first byte give the width."""
    b = r.u8()
    kind = b & 3
    if kind == 0:
        return b >> 2
    if kind == 1:
        return (b | r.u8() << 8) >> 2
    return (b | r.u8() << 8 | r.u8() << 16 | r.u8() << 24) >> 2


def _decode_quest(raw, flags):
    """Quest flag byte, then the stages the quest has reached (stage index and done flag)."""
    out = {"flags": None, "stages": []}
    r = _Reader(raw)
    try:
        if flags & CHANGE_QUEST_FLAGS:
            out["flags"] = r.u8()
            r.u8()
        if flags & CHANGE_QUEST_SCRIPT_DELAY:
            r.f32()
        if flags & CHANGE_QUEST_STAGES:
            for _ in range(_vsval(r)):
                out["stages"].append({"stage": r.u16(), "done": bool(r.u8())})
    except SaveError:
        out["partial"] = True
    return out


def find_saves(save_dir):
    """The .ess saves in a folder, newest first."""
    d = Path(save_dir)
    return sorted(d.glob("*.ess"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
