"""Source map growth keeps old cell words, actors, warps, links and undo intact."""
import json
from pathlib import Path
import tempfile
import unittest

from connections import Connections
from route_expand import RouteExpander
from world import World, EVENT_KEYS, GROUPS, LAYOUTS, json_bytes, pack_words, words
from workspace_edits import SourceTransactions


class FixtureWorld(World):
    def _pair(self, primary, secondary):
        return None, {'count': 1024, 'revision': 'fixture-artwork',
                      'metatiles': [{'id': index, 'valid': index != 511} for index in range(1024)]}


class RouteExpandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.write(LAYOUTS, {'layouts': []})
        self.write(GROUPS, {'group_order': ['gMapGroup_Test'], 'gMapGroup_Test': []})
        self.write('data/event_scripts.s', b'@ Original includes\n')
        self.add_map('Route', 16, 14)
        self.add_map('West', 8, 16)
        self.add_map('East', 8, 14)
        self.add_map('North', 18, 7)
        self.add_map('South', 18, 7)
        self.add_map('House', 3, 3)
        self.links('Route', [('West', 'left', -1), ('East', 'right', 0),
                             ('North', 'up', -1), ('South', 'down', 0)])
        self.links('West', [('Route', 'right', 1)])
        self.links('East', [('Route', 'left', 0)])
        self.links('North', [('Route', 'down', 1)])
        self.links('South', [('Route', 'up', 0)])
        self.world = FixtureWorld(self.source)
        self.expander = RouteExpander(self.world)

    def write(self, relative, value):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else json_bytes(value))

    def read(self, relative):
        return json.loads((self.source / relative).read_bytes())

    def add_map(self, name, width, height):
        data = {'id': 'MAP_' + name.upper(), 'name': name, 'layout': 'LAYOUT_' + name.upper(),
                'map_type': 'MAP_TYPE_ROUTE', 'region_map_section': 'MAPSEC_ROUTE_102',
                'connections': None, **{key: [] for key in EVENT_KEYS}}
        layout = {'id': data['layout'], 'name': name + '_Layout', 'width': width, 'height': height,
                  'primary_tileset': 'gTileset_General', 'secondary_tileset': 'gTileset_Petalburg',
                  'blockdata_filepath': f'data/layouts/{name}/map.bin',
                  'border_filepath': f'data/layouts/{name}/border.bin'}
        self.write(f'data/maps/{name}/map.json', data)
        self.write(f'data/maps/{name}/scripts.inc', (name + '_MapScripts::\n\t.byte 0\n').encode())
        self.write(layout['blockdata_filepath'], pack_words([0xA000 + index for index in range(width * height)]))
        self.write(layout['border_filepath'], pack_words([0xD001, 0xE002, 0xF003, 0xC004]))
        layouts = self.read(LAYOUTS)
        layouts['layouts'].append(layout)
        self.write(LAYOUTS, layouts)
        groups = self.read(GROUPS)
        groups['gMapGroup_Test'].append(name)
        self.write(GROUPS, groups)

    def update_map(self, name, **changes):
        path = f'data/maps/{name}/map.json'
        data = self.read(path)
        data.update(changes)
        self.write(path, data)

    def links(self, name, values):
        self.update_map(name, connections=[{'map': 'MAP_' + target.upper(), 'direction': direction, 'offset': offset}
                                           for target, direction, offset in values])

    def body(self, **extra):
        name = extra.get('name', 'Route')
        return {'name': name, 'revision': self.world.get_map(name)['revision'],
                'top': 2, 'bottom': 3, 'left': 4, 'right': 5, 'fill_tile': 7, **extra}

    def plan(self, **extra):
        body = self.body(**extra)
        details = self.expander.preview(body)
        return self.expander.plan({**body, 'world_revision': details['world_revision']})

    def planned_map(self, plan, name):
        path = f'data/maps/{name}/map.json'
        return json.loads(plan.get(path, (self.source / path).read_bytes()))

    def snapshot(self):
        return {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}

    def test_grow_all_sides_preserves_every_old_bit_and_fills_only_new_cells(self):
        before = self.snapshot()
        old = self.world.get_map('Route')
        body = self.body()
        preview = self.expander.preview(body)
        plan = self.expander.plan({**body, 'world_revision': preview['world_revision']})
        self.assertEqual((preview['width'], preview['height']), (25, 19))
        self.assertEqual((preview['origin_x'], preview['origin_y']), (-4, -2))
        self.assertEqual(preview['added_cells'], 251)
        self.assertEqual(preview['fill_cell'], 0x3007)
        self.assertEqual(preview['affected_maps'], ['East', 'North', 'Route', 'South', 'West'])
        new = words(plan[old['layout']['blockdata_filepath']])
        for y in range(19):
            for x in range(25):
                expected = old['cells'][(y - 2) * 16 + x - 4] if 4 <= x < 20 and 2 <= y < 16 else 0x3007
                self.assertEqual(new[y * 25 + x], expected)
        layouts = json.loads(plan[LAYOUTS])
        layout = next(item for item in layouts['layouts'] if item['id'] == 'LAYOUT_ROUTE')
        self.assertEqual(layout, {**old['layout'], 'width': 25, 'height': 19})
        self.assertNotIn(GROUPS, plan)
        self.assertNotIn(old['layout']['border_filepath'], plan)
        self.assertEqual(self.snapshot(), before)

    def test_all_event_kinds_shift_without_reindexing_or_touching_incoming_references(self):
        event_data = {
            'object_events': [{'x': 8, 'y': 3, 'local_id': 'LOCALID_TRAINER', 'script': 'Trainer', 'flag': 'FLAG_TRAINER'},
                              {'x': -5, 'y': -8, 'type': 'clone', 'target_map': 'MAP_HOUSE', 'target_local_id': 1}],
            'warp_events': [{'x': 1, 'y': 1, 'dest_map': 'MAP_HOUSE', 'dest_warp_id': 0},
                            {'x': 15, 'y': 13, 'warp_id': 'WARP_ROUTE', 'dest_map': 'MAP_ROUTE', 'dest_warp_id': 0}],
            'coord_events': [{'x': 2, 'y': 3, 'script': 'Trigger', 'var': 'VAR_STORY', 'var_value': 4}],
            'bg_events': [{'x': 0, 'y': 0, 'type': 'sign', 'script': 'Sign'}]
        }
        self.update_map('Route', **event_data)
        self.update_map('House', warp_events=[{'x': 1, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': '1'}],
                        object_events=[{'x': 1, 'y': 1, 'type': 'clone', 'target_map': 'MAP_ROUTE', 'target_local_id': 1}])
        plan = self.plan()
        saved = self.planned_map(plan, 'Route')
        for key in EVENT_KEYS:
            expected = [{**event, 'x': event['x'] + 4, 'y': event['y'] + 2} for event in event_data[key]]
            self.assertEqual(saved[key], expected)
        self.assertNotIn('data/maps/House/map.json', plan)
        self.assertNotIn('data/maps/Route/scripts.inc', plan)

    def test_offsets_transform_both_ends_and_reciprocity_survives(self):
        plan = self.plan()
        expected = [('West', 'left', 1), ('East', 'right', 2), ('North', 'up', 3), ('South', 'down', 4)]
        links = self.planned_map(plan, 'Route')['connections']
        for target, direction, offset in expected:
            self.assertIn({'map': 'MAP_' + target.upper(), 'direction': direction, 'offset': offset}, links)
            self.assertEqual(self.planned_map(plan, target)['connections'][0]['offset'], -offset)
        for path, data in plan.items():
            self.write(path, data)
        catalog = Connections(self.source).catalog()
        self.assertTrue(all(link['reciprocal'] and link['overlap'] for row in catalog['maps'] for link in row['connections']))

    def test_bottom_right_growth_keeps_event_coordinates_and_cardinal_offsets(self):
        self.update_map('Route', object_events=[{'x': 2, 'y': 3}])
        body = self.body(top=0, left=0)
        details = self.expander.preview(body)
        plan = self.expander.plan({**body, 'world_revision': details['world_revision']})
        self.assertEqual(details['affected_maps'], ['Route'])
        self.assertEqual(set(plan), {LAYOUTS, 'data/layouts/Route/map.bin'})
        self.assertEqual(self.planned_map(plan, 'Route'), self.read('data/maps/Route/map.json'))

    def test_one_way_links_and_unrelated_dive_links_stay_unchanged(self):
        self.links('East', [])
        self.links('House', [('West', 'dive', 0)])
        plan = self.plan()
        self.assertNotIn('data/maps/East/map.json', plan)
        self.assertNotIn('data/maps/House/map.json', plan)
        self.assertTrue(any(link['map'] == 'MAP_EAST' for link in self.planned_map(plan, 'Route')['connections']))

    def test_invalid_sides_fill_and_dimensions_are_rejected_without_writes(self):
        before = self.snapshot()
        for edit in ({'top': -1}, {'right': True}, {'left': 1.5}, {'bottom': '2'},
                     {'top': 0, 'bottom': 0, 'left': 0, 'right': 0}, {'fill_tile': 1024},
                     {'fill_tile': 511}, {'fill_tile': True}, {'right': 250},
                     {'top': 100, 'right': 100}, {'revision': 'old'}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.expander.preview(self.body(**edit))
        with self.assertRaises(ValueError):
            self.expander.preview([])
        self.assertEqual(self.snapshot(), before)

    def test_shared_layout_and_shared_events_are_rejected(self):
        self.update_map('House', layout='LAYOUT_ROUTE')
        with self.assertRaisesRegex(ValueError, 'shares a layout'):
            self.expander.preview(self.body())
        self.update_map('House', layout='LAYOUT_HOUSE', shared_events_map='Route')
        with self.assertRaisesRegex(ValueError, 'shares a layout'):
            self.expander.preview(self.body())
        self.update_map('House', shared_events_map=None)
        self.update_map('Route', shared_events_map='House')
        with self.assertRaisesRegex(ValueError, 'shares a layout'):
            self.expander.preview(self.body())

    def test_distinct_layouts_cannot_share_the_file_being_expanded(self):
        layouts = self.read(LAYOUTS)
        house = next(item for item in layouts['layouts'] if item['id'] == 'LAYOUT_HOUSE')
        house['blockdata_filepath'] = 'data/layouts/Route/map.bin'
        self.write(LAYOUTS, layouts)
        with self.assertRaisesRegex(ValueError, 'shares its terrain file'):
            self.expander.preview(self.body())

    def test_omitted_sides_default_to_zero(self):
        body = {'name': 'Route', 'revision': self.world.get_map('Route')['revision'], 'right': 1, 'fill_tile': 0}
        result = self.expander.preview(body)
        self.assertEqual(result['additions'], {'top': 0, 'bottom': 0, 'left': 0, 'right': 1})
        self.assertEqual((result['width'], result['height']), (17, 14))

    def test_preview_required_and_distant_map_script_or_artwork_change_invalidates_it(self):
        body = self.body()
        details = self.expander.preview(body)
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.expander.plan(body)
        self.update_map('House', warp_events=[{'x': 1, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': 0}])
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.expander.plan({**body, 'world_revision': details['world_revision']})
        details = self.expander.preview(body)
        self.write('data/maps/House/scripts.inc', b'House_MapScripts::\n\t.byte 0\n@ changed\n')
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.expander.preview({**body, 'world_revision': details['world_revision']})
        details = self.expander.preview(body)
        pair = self.world._pair
        self.world._pair = lambda *args: (None, {**pair(*args)[1], 'revision': 'changed-artwork'})
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.expander.plan({**body, 'world_revision': details['world_revision']})

    def test_camera_padding_and_unresolved_or_nonoverlapping_links_rejected(self):
        for links in ([('East', 'dive', 0)], [('Missing', 'right', 0)], [('East', 'right', 200)]):
            self.links('Route', links)
            with self.subTest(links=links), self.assertRaisesRegex(ValueError, 'links|shared edge'):
                self.expander.preview(self.body())
        self.links('Route', [('House', 'right', 0)])
        with self.assertRaisesRegex(ValueError, 'camera edge padding'):
            self.expander.preview(self.body())
        self.links('Route', [])
        self.links('House', [('Route', 'emerge', 0)])
        with self.assertRaisesRegex(ValueError, 'dive/emerge'):
            self.expander.preview(self.body())

    def test_growth_cannot_make_two_walking_destinations_overlap(self):
        self.add_map('Other', 16, 14)
        self.add_map('Hub', 32, 7)
        self.links('Route', [('Hub', 'up', 0)])
        self.links('Hub', [('Route', 'down', 0), ('Other', 'down', 16)])
        self.links('West', [])
        self.links('East', [])
        self.links('North', [])
        self.links('South', [])
        with self.assertRaisesRegex(ValueError, 'overlap.*walking connections'):
            self.expander.preview(self.body(top=0, bottom=0, left=0, right=1))

    def test_invalid_event_coordinate_does_not_mutate_source(self):
        for event in ({'x': '2', 'y': 1}, {'x': True, 'y': 1}, {'x': 32767, 'y': 1}):
            self.update_map('Route', object_events=[event])
            before = self.snapshot()
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'integer'):
                self.expander.preview(self.body())
            self.assertEqual(self.snapshot(), before)

    def test_transaction_commit_and_undo_restore_every_original_byte(self):
        before = self.snapshot()
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        transactions = SourceTransactions(self.source, Path(state.name))
        transaction = transactions.commit(self.plan(), 'Expand Route')
        saved = FixtureWorld(self.source).get_map('Route')
        self.assertEqual((saved['width'], saved['height']), (25, 19))
        transactions.undo(transaction['id'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(transactions.created(), set())


if __name__ == '__main__':
    unittest.main()
