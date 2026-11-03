"""Tests for BA2 archive reading and FO4 localized-string resolution.

The real-file tests are marked ``gamefiles`` and skip when Fallout 4 isn't
installed. They validate that:
  - GNRL and DX10 BA2 archives parse and index their files,
  - string tables extract from a GNRL archive,
  - FO4 localized FULL names resolve (via the `_en` language alias) when a
    plugin is loaded from a Data dir whose BA2s hold the strings.
"""

import struct
import pytest
from pathlib import Path

from esplib import Plugin, Ba2Reader, Ba2Writer
from esplib.ba2 import ba2_hash, _hash_path

# Small known-good archives built with Bethesda's Archive2.exe (genuine engine
# format), committed so the writer can be checked against real ground truth
# without a game install. fixture_gnrl holds one 64-byte stub nif; fixture_dx10
# holds one 4x4 BC7 single-mip dds (16-byte body, bytes(range(16))).
FIXTURES = Path(__file__).parent / "fixtures"
GNRL_FIXTURE = FIXTURES / "fixture_gnrl.ba2"
DX10_FIXTURE = FIXTURES / "fixture_dx10.ba2"
from esplib.strings import _language_aliases
from esplib.game_discovery import find_game_data, find_game_file


def _fo4_data():
    return find_game_data('fo4')


# ---------------------------------------------------------------------------
# Unit: BA2 file/dir hash (no game files) — pinned to verified vanilla values
# ---------------------------------------------------------------------------


class TestBa2Hash:
    # Verified 60/60 against Fallout4 - Interface.ba2 (the hash is a RAW table
    # CRC32: poly 0xEDB88320, init 0, no final XOR — NOT zlib's standard CRC32).

    def test_name_hash_pin(self):
        assert ba2_hash('bartermenu') == 0xEF32EBB6

    def test_dir_hash_pin(self):
        assert ba2_hash('interface') == 0xD2FDF873

    def test_case_insensitive(self):
        assert ba2_hash('BarterMenu') == ba2_hash('bartermenu')

    def test_hash_path_splits_dir_stem_ext(self):
        dir_hash, name_hash, ext = _hash_path(r'Interface\BarterMenu.swf')
        assert name_hash == 0xEF32EBB6
        assert dir_hash == 0xD2FDF873
        assert ext == b'swf\x00'

    def test_hash_path_forward_slashes_and_nif(self):
        dir_hash, name_hash, ext = _hash_path(
            'Meshes/Actors/Character/FaceGenData/FaceGeom/Fallout4.esm/001234.nif')
        assert ext == b'nif\x00'
        assert dir_hash == ba2_hash(
            r'Meshes\Actors\Character\FaceGenData\FaceGeom\Fallout4.esm')
        assert name_hash == ba2_hash('001234')


# ---------------------------------------------------------------------------
# Unit: language aliasing (no game files)
# ---------------------------------------------------------------------------


class TestLanguageAliases:


    def test_english_expands_to_en(self):
        # FO4 names English strings <plugin>_en.STRINGS; Skyrim uses _english.
        assert _language_aliases('english') == ['english', 'en']


    def test_en_expands_to_english(self):
        assert _language_aliases('en') == ['en', 'english']


    def test_unknown_language_passes_through(self):
        assert _language_aliases('klingon') == ['klingon']


# ---------------------------------------------------------------------------
# Unit: GNRL Ba2Writer round-trips through Ba2Reader (no game files)
# ---------------------------------------------------------------------------


class TestBa2WriterGnrl:

    def test_roundtrip_stored_and_compressed(self, tmp_path):
        big = b'ABCD' * 2000          # compresses well -> stored compressed
        small = bytes(range(7))       # tiny -> stored uncompressed
        nif_path = (r'Meshes\Actors\Character\FaceGenData\FaceGeom'
                    r'\Fallout4.esm\00012345.nif')
        bin_path = r'Data\foo.bin'

        w = Ba2Writer('GNRL')
        w.add_file(nif_path, big)
        w.add_file(bin_path, small)
        out = tmp_path / 'Test - Main.ba2'
        w.write(out)

        with Ba2Reader(out) as r:
            assert r.archive_type == 'GNRL'
            assert len(r.list_files()) == 2
            # case- and slash-insensitive lookup, like the reader's own API
            assert r.has_file(nif_path.replace('\\', '/').lower())
            assert r.read_file(nif_path) == big
            assert r.read_file(bin_path) == small

    def test_no_compress_stores_verbatim(self, tmp_path):
        data = b'ZZZZ' * 2000
        w = Ba2Writer('GNRL', compress=False)
        w.add_file(r'a\b.bin', data)
        out = tmp_path / 'NoComp - Main.ba2'
        w.write(out)
        with Ba2Reader(out) as r:
            assert r.read_file(r'a\b.bin') == data

    def test_reject_dds_in_gnrl(self, tmp_path):
        w = Ba2Writer('GNRL')
        with pytest.raises(ValueError):
            w.add_dds(r'x\y.dds', b'DDS ')

    def test_record_matches_archive2_field_values(self, tmp_path):
        # The engine's index parser requires the GNRL `flags` dword and the
        # 0xBAADF00D tail sentinel; writing 0 crashed FO4 at archive
        # registration (BSResource::Archive2::Index::AddDataFromReader). Derive
        # the expected values from the Archive2-built fixture, don't hardcode.
        vraw = GNRL_FIXTURE.read_bytes()
        _, _, _, v_flags, _, _, _, v_align = \
            struct.unpack('<I 4s I I Q I I I', vraw[24:24 + 36])

        w = Ba2Writer('GNRL')
        w.add_file(r'Meshes\a\00.nif', b'data' * 100)
        out = tmp_path / 'V - Main.ba2'
        w.write(out)
        _, _, _, flags, _, _, _, align = \
            struct.unpack('<I 4s I I Q I I I', out.read_bytes()[24:24 + 36])
        assert flags == v_flags
        assert align == v_align

    def test_reader_reads_archive2_gnrl(self):
        # Cross-tool: our reader extracts a genuine Archive2 GNRL archive,
        # decompressing correctly back to the original stub bytes.
        with Ba2Reader(GNRL_FIXTURE) as r:
            assert r.archive_type == 'GNRL'
            (name,) = r.list_files()
            assert r.read_file(name) == b'STUBNIF\x00' * 8


# ---------------------------------------------------------------------------
# Unit: DX10 Ba2Writer round-trips through Ba2Reader (no game files)
# ---------------------------------------------------------------------------


def _make_dx10_dds(width, height, num_mips, fmt, body):
    """A minimal DX10-headed .dds: magic + 124-byte DDS_HEADER + 20-byte
    DXT10 header + raw mip body. Built independently of Ba2Reader so the
    round-trip actually exercises the writer's own DDS parser."""
    pixelformat = struct.pack('<I I 4s I I I I I',
                              32, 0x4, b'DX10', 0, 0, 0, 0, 0)  # FOURCC=DX10
    header = struct.pack('<7I 44s 32s 5I',
                         124, 0x1007, height, width, len(body), 0, num_mips,
                         b'\x00' * 44, pixelformat, 0x1000, 0, 0, 0, 0)
    dxt10 = struct.pack('<5I', fmt, 3, 0, 1, 0)  # dxgiFormat, dim=TEXTURE2D,...
    return b'DDS ' + header + dxt10 + body


class TestBa2WriterDx10:

    def test_roundtrip_stored_and_compressed(self, tmp_path):
        from esplib.ba2 import _parse_dds
        # BC7 (fmt 98): 16 bytes/block. 8x8 = 2x2 blocks = 64 bytes (patterned,
        # stored); 256x256 = 64x64 blocks = 65536 bytes (zeros, compressed).
        small_body = bytes((i * 7) % 256 for i in range(64))
        big_body = b'\x00' * (64 * 64 * 16)
        dds_a = _make_dx10_dds(8, 8, 1, 98, small_body)
        dds_b = _make_dx10_dds(256, 256, 1, 98, big_body)
        path_a = (r'Textures\Actors\Character\FaceCustomization'
                  r'\Fallout4.esm\00ab_d.dds')
        path_b = r'Textures\test\big_d.dds'

        w = Ba2Writer('DX10')
        w.add_dds(path_a, dds_a)
        w.add_dds(path_b, dds_b)
        out = tmp_path / 'Test - Textures.ba2'
        w.write(out)

        with Ba2Reader(out) as r:
            assert r.archive_type == 'DX10'
            assert len(r.list_files()) == 2
            recon_a = r.read_file(path_a)
            recon_b = r.read_file(path_b)

        wa, ha, ma, fa, ba = _parse_dds(recon_a)
        assert (wa, ha, ma, fa) == (8, 8, 1, 98)
        assert ba == small_body
        wb, hb, mb, fb, bb = _parse_dds(recon_b)
        assert (wb, hb, mb, fb) == (256, 256, 1, 98)
        assert bb == big_body

    def test_reject_file_in_dx10(self, tmp_path):
        w = Ba2Writer('DX10')
        with pytest.raises(ValueError):
            w.add_file(r'x\y.nif', b'data')

    def test_multimip_splits_into_streaming_chunks(self, tmp_path):
        # A single all-mips chunk loads every texture at full res and tanks
        # framerate; the engine streams mips per chunk. Match Archive2: mip0,
        # mip1, then the tail — 3 chunks for a texture with >=3 mips.
        def bc7_size(w, h, m):
            t = 0
            for i in range(m):
                mw, mh = max(1, w >> i), max(1, h >> i)
                t += max(1, (mw + 3) // 4) * max(1, (mh + 3) // 4) * 16
            return t

        mips = 7                              # 64x64 -> 7 mips
        body = bytes((i * 13 + 1) % 256 for i in range(bc7_size(64, 64, mips)))
        dds = _make_dx10_dds(64, 64, mips, 98, body)
        path = r'Textures\a\m_d.dds'
        w = Ba2Writer('DX10')
        w.add_dds(path, dds)
        out = tmp_path / 'M - Textures.ba2'
        w.write(out)

        raw = out.read_bytes()
        (_, _, _, _, nchunks, *_rest) = \
            struct.unpack('<I 4s I B B H H H B B B B', raw[24:24 + 24])
        assert nchunks == 3
        # chunk start/end mips: (0,0), (1,1), (2,6)
        spans = []
        base = 24 + 24
        for c in range(3):
            _o, _p, _u, smip, emip, _al = \
                struct.unpack('<Q I I H H I', raw[base + c*24: base + c*24 + 24])
            spans.append((smip, emip))
        assert spans == [(0, 0), (1, 1), (2, 6)]

        # And it still round-trips: reader concatenates chunks back to the body.
        with Ba2Reader(out) as r:
            from esplib.ba2 import _parse_dds
            wd, ht, m, fmt, rbody = _parse_dds(r.read_file(path))
        assert (wd, ht, m, fmt) == (64, 64, mips, 98)
        assert rbody == body

    def test_dx10_record_matches_archive2_field_values(self, tmp_path):
        # tileMode, chunk-header size and the 0xBAADF00D chunk tail must match
        # the engine's expectation, or it rejects Textures.ba2. Expected values
        # come from the Archive2-built DX10 fixture.
        vraw = DX10_FIXTURE.read_bytes()
        (_, _, _, _, _, v_chunk_hdr, _, _, _, _, _, v_tile) = \
            struct.unpack('<I 4s I B B H H H B B B B', vraw[24:24 + 24])
        _, _, _, _, _, v_calign = struct.unpack('<Q I I H H I', vraw[48:48 + 24])

        body = bytes((i * 7) % 256 for i in range(64))
        w = Ba2Writer('DX10')
        w.add_dds(r'Textures\a\00_d.dds', _make_dx10_dds(8, 8, 1, 98, body))
        out = tmp_path / 'V - Textures.ba2'
        w.write(out)
        raw = out.read_bytes()
        (_, _, _, _, _, chunk_hdr, _, _, _, _, _, tile) = \
            struct.unpack('<I 4s I B B H H H B B B B', raw[24:24 + 24])
        _, _, _, _, _, calign = struct.unpack('<Q I I H H I', raw[48:48 + 24])
        assert tile == v_tile
        assert chunk_hdr == v_chunk_hdr
        assert calign == v_calign

    def test_reader_reads_archive2_dx10(self):
        # Cross-tool: our reader rebuilds a DDS from a genuine Archive2 DX10
        # archive; metadata + mip body match what went in.
        with Ba2Reader(DX10_FIXTURE) as r:
            assert r.archive_type == 'DX10'
            (name,) = r.list_files()
            dds = r.read_file(name)
        from esplib.ba2 import _parse_dds
        w, h, mips, fmt, body = _parse_dds(dds)
        assert (w, h, mips, fmt) == (4, 4, 1, 98)
        assert body == bytes(range(16))


# ---------------------------------------------------------------------------
# Real-file: BA2 reading (requires Fallout 4)
# ---------------------------------------------------------------------------


@pytest.mark.gamefiles
class TestBa2Reader:


    def test_gnrl_archive_parses(self):
        data = _fo4_data()
        if not data:
            pytest.skip("Fallout 4 not installed")
        path = data / 'Fallout4 - Interface.ba2'
        if not path.exists():
            pytest.skip("Interface BA2 not found")
        with Ba2Reader(path) as ba2:
            assert ba2.archive_type == 'GNRL'
            files = ba2.list_files()
            assert len(files) > 0
            # The English STRINGS table lives here.
            assert ba2.has_file(r'strings\fallout4_en.strings')


    def test_extract_strings_table(self):
        data = _fo4_data()
        if not data:
            pytest.skip("Fallout 4 not installed")
        path = data / 'Fallout4 - Interface.ba2'
        if not path.exists():
            pytest.skip("Interface BA2 not found")
        with Ba2Reader(path) as ba2:
            raw = ba2.read_file(r'strings/fallout4_en.strings')
            assert raw is not None and len(raw) > 8
            count, data_size = struct.unpack('<II', raw[:8])
            assert count > 0
            assert data_size > 0


    def test_dx10_archive_parses(self):
        data = _fo4_data()
        if not data:
            pytest.skip("Fallout 4 not installed")
        path = data / 'Fallout4 - Textures1.ba2'
        if not path.exists():
            pytest.skip("Textures BA2 not found")
        with Ba2Reader(path) as ba2:
            assert ba2.archive_type == 'DX10'
            assert len(ba2.list_files()) > 0


    def test_dx10_extract_reconstructs_dds(self):
        # A DX10 texture must come back as a complete DX10-headered DDS:
        # 'DDS ' magic + 124-byte header + 20-byte DXT10 header, with the
        # dxgiFormat / dimensions matching the indexed metadata and the body
        # length equal to the concatenated mip chunks.
        data = _fo4_data()
        if not data:
            pytest.skip("Fallout 4 not installed")
        path = data / 'Fallout4 - Textures1.ba2'
        if not path.exists():
            pytest.skip("Textures BA2 not found")
        target = r'textures\actors\character\basehumanmale\basemalehead_d.dds'
        with Ba2Reader(path) as ba2:
            assert ba2.has_file(target)
            dds = ba2.read_file(target)
            assert dds[:4] == b'DDS '
            (hdr_size,) = struct.unpack('<I', dds[4:8])
            assert hdr_size == 124
            assert dds[84:88] == b'DX10'  # ddspf.dwFourCC
            height, width = struct.unpack('<II', dds[12:20])
            dxgi_format = struct.unpack('<I', dds[128:132])[0]
            entry = ba2._by_name[target.replace('/', '\\')]
            assert (width, height) == (entry.dx10_width, entry.dx10_height)
            assert dxgi_format == entry.dx10_format
            body = sum(unpacked for _, _, unpacked in entry.chunks)
            assert len(dds) == 148 + body


# ---------------------------------------------------------------------------
# Real-file: end-to-end localized name resolution (requires Fallout 4)
# ---------------------------------------------------------------------------


@pytest.mark.gamefiles
class TestPartialLoad:
    """Plugin.load(only_signatures=...) parses just the named top-level groups
    and seeks past the rest — a fast partial load."""

    def test_only_npc_skips_other_groups(self):
        esm = find_game_file('Fallout4.esm', 'fo4')
        if not esm:
            pytest.skip("Fallout4.esm not found")
        p = Plugin.load(esm, only_signatures={'NPC_'})
        assert p.partial_load is True
        assert len(list(p.get_records_by_signature('NPC_'))) > 1000
        # Groups we didn't ask for must not have been parsed.
        assert not list(p.get_records_by_signature('WEAP'))
        assert not list(p.get_records_by_signature('CELL'))
        # TES4 header is always parsed.
        assert p.header is not None

    def test_empty_set_is_header_only(self):
        esm = find_game_file('Fallout4.esm', 'fo4')
        if not esm:
            pytest.skip("Fallout4.esm not found")
        p = Plugin.load(esm, only_signatures=frozenset())
        assert p.partial_load is True
        assert not list(p.get_records_by_signature('NPC_'))
        assert not list(p.get_records_by_signature('WEAP'))
        assert p.header is not None  # TES4 still parsed


@pytest.mark.gamefiles
@pytest.mark.slow
class TestFo4StringResolution:


    @pytest.fixture(scope='class')
    def fo4(self):
        esm = find_game_file('Fallout4.esm', 'fo4')
        if not esm:
            pytest.skip("Fallout4.esm not found")
        return Plugin.load(esm)


    def test_string_tables_loaded(self, fo4):
        assert fo4.is_localized
        assert fo4.string_tables is not None
        assert fo4.string_tables.strings is not None


    def test_npc_full_names_resolve(self, fo4):
        resolved = 0
        for npc in fo4.get_records_by_signature('NPC_'):
            full = npc.get_subrecord('FULL')
            if full is None or full.size != 4:
                continue
            if npc.full_name:
                resolved += 1
            if resolved >= 5:
                break
        assert resolved >= 5, "expected localized NPC names to resolve from BA2"
