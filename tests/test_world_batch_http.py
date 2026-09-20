"""World-canvas batch HTTP tests against a disposable, renderable source fixture."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from server import Handler, Project, ThreadingHTTPServer
from world import pack_words, words


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class WorldBatchHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/pokeemerald'
        self.source.mkdir(parents=True)
        self.write('rom.sha1', b'temporary integration fixture\n')
        layouts = []
        for name, width, height, map_type, neighbor, direction in (
                ('Town', 3, 3, 'MAP_TYPE_TOWN', 'Route', 'up'),
                ('Route', 4, 3, 'MAP_TYPE_ROUTE', 'Town', 'down')):
            data = {
                'name': name, 'id': 'MAP_' + name.upper(), 'layout': 'LAYOUT_' + name.upper(),
                'map_type': map_type, 'region_map_section': 'MAPSEC_LITTLEROOT_TOWN',
                'requires_flash': False, 'allow_cycling': True, 'allow_escaping': False,
                'allow_running': True, 'show_map_name': True,
                'connections': [{'map': 'MAP_' + neighbor.upper(), 'direction': direction, 'offset': 0}],
                'object_events': [], 'warp_events': [], 'coord_events': [], 'bg_events': [],
                'custom_metadata': {'keep': 'unknown fields and original bytes'},
            }
            self.write(f'data/maps/{name}/map.json', (json.dumps(data, indent=2) + '\r\n').encode())
            layout = {
                'id': data['layout'], 'name': name + '_Layout', 'width': width, 'height': height,
                'primary_tileset': 'gTileset_Primary', 'secondary_tileset': 'gTileset_Secondary',
                'blockdata_filepath': f'data/layouts/{name}/map.bin',
                'border_filepath': f'data/layouts/{name}/border.bin',
            }
            layouts.append(layout)
            self.write(layout['blockdata_filepath'], pack_words([0x3000] * (width * height)))
            self.write(layout['border_filepath'], pack_words([0x3000] * 4))
        self.write('data/layouts/layouts.json', json.dumps({'layouts': layouts}).encode())
        self.write('include/constants/metatile_behaviors.h', b'enum { MB_NORMAL, MB_UNUSED };\n')
        self.write('src/graphics.c', b'')
        headers, graphics, metatiles = [], [], []
        palette = 'data/tilesets/fixture.pal'
        self.write(palette, ('JASC-PAL\n0100\n16\n' + '0 0 0\n' * 16).encode())
        for name, secondary, count in [('Primary', 'FALSE', 2), ('Secondary', 'TRUE', 1)]:
            base = f'data/tilesets/{name}'
            sheet = Image.new('P', (8, 8))
            sheet.putpalette([0, 0, 0] * 256)
            tile_path = self.source / f'{base}/tiles.png'
            tile_path.parent.mkdir(parents=True)
            sheet.save(tile_path)
            self.write(f'{base}/metatiles.bin', pack_words([0] * (count * 8)))
            self.write(f'{base}/attributes.bin', pack_words([0] * count))
            headers.append(f'const struct Tileset gTileset_{name} = {{'
                           f'.isSecondary = {secondary}, .tiles = tiles_{name}, '
                           f'.palettes = palettes_{name}, .metatiles = metatiles_{name}, '
                           f'.metatileAttributes = attributes_{name}}};')
            graphics.append(f'const u32 tiles_{name}[] = INCGFX_U32("{base}/tiles.png");')
            graphics.append(f'const u16 palettes_{name}[][16] = {{' +
                            ','.join(f'INCGFX_U16("{palette}")' for _ in range(13)) + '};')
            metatiles.extend([
                f'const u16 metatiles_{name}[] = INCBIN_U16("{base}/metatiles.bin");',
                f'const u16 attributes_{name}[] = INCBIN_U16("{base}/attributes.bin");',
            ])
        for name, contents in [('headers', headers), ('graphics', graphics), ('metatiles', metatiles)]:
            self.write(f'src/data/tilesets/{name}.h', '\n'.join(contents).encode())
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

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, route, payload=None, authorized=True):
        headers = {'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if authorized:
            headers['X-Workbench-Token'] = self.token
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, route, json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def snapshot(self):
        return {path.relative_to(self.source).as_posix(): path.read_bytes()
                for path in self.source.rglob('*') if path.is_file()}

    def map(self, name):
        status, result = self.request('GET', '/api/world/map?name=' + name)
        self.assertEqual(status, 200, result)
        return result

    def paint_batch(self):
        maps = [self.map(name) for name in ('Town', 'Route')]
        for index, data in enumerate(maps):
            data['cells'][index] = 0x3001
        return {'maps': [{'name': data['name'], 'revision': data['revision'], 'cells': data['cells']}
                         for data in maps]}

    def transactions(self):
        status, result = self.request('GET', '/api/transactions')
        self.assertEqual(status, 200, result)
        return result['transactions']

    def test_two_map_save_commits_one_transaction_and_undo_restores_exact_bytes(self):
        body = self.paint_batch()
        status, result = self.request('POST', '/api/worldmap/save', body)
        self.assertEqual(status, 200, result)
        self.assertIsInstance(result['transaction'], str)
        self.assertEqual([data['name'] for data in result['saved']], ['Town', 'Route'])
        changed = {relative for relative, data in self.snapshot().items() if data != self.pristine[relative]}
        self.assertEqual(changed, {'data/layouts/Town/map.bin', 'data/layouts/Route/map.bin'})
        for expected, saved in zip(body['maps'], result['saved']):
            with self.subTest(map=expected['name']):
                self.assertEqual(saved['cells'], expected['cells'])
                self.assertNotEqual(saved['revision'], expected['revision'])
                self.assertEqual(self.map(expected['name'])['cells'], expected['cells'])
                self.assertEqual(words((self.source / saved['layout']['blockdata_filepath']).read_bytes()),
                                 expected['cells'])
        transactions = self.transactions()
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]['id'], result['transaction'])
        self.assertEqual(set(transactions[0]['files']), changed)
        self.assertEqual(self.request('GET', '/api/changes')[1]['count'], 2)
        status, undone = self.request('POST', '/api/transaction/undo', {'id': result['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertEqual(self.snapshot(), self.pristine)
        for original in body['maps']:
            self.assertEqual(self.map(original['name'])['revision'], original['revision'])
        self.assertEqual(self.request('GET', '/api/changes')[1]['count'], 0)

    def test_stale_second_map_rejects_entire_batch_without_writes_or_transactions(self):
        body = self.paint_batch()
        route_path = self.source / 'data/layouts/Route/map.bin'
        external = words(route_path.read_bytes())
        external[-1] = 0x3001
        route_path.write_bytes(pack_words(external))
        expected = self.snapshot()
        status, error = self.request('POST', '/api/worldmap/save', body)
        self.assertEqual(status, 400, error)
        self.assertIn('Route', error['error'])
        self.assertIn('changed', error['error'])
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual((self.source / 'data/layouts/Town/map.bin').read_bytes(),
                         self.pristine['data/layouts/Town/map.bin'])
        self.assertEqual(self.transactions(), [])
        self.assertFalse(self.project.originals.exists())
        self.assertFalse((self.project.state / 'transactions').exists())

    def test_missing_token_rejects_batch_without_writes_or_transactions(self):
        status, error = self.request('POST', '/api/worldmap/save', self.paint_batch(), authorized=False)
        self.assertEqual(status, 403, error)
        self.assertIn('reconnect', error['error'])
        self.assertEqual(self.snapshot(), self.pristine)
        self.assertEqual(self.transactions(), [])
        self.assertFalse(self.project.originals.exists())
        self.assertFalse((self.project.state / 'transactions').exists())


if __name__ == '__main__':
    unittest.main()
