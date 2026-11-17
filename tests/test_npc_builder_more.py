"""Extra coverage for tools.npcs.npc_builder: NIF patching, lookups, spec planning, placement and FaceGen copy."""
import struct

import pytest

import server
from tools.npcs import npc_builder as nb

pytestmark = pytest.mark.fakegame


def build_nif(types, blocks, strings):
    """Minimal Skyrim SE NIF: one (type name, bytes) block each; header tables as the parsers expect."""
    d = b'Gamebryo File Format\n' + struct.pack('<IBIII', 0x14020007, 1, 12, len(blocks), 100)
    d += b'\x01a' * 3 + struct.pack('<H', len(types))
    for t in types:
        d += struct.pack('<I', len(t)) + t.encode()
    d += struct.pack('<%dH' % len(blocks), *[types.index(t) for t, _ in blocks])
    d += struct.pack('<%dI' % len(blocks), *[len(b) for _, b in blocks])
    d += struct.pack('<II', len(strings), max(len(s) for s in strings))
    for s in strings:
        d += struct.pack('<I', len(s)) + s.encode()
    return d + struct.pack('<I', 0) + b''.join(b for _, b in blocks)


TEX = 'data\\textures\\actors\\character\\facegendata\\facetint\\Skyrim.esm\\0001347F.dds'


def face_nif():
    block = b'\0' * 4 + struct.pack('<I', len(TEX)) + TEX.encode() + b'tail'
    return build_nif(['NiNode'], [('NiNode', block)], ['Skyrim.esm', TEX, 'Other'])


class TestNifPatching:
    def test_retarget_rewrites_strings_and_block_paths(self):
        out = nb.retarget_nif(face_nif(), 'Skyrim.esm', 'Mine.esp', '0001347F', '00000801')
        assert b'0001347F' not in out and out.count(b'Mine.esp\\00000801.dds') == 2
        n = TEX.replace('Skyrim.esm', 'Mine.esp').replace('0001347F', '00000801')
        assert struct.pack('<I', len(n)) + n.encode() in out
        assert out.endswith(b'tail')

    def test_retarget_without_string_table_fails(self):
        with pytest.raises(ValueError):
            nb.retarget_nif(build_nif(['NiNode'], [('NiNode', b'xxxx0001347Fxxxx')], ['Other']),
                            'Skyrim.esm', 'Mine.esp', '0001347F', '00000801')

    def test_hair_tint_changes_only_type_6_shaders(self):
        def shader(kind):
            return struct.pack('<III', kind, 0, 0) + b'\0' * 88 + struct.pack('<3f', 1, 1, 1) + b'\0' * 4
        data = build_nif(['BSLightingShaderProperty', 'NiNode'],
                         [('BSLightingShaderProperty', shader(6)), ('NiNode', b'\0' * 8),
                          ('BSLightingShaderProperty', shader(1))], ['a'])
        out, changed = nb.set_nif_hair_tint(data, (255, 0, 51))
        assert changed == 1 and len(out) == len(data)
        assert struct.pack('<3f', 1.0, 0.0, 51 / 255) in out
        assert out.count(struct.pack('<3f', 1, 1, 1)) == 1


class TestEnumValue:
    def test_names_indexes_and_errors(self):
        errors = []
        names = ['Calm', 'Angry']
        assert nb.enum_value(' angry ', names, 'mood', errors) == 1
        assert nb.enum_value(0, names, 'mood', errors) == 0 and not errors
        assert nb.enum_value('bored', names, 'mood', errors) == 0
        assert nb.enum_value(5, names, 'mood', errors) == 0
        assert len(errors) == 2 and 'out of range' in errors[1]


@pytest.fixture
def builder(sandbox):
    return server.npc_builder(None)


class TestResolver:
    def test_lookup_forms(self, builder):
        r = builder.resolver
        fid, rec = r.lookup('Sven', ['NPC_'])
        assert rec.editor_id == 'Sven' and fid == 0x01347F
        assert r.lookup('Skyrim.esm:01347F', ['NPC_'])[0] == fid
        assert r.lookup('0x01347F', ['NPC_'])[0] == fid
        assert r.lookup('0001347F', ['NPC_'])[0] == fid
        assert r.lookup('sve', ['NPC_'])[0] == fid
        assert r.lookup('Lydia', ['NPC_'])[1].editor_id == 'Lydia'

    def test_lookup_errors(self, builder):
        r = builder.resolver
        for query, sigs, text in (('', ['NPC_'], 'empty'), ('Skyrim.esm:999999', ['NPC_'], 'not found'),
                                  ('Nobody', ['NPC_'], 'no NPC_'), ('Sven', ['RACE'], 'no RACE')):
            with pytest.raises(ValueError, match=text):
                r.lookup(query, sigs)

    def test_ambiguous(self, builder):
        with pytest.raises(ValueError, match='ambiguous'):
            builder.resolver.lookup('n', ['NPC_', 'RACE'])

    def test_search(self, builder):
        hits = builder.resolver.search(['NPC_'], 'sven')
        assert [h['editor_id'] for h in hits] == ['Sven'] and hits[0]['plugin'] == 'Skyrim.esm'
        assert len(builder.resolver.search(['NPC_'], '', limit=1)) == 1
        assert nb.NpcLookup(builder.m, builder.resolver).find('Sven') is not None


class TestCellFinder:
    def test_find_by_grid_and_persistent(self, builder):
        plugin, chain, cell = builder.cells.find(worldspace='Tamriel', grid_x=4, grid_y=-11)
        assert plugin.file_path.name == 'Skyrim.esm' and builder.cells.grid(cell) == (4, -11)
        pplugin, pchain, pcell = builder.cells.persistent('tamriel')
        assert pcell.form_id.value & 0xFFFFFF == 0x0D3C95
        with pytest.raises(ValueError, match='no cell'):
            builder.cells.find(worldspace='Tamriel', grid_x=0, grid_y=0)
        with pytest.raises(ValueError, match='no cell'):
            builder.cells.find(cell='Nowhere')
        with pytest.raises(ValueError, match='persistent'):
            builder.cells.persistent('Nowhere')

    def test_find_by_form_id_and_search(self, builder):
        assert builder.cells.find(cell='000D3C96')[2].form_id.value & 0xFFFFFF == 0x0D3C96
        hits = builder.cells.search('')
        assert hits and all('form_id' in h for h in hits)
        assert builder.cells.search('zzzz-no-such-cell') == []


def plan(builder, **spec):
    base = {'editor_id': 'PlanNpc', 'name': 'Plan Npc', 'clone_from': 'Sven'}
    return builder.plan({**base, **spec})


class TestPlanning:
    def test_stats_validation(self, builder):
        p = plan(builder, level=0, health='a', aggression='Foolhardy', confidence='nope', mood=9,
                 skills={'smithing': 50, 'bogus': 1, 'archery': 200}, morality=7, assistance='x')
        text = ' | '.join(p.errors)
        for part in ('level must', 'health must', 'mood must', 'confidence', 'unknown skill', 'archery',
                     'morality', 'assistance'):
            assert part in text, part
        good = plan(builder, aggression='Aggressive', skills={'OneHanded': 40})
        assert not good.errors and good.stats['aggression'] == 1 and good.stats['skills']

    def test_basic_field_errors(self, builder):
        p = builder.plan({'editor_id': 'bad id', 'bogus': 1, 'name': 'Caf\u0153\u2603'})
        assert any('unknown field' in e for e in p.errors) and any('editor_id' in e for e in p.errors)
        assert any('Windows-1252' in e for e in p.errors) and any('race is required' in e for e in p.errors)
        dup = builder.plan({'editor_id': 'Sven', 'name': 'x', 'clone_from': 'Lydia'})
        assert any('already exists' in e for e in dup.errors)
        scratch = builder.plan({'editor_id': 'Scratch', 'name': 'x', 'race': 'NordRace'})
        assert any('npc_class is required' in e for e in scratch.errors)
        assert any('no voice_type' in w for w in scratch.warnings) and any('no AI packages' in w for w in scratch.warnings)
        edit = builder.plan({'clone_from': 'Sven', 'add_inventory': ['x'], 'inventory': []}, edit=True)
        assert any('clone_from cannot' in e for e in edit.errors)
        assert any('either inventory' in e for e in edit.errors)
        create = builder.plan({'editor_id': 'AddInv', 'name': 'x', 'clone_from': 'Sven', 'add_inventory': ['x']})
        assert any('only for edit_npc' in e for e in create.errors)

    def test_reference_errors(self, builder):
        p = plan(builder, race='NoSuchRace', factions=[{'faction': 'Nope', 'rank': 1}], spells=['Nope'],
                 outfit='NoOutfit', keywords=['NoKw'], inventory=[{'item': 'Nope', 'count': 2}])
        assert len([e for e in p.errors if 'no ' in e or 'not found' in e]) >= 5

    def test_placement_validation(self, builder):
        p = plan(builder, placement={'cell': 'Nowhere', 'x': 1, 'bogus': 2})
        text = ' | '.join(p.errors)
        assert 'unknown keys' in text and 'x, y and z' in text and 'no cell' in text
        ok = plan(builder, placement={'worldspace': 'Tamriel', 'grid_x': 4, 'grid_y': -11, 'x': 1, 'y': 2, 'z': 3})
        assert not ok.errors and ok.cell is not None
        assert any('non-persistent' in w for w in ok.warnings)

    def test_dialogue_errors(self, builder):
        p = plan(builder, dialogue=[
            {'kind': 'nonsense', 'lines': ['a'], 'pick': 'weird'},
            {'prompt': 'p'},
            {'prompt': 'p', 'lines': ['a'], 'answers': [['b']]},
            {'prompt': 'p', 'answers': 'x'},
            {'prompt': 'p', 'lines': ['a'], 'child_of': 7},
            {'prompt': 'p', 'lines': ['a'], 'ends_branch': True},
            {'prompt': 'p\u2603', 'lines': ['a'], 'color': 1}])
        text = ' | '.join(p.errors)
        for part in ('pick must', 'kind must', 'lines are required', 'either lines or answers', 'non-empty list',
                     'child_of', 'ends_branch', 'Windows-1252', 'unknown keys'):
            assert part in text, part
        bad_commentary = plan(builder, commentary_text={'nope': ['x']}, weather_lines=True)
        assert any('commentary_text' in e for e in bad_commentary.errors)

    def test_dialogue_options_are_kept(self, builder):
        p = plan(builder, dialogue=[
            {'prompt': 'Parent', 'lines': ['One.'], 'say_once': True},
            {'prompt': 'Child', 'answers': [['a'], ['b']], 'pick': 'in_order', 'child_of': 0, 'goodbye': True,
             'loop': True, 'ends_branch': True, 'blocking': True},
            {'kind': 'greeting', 'line': 'Hello.'}], combat_lines=True, travel_lines=True, ambient_lines=True)
        assert not p.errors, p.errors
        opts = p.topics[1][-1]
        assert opts['child_of'] == 0 and opts['goodbye'] and opts['ends_branch'] and opts['pick'] == 'in_order'
        assert len(p.topics) > 3

    def test_follower_cost_validation(self, builder):
        p = plan(builder, follower={'cost': -1}, dialogue=[{'prompt': 'Hi', 'lines': ['Hi.'],
                                                            'show_while_following': True}])
        assert any('cost' in e for e in p.errors)

    def test_conditions(self, builder):
        cond = [
            {'function': 'GetIsSex', 'sex': 'female'},
            {'function': 'GetIsSex', 'sex': 'robot'},
            {'function': 'GetInFaction'},
            {'function': 'GetActorValue', 'actor_value': 18, 'op': '>=', 'value': 10},
            {'function': 'GetStageDone', 'form': 'Nope'},
            {'function': 'GetInFaction', 'form': 'Sven', 'param2': 'x'},
            {'function': 'GetInFaction', 'form': 'Sven', 'value_global': 'NoGlobal'},
            {'function': 'GetInFaction', 'form': 'Sven', 'value_global': 'SpeechEasy', 'or': True},
            {'function': 'Bogus', 'form': 'Sven'}]
        p = plan(builder, dialogue=[{'prompt': 'p', 'lines': ['a'], 'conditions': cond}])
        text = ' | '.join(p.errors)
        assert 'GetIsSex needs sex' in text and 'param2 must be' in text and 'value_global' in text
        assert len(p.errors) >= 6
        entry = p.topics[0]
        assert len(entry[4]) == 3 and entry[4][0][1] < 0 and entry[4][1][1] == -18

    def test_scripts(self, builder):
        p = plan(builder, dialogue=[
            {'prompt': 'a', 'lines': ['x'], 'give_items': [{'item': 'Book1CheapHorkers', 'count': 3}, 'Nope']},
            {'prompt': 'b', 'lines': ['x'], 'properties': {'Thing': 'Sven', '1bad': 'Sven', 'Gone': 'Nope'}},
            {'prompt': 'c', 'lines': ['x'], 'code': 'Debug.Trace("hi")', 'properties': {'Thing': 'Sven'}}])
        text = ' | '.join(p.errors)
        assert 'bad property name' in text and 'properties need code' in text and 'give_items' in text
        script = p.topics[2][3]
        assert script[0] == 'Debug.Trace("hi")' and script[1][0][0] == 'Thing'

    def test_reference_id(self, builder):
        assert builder.reference_id('Skyrim.esm:013487') == 0x013487
        assert builder.reference_id('13487') == 0x013487
        for bad in ('nonsense text', 'Missing.esp:1'):
            with pytest.raises(ValueError):
                builder.reference_id(bad)

    def test_target_path_and_validate(self, builder, sandbox):
        assert builder.target_path({'editor_id': 'Abc'}).name == 'Abc.esp'
        assert builder.target_path({'plugin_name': 'X.esl'}).name == 'X.esl'
        with pytest.raises(ValueError):
            builder.target_path({'plugin_name': 'sub\\X.esp'})
        res = builder.validate({'editor_id': 'ValNpc', 'name': 'V', 'clone_from': 'Sven'})
        assert res['ok'] and not res['errors']


class TestWriteVariants:
    def test_places_npc_in_exterior_cell(self, sandbox):
        res = server.create_npc({
            'plugin_name': 'Placed.esp', 'editor_id': 'PlacedSven', 'name': 'Placed Sven', 'clone_from': 'Sven',
            'placement': {'worldspace': 'Tamriel', 'grid_x': 4, 'grid_y': -11, 'x': 100.0, 'y': 200.0, 'z': 0.0,
                          'rotation_z': 90, 'persistent': False}})
        assert 'error' not in res, res
        again = server.create_npc({
            'plugin_name': 'Placed2.esp', 'editor_id': 'PersistSven', 'name': 'Persist Sven', 'clone_from': 'Sven',
            'placement': {'worldspace': 'Tamriel', 'grid_x': 4, 'grid_y': -11, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                          'persistent': True}})
        assert 'error' not in again, again
        assert any(r.get('signature') == 'ACHR' or 'ACHR' in str(r) for r in again['records'])

    def test_stats_skills_and_inventory_write(self, sandbox):
        res = server.create_npc({
            'plugin_name': 'Stats.esp', 'editor_id': 'StatSven', 'name': 'Stat Sven', 'clone_from': 'Sven',
            'short_name': 'Stat', 'level': 12, 'health': 300, 'magicka': 10, 'stamina': 20, 'speed_multiplier': 110,
            'aggression': 'Frenzied', 'confidence': 'Brave', 'morality': 'Any Crime', 'assistance': 'Helps Allies',
            'energy': 30, 'mood': 2, 'calc_min_level': 1, 'calc_max_level': 20,
            'skills': {'archery': 70, 'smithing': 30}, 'inventory': [{'item': 'Book1CheapHorkers', 'count': 2}],
            'keywords': [], 'dialogue': [{'prompt': 'Gift', 'lines': ['Take it.'],
                                          'give_items': [{'item': 'Book1CheapHorkers', 'count': 1}]}]})
        assert 'error' not in res or 'ompil' in res['error'], res
        if 'error' not in res:
            info = server.get_npc('StatSven', plugins=['Skyrim.esm', str(sandbox / 'Stats.esp')])
            assert info['editor_id'] == 'StatSven'

    def test_edit_npc_stats_and_dialogue(self, sandbox):
        res = server.edit_npc('Sven', {'level': 20, 'health': 500, 'aggression': 'Aggressive',
                                       'skills': {'archery': 90}, 'add_inventory': ['Book1CheapHorkers'],
                                       'dialogue': [{'prompt': 'New talk', 'lines': ['Yes?']}]}, 'Patch.esp')
        assert 'error' not in res, res
        assert res['record'].startswith('override')
        again = server.edit_npc('Sven', {'level': 21}, 'Patch.esp')
        assert 'error' not in again and again['record'] == 'edited in place'


class TestFaceGenCopy:
    def test_copy_with_loose_files(self, sandbox, tmp_path_factory):
        data = tmp_path_factory.mktemp('loose')
        mesh = data / 'Meshes' / 'Actors' / 'Character' / 'FaceGenData' / 'FaceGeom' / 'Skyrim.esm' / '0001347F.nif'
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(face_nif())
        tint = data / 'Textures' / 'Actors' / 'Character' / 'FaceGenData' / 'FaceTint' / 'Skyrim.esm' / '0001347F.dds'
        tint.parent.mkdir(parents=True)
        tint.write_bytes(b'DDS fake')
        builder = server.npc_builder(None)
        builder.data_dir = data
        res = builder.write({'plugin_name': 'Face.esp', 'editor_id': 'FaceSven', 'name': 'Face Sven',
                             'clone_from': 'Sven'})
        copied = res['facegen']['copied']
        assert len(copied) == 2 and res['facegen']['note']
        new_mesh = sandbox / copied[0]
        assert b'Face.esp' in new_mesh.read_bytes() and b'0001347F' not in new_mesh.read_bytes()
        assert not any('FaceGen not copied' in w for w in res['warnings'])

    def test_missing_source_mesh_is_reported(self, sandbox, tmp_path_factory):
        builder = server.npc_builder(None)
        builder.data_dir = tmp_path_factory.mktemp('empty')
        res = builder.write({'plugin_name': 'NoFace.esp', 'editor_id': 'NoFaceSven', 'name': 'x',
                             'clone_from': 'Sven'})
        assert res['facegen']['copied'] == [] and any('FaceGen not copied' in w for w in res['warnings'])
        builder.data_dir = None
        assert builder.copy_facegen(builder.plan({'editor_id': 'Q', 'name': 'q', 'clone_from': 'Sven'}).clone,
                                    sandbox / 'Q.esp', 1)['note'] == 'no Data folder configured'

    def test_face_errors(self, sandbox):
        server.edit_npc('Sven', {'level': 5}, 'FacePatch.esp')
        for changes in ({'beard': 'Nope'}, {'nose_length': 9}):
            res = server.edit_npc_face('Sven', changes, 'FacePatch.esp')
            assert 'error' in res
        assert 'error' in server.paint_npc_face('Sven', 'FacePatch.esp', 'Mask', 'red')
        assert 'error' in server.paint_npc_face('Sven', 'Nope.esp', 'Mask', 'red')
        info = server.get_npc_face('Sven')
        assert info['npc'] == 'Sven' and 'face' in info
