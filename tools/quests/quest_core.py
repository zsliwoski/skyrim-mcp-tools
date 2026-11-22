"""Shared QUST subrecord writers: the quest header, stages, objectives and aliases every quest builder emits."""
import struct

from tools.npcs.npc_builder import z


def write_header(q, editor_id, name, flags=0x1, priority=50, quest_type=0, event=None):
    """EDID, FULL, DNAM (and ENAM for a story-manager event such as b'SCPT') and the NEXT marker that precedes stages."""
    q.add_subrecord('EDID', z(editor_id))
    q.add_subrecord('FULL', z(name))
    q.add_subrecord('DNAM', struct.pack('<HBBII', flags, priority, 0, 0, quest_type))
    if event is not None:
        q.add_subrecord('ENAM', event)
    q.add_subrecord('NEXT', b'')


def write_stage(q, index, text=None, flags=0, complete=None):
    """INDX/QSDT/CNAM. complete is the QSDT byte (1 completes the quest); default is 1 from stage 30 up."""
    q.add_subrecord('INDX', struct.pack('<HBB', index, flags, 0))
    q.add_subrecord('QSDT', struct.pack('<B', (1 if index >= 30 else 0) if complete is None else complete))
    if text is not None:
        q.add_subrecord('CNAM', z(text))


def write_objective(q, index, text, targets=(), flags=0):
    """QOBJ/FNAM/NNAM followed by a QSTA per target, given as (alias id, target flags)."""
    q.add_subrecord('QOBJ', struct.pack('<H', index))
    q.add_subrecord('FNAM', struct.pack('<I', flags))
    q.add_subrecord('NNAM', z(text))
    for alias_id, tflags in targets:
        q.add_subrecord('QSTA', struct.pack('<II', alias_id, tflags))


def write_next_alias_id(q, value):
    """ANAM: the next free alias ID, written after the objectives."""
    q.add_subrecord('ANAM', struct.pack('<I', value))


def write_alias(q, alias_id, name, flags, fills=(), start='ALST', vtck=True):
    """One alias block: ALST (or ALLS for a location alias), ALID, FNAM flags, the fill subrecords given as
    (signature, bytes), an optional VTCK, ALED."""
    q.add_subrecord(start, struct.pack('<I', alias_id))
    q.add_subrecord('ALID', z(name))
    q.add_subrecord('FNAM', struct.pack('<I', flags))
    for sig, data in fills:
        q.add_subrecord(sig, data)
    if vtck:
        q.add_subrecord('VTCK', struct.pack('<I', 0))
    q.add_subrecord('ALED', b'')


STAGE_TAIL = ('QOBJ', 'ANAM', 'ALST', 'ALLS')
OBJECTIVE_TAIL = ('ANAM', 'ALST', 'ALLS')


def has_index(q, sig, index):
    return any(s.signature == sig and struct.unpack('<H', bytes(s.data)[:2])[0] == index for s in q.subrecords)


def splice_block(q, sig, index, tail, writer):
    """Run writer(q) (which appends one INDX or QOBJ block) and move the new subrecords into index order:
    before the first same-kind block with a larger index, else before the first subrecord in tail."""
    if has_index(q, sig, index):
        raise ValueError(f"quest already has {'stage' if sig == 'INDX' else 'objective'} {index}")
    subs = q.subrecords
    n = len(subs)
    writer(q)
    block = subs[n:]
    del subs[n:]
    pos = next((i for i, s in enumerate(subs) if s.signature == sig
                and struct.unpack('<H', bytes(s.data)[:2])[0] > index), None)
    if pos is None:
        pos = next((i for i, s in enumerate(subs) if s.signature in tail), len(subs))
    subs[pos:pos] = block
    q.modified = True
