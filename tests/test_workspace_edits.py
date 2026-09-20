from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import json
from workspace_edits import SourceTransactions, protected_species, safe_path


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.edits = SourceTransactions(self.source, self.root / 'state')
        (self.source / 'a.bin').write_bytes(b'first')
        (self.source / 'b.bin').write_bytes(b'second')

    def tearDown(self):
        self.temp.cleanup()

    def test_multi_file_save_and_undo_restore_exact_bytes(self):
        result = self.edits.commit({'a.bin': b'changed', 'b.bin': b'also changed', 'data/New/map.json': b'{}'}, 'New map')
        self.assertEqual(len(result['files']), 3)
        self.assertIn('data/New/map.json', self.edits.created())
        self.edits.undo(result['id'])
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')
        self.assertEqual((self.source / 'b.bin').read_bytes(), b'second')
        self.assertFalse((self.source / 'data/New/map.json').exists())
        self.assertFalse(self.edits.created())

    def test_partial_failure_rolls_back_preceding_files(self):
        replace = os.replace
        def failing(src, dst):
            if Path(dst).name == 'b.bin' and '.workbench-' in Path(src).name:
                raise OSError('Simulated disk failure')
            return replace(src, dst)
        with patch('workspace_edits.os.replace', side_effect=failing), self.assertRaises(OSError):
            self.edits.commit({'a.bin': b'changed', 'b.bin': b'changed'})
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')
        self.assertEqual((self.source / 'b.bin').read_bytes(), b'second')

    def test_external_change_blocks_undo(self):
        result = self.edits.commit({'a.bin': b'changed'})
        (self.source / 'a.bin').write_bytes(b'external edit')
        with self.assertRaises(ValueError):
            self.edits.undo(result['id'])
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'external edit')

    def test_protected_species_and_paths_reject_entire_plan(self):
        for path in ('../outside', '/absolute', 'src/data/pokemon/species_info.h', 'graphics/pokemon/treecko/front.png',
                     'SRC/DATA/POKEMON/SPECIES_INFO.H', 'Graphics/Pokemon/Treecko/front.png',
                     'INCLUDE/CONSTANTS/SPECIES.H', 'src/data/pokemon /species_info.h',
                     'src/data/pokemon./species_info.h', 'a.bin ', 'data/NUL.txt', 'data/bad\x00file'):
            with self.assertRaises(ValueError):
                self.edits.commit({'a.bin': b'changed', path: b'not allowed'})
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')

    def test_protection_handles_windows_path_spellings(self):
        for path in ('SRC/DATA/POKEMON/species_info.h', 'src\\data\\pokemon\\evolution.h',
                     'Graphics/Pokemon/Treecko/front.png', 'INCLUDE/CONSTANTS/SPECIES.H',
                     'src/data/pokemon_graphics/FRONT_PIC_TABLE.H'):
            self.assertTrue(protected_species(path), path)
        self.assertFalse(protected_species('src/starter_choose.c'))
        self.assertFalse(protected_species('src/data/trainers.h'))

    def test_duplicate_case_aliases_reject_entire_plan(self):
        with self.assertRaisesRegex(ValueError, 'multiple path spellings'):
            self.edits.commit({'a.bin': b'changed', 'A.BIN': b'other'})
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')

    def test_resolved_alias_cannot_write_protected_target(self):
        protected = self.source / 'src/data/pokemon/species_info.h'
        protected.parent.mkdir(parents=True)
        protected.write_bytes(b'protected')
        real_safe_path = safe_path

        def alias(source, relative, allow_new=False):
            # Models the canonical destination returned for an in-project junction,
            # symlink or Windows 8.3 alias without requiring symlink privileges.
            return protected if relative == 'data/species_alias.h' else real_safe_path(source, relative, allow_new)

        with patch('workspace_edits.safe_path', side_effect=alias), self.assertRaisesRegex(ValueError, 'protected'):
            self.edits.commit({'a.bin': b'changed', 'data/species_alias.h': b'forbidden'})
        self.assertEqual(protected.read_bytes(), b'protected')
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')

    def test_manifest_failure_after_ledger_commit_restores_absent_ledger(self):
        replace = os.replace

        def fail_committed_manifest(src, dst):
            if Path(dst).name == 'manifest.json' and json.loads(Path(src).read_bytes())['status'] == 'committed':
                self.assertIn('data/New/map.json', self.edits.created())
                raise OSError('Final manifest failure')
            return replace(src, dst)

        with patch('workspace_edits.os.replace', side_effect=fail_committed_manifest), self.assertRaises(OSError):
            self.edits.commit({'a.bin': b'changed', 'data/New/map.json': b'{}'})
        self.assertEqual((self.source / 'a.bin').read_bytes(), b'first')
        self.assertFalse((self.source / 'data/New/map.json').exists())
        self.assertFalse(self.edits.created_path.exists())
        self.assertEqual(self.edits.list_transactions(), [])
        self.assertFalse(list(self.root.rglob('*.tmp')))
        manifests = list((self.root / 'state/transactions').glob('*/manifest.json'))
        self.assertEqual(json.loads(manifests[0].read_bytes())['status'], 'rolled_back')

    def test_manifest_failure_restores_existing_ledger_after_delete_and_create(self):
        self.edits.commit({'data/Existing/file.bin': b'created earlier'})
        ledger_before = self.edits.created_path.read_bytes()
        replace = os.replace

        def fail_committed_manifest(src, dst):
            if Path(dst).name == 'manifest.json' and json.loads(Path(src).read_bytes())['status'] == 'committed':
                self.assertEqual(self.edits.created(), {'data/New/file.bin'})
                raise OSError('Final manifest failure')
            return replace(src, dst)

        with patch('workspace_edits.os.replace', side_effect=fail_committed_manifest), self.assertRaises(OSError):
            self.edits.commit({'data/Existing/file.bin': None, 'data/New/file.bin': b'new'})
        self.assertEqual(self.edits.created_path.read_bytes(), ledger_before)
        self.assertEqual((self.source / 'data/Existing/file.bin').read_bytes(), b'created earlier')
        self.assertFalse((self.source / 'data/New/file.bin').exists())
        self.assertEqual(len(self.edits.list_transactions()), 1)
        self.assertFalse(list(self.root.rglob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
