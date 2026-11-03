"""Starfield plugins (header version 0.96) are not Fallout 4 plugins.

detect_game's Fallout 4 range (0.945-1.01) took Starfield's 0.96, so every Starfield plugin read
from disk was bound to Fallout 4's schemas. A modified record is written in its schema's member
order, with signatures the schema doesn't know moved to the end -- so a Starfield record a script
edited came out in Fallout 4's order (an ARMA's BFCB/BFCE/BO64 after its models, an NPC_'s ODTY
after DOFT), and xEdit reported every one as "unexpected (or out of order) subrecord".
esplib has no Starfield definitions, so a Starfield plugin is schemaless and keeps its order.
"""

from esplib import Plugin, Record
from esplib.defs.game import GameRegistry

import esplib.defs.fo4  # noqa: F401  registers fo4

# A Starfield ARMA's own order (FSF.esm's FSFCanine_AA_Naked_Tail, abridged).
SF_ARMA = ['EDID', 'BFCB', 'BFCE', 'BO64', 'RNAM', 'DNAM', 'MOD2', 'MOD3', 'FLLD', 'MNAM', 'TNAM']


def test_starfield_version_is_not_fallout4():
    assert GameRegistry.detect_game(0.96) is not GameRegistry.get_game('fo4')
    assert GameRegistry.detect_game(0.95) is GameRegistry.get_game('fo4')
    assert GameRegistry.detect_game(1.0) is GameRegistry.get_game('fo4')


def test_modified_starfield_record_keeps_its_order(tmp_path):
    p = Plugin()
    p.file_path = tmp_path / 'SF.esm'
    p.header.version = 0.96
    p.header.masters = ['Starfield.esm']
    p.header.master_sizes = [0]
    rec = Record('ARMA', 0, 0)
    for sig in SF_ARMA:
        rec.add_subrecord(sig, b'x\0' if sig in ('EDID', 'MOD2', 'MOD3', 'TNAM') else b'\0\0\0\0')
    p.add_record(rec)
    p.save()

    again = Plugin(tmp_path / 'SF.esm')
    arma = next(iter(again.get_records_by_signature('ARMA')))
    arma.get_subrecord('MOD2').data = b'changed\0'          # modified: written from its children
    arma.modified = True
    again.save()

    final = next(iter(Plugin(tmp_path / 'SF.esm').get_records_by_signature('ARMA')))
    assert [s.signature for s in final.subrecords] == SF_ARMA
