"""Lossless map splitting, connection transforms and incoming event references."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from connections import Connections
from route_split import ENCOUNTERS, REMATCHES, RouteSplitter
from world import World, GROUPS, LAYOUTS, json_bytes, pack_words, words
from workspace_edits import SourceTransactions


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'


class FixtureWorld(World):
    def _pair(self, primary, secondary):
        atlas = Image.new('RGBA', (256, 1024))
        draw = ImageDraw.Draw(atlas)
        for index in range(1024):
            x, y = (index % 16) * 16, (index // 16) * 16
            draw.rectangle((x, y, x + 15, y + 15), fill=(index * 3 % 256, index * 2 % 256, index % 256, 255))
        return atlas, {'count': 1024, 'metatiles': [], 'revision': 'fixture-tiles'}


class RouteSplitTests(unittest.TestCase):
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
        self.add_map('House', 3, 3)
        self.links('Route', [('West', 'left', -1), ('East', 'right', 0), ('North', 'up', -1)])
        self.links('West', [('Route', 'right', 1)])
        self.links('East', [('Route', 'left', 0)])
        self.links('North', [('Route', 'down', 1)])
        self.world = FixtureWorld(self.source)
        self.splitter = RouteSplitter(self.world)

    def write(self, path, value):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else json_bytes(value))

    def read(self, path):
        return json.loads((self.source / path).read_bytes())

    def add_map(self, name, width, height):
        constant = name.upper()
        data = {'id': 'MAP_' + constant, 'name': name, 'layout': 'LAYOUT_' + constant,
                'map_type': 'MAP_TYPE_ROUTE', 'region_map_section': 'MAPSEC_ROUTE_102',
                'connections': None, 'object_events': [], 'warp_events': [], 'coord_events': [], 'bg_events': []}
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
        return {'name': 'Route', 'revision': self.world.get_map('Route')['revision'], 'axis': 'vertical', 'cut': 8, **extra}

    def plan(self, **extra):
        body = self.body(**extra)
        preview = self.splitter.preview(body)
        return self.splitter.plan({**body, 'new_name': preview['new_name'], 'world_revision': preview['world_revision']})

    def planned_map(self, plan, name):
        return json.loads(plan.get(f'data/maps/{name}/map.json', (self.source / f'data/maps/{name}/map.json').read_bytes()
                                   if (self.source / f'data/maps/{name}/map.json').exists() else b'{}'))

    def apply(self, plan):
        for path, data in plan.items():
            self.write(path, data)

    def test_vertical_split_preserves_every_cell_bit_border_and_old_registrations(self):
        original = self.world.get_map('Route')
        before = {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        plan = self.plan()
        first = words(plan['data/layouts/Route/map.bin'])
        second = words(plan['data/layouts/RoutePart2/map.bin'])
        restored = [cell for y in range(14) for cell in first[y * 8:y * 8 + 8] + second[y * 8:y * 8 + 8]]
        self.assertEqual(restored, original['cells'])
        self.assertEqual(words(plan['data/layouts/RoutePart2/border.bin']), original['border'])
        self.assertNotIn('data/layouts/Route/border.bin', plan)
        groups = json.loads(plan[GROUPS])
        self.assertEqual(groups['gMapGroup_Test'], self.read(GROUPS)['gMapGroup_Test'])
        self.assertEqual(groups['gMapGroup_CustomWorld'], ['RoutePart2'])
        self.assertEqual(self.planned_map(plan, 'Route')['id'], original['id'])
        self.assertEqual(self.planned_map(plan, 'RoutePart2')['shared_scripts_map'], 'Route')
        self.assertEqual({path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}, before)

    def test_vertical_split_rebases_both_sides_of_edges_and_preserves_reciprocity(self):
        plan = self.plan()
        route = self.planned_map(plan, 'Route')['connections']
        new = self.planned_map(plan, 'RoutePart2')['connections']
        self.assertIn({'map': 'MAP_WEST', 'offset': -1, 'direction': 'left'}, route)
        self.assertNotIn('MAP_EAST', [link['map'] for link in route])
        self.assertIn({'map': 'MAP_EAST', 'offset': 0, 'direction': 'right'}, new)
        self.assertIn({'map': 'MAP_NORTH', 'offset': -1, 'direction': 'up'}, route)
        self.assertIn({'map': 'MAP_NORTH', 'offset': -9, 'direction': 'up'}, new)
        self.assertEqual(self.planned_map(plan, 'North')['connections'], [
            {'map': 'MAP_ROUTE', 'direction': 'down', 'offset': 1},
            {'map': 'MAP_ROUTE_PART2', 'direction': 'down', 'offset': 9}])
        self.assertEqual(self.planned_map(plan, 'East')['connections'][0]['map'], 'MAP_ROUTE_PART2')
        self.apply(plan)
        catalog = Connections(self.source).catalog()
        self.assertTrue(all(link['reciprocal'] for row in catalog['maps'] for link in row['connections']))
        self.assertTrue(all(link['overlap'] for row in catalog['maps'] for link in row['connections']))

    def test_horizontal_split_preserves_cells_and_offsets(self):
        plan = self.plan(axis='horizontal', cut=7)
        self.assertEqual(words(plan['data/layouts/Route/map.bin']) + words(plan['data/layouts/RoutePart2/map.bin']),
                         self.world.get_map('Route')['cells'])
        self.assertIn({'map': 'MAP_WEST', 'direction': 'left', 'offset': -8}, self.planned_map(plan, 'RoutePart2')['connections'])
        self.assertIn({'map': 'MAP_ROUTE_PART2', 'direction': 'down', 'offset': 0}, self.planned_map(plan, 'Route')['connections'])
        self.apply(plan)
        self.assertTrue(all(link['reciprocal'] for row in Connections(self.source).catalog()['maps'] for link in row['connections']))

    def test_events_partition_coordinates_and_all_incoming_and_same_map_warps(self):
        warps = [
            {'x': 1, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': 2},
            {'x': 15, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': '0', 'warp_id': 'WARP_ROUTE_RIGHT'},
            {'x': 14, 'y': 3, 'dest_map': 'MAP_HOUSE', 'dest_warp_id': 0}]
        self.update_map('Route', warp_events=warps,
                        object_events=[{'x': 0, 'y': 5, 'script': 'SharedScript', 'flag': 'FLAG_ITEM', 'local_id': 'LOCALID_FIRST'},
                                       {'x': 9, 'y': 1, 'script': 'SharedScript', 'flag': 'FLAG_BERRY', 'local_id': 'LOCALID_SECOND'}],
                        coord_events=[{'x': 8, 'y': 3, 'type': 'trigger', 'script': 'SharedScript'}],
                        bg_events=[{'x': 3, 'y': 1, 'type': 'sign', 'script': 'SharedScript'}])
        self.update_map('House', warp_events=[{'x': 1, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': '2'},
                                             {'x': 2, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': 'WARP_ROUTE_RIGHT'}],
                        object_events=[{'x': 0, 'y': 0, 'type': 'clone', 'target_map': 'MAP_ROUTE', 'target_local_id': 2}])
        plan = self.plan()
        first, second, house = (self.planned_map(plan, name) for name in ('Route', 'RoutePart2', 'House'))
        self.assertEqual(first['warp_events'][0]['dest_map'], 'MAP_ROUTE_PART2')
        self.assertEqual(first['warp_events'][0]['dest_warp_id'], 1)
        self.assertEqual(second['warp_events'][0]['x'], 7)
        self.assertEqual(second['warp_events'][0]['dest_warp_id'], '0')
        self.assertEqual(house['warp_events'][0]['dest_map'], 'MAP_ROUTE_PART2')
        self.assertEqual(house['warp_events'][0]['dest_warp_id'], '1')
        self.assertEqual(house['warp_events'][1]['dest_warp_id'], 'WARP_ROUTE_RIGHT')
        self.assertEqual(house['object_events'][0]['target_map'], 'MAP_ROUTE_PART2')
        self.assertEqual(house['object_events'][0]['target_local_id'], 1)
        self.assertEqual(second['object_events'][0], {'x': 1, 'y': 1, 'script': 'SharedScript', 'flag': 'FLAG_BERRY', 'local_id': 'LOCALID_SECOND'})
        self.assertEqual(second['coord_events'][0]['x'], 0)
        self.assertEqual(first['bg_events'][0]['x'], 3)

    def test_encounters_copied_without_changing_species_and_moved_rematch_follows_actor(self):
        self.update_map('Route', object_events=[{'x': 14, 'y': 1, 'script': 'Route_EventScript_Trainer'}])
        self.write('data/maps/Route/scripts.inc', b'Route_MapScripts::\n\t.byte 0\n\nRoute_EventScript_Trainer::\n\ttrainerbattle_single TRAINER_TEST, TextA, TextB\n\tend\n')
        self.write(REMATCHES, b'const x[] = {\n [REMATCH_TEST] = REMATCH(TRAINER_TEST, TRAINER_TEST_2, MAP_ROUTE),\n};\n')
        entry = {'map': 'MAP_ROUTE', 'base_label': 'gRoute', 'land_mons': {'encounter_rate': 20, 'mons': [{'species': 'SPECIES_RALTS', 'min_level': 2, 'max_level': 4}]}}
        self.write(ENCOUNTERS, {'wild_encounter_groups': [{'for_maps': True, 'encounters': [entry]}]})
        plan = self.plan()
        encounters = json.loads(plan[ENCOUNTERS])['wild_encounter_groups'][0]['encounters']
        self.assertEqual(encounters[0], entry)
        self.assertEqual(encounters[1]['map'], 'MAP_ROUTE_PART2')
        self.assertEqual(encounters[1]['land_mons'], entry['land_mons'])
        self.assertNotEqual(encounters[1]['base_label'], entry['base_label'])
        self.assertIn(b'TRAINER_TEST_2, MAP_ROUTE_PART2)', plan[REMATCHES])

    def test_global_revision_rejects_distant_link_or_story_changes(self):
        body = self.body()
        preview = self.splitter.preview(body)
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.splitter.plan(body)
        self.update_map('House', bg_events=[{'x': 1, 'y': 1}])
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.splitter.plan({**body, 'world_revision': preview['world_revision']})
        body = self.body()
        preview = self.splitter.preview(body)
        self.write('data/maps/House/scripts.inc', b'House_MapScripts::\n\t.byte 0\n@ concurrent change\n')
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.splitter.plan({**body, 'world_revision': preview['world_revision']})

    def test_invalid_cut_name_stale_and_shared_layout_are_rejected_without_writes(self):
        for edit in ({'cut': 0}, {'cut': 16}, {'cut': 2.5}, {'cut': True}, {'axis': 'diagonal'},
                     {'new_name': '../Outside'}, {'new_name': 'West'}, {'revision': 'stale'}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.splitter.preview(self.body(**edit))
        self.update_map('House', layout='LAYOUT_ROUTE')
        with self.assertRaisesRegex(ValueError, 'shares a layout'):
            self.splitter.preview(self.body())

    def test_unsafe_dynamic_warps_dive_links_and_offmap_actors_are_rejected(self):
        self.update_map('House', warp_events=[{'x': 1, 'y': 1, 'dest_map': 'MAP_ROUTE', 'dest_warp_id': -1}])
        with self.assertRaisesRegex(ValueError, 'landing warp'):
            self.splitter.preview(self.body())
        self.update_map('House', warp_events=[])
        self.links('Route', [('East', 'dive', 0)])
        with self.assertRaisesRegex(ValueError, 'dive/emerge'):
            self.splitter.preview(self.body())
        self.links('Route', [])
        self.update_map('Route', object_events=[{'x': -1, 'y': 2}])
        with self.assertRaisesRegex(ValueError, 'off-map'):
            self.splitter.preview(self.body())

    def test_one_way_links_are_not_silently_made_reciprocal(self):
        self.links('East', [])
        plan = self.plan()
        self.assertEqual(self.planned_map(plan, 'East')['connections'], [])
        self.assertIn({'map': 'MAP_EAST', 'direction': 'right', 'offset': 0}, self.planned_map(plan, 'RoutePart2')['connections'])

    def test_repeated_split_uses_unique_names_and_preserves_existing_parts(self):
        self.add_map('WideRoute', 32, 14)
        self.apply(self.plan(name='WideRoute', revision=self.world.get_map('WideRoute')['revision'], cut=16))
        first_part = (self.source / 'data/maps/WideRoutePart2/map.json').read_bytes()
        plan = self.plan(name='WideRoute', revision=self.world.get_map('WideRoute')['revision'], cut=8)
        self.assertIn('data/maps/WideRoutePart3/map.json', plan)
        # Its incoming edge legitimately moves to the newly inserted part.
        self.assertEqual(self.planned_map(plan, 'WideRoutePart2')['connections'][-1]['map'], 'MAP_WIDE_ROUTE_PART3')
        self.assertEqual((self.source / 'data/maps/WideRoutePart2/map.json').read_bytes(), first_part)

    def test_camera_padding_rejects_tiny_halves_and_perpendicular_connected_depth(self):
        for axis, cut in (('vertical', 7), ('vertical', 9), ('horizontal', 6), ('horizontal', 8)):
            with self.subTest(axis=axis, cut=cut), self.assertRaisesRegex(ValueError, 'padding'):
                self.splitter.preview(self.body(axis=axis, cut=cut))
        layouts = self.read(LAYOUTS)
        next(item for item in layouts['layouts'] if item['id'] == 'LAYOUT_EAST')['width'] = 3
        self.write(LAYOUTS, layouts)
        with self.assertRaisesRegex(ValueError, 'camera edge padding'):
            self.splitter.preview(self.body())

    def test_source_transaction_commit_renders_both_halves_and_undo_restores_every_file(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        transactions = SourceTransactions(self.source, Path(state.name))
        before = {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        original_render = Image.open(io.BytesIO(self.world.render_map('Route'))).copy()
        transaction = transactions.commit(self.plan(), 'Split Route')
        self.assertIsNotNone(transaction['id'])
        saved = FixtureWorld(self.source)
        for name in ('Route', 'RoutePart2'):
            data = saved.get_map(name)
            self.assertEqual((data['width'], data['height']), (8, 14))
            self.assertEqual(len(data['cells']), 112)
        rendered = Image.new('RGBA', original_render.size)
        rendered.paste(Image.open(io.BytesIO(saved.render_map('Route'))), (0, 0))
        rendered.paste(Image.open(io.BytesIO(saved.render_map('RoutePart2'))), (128, 0))
        self.assertEqual(rendered.tobytes(), original_render.tobytes())
        links = Connections(self.source).catalog()
        self.assertTrue(all(link['reciprocal'] and link['overlap'] for row in links['maps'] for link in row['connections']))
        restored = transactions.undo(transaction['id'])
        self.assertIsNotNone(restored['id'])
        after = {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(FixtureWorld(self.source).get_map('Route')['width'], 16)
        self.assertEqual(transactions.created(), set())


@unittest.skipUnless((SOURCE / 'data/maps/Route102/map.json').is_file(), 'Emerald source is not installed')
class RealRoute102SplitTests(unittest.TestCase):
    def test_midpoint_is_lossless_and_updates_calvin_and_oldale_without_writing_source(self):
        world = World(SOURCE)
        splitter = RouteSplitter(world)
        current = world.get_map('Route102')
        body = {'name': 'Route102', 'revision': current['revision'], 'axis': 'vertical', 'cut': 25}
        preview = splitter.preview(body)
        plan = splitter.plan({**body, 'world_revision': preview['world_revision'], 'new_name': preview['new_name']})
        new_name = preview['new_name']
        old_raw = (SOURCE / current['layout']['blockdata_filepath']).read_bytes()
        first, second = words(plan[current['layout']['blockdata_filepath']]), words(plan[f'data/layouts/{new_name}/map.bin'])
        self.assertEqual([value for y in range(20) for value in first[y * 25:(y + 1) * 25] + second[y * 25:(y + 1) * 25]], current['cells'])
        part = json.loads(plan[f'data/maps/{new_name}/map.json'])
        calvin = next(event for event in part['object_events'] if event['script'] == 'Route102_EventScript_Calvin')
        self.assertEqual((calvin['x'], calvin['y']), (8, 14))
        self.assertEqual(preview['rematches_retargeted'], 1)
        self.assertEqual(preview['encounter_tables_copied'], 1)
        self.assertIn(part['id'].encode(), plan[REMATCHES])
        oldale = json.loads(plan['data/maps/OldaleTown/map.json'])
        self.assertTrue(any(edge['map'] == part['id'] and edge['direction'] == 'left' for edge in oldale['connections']))
        self.assertEqual((SOURCE / current['layout']['blockdata_filepath']).read_bytes(), old_raw)
        self.assertFalse((SOURCE / f'data/maps/{new_name}/map.json').exists())
        self.assertTrue(all(not path.startswith(('graphics/', 'src/data/pokemon/')) for path in plan))
        for axis, cut in (('vertical', 6), ('horizontal', 4)):
            with self.assertRaisesRegex(ValueError, 'padding'):
                splitter.preview({**body, 'axis': axis, 'cut': cut})


if __name__ == '__main__':
    unittest.main()
