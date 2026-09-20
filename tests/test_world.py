from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from world import World, LAYOUTS, GROUPS, words


SOURCE = Path(__file__).resolve().parents[1] / 'source' / 'pokeemerald'


class WorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(SOURCE)

    def littleroot(self):
        return self.world.get_map('LittlerootTown')

    def test_catalogs_cover_source_and_every_static_object_has_preview(self):
        catalog = self.world.list_maps()
        self.assertEqual(len(catalog['maps']), len(list((SOURCE / 'data/maps').glob('*/map.json'))))
        self.assertEqual(len(catalog['tilesets']), 75)
        objects = self.world.object_catalog()['objects']
        self.assertEqual(len(objects), 255)
        self.assertEqual(sum(obj['preview_available'] for obj in objects), 239)
        self.assertTrue(all(obj['preview_available'] or obj['dynamic'] for obj in objects))
        truck = Image.open(io.BytesIO(self.world.object_sprite('OBJ_EVENT_GFX_TRUCK')))
        self.assertEqual(truck.size, (48, 48))
        self.assertGreater(len(truck.getcolors()), 5)
        self.assertEqual(truck.getpixel((0, 0))[3], 0)

    def test_source_blocks_decode_losslessly_and_noop_has_no_writes(self):
        current = self.littleroot()
        raw = (SOURCE / current['layout']['blockdata_filepath']).read_bytes()
        self.assertEqual(current['cells'], words(raw))
        self.assertEqual(self.world.plan_save('LittlerootTown', current), {})
        self.assertEqual(len(current['cells']), current['width'] * current['height'])

    def test_real_render_matches_atlas_ids_and_has_palette_colors(self):
        current = self.littleroot()
        atlas = Image.open(io.BytesIO(self.world.atlas('LittlerootTown')))
        rendered = Image.open(io.BytesIO(self.world.render_map('LittlerootTown')))
        self.assertEqual(rendered.size, (320, 320))
        self.assertGreater(len(rendered.getcolors(65536)), 40)
        for index in (0, 26, 182, 277):
            tile_id = current['cells'][index] & 1023
            ax, ay = tile_id % 16 * 16, tile_id // 16 * 16
            x, y = index % 20 * 16, index // 20 * 16
            self.assertEqual(atlas.crop((ax, ay, ax + 16, ay + 16)).tobytes(),
                             rendered.crop((x, y, x + 16, y + 16)).tobytes())
        self.assertTrue(any(m['behavior_name'] == 'MB_TALL_GRASS' for m in current['tileset']['metatiles']))

    def test_paint_changes_exactly_one_u16_preserving_collision_and_elevation(self):
        current = self.littleroot()
        original = current['cells'].copy()
        current['cells'][22] = (original[22] & 0xFC00) | 1
        plan = self.world.plan_save('LittlerootTown', current)
        relative = current['layout']['blockdata_filepath']
        self.assertEqual(set(plan), {relative})
        planned = words(plan[relative])
        self.assertEqual(planned[:22], original[:22])
        self.assertEqual(planned[23:], original[23:])
        self.assertEqual(planned[22] & 0xFC00, original[22] & 0xFC00)
        self.assertEqual((SOURCE / relative).read_bytes(), bytes().join(v.to_bytes(2, 'little') for v in original))

    def test_revision_and_ranges_reject_invalid_edits(self):
        current = self.littleroot()
        for values in ({'revision': 'stale'}, {'cells': [0]}, {'border': []}, {'width': 255, 'height': 255},
                       {'layout': {**current['layout'], 'blockdata_filepath': '../../evil.bin'}}):
            with self.subTest(values=list(values)), self.assertRaises(ValueError):
                self.world.plan_save('LittlerootTown', {**current, **values})
        bad = deepcopy(current)
        bad['cells'][0] = True
        with self.assertRaises(ValueError):
            self.world.plan_save('LittlerootTown', bad)
        bad['cells'][0] = 65536
        with self.assertRaises(ValueError):
            self.world.plan_save('LittlerootTown', bad)

    def test_object_insert_move_remove_and_unknown_fields_preserved(self):
        current = self.littleroot()
        original = deepcopy(current['map'])
        current['map']['custom_note'] = 'retained for external tooling'
        event = deepcopy(current['map']['object_events'][1])
        event.update(x=8, y=14, graphics_id='OBJ_EVENT_GFX_NINJA_BOY', script='0', flag='0')
        event.pop('local_id', None)
        current['map']['object_events'].append(event)
        current['map']['object_events'][0]['x'] = 15
        current['map']['object_events'].pop(2)
        plan = self.world.plan_save('LittlerootTown', current)
        result = json.loads(plan['data/maps/LittlerootTown/map.json'])
        self.assertEqual(result['custom_note'], 'retained for external tooling')
        self.assertEqual(result['warp_events'], original['warp_events'])
        self.assertEqual(result['object_events'][-1]['graphics_id'], 'OBJ_EVENT_GFX_NINJA_BOY')
        self.assertEqual(result['object_events'][0]['x'], 15)
        self.assertEqual(len(result['object_events']), len(original['object_events']))

    def test_event_reference_and_bounds_validation(self):
        for field, value in [('graphics_id', 'OBJ_EVENT_GFX_MADE_UP'), ('x', -1), ('elevation', 16),
                             ('movement_type', 'MOVEMENT_TYPE_FAKE'), ('script', 'MissingStoryLabel')]:
            current = self.littleroot()
            current['map']['object_events'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.world.plan_save('LittlerootTown', current)
        current = self.littleroot()
        current['map']['warp_events'][0]['dest_warp_id'] = 200
        with self.assertRaises(ValueError):
            self.world.plan_save('LittlerootTown', current)

    def test_resize_is_atomic_and_rejects_stranded_events(self):
        current = self.littleroot()
        current.update(width=21, cells=[v for row in range(20) for v in current['cells'][row * 20:(row + 1) * 20] + [current['cells'][0]]])
        plan = self.world.plan_save('LittlerootTown', current)
        self.assertEqual(set(plan), {LAYOUTS, current['layout']['blockdata_filepath']})
        resized = next(x for x in json.loads(plan[LAYOUTS])['layouts'] if x['id'] == current['layout']['id'])
        self.assertEqual((resized['width'], resized['height']), (21, 20))
        current = self.littleroot()
        current.update(width=2, height=2, cells=current['cells'][:4])
        with self.assertRaisesRegex(ValueError, 'outside the map'):
            self.world.plan_save('LittlerootTown', current)

    def test_shared_layout_requires_explicit_confirmation(self):
        layouts = {}
        for row in self.world.list_maps()['maps']:
            layouts.setdefault(row['layout'], []).append(row['name'])
        names = next(names for names in layouts.values() if len(names) > 1)
        current = self.world.get_map(names[0])
        current['cells'][0] ^= 0x400
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.world.plan_save(names[0], current)
        current['confirm_shared'] = True
        plan = self.world.plan_save(names[0], current)
        self.assertEqual(set(plan), {current['layout']['blockdata_filepath']})

    def test_shared_events_write_owner_and_preserve_reference(self):
        current = self.world.get_map('ContestHallBeauty')
        self.assertEqual(current['events_owner'], 'ContestHall')
        current['map']['object_events'][0]['x'] = 5
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.world.plan_save('ContestHallBeauty', current)
        plan = self.world.plan_save('ContestHallBeauty', {**current, 'confirm_shared': True})
        self.assertEqual(set(plan), {'data/maps/ContestHall/map.json'})
        self.assertEqual(json.loads(plan['data/maps/ContestHall/map.json'])['object_events'][0]['x'], 5)

    def test_shared_resize_checks_other_maps_events_too(self):
        current = self.world.get_map('BattleFrontier_BattleTowerBattleRoom')
        current.update(width=1, height=1, cells=[current['cells'][0]], confirm_shared=True)
        for key in ('object_events', 'warp_events', 'coord_events', 'bg_events'):
            current['map'][key] = []
        with self.assertRaisesRegex(ValueError, 'Shared map'):
            self.world.plan_save(current['name'], current)

    def test_new_map_registration_is_complete_and_has_no_inherited_story_events(self):
        plan = self.world.plan_new('MyNewTown', 'LittlerootTown', 25, 22)
        self.assertEqual(len(plan), 7)
        data = json.loads(plan['data/maps/MyNewTown/map.json'])
        self.assertEqual(data['id'], 'MAP_MY_NEW_TOWN')
        self.assertEqual(data['object_events'], [])
        self.assertEqual(data['warp_events'], [])
        self.assertIsNone(data['connections'])
        self.assertEqual(len(words(plan['data/layouts/MyNewTown/map.bin'])), 550)
        self.assertEqual(len(set(words(plan['data/layouts/MyNewTown/map.bin']))), 1)
        self.assertEqual(len(plan['data/layouts/MyNewTown/border.bin']), 8)
        self.assertIn('MyNewTown', json.loads(plan[GROUPS])['gMapGroup_CustomWorld'])
        self.assertIn(b'.include "data/maps/MyNewTown/scripts.inc"', plan['data/event_scripts.s'])
        self.assertEqual(plan['data/maps/MyNewTown/scripts.inc'], b'MyNewTown_MapScripts::\n\t.byte 0\n')
        self.assertFalse((SOURCE / 'data/maps/MyNewTown').exists())
        for name in ('../Oops', 'LittlerootTown', 'littleroottown', 'CON'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.world.plan_new(name)

    def test_revision_includes_shared_owner_and_binary_data(self):
        current = self.world.get_map('ContestHallBeauty')
        old_read = self.world._read

        def changed(relative):
            data = old_read(relative)
            return data + b' ' if relative == 'data/maps/ContestHall/map.json' else data

        with patch.object(self.world, '_read', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed since'):
                self.world.plan_save('ContestHallBeauty', current)

    def test_new_map_can_reuse_empty_folders_left_by_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = World(folder)
            for relative in ('data/maps/RestoredTown', 'data/layouts/RestoredTown'):
                (Path(folder) / relative).mkdir(parents=True)
            with patch.object(draft, '_maps', side_effect=self.world._maps), \
                 patch.object(draft, 'get_map', side_effect=self.world.get_map), \
                 patch.object(draft, '_json', side_effect=self.world._json), \
                 patch.object(draft, '_text', side_effect=self.world._text):
                plan = draft.plan_new('RestoredTown')
                self.assertIn('data/maps/RestoredTown/map.json', plan)
                (Path(folder) / 'data/maps/RestoredTown/user-file.txt').write_text('preserve me')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    draft.plan_new('RestoredTown')


if __name__ == '__main__':
    unittest.main()
