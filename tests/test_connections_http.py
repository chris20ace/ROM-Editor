"""Connection editor HTTP integration using disposable source fixtures only."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import Handler, Project, ThreadingHTTPServer


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class ConnectionsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source/pokeemerald'
        self.source.mkdir(parents=True)
        (self.source / 'rom.sha1').write_text('temporary integration fixture\n', encoding='utf-8')
        layouts = []
        for name, width, height, map_type in [('Town', 20, 20, 'MAP_TYPE_TOWN'),
                                               ('Route', 12, 12, 'MAP_TYPE_ROUTE'),
                                               ('House', 10, 10, 'MAP_TYPE_INDOOR')]:
            data = {'name': name, 'id': 'MAP_' + name.upper(), 'layout': 'LAYOUT_' + name.upper(),
                    'map_type': map_type, 'region_map_section': 'MAPSEC_LITTLEROOT_TOWN',
                    'connections': None, 'warp_events': [], 'object_events': [],
                    'custom_metadata': {'keep': 'unknown fields and original bytes'}}
            path = self.source / f'data/maps/{name}/map.json'
            path.parent.mkdir(parents=True)
            path.write_bytes((json.dumps(data, indent=2) + '\r\n').encode('utf-8'))
            layouts.append({'id': data['layout'], 'width': width, 'height': height})
        path = self.source / 'data/layouts/layouts.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(json.dumps({'layouts': layouts}).encode('utf-8'))
        self.pristine = self.snapshot()
        self.project = Project(self.root)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.project = self.project
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        status, session = self.request('GET', '/api/session', authorized=False)
        self.assertEqual(status, 200)
        self.token = session['token']

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

    def catalog(self):
        status, catalog = self.request('GET', '/api/connections')
        self.assertEqual(status, 200, catalog)
        return catalog

    def edge(self, revision=None):
        return {'revision': revision or self.catalog()['revision'], 'a': 'Town', 'b': 'Route',
                'direction': 'up', 'offset': 3, 'action': 'connect'}

    def warp(self, revision=None):
        return {'revision': revision or self.catalog()['revision'],
                'a': {'map': 'Town', 'x': 5, 'y': 8, 'elevation': 0, 'index': None},
                'b': {'map': 'House', 'x': 4, 'y': 8, 'elevation': 0, 'index': None}}

    def test_catalog_reads_all_fixture_maps_without_rendering_dependencies(self):
        catalog = self.catalog()
        self.assertEqual(len(catalog['revision']), 64)
        self.assertEqual({row['name'] for row in catalog['maps']}, {'Town', 'Route', 'House'})
        self.assertTrue(all(row['connections'] == [] and row['warps'] == [] for row in catalog['maps']))
        self.assertEqual(self.snapshot(), self.pristine)
        self.assertFalse(self.project.originals.exists())

    def test_edge_post_saves_both_maps_and_undo_restores_exact_original_bytes(self):
        initial_revision = self.catalog()['revision']
        status, saved = self.request('POST', '/api/connections/edge', self.edge(initial_revision))
        self.assertEqual(status, 200, saved)
        self.assertIsInstance(saved['transaction'], str)
        self.assertNotEqual(saved['revision'], initial_revision)
        maps = {row['name']: row for row in saved['maps']}
        self.assertEqual(maps['Town']['connections'][0]['offset'], 3)
        self.assertEqual(maps['Town']['connections'][0]['dest_name'], 'Route')
        self.assertEqual(maps['Route']['connections'][0]['offset'], -3)
        self.assertEqual(maps['Route']['connections'][0]['direction'], 'down')
        self.assertTrue(maps['Town']['connections'][0]['reciprocal'])
        status, transactions = self.request('GET', '/api/transactions')
        self.assertEqual(status, 200)
        self.assertEqual(set(transactions['transactions'][0]['files']),
                         {'data/maps/Town/map.json', 'data/maps/Route/map.json'})
        status, changes = self.request('GET', '/api/changes')
        self.assertEqual(status, 200)
        self.assertEqual(changes['count'], 2)
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertEqual(self.snapshot(), self.pristine)
        self.assertEqual(self.catalog()['revision'], initial_revision)
        self.assertEqual(self.request('GET', '/api/changes')[1]['count'], 0)

    def test_new_warp_pair_saves_two_files_with_reciprocal_zero_based_destinations(self):
        status, saved = self.request('POST', '/api/connections/warp', self.warp())
        self.assertEqual(status, 200, saved)
        maps = {row['name']: row for row in saved['maps']}
        town, house = maps['Town']['warps'][0], maps['House']['warps'][0]
        self.assertEqual((town['index'], town['dest_name'], town['dest_index']), (0, 'House', 0))
        self.assertEqual((house['index'], house['dest_name'], house['dest_index']), (0, 'Town', 0))
        self.assertTrue(town['reciprocal'] and house['reciprocal'])
        self.assertEqual((town['x'], town['y'], town['elevation']), (5, 8, 0))
        changed = {relative for relative, data in self.snapshot().items() if data != self.pristine[relative]}
        self.assertEqual(changed, {'data/maps/Town/map.json', 'data/maps/House/map.json'})
        for relative in changed:
            self.assertEqual(json.loads(self.snapshot()[relative])['custom_metadata'],
                             json.loads(self.pristine[relative])['custom_metadata'])
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertEqual(self.snapshot(), self.pristine)

    def test_stale_edge_and_warp_revisions_fail_before_creating_backups_or_writing(self):
        revision = self.catalog()['revision']
        house_path = self.source / 'data/maps/House/map.json'
        external = json.loads(house_path.read_bytes())
        external['custom_metadata']['keep'] = 'external editor made a change'
        house_path.write_bytes(json.dumps(external).encode('utf-8'))
        expected = self.snapshot()
        for route, payload in [('/api/connections/edge', self.edge(revision)),
                               ('/api/connections/warp', self.warp(revision))]:
            with self.subTest(route=route):
                status, error = self.request('POST', route, payload)
                self.assertEqual(status, 400, error)
                self.assertIn('Maps changed', error['error'])
                self.assertEqual(self.snapshot(), expected)
        self.assertFalse(self.project.originals.exists())
        self.assertEqual(self.request('GET', '/api/transactions')[1]['transactions'], [])

    def test_second_save_with_old_revision_cannot_overwrite_committed_pair(self):
        body = self.edge()
        status, saved = self.request('POST', '/api/connections/edge', body)
        self.assertEqual(status, 200, saved)
        expected = self.snapshot()
        status, error = self.request('POST', '/api/connections/edge', {**body, 'offset': 4, 'replace': True})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(len(self.request('GET', '/api/transactions')[1]['transactions']), 1)

    def test_missing_token_rejects_edge_and_warp_writes(self):
        for route, payload in [('/api/connections/edge', self.edge()),
                               ('/api/connections/warp', self.warp())]:
            with self.subTest(route=route):
                status, error = self.request('POST', route, payload, authorized=False)
                self.assertEqual(status, 403, error)
                self.assertIn('reconnect', error['error'])
                self.assertEqual(self.snapshot(), self.pristine)
        self.assertFalse(self.project.originals.exists())


if __name__ == '__main__':
    unittest.main()
