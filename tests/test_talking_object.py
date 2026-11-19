"""create_talking_object: a TACT base form, a placed REFR and GetIsID-conditioned dialogue."""
import pytest
from esplib import Plugin

import server

pytestmark = pytest.mark.fakegame

PLACE = {'worldspace': 'Tamriel', 'grid_x': 4, 'grid_y': -11, 'x': 10.0, 'y': 20.0, 'z': 30.0}
SPEC = {'editor_id': 'TalkBarrel', 'name': 'Old Barrel', 'voice_type': 'MaleNord', 'placement': PLACE,
        'dialogue': [{'kind': 'greeting', 'lines': ['Hic.']}, {'prompt': 'Who are you?', 'lines': ['A barrel.']}]}


def test_builds_tact_ref_and_dialogue(sandbox):
    res = server.create_talking_object(SPEC, 'Barrel.esp')
    assert 'error' not in res, res
    plugin = Plugin.load(res['path'])
    tact = list(plugin.get_records_by_signature('TACT'))[0]
    assert tact.editor_id == 'TalkBarrel'
    assert tact.get_subrecord('MODL').data.rstrip(b'\0') == b'Clutter\\Barrel02.nif'
    assert tact.get_subrecord('VNAM') is not None
    ref = list(plugin.get_records_by_signature('REFR'))[0]
    assert ref.get_subrecord('NAME').data == tact.form_id.value.to_bytes(4, 'little')
    quest = list(plugin.get_records_by_signature('QUST'))[0]
    assert quest.get_subrecord('ALST') is None
    assert len(list(plugin.get_records_by_signature('INFO'))) == 2


@pytest.mark.parametrize('change', [{'dialogue': []}, {'name': ''}, {'bounds': [1, 2]}, {'bogus': 1},
                                    {'voice_type': 'NoSuchVoice'}])
def test_rejects_bad_specs(sandbox, change):
    assert 'error' in server.create_talking_object(dict(SPEC, **change), 'Bad.esp')
