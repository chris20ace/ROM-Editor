from pathlib import Path
import io
import unittest
from PIL import Image
from region import Region


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).resolve().parents[1] / 'source/pokeemerald'
        self.region = Region(self.source)

    def test_original_shape_and_one_tile_edit(self):
        data = self.region.get()
        self.assertEqual(len(data['cells']), 4096)
        self.assertEqual(len(data['section_cells']), 420)
        data['cells'][1000] = (data['cells'][1000] + 1) % 233
        plan = self.region.plan_save(data)
        original = (self.source / Region.TILES).read_bytes()
        self.assertEqual(sum(a != b for a, b in zip(original, plan[Region.TILES])), 1)
        self.assertEqual(plan[Region.LAYOUT], (self.source / Region.LAYOUT).read_bytes())
        self.assertEqual(plan[Region.SECTIONS], (self.source / Region.SECTIONS).read_bytes())
        self.assertTrue(self.region.atlas().startswith(b'\x89PNG'))

    def test_artwork_uses_background_palette_slots(self):
        original = Image.open(self.source / Region.IMAGE)
        rendered = Image.open(io.BytesIO(self.region.atlas()))
        position = next((x, y) for y in range(original.height) for x in range(original.width)
                        if original.getpixel((x, y)) == 113)
        colors = (self.source / Region.PALETTE).read_text().splitlines()
        self.assertEqual(rendered.getpixel(position), tuple(map(int, colors[4].split())))
        self.assertGreater(len(rendered.getcolors(256)), 10)

    def test_invalid_ids_lengths_and_revision_rejected(self):
        for key, value in [('revision', 'stale'), ('cells', [999] * 4096), ('section_cells', ['NOT_A_SECTION'] * 420)]:
            data = self.region.get()
            data[key] = value
            with self.assertRaises(ValueError):
                self.region.plan_save(data)


if __name__ == '__main__':
    unittest.main()
