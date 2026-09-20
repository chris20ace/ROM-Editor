import hashlib
import http.client
import json
import io
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import zipfile

from server import Conflict, Handler, Project, ThreadingHTTPServer


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source' / 'pokeemerald'
        self.source.mkdir(parents=True)
        (self.source / 'rom.sha1').write_text('example')
        (self.source / 'sample.json').write_bytes(b'{"value": 1}\n')
        (self.source / 'sample.c').write_bytes(b'int hp = 40;\r\n')
        (self.source / 'map.bin').write_bytes(b'\x00\x01\xff')
        self.project = Project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_restore_and_patch_preserve_original(self):
        first = self.project.file('sample.c')
        second = self.project.write('sample.c', 'int hp = 45;\r\n', first['sha256'])
        self.assertEqual((self.project.originals / 'sample.c').read_bytes(), b'int hp = 40;\r\n')
        self.assertIn('+int hp = 45;', self.project.patch())
        self.assertEqual(self.project.changes()['count'], 1)
        third = self.project.write('sample.c', 'int hp = 46;\r\n', second['sha256'])
        self.assertEqual((self.project.originals / 'sample.c').read_bytes(), b'int hp = 40;\r\n')
        self.project.write('sample.c', None, third['sha256'], restore=True)
        self.assertEqual((self.source / 'sample.c').read_bytes(), b'int hp = 40;\r\n')
        self.assertEqual(self.project.changes()['count'], 0)
        self.assertEqual(len(list((self.project.state / 'versions').rglob('sample.c'))), 3)

    def test_conflict_does_not_overwrite_external_edit(self):
        first = self.project.file('sample.c')
        (self.source / 'sample.c').write_bytes(b'int hp = 99;\n')
        with self.assertRaises(Conflict):
            self.project.write('sample.c', 'int hp = 50;', first['sha256'])
        self.assertEqual((self.source / 'sample.c').read_bytes(), b'int hp = 99;\n')

    def test_rejects_invalid_json_without_backup_or_write(self):
        first = self.project.file('sample.json')
        with self.assertRaises(ValueError):
            self.project.write('sample.json', '{not json}', first['sha256'])
        self.assertEqual((self.source / 'sample.json').read_bytes(), b'{"value": 1}\n')
        self.assertFalse(self.project.originals.exists())

    def test_rejects_paths_outside_source_and_binary_edits(self):
        for path in ('../rom-report.json', '/etc/passwd', 'C:/Windows/win.ini', 'sample.c/../sample.json'):
            with self.assertRaises(ValueError):
                self.project.file(path)
        with self.assertRaises(ValueError):
            self.project.write('map.bin', 'oops', self.project.file('map.bin')['sha256'])

    def test_unsupported_record_source_does_not_break_overview(self):
        # This fixture deliberately has no C record tables, like an incompatible edit.
        overview = self.project.overview()
        self.assertIsNone(overview['counts']['pokemon'])
        self.assertFalse(overview['editors']['pokemon']['ready'])
        self.assertEqual(overview['counts']['files'], 4)

    def test_deleted_tracked_file_does_not_block_changes_or_patch(self):
        first = self.project.file('sample.c')
        self.project.write('sample.c', 'int hp = 45;\n', first['sha256'])
        (self.source / 'sample.c').unlink()
        changes = self.project.changes()
        self.assertEqual(changes['count'], 1)
        self.assertTrue(changes['files'][0]['deleted'])
        self.assertIn('+++ /dev/null', self.project.patch())

    def test_export_includes_binary_and_created_files_and_undo_removes_changes(self):
        result = self.project.commit({'map.bin': b'\x01\x02\xff', 'data/new.inc': b'New_MapScripts::\n\t.byte 0\n'}, 'Test map')
        with zipfile.ZipFile(io.BytesIO(self.project.export())) as archive:
            self.assertEqual(archive.read('source/map.bin'), b'\x01\x02\xff')
            self.assertEqual(archive.read('source/data/new.inc'), b'New_MapScripts::\n\t.byte 0\n')
            self.assertEqual(len(json.loads(archive.read('changes.json'))['files']), 2)
            self.assertNotIn('source/sample.c', archive.namelist())
        self.assertIn('--- /dev/null', self.project.patch())
        undone = self.project.transactions.undo(result['id'])
        self.project.refresh(undone['files'])
        self.assertEqual(self.project.changes()['count'], 0)
        self.assertFalse((self.source / 'data/new.inc').exists())

    def test_source_edit_keeps_new_map_file_classified_as_created(self):
        self.project.commit({'data/new.inc': b'New_MapScripts::\n\t.byte 0\n'}, 'New map')
        first = self.project.file('data/new.inc')
        self.project.write('data/new.inc', first['content'] + '\n@ New conversation\n', first['sha256'])
        self.assertTrue(self.project.changes()['files'][0]['created'])
        self.assertFalse((self.project.originals / 'data/new.inc').exists())
        self.assertIn('--- /dev/null', self.project.patch())

    def test_pokemon_species_edits_are_protected(self):
        original_source = Path(__file__).resolve().parents[1] / 'source' / 'pokeemerald'
        for rel in ('src/data/pokemon/species_info.h', 'include/constants/pokemon.h'):
            target = self.source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original_source / rel, target)
        project = Project(self.root)
        record = project.records.get_record('pokemon', 'SPECIES_TREECKO')
        original_bytes = (self.source / record['source']).read_bytes()
        with self.assertRaises(ValueError):
            project.update_record({'kind': 'pokemon', 'id': 'SPECIES_TREECKO',
                                   'fields': {'baseHP': 45}, 'expected_sha256': record['sha256']})
        with self.assertRaises(ValueError):
            project.write(record['source'], 'bad', project.file(record['source'])['sha256'])
        self.assertEqual(project.changes()['count'], 0)
        self.assertEqual((self.source / record['source']).read_bytes(), original_bytes)

    def test_http_write_requires_token_and_same_origin(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.project = self.project
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(method, route, payload=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                body = json.dumps(payload) if payload is not None else None
                conn.request(method, route, body, headers or {})
                response = conn.getresponse()
                result = response.status, json.loads(response.read())
                conn.close()
                return result
            status, session = request('GET', '/api/session')
            self.assertEqual(status, 200)
            data = {'path': 'sample.json', 'content': '{"value": 2}', 'expected_sha256': self.project.file('sample.json')['sha256']}
            headers = {'Content-Type': 'application/json'}
            self.assertEqual(request('POST', '/api/file', data, headers)[0], 403)
            headers['X-Workbench-Token'] = session['token']
            headers['Origin'] = 'https://example.com'
            self.assertEqual(request('POST', '/api/file', data, headers)[0], 403)
            headers['Origin'] = f'http://127.0.0.1:{server.server_port}'
            self.assertEqual(request('POST', '/api/file', data, headers)[0], 200)
            self.assertEqual(request('POST', '/api/file', data, headers)[0], 409)
            self.assertEqual(request('GET', '/api/session', headers={'Host': 'evil.example'})[0], 403)
            self.assertEqual(request('GET', '/api/file?path=..%2Fsecret.txt')[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    unittest.main()
