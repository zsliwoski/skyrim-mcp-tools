"""Pure-function tests for tools.npcs.facegen using synthetic TRI and NIF data."""
import io
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from tools.npcs import facegen as fg


class Sub:
    def __init__(self, signature, data):
        self.signature, self.data = signature, data


class Rec:
    def __init__(self, **subs):
        self.subrecords = [Sub(k, v) for k, v in subs.items()]

    def get_subrecord(self, sig):
        return next((s for s in self.subrecords if s.signature == sig), None)

    def add_subrecord(self, sig, data):
        self.subrecords.append(Sub(sig, data))


def make_tri(base, morphs):
    nv = len(base)
    head = b'FRTRI003' + struct.pack('<8I', nv, 0, 0, 0, 0, 0, 0, len(morphs))
    body = head.ljust(64, b'\0') + np.asarray(base, '<f4').tobytes()
    for name, (scale, deltas) in morphs.items():
        raw = name.encode() + b'\0'
        body += struct.pack('<I', len(raw)) + raw + struct.pack('<f', scale)
        body += np.asarray(deltas, '<i2').tobytes()
    return body


def make_nif(shapes):
    """Minimal NIF holding BSDynamicTriShape blocks: name index, filler, size field, 16-byte vertices."""
    blocks = []
    for k, xyz in enumerate(shapes.values()):
        verts = np.zeros((len(xyz), 4), '<f4')
        verts[:, :3] = xyz
        verts[:, 3] = 7.5
        blocks.append(struct.pack('<I', k) + b'\0' * 8 + struct.pack('<I', len(xyz) * 16) + verts.tobytes())
    nb = len(blocks)
    d = b'Gamebryo File Format\n' + struct.pack('<IBIII', 0x14020007, 1, 12, nb, 100)
    d += b'\x01a' * 3
    d += struct.pack('<H', 1) + struct.pack('<I', 17) + b'BSDynamicTriShape'
    d += struct.pack('<%dH' % nb, *[0] * nb) + struct.pack('<%dI' % nb, *[len(b) for b in blocks])
    names = list(shapes)
    d += struct.pack('<II', len(names), max(len(n) for n in names))
    for n in names:
        d += struct.pack('<I', len(n)) + n.encode()
    return d + struct.pack('<I', 0) + b''.join(blocks)


BASE = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
SKINNY = np.array([[10, 0, 0]] * 3)
RACE = np.array([[0, 20, 0]] * 3)
NOSE = np.array([[0, 0, 30]] * 3)
PRESET = np.array([[5, 5, 5]] * 3)


def tris():
    base = fg.Tri(make_tri(BASE, {'SkinnyMorph': (0.1, SKINNY)}))
    race = fg.Tri(make_tri(BASE, {'NordRace': (0.1, RACE)}))
    chargen = fg.Tri(make_tri(BASE, {'NoseLong': (0.1, NOSE), 'NoseShort': (0.1, -NOSE),
                                     'NoseType2': (0.1, PRESET)}))
    return base, race, chargen


def face(**kw):
    f = {k: 0.0 for k in fg.SLIDER_KEYS}
    f.update(nose_type=None, eye_type=None, lip_type=None, weight=50.0, height=1.0)
    f.update(kw)
    return f


def hdpt(paths):
    subs = []
    for kind, p in paths.items():
        subs += [Sub('NAM0', struct.pack('<I', kind)), Sub('NAM1', p.encode() + b'\0')]
    return SimpleNamespace(subrecords=subs)


class TestFaceRecord:
    def test_decode_defaults(self):
        f = fg.decode_face(Rec())
        assert f['weight'] == 50.0 and f['height'] == 1.0 and f['nose_type'] is None
        assert all(f[k] == 0.0 for k in fg.SLIDER_KEYS)

    def test_encode_roundtrip_creates_subrecords(self):
        npc = Rec()
        original = fg.decode_face(npc)
        new = dict(original, nose_length=0.5, eye_type=4, weight=80.0)
        fg.encode_face(npc, new, original)
        back = fg.decode_face(npc)
        assert back['nose_length'] == 0.5 and back['eye_type'] == 4 and back['weight'] == 80.0
        assert back['nose_type'] is None and back['unknown_slider'] == 0.0

    def test_encode_updates_existing_and_noop(self):
        npc = Rec(NAM9=struct.pack('<19f', *[0.1] * 19), NAMA=struct.pack('<4I', 1, 2, 3, 4),
                  NAM7=struct.pack('<f', 10))
        original = fg.decode_face(npc)
        fg.encode_face(npc, dict(original), original)
        assert fg.decode_face(npc) == original
        new = dict(original, jaw_width=-1.0, lip_type=None, weight=20.0)
        fg.encode_face(npc, new, original)
        back = fg.decode_face(npc)
        assert back['jaw_width'] == -1.0 and back['lip_type'] is None and back['nose_type'] == 1
        assert back['weight'] == 20.0

    def test_merge_face_validation(self):
        merged, errors = fg.merge_face(face(), {'nose_length': 0.5, 'nose_type': 3, 'weight': 70,
                                                'lip_type': None})
        assert not errors and merged['nose_length'] == 0.5 and merged['nose_type'] == 3
        _, errors = fg.merge_face(face(), {'nose_length': 2, 'jaw_width': True, 'nose_type': -1,
                                           'eye_type': 'x', 'weight': 101, 'bogus': 1})
        assert len(errors) == 6 and 'unknown face field' in errors[-1]
        assert fg.merge_face(face(), None)[1] == []

    def test_describe_sliders(self):
        assert set(fg.describe_sliders()) == set(fg.SLIDER_KEYS)


class TestTriAndPredict:
    def test_tri_reads_base_and_morphs(self):
        base, _, chargen = tris()
        assert base.nv == 3 and base.has('SkinnyMorph') and not base.has('x')
        assert chargen.names() == ['NoseLong', 'NoseShort', 'NoseType2']
        assert np.allclose(chargen.morph('NoseLong'), NOSE * 0.1)

    def test_predict_combines_morphs(self):
        base, race, chargen = tris()
        f = face(weight=0.0, nose_length=0.5, nose_type=2)
        v = fg.predict(base, race, chargen, 'NordRace', f)
        expect = np.array(BASE, float) + SKINNY * 0.1 + RACE * 0.1 + 0.5 * NOSE * 0.1 + PRESET * 0.1
        assert np.allclose(v, expect)
        neg = fg.predict(base, None, chargen, 'NordRace', face(nose_length=-1.0))
        assert np.allclose(neg[:, 2], -3.0 + 0) and np.allclose(neg[:, 0] - np.array(BASE)[:, 0], 0.5)

    def test_part_tris_loads_by_kind(self):
        files = {'Meshes\\a\\base.tri': make_tri(BASE, {}), 'Meshes\\a\\cg.tri': make_tri(BASE, {})}
        part = hdpt({fg.TRI_BASE: 'a\\base.tri', fg.TRI_RACE: 'a\\race.tri', fg.TRI_CHARGEN: 'a\\cg.tri'})
        base, race, chargen = fg.part_tris(part, files.get)
        assert base.nv == 3 and race is None and chargen.nv == 3


class TestNif:
    def test_positions_roundtrip(self):
        xyz = np.array(BASE, float) + 2
        nif = fg.Nif(make_nif({'Head': xyz}))
        assert nif.shapes['Head'][1] == 3 and np.allclose(nif.positions('Head'), xyz)
        nif.set_positions('Head', xyz + 1)
        again = fg.Nif(nif.bytes())
        assert np.allclose(again.positions('Head'), xyz + 1)

    def test_vertex_count_failure(self):
        data = bytearray(make_nif({'Head': np.zeros((3, 3))}))
        data[-52:-48] = struct.pack('<I', 5)
        with pytest.raises(fg.FaceGenError):
            fg.Nif(bytes(data))

    def setup_method(self):
        base, race, chargen = tris()
        self.files = {'Meshes\\b.tri': make_tri(BASE, {'SkinnyMorph': (0.1, SKINNY)}),
                      'Meshes\\r.tri': make_tri(BASE, {'NordRace': (0.1, RACE)}),
                      'Meshes\\c.tri': make_tri(BASE, {'NoseLong': (0.1, NOSE)})}
        full = hdpt({fg.TRI_BASE: 'b.tri', fg.TRI_RACE: 'r.tri', fg.TRI_CHARGEN: 'c.tri'})
        self.parts = {'head': full, 'nomorph': hdpt({fg.TRI_BASE: 'b.tri'}),
                      'wrongcount': full}

    def test_regenerate_and_check(self):
        f = face(nose_length=1.0)
        start = np.array(BASE, float)
        nif = make_nif({'Head': start, 'NoMorph': start, 'Ghost': start})
        out, report = fg.regenerate(nif, f, 'NordRace', {'head': self.parts['head'],
                                                         'nomorph': self.parts['nomorph']}, self.files.get)
        assert report == {'Head': 'regenerated', 'NoMorph': 'unchanged (part has no morph targets)',
                          'Ghost': 'skipped (no matching head part)'}
        shifted = fg.Nif(out).positions('Head')
        assert np.allclose(shifted[:, 2], 3.0)
        errs = fg.check(out, f, 'NordRace', {'head': self.parts['head']}, self.files.get)
        assert errs['Head'][0] < 1e-3 and errs['Head'][1] < 1e-3

    def test_regenerate_relative_and_count_mismatch(self):
        f, old = face(nose_length=1.0), face()
        nif = make_nif({'Head': np.array(BASE, float) + 100})
        out, _ = fg.regenerate(nif, f, 'NordRace', {'head': self.parts['head']}, self.files.get, old_face=old)
        assert np.allclose(fg.Nif(out).        positions('Head')[:, 2], 103.0)
        big = make_nif({'Head': np.zeros((4, 3))})
        _, report = fg.regenerate(big, f, 'NordRace', {'head': self.parts['head']}, self.files.get)
        assert report['Head'].startswith('skipped (vertex count 4 != 3')
        assert fg.check(big, f, 'NordRace', {'head': self.parts['head']}, self.files.get) == {}


class TestLayout:
    def test_parse_dump_roundtrip_and_lookup(self):
        data = make_nif({'Head': np.zeros((3, 3)), 'Eyes': np.ones((3, 3))})
        lay = fg._Layout(data)
        assert lay.nb == 2 and lay.type_of(0) == 'BSDynamicTriShape'
        assert lay.name_of(1) == 'Eyes' and lay.find('Eyes', ('BSDynamicTriShape',)) == 1
        assert lay.find('Eyes', ('NiNode',)) is None and lay.find('Nope', ('BSDynamicTriShape',)) is None
        assert lay.dump() == data

    def test_interning(self):
        lay = fg._Layout(make_nif({'Head': np.zeros((3, 3))}))
        assert fg._intern(lay, 'Head') == 0 and fg._intern(lay, 'New') == 1
        assert fg._add_type(lay, 'BSDynamicTriShape') == 0 and fg._add_type(lay, 'NiNode') == 1
        assert fg._Layout(lay.dump()).strings == ['Head', 'New']

    def test_rejects_other_games(self):
        data = bytearray(make_nif({'Head': np.zeros((3, 3))}))
        nl = data.index(b'\n')
        struct.pack_into('<I', data, nl + 6, 11)
        with pytest.raises(fg.FaceGenError):
            fg._Layout(bytes(data))

    def test_add_shape_errors(self):
        host = make_nif({'Head': np.zeros((3, 3))})
        donor = make_nif({'Head': np.zeros((3, 3)), 'Beard': np.zeros((3, 3))})
        with pytest.raises(fg.FaceGenError, match='already'):
            fg.add_shape(host, donor, 'Head')
        with pytest.raises(fg.FaceGenError, match='no shape'):
            fg.add_shape(host, donor, 'Mustache')


class TestPaint:
    def test_parse_color(self):
        assert fg.parse_color('Red') == fg.COLORS['red']
        assert fg.parse_color('#0a0b0C') == (10, 11, 12)
        with pytest.raises(fg.FaceGenError):
            fg.parse_color('chartreuse-ish')

    def test_apply_paint_blends_through_mask(self):
        pil = pytest.importorskip('PIL.Image')

        def png(color, mode):
            buf = io.BytesIO()
            pil.new(mode, (4, 4), color).save(buf, 'PNG')
            return buf.getvalue()

        out = fg.apply_paint(png((0, 0, 0, 255), 'RGBA'), png(255, 'L'), (100, 200, 50), 0.5)
        assert out[:4] == b'DDS ' and struct.unpack_from('<I', out, 12)[0] == 4
        first = out[128:132]
        assert first == bytes([25, 100, 50, 255])
