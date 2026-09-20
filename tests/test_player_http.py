"""Player editor HTTP integration with disposable indexed sprites and shared palettes."""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from PIL import Image

from server import Handler, Project, ThreadingHTTPServer


FRONT = 'graphics/trainers/front_pics/brendan.png'
BACK = 'graphics/trainers/back_pics/brendan.png'
PALETTE = 'graphics/trainers/palettes/brendan.pal'
POKEMON = 'graphics/pokemon/treecko/front.png'


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class PlayerHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/pokeemerald'
        self.source.mkdir(parents=True)
        self.write('rom.sha1', b'temporary player HTTP fixture\n')
        for path in (FRONT, BACK, POKEMON):
            image = Image.new('P', (64, 64))
            image.putpalette([255, 0, 0] * 16)
            image.putdata([0, 1, 2, 3] * 1024)
            stream = io.BytesIO()
            image.save(stream, 'PNG', bits=4)
            self.write(path, stream.getvalue())
        self.colors = [(80, 90, 100), (12, 100, 199), (248, 8, 16), (32, 64, 96)] + [(0, 0, 0)] * 12
        # Keep unusual original line endings so transaction restoration must be byte-exact.
        self.write(PALETTE, ('JASC-PAL\n0100\n16\n' + '\n'.join(' '.join(map(str, rgb)) for rgb in self.colors) + '\n').encode())
        self.write('src/data/graphics/trainers.h', (
            f'const u32 gTrainerFrontPic_Brendan[] = INCGFX_U32("{FRONT}", ".4bpp.lz");\n'
            f'const u32 gTrainerBackPic_Brendan[] = INCGFX_U32("{BACK}", ".4bpp");\n'
            f'const u16 gTrainerPalette_Brendan[] = INCGFX_U16("{PALETTE}", ".gbapal");\n'
        ).encode())
        self.write('src/data/trainer_graphics/front_pic_tables.h',
                   b'TRAINER_SPRITE(BRENDAN, gTrainerFrontPic_Brendan)\nTRAINER_PAL(BRENDAN, gTrainerPalette_Brendan)\n')
        self.write('src/data/trainer_graphics/back_pic_tables.h',
                   b'TRAINER_BACK_SPRITE(BRENDAN, gTrainerBackPic_Brendan)\nTRAINER_BACK_PAL(BRENDAN, gTrainerPalette_Brendan)\n')
        self.pristine = self.snapshot()
        self.project = Project(self.root)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.project = self.project
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        status, session = self.request('GET', '/api/session', authorized=False)
        self.assertEqual(status, 200, session)
        self.token = session['token']

    def write(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def snapshot(self):
        return {path.relative_to(self.source).as_posix(): path.read_bytes()
                for path in self.source.rglob('*') if path.is_file()}

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, route, payload=None, authorized=True, raw=False):
        headers = {'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if authorized:
            headers['X-Workbench-Token'] = self.token
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, route, json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            content = response.read()
            return (response.status, response.getheader('Content-Type'), content) if raw else (response.status, json.loads(content))
        finally:
            connection.close()

    def asset(self, path=BACK):
        status, asset = self.request('GET', '/api/player/asset?' + urlencode({'path': path}))
        self.assertEqual(status, 200, asset)
        return asset

    def edit(self, palette=False):
        asset = self.asset()
        asset['pixels'][5] = 2
        if palette:
            asset['palette'][1] = '#10AAFF'
            asset['confirm_shared'] = True
        return asset

    def assert_no_transactions(self):
        status, result = self.request('GET', '/api/transactions')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['transactions'], [])
        self.assertFalse(self.project.originals.exists())
        self.assertFalse((self.project.state / 'transactions').exists())

    def test_catalog_detail_and_preview_use_effective_shared_palette_and_transparency(self):
        status, catalog = self.request('GET', '/api/player')
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog['asset_count'], 2)
        self.assertEqual({row['path'] for row in catalog['assets']}, {FRONT, BACK})
        asset = self.asset()
        self.assertEqual((asset['width'], asset['height'], asset['frame_count']), (64, 64, 1))
        self.assertEqual(asset['palette_path'], PALETTE)
        self.assertEqual({row['path'] for row in asset['palette_shared_with']}, {FRONT})
        self.assertEqual(asset['pixels'], [0, 1, 2, 3] * 1024)
        self.assertEqual(asset['palette'][1], '#0C64C7')
        self.assertEqual(asset['engine_palette'][1], '#0862C5')
        status, mime, content = self.request('GET', asset['preview_url'], raw=True)
        self.assertEqual((status, mime), (200, 'image/png'))
        with Image.open(io.BytesIO(content)) as preview:
            self.assertEqual((preview.mode, preview.size), ('RGBA', (64, 64)))
            self.assertEqual(preview.getpixel((0, 0))[3], 0)
            self.assertEqual(preview.getpixel((1, 0)), (8, 98, 197, 255))
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()

    def test_save_commits_pixels_and_shared_palette_then_undo_restores_exact_source(self):
        original_revision = self.asset()['revision']
        other_revision = self.asset(FRONT)['revision']
        body = self.edit(palette=True)
        status, result = self.request('POST', '/api/player/save', body)
        self.assertEqual(status, 200, result)
        self.assertIsInstance(result['transaction'], str)
        self.assertEqual(result['asset']['pixels'], body['pixels'])
        self.assertEqual(result['asset']['palette'], body['palette'])
        self.assertNotEqual(result['asset']['revision'], original_revision)
        self.assertNotEqual(self.asset(FRONT)['revision'], other_revision)
        changed = {path for path, content in self.snapshot().items() if content != self.pristine[path]}
        self.assertEqual(changed, {BACK, PALETTE})
        with Image.open(self.source / BACK) as saved:
            self.assertEqual((saved.mode, saved.size), ('P', (64, 64)))
            self.assertEqual(list(saved.tobytes()), body['pixels'])
        status, transactions = self.request('GET', '/api/transactions')
        self.assertEqual(status, 200, transactions)
        self.assertEqual(len(transactions['transactions']), 1)
        self.assertEqual(set(transactions['transactions'][0]['files']), changed)
        self.assertEqual(self.request('GET', '/api/changes')[1]['count'], 2)
        status, restored = self.request('POST', '/api/transaction/undo', {'id': result['transaction']})
        self.assertEqual(status, 200, restored)
        self.assertEqual(self.snapshot(), self.pristine)
        self.assertEqual(self.asset()['revision'], original_revision)
        self.assertEqual(self.asset(FRONT)['revision'], other_revision)
        self.assertEqual(self.request('GET', '/api/changes')[1]['count'], 0)

    def test_stale_shared_palette_revision_rejects_before_any_source_write(self):
        body = self.edit()
        path = self.source / PALETTE
        path.write_bytes(path.read_bytes().replace(b'12 100 199', b'16 100 199'))
        expected = self.snapshot()
        status, error = self.request('POST', '/api/player/save', body)
        self.assertEqual(status, 400, error)
        self.assertIn('changed', error['error'])
        self.assertEqual(self.snapshot(), expected)
        self.assert_no_transactions()

    def test_shared_palette_requires_explicit_confirmation_before_pixel_or_palette_save(self):
        body = self.edit(palette=True)
        body.pop('confirm_shared')
        status, error = self.request('POST', '/api/player/save', body)
        self.assertEqual(status, 400, error)
        self.assertIn('shared', error['error'])
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()

    def test_missing_token_and_unrelated_sprite_paths_are_rejected_without_writes(self):
        status, error = self.request('POST', '/api/player/save', self.edit(), authorized=False)
        self.assertEqual(status, 403, error)
        self.assertIn('reconnect', error['error'])
        for route in ('/api/player/asset', '/api/player/preview'):
            for path in (POKEMON, '../outside.png'):
                with self.subTest(route=route, path=path):
                    status, error = self.request('GET', route + '?' + urlencode({'path': path}))
                    self.assertEqual(status, 400, error)
        status, error = self.request('POST', '/api/player/save', {**self.edit(), 'path': POKEMON})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()


if __name__ == '__main__':
    unittest.main()
