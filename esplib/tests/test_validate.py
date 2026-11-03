"""Plugin.validate(): what it reports about a plugin's own and foreign FormIDs.

A new record gets the sentinel file index 0xFF until save() gives it the plugin's own index
(len(masters)). Before save, validate() took the sentinel for an unknown master: a plugin builder
that checked its plugin before saving got one bogus "unknown master file" line per new record.
The light-plugin checks counted the plugin's own records by file index 0, which is only right
for a plugin with no masters, so they never looked at a pending record, nor at the records of a
light plugin that has masters.
"""

from esplib import FormID, Plugin


def light_plugin(tmp_path, masters=('Starfield.esm', 'FSF.esm')):
    p = Plugin.new_plugin(tmp_path / 'Test.esp', masters=list(masters), game='tes5')
    p.header.is_esl = True
    return p


def test_pending_records_are_not_unknown_masters(tmp_path):
    p = light_plugin(tmp_path)
    for i in range(3):
        p.new_record('GLOB', f'TestGlobal{i}')
    assert all(r.form_id.file_index == Plugin._LOCAL_SENTINEL for r in p.records)
    assert p.validate() == []


def test_own_and_master_records_are_fine(tmp_path):
    p = light_plugin(tmp_path)
    p.new_record('GLOB', 'Own', form_id=0x800)                 # file index 2 = this plugin
    override = p.new_record('GLOB', 'Override')
    override.form_id = FormID(0x01000D62)                       # FSF.esm's record
    assert p.validate() == []


def test_unknown_master_is_still_reported(tmp_path):
    p = light_plugin(tmp_path)
    stray = p.new_record('GLOB', 'Stray')
    stray.form_id = FormID(0x05000801)                          # only 2 masters
    issues = p.validate()
    assert len(issues) == 1 and 'unknown master file' in issues[0]


def test_light_limits_apply_to_own_records_with_masters(tmp_path):
    p = light_plugin(tmp_path)
    p.new_record('GLOB', 'Big', form_id=0x1000)                # own index 2, over 0xFFF
    issues = p.validate()
    assert any('ESL FormID out of range' in i for i in issues), issues


def test_light_limits_apply_to_pending_records(tmp_path):
    p = light_plugin(tmp_path)
    p.header.next_object_id = 0x1000
    p.new_record('GLOB', 'PendingBig')                           # sentinel index, object 0x1000
    issues = p.validate()
    assert any('ESL FormID out of range' in i for i in issues), issues
    assert not any('unknown master' in i for i in issues), issues
