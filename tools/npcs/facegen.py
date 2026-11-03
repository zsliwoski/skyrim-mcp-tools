"""Read and regenerate Skyrim FaceGen data.

An NPC's face is stored twice: as morph values on the NPC_ record (NAM9 sliders, NAMA presets, NAM7
weight) and as baked geometry in a FaceGen NIF. The game shows the NIF, so editing the record alone
changes nothing for an NPC that has one. Each head part (HDPT) names three TRI morph files, and the
baked vertices of a part are

    base + race morph + SkinnyMorph * (1 - weight / 100) + presets + sum(|slider| * slider morph)

which reproduces the vanilla FaceGen files to well under a hundredth of a unit.
"""
import re
import struct
from functools import lru_cache

import numpy as np

# NAM9 order. Each slider runs -1..1; a positive value applies `pos`, a negative one applies `neg`.
SLIDERS = [
    ('nose_length', 'NoseLong', 'NoseShort', 'long / short nose'),
    ('nose_height', 'NoseUp', 'NoseDown', 'nose tip up / down'),
    ('jaw_height', 'JawDown', 'JawUp', 'jaw lower (longer face) / higher (shorter face)'),
    ('jaw_width', 'JawWide', 'JawNarrow', 'wide / narrow jaw'),
    ('jaw_forward', 'JawForward', 'JawBack', 'jaw forward / back (no effect on most male heads)'),
    ('cheek_height', 'CheeksUp', 'CheeksDown', 'cheeks up / down'),
    ('cheek_forward', 'CheeksOut', 'CheeksIn', 'cheeks out / in'),
    ('eye_height', 'EyesMoveUp', 'EyesMoveDown', 'eyes up / down'),
    ('eye_spacing', 'EyesMoveOut', 'EyesMoveIn', 'eyes further apart / closer'),
    ('brow_height', 'BrowUp', 'BrowDown', 'brows up / down'),
    ('brow_spacing', 'BrowOut', 'BrowIn', 'brows further apart / closer'),
    ('brow_forward', 'BrowForward', 'BrowBack', 'brows forward / back'),
    ('lip_depth', 'LipMoveOut', 'LipMoveIn', 'lips out / in'),
    ('lip_height', 'LipMoveUp', 'LipMoveDown', 'lips up / down'),
    ('chin_width', 'ChinWide', 'ChinThin', 'wide / narrow chin'),
    ('chin_height', 'ChinMoveDown', 'ChinMoveUp', 'chin lower (longer face) / higher'),
    ('bite', 'Underbite', 'Overbite', 'underbite / overbite'),
    ('eye_depth', 'EyesForward', 'EyesBack', 'eyes forward / back'),
]
SLIDER_KEYS = [s[0] for s in SLIDERS]
PRESETS = [('nose_type', 'NoseType', 0), ('eye_type', 'EyesType', 2), ('lip_type', 'LipType', 3)]
NAMA_NONE = 0xFFFFFFFF
TRI_BASE, TRI_RACE, TRI_CHARGEN = 1, 0, 2  # HDPT NAM0 values


class FaceGenError(ValueError):
    """Raised for FaceGen meshes or data the tools can't read or safely modify."""



def decode_face(npc):
    """Face values from an NPC_ record: sliders by name, preset indexes, weight and height."""
    nam9, nama = npc.get_subrecord('NAM9'), npc.get_subrecord('NAMA')
    raw = list(struct.unpack('<19f', bytes(nam9.data))) if nam9 is not None else [0.0] * 19
    face = {k: round(raw[i], 4) for i, k in enumerate(SLIDER_KEYS)}
    face['unknown_slider'] = raw[18] if abs(raw[18]) < 1e6 else 0.0
    vals = struct.unpack('<4I', bytes(nama.data)) if nama is not None else (NAMA_NONE,) * 4
    for key, _, pos in PRESETS:
        face[key] = None if vals[pos] == NAMA_NONE else vals[pos]
    face['weight'] = _float(npc, 'NAM7', 50.0)
    face['height'] = _float(npc, 'NAM6', 1.0)
    return face


def _float(rec, sig, default):
    """First float of a subrecord rounded to 4 places, or default if the subrecord is missing."""
    s = rec.get_subrecord(sig)
    return round(struct.unpack('<f', bytes(s.data)[:4])[0], 4) if s is not None else default


def encode_face(npc, face, original):
    """Write changed face values back into an NPC_ record (NAM9, NAMA, NAM7)."""
    nam9, nama = npc.get_subrecord('NAM9'), npc.get_subrecord('NAMA')
    raw = (list(struct.unpack('<19f', bytes(nam9.data))) if nam9 is not None
           else [0.0] * 18 + [struct.unpack('<f', b'\xff\xff\x7f\x7f')[0]])
    if any(face[k] != original[k] for k in SLIDER_KEYS):
        for i, k in enumerate(SLIDER_KEYS):
            raw[i] = float(face[k])
        data = struct.pack('<19f', *raw)
        if nam9 is not None:
            nam9.data = data
        else:
            npc.add_subrecord('NAM9', data)
    if any(face[k] != original[k] for k, _, _ in PRESETS):
        vals = (list(struct.unpack('<4I', bytes(nama.data))) if nama is not None
                else [NAMA_NONE] * 4)
        for key, _, pos in PRESETS:
            vals[pos] = NAMA_NONE if face[key] is None else int(face[key])
        data = struct.pack('<4I', *vals)
        if nama is not None:
            nama.data = data
        else:
            npc.add_subrecord('NAMA', data)
    if face['weight'] != original['weight']:
        data = struct.pack('<f', float(face['weight']))
        sub = npc.get_subrecord('NAM7')
        if sub is not None:
            sub.data = data
        else:
            npc.add_subrecord('NAM7', data)


def merge_face(current, changes):
    """Apply user changes (slider values -1..1, presets, weight) to a decoded face; returns (face, errors)."""
    face, errors = dict(current), []
    for key, value in (changes or {}).items():
        if key in SLIDER_KEYS:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not -1 <= value <= 1:
                errors.append(f"{key}: must be a number from -1 to 1")
            else:
                face[key] = float(value)
        elif key in ('nose_type', 'eye_type', 'lip_type'):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                errors.append(f"{key}: must be a preset number or null")
            else:
                face[key] = value
        elif key == 'weight':
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                errors.append("weight: must be 0-100")
            else:
                face[key] = float(value)
        else:
            errors.append(f"unknown face field {key!r}; use {SLIDER_KEYS + ['nose_type', 'eye_type', 'lip_type', 'weight']}")
    return face, errors


# -- TRI / NIF ---------------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _parse_tri(data):
    """Return (vertex count, base positions, {morph name: (file offset of int16 deltas, scale)}); cached by file bytes."""
    nv, nt, _, _, _, ntc, _, nm = struct.unpack_from('<8I', data, 8)
    base = np.frombuffer(data, '<f4', nv * 3, 64).reshape(nv, 3).astype(np.float64)
    i = 64 + nv * 12 + nt * 12 + ntc * 8 + nt * 12
    names, offsets = [], {}
    for _ in range(nm):
        (n,) = struct.unpack_from('<I', data, i)
        name = data[i + 4:i + 4 + n].rstrip(b'\0').decode('latin-1')
        (scale,) = struct.unpack_from('<f', data, i + 4 + n)
        offsets[name] = (i + 8 + n, scale)
        names.append(name)
        i += 8 + n + nv * 6
    return nv, base, offsets


class Tri:
    """A FaceGen .tri: base vertices plus named morphs, decoded on demand."""

    def __init__(self, data):
        self.data = bytes(data)
        self.nv, self.base, self.offsets = _parse_tri(self.data)

    def morph(self, name):
        """Per-vertex (n, 3) deltas for the named morph, int16 values multiplied by the morph's scale."""
        off, scale = self.offsets[name]
        return np.frombuffer(self.data, '<i2', self.nv * 3, off).reshape(self.nv, 3) * float(scale)

    def has(self, name):
        return name in self.offsets

    def names(self):
        return list(self.offsets)


class Nif:
    """The BSDynamicTriShape blocks of a FaceGen NIF (positions are rewritten in place)."""

    def __init__(self, data):
        d = self.buf = bytearray(data)
        i = d.index(b'\n') + 2
        _, _, nb, _ = struct.unpack_from('<IIII', d, i)
        i += 16
        for _ in range(3):
            i += 1 + d[i]
        (nt,) = struct.unpack_from('<H', d, i)
        i += 2
        types = []
        for _ in range(nt):
            (n,) = struct.unpack_from('<I', d, i)
            types.append(bytes(d[i + 4:i + 4 + n]).decode())
            i += 4 + n
        idx = struct.unpack_from('<%dH' % nb, d, i)
        i += 2 * nb
        sizes = struct.unpack_from('<%dI' % nb, d, i)
        i += 4 * nb
        (ns,) = struct.unpack_from('<I', d, i)
        i += 8
        strings = []
        for _ in range(ns):
            (n,) = struct.unpack_from('<I', d, i)
            strings.append(bytes(d[i + 4:i + 4 + n]).decode('latin-1'))
            i += 4 + n
        i += 4
        self.shapes = {}
        for b in range(nb):
            if types[idx[b]] == 'BSDynamicTriShape':
                name = strings[struct.unpack_from('<I', d, i)[0]]
                end, count = i + sizes[b], self._vertex_count(i, sizes[b])
                self.shapes[name] = (end - count * 16, count)
            i += sizes[b]

    def _vertex_count(self, start, size):
        """Vertex count n of a dynamic shape block, found by matching the trailing 16*n-byte array's size field."""
        for n in range(1, (size - 8) // 16 + 1):
            at = start + size - n * 16 - 4
            if struct.unpack_from('<I', self.buf, at)[0] == n * 16:
                return n
        raise FaceGenError("could not locate the dynamic vertices of a face shape")

    def positions(self, name):
        """(n, 3) float64 vertex positions of a dynamic shape; each vertex is 16 bytes (xyz plus a fourth float)."""
        off, n = self.shapes[name]
        return np.frombuffer(self.buf, '<f4', n * 4, off).reshape(n, 4)[:, :3].astype(np.float64)

    def set_positions(self, name, xyz):
        """Overwrite the xyz of each vertex in place, leaving the fourth float untouched."""
        off, n = self.shapes[name]
        view = np.frombuffer(self.buf, '<f4', n * 4, off).reshape(n, 4)
        view[:, :3] = xyz.astype(np.float32)

    def bytes(self):
        return bytes(self.buf)


def predict(base, race, chargen, race_edid, face):
    """Vertices of one head part for a face. base/race/chargen are Tri objects (race/chargen may be None)."""
    v = base.base.copy()
    if base.has('SkinnyMorph'):
        v += base.morph('SkinnyMorph') * (1.0 - face['weight'] / 100.0)
    if race is not None and race.has(race_edid):
        v += race.morph(race_edid)
    if chargen is not None:
        for key, pos, neg, _ in SLIDERS:
            value = face[key]
            name = pos if value > 0 else neg
            if value and chargen.has(name):
                v += abs(value) * chargen.morph(name)
        for key, prefix, _ in PRESETS:
            if face[key] is not None and chargen.has(f"{prefix}{face[key]}"):
                v += chargen.morph(f"{prefix}{face[key]}")
    return v


def part_tris(hdpt, load):
    """(base, race, chargen) Tri objects for a HDPT record; `load(path)` returns bytes or None."""
    paths, kind = {}, None
    for s in hdpt.subrecords:
        if s.signature == 'NAM0':
            kind = struct.unpack('<I', bytes(s.data))[0]
        elif s.signature == 'NAM1' and kind is not None:
            paths[kind] = bytes(s.data).rstrip(b'\0').decode('latin-1')
    out = []
    for k in (TRI_BASE, TRI_RACE, TRI_CHARGEN):
        data = load('Meshes\\' + paths[k]) if k in paths else None
        out.append(Tri(data) if data else None)
    return out


def regenerate(nif_bytes, face, race_edid, hdpt_by_name, load, old_face=None):
    """Update the dynamic vertices of every shape that has slider morphs. Returns (bytes, report).

    With old_face the existing geometry is shifted by the difference; without it, rebuilt absolutely."""
    nif, report = Nif(nif_bytes), {}
    for name in nif.shapes:
        hdpt = hdpt_by_name.get(name.lower())
        if hdpt is None:
            report[name] = 'skipped (no matching head part)'
            continue
        base, race, chargen = part_tris(hdpt, load)
        if base is None or chargen is None:
            report[name] = 'unchanged (part has no morph targets)'
            continue
        if base.nv != nif.shapes[name][1]:
            report[name] = f"skipped (vertex count {nif.shapes[name][1]} != {base.nv})"
            continue
        new = predict(base, race, chargen, race_edid, face)
        if old_face is not None:
            # apply only the change, so effects the model doesn't capture (e.g. mouth) stay as shipped
            new = nif.positions(name) + new - predict(base, race, chargen, race_edid, old_face)
        nif.set_positions(name, new)
        report[name] = 'regenerated'
    return nif.bytes(), report


def check(nif_bytes, face, race_edid, hdpt_by_name, load):
    """How closely the model reproduces an existing FaceGen NIF: {shape: (rms, max)} in game units."""
    nif, out = Nif(nif_bytes), {}
    for name in nif.shapes:
        hdpt = hdpt_by_name.get(name.lower())
        if hdpt is None:
            continue
        base, race, chargen = part_tris(hdpt, load)
        if base is None or chargen is None or base.nv != nif.shapes[name][1]:
            continue
        err = np.linalg.norm(predict(base, race, chargen, race_edid, face) - nif.positions(name), axis=1)
        out[name] = (float(np.sqrt((err ** 2).mean())), float(err.max()))
    return out


def describe_sliders():
    """Slider key -> human label."""
    return {k: label for k, _, _, label in SLIDERS}


COLORS = {'blood red': (140, 12, 12), 'red': (170, 20, 20), 'black': (15, 15, 15), 'white': (235, 235, 235),
          'blue': (35, 70, 160), 'green': (40, 110, 50), 'yellow': (210, 180, 40), 'orange': (200, 100, 20),
          'purple': (100, 40, 130)}


def parse_color(value):
    """RGB tuple from a COLORS name or #rrggbb hex string; raises FaceGenError otherwise."""
    v = str(value).strip().lower()
    if v in COLORS:
        return COLORS[v]
    h = v.lstrip('#')
    if len(h) == 6 and all(c in '0123456789abcdef' for c in h):
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    raise FaceGenError(f"unknown colour {value!r}; use #rrggbb or one of {sorted(COLORS)}")


def apply_paint(tint_dds, mask_dds, rgb, opacity=1.0):
    """Blend a flat colour through a tint mask (white = painted) into a FaceTint DDS; returns DXT5 DDS bytes."""
    import io
    from PIL import Image
    tint = Image.open(io.BytesIO(tint_dds)).convert('RGBA')
    mask = Image.open(io.BytesIO(mask_dds)).convert('L').resize(tint.size)
    a = np.asarray(mask, dtype=np.float64)[..., None] / 255.0 * float(opacity)
    t = np.asarray(tint, dtype=np.float64)
    t[..., :3] = t[..., :3] * (1 - a) + np.array(rgb, dtype=np.float64) * a
    out = Image.fromarray(t.round().clip(0, 255).astype('uint8'), 'RGBA')
    return _write_bgra_dds(out)


def _write_bgra_dds(img):
    """Uncompressed A8R8G8B8 DDS with a full mip chain (Pillow's DXT5 writer is unreliable)."""
    from PIL import Image
    mips, level = [], img
    while True:
        mips.append(level)
        if min(level.size) == 1:
            break
        level = level.resize((max(1, level.width // 2), max(1, level.height // 2)), Image.BOX)
    header = struct.pack('<4sIIIIIII44sII4s5I', b'DDS ', 124, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | 0x20000,
                         img.height, img.width, img.width * 4, 0, len(mips), b'\0' * 44,
                         32, 0x41, b'\0\0\0\0', 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    header += struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    body = b''
    for m in mips:
        a = np.asarray(m, dtype=np.uint8)
        body += a[..., [2, 1, 0, 3]].tobytes()
    return header + body


# -- block-level NIF surgery (adding/removing a head part shape) -------------------------------

class _Layout:
    """Header tables and block spans of a Skyrim SE NIF (20.2.0.7, user version 12, BS version 100)."""

    def __init__(self, data):
        d = self.d = bytes(data)
        i = d.index(b'\n') + 1
        self.pre = d[:i + 9]  # version line, version, endian, user version
        i += 5
        self.user, self.nb, self.bsver = struct.unpack_from('<III', d, i)
        if (self.user, self.bsver) != (12, 100):
            raise FaceGenError("only Skyrim Special Edition (BS version 100) FaceGen meshes are supported")
        i += 12
        start = i
        for _ in range(3):
            i += 1 + d[i]
        self.exports = d[start:i]
        (nt,) = struct.unpack_from('<H', d, i)
        i += 2
        self.types = []
        for _ in range(nt):
            (n,) = struct.unpack_from('<I', d, i)
            self.types.append(d[i + 4:i + 4 + n].decode())
            i += 4 + n
        self.idx = list(struct.unpack_from('<%dH' % self.nb, d, i))
        i += 2 * self.nb
        self.sizes = list(struct.unpack_from('<%dI' % self.nb, d, i))
        i += 4 * self.nb
        (ns, self.maxlen) = struct.unpack_from('<II', d, i)
        i += 8
        self.strings = []
        for _ in range(ns):
            (n,) = struct.unpack_from('<I', d, i)
            self.strings.append(d[i + 4:i + 4 + n].decode('latin-1'))
            i += 4 + n
        (ng,) = struct.unpack_from('<I', d, i)
        if ng:
            raise FaceGenError("unexpected group table in NIF")
        i += 4
        self.body_start = i
        self.blocks, pos = [], i
        for s in self.sizes:
            self.blocks.append(d[pos:pos + s])
            pos += s
        self.footer = d[pos:]

    def type_of(self, b):
        return self.types[self.idx[b]]

    def name_of(self, b):
        n = struct.unpack_from('<i', self.blocks[b], 0)[0]
        return self.strings[n] if 0 <= n < len(self.strings) else None

    def find(self, name, kinds):
        """Index of the first block of one of the given types with this name, or None."""
        return next((b for b in range(self.nb) if self.type_of(b) in kinds and self.name_of(b) == name), None)

    def dump(self):
        """Re-serialise the header and blocks; the string table and type list may have grown since parsing."""
        out = bytearray(self.pre)
        out += struct.pack('<I', self.nb) + struct.pack('<I', self.bsver)
        out += self.exports + struct.pack('<H', len(self.types))
        for t in self.types:
            out += struct.pack('<I', len(t)) + t.encode()
        out += struct.pack('<%dH' % self.nb, *self.idx) + struct.pack('<%dI' % self.nb, *self.sizes)
        out += struct.pack('<II', len(self.strings), max(len(s) for s in self.strings))
        for s in self.strings:
            out += struct.pack('<I', len(s)) + s.encode('latin-1')
        out += struct.pack('<I', 0) + b''.join(self.blocks) + self.footer
        return bytes(out)


def _shape_refs(blk):
    """Offset of a BSTriShape's (skin, shader, alpha) refs: after name, extras, controller, transform, bounds."""
    (extra,) = struct.unpack_from('<I', blk, 4)
    return 4 + 4 + 4 * extra + 4 + 4 + 12 + 36 + 4 + 4 + 16


def _node_children(blk):
    """(offset of the child count, child count) in a NiNode block, using the same fixed prefix as _shape_refs."""
    (extra,) = struct.unpack_from('<I', blk, 4)
    at = 4 + 4 + 4 * extra + 4 + 4 + 12 + 36 + 4 + 4
    return at, struct.unpack_from('<I', blk, at)[0]


def _intern(lay, s):
    """Index of s in the layout's string table, appending it if absent."""
    if s not in lay.strings:
        lay.strings.append(s)
    return lay.strings.index(s)


def _add_type(lay, t):
    """Index of block type t in the layout's type list, appending it if absent."""
    if t not in lay.types:
        lay.types.append(t)
    return lay.types.index(t)


def add_shape(host_bytes, donor_bytes, shape_name):
    """Copy a head-part shape (with skin, shader, textures) from a donor FaceGen NIF into the host NIF."""
    host, donor = _Layout(host_bytes), _Layout(donor_bytes)
    if host.find(shape_name, ('BSDynamicTriShape',)) is not None:
        raise FaceGenError(f"{shape_name} is already in the mesh")
    src = donor.find(shape_name, ('BSDynamicTriShape',))
    if src is None:
        raise FaceGenError(f"donor mesh has no shape {shape_name}")
    sblk = donor.blocks[src]
    at = _shape_refs(sblk)
    skin, shader, alpha = struct.unpack_from('<iii', sblk, at)
    skin_data, partition, root = struct.unpack_from('<iii', donor.blocks[skin], 0)
    (nbones,) = struct.unpack_from('<I', donor.blocks[skin], 12)
    bones = list(struct.unpack_from('<%di' % nbones, donor.blocks[skin], 16))
    (tex,) = struct.unpack_from('<i', donor.blocks[shader], _shader_tex_offset(donor.blocks[shader]))
    order = [src, skin, skin_data, partition, shader, tex] + ([alpha] if alpha >= 0 else [])
    new = {old: host.nb + k for k, old in enumerate(order)}

    def host_node(old):
        n = donor.name_of(old)
        b = host.find(n, ('NiNode', 'BSFadeNode'))
        if b is None:
            raise FaceGenError(f"host mesh has no node {n!r} to attach the skin to")
        return b

    copies = {}
    for old in order:
        blk = bytearray(donor.blocks[old])
        # name index offset: shapes and alpha start with it, shaders have the shader type first
        at_name = {'BSDynamicTriShape': 0, 'NiAlphaProperty': 0, 'BSLightingShaderProperty': 4}.get(donor.type_of(old))
        if at_name is not None:
            name = struct.unpack_from('<i', blk, at_name)[0]
            if 0 <= name < len(donor.strings):
                struct.pack_into('<i', blk, at_name, _intern(host, donor.strings[name]))
        copies[old] = blk
    struct.pack_into('<iii', copies[src], at, new[skin], new[shader], new[alpha] if alpha >= 0 else -1)
    struct.pack_into('<iii', copies[skin], 0, new[skin_data], new[partition], host_node(root))
    for k, b in enumerate(bones):
        struct.pack_into('<i', copies[skin], 16 + 4 * k, host_node(b))
    struct.pack_into('<i', copies[shader], _shader_tex_offset(donor.blocks[shader]), new[tex])

    parent = next((b for b in range(host.nb) if host.type_of(b) == 'NiNode' and any(
        host.type_of(c) == 'BSDynamicTriShape' for c in _children(host.blocks[b]))), None)
    if parent is None:
        raise FaceGenError("host mesh has no node holding the face shapes")
    pb = host.blocks[parent]
    cat, count = _node_children(pb)
    host.blocks[parent] = pb[:cat] + struct.pack('<I', count + 1) + pb[cat + 4:cat + 4 + 4 * count] \
        + struct.pack('<i', new[src]) + pb[cat + 4 + 4 * count:]
    host.sizes[parent] += 4
    for old in order:
        host.blocks.append(bytes(copies[old]))
        host.sizes.append(len(copies[old]))
        host.idx.append(_add_type(host, donor.type_of(old)))
    host.nb += len(order)
    return host.dump()


def _children(blk):
    at, count = _node_children(blk)
    return list(struct.unpack_from('<%di' % count, blk, at + 4))


def _shader_tex_offset(blk):
    (extra,) = struct.unpack_from('<I', blk, 8)
    return 4 + 4 + 4 + 4 * extra + 4 + 4 + 4 + 8 + 8
