"""Reshape saves and undo keep source files and every canvas box together."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import Project
from world import EVENT_KEYS, GROUPS, LAYOUTS, World, json_bytes, pack_words
from world_positions import WorldPositions
from world_reshape import SIDECAR, WorldReshaper
from worldmap import WorldMap


class FixtureWorld(World):
    def _pair(self, primary, secondary):
        return None, {'count': 1024, 'revision': 'fixture-artwork',
                      'metatiles': [{'id': index, 'valid': True} for index in range(1024)]}


class FixtureProject(Project):
    @property
    def world(self):
        if self._world is None:
            self._world = FixtureWorld(self.source)
        return self._world


class ReshapeFixture:
    def make_fixture(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/pokeemerald'
        self.write('rom.sha1', b'Fixture only\n')
        self.write(LAYOUTS, {'layouts': []})
        self.write(GROUPS, {'group_order': ['gMapGroup_Test'], 'gMapGroup_Test': []})
        self.write('data/event_scripts.s', b'@ Fixture scripts\n')
        self.add_map('RouteA', 8, 7)
        self.add_map('RouteB', 8, 7)
        self.add_map('House', 8, 7, 'MAP_TYPE_INDOOR')
        for name, other, direction in [('RouteA', 'RouteB', 'right'), ('RouteB', 'RouteA', 'left')]:
            path = f'data/maps/{name}/map.json'
            data = json.loads((self.source / path).read_bytes())
            data['connections'] = [{'map': 'MAP_' + other.upper(), 'direction': direction, 'offset': 0}]
            self.write(path, data)
        self.project = FixtureProject(self.root)
        self.service = WorldReshaper(self.project)
        self.positions = WorldPositions(self.root)

    def write(self, relative, value):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else json_bytes(value))

    def add_map(self, name, width, height, map_type='MAP_TYPE_ROUTE'):
        data = {'id': 'MAP_' + name.upper(), 'name': name, 'layout': 'LAYOUT_' + name.upper(),
                'map_type': map_type, 'region_map_section': 'MAPSEC_ROUTE_102',
                'connections': None, **{key: [] for key in EVENT_KEYS}}
        layout = {'id': data['layout'], 'name': name + '_Layout', 'width': width, 'height': height,
                  'primary_tileset': 'gTileset_General', 'secondary_tileset': 'gTileset_Petalburg',
                  'blockdata_filepath': f'data/layouts/{name}/map.bin',
                  'border_filepath': f'data/layouts/{name}/border.bin'}
        self.write(f'data/maps/{name}/map.json', data)
        self.write(f'data/maps/{name}/scripts.inc', (name + '_MapScripts::\n\t.byte 0\n').encode())
        self.write(layout['blockdata_filepath'], pack_words([0x3000 + index for index in range(width * height)]))
        self.write(layout['border_filepath'], pack_words([0x3000] * 4))
        layouts = json.loads((self.source / LAYOUTS).read_bytes())
        layouts['layouts'].append(layout)
        self.write(LAYOUTS, layouts)
        groups = json.loads((self.source / GROUPS).read_bytes())
        groups['gMapGroup_Test'].append(name)
        self.write(GROUPS, groups)

    def snapshot(self):
        return {path.relative_to(self.source).as_posix(): path.read_bytes()
                for path in self.source.rglob('*') if path.is_file()}

    def canvas(self):
        catalog = WorldMap(self.source).catalog()
        saved = self.positions.read(catalog)
        return {row['name']: saved['positions'].get(row['name'], {'x': row['x'], 'y': row['y']})
                for row in catalog['maps']}

    def position(self, updates):
        return self.positions.save({'revision': self.positions.read(self.project.world._maps())['revision'],
                                    'positions': updates}, self.project.world._maps())

    def body(self, kind='expand', **extra):
        revision = self.positions.read(self.project.world._maps())['revision']
        if kind == 'expand':
            return {'name': 'RouteA', 'revision': self.project.world.get_map('RouteA')['revision'],
                    'positions_revision': revision, 'left': 2, 'top': 1, 'bottom': 0, 'right': 0,
                    'fill_tile': 7, **extra}
        positions = self.canvas()
        return {'name': 'RouteB', 'positions_revision': revision,
                'maps': [{'name': name, **positions[name], 'revision': self.project.world.get_map(name)['revision']}
                         for name in ('RouteA', 'RouteB')], **extra}

    def ready(self, kind='expand', **extra):
        body = self.body(kind, **extra)
        return {**body, 'world_revision': self.service.preview(kind, body)['world_revision']}


class WorldReshapeTests(ReshapeFixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()

    def test_preview_does_not_write_source_or_canvas(self):
        before = self.snapshot()
        for kind in ('expand', 'merge'):
            result = self.service.preview(kind, self.body(kind))
            self.assertEqual(result['positions_revision'], self.body(kind)['positions_revision'])
            self.assertEqual(result['canvas_position']['name'], self.body(kind)['name'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.project.state.exists())

    def test_expand_keeps_every_existing_box_and_old_terrain_world_origin(self):
        self.position({'House': {'x': -250, 'y': 420}})
        before = self.canvas()
        result = self.service.commit('expand', self.ready())
        after = self.canvas()
        self.assertEqual(after['House'], before['House'])
        self.assertEqual(after['RouteB'], before['RouteB'])
        self.assertEqual(after['RouteA'], {'x': before['RouteA']['x'] - 2, 'y': before['RouteA']['y'] - 1})
        self.assertEqual(result['canvas_positions']['positions'], after)
        self.assertEqual(self.project.world.get_map('RouteA')['width'], 10)
        self.assertTrue((self.project.state / 'transactions' / result['transaction'] / SIDECAR).exists())

    def test_merge_anchors_retained_right_box_to_leftmost_position_and_freezes_other_boxes(self):
        self.position({'RouteA': {'x': -20, 'y': 25}, 'RouteB': {'x': -12, 'y': 25},
                       'House': {'x': 91, 'y': -3}})
        before = self.canvas()
        result = self.service.commit('merge', self.ready('merge'))
        after = self.canvas()
        self.assertEqual(set(after), {'RouteB', 'House'})
        self.assertEqual(after['RouteB'], before['RouteA'])
        self.assertEqual(after['House'], before['House'])
        self.assertEqual((result['width'], result['height']), (16, 7))

    def test_positions_revision_is_required_for_preview_and_commit(self):
        before = self.snapshot()
        for kind in ('expand', 'merge'):
            for revision in (None, 123, 'stale'):
                with self.subTest(kind=kind, revision=revision), self.assertRaisesRegex(ValueError, 'positions changed'):
                    self.service.preview(kind, self.body(kind, positions_revision=revision))
        body = self.ready()
        self.position({'House': {'x': 500, 'y': 20}})
        with self.assertRaisesRegex(ValueError, 'positions changed'):
            self.service.commit('expand', body)
        self.assertEqual(self.snapshot(), before)

    def test_merge_rejects_forged_stale_fractional_unknown_or_duplicate_positions(self):
        before = self.snapshot()
        for value in (1, True, 1.5, None, 100001):
            body = self.body('merge')
            body['maps'][0]['x'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.preview('merge', body)
        for name in ('Unknown', 'RouteB'):
            body = self.body('merge')
            body['maps'][0]['name'] = name
            if name == 'RouteB':
                body['maps'][0] = dict(body['maps'][1])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'exist and cannot be repeated'):
                self.service.preview('merge', body)
        self.assertEqual(self.snapshot(), before)

    def test_source_revision_checks_remain_enforced(self):
        body = self.ready()
        self.write('data/maps/RouteA/scripts.inc', b'Changed_MapScripts::\n\t.byte 0\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.commit('expand', body)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())

    def test_undo_restores_no_position_file_and_exact_source_bytes_then_redo_works(self):
        before = self.snapshot()
        result = self.service.commit('expand', self.ready())
        expanded = self.snapshot()
        positions_after = self.positions.path.read_bytes()
        restored = self.service.undo(result['transaction'])
        self.assertTrue(restored['positions_restored'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())
        redone = self.service.undo(restored['id'])
        self.assertTrue(redone['positions_restored'])
        self.assertEqual(self.snapshot(), expanded)
        self.assertEqual(self.positions.path.read_bytes(), positions_after)

    def test_undo_merge_restores_removed_map_and_original_position_bytes(self):
        self.position({'RouteA': {'x': -10, 'y': 30}, 'RouteB': {'x': -2, 'y': 30}})
        original = b'{"version":1,"positions":{"RouteA":{"x":-10,"y":30},"RouteB":{"x":-2,"y":30}}}\n'
        self.positions.path.write_bytes(original)
        before = self.snapshot()
        result = self.service.commit('merge', self.ready('merge'))
        self.service.undo(result['transaction'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), original)

    def test_undo_preserves_later_user_box_moves(self):
        before = self.snapshot()
        result = self.service.commit('expand', self.ready())
        self.position({'House': {'x': 99, 'y': 999}})
        latest = self.positions.path.read_bytes()
        undone = self.service.undo(result['transaction'])
        self.assertFalse(undone['positions_restored'])
        self.assertIn('Later', undone['positions_message'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), latest)

    def test_ordinary_source_undo_and_invalid_identifiers_keep_working(self):
        before = self.snapshot()
        saved = self.project.commit({'data/maps/RouteA/scripts.inc': b'Changed\n'}, 'Ordinary edit')
        restored = self.service.undo(saved['id'])
        self.assertNotIn('positions_restored', restored)
        self.assertEqual(self.snapshot(), before)
        for identifier in (None, '../outside', 'not-found', ''):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.service.undo(identifier)

    def test_metadata_staging_failure_leaves_everything_unchanged(self):
        before = self.snapshot()
        with patch('world_reshape._atomic_write', side_effect=OSError('metadata full')):
            with self.assertRaisesRegex(OSError, 'metadata full'):
                self.service.commit('expand', self.ready())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_sidecar_failure_rolls_back_source_without_creating_canvas_positions(self):
        before = self.snapshot()
        with patch.object(self.service, '_attach_sidecar', side_effect=OSError('sidecar full')):
            with self.assertRaisesRegex(OSError, 'sidecar full'):
                self.service.commit('expand', self.ready())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())
        self.assertEqual(self.project.transactions.list_transactions(), [])
        self.assertEqual(list(self.project.state.glob('reshape-pending-*')), [])

    def test_position_save_failure_rolls_back_source_and_original_canvas_bytes(self):
        self.position({'House': {'x': -7, 'y': -8}})
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        real_save = self.service.positions.save

        def save_then_fail(*args):
            real_save(*args)
            raise OSError('disk failed after position save')

        with patch.object(self.service.positions, 'save', side_effect=save_then_fail):
            with self.assertRaisesRegex(OSError, 'disk failed'):
                self.service.commit('merge', self.ready('merge'))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_position_save_failure_restores_absent_file(self):
        before = self.snapshot()
        with patch.object(self.service.positions, 'save', side_effect=OSError('positions full')):
            with self.assertRaisesRegex(OSError, 'positions full'):
                self.service.commit('expand', self.ready())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())

    def test_external_canvas_edit_during_source_commit_is_preserved_on_conflict(self):
        before = self.snapshot()
        real_commit = self.project.transactions.commit
        later = b'{"version":1,"positions":{"House":{"x":80,"y":90}}}\n'

        def commit_then_external_move(*args, **kwargs):
            result = real_commit(*args, **kwargs)
            self.positions.path.write_bytes(later)
            return result

        with patch.object(self.project.transactions, 'commit', side_effect=commit_then_external_move):
            with self.assertRaisesRegex(RuntimeError, 'current file was kept'):
                self.service.commit('expand', self.ready())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), later)

    def test_undo_position_failure_rolls_back_the_source_undo(self):
        result = self.service.commit('expand', self.ready())
        expanded, positions = self.snapshot(), self.positions.path.read_bytes()
        with patch.object(self.service, '_restore_positions', side_effect=OSError('undo positions full')):
            with self.assertRaisesRegex(OSError, 'undo positions full'):
                self.service.undo(result['transaction'])
        self.assertEqual(self.snapshot(), expanded)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        # Original edit remains undoable after the failed attempt.
        self.assertTrue(self.service.undo(result['transaction'])['positions_restored'])

    def test_corrupt_sidecar_blocks_undo_before_touching_source(self):
        result = self.service.commit('expand', self.ready())
        expanded = self.snapshot()
        sidecar = self.project.state / 'transactions' / result['transaction'] / SIDECAR
        for contents in (b'{', b'[]', b'{"version":1,"before":"invalid!","after":null}',
                         b'{"version":true,"before":null,"after":null}'):
            sidecar.write_bytes(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'backup is invalid'):
                self.service.undo(result['transaction'])
            self.assertEqual(self.snapshot(), expanded)

    def test_later_source_changes_still_block_undo_and_leave_positions_untouched(self):
        result = self.service.commit('expand', self.ready())
        self.write('data/layouts/RouteA/map.bin', b'changed later')
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed after this save'):
            self.service.undo(result['transaction'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)


if __name__ == '__main__':
    unittest.main()
