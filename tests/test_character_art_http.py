"""Appearance API transactions use disposable trainer/NPC source graphics."""
import io
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from PIL import Image

from server import Project, ThreadingHTTPServer
from tests import test_player_http as http_fixture
from tests.test_character_art import (make_character_fixture, HIKER, NPC, POKEMON,
                                      NPC_PAL, STEVEN, STEVEN_BACK)


class CharacterArtHttpTests(unittest.TestCase):
    request = http_fixture.PlayerHttpTests.request
    snapshot = http_fixture.PlayerHttpTests.snapshot
    stop_server = http_fixture.PlayerHttpTests.stop_server
    assert_no_transactions = http_fixture.PlayerHttpTests.assert_no_transactions

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/pokeemerald'
        make_character_fixture(self.source)
        self.pristine = self.snapshot()
        self.project = Project(self.root)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_fixture.QuietHandler)
        self.server.project = self.project
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        status, session = self.request('GET', '/api/session', authorized=False)
        self.assertEqual(status, 200, session)
        self.token = session['token']

    def asset(self, path):
        status, asset = self.request('GET', '/api/appearance/asset?' + urlencode({'path': path}))
        self.assertEqual(status, 200, asset)
        return asset

    def test_catalog_and_png_preview_are_read_only(self):
        status, catalog = self.request('GET', '/api/appearance')
        self.assertEqual(status, 200, catalog)
        self.assertIn(HIKER, {row['path'] for row in catalog['assets']})
        self.assertIn(NPC, {row['path'] for row in catalog['assets']})
        self.assertNotIn(POKEMON, {row['path'] for row in catalog['assets']})
        asset = self.asset(NPC)
        status, mime, content = self.request('GET', asset['preview_url'], raw=True)
        self.assertEqual((status, mime), (200, 'image/png'))
        image = Image.open(io.BytesIO(content))
        self.assertEqual(image.getpixel((1, 0)), tuple(asset['palette_rgba'][1]))
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()

    def test_trainer_and_npc_pixels_save_and_undo_restore_byte_exact_source(self):
        for path in (HIKER, NPC):
            with self.subTest(path=path):
                body = self.asset(path)
                original_revision = body['revision']
                body['pixels'][0] = 13
                status, saved = self.request('POST', '/api/appearance/save', body)
                self.assertEqual(status, 200, saved)
                self.assertEqual(saved['asset']['pixels'], body['pixels'])
                self.assertNotEqual(saved['asset']['revision'], original_revision)
                self.assertEqual({p for p, data in self.snapshot().items() if data != self.pristine[p]}, {path})
                self.assertEqual((self.source / POKEMON).read_bytes(), self.pristine[POKEMON])
                status, restored = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
                self.assertEqual(status, 200, restored)
                self.assertEqual(self.snapshot(), self.pristine)
                self.assertEqual(self.asset(path)['revision'], original_revision)

    def test_shared_embedded_palette_and_back_sprite_are_one_transaction(self):
        body = self.asset(STEVEN_BACK)
        body['pixels'][2] = 7
        body['palette'][3] = '#AABBCC'
        body['confirm_shared'] = True
        status, saved = self.request('POST', '/api/appearance/save', body)
        self.assertEqual(status, 200, saved)
        self.assertEqual({p for p, data in self.snapshot().items() if data != self.pristine[p]}, {STEVEN, STEVEN_BACK})
        status, restored = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, restored)
        self.assertEqual(self.snapshot(), self.pristine)

    def test_locked_palette_rejected_before_pixel_writes_even_with_confirmation(self):
        body = self.asset(NPC)
        body['pixels'][0] = 13
        body['palette'][3] = '#AABBCC'
        body['confirm_shared'] = True
        status, error = self.request('POST', '/api/appearance/save', body)
        self.assertEqual(status, 400, error)
        self.assertIn('Pokémon', error['error'])
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()

    def test_auth_stale_revision_and_protected_graphics_fail_without_writes(self):
        body = self.asset(HIKER)
        body['pixels'][0] = 13
        status, error = self.request('POST', '/api/appearance/save', body, authorized=False)
        self.assertEqual(status, 403, error)
        body['revision'] = 'stale'
        status, error = self.request('POST', '/api/appearance/save', body)
        self.assertEqual(status, 400, error)
        self.assertIn('changed', error['error'])
        for endpoint in ('asset', 'preview'):
            status, error = self.request('GET', '/api/appearance/' + endpoint + '?' + urlencode({'path': POKEMON}))
            self.assertEqual(status, 400, error)
        self.assertEqual(self.snapshot(), self.pristine)
        self.assert_no_transactions()


if __name__ == '__main__':
    unittest.main()
