"""BA2 archive reading for Fallout 4 (and compatible Starfield archives).

Minimal reader focused on extracting files (especially string tables and
loose-equivalent assets) from Bethesda BA2 archives. Mirrors the public
API of :class:`esplib.bsa.BsaReader` so callers can treat the two
interchangeably:

    with Ba2Reader("Fallout4 - Interface.ba2") as ba2:
        data = ba2.read_file("strings/fallout4_en.strings")

Two archive types exist:
  - GNRL: general files (meshes, strings, scripts, …). Stored verbatim or
    zlib-compressed. Fully supported here.
  - DX10: textures, stored as a per-texture header plus chunked, individually
    zlib-compressed mip data with no on-disk DDS header. We index them and
    reconstruct a valid DDS on extract: a DX10-extended header (built from the
    texture header's width/height/mip-count/DXGI-format) followed by the
    decompressed mip chunks concatenated in order. PIL and PyNifly can then
    open the result by path like any loose .dds.
"""

import struct
import zlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)


# --- BA2 file/dir name hash ------------------------------------------------
# The engine resolves a file by the (dir_hash, name_hash, ext) triple, so the
# hash is mandatory for a writer (the name table alone is not enough). It is a
# RAW table CRC32 — poly 0xEDB88320 (reflected, the same table zlib uses) but
# init = 0 and NO final XOR, which is the only difference from zlib's standard
# crc32. Verified 60/60 against Fallout4 - Interface.ba2.

def _make_hash_table(poly: int = 0xEDB88320) -> list:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c >> 1) ^ poly) if (c & 1) else (c >> 1)
        table.append(c & 0xFFFFFFFF)
    return table


_HASH_TABLE = _make_hash_table()


def ba2_hash(s: str) -> int:
    """The BA2 name/dir hash: raw table CRC32 of `s` lowercased, cp1252."""
    c = 0
    for b in s.lower().encode('cp1252', 'replace'):
        c = ((c >> 8) ^ _HASH_TABLE[(c ^ b) & 0xFF]) & 0xFFFFFFFF
    return c


def _hash_path(archive_path: str) -> tuple:
    """Split an in-archive path into (dir_hash, name_hash, ext_bytes).

    `ext_bytes` is the lowercased extension WITHOUT the dot, the first 4 bytes,
    null-padded to 4 (e.g. b'nif\\x00', b'dds\\x00'). Directory is backslash-
    separated with no leading/trailing slash; the filename stem carries no
    extension. Matches how Ba2Reader indexes (dir_hash, name_hash, ext)."""
    p = archive_path.replace('/', '\\').strip('\\')
    dir_part, sep, filename = p.rpartition('\\')
    if '.' in filename:
        stem, _, ext = filename.rpartition('.')
    else:
        stem, ext = filename, ''
    ext_bytes = ext.lower().encode('cp1252', 'replace')[:4].ljust(4, b'\x00')
    return ba2_hash(dir_part), ba2_hash(stem), ext_bytes


# --- DDS header reconstruction for DX10 textures ---------------------------
_DDS_MAGIC = b"DDS "
_DDSD_CAPS = 0x1
_DDSD_HEIGHT = 0x2
_DDSD_WIDTH = 0x4
_DDSD_PIXELFORMAT = 0x1000
_DDSD_PITCH = 0x8
_DDSD_LINEARSIZE = 0x80000
_DDSD_MIPMAPCOUNT = 0x20000
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_COMPLEX = 0x8
_DDSCAPS_MIPMAP = 0x400000
_DDPF_FOURCC = 0x4
_D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3

# DXGI_FORMAT -> bytes per 4x4 block, for the block-compressed formats. BC1
# and BC4 pack a block in 8 bytes; every other BC format uses 16.
_BC_BLOCK_BYTES = {
    70: 8, 71: 8, 72: 8,            # BC1 (typeless/unorm/srgb)
    73: 16, 74: 16, 75: 16,         # BC2
    76: 16, 77: 16, 78: 16,         # BC3
    79: 8, 80: 8, 81: 8,            # BC4
    82: 16, 83: 16, 84: 16,         # BC5
    94: 16, 95: 16, 96: 16,         # BC6H
    97: 16, 98: 16, 99: 16,         # BC7
}

# DXGI_FORMAT -> bytes per pixel, for the uncompressed formats FO4 textures
# occasionally use (face/body maps are almost always BC, but be complete).
_UNCOMPRESSED_BPP = {
    2: 16, 10: 8,                   # R32G32B32A32, R16G16B16A16
    28: 4, 29: 4,                   # R8G8B8A8 (unorm/srgb)
    87: 4, 88: 4, 91: 4,           # B8G8R8A8 / B8G8R8X8 variants
    49: 2, 61: 1, 80: 1,           # R8G8, R8, A8 (80 is BC4 above; A8 is 65)
    65: 1,                          # A8_UNORM
}


def _build_dx10_dds_header(width: int, height: int, num_mips: int,
                           dxgi_format: int) -> bytes:
    """Construct a DDS magic + DDS_HEADER + DDS_HEADER_DXT10 prefix for a
    texture of the given dimensions/format. Mirrors the writer in the
    furrifier's facegen.dds but parameterized on the DXGI format and mip
    count read back from the BA2 texture header."""
    num_mips = max(1, num_mips)
    block_bytes = _BC_BLOCK_BYTES.get(dxgi_format)
    if block_bytes is not None:
        # Compressed: pitchOrLinearSize = total bytes of the top mip.
        blocks_w = max(1, (width + 3) // 4)
        blocks_h = max(1, (height + 3) // 4)
        pitch_or_linear = blocks_w * blocks_h * block_bytes
        flags = (_DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH
                 | _DDSD_PIXELFORMAT | _DDSD_LINEARSIZE)
    else:
        # Uncompressed: pitchOrLinearSize = bytes in one scanline.
        bpp = _UNCOMPRESSED_BPP.get(dxgi_format, 4)
        pitch_or_linear = width * bpp
        flags = (_DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH
                 | _DDSD_PIXELFORMAT | _DDSD_PITCH)

    caps1 = _DDSCAPS_TEXTURE
    if num_mips > 1:
        flags |= _DDSD_MIPMAPCOUNT
        caps1 |= _DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP

    pixelformat = struct.pack(
        "<I I 4s I I I I I",
        32, _DDPF_FOURCC, b"DX10", 0, 0, 0, 0, 0)
    header = struct.pack(
        "<I I I I I I I 11I 32s 5I",
        124, flags, height, width, pitch_or_linear, 0, num_mips,
        *([0] * 11), pixelformat, caps1, 0, 0, 0, 0)
    dxt10 = struct.pack(
        "<I I I I I",
        dxgi_format, _D3D10_RESOURCE_DIMENSION_TEXTURE2D, 0, 1, 0)
    return _DDS_MAGIC + header + dxt10


# Legacy FOURCC -> DXGI_FORMAT, for .dds inputs that predate the DX10 header.
# The furrifier always writes DX10 BC7, so this is a robustness fallback.
_FOURCC_TO_DXGI = {
    b'DXT1': 71,   # BC1_UNORM
    b'DXT3': 74,   # BC2_UNORM
    b'DXT5': 77,   # BC3_UNORM
    b'ATI2': 83,   # BC5_UNORM
    b'BC5U': 83,
}


def _parse_dds(data: bytes) -> tuple:
    """Parse a .dds into (width, height, num_mips, dxgi_format, mip_body).

    `mip_body` is the raw mip pyramid (everything after the header) — exactly
    what a DX10 BA2 chunk stores. The inverse of Ba2Reader._extract_dx10, which
    rebuilds the header from these same fields."""
    if data[:4] != b'DDS ':
        raise ValueError("not a DDS file (bad magic)")
    height = struct.unpack_from('<I', data, 12)[0]
    width = struct.unpack_from('<I', data, 16)[0]
    num_mips = struct.unpack_from('<I', data, 28)[0] or 1
    fourcc = data[84:88]
    if fourcc == b'DX10':
        dxgi_format = struct.unpack_from('<I', data, 128)[0]
        body = data[148:]   # 4 magic + 124 header + 20 DXT10 header
    else:
        dxgi_format = _FOURCC_TO_DXGI.get(fourcc)
        if dxgi_format is None:
            raise ValueError(
                f"unsupported DDS fourCC {fourcc!r}; expected a DX10 header")
        body = data[128:]   # 4 magic + 124 header
    return width, height, num_mips, dxgi_format, body


def _mip_byte_sizes(width: int, height: int, num_mips: int,
                    dxgi_format: int) -> list:
    """Byte size of each mip level, largest first. Block-compressed formats
    round dimensions up to 4x4 blocks; uncompressed use bytes-per-pixel."""
    block = _BC_BLOCK_BYTES.get(dxgi_format)
    bpp = _UNCOMPRESSED_BPP.get(dxgi_format, 4)
    sizes = []
    for m in range(max(1, num_mips)):
        w = max(1, width >> m)
        h = max(1, height >> m)
        if block is not None:
            sizes.append(max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block)
        else:
            sizes.append(w * h * bpp)
    return sizes


def _split_mip_chunks(body: bytes, width: int, height: int, num_mips: int,
                      dxgi_format: int) -> list:
    """Split a mip pyramid into FO4's streaming chunk layout: mip 0, mip 1, then
    the remaining mips bundled — i.e. [(start_mip, end_mip, bytes), ...], up to
    3 chunks (matches Archive2 / vanilla). Splitting per-mip lets the engine
    stream low mips cheaply; a single all-mips chunk forces a full-resolution
    load of every texture and tanks framerate at scale. Falls back to one chunk
    spanning all mips if the computed sizes don't sum to the body (e.g. an
    unexpected/array/cubemap layout) — correctness over the optimization."""
    n = max(1, num_mips)
    sizes = _mip_byte_sizes(width, height, n, dxgi_format)
    if sum(sizes) != len(body):
        return [(0, n - 1, body)]
    slices, off = [], 0
    for s in sizes:
        slices.append(body[off:off + s])
        off += s
    n_chunks = min(3, n)
    groups = [(c, c, slices[c]) for c in range(n_chunks - 1)]
    tail = n_chunks - 1
    groups.append((tail, n - 1, b''.join(slices[tail:])))
    return groups


class _Ba2Entry:
    """Metadata for a single file in a BA2 archive."""

    def __init__(self):
        self.name_hash = 0
        self.ext = ""
        self.dir_hash = 0
        self.offset = 0
        self.packed_size = 0
        self.unpacked_size = 0
        self.name = ""  # full in-archive path, set from the name table
        # DX10 texture fields (unused for GNRL entries).
        self.is_dx10 = False
        self.dx10_width = 0
        self.dx10_height = 0
        self.dx10_num_mips = 0
        self.dx10_format = 0
        self.chunks: List[tuple] = []  # (offset, packed_size, unpacked_size)

    def __repr__(self):
        return f"_Ba2Entry({self.name!r})"


class Ba2Reader:
    """Reads Bethesda BA2 archives (Fallout 4).

    Usage:
        with Ba2Reader("Fallout4 - Interface.ba2") as ba2:
            data = ba2.read_file("strings/fallout4_en.strings")
    """

    def __init__(self, path):
        self.path = Path(path)
        self._file = None
        self.archive_type: Optional[str] = None  # 'GNRL' or 'DX10'
        self._version = None
        self._entries: List[_Ba2Entry] = []
        self._by_name: Dict[str, _Ba2Entry] = {}


    def __enter__(self):
        self.open()
        return self


    def __exit__(self, *args):
        self.close()


    def open(self):
        self._file = open(self.path, 'rb')
        self._read_index()
        return self


    def close(self):
        if self._file:
            self._file.close()
            self._file = None


    def _read_index(self):
        f = self._file
        magic = f.read(4)
        if magic != b'BTDX':
            raise ValueError(f"Not a BA2 file: {self.path}")

        self._version = struct.unpack('<I', f.read(4))[0]
        archive_type = f.read(4)
        self.archive_type = archive_type.decode('ascii', 'replace').rstrip('\x00')
        file_count = struct.unpack('<I', f.read(4))[0]
        name_table_offset = struct.unpack('<Q', f.read(8))[0]

        if self.archive_type == 'GNRL':
            self._read_gnrl(f, file_count)
        elif self.archive_type == 'DX10':
            self._read_dx10(f, file_count)
        else:
            raise ValueError(
                f"Unknown BA2 type {self.archive_type!r}: {self.path}")

        self._read_name_table(f, name_table_offset, file_count)


    def _read_gnrl(self, f, file_count):
        # GNRL entry: name_hash(4) ext(4) dir_hash(4) flags(4)
        #             offset(8) packed_size(4) unpacked_size(4) align(4)
        for _ in range(file_count):
            name_hash = struct.unpack('<I', f.read(4))[0]
            ext = f.read(4)
            dir_hash = struct.unpack('<I', f.read(4))[0]
            _flags = struct.unpack('<I', f.read(4))[0]
            offset = struct.unpack('<Q', f.read(8))[0]
            packed_size = struct.unpack('<I', f.read(4))[0]
            unpacked_size = struct.unpack('<I', f.read(4))[0]
            _align = struct.unpack('<I', f.read(4))[0]
            entry = _Ba2Entry()
            entry.name_hash = name_hash
            entry.ext = ext.decode('ascii', 'replace').rstrip('\x00')
            entry.dir_hash = dir_hash
            entry.offset = offset
            entry.packed_size = packed_size
            entry.unpacked_size = unpacked_size
            self._entries.append(entry)


    def _read_dx10(self, f, file_count):
        # DX10 entry header (24 bytes) then chunk_count chunks (24 bytes each).
        # We record the texture header (width/height/mips/format) and every
        # chunk so _extract can rebuild a valid DDS (see module docstring).
        for _ in range(file_count):
            name_hash = struct.unpack('<I', f.read(4))[0]
            ext = f.read(4)
            dir_hash = struct.unpack('<I', f.read(4))[0]
            _unk = f.read(1)
            chunk_count = f.read(1)[0]
            _chunk_hdr_size = struct.unpack('<H', f.read(2))[0]
            height = struct.unpack('<H', f.read(2))[0]
            width = struct.unpack('<H', f.read(2))[0]
            num_mips = f.read(1)[0]
            fmt = f.read(1)[0]
            _unk2 = f.read(2)  # isCubemap(u8) + tileMode(u8) — unused here
            entry = _Ba2Entry()
            entry.name_hash = name_hash
            entry.ext = ext.decode('ascii', 'replace').rstrip('\x00')
            entry.dir_hash = dir_hash
            entry.is_dx10 = True
            entry.dx10_width = width
            entry.dx10_height = height
            entry.dx10_num_mips = num_mips
            entry.dx10_format = fmt
            for ci in range(chunk_count):
                ch_offset = struct.unpack('<Q', f.read(8))[0]
                ch_packed = struct.unpack('<I', f.read(4))[0]
                ch_unpacked = struct.unpack('<I', f.read(4))[0]
                _start_mip = struct.unpack('<H', f.read(2))[0]
                _end_mip = struct.unpack('<H', f.read(2))[0]
                _ch_align = struct.unpack('<I', f.read(4))[0]
                entry.chunks.append((ch_offset, ch_packed, ch_unpacked))
                if ci == 0:
                    entry.offset = ch_offset
                    entry.packed_size = ch_packed
                    entry.unpacked_size = ch_unpacked
            self._entries.append(entry)


    def _read_name_table(self, f, offset, file_count):
        f.seek(offset)
        for i in range(file_count):
            if i >= len(self._entries):
                break
            len_bytes = f.read(2)
            if len(len_bytes) < 2:
                break
            name_len = struct.unpack('<H', len_bytes)[0]
            name = f.read(name_len).decode('cp1252', 'replace')
            self._entries[i].name = name
            self._by_name[_norm(name)] = self._entries[i]


    def list_files(self) -> List[str]:
        """Return every in-archive file path."""
        return [e.name for e in self._entries]


    def has_file(self, path: str) -> bool:
        """True if the given path exists in the archive (case-insensitive)."""
        return _norm(path) in self._by_name


    def read_file(self, path: str) -> Optional[bytes]:
        """Extract a file by its archive path (e.g. 'strings/foo.strings').

        Matching is case-insensitive and slash-insensitive. Returns None if
        not found. GNRL files are fully decompressed; DX10 entries are
        reassembled into a complete DX10-headered DDS (see module docstring).
        """
        entry = self._by_name.get(_norm(path))
        if entry is None:
            return None
        if entry.is_dx10:
            return self._extract_dx10(entry)
        return self._extract(entry)


    def _extract(self, entry: _Ba2Entry) -> bytes:
        f = self._file
        f.seek(entry.offset)
        size = entry.packed_size or entry.unpacked_size
        raw = f.read(size)
        # Compressed when a non-zero packed size differs from unpacked.
        if entry.packed_size and entry.packed_size != entry.unpacked_size:
            return zlib.decompress(raw)
        return raw


    def _extract_dx10(self, entry: _Ba2Entry) -> bytes:
        """Rebuild a DDS for a DX10 texture: header reconstructed from the
        texture metadata, body = the decompressed mip chunks concatenated in
        archive order (largest mip first)."""
        f = self._file
        body = bytearray()
        for offset, packed, unpacked in entry.chunks:
            f.seek(offset)
            size = packed or unpacked
            raw = f.read(size)
            if packed and packed != unpacked:
                raw = zlib.decompress(raw)
            body += raw
        header = _build_dx10_dds_header(
            entry.dx10_width, entry.dx10_height,
            entry.dx10_num_mips, entry.dx10_format)
        return bytes(header) + bytes(body)


    def __repr__(self):
        return f"Ba2Reader({self.path}, type={self.archive_type})"


def _norm(path: str) -> str:
    """Normalize an archive path for case/slash-insensitive matching."""
    return path.replace('/', '\\').lower()


# BA2 binary layout shared by reader and writer.
_BA2_HEADER = '<4s I 4s I Q'          # magic, version, type, file_count, name_tbl_off
_BA2_HEADER_SIZE = struct.calcsize(_BA2_HEADER)   # 24
_GNRL_RECORD = '<I 4s I I Q I I I'    # name_hash, ext, dir_hash, flags, off, packed, unpacked, align
_GNRL_RECORD_SIZE = struct.calcsize(_GNRL_RECORD)  # 36
_DX10_TEX_HEADER = '<I 4s I B B H H H B B B B'     # +chunk records follow
_DX10_TEX_HEADER_SIZE = struct.calcsize(_DX10_TEX_HEADER)   # 24
_DX10_CHUNK = '<Q I I H H I'          # off, packed, unpacked, start_mip, end_mip, align
_DX10_CHUNK_SIZE = struct.calcsize(_DX10_CHUNK)    # 24

# Field VALUES the FO4 engine's archive-index parser requires — verified
# constant across every record of vanilla Fallout4 - Interface.ba2 (GNRL) and
# Fallout4 - Textures1.ba2 (DX10). Writing 0 here (which the reader tolerates)
# makes the engine build a corrupt index entry and crash at registration time,
# so the writer must reproduce them exactly.
_GNRL_FLAGS = 0x00100100      # the GNRL record "flags" dword (always this)
_BAADF00D = 0xBAADF00D        # alignment sentinel in the record / chunk tail
_DX10_TILE_MODE = 8           # vanilla DX10 tex-header tileMode byte


class Ba2Writer:
    """Writes a Bethesda BA2 archive (Fallout 4). The inverse of Ba2Reader.

    One archive holds one type: 'GNRL' (general files — add_file) or 'DX10'
    (textures — add_dds). FO4 stores textures only in DX10 archives and
    everything else in GNRL, so a facegen pack needs one of each.

        w = Ba2Writer('GNRL')
        w.add_file(r'Meshes\\...\\00ab.nif', nif_bytes)
        w.write('MyMod - Main.ba2')
    """

    def __init__(self, archive_type: str = 'GNRL', *,
                 compress: bool = True, version: int = 1):
        if archive_type not in ('GNRL', 'DX10'):
            raise ValueError(f"archive_type must be 'GNRL' or 'DX10', "
                             f"got {archive_type!r}")
        self.archive_type = archive_type
        self.compress = compress
        self.version = version
        self._files: List[dict] = []


    def _maybe_compress(self, data: bytes) -> tuple:
        """(blob, packed_size, unpacked_size). packed_size 0 == stored — the
        convention Ba2Reader decodes (it decompresses only when packed_size is
        non-zero and differs from unpacked_size)."""
        unpacked = len(data)
        if self.compress:
            comp = zlib.compress(data)
            if len(comp) < unpacked:
                return comp, len(comp), unpacked
        return data, 0, unpacked


    def add_file(self, archive_path: str, data: bytes) -> None:
        """Add a general (GNRL) file by its in-archive path."""
        if self.archive_type != 'GNRL':
            raise ValueError("add_file is for GNRL archives; use add_dds")
        dir_hash, name_hash, ext = _hash_path(archive_path)
        blob, packed, unpacked = self._maybe_compress(data)
        self._files.append({
            'name': archive_path.replace('/', '\\').strip('\\'),
            'name_hash': name_hash, 'dir_hash': dir_hash, 'ext': ext,
            'blob': blob, 'packed': packed, 'unpacked': unpacked,
        })


    def add_dds(self, archive_path: str, dds_bytes: bytes) -> None:
        """Add a texture (DX10) by parsing the .dds into a tex header + raw mip
        body, split into FO4's streaming chunk layout (see _split_mip_chunks)."""
        if self.archive_type != 'DX10':
            raise ValueError("add_dds is for DX10 archives; use add_file")
        width, height, num_mips, fmt, body = _parse_dds(dds_bytes)
        dir_hash, name_hash, ext = _hash_path(archive_path)
        chunks = []
        for start_mip, end_mip, raw in _split_mip_chunks(
                body, width, height, num_mips, fmt):
            blob, packed, unpacked = self._maybe_compress(raw)
            chunks.append({'blob': blob, 'packed': packed, 'unpacked': unpacked,
                           'start_mip': start_mip, 'end_mip': end_mip})
        self._files.append({
            'name': archive_path.replace('/', '\\').strip('\\'),
            'name_hash': name_hash, 'dir_hash': dir_hash, 'ext': ext,
            'chunks': chunks,
            'width': width, 'height': height, 'num_mips': num_mips, 'fmt': fmt,
        })


    def __len__(self) -> int:
        return len(self._files)


    def write(self, out_path) -> None:
        if self.archive_type == 'GNRL':
            records_size = _GNRL_RECORD_SIZE * len(self._files)
        else:
            records_size = sum(
                _DX10_TEX_HEADER_SIZE + _DX10_CHUNK_SIZE * len(f['chunks'])
                for f in self._files)
        data_start = _BA2_HEADER_SIZE + records_size

        records = bytearray()
        data = bytearray()
        offset = data_start
        for f in self._files:
            if self.archive_type == 'GNRL':
                records += struct.pack(
                    _GNRL_RECORD, f['name_hash'], f['ext'], f['dir_hash'],
                    _GNRL_FLAGS, offset, f['packed'], f['unpacked'], _BAADF00D)
                data += f['blob']
                offset += len(f['blob'])
            else:
                chunks = f['chunks']
                records += struct.pack(
                    _DX10_TEX_HEADER, f['name_hash'], f['ext'], f['dir_hash'],
                    0, len(chunks), _DX10_CHUNK_SIZE, f['height'], f['width'],
                    f['num_mips'], f['fmt'], 0, _DX10_TILE_MODE)
                for ch in chunks:
                    records += struct.pack(
                        _DX10_CHUNK, offset, ch['packed'], ch['unpacked'],
                        ch['start_mip'], ch['end_mip'], _BAADF00D)
                    data += ch['blob']
                    offset += len(ch['blob'])

        name_table_offset = data_start + len(data)
        name_table = bytearray()
        for f in self._files:
            nm = f['name'].encode('cp1252', 'replace')
            name_table += struct.pack('<H', len(nm)) + nm

        header = struct.pack(
            _BA2_HEADER, b'BTDX', self.version,
            self.archive_type.encode('ascii'), len(self._files),
            name_table_offset)

        with open(out_path, 'wb') as fh:
            fh.write(header)
            fh.write(records)
            fh.write(data)
            fh.write(name_table)
