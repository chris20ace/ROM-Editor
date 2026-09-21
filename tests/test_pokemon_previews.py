"""Party pictures use actual species tables without changing Pokémon assets."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pokemon_previews import FILES, PokemonPreviews


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"
PICTURE = "graphics/pokemon/fixture/anim_front.png"
PALETTE = "graphics/pokemon/fixture/normal.pal"


class PokemonPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(FILES[0], "SPECIES_SPRITE(PIKACHU, gActualPicture),\nSPECIES_SPRITE(ALIAS, gActualPicture),\n")
        self.write(FILES[1], "SPECIES_PAL(PIKACHU, gActualPalette),\nSPECIES_PAL(ALIAS, gActualPalette),\n")
        self.write(FILES[2], f'const u32 gActualPicture[] = INCGFX_U32("{PICTURE}", ".4bpp.lz");\n')
        self.write(FILES[3], f'const u32 gActualPalette[] = INCGFX_U32("{PALETTE}", ".gbapal.lz");\n')
        self.colors = [(13 * i, 255 - 9 * i, 7 * i) for i in range(16)]
        self.write_palette(self.colors)
        # The embedded PNG colors deliberately disagree with the game palette.
        # Four quadrants distinguish frame cropping from resizing the sheet.
        self.image = Image.new("P", (128, 128), 3)
        self.image.putpalette([value for i in range(256) for value in (i, 0, 0)])
        for y in range(64):
            for x in range(128):
                self.image.putpixel((x, y), (x + y) % 16 if x < 64 else 2)
        self.write_image()
        self.previews = PokemonPreviews(self.root)

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def write_palette(self, colors, header="JASC-PAL\n0100\n16"):
        self.write(PALETTE, header + "\n" + "\n".join(" ".join(map(str, row)) for row in colors) + "\n")

    def write_image(self):
        path = self.root / PICTURE
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def test_table_mapping_external_palette_transparency_and_first_frame(self):
        before = self.snapshot()
        self.assertEqual(self.previews.records()["SPECIES_PIKACHU"], (PICTURE, PALETTE))
        data = self.previews.preview("SPECIES_PIKACHU")
        with Image.open(io.BytesIO(data)) as rendered:
            self.assertEqual(rendered.size, (64, 64))
            self.assertEqual(rendered.mode, "RGBA")
            for x, y in ((0, 0), (1, 0), (15, 0), (63, 63), (2, 31)):
                index = (x + y) % 16
                # GBA stores five bits per channel, then previews expand them.
                expected = tuple((channel >> 3) * 255 // 31 for channel in self.colors[index])
                self.assertEqual(rendered.getpixel((x, y)), (*expected, 0 if index == 0 else 255))
        self.assertEqual(self.previews.preview("SPECIES_ALIAS"), data)
        self.assertEqual(self.snapshot(), before, "Previews must never write Pokémon assets")

    def test_unknown_ids_and_path_like_inputs_are_rejected(self):
        before = self.snapshot()
        for species in (None, True, 25, [], {}, "PIKACHU", "SPECIES_UNKNOWN", "../../outside.png",
                        "SPECIES_../../outside", "graphics/pokemon/fixture/anim_front.png"):
            with self.subTest(species=species), self.assertRaisesRegex(ValueError, "Choose a Pokémon"):
                self.previews.preview(species)
        self.assertEqual(self.snapshot(), before)

    def test_source_mapping_cannot_escape_species_graphics(self):
        for picture, palette in (("graphics/pokemon/../../outside.png", PALETTE),
                                 ("graphics/trainers/front_pics/hiker.png", PALETTE),
                                 (PICTURE, "graphics/pokemon/../../outside.pal")):
            with self.subTest(picture=picture, palette=palette):
                self.write(FILES[2], f'const u32 gActualPicture[] = INCGFX_U32("{picture}", ".4bpp.lz");\n')
                self.write(FILES[3], f'const u32 gActualPalette[] = INCGFX_U32("{palette}", ".gbapal.lz");\n')
                with self.assertRaisesRegex(ValueError, "graphics directory"):
                    self.previews.preview("SPECIES_PIKACHU")

    def test_invalid_palette_headers_colors_and_indices_are_rejected(self):
        for colors, header in ((self.colors, "JASC-PAL\n0100\n256"),
                               (self.colors[:-1], "JASC-PAL\n0100\n16"),
                               ([(-1, 0, 0)] + self.colors[1:], "JASC-PAL\n0100\n16"),
                               ([(256, 0, 0)] + self.colors[1:], "JASC-PAL\n0100\n16"),
                               ([(0, 0)] + self.colors[1:], "JASC-PAL\n0100\n16")):
            with self.subTest(colors=colors[0], header=header):
                self.write_palette(colors, header)
                with self.assertRaises(ValueError):
                    self.previews.preview("SPECIES_PIKACHU")
        self.write_palette(self.colors)
        self.image.putpixel((0, 0), 16)
        self.write_image()
        with self.assertRaisesRegex(ValueError, "palette index"):
            self.previews.preview("SPECIES_PIKACHU")

    def test_small_and_nonindexed_sprites_are_rejected(self):
        for mode, size in (("RGB", (64, 64)), ("P", (32, 64)), ("P", (64, 32))):
            with self.subTest(mode=mode, size=size):
                self.image = Image.new(mode, size)
                self.write_image()
                with self.assertRaisesRegex(ValueError, "indexed 64-pixel"):
                    self.previews.preview("SPECIES_PIKACHU")

    def test_updated_source_table_invalidates_cached_mapping(self):
        self.assertIn("SPECIES_PIKACHU", self.previews.records())
        self.write(FILES[0], "SPECIES_SPRITE(RENAMED_FIXTURE_SPECIES, gActualPicture),\n")
        self.write(FILES[1], "SPECIES_PAL(RENAMED_FIXTURE_SPECIES, gActualPalette),\n")
        self.assertEqual(self.previews.records(), {"SPECIES_RENAMED_FIXTURE_SPECIES": (PICTURE, PALETTE)})
        with self.assertRaises(ValueError):
            self.previews.preview("SPECIES_PIKACHU")

    def test_castform_generated_paths_use_the_normal_form(self):
        self.write(FILES[0], "SPECIES_SPRITE(CASTFORM, gActualPicture),\n")
        self.write(FILES[1], "SPECIES_PAL(CASTFORM, gActualPalette),\n")
        self.write(FILES[2], 'const u32 gActualPicture[] = INCGFX_U32("graphics/pokemon/castform/anim_front.4bpp", ".lz");\n')
        self.write(FILES[3], 'const u32 gActualPalette[] = INCGFX_U32("graphics/pokemon/castform/normal.gbapal", ".lz");\n')
        picture = self.root / "graphics/pokemon/castform/normal/anim_front.png"
        picture.parent.mkdir(parents=True)
        picture.write_bytes((self.root / PICTURE).read_bytes())
        self.write("graphics/pokemon/castform/normal/normal.pal", (self.root / PALETTE).read_text(encoding="utf-8"))
        self.assertEqual(self.previews.records()["SPECIES_CASTFORM"],
                         ("graphics/pokemon/castform/normal/anim_front.png", "graphics/pokemon/castform/normal/normal.pal"))
        with Image.open(io.BytesIO(self.previews.preview("SPECIES_CASTFORM"))) as image:
            self.assertEqual(image.size, (64, 64))


class RealPokemonPreviewTests(unittest.TestCase):
    def test_every_real_species_and_form_renders_without_source_changes(self):
        previews = PokemonPreviews(SOURCE)
        records = previews.records()
        self.assertGreaterEqual(len(records), 439)
        paths = set(FILES) | {path for pair in records.values() for path in pair}
        before = {path: hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() for path in paths}
        for species in records:
            with self.subTest(species=species):
                with Image.open(io.BytesIO(previews.preview(species))) as image:
                    self.assertEqual(image.size, (64, 64))
                    self.assertEqual(image.mode, "RGBA")
        after = {path: hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
