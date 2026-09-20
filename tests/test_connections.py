from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from connections import Connections, OPPOSITE


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.model = Connections(self.source)
        self.maps = {}
        layouts = []
        for name, width, height in [('Alpha', 20, 20), ('Beta', 12, 12), ('Gamma', 8, 8), ('BetaMirror', 12, 12)]:
            data = {'name': name, 'id': 'MAP_' + name.upper(), 'layout': 'LAYOUT_' + name.upper(),
                    'map_type': 'MAP_TYPE_TOWN', 'region_map_section': 'MAPSEC_LITTLEROOT_TOWN',
                    'connections': None, 'warp_events': [], 'object_events': [{'x': 1, 'script': 'KeepMyStory'}],
                    'custom_note': 'preserve unknown fields'}
            if name == 'BetaMirror':
                data.pop('warp_events')
                data['shared_events_map'] = 'Beta'
            self.maps[name] = data
            self.write(name, data)
            layouts.append({'id': data['layout'], 'width': width, 'height': height})
        path = self.source / 'data/layouts/layouts.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'layouts': layouts}), encoding='utf-8')

    def write(self, name, data):
        path = self.source / f'data/maps/{name}/map.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def read(self, name):
        return json.loads((self.source / f'data/maps/{name}/map.json').read_bytes())

    def apply(self, plan):
        for relative, content in plan.items():
            (self.source / relative).write_bytes(content)

    def edge(self, **changes):
        return {'revision': self.model.catalog()['revision'], 'a': 'Alpha', 'b': 'Beta',
                'direction': 'up', 'offset': 2, 'action': 'connect', **changes}

    def warp(self, a_map='Alpha', b_map='Gamma', **changes):
        return {'revision': self.model.catalog()['revision'],
                'a': {'map': a_map, 'x': 2, 'y': 3, 'elevation': 0, 'index': None},
                'b': {'map': b_map, 'x': 4, 'y': 5, 'elevation': 3, 'index': None}, **changes}

    def test_real_catalog_covers_all_maps_and_resolves_known_reciprocals(self):
        catalog = Connections(SOURCE).catalog()
        self.assertEqual(len(catalog['maps']), len(list((SOURCE / 'data/maps').glob('*/map.json'))))
        maps = {row['name']: row for row in catalog['maps']}
        town = maps['LittlerootTown']
        route = next(link for link in town['connections'] if link['dest_name'] == 'Route101')
        self.assertTrue(route['reciprocal'])
        self.assertEqual(route['direction'], 'up')
        self.assertEqual(route['overlap'], [0, 20])
        self.assertEqual(maps['ContestHallBeauty']['events_owner'], 'ContestHall')
        self.assertIn('ContestHall', maps['ContestHallBeauty']['events_shared_with'])
        house = maps['LittlerootTown_BrendansHouse_1F']
        self.assertEqual(house['warps'][0]['dest_name'], 'LittlerootTown')
        self.assertEqual(house['warps'][0]['dest_index'], 1)

    def test_all_edge_directions_use_opposite_direction_and_negative_offset(self):
        for direction, reverse in OPPOSITE.items():
            with self.subTest(direction=direction):
                plan = self.model.plan_edge(self.edge(direction=direction))
                a = json.loads(plan['data/maps/Alpha/map.json'])
                b = json.loads(plan['data/maps/Beta/map.json'])
                self.assertEqual(a['connections'], [{'map': 'MAP_BETA', 'direction': direction, 'offset': 2}])
                self.assertEqual(b['connections'], [{'map': 'MAP_ALPHA', 'direction': reverse, 'offset': -2}])
                self.assertEqual(a['object_events'], self.maps['Alpha']['object_events'])
                self.assertEqual(a['custom_note'], self.maps['Alpha']['custom_note'])
        self.assertIsNone(self.read('Alpha')['connections'])

    def test_nonoverlap_is_rejected_and_disjoint_neighbors_are_preserved(self):
        with self.assertRaisesRegex(ValueError, 'no shared edge'):
            self.model.plan_edge(self.edge(offset=20))
        with self.assertRaisesRegex(ValueError, 'no shared edge'):
            self.model.plan_edge(self.edge(offset=-12))
        self.apply(self.model.plan_edge(self.edge(offset=0)))
        plan = self.model.plan_edge(self.edge(b='Gamma', offset=12))
        a = json.loads(plan['data/maps/Alpha/map.json'])
        self.assertEqual(len(a['connections']), 2)
        self.assertEqual(a['connections'][0], self.read('Alpha')['connections'][0])
        self.assertNotIn('data/maps/Beta/map.json', plan)
        with self.assertRaisesRegex(ValueError, 'Unlink it first'):
            self.model.plan_edge(self.edge(b='Gamma', offset=11, replace=True))

    def test_conflicts_on_destination_edge_are_also_rejected(self):
        self.apply(self.model.plan_edge(self.edge(a='Beta', b='Gamma', direction='down', offset=0)))
        with self.assertRaisesRegex(ValueError, 'Beta already connects down'):
            self.model.plan_edge(self.edge())

    def test_existing_pair_moves_only_with_explicit_replace_and_does_not_duplicate(self):
        self.apply(self.model.plan_edge(self.edge(offset=0)))
        self.assertEqual(self.model.plan_edge(self.edge(offset=0)), {})
        with self.assertRaisesRegex(ValueError, 'Confirm replacement'):
            self.model.plan_edge(self.edge(offset=2))
        plan = self.model.plan_edge(self.edge(offset=2, replace=True))
        self.assertEqual(json.loads(plan['data/maps/Alpha/map.json'])['connections'][0]['offset'], 2)
        self.assertEqual(len(json.loads(plan['data/maps/Beta/map.json'])['connections']), 1)

    def test_disconnect_removes_only_that_pair_and_both_reciprocal_records(self):
        self.apply(self.model.plan_edge(self.edge(offset=0)))
        self.apply(self.model.plan_edge(self.edge(b='Gamma', offset=12)))
        plan = self.model.plan_edge(self.edge(action='disconnect'))
        a = json.loads(plan['data/maps/Alpha/map.json'])
        b = json.loads(plan['data/maps/Beta/map.json'])
        self.assertEqual(a['connections'], [{'map': 'MAP_GAMMA', 'direction': 'up', 'offset': 12}])
        self.assertEqual(b['connections'], [])
        self.assertNotIn('data/maps/Gamma/map.json', plan)
        self.apply(plan)
        self.assertEqual(self.model.plan_edge(self.edge(action='disconnect')), {})

    def test_stale_revision_and_invalid_inputs_do_not_produce_plans(self):
        body = self.edge()
        changed = self.read('Gamma')
        changed['custom_note'] = 'external edit'
        self.write('Gamma', changed)
        with self.assertRaisesRegex(ValueError, 'Maps changed'):
            self.model.plan_edge(body)
        for changes in ({'direction': 'dive'}, {'offset': True}, {'offset': 1.5}, {'a': '../Alpha'}, {'b': 'Alpha'}, {'action': 'erase'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.model.plan_edge(self.edge(**changes))

    def test_small_map_depth_is_rejected_for_engine_border_copy(self):
        path = self.source / 'data/layouts/layouts.json'
        layouts = json.loads(path.read_bytes())
        layouts['layouts'][1]['height'] = 6
        path.write_text(json.dumps(layouts), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'at least 7 tiles deep'):
            self.model.plan_edge(self.edge())
        self.assertTrue(self.model.plan_edge(self.edge(direction='right', offset=0)))

    def test_new_warp_pair_uses_zero_based_indices_and_atomic_source_plan(self):
        plan = self.model.plan_warp(self.warp())
        self.assertEqual(set(plan), {'data/maps/Alpha/map.json', 'data/maps/Gamma/map.json'})
        a, b = (json.loads(plan[f'data/maps/{name}/map.json']) for name in ('Alpha', 'Gamma'))
        self.assertEqual(a['warp_events'], [{'x': 2, 'y': 3, 'elevation': 0, 'dest_map': 'MAP_GAMMA', 'dest_warp_id': 0}])
        self.assertEqual(b['warp_events'][0]['dest_warp_id'], 0)
        self.assertEqual(b['warp_events'][0]['dest_map'], 'MAP_ALPHA')
        self.assertEqual(a['object_events'], self.maps['Alpha']['object_events'])
        self.assertEqual(self.read('Alpha')['warp_events'], [])
        self.apply(plan)
        catalog = {row['name']: row for row in self.model.catalog()['maps']}
        self.assertTrue(catalog['Alpha']['warps'][0]['reciprocal'])
        self.assertTrue(catalog['Gamma']['warps'][0]['reciprocal'])
        body = self.warp()
        body['a']['index'] = body['b']['index'] = 0
        self.assertEqual(self.model.plan_warp(body), {})

    def test_existing_warp_rewire_requires_confirmation_and_preserves_other_door_cells(self):
        a = self.read('Alpha')
        a['warp_events'] = [
            {'x': 2, 'y': 3, 'elevation': 0, 'dest_map': 'MAP_BETA', 'dest_warp_id': 0, 'custom_warp': 42},
            {'x': 3, 'y': 3, 'elevation': 0, 'dest_map': 'MAP_BETA', 'dest_warp_id': 0},
            {'x': 7, 'y': 8, 'elevation': 3, 'dest_map': 'MAP_DYNAMIC', 'dest_warp_id': 'WARP_ID_DYNAMIC'},
        ]
        self.write('Alpha', a)
        body = self.warp()
        body['a']['index'] = 0
        with self.assertRaisesRegex(ValueError, 'Confirm replacement'):
            self.model.plan_warp(body)
        plan = self.model.plan_warp({**body, 'confirm_replace': True})
        updated = json.loads(plan['data/maps/Alpha/map.json'])['warp_events']
        self.assertEqual(updated[1:], a['warp_events'][1:])
        self.assertEqual(updated[0]['custom_warp'], 42)
        self.assertEqual(updated[0]['dest_map'], 'MAP_GAMMA')
        self.assertNotIn('data/maps/Beta/map.json', plan)

    def test_same_map_warp_pair_appends_two_distinct_slots(self):
        plan = self.model.plan_warp(self.warp(b_map='Alpha'))
        self.assertEqual(len(plan), 1)
        warps = json.loads(plan['data/maps/Alpha/map.json'])['warp_events']
        self.assertEqual([w['dest_warp_id'] for w in warps], [1, 0])
        self.apply(plan)
        body = self.warp(b_map='Alpha')
        body['a']['index'] = body['b']['index'] = 0
        with self.assertRaisesRegex(ValueError, 'same actual warp'):
            self.model.plan_warp({**body, 'confirm_replace': True})

    def test_shared_warp_edits_require_confirmation_and_write_owner_only(self):
        body = self.warp(a_map='BetaMirror')
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.model.plan_warp(body)
        plan = self.model.plan_warp({**body, 'confirm_shared': True})
        self.assertEqual(set(plan), {'data/maps/Beta/map.json', 'data/maps/Gamma/map.json'})
        self.assertEqual(json.loads(plan['data/maps/Gamma/map.json'])['warp_events'][0]['dest_map'], 'MAP_BETAMIRROR')
        self.assertNotIn('warp_events', self.read('BetaMirror'))

    def test_shared_owners_cannot_target_same_actual_slot(self):
        b = self.read('Beta')
        b['warp_events'] = [{'x': 1, 'y': 1, 'elevation': 0, 'dest_map': 'MAP_DYNAMIC', 'dest_warp_id': 'WARP_ID_DYNAMIC'}]
        self.write('Beta', b)
        body = self.warp(a_map='Beta', b_map='BetaMirror', confirm_shared=True, confirm_replace=True)
        body['a']['index'] = body['b']['index'] = 0
        with self.assertRaisesRegex(ValueError, 'same actual warp'):
            self.model.plan_warp(body)

    def test_out_of_bounds_duplicate_tiles_and_invalid_indices_are_rejected(self):
        for change in ({'x': -1}, {'x': 20}, {'elevation': 16}, {'index': 0}, {'index': True}):
            body = self.warp()
            body['a'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.model.plan_warp(body)
        self.apply(self.model.plan_warp(self.warp()))
        with self.assertRaisesRegex(ValueError, 'already has warp'):
            self.model.plan_warp(self.warp())

    def test_same_owner_endpoint_coordinate_swap_validates_final_state(self):
        self.apply(self.model.plan_warp(self.warp(b_map='Alpha')))
        body = self.warp(b_map='Alpha')
        body['a'].update(index=0, x=4, y=5, elevation=3)
        body['b'].update(index=1, x=2, y=3, elevation=0)
        plan = self.model.plan_warp(body)
        warps = json.loads(plan['data/maps/Alpha/map.json'])['warp_events']
        self.assertEqual([(w['x'], w['y']) for w in warps], [(4, 5), (2, 3)])


if __name__ == '__main__':
    unittest.main()
