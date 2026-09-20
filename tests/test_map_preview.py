"""Thumbnail rendering and HTTP bounds; all map files are temporary fixtures."""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from server import Handler, Project, ThreadingHTTPServer
from world import pack_words


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class MapPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / 'source/pokeemerald'
        self.source.mkdir(parents=True)
        (self.source / 'rom.sha1').write_text('temporary preview fixture\n')
        layout = {'id': 'LAYOUT_RECTANGLE', 'width': 6, 'height': 3,
                  'primary_tileset': 'Primary', 'secondary_tileset': 'Secondary',
                  'blockdata_filepath': 'data/layouts/Rectangle/map.bin'}
        for relative, content in {
            'data/layouts/layouts.json': json.dumps({'layouts': [layout]}).encode(),
            'data/maps/Rectangle/map.json': json.dumps({'layout': layout['id']}).encode(),
            layout['blockdata_filepath']: pack_words([0, 1, 0, 1, 0, 1] * 3),
        }.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.pristine = self.snapshot()
        self.project = Project(root)
        self.world = self.project.world
        # Tileset parsing is covered elsewhere; supply a known atlas to check exact pixels.
        atlas = Image.new('RGBA', (256, 16), '#00aa55')
        atlas.paste('#ff0044', (16, 0, 32, 16))
        pair = patch.object(self.world, '_pair', return_value=(atlas, {'count': 2}))
        self.pair = pair.start()
        self.addCleanup(pair.stop)

    def tearDown(self):
        self.assertEqual(self.snapshot(), self.pristine)

    def snapshot(self):
        return {path.relative_to(self.source).as_posix(): path.read_bytes()
                for path in self.source.rglob('*') if path.is_file()}

    def decoded(self, content):
        image = Image.open(io.BytesIO(content))
        image.load()
        return image

    def test_thumbnail_preserves_aspect_and_nearest_pixels_without_enlarging(self):
        original = self.world.render_map('Rectangle')
        full = self.decoded(original)
        self.assertEqual(full.size, (96, 48))
        for limit in (32, 48):
            with self.subTest(limit=limit):
                thumbnail = self.decoded(self.world.render_map('Rectangle', max_size=limit))
                self.assertEqual(thumbnail.size, (limit, limit // 2))
                expected = full.resize(thumbnail.size, Image.Resampling.NEAREST)
                self.assertEqual(thumbnail.tobytes(), expected.tobytes())
        self.assertEqual(self.world.render_map('Rectangle', max_size=1024), original)
        self.assertEqual(self.world.render_map('Rectangle', max_size=None), original)

    def test_invalid_sizes_fail_before_source_lookup_or_image_work(self):
        with patch.object(self.world, '_lookup') as lookup:
            for limit in (0, 31, 1025, -32, True, False, 32.0, '32', {}, []):
                with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'Preview maximum size'):
                    self.world.render_map('Rectangle', max_size=limit)
            lookup.assert_not_called()
            self.pair.assert_not_called()

    def test_http_size_query_returns_png_and_rejects_invalid_values_before_rendering(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        server.project = self.project
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            def request(query):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request('GET', '/api/world/maps/Rectangle/preview.png' + query)
                    response = connection.getresponse()
                    return response.status, response.getheader('Content-Type'), response.read()
                finally:
                    connection.close()

            for query, size in [('', (96, 48)), ('?max_size=32', (32, 16)), ('?max_size=1024', (96, 48))]:
                with self.subTest(query=query):
                    status, content_type, data = request(query)
                    self.assertEqual(status, 200, data)
                    self.assertEqual(content_type, 'image/png')
                    self.assertEqual(self.decoded(data).size, size)
            with patch.object(self.world, 'render_map') as render:
                for query in ('?max_size=31', '?max_size=1025', '?max_size=-1', '?max_size=32.5',
                              '?max_size=true', '?max_size=', '?max_size=32&max_size=64'):
                    with self.subTest(query=query):
                        status, _, data = request(query)
                        self.assertEqual(status, 400, data)
                        self.assertIn('32 to 1024', json.loads(data)['error'])
                render.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    unittest.main()
