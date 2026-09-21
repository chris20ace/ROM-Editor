"""Connection synchronization is geometric, scoped, revisioned and undoable."""
import json
import unittest
from unittest.mock import patch

from test_world_reshape import ReshapeFixture
from world_link_sync import WorldLinkSync
from world_reshape import WorldReshaper


class WorldLinkSyncTests(ReshapeFixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()
        self.sync = WorldLinkSync(self.project)
        self.position({'RouteA': {'x': 0, 'y': 0}, 'RouteB': {'x': 8, 'y': 2},
                       'House': {'x': 100, 'y': 100}})

    def body(self, **extra):
        data = self.sync.catalog()
        return {'revision': data['revision'], 'positions_revision': data['positions_revision'], **extra}

    def data(self, name):
        return json.loads((self.source / f'data/maps/{name}/map.json').read_bytes())

    def links(self, name, links):
        data = self.data(name)
        data['connections'] = links
        self.write(f'data/maps/{name}/map.json', data)

    def test_catalog_uses_saved_positions_and_real_endpoints(self):
        result = self.sync.catalog()
        self.assertEqual(result['default_names'], ['RouteA', 'RouteB'])
        rows = {row['name']: row for row in result['maps']}
        self.assertEqual((rows['RouteB']['x'], rows['RouteB']['y']), (8, 2))
        self.assertFalse(result['edges'][0]['aligned'])
        self.assertTrue(result['edges'][0]['reciprocal'])
        self.assertIn('audit', result)

    def test_preview_changes_only_offset_and_never_writes(self):
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        result = self.sync.preview(self.body())
        self.assertEqual(result['counts'], {'added': 2, 'removed': 2, 'unchanged': 0, 'affected_maps': 2})
        self.assertTrue(result['can_apply'])
        self.assertEqual({(e['from_name'], e['offset']) for e in result['added']}, {('RouteA', 2), ('RouteB', -2)})
        self.assertTrue(all(e['aligned'] and e['reciprocal'] for e in result['added']))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_commit_changes_source_and_preserves_every_coordinate_and_event(self):
        data = self.data('RouteA')
        data['warp_events'] = [{'x': 1, 'y': 2, 'dest_map': 'MAP_HOUSE', 'dest_warp_id': 0, 'elevation': 0}]
        self.write('data/maps/RouteA/map.json', data)
        before = self.canvas()
        result = self.sync.commit(self.body())
        self.assertTrue(result['transaction'])
        self.assertEqual(self.data('RouteA')['connections'], [{'map': 'MAP_ROUTEB', 'direction': 'right', 'offset': 2}])
        self.assertEqual(self.data('RouteB')['connections'], [{'map': 'MAP_ROUTEA', 'direction': 'left', 'offset': -2}])
        self.assertEqual(self.data('RouteA')['warp_events'], data['warp_events'])
        self.assertEqual(self.canvas(), before)
        self.assertEqual(result['canvas_positions']['positions'], before)

    def test_disconnected_selected_edges_are_removed(self):
        self.position({'RouteB': {'x': 20, 'y': 20}})
        result = self.sync.commit(self.body())
        self.assertEqual(result['counts']['removed'], 2)
        self.assertEqual(result['counts']['added'], 0)
        self.assertEqual(self.data('RouteA')['connections'], [])
        self.assertEqual(self.data('RouteB')['connections'], [])

    def test_corner_contact_is_not_a_walking_connection(self):
        self.position({'RouteB': {'x': 8, 'y': 7}})
        result = self.sync.preview(self.body())
        self.assertEqual(result['added'], [])
        self.assertEqual(len(result['removed']), 2)

    def test_vertical_contact_with_negative_tangent_offset(self):
        self.position({'RouteB': {'x': -3, 'y': 7}})
        result = self.sync.commit(self.body())
        self.assertEqual(self.data('RouteA')['connections'], [{'map': 'MAP_ROUTEB', 'direction': 'down', 'offset': -3}])
        self.assertEqual(self.data('RouteB')['connections'], [{'map': 'MAP_ROUTEA', 'direction': 'up', 'offset': 3}])
        self.assertEqual(result['errors'], [])

    def test_overlapping_boxes_block_preview_and_commit(self):
        self.position({'RouteB': {'x': 7, 'y': 2}})
        before = self.snapshot()
        result = self.sync.preview(self.body())
        self.assertFalse(result['can_apply'])
        self.assertIn('overlaps', result['errors'][0])
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            self.sync.commit(self.body())
        self.assertEqual(self.snapshot(), before)

    def test_default_scope_does_not_infer_house_edges(self):
        self.position({'House': {'x': 0, 'y': 7}})
        self.sync.commit(self.body())
        self.assertIsNone(self.data('House')['connections'])
        self.assertEqual(len(self.data('RouteA')['connections']), 1)

    def test_explicit_scope_can_connect_an_interior_and_preserves_outside_links(self):
        self.position({'House': {'x': 0, 'y': 7}})
        result = self.sync.commit(self.body(names=['House', 'RouteA']))
        self.assertEqual(self.data('RouteA')['connections'], [
            {'map': 'MAP_ROUTEB', 'direction': 'right', 'offset': 0},
            {'map': 'MAP_HOUSE', 'direction': 'down', 'offset': 0}])
        self.assertEqual(self.data('House')['connections'], [{'map': 'MAP_ROUTEA', 'direction': 'up', 'offset': 0}])
        self.assertEqual(self.data('RouteB')['connections'], [{'map': 'MAP_ROUTEA', 'direction': 'left', 'offset': 0}])
        self.assertTrue(any('outside' in warning for warning in result['warnings']))

    def test_outside_link_conflicting_with_new_contact_is_blocked(self):
        self.links('RouteA', [{'map': 'MAP_HOUSE', 'direction': 'right', 'offset': 0}])
        result = self.sync.preview(self.body())
        self.assertFalse(result['can_apply'])
        self.assertTrue(any('conflicting' in error for error in result['errors']))

    def test_separate_nonoverlapping_spans_on_same_edge_are_allowed(self):
        self.add_map('RouteC', 8, 7)
        self.add_map('Wide', 16, 7)
        self.position({'Wide': {'x': 0, 'y': 30}, 'RouteA': {'x': 0, 'y': 23},
                       'RouteC': {'x': 8, 'y': 23}, 'RouteB': {'x': 100, 'y': 100}})
        result = self.sync.commit(self.body())
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.data('Wide')['connections'], [
            {'map': 'MAP_ROUTEA', 'direction': 'up', 'offset': 0},
            {'map': 'MAP_ROUTEC', 'direction': 'up', 'offset': 8}])

    def test_dive_emerge_and_unrelated_map_data_are_unchanged(self):
        old = self.data('RouteA')['connections']
        special = {'map': 'MAP_HOUSE', 'direction': 'dive', 'offset': 0}
        self.links('RouteA', old + [special])
        self.links('House', [{'map': 'MAP_ROUTEA', 'direction': 'emerge', 'offset': 0}])
        house = (self.source / 'data/maps/House/map.json').read_bytes()
        self.sync.commit(self.body())
        self.assertIn(special, self.data('RouteA')['connections'])
        self.assertEqual((self.source / 'data/maps/House/map.json').read_bytes(), house)

    def test_narrow_horizontal_maps_rejected_for_camera_padding(self):
        self.add_map('Narrow', 7, 7)
        self.position({'Narrow': {'x': -7, 'y': 0}})
        result = self.sync.preview(self.body())
        self.assertTrue(any('8 tiles of width' in error for error in result['errors']))

    def test_small_vertical_maps_rejected_for_camera_padding(self):
        self.add_map('Short', 8, 6)
        self.position({'Short': {'x': 0, 'y': -6}})
        result = self.sync.preview(self.body())
        self.assertTrue(any('7 tiles of height' in error for error in result['errors']))

    def test_stale_map_or_positions_rejected_without_writes(self):
        body = self.body()
        self.position({'House': {'x': 20, 'y': 500}})
        with self.assertRaisesRegex(ValueError, 'positions changed'):
            self.sync.commit(body)
        body = self.body()
        data = self.data('House')
        data['region_map_section'] = 'MAPSEC_ROUTE_103'
        self.write('data/maps/House/map.json', data)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Maps changed'):
            self.sync.commit(body)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_selection_is_rejected(self):
        for names in (None, 'RouteA', [], ['RouteA'], ['RouteA', 'RouteA'], ['RouteA', 'Missing'], [1, 'RouteA']):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'two different'):
                self.sync.preview(self.body(names=names))

    def test_idempotent_sync_does_not_create_another_transaction(self):
        self.sync.commit(self.body())
        before = self.snapshot()
        history = self.project.transactions.list_transactions()
        preview = self.sync.preview(self.body())
        self.assertFalse(preview['can_apply'])
        result = self.sync.commit(self.body())
        self.assertIsNone(result['transaction'])
        self.assertEqual(self.project.transactions.list_transactions(), history)
        self.assertEqual(self.snapshot(), before)

    def test_saved_history_restores_exact_source_and_canvas_bytes(self):
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        result = self.sync.commit(self.body())
        undone = WorldReshaper(self.project).undo(result['transaction'])
        self.assertTrue(undone['positions_restored'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_freezes_unpositioned_boxes_when_changed_graph_would_repack_them(self):
        self.position({'House': None})
        before = self.canvas()
        self.sync.commit(self.body())
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.positions.read(self.project.world._maps())['positions'], before)

    def test_scoped_invalid_old_offset_is_replaced_from_geometry(self):
        for value in (None, False, {'invalid': 1}):
            with self.subTest(value=value):
                self.links('RouteA', [{'map': 'MAP_ROUTEB', 'direction': 'right', 'offset': value}])
                result = self.sync.preview(self.body())
                self.assertEqual(result['errors'], [])
                self.assertTrue(result['can_apply'])
                self.assertEqual(next(e for e in result['added'] if e['from_name'] == 'RouteA')['offset'], 2)

    def test_undo_keeps_later_user_box_moves(self):
        before = self.snapshot()
        result = self.sync.commit(self.body())
        self.position({'House': {'x': 500, 'y': -20}})
        positions = self.positions.path.read_bytes()
        undone = WorldReshaper(self.project).undo(result['transaction'])
        self.assertFalse(undone['positions_restored'])
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.snapshot(), before)

    def test_position_save_failure_rolls_back_source(self):
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        with patch.object(self.sync.positions, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.sync.commit(self.body())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_sidecar_failure_rolls_back_source(self):
        before, positions = self.snapshot(), self.positions.path.read_bytes()
        with patch.object(WorldReshaper, '_attach_sidecar', side_effect=OSError('sidecar full')):
            with self.assertRaisesRegex(OSError, 'sidecar full'):
                self.sync.commit(self.body())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_new_source_changes_during_commit_preparation_are_rejected(self):
        body = self.body()
        real_prepare = self.sync._prepare
        def changed(*args):
            result = real_prepare(*args)
            data = self.data('House')
            data['region_map_section'] = 'MAPSEC_ROUTE_103'
            self.write('data/maps/House/map.json', data)
            return result
        with patch.object(self.sync, '_prepare', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Maps changed while saving'):
                self.sync.commit(body)
        self.assertEqual(self.data('RouteA')['connections'][0]['offset'], 0)


if __name__ == '__main__':
    unittest.main()
