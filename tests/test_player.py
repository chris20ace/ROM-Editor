"""Player artwork round trips, actual palette bindings and protected scope."""
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from player import Player, METADATA, PLAYER_PNGS, PLAYER_PALETTES, _engine


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'
WALK = 'graphics/object_events/pics/people/brendan/walking.png'
RUN = 'graphics/object_events/pics/people/brendan/running.png'
PAL = 'graphics/object_events/palettes/brendan.pal'
REFLECTION = 'graphics/object_events/palettes/brendan_reflection.pal'
FRONT = 'graphics/trainers/front_pics/brendan.png'
BACK = 'graphics/trainers/back_pics/brendan.png'
ICON = 'graphics/pokenav/region_map/brendan_icon.png'


def make_player_fixture(source):
    """Small source-shaped project reused by HTTP integration tests."""
    source = Path(source)
    def write(path, data):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding='utf-8')

    def png(path, width, height):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new('P', (width, height))
        image.putpalette([channel for i in range(16) for channel in (i * 16, 255 - i * 16, i * 8)])
        image.putdata([i % 16 for i in range(width * height)])
        image.save(target, bits=4)

    graphics, tables, infos = [], [], []
    for form in ('Walking', 'Running'):
        symbol = 'gObjectEventPic_Brendan' + form
        path = WALK if form == 'Walking' else RUN
        png(path, 32, 32)
        graphics.append(f'const u32 {symbol}[] = INCGFX_U32("{path}", ".4bpp", "-mwidth 2 -mheight 4");')
        tables.append(f'static const struct SpriteFrameImage sPicTable_Brendan{form}[] = {{overworld_frame({symbol}, 2, 4, 0), overworld_frame({symbol}, 2, 4, 1)}};')
        infos.append(f'const struct ObjectEventGraphicsInfo gInfo_Brendan{form} = {{.paletteTag = OBJ_EVENT_PAL_TAG_BRENDAN, .images = sPicTable_Brendan{form}, .width = 16, .height = 32, .disableReflectionPaletteLoad = FALSE,}};')
    for symbol, path, reverse in (('gObjectEventPal_Brendan', PAL, False),
                                  ('gObjectEventPal_BrendanReflection', REFLECTION, True)):
        graphics.append(f'const u16 {symbol}[] = INCGFX_U16("{path}", ".gbapal");')
        colors = [f'{i * 8} {255 - i * 8} {i * 16}' for i in range(16)]
        if reverse:
            colors.reverse()
        write(path, '\n'.join(['JASC-PAL', '0100', '16'] + colors) + '\n')
    write(METADATA[0], '\n'.join(graphics))
    write(METADATA[1], '\n'.join(infos))
    write(METADATA[2], '\n'.join(tables))
    write('src/event_object_movement.c', '''
const struct SpritePalette palettes[] = {
 {gObjectEventPal_Brendan, OBJ_EVENT_PAL_TAG_BRENDAN},
 {gObjectEventPal_BrendanReflection, OBJ_EVENT_PAL_TAG_BRENDAN_REFLECTION},
};
static const u16 sReflectionPaletteTags_Brendan[] = {OBJ_EVENT_PAL_TAG_BRENDAN_REFLECTION};
static const struct PairedPalettes pairs[] = {{OBJ_EVENT_PAL_TAG_BRENDAN, sReflectionPaletteTags_Brendan}};
''')
    png(FRONT, 64, 64)
    png(BACK, 64, 256)
    trainer_pal = 'graphics/trainers/palettes/brendan.pal'
    write(trainer_pal, (source / PAL).read_text())
    write('src/data/graphics/trainers.h', f'''
const u32 gTrainerFrontPic_Brendan[] = INCGFX_U32("{FRONT}", ".4bpp.lz");
const u8 gTrainerBackPic_Brendan[] = INCGFX_U8("{BACK}", ".4bpp");
const u32 gTrainerPalette_Brendan[] = INCGFX_U32("{trainer_pal}", ".gbapal.lz");
''')
    write('src/data/trainer_graphics/front_pic_tables.h', 'TRAINER_SPRITE(BRENDAN, gTrainerFrontPic_Brendan, TRAINER_PIC_SIZE),\nTRAINER_PAL(BRENDAN, gTrainerPalette_Brendan),')
    write('src/data/trainer_graphics/back_pic_tables.h', '.data = gTrainerBackPic_Brendan,\nTRAINER_BACK_PAL(BRENDAN, gTrainerPalette_Brendan),')
    png(ICON, 16, 16)
    write('src/region_map.c', f'''
const u8 sRegionMapPlayerIcon_BrendanGfx[] = INCGFX_U8("{ICON}", ".4bpp");
const u16 sRegionMapPlayerIcon_BrendanPal[] = INCGFX_U16("{ICON}", ".gbapal");
''')
    write('rom.sha1', '0' * 40 + ' *pokeemerald.gba\n')


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        make_player_fixture(self.source)
        self.player = Player(self.source)

    def body(self, path=WALK, palette_path=None):
        asset = self.player.get_asset(path, palette_path)
        return {key: asset[key] for key in ('path', 'palette_path', 'revision', 'pixels', 'palette')}

    def apply(self, plan):
        for path, data in plan.items():
            (self.source / path).write_bytes(data)

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def test_catalog_frames_palette_binding_and_preview_use_engine_colors(self):
        catalog = self.player.catalog()
        self.assertEqual(catalog['asset_count'], 5)
        asset = self.player.get_asset(WALK)
        self.assertEqual(asset['frame_count'], 2)
        self.assertEqual(asset['frames'][1]['x'], 16)
        self.assertEqual(asset['palette_path'], PAL)
        self.assertNotEqual(asset['palette'], self.player.get_asset(ICON)['palette'])
        self.assertEqual(asset['palette_options'][1]['path'], REFLECTION)
        self.assertEqual(asset['palette_shared_with'][0]['path'], RUN)
        image = Image.open(io.BytesIO(self.player.preview(WALK)))
        self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((1, 0)), (*_engine((8, 247, 16)), 255))

    def test_pixel_plan_preserves_exact_indices_palette_dimensions_and_transparency(self):
        body = self.body()
        body['pixels'][0] = 7
        body['pixels'][-1] = 0
        before = self.snapshot()
        plan = self.player.plan_save(body)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(set(plan), {WALK})
        image = Image.open(io.BytesIO(plan[WALK]))
        original = Image.open(io.BytesIO(before[WALK]))
        self.assertEqual(image.mode, 'P')
        self.assertEqual(image.size, original.size)
        self.assertEqual(list(image.tobytes()), body['pixels'])
        self.assertEqual(image.getpalette(), original.getpalette())
        self.assertEqual(image.info['transparency'], 0)

    def test_shared_external_palette_confirmation_and_atomic_pixel_palette_plan(self):
        body = self.body()
        body['pixels'][0] = 4
        body['palette'][5] = '#12ABF0'
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.player.plan_save(body)
        body['confirm_shared'] = True
        plan = self.player.plan_save(body)
        self.assertEqual(set(plan), {WALK, PAL})
        self.apply(plan)
        self.assertEqual(self.player.get_asset(RUN)['palette'][5], '#12ABF0')
        self.assertEqual(self.player.get_asset(WALK)['pixels'][0], 4)

    def test_palette_only_changes_external_palette_not_png_or_reflection(self):
        body = self.body(palette_path=REFLECTION)
        body['palette'][2] = '#ABCDEF'
        body['confirm_shared'] = True
        self.assertEqual(set(self.player.plan_save(body)), {REFLECTION})

    def test_embedded_palette_and_pixels_roundtrip_without_quantization(self):
        body = self.body(ICON)
        body['palette'][15] = '#123456'
        body['pixels'][10] = 15
        plan = self.player.plan_save(body)
        self.assertEqual(set(plan), {ICON})
        self.apply(plan)
        result = self.player.get_asset(ICON)
        self.assertEqual(result['pixels'], body['pixels'])
        self.assertEqual(result['palette'], body['palette'])
        self.assertEqual(result['engine_palette'][15], '#103152')

    def test_noop_is_empty_plan_and_read_only(self):
        before = self.snapshot()
        self.player.catalog()
        for path in (WALK, RUN, FRONT, BACK, ICON):
            self.player.preview(path)
            self.assertEqual(self.player.plan_save(self.body(path)), {})
        self.assertEqual(before, self.snapshot())

    def test_stale_pixel_shared_palette_alternate_palette_and_metadata_rejected(self):
        for relative in (WALK, RUN, PAL, REFLECTION, 'src/event_object_movement.c'):
            with self.subTest(relative=relative):
                body = self.body()
                path = self.source / relative
                original = path.read_bytes()
                path.write_bytes(original + (b'\n' if not relative.endswith('.png') else b'changed'))
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.player.plan_save(body)
                path.write_bytes(original)

    def test_rejects_paths_palettes_pixels_alpha_and_dimensions(self):
        for invalid in ('graphics/pokemon/pikachu/front.png', '../outside.png', WALK.upper(), WALK.replace('/', '\\')):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                self.player.get_asset(invalid)
        for changes in ({'palette_path': 'graphics/pokemon/pikachu/normal.pal'},
                        {'palette_path': [PAL]},
                        {'width': 16}, {'height': 16}, {'transparent_index': 1},
                        {'pixels': [0]}, {'palette': ['#FFFFFF'] * 17},
                        {'palette': ['#FFFFFF00'] * 16}):
            with self.subTest(changes=str(changes)[:60]), self.assertRaises(ValueError):
                self.player.plan_save({**self.body(), **changes})
        for index in (-1, 16, True, 1.5, '2'):
            body = self.body()
            body['pixels'][0] = index
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.player.plan_save(body)

    def test_rejects_metadata_redirect_to_pokemon_asset(self):
        header = self.source / METADATA[0]
        header.write_text(header.read_text().replace(WALK, 'graphics/pokemon/pikachu/front.png'))
        with self.assertRaises(ValueError):
            self.player.get_asset(WALK)

    def test_trainer_front_back_share_actual_external_palette_and_four_back_frames(self):
        front = self.player.get_asset(FRONT)
        back = self.player.get_asset(BACK)
        self.assertEqual(front['palette_path'], back['palette_path'])
        self.assertEqual(back['frame_count'], 4)
        self.assertEqual(back['frames'][3], {'index': 3, 'x': 0, 'y': 192, 'width': 64, 'height': 64, 'label': 'Frame 4'})
        self.assertIn(FRONT, [a['path'] for a in back['palette_shared_with']])


@unittest.skipUnless(SOURCE.is_dir(), 'Real Emerald source is not present')
class RealPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.player = Player(SOURCE)
        cls.catalog = cls.player.catalog()

    def test_all_named_actual_player_pngs_and_every_frame_are_editable_read_only(self):
        self.assertEqual({a['path'] for a in self.catalog['assets']}, PLAYER_PNGS)
        self.assertEqual(self.catalog['asset_count'], 39)
        for row in self.catalog['assets']:
            with self.subTest(path=row['path']):
                asset = self.player.get_asset(row['path'])
                self.assertEqual(asset['frame_count'] * asset['frame_width'] * asset['frame_height'], len(asset['pixels']))
                self.assertEqual(self.player.plan_save(asset), {})
                before = (SOURCE / asset['path']).read_bytes()
                asset['pixels'][0] = (asset['pixels'][0] + 1) % 16
                plan = self.player.plan_save(asset)
                image = Image.open(io.BytesIO(plan[asset['path']]))
                self.assertEqual(list(image.tobytes()), asset['pixels'])
                self.assertEqual((SOURCE / asset['path']).read_bytes(), before)
                for palette in row['palette_options']:
                    self.assertIn(palette['path'], PLAYER_PALETTES)
                    preview = Image.open(io.BytesIO(self.player.preview(row['path'], palette['path'])))
                    self.assertEqual(preview.size, (asset['width'], asset['height']))

    def test_real_shared_underwater_reflections_battle_palettes_and_link_variant(self):
        brendan = self.player.get_asset('graphics/object_events/pics/people/brendan/underwater.png')
        may = self.player.get_asset('graphics/object_events/pics/people/may/underwater.png')
        self.assertEqual(brendan['palette_path'], 'graphics/object_events/palettes/player_underwater.pal')
        self.assertEqual(brendan['palette_path'], may['palette_path'])
        self.assertIn(may['path'], [x['path'] for x in brendan['palette_shared_with']])
        walking = self.player.get_asset(WALK)
        self.assertEqual({p['label'] for p in walking['palette_options']},
                         {'Normal', 'Reflection', 'Link room palette', 'Link room reflection'})
        for name in ('brendan', 'may', 'brendan_rs', 'may_rs'):
            front = self.player.get_asset(f'graphics/trainers/front_pics/{name}.png')
            back = self.player.get_asset(f'graphics/trainers/back_pics/{name}.png')
            self.assertEqual(front['palette_path'], back['palette_path'])

    def test_credits_palette_change_from_bicycle_preserves_credits_pixels(self):
        bicycle = 'graphics/intro/scene_2/bicycle.png'
        credits = 'graphics/intro/scene_2/brendan_credits.png'
        asset = self.player.get_asset(bicycle, credits)
        original = Image.open(SOURCE / credits)
        asset['palette'][1] = '#AAAAAA'
        asset['confirm_shared'] = True
        plan = self.player.plan_save(asset)
        self.assertEqual(set(plan), {credits})
        self.assertEqual(Image.open(io.BytesIO(plan[credits])).tobytes(), original.tobytes())


if __name__ == '__main__':
    unittest.main()
