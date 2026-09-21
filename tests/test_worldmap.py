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

    def assert_source_geometry(self, catalog, source=None):
        """Every whole source map and every cardinal record stays accounted for."""
        source = source or self.source
        maps = {path.parent.name: json.loads(path.read_bytes())
                for path in (source / 'data/maps').glob('*/map.json')}
        layouts = {layout['id']: layout for layout in json.loads((source / 'data/layouts/layouts.json').read_bytes())['layouts']}
        rows = self.rows(catalog)
        self.assertEqual(len(catalog['maps']), len(maps))
        self.assertEqual(set(rows), set(maps))
        self.assertEqual(catalog['conflicts'], [])
        self.assertEqual(catalog['overlaps'], [])
        for collection in ('components', 'sections'):
            members = [name for group in catalog[collection] for name in group['names']]
            self.assertEqual(len(members), len(set(members)), collection)
            self.assertEqual(set(members), set(maps), collection)
        for name, row in rows.items():
            layout = layouts[maps[name]['layout']]
            self.assertEqual((row['width'], row['height']), (layout['width'], layout['height']), name)
            self.assertEqual(row['overlaps'], [])
            self.assertNotIn('projection', row)
        for index, a in enumerate(catalog['maps']):
            for b in catalog['maps'][index + 1:]:
                width = min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x'])
                height = min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y'])
                self.assertTrue(width <= 0 or height <= 0, (a['name'], b['name']))

        opposites = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
        def edge_key(a, b, direction, offset):
            return (a, b, direction, offset) if a <= b else (b, a, opposites[direction], -offset)

        transitions = {edge_key(link['a'], link['b'], link['direction'], link['offset']): link
                       for link in catalog['transitions']}
        self.assertEqual(len(transitions), len(catalog['transitions']))
        self.assertEqual(len({link['id'] for link in catalog['transitions']}), len(transitions))
        expected_transitions = set()
        ids = {data['id']: name for name, data in maps.items()}
        for name, data in maps.items():
            a = rows[name]
            for connection in data.get('connections') or []:
                direction, offset = connection['direction'], connection['offset']
                if direction not in opposites or type(offset) is not int or connection['map'] not in ids:
                    continue
                b = rows[ids[connection['map']]]
                key = edge_key(name, b['name'], direction, offset)
                if a['component'] != b['component']:
                    expected_transitions.add(key)
                    continue
                expected = {'up': (a['x'] + offset, a['y'] - b['height']),
                            'down': (a['x'] + offset, a['y'] + a['height']),
                            'left': (a['x'] - b['width'], a['y'] + offset),
                            'right': (a['x'] + a['width'], a['y'] + offset)}[direction]
                self.assertEqual((b['x'], b['y']), expected, key)
        self.assertEqual(set(transitions), expected_transitions)
        for link in transitions.values():
            a, b = rows[link['a']], rows[link['b']]
            direction, offset = link['direction'], link['offset']
            self.assertEqual(link['from_component'], a['component'])
            self.assertEqual(link['to_component'], b['component'])
            self.assertNotEqual(a['component'], b['component'])
            axis = 'height' if direction in {'left', 'right'} else 'width'
            start, end = max(0, offset), min(a[axis], offset + b[axis])
            self.assertEqual(link['span'], max(0, end - start))
            middle = (start + end) / 2
            source_point = {'left': (a['x'], a['y'] + middle),
                            'right': (a['x'] + a['width'], a['y'] + middle),
                            'up': (a['x'] + middle, a['y']),
                            'down': (a['x'] + middle, a['y'] + a['height'])}[direction]
            target_point = {'left': (b['x'] + b['width'], b['y'] + middle - offset),
                            'right': (b['x'], b['y'] + middle - offset),
                            'up': (b['x'] + middle - offset, b['y'] + b['height']),
                            'down': (b['x'] + middle - offset, b['y'])}[direction]
            self.assertEqual((link['source']['x'], link['source']['y']), source_point)
            self.assertEqual((link['target']['x'], link['target']['y']), target_point)

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
        # Original records are exposed exactly, without inventing a reciprocal
        # source link. Consumers can find incoming edges by scanning these rows.
        self.assertEqual(rows['Route']['connections'], [
            {'map': 'MAP_LITTLEROOTTOWN', 'name': 'LittlerootTown', 'direction': 'down', 'offset': 4}])
        self.assertEqual(rows['LittlerootTown']['connections'], [])

    def test_catalog_connections_include_only_known_integer_cardinal_source_records(self):
        self.add_map('East', 12, 12)
        self.links('LittlerootTown', [('East', 'right', -3), ('East', 'dive', 0),
                                    ('Missing', 'up', 0), ('East', 'left', '2'),
                                    ('East', 'down', True)])
        before = {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        self.assertEqual(rows['LittlerootTown']['connections'], [
            {'map': 'MAP_EAST', 'name': 'East', 'direction': 'right', 'offset': -3}])
        self.assertEqual(rows['East']['connections'], [])
        self.assertEqual((rows['East']['x'] - rows['LittlerootTown']['x'],
                          rows['East']['y'] - rows['LittlerootTown']['y']), (20, -3))
        self.assertEqual(catalog['conflicts'], [])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()})

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
        self.assertEqual(catalog['components'][0]['source_component'], 'LittlerootTown')
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

    def test_inconsistent_cycle_becomes_whole_sections_with_explicit_original_links(self):
        self.add_map('East', 20, 20)
        self.add_map('Southeast', 20, 20)
        self.links('LittlerootTown', [('East', 'right', 0), ('Southeast', 'down', 10)])
        self.links('East', [('Southeast', 'down', 0)])
        before = {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        first = self.model.catalog()
        second = self.model.catalog()
        self.assertEqual(first, second)
        self.assertGreater(len(first['components']), 1)
        self.assertTrue(first['transitions'])
        self.assert_source_geometry(first)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()})

    def test_overlapping_neighbors_are_separated_without_concealing_source_rectangles(self):
        self.add_map('North', 20, 20)
        self.add_map('NorthOther', 20, 20)
        self.links('LittlerootTown', [('North', 'up', 0), ('NorthOther', 'up', 10)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        self.assertNotEqual(rows['North']['component'], rows['NorthOther']['component'])
        self.assertTrue(catalog['transitions'])
        self.assert_source_geometry(catalog)

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

    def test_real_hoenn_sections_preserve_every_map_and_route103_join(self):
        catalog = WorldMap(SOURCE).catalog()
        rows = self.rows(catalog)
        expected = {p.parent.name for p in (SOURCE / 'data/maps').glob('*/map.json')}
        self.assertEqual(set(rows), expected)
        self.assertEqual(len(catalog['maps']), len(expected))
        main = [section for section in catalog['sections'] if section['kind'] == 'main']
        main_names = {name for section in main for name in section['names']}
        self.assertGreater(len(main), 1)
        self.assertIn('LittlerootTown', main_names)
        self.assertIn('EverGrandeCity', main_names)
        self.assertEqual(rows['Route101']['y'] + rows['Route101']['height'], rows['LittlerootTown']['y'])
        self.assertEqual(rows['Route101']['x'], rows['LittlerootTown']['x'])
        self.assertEqual(catalog['overlaps'], [])
        self.assertEqual(catalog['conflicts'], [])
        self.assertTrue(catalog['transitions'])
        self.assertEqual(rows['Route103']['component'], rows['Route110']['component'])
        self.assertEqual((rows['Route110']['x'], rows['Route110']['y']),
                         (rows['Route103']['x'] + 80, rows['Route103']['y'] - 60))
        self.assertIn('LittlerootTown_BrendansHouse_1F', rows)
        self.assertIn('FarawayIsland_Interior', rows)
        self.assertTrue(rows['Route104_Prototype']['archived'])
        self.assertEqual(rows['Route103']['connections'], [
            {'map': 'MAP_OLDALE_TOWN', 'name': 'OldaleTown', 'direction': 'down', 'offset': 0},
            {'map': 'MAP_ROUTE110', 'name': 'Route110', 'direction': 'right', 'offset': -60}])
        self.assertIn({'map': 'MAP_ROUTE103', 'name': 'Route103', 'direction': 'left', 'offset': 60},
                      rows['Route110']['connections'])
        self.assertIn({'map': 'MAP_ROUTE103', 'name': 'Route103', 'direction': 'up', 'offset': 0},
                      rows['OldaleTown']['connections'])
        self.assertEqual((rows['Route103']['width'], rows['Route103']['height']), (80, 22))
        self.assertFalse(any('projection' in row for row in rows.values()))

    def test_all_real_catalog_connections_match_source_without_synthetic_return_edges(self):
        catalog = WorldMap(SOURCE).catalog()
        rows = self.rows(catalog)
        names = {row['id']: name for name, row in rows.items()}
        for name, row in rows.items():
            source = json.loads((SOURCE / 'data/maps' / name / 'map.json').read_bytes())
            expected = [{'map': connection['map'], 'name': names[connection['map']],
                         'direction': connection['direction'], 'offset': connection['offset']}
                        for connection in source.get('connections') or []
                        if connection['direction'] in {'up', 'down', 'left', 'right'}
                        and connection['map'] in names and type(connection['offset']) is int]
            self.assertEqual(row['connections'], expected, name)

    def test_real_source_edges_are_exact_inside_sections_or_preserved_between_them(self):
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
        self.assert_source_geometry(catalog, SOURCE)
        self.assertEqual(catalog, WorldMap(SOURCE).catalog())
        self.assertEqual(before, {path: path.read_bytes() for path in source_paths})

    def test_inconsistent_cycle_preserves_bridge_and_complete_leaf_map(self):
        self.add_map('A', 20, 20)
        self.add_map('B', 20, 20)
        self.add_map('CTown', 20, 20, 'MAP_TYPE_TOWN')
        self.add_map('DetachedLeaf', 8, 8, 'MAP_TYPE_INDOOR')
        self.links('LittlerootTown', [('A', 'right', 0)])
        self.links('A', [('B', 'right', 0), ('CTown', 'down', 20)])
        self.links('B', [('CTown', 'down', 2), ('DetachedLeaf', 'up', 0)])
        catalog = self.model.catalog()
        rows = self.rows(catalog)
        self.assertGreater(len(catalog['components']), 1)
        self.assert_source_geometry(catalog)
        self.assertEqual(rows['DetachedLeaf']['component'], rows['B']['component'])
        self.assertEqual(rows['DetachedLeaf']['x'], rows['B']['x'])
        self.assertEqual(rows['DetachedLeaf']['y'] + 8, rows['B']['y'])
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
        self.assertGreaterEqual(len(rows), 518)
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
        self.assertEqual({section['kind'] for section in sections.values()}, {'main', 'town', 'route', 'dungeon', 'special'})
        main_parts = [section for section in sections.values() if section['kind'] == 'main']
        self.assertGreater(len(main_parts), 1)
        self.assertEqual(sum(len(section['names']) for section in main_parts), 49)
        left, top = min(part['bounds']['x'] for part in main_parts), min(part['bounds']['y'] for part in main_parts)
        right = max(part['bounds']['x'] + part['bounds']['width'] for part in main_parts)
        bottom = max(part['bounds']['y'] + part['bounds']['height'] for part in main_parts)
        self.assertEqual(catalog['initial_bounds'], {'x': left, 'y': top, 'width': right - left, 'height': bottom - top})
        self.assertGreater(sections['town']['bounds']['x'], right)
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
