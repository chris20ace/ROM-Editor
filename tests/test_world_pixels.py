"""Pixel cuts compile into real source assets without changing existing art."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from world import World, pack_words, words
from world_batch import plan_world_batch
from workspace_edits import SourceTransactions


class WorldPixelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.write('include/constants/metatile_behaviors.h', b'enum { MB_NORMAL, MB_OTHER };\n')
        self.write('src/graphics.c', b'')
        self.write('src/tileset_anims.c', b'')
        layouts = []
        for name in ['A', 'B']:
            layout = {'id': 'LAYOUT_' + name, 'name': name, 'width': 3, 'height': 2,
                      'primary_tileset': 'gTileset_Primary', 'secondary_tileset': 'gTileset_Secondary',
                      'blockdata_filepath': f'data/layouts/{name}/map.bin', 'border_filepath': f'data/layouts/{name}/border.bin'}
            layouts.append(layout)
            data = {'name': name, 'id': 'MAP_' + name, 'layout': layout['id'], 'requires_flash': False,
                    'allow_cycling': True, 'allow_escaping': False, 'allow_running': True, 'show_map_name': True,
                    'map_type': 'MAP_TYPE_TOWN', 'region_map_section': 'MAPSEC_LITTLEROOT_TOWN',
                    'connections': None, 'object_events': [], 'warp_events': [], 'coord_events': [], 'bg_events': []}
            self.write(f'data/maps/{name}/map.json', json.dumps(data).encode())
            self.write(layout['blockdata_filepath'], pack_words([0x3400] * 6))
            self.write(layout['border_filepath'], pack_words([0x3000] * 4))
        self.write('data/layouts/layouts.json', json.dumps({'layouts': layouts}).encode())
        colors = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (255, 255, 0)] + [(0, 0, 0)] * 12
        headers, graphics, metatiles = [], [], []
        for name, secondary in [('Primary', False), ('Secondary', True)]:
            base = f'data/tilesets/{name}'
            self.palette(f'{base}/palette.pal', colors)
            image = Image.new('P', (8, 8 if secondary else 32))
            image.putpalette([channel for color in colors for channel in color] + [0] * (768 - 48))
            if not secondary:
                for y in range(8):
                    for x in range(8):
                        image.putpixel((x, y + 8), 1)
                        image.putpixel((x, y + 16), 2)
                        image.putpixel((x, y + 24), 3 if x < 3 else 0)
            self.image(f'{base}/tiles.png', image)
            descriptors = [0] * 8 if secondary else ([1] * 4 + [0] * 4 + [2] * 4 + [0] * 4 + [0x403] * 4 + [0] * 4 + [1] * 4 + [0] * 4)
            attrs = [0x1000] if secondary else [0x1000, 0x1005, 0x1000, 0]
            self.write(f'{base}/metatiles.bin', pack_words(descriptors))
            self.write(f'{base}/attributes.bin', pack_words(attrs))
            headers.append(f'const struct Tileset gTileset_{name} = {{.isSecondary = {"TRUE" if secondary else "FALSE"}, .tiles = tiles_{name}, .palettes = palettes_{name}, .metatiles = metatiles_{name}, .metatileAttributes = attributes_{name}}};')
            graphics.append(f'const u32 tiles_{name}[] = INCGFX_U32("{base}/tiles.png");')
            graphics.append(f'const u16 palettes_{name}[][16] = {{' + ','.join(f'INCGFX_U16("{base}/palette.pal")' for _ in range(13)) + '};')
            metatiles += [f'const u16 metatiles_{name}[] = INCBIN_U16("{base}/metatiles.bin");',
                          f'const u16 attributes_{name}[] = INCBIN_U16("{base}/attributes.bin");']
        for name, lines in [('headers', headers), ('graphics', graphics), ('metatiles', metatiles)]:
            self.write(f'src/data/tilesets/{name}.h', '\n'.join(lines).encode())
        self.world = World(self.source)

    def write(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def palette(self, path, colors):
        self.write(path, ('JASC-PAL\n0100\n16\n' + '\n'.join(' '.join(map(str, color)) for color in colors) + '\n').encode())

    def image(self, path, image):
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        self.write(path, stream.getvalue())

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def body(self, name='A', refs=None):
        body = self.world.get_map(name)
        body['pixel_patches'] = {'0': refs if refs is not None else [((1 if i % 16 < i // 16 else 0) * 256 + i) for i in range(256)]}
        body['tileset_revision'] = body['tileset']['revision']
        return body

    def test_arbitrary_diagonal_pixels_round_trip_exactly_with_layers_and_cell_metadata(self):
        body = self.body(refs=[((2 if i % 16 + 2 < i // 16 else 0) * 256 + ((i + 3) % 256)) for i in range(256)])
        original = self.snapshot()
        original_atlas = Image.open(io.BytesIO(self.world.atlas('A')))
        original_other_map = self.world.render_map('B')
        expected = [original_atlas.getpixel(((ref // 256 % 16) * 16 + ref % 16,
                                             (ref // 256 // 16) * 16 + ref % 256 // 16)) for ref in body['pixel_patches']['0']]
        untouched = deepcopy(body)
        plan = self.world.plan_save('A', body)
        self.assertEqual(body, untouched)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(set(plan), {'data/layouts/A/map.bin', 'data/tilesets/Secondary/metatiles.bin',
                                    'data/tilesets/Secondary/attributes.bin', 'data/tilesets/Secondary/tiles.png'})
        for path in ['data/tilesets/Secondary/metatiles.bin', 'data/tilesets/Secondary/attributes.bin']:
            self.assertTrue(plan[path].startswith(original[path]))
        old_sheet = Image.open(io.BytesIO(original['data/tilesets/Secondary/tiles.png']))
        new_sheet = Image.open(io.BytesIO(plan['data/tilesets/Secondary/tiles.png']))
        self.assertEqual(old_sheet.tobytes(), new_sheet.crop((0, 0, *old_sheet.size)).tobytes())
        self.assertEqual(old_sheet.getpalette(), new_sheet.getpalette())
        transaction = SourceTransactions(self.source, self.root / 'state').commit(plan)
        self.assertTrue(transaction['id'])
        fresh = World(self.source)
        saved = fresh.get_map('A')
        self.assertEqual(saved['cells'][0] & 0xFC00, 0x3400)
        self.assertEqual(saved['cells'][1:], body['cells'][1:])
        new_id = saved['cells'][0] & 1023
        self.assertEqual(saved['tileset']['metatiles'][new_id]['attribute'], 0x1000)
        rendered = Image.open(io.BytesIO(fresh.render_map('A')))
        self.assertEqual([rendered.getpixel((x, y)) for y in range(16) for x in range(16)], expected)
        self.assertNotEqual(saved['tileset']['revision'], body['tileset_revision'])
        self.assertEqual(fresh.render_map('B'), original_other_map)

    def test_identity_and_existing_flipped_descriptors_need_no_new_assets(self):
        self.assertEqual(self.world.plan_save('A', self.body(refs=[i for i in range(256)])), {})
        body = self.body(refs=[2 * 256 + i for i in range(256)])
        plan = self.world.plan_save('A', body)
        self.assertEqual(set(plan), {'data/layouts/A/map.bin'})
        self.assertEqual(words(plan['data/layouts/A/map.bin'])[0], 0x3402)

    def test_partial_art_retains_destination_behavior_even_if_all_pixels_come_from_another_tile(self):
        body = self.body(refs=[256 + i for i in range(256)])
        plan = self.world.plan_save('A', body)
        self.assertNotIn('data/tilesets/Secondary/tiles.png', plan)
        self.assertEqual(words(plan['data/tilesets/Secondary/attributes.bin'])[-1], 0x1000)

    def test_batch_deduplicates_shared_graphics_and_allocates_distinct_metatiles_atomically(self):
        bodies = [self.body('A'), self.body('B')]
        before = self.snapshot()
        saved_bodies = deepcopy(bodies)
        plan = plan_world_batch(self.world, bodies)
        self.assertEqual(bodies, saved_bodies)
        self.assertEqual(self.snapshot(), before)
        a_id = words(plan['data/layouts/A/map.bin'])[0] & 1023
        b_id = words(plan['data/layouts/B/map.bin'])[0] & 1023
        self.assertEqual(a_id, b_id)
        self.assertEqual(len(plan['data/tilesets/Secondary/attributes.bin']), 4)
        bodies[1]['pixel_patches']['0'] = [((1 if i % 16 + i // 16 < 8 else 0) * 256 + i) for i in range(256)]
        plan = plan_world_batch(self.world, bodies)
        self.assertEqual(len(plan['data/tilesets/Secondary/attributes.bin']), 6)
        self.assertNotEqual(words(plan['data/layouts/A/map.bin'])[0], words(plan['data/layouts/B/map.bin'])[0])

    def test_invalid_or_stale_patches_abort_without_writes(self):
        before = self.snapshot()
        cases = [None, [], {'-1': [0] * 256}, {'00': [0] * 256}, {'6': [0] * 256}, {'0': [0] * 255},
                 {'0': [True] * 256}, {'0': [999 * 256] * 256}, {'0': [3 * 256] * 256}]
        for patches in cases:
            body = self.body()
            body['pixel_patches'] = patches
            with self.subTest(patches=str(patches)[:40]), self.assertRaises(ValueError):
                self.world.plan_save('A', body)
        body = self.body()
        body['tileset_revision'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'artwork changed'):
            self.world.plan_save('A', body)
        second = self.body('B')
        second['revision'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'changed'):
            plan_world_batch(self.world, [self.body(), second])
        self.assertEqual(self.snapshot(), before)

    def test_full_graphics_or_metatile_bank_rejects_without_overwriting_art(self):
        for full_graphics in (True, False):
            with self.subTest(full_graphics=full_graphics):
                if full_graphics:
                    image = Image.open(self.source / 'data/tilesets/Secondary/tiles.png')
                    bigger = Image.new('P', (8, 4096))
                    bigger.putpalette(image.getpalette())
                    self.image('data/tilesets/Secondary/tiles.png', bigger)
                else:
                    image = Image.new('P', (8, 8))
                    image.putpalette([0] * 768)
                    self.image('data/tilesets/Secondary/tiles.png', image)
                    self.write('data/tilesets/Secondary/metatiles.bin', pack_words([0] * (512 * 8)))
                    self.write('data/tilesets/Secondary/attributes.bin', pack_words([0x1000] * 512))
                self.world = World(self.source)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'no free .*slots'):
                    self.world.plan_save('A', self.body())
                self.assertEqual(self.snapshot(), before)

    def test_animation_and_door_slots_are_not_used_for_new_graphics(self):
        self.write('src/tileset_anims.c', b'AppendTilesetAnimToBuffer(frame, (BG_VRAM + TILE_OFFSET_4BPP(NUM_TILES_IN_PRIMARY + 1)), 4 * TILE_SIZE_4BPP);\n')
        plan = self.world.plan_save('A', self.body())
        new = words(plan['data/tilesets/Secondary/metatiles.bin'])[8:]
        self.assertFalse(any(513 <= value & 1023 <= 516 for value in new))
        self.assertTrue(any(value & 1023 >= 517 for value in new))
        self.assertFalse(any(value & 1023 >= 1008 for value in new))

    def test_fragmenting_animated_graphics_is_rejected_but_whole_blocks_are_kept(self):
        self.write('src/tileset_anims.c', b'AppendTilesetAnimToBuffer(frame, (BG_VRAM + TILE_OFFSET_4BPP(2)), 1 * TILE_SIZE_4BPP);\n')
        with self.assertRaisesRegex(ValueError, 'animated tile'):
            self.world.plan_save('A', self.body())
        self.assertNotIn('data/tilesets/Secondary/tiles.png', self.world.plan_save('A', self.body(refs=[256 + i for i in range(256)])))

    def test_palette_mixtures_are_never_quantized(self):
        self.palette('data/tilesets/Primary/palette.pal', [(0, 0, 0)] + [(255, 0, 0)] * 15)
        self.palette('data/tilesets/Secondary/palette.pal', [(0, 0, 0)] + [(0, 0, 255)] * 15)
        descriptors = words((self.source / 'data/tilesets/Primary/metatiles.bin').read_bytes())
        descriptors[8:12] = [0x6001] * 4
        self.write('data/tilesets/Primary/metatiles.bin', pack_words(descriptors))
        self.world = World(self.source)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'palette'):
            self.world.plan_save('A', self.body())
        self.assertEqual(self.snapshot(), before)

    def test_nonuniform_implicit_background_is_not_flattened_or_shifted(self):
        old = Image.open(self.source / 'data/tilesets/Primary/tiles.png')
        image = Image.new('P', (8, 21 * 8))
        image.putpalette(old.getpalette())
        image.paste(old, (0, 0))
        for y in range(8):
            for x in range(8):
                image.putpixel((x, 20 * 8 + y), 1 if x < 4 else 2)
        self.image('data/tilesets/Primary/tiles.png', image)
        descriptors = words((self.source / 'data/tilesets/Primary/metatiles.bin').read_bytes())
        descriptors[24:32] = [0] * 8
        self.write('data/tilesets/Primary/metatiles.bin', pack_words(descriptors))
        self.world = World(self.source)
        body = self.body(refs=[3 * 256 + ((i // 16) * 16 + (i % 16 + 1) % 16) for i in range(256)])
        body['cells'][0] = 0x3403
        with self.assertRaisesRegex(ValueError, 'background layer'):
            self.world.plan_save('A', body)


if __name__ == '__main__':
    unittest.main()
