"""A Starfield plugin's TES4 header, built from scratch, is laid out the Starfield way.

Starfield's masters are bare MAST entries -- no DATA (master size) after each, as in Skyrim and
Fallout 4 -- and xEdit's definition requires CNAM (author) and INCC (interior cell count). Every
CK-made Starfield plugin reads HEDR CNAM [SNAM] MAST... INCC. esplib wrote HEDR MAST DATA MAST
DATA, and xEdit reported each DATA as "unexpected (or out of order) subrecord" (Bad Dog,
FSFNPC_Fox.esm, 2026-10-20).
"""
import struct

from esplib import Plugin


def header_sigs(path):
    with open(path, 'rb') as f:
        hdr = f.read(24)
        body = f.read(struct.unpack_from('<I', hdr, 4)[0])
    pos, sigs = 0, []
    while pos < len(body):
        sigs.append(body[pos:pos + 4].decode('ascii'))
        pos += 6 + struct.unpack_from('<H', body, pos + 4)[0]
    return sigs


def build(tmp_path, version, name='Test.esm', **header):
    p = Plugin()
    p.file_path = tmp_path / name
    p.header.version = version
    p.header.masters = ['Starfield.esm', 'FSF.esm']
    p.header.master_sizes = [0, 0]
    for k, v in header.items():
        setattr(p.header, k, v)
    p.save()
    return p.file_path


def test_starfield_header_layout(tmp_path):
    path = build(tmp_path, 0.96)
    assert header_sigs(path) == ['HEDR', 'CNAM', 'MAST', 'MAST', 'INCC']
    assert Plugin(path).header.masters == ['Starfield.esm', 'FSF.esm']


def test_starfield_header_keeps_author_and_description(tmp_path):
    path = build(tmp_path, 0.96, author='Bad Dog', description='Fox crowds')
    assert header_sigs(path) == ['HEDR', 'CNAM', 'SNAM', 'MAST', 'MAST', 'INCC']
    again = Plugin(path).header
    assert (again.author, again.description) == ('Bad Dog', 'Fox crowds')


def test_older_games_keep_master_sizes(tmp_path):
    for version in (1.71, 1.0):                                   # Skyrim SE, Fallout 4
        sigs = header_sigs(build(tmp_path, version, name=f'v{version}.esm'))
        assert sigs == ['HEDR', 'MAST', 'DATA', 'MAST', 'DATA'], (version, sigs)
