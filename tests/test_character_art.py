"""Character discovery, indexed saves, shared identities and Pokémon protection."""
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from PIL import Image

from character_art import CharacterArt, CHARACTER_METADATA
from player import Player
from tests.test_player import make_player_fixture, WALK


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'
HIKER = 'graphics/trainers/front_pics/hiker.png'
ROXANNE = 'graphics/trainers/front_pics/roxanne.png'
STEVEN = 'graphics/trainers/front_pics/steven.png'
STEVEN_BACK = 'graphics/trainers/back_pics/steven.png'
NPC = 'graphics/object_events/pics/people/boy_1.png'
NPC_TWO = 'graphics/object_events/pics/people/girl_1.png'
POKEMON = 'graphics/object_events/pics/pokemon/pikachu.png'
NPC_PAL = 'graphics/object_events/palettes/npc_1.pal'
NPC_REFLECTION = 'graphics/object_events/palettes/npc_1_reflection.pal'
UNUSED = 'graphics/object_events/pics/people/unused_woman.png'


def make_character_fixture(source):
    source = Path(source)
    make_player_fixture(source)

    def write(path, text, append=False):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((target.read_text(encoding='utf-8') if append and target.exists() else '') + text, encoding='utf-8')

    def png(path, size):
        image = Image.new('P', size)
        image.putpalette([value for i in range(16) for value in (i * 16, i * 8, 255 - i * 16)])
        image.putdata([i % 16 for i in range(size[0] * size[1])])
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, bits=4)

    for symbol, path, size in (('Hiker', HIKER, (64, 64)), ('Roxanne', ROXANNE, (64, 64)), ('Steven', STEVEN, (64, 64))):
        png(path, size)
        palette = f'graphics/trainers/palettes/{symbol.lower()}.pal'
        if symbol == 'Steven':
            palette = path
        else:
            write(palette, (source / 'graphics/object_events/palettes/brendan.pal').read_text())
        write('src/data/graphics/trainers.h', f'\nconst u32 gTrainerFrontPic_{symbol}[] = INCGFX_U32("{path}", ".4bpp.lz");\nconst u32 gTrainerPalette_{symbol}[] = INCGFX_U32("{palette}", ".gbapal.lz");\n', True)
        ident = 'LEADER_ROXANNE' if symbol == 'Roxanne' else symbol.upper()
        write('src/data/trainer_graphics/front_pic_tables.h', f'\nTRAINER_SPRITE({ident}, gTrainerFrontPic_{symbol}, TRAINER_PIC_SIZE),\nTRAINER_PAL({ident}, gTrainerPalette_{symbol}),\n', True)
    png(STEVEN_BACK, (64, 256))
    write('src/data/graphics/trainers.h', f'const u8 gTrainerBackPic_Steven[] = INCGFX_U8("{STEVEN_BACK}", ".4bpp");\n', True)
    write('src/data/trainer_graphics/back_pic_tables.h', '\n[TRAINER_BACK_PIC_STEVEN] = {.data = (const u32 *)gTrainerBackPic_Steven, .tag = TRAINER_BACK_PIC_STEVEN},\nTRAINER_BACK_PAL(STEVEN, gTrainerPalette_Steven),\n', True)
    write('src/data/trainers.h', '''
[TRAINER_HIKER_ONE] = {.trainerPic = TRAINER_PIC_HIKER, .trainerName = _("ONE"), .party = {0}},
[TRAINER_HIKER_TWO] = {.trainerPic = TRAINER_PIC_HIKER, .trainerName = _("TWO"), .party = {0}},
[TRAINER_ROXANNE] = {.trainerPic = TRAINER_PIC_LEADER_ROXANNE, .trainerName = _("ROXANNE")},
''')
    for symbol, path in (('Boy', NPC), ('Girl', NPC_TWO), ('Pikachu', POKEMON)):
        png(path, (32, 32))
        write(CHARACTER_METADATA[0], f'\nconst u32 gPic_{symbol}[] = INCGFX_U32("{path}", ".4bpp", "-mwidth 2 -mheight 4");\n', True)
        write(CHARACTER_METADATA[1], f'\nconst struct ObjectEventGraphicsInfo gInfo_{symbol} = {{.paletteTag = OBJ_EVENT_PAL_TAG_NPC_1, .images = sPic_{symbol}, .width = 16, .height = 32, .disableReflectionPaletteLoad = FALSE,}};\n', True)
        write(CHARACTER_METADATA[2], f'\nstatic const struct SpriteFrameImage sPic_{symbol}[] = {{overworld_frame(gPic_{symbol}, 2, 4, 0), overworld_frame(gPic_{symbol}, 2, 4, 1)}};\n', True)
        write('src/data/object_events/object_event_graphics_info_pointers.h', f'[OBJ_EVENT_GFX_{symbol.upper()}] = &gInfo_{symbol},\n', True)
    for symbol, path, tag in (('Npc1', NPC_PAL, 'NPC_1'), ('Npc1Reflection', NPC_REFLECTION, 'NPC_1_REFLECTION')):
        write(path, (source / 'graphics/object_events/palettes/brendan.pal').read_text())
        write(CHARACTER_METADATA[0], f'const u16 gPal_{symbol}[] = INCGFX_U16("{path}", ".gbapal");\n', True)
        write('src/event_object_movement.c', f'{{gPal_{symbol}, OBJ_EVENT_PAL_TAG_{tag}}},\n', True)
    png(UNUSED, (32, 32))
    write(CHARACTER_METADATA[0], f'const u32 gPic_UnusedWoman[] = INCGFX_U32("{UNUSED}", ".4bpp", "-mwidth 2 -mheight 4");\n', True)
    write('data/maps/TestTown/map.json', json.dumps({'object_events': [{'local_id': 'LOCAL_ID_CHILD', 'graphics_id': 'OBJ_EVENT_GFX_BOY', 'x': 3, 'y': 5, 'script': 'Child_Script'}]}))


class CharacterArtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        make_character_fixture(self.source)
        self.art = CharacterArt(self.source)

    def body(self, path, palette_path=None):
        asset = self.art.get_asset(path, palette_path)
        return {key: asset[key] for key in ('path', 'palette_path', 'revision', 'pixels', 'palette')}

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def test_catalog_retains_players_and_discovers_every_mapped_character_and_stored_sheet(self):
        catalog = self.art.catalog()
        rows = {row['path']: row for row in catalog['assets']}
        self.assertTrue(set(Player(self.source)._records()).issubset(rows))
        self.assertEqual(rows[ROXANNE]['category'], 'gyms')
        self.assertEqual(rows[NPC]['category'], 'npcs')
        self.assertEqual(rows[HIKER]['category'], 'trainers')
        self.assertEqual(rows[WALK]['category'], 'players')
        self.assertEqual(rows[HIKER]['usage_count'], 2)
        self.assertEqual(rows[HIKER]['trainer_uses'][1]['id'], 'TRAINER_HIKER_TWO')
        self.assertEqual(rows[NPC]['usage_count'], 1)
        self.assertEqual(rows[NPC]['map_uses'][0]['map'], 'TestTown')
        self.assertEqual(rows[NPC]['object_graphics_ids'], ['OBJ_EVENT_GFX_BOY'])
        self.assertEqual(rows[STEVEN_BACK]['frame_count'], 4)
        self.assertEqual(rows[STEVEN_BACK]['palette_path'], STEVEN)
        self.assertTrue(rows[UNUSED]['archived'])
        self.assertNotIn(POKEMON, rows)

    def test_picture_choices_use_actual_palette_and_revision_urls(self):
        choices = {row['id']: row for row in self.art.trainer_pictures()}
        hiker = choices['TRAINER_PIC_HIKER']
        self.assertEqual(hiker['path'], HIKER)
        self.assertIn('/api/appearance/preview?', hiker['preview_url'])
        self.assertIn('palette_path=', hiker['preview_url'])
        self.assertIn('/player?path=', hiker['edit_url'])
        self.assertEqual(choices['TRAINER_PIC_STEVEN']['palette_path'], STEVEN)
        old = hiker['preview_url']
        (self.source / HIKER).write_bytes((self.source / HIKER).read_bytes() + b'changed')
        self.assertNotEqual(old, {row['id']: row for row in self.art.trainer_pictures()}['TRAINER_PIC_HIKER']['preview_url'])

    def test_missing_character_tables_give_empty_catalog_and_choices(self):
        with tempfile.TemporaryDirectory() as empty:
            art = CharacterArt(empty)
            self.assertEqual(art.trainer_pictures(), [])
            self.assertEqual(art.catalog()['assets'], [])

    def test_plan_is_read_only_and_pixel_save_retains_indices_dimensions_and_other_files(self):
        before = self.snapshot()
        body = self.body(HIKER)
        body['pixels'][12] = 15
        plan = self.art.plan_save(body)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(set(plan), {HIKER})
        image = Image.open(io.BytesIO(plan[HIKER]))
        self.assertEqual(image.mode, 'P')
        self.assertEqual(image.size, (64, 64))
        self.assertEqual(list(image.tobytes()), body['pixels'])
        self.assertEqual(image.info['transparency'], 0)

    def test_pokemon_palette_is_locked_but_character_pixels_remain_editable(self):
        asset = self.art.get_asset(NPC)
        self.assertTrue(asset['palette_locked'])
        self.assertTrue(any(row.get('protected') and row['path'] == POKEMON for row in asset['palette_shared_with']))
        body = self.body(NPC)
        body['pixels'][0] = 12
        self.assertEqual(set(self.art.plan_save(body)), {NPC})
        for path in (NPC_PAL, NPC_REFLECTION):
            with self.subTest(path=path):
                body = self.body(NPC, path)
                body['palette'][2] = '#ABCDEF'
                body['confirm_shared'] = True
                with self.assertRaisesRegex(ValueError, 'Pokémon'):
                    self.art.plan_save(body)

    def test_embedded_front_palette_shared_with_battle_back_requires_confirmation(self):
        body = self.body(STEVEN_BACK)
        body['palette'][2] = '#ABCDEF'
        body['pixels'][0] = 8
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.art.plan_save(body)
        body['confirm_shared'] = True
        plan = self.art.plan_save(body)
        self.assertEqual(set(plan), {STEVEN, STEVEN_BACK})
        front_before = Image.open(self.source / STEVEN).tobytes()
        self.assertEqual(Image.open(io.BytesIO(plan[STEVEN])).tobytes(), front_before)

    def test_changed_metadata_shared_sheet_or_palette_prevents_stale_save(self):
        for path in (NPC_TWO, NPC_PAL, NPC_REFLECTION, POKEMON, 'src/data/trainers.h'):
            with self.subTest(path=path):
                body = self.body(NPC)
                file = self.source / path
                old = file.read_bytes()
                file.write_bytes(old + b'\n')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.art.plan_save(body)
                file.write_bytes(old)

    def test_protected_unlisted_paths_and_invalid_pixels_rejected(self):
        for path in (POKEMON, 'graphics/pokemon/pikachu/front.png', '../outside.png', 'src/data/trainers.h'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.art.get_asset(path)
        for value in (16, -1, True, 2.5):
            body = self.body(HIKER)
            body['pixels'][0] = value
            with self.assertRaises(ValueError):
                self.art.plan_save(body)

    def test_noop_and_preview_are_read_only_and_palette_correct(self):
        before = self.snapshot()
        for path in (HIKER, ROXANNE, STEVEN_BACK, NPC, UNUSED, WALK):
            with self.subTest(path=path):
                body = self.body(path)
                self.assertEqual(self.art.plan_save(body), {})
                image = Image.open(io.BytesIO(self.art.preview(path)))
                asset = self.art.get_asset(path)
                self.assertEqual(image.size, (asset['width'], asset['height']))
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                self.assertEqual(image.getpixel((1, 0)), tuple(asset['palette_rgba'][1]))
        self.assertEqual(before, self.snapshot())

    def test_discovery_cache_handles_new_or_removed_files(self):
        self.art.catalog()
        image = self.source / HIKER
        contents = image.read_bytes()
        image.unlink()
        self.assertNotIn(HIKER, CharacterArt(self.source)._records())
        image.write_bytes(contents)
        self.assertIn(HIKER, CharacterArt(self.source)._records())


@unittest.skipUnless((SOURCE / 'src/data/graphics/trainers.h').exists(), 'game source not installed')
class InstalledCharacterArtTests(unittest.TestCase):
    def test_all_trainer_ids_back_sheets_and_human_sheets_discovered(self):
        art = CharacterArt(SOURCE)
        records = art._records()
        front_text = (SOURCE / 'src/data/trainer_graphics/front_pic_tables.h').read_text()
        ids = {'TRAINER_PIC_' + identifier for identifier in re.findall(r'TRAINER_SPRITE\((\w+),\s*gTrainer', front_text)}
        self.assertEqual(ids, {p['id'] for p in art.trainer_pictures()})
        for folder in ('graphics/trainers/back_pics', 'graphics/trainers/front_pics', 'graphics/object_events/pics/people'):
            expected = {path.relative_to(SOURCE).as_posix() for path in (SOURCE / folder).rglob('*.png')}
            self.assertTrue(expected.issubset(records), expected - records.keys())
        self.assertTrue(set(Player(SOURCE)._records()).issubset(records))
        self.assertFalse(any('/pokemon/' in path for path in records))
        for record in records.values():
            with self.subTest(path=record['path']):
                row = art._row(record)
                self.assertGreater(row['frame_count'], 0)
                self.assertEqual(row['width'] % row['frame_width'], 0)
                self.assertEqual(len(art._palette(row['palette_path'])), 16)


if __name__ == '__main__':
    unittest.main()
