"""Geometry tests for the continuous outdoor terrain canvas."""
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

    def test_excludes_interiors_and_underwater_but_includes_named_exteriors_and_new_outdoors(self):
        self.add_map('House', map_type='MAP_TYPE_INDOOR')
        self.add_map('IslandCave', map_type='MAP_TYPE_UNDERGROUND')
        self.add_map('Underwater_Route', map_type='MAP_TYPE_UNDERWATER')
        self.add_map('BirthIsland_Exterior', map_type='MAP_TYPE_INDOOR')
        self.add_map('MyNewTown', map_type='MAP_TYPE_TOWN')
        self.links('LittlerootTown', [('Underwater_Route', 'dive', 0)])
        catalog = self.model.catalog()
        self.assertEqual(set(self.rows(catalog)), {'LittlerootTown', 'BirthIsland_Exterior', 'MyNewTown'})
        self.assertEqual(len(catalog['components']), 3)
        self.assertEqual(catalog['components'][0]['label'], 'Hoenn')

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
        expected = {p.parent.name for p in (SOURCE / 'data/maps').glob('*/map.json')
                    if json.loads(p.read_bytes())['map_type'] in OUTDOOR_TYPES or p.parent.name in OUTDOOR_EXCEPTIONS}
        self.assertEqual(set(rows), expected)
        self.assertEqual(catalog['components'][0]['id'], 'LittlerootTown')
        self.assertIn('LittlerootTown', catalog['components'][0]['names'])
        self.assertIn('EverGrandeCity', catalog['components'][0]['names'])
        self.assertEqual(rows['Route101']['y'] + rows['Route101']['height'], rows['LittlerootTown']['y'])
        self.assertEqual(rows['Route101']['x'], rows['LittlerootTown']['x'])
        self.assertTrue(any({overlap['a'], overlap['b']} == {'Route116', 'VerdanturfTown'} for overlap in catalog['overlaps']))
        self.assertEqual(len(catalog['conflicts']), 3)
        self.assertNotIn('LittlerootTown_BrendansHouse_1F', rows)
        self.assertIn('FarawayIsland_Interior', rows)
        self.assertTrue(rows['Route104_Prototype']['archived'])


if __name__ == '__main__':
    unittest.main()
