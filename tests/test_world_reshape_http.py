"""HTTP previews, commits and Saved history undo use the same reshape service."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from server import Handler, ThreadingHTTPServer
from test_world_reshape import ReshapeFixture


class QuietHandler(Handler):
    def log_message(self, format, *args):
        pass


class WorldReshapeHttpTests(ReshapeFixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.project = self.project
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        status, session = self.request('GET', '/api/session', authorized=False)
        self.assertEqual(status, 200, session)
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
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        try:
            connection.request(method, route, json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_expansion_preview_commit_and_saved_history_undo(self):
        before = self.snapshot()
        original_positions = self.canvas()
        body = self.body()
        status, preview = self.request('POST', '/api/worldmap/expand/preview', body)
        self.assertEqual(status, 200, preview)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())
        status, saved = self.request('POST', '/api/worldmap/expand', {**body, 'world_revision': preview['world_revision']})
        self.assertEqual(status, 200, saved)
        self.assertEqual((saved['width'], saved['height']), (10, 8))
        self.assertEqual(self.canvas()['RouteB'], original_positions['RouteB'])
        self.assertNotEqual(self.snapshot(), before)
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertTrue(undone['positions_restored'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())

    def test_merge_commits_one_box_and_undo_restores_both(self):
        before = self.snapshot()
        body = self.body('merge')
        status, preview = self.request('POST', '/api/worldmap/merge/preview', body)
        self.assertEqual(status, 200, preview)
        status, saved = self.request('POST', '/api/worldmap/merge', {**body, 'world_revision': preview['world_revision']})
        self.assertEqual(status, 200, saved)
        self.assertEqual((saved['width'], saved['height']), (16, 7))
        self.assertEqual(set(self.project.world._maps()), {'RouteB', 'House'})
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertTrue(undone['positions_restored'])
        self.assertEqual(self.snapshot(), before)

    def test_stale_preview_and_forged_positions_are_rejected_without_source_changes(self):
        before = self.snapshot()
        body = self.ready()
        self.position({'House': {'x': 900, 'y': 10}})
        status, error = self.request('POST', '/api/worldmap/expand', body)
        self.assertEqual(status, 400, error)
        self.assertIn('positions changed', error['error'])
        body = self.body('merge')
        body['maps'][0]['x'] -= 1
        status, error = self.request('POST', '/api/worldmap/merge/preview', body)
        self.assertEqual(status, 400, error)
        self.assertIn('box moved', error['error'])
        self.assertEqual(self.snapshot(), before)

    def test_missing_source_preview_token_cannot_commit(self):
        before = self.snapshot()
        for kind in ('expand', 'merge'):
            status, error = self.request('POST', '/api/worldmap/' + kind, self.body(kind))
            self.assertEqual(status, 400, error)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())

    def test_later_box_moves_are_preserved_through_saved_history_undo(self):
        status, saved = self.request('POST', '/api/worldmap/expand', self.ready())
        self.assertEqual(status, 200, saved)
        self.position({'House': {'x': -700, 'y': 40}})
        later_positions = self.positions.path.read_bytes()
        status, undone = self.request('POST', '/api/transaction/undo', {'id': saved['transaction']})
        self.assertEqual(status, 200, undone)
        self.assertFalse(undone['positions_restored'])
        self.assertEqual(self.positions.path.read_bytes(), later_positions)

    def test_position_write_failure_returns_error_and_rolls_back_source(self):
        before = self.snapshot()
        body = self.ready()
        with patch('world_reshape.WorldPositions.save', side_effect=OSError('fixture disk unavailable')):
            status, error = self.request('POST', '/api/worldmap/expand', body)
        self.assertEqual(status, 500, error)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.positions.path.exists())
        self.assertEqual(self.project.transactions.list_transactions(), [])

    def test_endpoints_require_authorization(self):
        before = self.snapshot()
        for kind in ('expand', 'merge'):
            status, result = self.request('POST', '/api/worldmap/' + kind, self.body(kind), authorized=False)
            self.assertEqual(status, 403, result)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
