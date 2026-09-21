"""Canvas position persistence must never modify map source or partial saves."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from world_positions import WorldPositions


class WorldPositionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = WorldPositions(self.root)
        self.maps = [{'name': 'Route102'}, {'name': 'Route102_Part2'}, {'name': 'PetalburgCity'}]

    def save(self, updates, revision=None):
        if revision is None:
            revision = self.model.read(self.maps)['revision']
        return self.model.save({'revision': revision, 'positions': updates}, self.maps)

    def test_empty_read_does_not_create_files(self):
        result = self.model.read({'maps': self.maps})
        self.assertEqual(result['positions'], {})
        self.assertEqual(len(result['revision']), 64)
        self.assertFalse(self.model.path.exists())

    def test_positions_survive_reopen_and_other_updates(self):
        first = self.save({'Route102': {'x': -4, 'y': 25}})
        second = self.save({'Route102_Part2': {'x': 30, 'y': 25}})
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(second['positions'], {'Route102': {'x': -4, 'y': 25},
                                              'Route102_Part2': {'x': 30, 'y': 25}})
        self.assertEqual(WorldPositions(self.root).read(self.maps), second)
        self.assertEqual([path for path in self.root.rglob('*') if path.is_file()], [self.model.path])

    def test_stale_save_rejected_without_changing_file(self):
        old = self.model.read(self.maps)['revision']
        self.save({'Route102': {'x': 0, 'y': 4}})
        before = self.model.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.save({'PetalburgCity': {'x': 90, 'y': 1}}, old)
        self.assertEqual(self.model.path.read_bytes(), before)

    def test_validation_checks_whole_request_before_writing(self):
        self.save({'Route102': {'x': 2, 'y': 3}})
        before = self.model.path.read_bytes()
        bad_values = [True, 1.5, float('nan'), float('inf'), '5', 100001, -100001, None]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save({'Route102': {'x': 8, 'y': 9}, 'PetalburgCity': {'x': value, 'y': 2}})
            self.assertEqual(self.model.path.read_bytes(), before)
        for value in [{}, {'x': 1}, {'x': 1, 'y': 2, 'z': 3}, [1, 2]]:
            with self.subTest(position=value), self.assertRaises(ValueError):
                self.save({'Route102': value})
            self.assertEqual(self.model.path.read_bytes(), before)

    def test_unknown_map_and_missing_revision_rejected(self):
        for name in ['Missing', '../outside', '']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unknown map'):
                self.save({name: {'x': 0, 'y': 0}})
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.model.save({'positions': {}}, self.maps)
        self.assertFalse(self.model.path.exists())

    def test_map_name_mapping_is_supported(self):
        maps = {'Route102': {'id': 'MAP_ROUTE102'}, 'PetalburgCity': {'id': 'MAP_PETALBURG_CITY'}}
        initial = self.model.read(maps)
        result = self.model.save({'revision': initial['revision'], 'positions': {'Route102': {'x': 4, 'y': 9}}}, maps)
        self.assertEqual(result['positions']['Route102'], {'x': 4, 'y': 9})

    def test_null_removes_only_named_override(self):
        self.save({'Route102': {'x': 3, 'y': 5}, 'PetalburgCity': {'x': 10, 'y': 12}})
        result = self.save({'Route102': None})
        self.assertEqual(result['positions'], {'PetalburgCity': {'x': 10, 'y': 12}})

    def test_deleted_map_is_hidden_but_preserved_for_restoration(self):
        self.save({'Route102': {'x': 1, 'y': 3}})
        active_maps = ['PetalburgCity']
        result = self.model.read(active_maps)
        self.assertEqual(result['positions'], {})
        self.model.save({'revision': result['revision'], 'positions': {'PetalburgCity': {'x': 7, 'y': 8}}}, active_maps)
        self.assertEqual(self.model.read(self.maps)['positions']['Route102'], {'x': 1, 'y': 3})

    def test_corrupt_file_is_not_silently_overwritten(self):
        self.model.path.parent.mkdir(parents=True)
        for raw in [b'{', b'[]', b'{"version":true,"positions":{}}',
                    b'{"version":1,"positions":{"Route102":{"x":null,"y":1}}}']:
            self.model.path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'invalid'):
                self.save({'Route102': {'x': 1, 'y': 2}}, 'anything')
            self.assertEqual(self.model.path.read_bytes(), raw)

    def test_failed_replace_preserves_saved_file_and_cleans_temporary(self):
        self.save({'Route102': {'x': 1, 'y': 2}})
        before = self.model.path.read_bytes()
        with patch('world_positions.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.save({'Route102': {'x': 3, 'y': 4}})
        self.assertEqual(self.model.path.read_bytes(), before)
        self.assertEqual(list(self.model.path.parent.iterdir()), [self.model.path])

    def test_identical_save_preserves_revision_without_rewriting(self):
        first = self.save({'Route102': {'x': -100000, 'y': 100000}})
        with patch('world_positions.os.replace', side_effect=AssertionError('Unexpected write')):
            self.assertEqual(self.save({'Route102': {'x': -100000, 'y': 100000}}), first)
        self.assertEqual(json.loads(self.model.path.read_text())['version'], 1)


if __name__ == '__main__':
    unittest.main()
