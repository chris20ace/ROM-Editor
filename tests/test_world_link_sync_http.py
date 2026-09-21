"""Exercise canvas walking-link previews, saves and undo through the HTTP API."""
import http.client
import json
import threading
import unittest

from server import Handler, ThreadingHTTPServer
from test_world_reshape import ReshapeFixture


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class WorldLinkSyncHttpTests(ReshapeFixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()
        self.position({'RouteA': {'x': 0, 'y': 0}, 'RouteB': {'x': 8, 'y': 2},
                       'House': {'x': 100, 'y': 100}})
        route = self.data('RouteA')
        route['warp_events'] = [{'x': 1, 'y': 2, 'elevation': 0,
                                 'dest_map': 'MAP_HOUSE', 'dest_warp_id': '0'}]
        house = self.data('House')
        house['warp_events'] = [
            {'x': 3, 'y': 4, 'elevation': 3, 'dest_map': 'MAP_ROUTEA', 'dest_warp_id': 0},
            {'x': 5, 'y': 4, 'elevation': 0, 'dest_map': 'MAP_DYNAMIC', 'dest_warp_id': 'WARP_ID_DYNAMIC'},
        ]
        self.write('data/maps/RouteA/map.json', route)
        self.write('data/maps/House/map.json', house)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.project = self.project
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        status, session = self.request('GET', '/api/session', authorized=False)
        self.assertEqual(status, 200, session)
        self.token = session['token']

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def data(self, name):
        return json.loads((self.source / f'data/maps/{name}/map.json').read_bytes())

    def request(self, method, path, payload=None, *, authorized=True, headers=None, raw=False):
        request_headers = {'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        if authorized:
            request_headers['X-Workbench-Token'] = self.token
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        try:
            connection.request(method, path, None if payload is None else json.dumps(payload), request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, data.decode('utf-8') if raw else json.loads(data)
        finally:
            connection.close()

    def link_body(self):
        status, catalog = self.request('GET', '/api/worldmap/links')
        self.assertEqual(status, 200, catalog)
        return {'revision': catalog['revision'], 'positions_revision': catalog['positions_revision']}

    def test_catalog_and_preview_are_read_only_with_real_portal_endpoints(self):
        source, positions = self.snapshot(), self.positions.path.read_bytes()
        status, catalog = self.request('GET', '/api/worldmap/links')
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog['default_names'], ['RouteA', 'RouteB'])
        self.assertEqual(catalog['audit']['counts']['dynamic'], 1)
        portal = next(p for p in catalog['audit']['portals'] if p['source']['map'] == 'RouteA')
        self.assertEqual(portal['destination']['map'], 'House')
        self.assertEqual((portal['destination']['world_x'], portal['destination']['world_y']), (103.5, 104.5))
        status, preview = self.request('POST', '/api/worldmap/links/preview', self.link_body())
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview['can_apply'])
        self.assertEqual({(p['from_name'], p['offset']) for p in preview['added']},
                         {('RouteA', 2), ('RouteB', -2)})
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_apply_saves_reciprocal_offsets_preserves_canvas_and_portals_then_undoes(self):
        source, positions, canvas = self.snapshot(), self.positions.path.read_bytes(), self.canvas()
        body = self.link_body()
        status, preview = self.request('POST', '/api/worldmap/links/preview', body)
        self.assertEqual(status, 200, preview)
        status, saved = self.request('POST', '/api/worldmap/links', body)
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved['transaction'])
        self.assertEqual(self.data('RouteA')['connections'],
                         [{'map': 'MAP_ROUTEB', 'direction': 'right', 'offset': 2}])
        self.assertEqual(self.data('RouteB')['connections'],
                         [{'map': 'MAP_ROUTEA', 'direction': 'left', 'offset': -2}])
        for name in ('RouteA', 'RouteB', 'House'):
            self.assertEqual(self.data(name)['warp_events'],
                             json.loads(source[f'data/maps/{name}/map.json'])['warp_events'])
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.canvas(), canvas)
        self.assertEqual(saved['canvas_positions']['positions'], canvas)
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertTrue(undone['positions_restored'])
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_preview_and_apply_reject_missing_csrf_and_foreign_origin(self):
        body = self.link_body()
        source, positions = self.snapshot(), self.positions.path.read_bytes()
        for path in ('/api/worldmap/links/preview', '/api/worldmap/links'):
            with self.subTest(path=path, reason='missing CSRF token'):
                status, error = self.request('POST', path, body, authorized=False)
                self.assertEqual(status, 403, error)
            with self.subTest(path=path, reason='foreign origin with correct CSRF token'):
                status, error = self.request('POST', path, body, headers={'Origin': 'https://example.com'})
                self.assertEqual(status, 403, error)
            with self.subTest(path=path, reason='expired CSRF token'):
                status, error = self.request('POST', path, body, headers={'X-Workbench-Token': 'old-session-token'})
                self.assertEqual(status, 403, error)
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_stale_source_revision_rejects_preview_and_apply(self):
        body = self.link_body()
        house = self.data('House')
        house['story_note'] = 'A separate saved edit'
        self.write('data/maps/House/map.json', house)
        source, positions = self.snapshot(), self.positions.path.read_bytes()
        for path in ('/api/worldmap/links/preview', '/api/worldmap/links'):
            status, error = self.request('POST', path, body)
            self.assertEqual(status, 400, error)
            self.assertIn('Maps changed', error['error'])
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_stale_canvas_revision_cannot_apply_old_preview(self):
        body = self.link_body()
        status, preview = self.request('POST', '/api/worldmap/links/preview', body)
        self.assertEqual(status, 200, preview)
        self.position({'RouteB': {'x': 8, 'y': 1}})
        source, positions = self.snapshot(), self.positions.path.read_bytes()
        status, error = self.request('POST', '/api/worldmap/links', body)
        self.assertEqual(status, 400, error)
        self.assertIn('positions changed', error['error'])
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_overlap_preview_reports_error_and_apply_does_not_save(self):
        self.position({'RouteB': {'x': 7, 'y': 1}})
        source, positions = self.snapshot(), self.positions.path.read_bytes()
        body = self.link_body()
        status, preview = self.request('POST', '/api/worldmap/links/preview', body)
        self.assertEqual(status, 200, preview)
        self.assertFalse(preview['can_apply'])
        self.assertTrue(any('overlaps' in error for error in preview['errors']))
        status, error = self.request('POST', '/api/worldmap/links', body)
        self.assertEqual(status, 400, error)
        self.assertIn('overlaps', error['error'])
        self.assertEqual(self.snapshot(), source)
        self.assertEqual(self.positions.path.read_bytes(), positions)

    def test_browser_module_is_served(self):
        status, script = self.request('GET', '/worldmap-links.js', raw=True)
        self.assertEqual(status, 200)
        self.assertIn('WorldMapLinks', script)


if __name__ == '__main__':
    unittest.main()
