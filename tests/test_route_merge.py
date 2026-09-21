"""Rectangular source map merges preserve terrain, events and graph edges."""
import json
from pathlib import Path
import tempfile
import unittest

from route_merge import RouteMerger
from route_split import ENCOUNTERS, RouteSplitter
from world import World, GROUPS, LAYOUTS, json_bytes, pack_words, words
from workspace_edits import SourceTransactions


class FixtureWorld(World):
    def _pair(self, primary, secondary):
        return None, {'count': 1024, 'metatiles': [], 'revision': 'fixture-art'}


class RouteMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source'
        self.write(LAYOUTS, {'layouts': []})
        self.write(GROUPS, {'group_order': ['gMapGroup_Test'], 'gMapGroup_Test': []})
        self.write('data/event_scripts.s', b'\t.include "data/maps/A/scripts.inc"\n\t.include "data/maps/B/scripts.inc"\n')
        self.add_map('A', 8, 14)
        self.add_map('B', 8, 14)
        self.add_map('West', 8, 14)
        self.add_map('East', 8, 14)
        self.add_map('North', 16, 7)
        self.add_map('House', 3, 3)
        self.links('A', [('B', 'right', 0), ('West', 'left', 0), ('North', 'up', 0)])
        self.links('B', [('A', 'left', 0), ('East', 'right', 0), ('North', 'up', -8)])
        self.links('West', [('A', 'right', 0)])
        self.links('East', [('B', 'left', 0)])
        self.links('North', [('A', 'down', 0), ('B', 'down', 8)])
        self.world = FixtureWorld(self.source)
        self.merger = RouteMerger(self.world)

    def write(self, path, value):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else json_bytes(value))

    def read(self, path):
        return json.loads((self.source / path).read_bytes())

    def add_map(self, name, width, height):
        data = {'id': 'MAP_' + name.upper(), 'name': name, 'layout': 'LAYOUT_' + name.upper(),
                'map_type': 'MAP_TYPE_ROUTE', 'region_map_section': 'MAPSEC_ROUTE_102',
                'connections': None, 'object_events': [], 'warp_events': [], 'coord_events': [], 'bg_events': []}
        layout = {'id': data['layout'], 'name': name + '_Layout', 'width': width, 'height': height,
                  'primary_tileset': 'gTileset_General', 'secondary_tileset': 'gTileset_Petalburg',
                  'blockdata_filepath': f'data/layouts/{name}/map.bin', 'border_filepath': f'data/layouts/{name}/border.bin'}
        self.write(f'data/maps/{name}/map.json', data)
        self.write(f'data/maps/{name}/scripts.inc', (name + '_MapScripts::\n\t.byte 0\n').encode())
        self.write(layout['blockdata_filepath'], pack_words([0xA000 + index for index in range(width * height)]))
        self.write(layout['border_filepath'], pack_words([0xF000, 0xD002, 0xC003, 0xB004]))
        layouts, groups = self.read(LAYOUTS), self.read(GROUPS)
        layouts['layouts'].append(layout)
        groups['gMapGroup_Test'].append(name)
        self.write(LAYOUTS, layouts)
        self.write(GROUPS, groups)

    def update(self, name, **changes):
        path = f'data/maps/{name}/map.json'
        data = self.read(path)
        data.update(changes)
        self.write(path, data)

    def links(self, name, links):
        self.update(name, connections=[{'map': 'MAP_' + target.upper(), 'direction': direction, 'offset': offset}
                                        for target, direction, offset in links])

    def body(self, **extra):
        entries = extra.get('maps')
        if entries is None:
            entries = [{'name': name, 'x': x, 'y': 20, 'revision': self.world.get_map(name)['revision']}
                       for name, x in [('A', 10), ('B', 18)]]
        result = {'name': 'A', 'maps': entries}
        result.update(extra)
        return result

    def plan(self, **extra):
        body = self.body(**extra)
        preview = self.merger.preview(body)
        return self.merger.plan({**body, 'world_revision': preview['world_revision']})

    def planned(self, plan, name):
        path = f'data/maps/{name}/map.json'
        return json.loads(plan.get(path, (self.source / path).read_bytes()))

    def apply(self, plan):
        for path, data in plan.items():
            if data is None:
                (self.source / path).unlink()
            else:
                self.write(path, data)

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def test_exact_cells_headers_and_border_kept_and_donor_metadata_removed(self):
        before = self.snapshot()
        a, b = self.world.get_map('A'), self.world.get_map('B')
        plan = self.plan()
        self.assertEqual(words(plan['data/layouts/A/map.bin']),
                         [v for row in range(14) for v in a['cells'][row * 8:(row + 1) * 8] + b['cells'][row * 8:(row + 1) * 8]])
        self.assertIsNone(plan['data/maps/B/map.json'])
        self.assertNotIn('data/maps/B/scripts.inc', plan)
        self.assertNotIn('data/layouts/B/map.bin', plan)
        self.assertNotIn('data/layouts/A/border.bin', plan)
        self.assertNotIn('B', json.loads(plan[GROUPS])['gMapGroup_Test'])
        self.assertEqual([x['id'] for x in json.loads(plan[LAYOUTS])['layouts']],
                         [x['id'] for x in self.read(LAYOUTS)['layouts']])
        self.assertEqual(self.planned(plan, 'A')['id'], a['id'])
        self.assertEqual(before, self.snapshot())

    def test_internal_edges_removed_and_external_and_incoming_rebased_and_deduplicated(self):
        plan = self.plan()
        self.assertCountEqual(self.planned(plan, 'A')['connections'], [
            {'map': 'MAP_WEST', 'direction': 'left', 'offset': 0},
            {'map': 'MAP_EAST', 'direction': 'right', 'offset': 0},
            {'map': 'MAP_NORTH', 'direction': 'up', 'offset': 0}])
        self.assertEqual(self.planned(plan, 'North')['connections'], [{'map': 'MAP_A', 'direction': 'down', 'offset': 0}])
        self.assertEqual(self.planned(plan, 'East')['connections'], [{'map': 'MAP_A', 'direction': 'left', 'offset': 0}])

    def test_primary_can_be_right_box_with_same_combined_coordinates(self):
        self.update('A', bg_events=[{'x': 2, 'y': 3, 'type': 'sign'}])
        self.update('B', bg_events=[{'x': 1, 'y': 4, 'type': 'sign'}])
        plan = self.plan(name='B')
        merged = self.planned(plan, 'B')
        self.assertEqual([(e['x'], e['y']) for e in merged['bg_events']], [(9, 4), (2, 3)])
        self.assertEqual(self.planned(plan, 'North')['connections'], [{'map': 'MAP_B', 'direction': 'down', 'offset': 0}])

    def test_every_event_shifts_and_incoming_numeric_and_named_warps_and_clones_retarget(self):
        self.update('A', warp_events=[{'x': 1, 'y': 1, 'dest_map': 'MAP_B', 'dest_warp_id': 0}],
                    object_events=[{'x': 0, 'y': 1, 'local_id': 'LOCAL_A'}])
        self.update('B', warp_events=[{'x': 2, 'y': 3, 'warp_id': 'WARP_B', 'dest_map': 'MAP_A', 'dest_warp_id': '0'}],
                    object_events=[{'x': 3, 'y': 4, 'local_id': 'LOCAL_B'}],
                    coord_events=[{'x': 4, 'y': 5}], bg_events=[{'x': 5, 'y': 6}])
        self.update('House', warp_events=[{'x': 0, 'y': 0, 'dest_map': 'MAP_B', 'dest_warp_id': '0'},
                                         {'x': 1, 'y': 0, 'dest_map': 'MAP_B', 'dest_warp_id': 'WARP_B'}],
                    object_events=[{'type': 'clone', 'x': 0, 'y': 0, 'target_map': 'MAP_B', 'target_local_id': 1},
                                   {'type': 'clone', 'x': 1, 'y': 0, 'target_map': 'MAP_B', 'target_local_id': 'LOCAL_B'}])
        plan = self.plan()
        merged, house = self.planned(plan, 'A'), self.planned(plan, 'House')
        self.assertEqual(merged['warp_events'][0]['dest_warp_id'], 1)
        self.assertEqual(merged['warp_events'][1]['x'], 10)
        self.assertEqual(merged['object_events'][1]['x'], 11)
        self.assertEqual(merged['coord_events'][0]['x'], 12)
        self.assertEqual(merged['bg_events'][0]['x'], 13)
        self.assertEqual(house['warp_events'][0]['dest_map'], 'MAP_A')
        self.assertEqual(house['warp_events'][0]['dest_warp_id'], '1')
        self.assertEqual(house['warp_events'][1]['dest_warp_id'], 'WARP_B')
        self.assertEqual(house['object_events'][0]['target_local_id'], 2)
        self.assertEqual(house['object_events'][1]['target_local_id'], 'LOCAL_B')
        self.assertTrue(all(e['target_map'] == 'MAP_A' for e in house['object_events']))

    def test_four_boxes_and_horizontal_merge_are_exact_rectangles(self):
        for n in ('A', 'B', 'West', 'East', 'North'):
            self.links(n, [])
        self.add_map('C', 8, 14)
        self.add_map('D', 8, 14)
        entries = [{'name': n, 'x': x, 'y': y, 'revision': self.world.get_map(n)['revision']}
                   for n, x, y in [('A', 0, 0), ('B', 8, 0), ('C', 0, 14), ('D', 8, 14)]]
        preview = self.merger.preview(self.body(maps=entries))
        self.assertEqual((preview['width'], preview['height']), (16, 28))
        entries = [entries[0], {**entries[1], 'x': 0, 'y': 14}]
        preview = self.merger.preview(self.body(maps=entries))
        self.assertEqual((preview['width'], preview['height']), (8, 28))

    def test_gaps_overlaps_fractional_positions_duplicates_tilesets_and_limits_rejected(self):
        for x, message in ((17, 'overlap'), (19, 'gaps'), (18.2, 'integer')):
            body = self.body()
            body['maps'][1]['x'] = x
            with self.subTest(x=x), self.assertRaisesRegex(ValueError, message):
                self.merger.preview(body)
        body = self.body()
        body['maps'][1] = body['maps'][0]
        with self.assertRaisesRegex(ValueError, 'once'):
            self.merger.preview(body)
        layouts = self.read(LAYOUTS)
        layouts['layouts'][1]['secondary_tileset'] = 'gTileset_Other'
        self.write(LAYOUTS, layouts)
        with self.assertRaisesRegex(ValueError, 'tilesets'):
            self.merger.preview(self.body())
        layouts['layouts'][1]['secondary_tileset'] = 'gTileset_Petalburg'
        self.write(LAYOUTS, layouts)
        body = self.body()
        body['maps'][1]['x'] = 300
        with self.assertRaisesRegex(ValueError, 'width'):
            self.merger.preview(body)

    def test_shared_offmap_and_duplicate_ids_rejected(self):
        self.update('B', object_events=[{'x': -1, 'y': 0}])
        with self.assertRaisesRegex(ValueError, 'off-map'):
            self.merger.preview(self.body())
        self.update('A', object_events=[{'x': 1, 'y': 0, 'local_id': 'LOCAL_SAME'}])
        self.update('B', object_events=[{'x': 1, 'y': 0, 'local_id': 'LOCAL_SAME'}])
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.merger.preview(self.body())
        self.update('House', shared_events_map='A')
        with self.assertRaisesRegex(ValueError, 'shares a layout or events'):
            self.merger.preview(self.body())

    def test_ambiguous_dynamic_warp_dive_and_newly_interior_edges_rejected(self):
        self.update('House', warp_events=[{'x': 0, 'y': 0, 'dest_map': 'MAP_B', 'dest_warp_id': -1}])
        with self.assertRaisesRegex(ValueError, 'dynamic or unresolved'):
            self.merger.preview(self.body())
        self.update('House', warp_events=[])
        self.links('B', [('East', 'dive', 0)])
        with self.assertRaisesRegex(ValueError, 'dive/emerge'):
            self.merger.preview(self.body())
        self.links('B', [('West', 'left', 0)])
        with self.assertRaisesRegex(ValueError, 'interior or disconnected'):
            self.merger.preview(self.body())

    def test_encounter_policy_explicit_and_equal_split_tables_need_no_override(self):
        entry = {'map': 'MAP_A', 'base_label': 'gA', 'land_mons': {'mons': [{'species': 'SPECIES_RALTS'}]}}
        other = {**entry, 'map': 'MAP_B', 'base_label': 'gB'}
        self.write(ENCOUNTERS, {'wild_encounter_groups': [{'for_maps': True, 'encounters': [entry, other]}]})
        plan = self.plan()
        self.assertEqual(json.loads(plan[ENCOUNTERS])['wild_encounter_groups'][0]['encounters'], [entry])
        other = {**other, 'land_mons': {'mons': [{'species': 'SPECIES_POOCHYENA'}]}}
        self.write(ENCOUNTERS, {'wild_encounter_groups': [{'for_maps': True, 'encounters': [entry, other]}]})
        with self.assertRaisesRegex(ValueError, 'Explicitly choose'):
            self.merger.preview(self.body())
        preview = self.merger.preview(self.body(encounter_policy='keep_primary'))
        self.assertTrue(preview['different_encounters'])
        plan = self.plan(encounter_policy='keep_primary')
        self.assertEqual(json.loads(plan[ENCOUNTERS])['wild_encounter_groups'][0]['encounters'], [entry])

    def test_map_tokens_retargeted_but_script_labels_and_paths_kept(self):
        self.write('src/battle_setup.c', b'REMATCH(TRAINER_FOO, MAP_B)\nMAP_BIGGER\n')
        self.write('data/maps/B/scripts.inc', b'B_MapScripts::\n\t.byte 0\nB_Script::\n\twarp MAP_B, 0, 1, 2\n')
        plan = self.plan()
        self.assertEqual(plan['src/battle_setup.c'], b'REMATCH(TRAINER_FOO, MAP_A)\nMAP_BIGGER\n')
        self.assertIn(b'B_Script::', plan['data/maps/B/scripts.inc'])
        self.assertIn(b'warp MAP_A', plan['data/maps/B/scripts.inc'])
        self.assertNotIn('data/event_scripts.s', plan)
        self.write('src/map_switch.c', b'switch (n) { case MAP_A: return 1; case MAP_B: return 2; }')
        with self.assertRaisesRegex(ValueError, 'switch cases'):
            self.merger.preview(self.body())

    def test_protected_references_never_rewritten(self):
        self.write('src/data/pokemon/base_stats.h', b'// MAP_B\n')
        with self.assertRaisesRegex(ValueError, 'protected'):
            self.merger.preview(self.body())

    def test_combined_event_limits_and_cross_kind_aliases_are_rejected(self):
        self.update('A', object_events=[{'x': 1, 'y': 1} for _ in range(64)])
        self.update('B', object_events=[{'x': 1, 'y': 1} for _ in range(64)])
        with self.assertRaisesRegex(ValueError, 'limit of 126'):
            self.merger.preview(self.body())
        self.update('A', object_events=[{'x': 1, 'y': 1, 'local_id': 'EVENT_SAME'}])
        self.update('B', object_events=[], warp_events=[{'x': 1, 'y': 1, 'warp_id': 'EVENT_SAME', 'dest_map': 'MAP_HOUSE', 'dest_warp_id': 0}])
        with self.assertRaisesRegex(ValueError, 'conflicting named IDs'):
            self.merger.preview(self.body())

    def test_retained_callback_owner_can_be_a_removed_map_script(self):
        self.update('A', shared_scripts_map='B')
        self.update('House', shared_scripts_map='B')
        self.apply(self.plan())
        self.assertEqual(self.world.get_map('A')['scripts_path'], 'data/maps/B/scripts.inc')
        self.assertEqual(self.world.get_map('House')['scripts_path'], 'data/maps/B/scripts.inc')
        self.assertTrue((self.source / 'data/maps/B/scripts.inc').is_file())

    def test_direct_generated_event_references_are_rejected(self):
        self.write('data/map_reference.inc', b'\t.4byte B_MapEvents\n')
        with self.assertRaisesRegex(ValueError, 'directly references generated data'):
            self.merger.preview(self.body())

    def test_stale_generated_map_outputs_are_left_to_mapjson(self):
        self.write('data/maps/B/events.inc', b'B_MapEvents::\n\t.4byte B_ObjectEvents\n')
        self.write('data/maps/headers.inc', b'\t.include "data/maps/B/header.inc"\n')
        self.write('include/constants/map_groups.h', b'#define MAP_B (1 | (0 << 8))\n')
        plan = self.plan()
        self.assertNotIn('data/maps/B/events.inc', plan)
        self.assertNotIn('include/constants/map_groups.h', plan)
        self.assertIn(GROUPS, plan)

    def test_primary_terrain_file_alias_in_another_layout_is_rejected_without_writes(self):
        layouts = self.read(LAYOUTS)
        next(item for item in layouts['layouts'] if item['id'] == 'LAYOUT_EAST')['blockdata_filepath'] = 'data/layouts/A/../A/map.bin'
        self.write(LAYOUTS, layouts)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'shares its terrain file'):
            self.merger.preview(self.body())
        self.assertEqual(before, self.snapshot())

    def test_new_overlap_between_outgoing_neighbor_spans_is_rejected(self):
        for n in self.world._maps():
            self.links(n, [])
        self.add_map('Tall', 8, 28)
        self.links('A', [('Tall', 'right', 0)])
        self.links('B', [('East', 'right', 0)])
        entries = [{'name': n, 'x': 0, 'y': y, 'revision': self.world.get_map(n)['revision']}
                   for n, y in [('A', 0), ('B', 14)]]
        with self.assertRaisesRegex(ValueError, 'overlap.*walking connections'):
            self.merger.preview(self.body(maps=entries))

    def test_new_overlap_between_incoming_and_other_neighbor_spans_is_rejected(self):
        for n in self.world._maps():
            self.links(n, [])
        self.links('North', [('A', 'down', 0), ('West', 'down', 8)])
        with self.assertRaisesRegex(ValueError, 'overlap.*North.*walking connections'):
            self.merger.preview(self.body())

    def test_unexpanded_existing_edge_overlap_is_preserved(self):
        for n in self.world._maps():
            self.links(n, [])
        self.links('A', [('West', 'left', 0), ('East', 'left', 0)])
        # The combined rectangle grows rightward; its left edge and prior
        # ambiguous pair are unchanged, so the operation adds no conflict.
        plan = self.plan()
        self.assertEqual(len(self.planned(plan, 'A')['connections']), 2)

    def test_preview_binds_source_changes_and_geometry_and_policy(self):
        body = self.body()
        preview = self.merger.preview(body)
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.merger.plan(body)
        self.write('data/maps/House/scripts.inc', b'House_MapScripts::\n\t.byte 0\n@ changed\n')
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.merger.plan({**body, 'world_revision': preview['world_revision']})
        preview = self.merger.preview(body)
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.merger.plan({**body, 'encounter_policy': 'keep_primary', 'world_revision': preview['world_revision']})
        shifted = self.body()
        for item in shifted['maps']:
            item['x'] += 1
        with self.assertRaisesRegex(ValueError, 'preview'):
            self.merger.plan({**shifted, 'world_revision': preview['world_revision']})

    def test_transaction_undo_restores_all_files_and_removes_no_art(self):
        transactions = SourceTransactions(self.source, Path(self.temp.name) / 'state')
        before = self.snapshot()
        result = transactions.commit(self.plan(), 'Combine map boxes')
        self.assertFalse((self.source / 'data/maps/B/map.json').exists())
        self.assertTrue((self.source / 'data/maps/B/scripts.inc').exists())
        self.assertEqual(self.world.get_map('A')['width'], 16)
        transactions.undo(result['id'])
        self.assertEqual(before, self.snapshot())

    def test_split_and_recombine_roundtrip_keeps_terrain_and_transitions(self):
        # Recombine the existing fixtures first, then split/rejoin that real map.
        self.apply(self.plan())
        original = self.world.get_map('A')
        split_body = {'name': 'A', 'revision': original['revision'], 'axis': 'vertical', 'cut': 8}
        splitter = RouteSplitter(self.world)
        preview = splitter.preview(split_body)
        self.apply(splitter.plan({**split_body, 'world_revision': preview['world_revision'], 'new_name': preview['new_name']}))
        part = preview['new_name']
        entries = [{'name': n, 'x': x, 'y': 0, 'revision': self.world.get_map(n)['revision']} for n, x in [('A', 0), (part, 8)]]
        plan = self.plan(maps=entries)
        self.assertEqual(words(plan['data/layouts/A/map.bin']), original['cells'])
        self.assertCountEqual(self.planned(plan, 'A')['connections'], original['map']['connections'])


if __name__ == '__main__':
    unittest.main()
