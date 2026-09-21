"""Geometry, area organization and coverage tests for the all-map terrain canvas."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmap import WorldMap, OUTDOOR_TYPES, OUTDOOR_EXCEPTIONS, COMPONENT_GAP


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'


class WorldMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.layout_path = self.source / 'data/layouts/layouts.json'
        self.layout_path.parent.mkdir(parents=True)
        self.layout_path.write_text('{"layouts": []}', encoding='utf-8')
        self.model = WorldMap(self.source)
        self.add_map('LittlerootTown', 20, 20, 'MAP_TYPE_TOWN')

    def add_map(self, name, width=12, height=12, map_type='MAP_TYPE_ROUTE', connections=None):
        data = {'name': name, 'id': 'MAP_' + name.upper(), 'layout': 'LAYOUT_' + name.upper(),
                'map_type': map_type, 'region_map_section': 'MAPSEC_LITTLEROOT_TOWN',
                'connections': connections, 'warp_events': []}
        path = self.source / f'data/maps/{name}/map.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')
        layouts = json.loads(self.layout_path.read_bytes())
        layouts['layouts'].append({'id': data['layout'], 'width': width, 'height': height})
        self.layout_path.write_text(json.dumps(layouts), encoding='utf-8')

    def links(self, name, links):
        path = self.source / f'data/maps/{name}/map.json'
        data = json.loads(path.read_bytes())
        data['connections'] = [{'map': 'MAP_' + other.upper(), 'direction': direction, 'offset': offset}
                               for other, direction, offset in links]
        path.write_text(json.dumps(data), encoding='utf-8')

    def rows(self, catalog=None):
        return {row['name']: row for row in (catalog or self.model.catalog())['maps']}

    def test_cardinal_neighbors_use_exact_dimensions_and_offsets_without_gaps(self):
        self.add_map('North', 10, 8)
        self.add_map('South', 12, 9)
        self.add_map('West', 7, 11)
        self.add_map('East', 9, 6)
        self.links('LittlerootTown', [('North', 'up', 3), ('South', 'down', -4),
                                    ('West', 'left', 5), ('East', 'right', -2)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        origin = rows['LittlerootTown']
        self.assertEqual((rows['North']['x'] - origin['x'], rows['North']['y'] - origin['y']), (3, -8))
        self.assertEqual((rows['South']['x'] - origin['x'], rows['South']['y'] - origin['y']), (-4, 20))
        self.assertEqual((rows['West']['x'] - origin['x'], rows['West']['y'] - origin['y']), (-7, 5))
        self.assertEqual((rows['East']['x'] - origin['x'], rows['East']['y'] - origin['y']), (20, -2))
        self.assertEqual(len(catalog['components']), 1)
        self.assertEqual(catalog['conflicts'], [])
        self.assertEqual(catalog['overlaps'], [])
        self.assertEqual(catalog['bounds'], catalog['initial_bounds'])

    def test_one_way_incoming_edges_still_determine_contiguous_positions(self):
        self.add_map('Route', 12, 9)
        self.links('Route', [('LittlerootTown', 'down', 4)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        self.assertEqual(len(catalog['components']), 1)
        self.assertEqual(rows['Route']['y'] + 9, rows['LittlerootTown']['y'])
        self.assertEqual(rows['Route']['x'] + 4, rows['LittlerootTown']['x'])

    def test_includes_interiors_underwater_named_exteriors_and_new_outdoors(self):
        self.add_map('House', map_type='MAP_TYPE_INDOOR')
        self.add_map('IslandCave', map_type='MAP_TYPE_UNDERGROUND')
        self.add_map('Underwater_Route', map_type='MAP_TYPE_UNDERWATER')
        self.add_map('BirthIsland_Exterior', map_type='MAP_TYPE_INDOOR')
        self.add_map('MyNewTown', map_type='MAP_TYPE_TOWN')
        self.links('LittlerootTown', [('Underwater_Route', 'dive', 0)])
        catalog = self.model.catalog()
        self.assertEqual(set(self.rows(catalog)), {'LittlerootTown', 'House', 'IslandCave', 'Underwater_Route', 'BirthIsland_Exterior', 'MyNewTown'})
        self.assertEqual(len(catalog['components']), 6)
        self.assertEqual(catalog['components'][0]['label'], 'Hoenn')
        rows = self.rows(catalog)
        self.assertFalse(rows['House']['is_outdoor'])
        self.assertFalse(rows['Underwater_Route']['is_outdoor'])
        self.assertTrue(rows['BirthIsland_Exterior']['is_outdoor'])
        self.assertTrue(rows['MyNewTown']['is_outdoor'])

    def test_disconnected_components_are_packed_separately_and_archive_is_last(self):
        self.add_map('Detached', 8, 8)
        self.add_map('DetachedRoute', 10, 8)
        self.add_map('UnusedRoute_Prototype', 12, 12)
        self.links('Detached', [('DetachedRoute', 'right', 0)])
        catalog = self.model.catalog()
        self.assertEqual(catalog['components'][0]['id'], 'LittlerootTown')
        self.assertTrue(catalog['components'][-1]['archived'])
        self.assertIn('Archive', catalog['components'][-1]['label'])
        rows = self.rows(catalog)
        self.assertEqual(rows['DetachedRoute']['x'], rows['Detached']['x'] + 8)
        self.assertEqual(rows['DetachedRoute']['y'], rows['Detached']['y'])
        self.assertGreaterEqual(rows['Detached']['y'], 20 + COMPONENT_GAP)
        bounds = [component['bounds'] for component in catalog['components']]
        for i, a in enumerate(bounds):
            for b in bounds[i + 1:]:
                overlap_w = min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x'])
                overlap_h = min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y'])
                self.assertTrue(overlap_w <= 0 or overlap_h <= 0)
        self.assertNotEqual(catalog['bounds'], catalog['initial_bounds'])

    def test_connected_prototype_is_not_hidden_in_detached_archive(self):
        self.add_map('Route104_Prototype')
        self.links('LittlerootTown', [('Route104_Prototype', 'right', 0)])
        catalog = self.model.catalog()
        self.assertEqual(len(catalog['components']), 1)
        self.assertFalse(self.rows(catalog)['Route104_Prototype']['archived'])

    def test_inconsistent_cycle_is_reported_instead_of_silently_moving_maps(self):
        self.add_map('East', 20, 20)
        self.add_map('Southeast', 20, 20)
        self.links('LittlerootTown', [('East', 'right', 0), ('Southeast', 'down', 10)])
        self.links('East', [('Southeast', 'down', 0)])
        first = self.model.catalog()
        second = self.model.catalog()
        self.assertEqual(first, second)
        self.assertEqual(len(first['conflicts']), 1)
        seam = first['conflicts'][0]
        self.assertEqual((seam['a'], seam['b']), ('East', 'Southeast'))
        self.assertEqual(seam['delta'], {'x': -10, 'y': 0})
        self.assertTrue(any('seams disagree' in warning for warning in first['warnings']))

    def test_overlap_reports_exact_rectangle_and_both_map_names_for_layer_selection(self):
        self.add_map('North', 20, 20)
        self.add_map('NorthOther', 20, 20)
        self.links('LittlerootTown', [('North', 'up', 0), ('NorthOther', 'up', 10)])
        catalog = self.model.catalog()
        self.assertEqual(catalog['overlaps'], [{'a': 'North', 'b': 'NorthOther', 'x': 10, 'y': 0, 'width': 10, 'height': 20}])
        rows = self.rows(catalog)
        self.assertEqual(rows['North']['overlaps'], ['NorthOther'])
        self.assertEqual(rows['NorthOther']['overlaps'], ['North'])
        self.assertTrue(any('bring its terrain forward' in warning for warning in catalog['warnings']))

    def test_catalog_is_read_only_and_revision_changes_after_layout_or_map_changes(self):
        before = {str(p): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        first = self.model.catalog()
        after = {str(p): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        layouts = json.loads(self.layout_path.read_bytes())
        layouts['layouts'][0]['width'] = 21
        self.layout_path.write_text(json.dumps(layouts), encoding='utf-8')
        resized = self.model.catalog()
        self.assertNotEqual(first['revision'], resized['revision'])
        self.assertEqual(resized['maps'][0]['width'], 21)
        self.add_map('NewOcean', 15, 15, 'MAP_TYPE_OCEAN_ROUTE')
        self.assertNotEqual(resized['revision'], self.model.catalog()['revision'])

    def test_real_hoenn_geometry_exposes_source_seams_and_preserves_continent(self):
        catalog = WorldMap(SOURCE).catalog()
        rows = self.rows(catalog)
        expected = {p.parent.name for p in (SOURCE / 'data/maps').glob('*/map.json')}
        self.assertEqual(set(rows), expected)
        self.assertEqual(len(catalog['maps']), len(expected))
        self.assertEqual(catalog['components'][0]['id'], 'LittlerootTown')
        self.assertIn('LittlerootTown', catalog['components'][0]['names'])
        self.assertIn('EverGrandeCity', catalog['components'][0]['names'])
        self.assertEqual(rows['Route101']['y'] + rows['Route101']['height'], rows['LittlerootTown']['y'])
        self.assertEqual(rows['Route101']['x'], rows['LittlerootTown']['x'])
        self.assertEqual(catalog['overlaps'], [])
        self.assertEqual(len(catalog['conflicts']), 1)
        self.assertEqual({catalog['conflicts'][0]['a'], catalog['conflicts'][0]['b']}, {'Route103', 'Route110'})
        self.assertIn('LittlerootTown_BrendansHouse_1F', rows)
        self.assertIn('FarawayIsland_Interior', rows)
        self.assertTrue(rows['Route104_Prototype']['archived'])

    def test_real_town_coastlines_and_routes_use_source_offsets_and_keep_only_one_display_break(self):
        source_paths = [SOURCE / 'data/maps' / name / 'map.json' for name in
                        ('DewfordTown', 'Route107', 'FallarborTown', 'Route114', 'VerdanturfTown', 'Route116', 'Route103', 'Route110')]
        before = {path: path.read_bytes() for path in source_paths}
        catalog = WorldMap(SOURCE).catalog()
        rows = self.rows(catalog)
        # These three joins previously inherited a two-cell error from the first
        # BFS path, tearing the coastline and drawing Route116 over Verdanturf.
        for left, right in (('DewfordTown', 'Route107'), ('Route114', 'FallarborTown')):
            self.assertEqual(rows[right]['x'], rows[left]['x'] + rows[left]['width'])
            self.assertEqual(rows[right]['y'], rows[left]['y'])
        self.assertEqual(rows['VerdanturfTown']['y'], rows['Route116']['y'] + rows['Route116']['height'])
        self.assertEqual(rows['VerdanturfTown']['x'], rows['Route116']['x'] + 80)
        # Every source connection remains independently checkable; the one
        # incompatible cycle closure must stay visible in the catalog.
        reported = {frozenset((row['a'], row['b'])) for row in catalog['conflicts']}
        ids = {row['id']: row for row in rows.values()}
        for name in catalog['components'][0]['names']:
            data = json.loads((SOURCE / 'data/maps' / name / 'map.json').read_bytes())
            a = rows[name]
            for connection in data.get('connections') or []:
                direction = connection['direction']
                if direction not in {'up', 'down', 'left', 'right'}:
                    continue
                b = ids[connection['map']]
                offset = connection['offset']
                expected = {'up': (a['x'] + offset, a['y'] - b['height']),
                            'down': (a['x'] + offset, a['y'] + a['height']),
                            'left': (a['x'] - b['width'], a['y'] + offset),
                            'right': (a['x'] + a['width'], a['y'] + offset)}[direction]
                actual = b['x'], b['y']
                self.assertEqual(actual != expected, frozenset((name, b['name'])) in reported)
        self.assertEqual(len(reported), 1)
        self.assertEqual(before, {path: path.read_bytes() for path in source_paths})

    def test_inconsistent_cycle_prefers_route_display_break_and_keeps_bridge_attached(self):
        self.add_map('A', 20, 20)
        self.add_map('B', 20, 20)
        self.add_map('CTown', 20, 20, 'MAP_TYPE_TOWN')
        self.add_map('DetachedLeaf', 8, 8, 'MAP_TYPE_INDOOR')
        self.links('LittlerootTown', [('A', 'right', 0)])
        self.links('A', [('B', 'right', 0), ('CTown', 'down', 20)])
        self.links('B', [('CTown', 'down', 2), ('DetachedLeaf', 'up', 0)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        self.assertEqual(len(catalog['conflicts']), 1)
        self.assertEqual({catalog['conflicts'][0]['a'], catalog['conflicts'][0]['b']}, {'A', 'B'})
        self.assertEqual(rows['CTown']['x'], rows['A']['x'] + 20)
        self.assertEqual(rows['CTown']['x'], rows['B']['x'] + 2)
        self.assertEqual(rows['CTown']['y'], rows['B']['y'] + 20)
        self.assertEqual(rows['DetachedLeaf']['x'], rows['B']['x'])
        self.assertEqual(rows['DetachedLeaf']['y'] + 8, rows['B']['y'])
        self.assertEqual(len(catalog['components']), 1)
        self.assertEqual(catalog, self.model.catalog())

    def test_area_clusters_keep_homes_and_floors_with_owners_in_natural_order(self):
        self.add_map('LittlerootTown_House_10F', 8, 8, 'MAP_TYPE_INDOOR')
        self.add_map('LittlerootTown_House_2F', 8, 8, 'MAP_TYPE_INDOOR')
        self.add_map('LittlerootTown_House_1F', 8, 8, 'MAP_TYPE_INDOOR')
        self.add_map('MyNewTown', 20, 20, 'MAP_TYPE_TOWN')
        self.add_map('MyNewTown_House1', 8, 8, 'MAP_TYPE_INDOOR')
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        group = next(group for group in catalog['groups'] if group['area_id'] == 'LittlerootTown')
        self.assertEqual(group['names'], ['LittlerootTown_House_1F', 'LittlerootTown_House_2F', 'LittlerootTown_House_10F'])
        self.assertEqual(group['main_names'], ['LittlerootTown'])
        self.assertEqual(group['kind'], 'town')
        self.assertEqual(rows['LittlerootTown_House_1F']['role'], 'homes')
        self.assertEqual(rows['LittlerootTown_House_1F']['area_name'], 'Littleroot Town')
        self.assertEqual(rows['MyNewTown_House1']['area_id'], 'MyNewTown')
        self.assertEqual(rows['MyNewTown_House1']['area_kind'], 'town')
        self.assertNotEqual(rows['MyNewTown_House1']['group'], group['id'])
        # Each category is a display shelf, not another path in the game.
        self.assertEqual([section['id'] for section in catalog['sections']], ['main', 'town'])
        town_section = catalog['sections'][1]['bounds']
        self.assertGreaterEqual(town_section['x'], catalog['initial_bounds']['width'] + COMPONENT_GAP)

    def test_cross_area_cardinal_component_stays_whole_inside_one_cluster(self):
        self.add_map('OldaleTown_House1', 8, 8, 'MAP_TYPE_INDOOR')
        self.add_map('LittlerootTown_House1', 8, 8, 'MAP_TYPE_INDOOR')
        self.links('LittlerootTown_House1', [('OldaleTown_House1', 'right', 2)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        a, b = rows['LittlerootTown_House1'], rows['OldaleTown_House1']
        self.assertEqual((b['x'] - a['x'], b['y'] - a['y']), (8, 2))
        self.assertEqual(a['component'], b['component'])
        self.assertEqual(a['group'], b['group'])
        self.assertNotEqual(a['area_id'], b['area_id'])
        group = next(group for group in catalog['groups'] if group['id'] == a['group'])
        self.assertEqual(set(group['area_ids']), {'LittlerootTown', 'OldaleTown'})

    def test_real_all_map_categories_cover_secret_bases_floors_underwater_and_shared_rooms(self):
        catalog = WorldMap(SOURCE).catalog()
        rows = self.rows(catalog)
        expected = {path.parent.name for path in (SOURCE / 'data/maps').glob('*/map.json')}
        self.assertEqual(len(rows), len(expected))
        self.assertTrue(rows)
        self.assertEqual(set(rows), expected)
        self.assertEqual({row['area_kind'] for row in rows.values()}, {'town', 'route', 'dungeon', 'special'})
        for name in ('Underwater_Route124', 'SecretBase_RedCave1', 'VictoryRoad_B1F', 'ContestHallBeauty', 'BattlePyramidSquare01'):
            self.assertIn(name, rows)
            self.assertFalse(rows[name]['is_outdoor'])
            self.assertTrue(rows[name]['area_id'])
            self.assertTrue(rows[name]['role_label'])
            self.assertTrue(rows[name]['display_name'])
        self.assertEqual(rows['Underwater_Route124']['area_id'], 'Route124')
        self.assertEqual(rows['Underwater_Route124']['area_kind'], 'route')
        self.assertEqual(rows['SecretBase_RedCave1']['area_id'], 'SecretBases')
        self.assertEqual(rows['VictoryRoad_B1F']['area_kind'], 'dungeon')
        self.assertEqual(rows['ContestHallBeauty']['area_id'], 'ContestHalls')
        self.assertEqual(rows['BattlePyramidSquare01']['area_id'], 'BattleFrontier')
        section_names = [name for section in catalog['sections'] for name in section['names']]
        self.assertEqual(len(section_names), len(set(section_names)))
        self.assertEqual(set(section_names), expected)

    def test_real_shelves_and_detached_components_do_not_overlap_and_fit_landscape(self):
        catalog = WorldMap(SOURCE).catalog()
        self.assertGreater(catalog['bounds']['width'], catalog['bounds']['height'])
        sections = {section['id']: section for section in catalog['sections']}
        self.assertEqual(set(sections), {'main'} | {group['kind'] for group in catalog['groups']})
        # A split adds a map and a combination removes one. Derive connected
        # coverage from the current source edges instead of Emerald's old count.
        source_maps = {path.parent.name: json.loads(path.read_bytes())
                       for path in (SOURCE / 'data/maps').glob('*/map.json')}
        ids = {data['id']: name for name, data in source_maps.items()}
        neighbors = {name: set() for name in source_maps}
        for name, data in source_maps.items():
            for link in data.get('connections') or []:
                target = ids.get(link.get('map'))
                if target and link.get('direction') in {'up', 'down', 'left', 'right'} and type(link.get('offset')) is int:
                    neighbors[name].add(target)
                    neighbors[target].add(name)
        seed = 'LittlerootTown' if 'LittlerootTown' in source_maps else sections['main']['names'][0]
        expected, pending = set(), [seed]
        while pending:
            name = pending.pop()
            if name not in expected:
                expected.add(name)
                pending.extend(neighbors[name] - expected)
        self.assertEqual(set(sections['main']['names']), expected)
        self.assertEqual(len(sections['main']['names']), len(expected))
        rows = self.rows(catalog)
        bounds = {'x': min(rows[name]['x'] for name in expected), 'y': min(rows[name]['y'] for name in expected)}
        bounds['width'] = max(rows[name]['x'] + rows[name]['width'] for name in expected) - bounds['x']
        bounds['height'] = max(rows[name]['y'] + rows[name]['height'] for name in expected) - bounds['y']
        self.assertEqual(catalog['initial_bounds'], bounds)
        self.assertEqual((bounds['x'], bounds['y']), (0, 0))
        if 'town' in sections:
            self.assertGreater(sections['town']['bounds']['x'], sections['main']['bounds']['x'])
            self.assertEqual(sections['town']['bounds']['y'], 0)
        for collection in (catalog['components'], catalog['sections'], catalog['groups']):
            for index, left in enumerate(collection):
                a = left['bounds']
                for right in collection[index + 1:]:
                    b = right['bounds']
                    w = min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x'])
                    h = min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y'])
                    self.assertTrue(w <= 0 or h <= 0, (left['id'], right['id']))


if __name__ == '__main__':
    unittest.main()
