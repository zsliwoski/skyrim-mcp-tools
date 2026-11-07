"""Minimal Skyrim SE NIF reader/rewriter: list and replace texture paths, and read triangle meshes for a preview.

Only what the asset duplicator needs: blocks are located through the header's size table, so unknown block types are
carried over byte for byte.
"""
import struct
from dataclasses import dataclass, field

MAGIC = b'Gamebryo File Format, Version '
SHAPES = ('BSTriShape', 'BSSubIndexTriShape', 'BSMeshLODTriShape', 'BSLODTriShape')


class NifError(ValueError):
    pass


@dataclass
class Nif:
    data: bytes
    types: list = field(default_factory=list)  # block type name per block
    sizes: list = field(default_factory=list)
    sizes_at: int = 0  # file offset of the block size table
    blocks_at: int = 0  # file offset of the first block
    bsver: int = 0
    strings: list = field(default_factory=list)  # the header string table (block names)
    def block(self, i):
        start = self.blocks_at + sum(self.sizes[:i])
        return start, self.data[start:start + self.sizes[i]]


def _short(d, p):
    return p + 1 + d[p]


def parse(data):
    data = bytes(data)
    if not data.startswith(MAGIC):
        raise NifError("not a NIF file")
    p = data.index(b'\n') + 1
    version, endian, user, nblocks, bsver = struct.unpack_from('<IBIII', data, p)
    if version != 0x14020007 or endian != 1:
        raise NifError(f"unsupported NIF version {version:08X}")
    p += 17
    last = None
    # Author, optional unknown int, process script, export script and (newer files) a max file path
    for extra in (False, True):
        try:
            q = _short(data, p)
            if bsver >= 131:
                q += 4
            else:
                q = _short(data, q)
            q = _short(data, q)
            if extra:
                q = _short(data, q)
            ntypes = struct.unpack_from('<H', data, q)[0]
            q += 2
            types = []
            for _ in range(ntypes):
                n = struct.unpack_from('<I', data, q)[0]
                types.append(data[q + 4:q + 4 + n].decode('ascii'))
                q += 4 + n
            idx = struct.unpack_from(f'<{nblocks}H', data, q)
            q += 2 * nblocks
            sizes_at = q
            sizes = list(struct.unpack_from(f'<{nblocks}I', data, q))
            q += 4 * nblocks
            nstr = struct.unpack_from('<I', data, q)[0]
            q += 8
            strings = []
            for _ in range(nstr):
                n = struct.unpack_from('<I', data, q)[0]
                strings.append(data[q + 4:q + 4 + n].decode('cp1252'))
                q += 4 + n
            ngroups = struct.unpack_from('<I', data, q)[0]
            q += 4 + 4 * ngroups
            if q + sum(sizes) <= len(data) and all(i < ntypes for i in idx):
                return Nif(data, [types[i] for i in idx], sizes, sizes_at, q, bsver, strings)
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            last = e
    raise NifError(f"could not read the NIF header ({last})")


def _texture_blocks(nif):
    return [i for i, t in enumerate(nif.types) if t == 'BSShaderTextureSet']


def texture_paths(nif):
    """Every non-empty texture path stored in the file's BSShaderTextureSet blocks, in order."""
    out = []
    for i in _texture_blocks(nif):
        out += [t for t in _read_set(nif.block(i)[1]) if t]
    return out


def _read_set(block):
    n = struct.unpack_from('<I', block)[0]
    p, out = 4, []
    for _ in range(n):
        ln = struct.unpack_from('<I', block, p)[0]
        out.append(block[p + 4:p + 4 + ln].decode('cp1252'))
        p += 4 + ln
    return out


def replace_textures(nif, mapping):
    """New NIF bytes with texture paths replaced. `mapping` is {lowercase old path: new path}."""
    data, sizes = bytearray(nif.data), list(nif.sizes)
    for i in reversed(_texture_blocks(nif)):
        start, block = nif.block(i)
        paths = [mapping.get(t.lower().replace('/', '\\'), t) for t in _read_set(block)]
        new = struct.pack('<I', len(paths)) + b''.join(struct.pack('<I', len(t)) + t.encode('cp1252') for t in paths)
        data[start:start + len(block)] = new
        sizes[i] = len(new)
    struct.pack_into(f'<{len(sizes)}I', data, nif.sizes_at, *sizes)
    return bytes(data)


def shapes(nif):
    """[(block index, vertices[(x, y, z)], triangles[(a, b, c)])] for the file's BSTriShape-family blocks."""
    return [s[:3] for s in textured_shapes(nif)]


def textured_shapes(nif):
    """[(block index, vertices, triangles, uvs[(u, v)] or [], diffuse texture path or '')] per shape."""
    out = []
    for i, t in enumerate(nif.types):
        if t not in SHAPES:
            continue
        try:
            out.append(_shape(nif, i))
        except (struct.error, ValueError, IndexError):
            continue
    return out


def _diffuse(nif, shader):
    """The first texture of the BSLightingShaderProperty's texture set, or ''."""
    if shader < 0 or nif.types[shader] != 'BSLightingShaderProperty':
        return ''
    b = nif.block(shader)[1]
    n = struct.unpack_from('<I', b, 8)[0]
    p = 12 + 4 * n + 4 + 8 + 16  # extra data refs, controller, shader flags, UV offset and scale
    ref = struct.unpack_from('<i', b, p)[0]
    if ref < 0 or nif.types[ref] != 'BSShaderTextureSet':
        return ''
    paths = _read_set(nif.block(ref)[1])
    return paths[0] if paths else ''


def _half(b, o):
    return struct.unpack_from('<2e', b, o)


def _shape(nif, i):
    _, b = nif.block(i)
    p = 4  # name string index
    n = struct.unpack_from('<I', b, p)[0]
    p += 4 + 4 * n + 4  # extra data refs, controller
    p += 4  # flags
    trans = struct.unpack_from('<3f', b, p)
    rot = struct.unpack_from('<9f', b, p + 12)
    scale = struct.unpack_from('<f', b, p + 48)[0]
    p += 52 + 4 + 16  # collision, bounding sphere
    skin, shader = struct.unpack_from('<ii', b, p)
    p += 12  # skin/shader/alpha refs
    desc, ntri, nvert, size = struct.unpack_from('<QHHI', b, p)
    p += 16
    stride = (desc & 0xF) * 4
    tris = None
    if size == 0:  # skinned: the geometry is stored in the NiSkinPartition
        if skin < 0:
            raise ValueError("no vertex data")
        part = struct.unpack_from('<i', nif.block(skin)[1], 4)[0]
        vb, tris = _partition(nif.block(part)[1])
        desc, stride, nvert, p, b = vb[0], vb[1], vb[2], 0, vb[3]
    if not stride or not (desc >> 44) & 1:
        raise ValueError("no vertex data")
    verts, uvs = [], []
    has_uv = bool((desc >> 44) & 2)
    for v in range(nvert):
        o = p + v * stride
        x, y, z = struct.unpack_from('<3f', b, o + ((desc >> 4) & 0xF) * 4)
        verts.append((
            scale * (rot[0] * x + rot[1] * y + rot[2] * z) + trans[0],
            scale * (rot[3] * x + rot[4] * y + rot[5] * z) + trans[1],
            scale * (rot[6] * x + rot[7] * y + rot[8] * z) + trans[2]))
        if has_uv:
            uvs.append(_half(b, o + ((desc >> 8) & 0xF) * 4))
    if tris is None:
        p += nvert * stride
        tris = [struct.unpack_from('<3H', b, p + t * 6) for t in range(ntri)]
    return i, verts, tris, uvs, _diffuse(nif, shader)


def _partition(b):
    """((vertex desc, stride, count, vertex bytes), triangles) from a Skyrim SE NiSkinPartition block."""
    nparts, size, stride, desc = struct.unpack_from('<IIIQ', b, 0)
    p = 20
    vdata = b[p:p + size]
    p += size
    tris = []
    for _ in range(nparts):
        nv, nt, nb, ns, nw = struct.unpack_from('<5H', b, p)
        p += 10 + 2 * nb
        if b[p]:
            p += 2 * nv
        p += 1
        if b[p]:
            p += 4 * nv * nw
        p += 1 + 2 * ns
        faces = b[p]
        p += 1
        if faces and not ns:
            tris += [struct.unpack_from('<3H', b, p + 6 * k) for k in range(nt)]
            p += 6 * nt
        elif faces:
            raise ValueError("stripped partitions are not supported")
        if b[p]:
            p += nv * nw
        p += 1 + 2 + 8 + 6 * nt  # bone indices flag, LOD level + global VB, vertex desc, triangles copy
    return (desc, stride, size // stride if stride else 0, vdata), tris
class ObjError(ValueError):
    pass


def _name(nif, b):
    i = struct.unpack_from('<I', b, 0)[0]
    return nif.strings[i] if i < len(nif.strings) else ''


def _after_object(b):
    """Offset just past a NiAVObject's fields (where a node's child list starts)."""
    return 72 + 4 * struct.unpack_from('<I', b, 4)[0]


def _children(nif, i):
    b = nif.block(i)[1]
    p = _after_object(b)
    n = struct.unpack_from('<I', b, p)[0]
    return list(struct.unpack_from(f'<{n}i', b, p + 4))


def _shape_head(nif, i):
    """Offsets and counts of a BSTriShape-family block."""
    start, b = nif.block(i)
    p = _after_object(b) + 16  # bounding sphere
    skin, shader, alpha = struct.unpack_from('<3i', b, p)
    desc, ntri, nvert, size = struct.unpack_from('<QHHI', b, p + 12)
    return {'block': b, 'sphere': p - 16, 'skin': skin, 'shader': shader, 'desc': desc, 'ntri': ntri, 'nvert': nvert,
            'size': size, 'data': p + 28, 'stride': (desc & 0xF) * 4, 'count_at': p + 20}


def _texture_set_of(nif, shader):
    if shader < 0 or nif.types[shader] != 'BSLightingShaderProperty':
        return -1
    b = nif.block(shader)[1]
    n = struct.unpack_from('<I', b, 8)[0]
    ref = struct.unpack_from('<i', b, 12 + 4 * n + 4 + 8 + 16)[0]
    return ref if 0 <= ref < len(nif.types) and nif.types[ref] == 'BSShaderTextureSet' else -1


def outline(nif):
    """The scene graph as [(block index, depth, type, name, detail)], parents before children.

    Shapes list their shader property and texture paths underneath; rigged shapes are flagged.
    """
    rows, seen = [], set()

    def walk(i, depth):
        if not 0 <= i < len(nif.types) or i in seen:
            return
        seen.add(i)
        t, b = nif.types[i], nif.block(i)[1]
        if t in SHAPES:
            h = _shape_head(nif, i)
            rigged = h['skin'] >= 0 or h['size'] == 0
            rows.append((i, depth, t, _name(nif, b),
                         f"{h['nvert']} verts, {h['ntri']} tris" + (", rigged" if rigged else "")))
            if h['shader'] >= 0:
                rows.append((h['shader'], depth + 1, nif.types[h['shader']], '', ''))
                ts = _texture_set_of(nif, h['shader'])
                if ts >= 0:
                    for k, path in enumerate(_read_set(nif.block(ts)[1])):
                        if path:
                            rows.append((ts, depth + 2, f'texture {k}', path, ''))
        elif t.endswith('Node'):
            kids = _children(nif, i)
            rows.append((i, depth, t, _name(nif, b), f"{len(kids)} children"))
            for c in kids:
                walk(c, depth + 1)
        else:
            rows.append((i, depth, t, _name(nif, b) if t.startswith(('Ni', 'BS')) else '', ''))
    try:
        walk(0, 0)
    except (struct.error, IndexError):
        rows = []
    if not rows:
        rows = [(i, 0, t, '', '') for i, t in enumerate(nif.types)]
    return rows


def editable_shapes(nif):
    """[(block index, name)] of plain, unrigged BSTriShape blocks, the ones OBJ export/import handles."""
    out = []
    for i, depth, t, name, detail in outline(nif):
        if t == 'BSTriShape' and 'rigged' not in detail:
            out.append((i, name or f'Shape_{i}'))
    return out


def read_shape(nif, i):
    """Raw (object space, untransformed) geometry of an unrigged BSTriShape:
    {'name', 'verts', 'uvs', 'normals', 'tris'}; uvs and normals are [] when the shape has none."""
    if nif.types[i] != 'BSTriShape':
        raise NifError(f"block {i} is a {nif.types[i]}, only BSTriShape can be exported")
    h = _shape_head(nif, i)
    if h['skin'] >= 0 or h['size'] == 0:
        raise NifError("rigged shapes are not supported")
    b, desc, stride = h['block'], h['desc'], h['stride']
    flags = desc >> 44
    if not stride or not flags & 1:
        raise NifError("shape has no vertex data")
    base = h['data']
    verts, uvs, normals = [], [], []
    for v in range(h['nvert']):
        o = base + v * stride
        verts.append(struct.unpack_from('<3f', b, o + ((desc >> 4) & 0xF) * 4))
        if flags & 2:
            uvs.append(_half(b, o + ((desc >> 8) & 0xF) * 4))
        if flags & 8:
            nx, ny, nz = b[o + ((desc >> 16) & 0xF) * 4:][:3]
            normals.append((nx / 127.5 - 1, ny / 127.5 - 1, nz / 127.5 - 1))
    p = base + h['nvert'] * stride
    tris = [struct.unpack_from('<3H', b, p + t * 6) for t in range(h['ntri'])]
    return {'name': _name(nif, b), 'verts': verts, 'uvs': uvs, 'normals': normals, 'tris': tris}


def export_obj(nif, indices=None):
    """Wavefront OBJ text for the given shape blocks (default: every editable shape), one object per shape.

    Coordinates are the shape's own, untransformed vertex positions, and V is flipped to the OBJ convention, so
    import_obj can put the result back without any conversion.
    """
    wanted = dict(editable_shapes(nif))
    indices = list(wanted) if indices is None else list(indices)
    lines = ["# Skyrim mesh exported by skyrim-mcp-tools"]
    base = [1, 1, 1]
    for i in indices:
        s = read_shape(nif, i)
        lines.append(f"o {wanted.get(i, s['name'] or f'Shape_{i}')}")
        lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in s['verts']]
        lines += [f"vt {u:.6f} {1 - v:.6f}" for u, v in s['uvs']]
        lines += [f"vn {x:.5f} {y:.5f} {z:.5f}" for x, y, z in s['normals']]
        for a, b_, c in s['tris']:
            lines.append("f " + " ".join(_corner(k, base, bool(s['uvs']), bool(s['normals'])) for k in (a, b_, c)))
        base[0] += len(s['verts'])
        base[1] += len(s['uvs'])
        base[2] += len(s['normals'])
    return "\n".join(lines) + "\n"


def _corner(k, base, uv, nrm):
    v = base[0] + k
    if uv and nrm:
        return f"{v}/{base[1] + k}/{base[2] + k}"
    if uv:
        return f"{v}/{base[1] + k}"
    return f"{v}//{base[2] + k}" if nrm else str(v)


def parse_obj(text):
    """{object name: {'verts', 'uvs', 'normals', 'tris'}} from OBJ text; polygons are fanned into triangles and
    vertices are split wherever position, UV or normal differ. V is flipped back to the NIF convention."""
    pos, tex, nor = [], [], []
    objects, cur = {}, None
    for raw in text.splitlines():
        parts = raw.split('#')[0].split()
        if not parts:
            continue
        key = parts[0]
        try:
            if key == 'v':
                pos.append(tuple(float(x) for x in parts[1:4]))
            elif key == 'vt':
                tex.append((float(parts[1]), 1 - float(parts[2]) if len(parts) > 2 else 0.0))
            elif key == 'vn':
                nor.append(tuple(float(x) for x in parts[1:4]))
            elif key in ('o', 'g'):
                name = raw.split(None, 1)[1].strip() if len(parts) > 1 else f'object{len(objects)}'
                cur = objects.setdefault(name, {'map': {}, 'verts': [], 'uvs': [], 'normals': [], 'tris': [], 'hasuv': False,
                                                'hasn': False})
            elif key == 'f':
                if cur is None:
                    cur = objects.setdefault('object', {'map': {}, 'verts': [], 'uvs': [], 'normals': [], 'tris': [],
                                                        'hasuv': False, 'hasn': False})
                ids = [_obj_corner(c, len(pos), len(tex), len(nor), cur, pos, tex, nor) for c in parts[1:]]
                for k in range(1, len(ids) - 1):
                    if len({ids[0], ids[k], ids[k + 1]}) == 3:
                        cur['tris'].append((ids[0], ids[k], ids[k + 1]))
        except (ValueError, IndexError) as e:
            raise ObjError(f"bad OBJ line {raw.strip()!r}: {e}")
    out = {}
    for name, o in objects.items():
        if o['tris']:
            out[name] = {'verts': o['verts'], 'uvs': o['uvs'] if o['hasuv'] else [],
                         'normals': o['normals'] if o['hasn'] else [], 'tris': o['tris']}
    return out


def _obj_corner(token, npos, ntex, nnor, obj, pos, tex, nor):
    f = (token.split('/') + ['', ''])[:3]

    def idx(s, n):
        if not s:
            return None
        k = int(s)
        k = k - 1 if k > 0 else n + k
        if not 0 <= k < n:
            raise IndexError(f"index {s} out of range")
        return k
    key = (idx(f[0], npos), idx(f[1], ntex), idx(f[2], nnor))
    if key[0] is None:
        raise ValueError("face without a position")
    if key not in obj['map']:
        obj['map'][key] = len(obj['verts'])
        obj['verts'].append(pos[key[0]])
        obj['uvs'].append(tex[key[1]] if key[1] is not None else (0.0, 0.0))
        obj['normals'].append(nor[key[2]] if key[2] is not None else (0.0, 0.0, 1.0))
        obj['hasuv'] |= key[1] is not None
        obj['hasn'] |= key[2] is not None
    return obj['map'][key]


def _unit(a):
    import numpy as np
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return a / np.where(n < 1e-12, 1, n)


def smooth_normals(verts, tris):
    import numpy as np
    v = np.array(verts, dtype=np.float64)
    t = np.array(tris, dtype=np.int64)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    out = np.zeros_like(v)
    for k in range(3):
        np.add.at(out, t[:, k], fn)
    return _unit(out)


def tangent_frame(verts, uvs, normals, tris):
    """(tangent, bitangent) per vertex from the UV layout, orthogonal to the normals. UVs are as stored in the NIF."""
    import numpy as np
    p = np.array(verts, dtype=np.float64)
    uv = np.array(uvs, dtype=np.float64)
    n = np.array(normals, dtype=np.float64)
    t = np.array(tris, dtype=np.int64)
    e1, e2 = p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]
    d1, d2 = uv[t[:, 1]] - uv[t[:, 0]], uv[t[:, 2]] - uv[t[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) < 1e-12, 0.0, 1.0 / np.where(det == 0, 1, det))[:, None]
    tu = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    tv = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    tan, bit = np.zeros_like(p), np.zeros_like(p)
    for k in range(3):
        np.add.at(tan, t[:, k], tu)
        np.add.at(bit, t[:, k], tv)
    tan = _unit(tan - n * (tan * n).sum(1, keepdims=True))
    bit = _unit(bit - n * (bit * n).sum(1, keepdims=True))
    return tan, bit


def _byte(x):
    return max(0, min(255, int(round((x + 1) * 127.5))))


def import_obj(nif, index, text, object_name=None):
    """New NIF bytes with the BSTriShape at block `index` replaced by an object from OBJ text.

    The shape keeps its vertex format, shader, textures and transform; positions, UVs, normals and tangents are
    rebuilt (vertex colors are kept only when the vertex count is unchanged, otherwise they become white).
    """
    if nif.types[index] != 'BSTriShape':
        raise NifError(f"block {index} is a {nif.types[index]}, only BSTriShape can be replaced")
    h = _shape_head(nif, index)
    if h['skin'] >= 0 or h['size'] == 0:
        raise NifError("rigged shapes are not supported")
    objs = parse_obj(text)
    if not objs:
        raise ObjError("the OBJ file has no faces")
    shape_name = _name(nif, h['block'])
    if object_name is not None:
        if object_name not in objs:
            raise ObjError(f"no object {object_name!r} in the OBJ (found: {', '.join(objs)})")
        obj = objs[object_name]
    elif len(objs) == 1:
        obj = next(iter(objs.values()))
    elif shape_name in objs:
        obj = objs[shape_name]
    else:
        raise ObjError(f"the OBJ has several objects ({', '.join(objs)}); none is named {shape_name!r}")
    verts, tris = obj['verts'], obj['tris']
    if len(verts) > 65535 or len(tris) > 65535:
        raise ObjError("too many vertices or triangles for one shape (limit 65535)")
    desc, stride, flags = h['desc'], h['stride'], h['desc'] >> 44
    old, b = h['block'], h['block']
    if flags & 2 and not obj['uvs']:
        obj = dict(obj, uvs=[(0.0, 0.0)] * len(verts))
    normals = obj['normals'] or smooth_normals(verts, tris).tolist()
    tan = bit = None
    if flags & 16:  # Skyrim's "tangent" is dP/dV and its "bitangent" dP/dU (checked against vanilla meshes)
        bit, tan = tangent_frame(verts, obj['uvs'] or [(0.0, 0.0)] * len(verts), normals, tris)
    nv = len(verts)
    buf = bytearray(nv * stride)
    offs = {k: ((desc >> s) & 0xF) * 4 for k, s in (('pos', 4), ('uv', 8), ('nrm', 16), ('tan', 20), ('col', 24))}
    old_stride_data = h['data']
    for v in range(nv):
        o = v * stride
        struct.pack_into('<3f', buf, o + offs['pos'], *verts[v])
        if flags & 16:
            struct.pack_into('<f', buf, o + offs['pos'] + 12, float(bit[v][0]))
        if flags & 2:
            struct.pack_into('<2e', buf, o + offs['uv'], *obj['uvs'][v])
        if flags & 8:
            nrm = normals[v]
            buf[o + offs['nrm']:o + offs['nrm'] + 3] = bytes(_byte(c) for c in nrm)
            buf[o + offs['nrm'] + 3] = _byte(bit[v][1]) if flags & 16 else 127
        if flags & 16:
            buf[o + offs['tan']:o + offs['tan'] + 3] = bytes(_byte(c) for c in tan[v])
            buf[o + offs['tan'] + 3] = _byte(bit[v][2])
        if flags & 32:
            if nv == h['nvert']:
                src = old_stride_data + v * stride + offs['col']
                buf[o + offs['col']:o + offs['col'] + 4] = old[src:src + 4]
            else:
                buf[o + offs['col']:o + offs['col'] + 4] = b'\xff\xff\xff\xff'
    faces = b''.join(struct.pack('<3H', *t) for t in tris)
    head = bytearray(old[:h['count_at']])
    cx = [sum(c) / nv for c in zip(*verts)]
    rad = max(sum((a - c) ** 2 for a, c in zip(v, cx)) ** 0.5 for v in verts)
    struct.pack_into('<4f', head, h['sphere'], *cx, rad)
    new = bytes(head) + struct.pack('<HHI', len(tris), nv, len(buf) + len(faces)) + bytes(buf) + faces \
        + old[h['data'] + h['nvert'] * stride + h['ntri'] * 6:]
    return _replace_block(nif, index, new)


def _replace_block(nif, i, new):
    start, block = nif.block(i)
    data, sizes = bytearray(nif.data), list(nif.sizes)
    data[start:start + len(block)] = new
    sizes[i] = len(new)
    struct.pack_into(f'<{len(sizes)}I', data, nif.sizes_at, *sizes)
    return bytes(data)

