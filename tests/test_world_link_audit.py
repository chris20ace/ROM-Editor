from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from connections import Connections
from world_link_audit import WorldLinkAudit, audit_warps


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'


class WorldLinkAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.maps = {}
        layouts = []
        for name in ('Alpha', 'Beta', 'Mirror', 'MirrorAgain'):
            self.maps[name] = {'name': name, 'id': 'MAP_' + name.upper(),
                               'layout': 'LAYOUT_' + name.upper(), 'warp_events': []}
            layouts.append({'id': 'LAYOUT_' + name.upper(), 'width': 10, 'height': 8})
        self.maps['Mirror']['shared_events_map'] = 'Beta'
        self.maps['MirrorAgain']['shared_events_map'] = 'Mirror'
        self.maps['Alpha']['warp_events'] = [self.warp('Beta', 0, x=2, y=3)]
        self.maps['Beta']['warp_events'] = [self.warp('Alpha', '0', x=5, y=6)]
        path = self.source / 'data/layouts/layouts.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'layouts': layouts}), encoding='utf-8')
        self.save()

    @staticmethod
    def warp(target, index, **changes):
        return {'x': 1, 'y': 1, 'elevation': 3, 'dest_map': 'MAP_' + target.upper(),
                'dest_warp_id': index, **changes}

    def save(self):
        for name, data in self.maps.items():
            path = self.source / f'data/maps/{name}/map.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding='utf-8')

    def audit(self, **positions):
        self.save()
        return WorldLinkAudit(self.source).catalog(positions or None)

    def portal(self, name='Alpha', index=0, **positions):
        return next(p for p in self.audit(**positions)['portals']
                    if p['source']['map'] == name and p['source']['index'] == index)

    def test_numeric_endpoints_are_exact_and_reciprocal(self):
        p = self.portal(Alpha={'x': -2, 'y': 9}, Beta={'x': 100, 'y': 200})
        self.assertEqual(p['status'], 'fixed')
        self.assertTrue(p['reciprocal'])
        self.assertEqual(p['source']['world_x'], .5)
        self.assertEqual(p['destination']['world_y'], 206.5)
        self.assertEqual(p['destination']['elevation'], 3)
        self.assertEqual(p['destination']['index'], 0)

    def test_shared_event_owner_chain_uses_requested_map_coordinates(self):
        self.maps['Alpha']['warp_events'][0]['dest_map'] = 'MAP_MIRRORAGAIN'
        p = self.portal(MirrorAgain={'x': 200, 'y': 300})
        self.assertEqual(p['destination']['map'], 'MirrorAgain')
        self.assertEqual(p['destination']['events_owner'], 'Beta')
        self.assertEqual(p['destination']['world_x'], 205.5)
        self.assertTrue(p['reciprocal'])

    def test_symbolic_warp_ids_resolve_like_generated_constants(self):
        self.maps['Alpha']['warp_events'][0].update(warp_id='WARP_ALPHA_DOOR', dest_warp_id='WARP_BETA_EXIT')
        self.maps['Beta']['warp_events'][0].update(warp_id='WARP_BETA_EXIT', dest_warp_id='WARP_ALPHA_DOOR')
        self.maps['Alpha']['warp_events'][0]['dest_map'] = 'MAP_MIRROR'
        p = self.portal()
        self.assertEqual(p['dest_index'], 0)
        self.assertEqual(p['status'], 'fixed')
        self.assertTrue(p['symbolic'])
        self.assertTrue(p['reciprocal'])

    def test_generated_constants_are_global_not_guessed_from_names(self):
        self.maps['Alpha']['warp_events'][0]['warp_id'] = 'WARP_GLOBAL_ZERO'
        self.maps['Alpha']['warp_events'][0]['dest_warp_id'] = 'WARP_GLOBAL_ZERO'
        self.assertEqual(self.portal()['destination']['index'], 0)

    def test_unknown_or_duplicate_symbols_are_unresolved(self):
        self.maps['Alpha']['warp_events'][0]['dest_warp_id'] = 'WARP_DOES_NOT_EXIST'
        self.assertIn('not defined', self.portal()['reason'])
        self.maps['Alpha']['warp_events'][0]['warp_id'] = 'WARP_DOES_NOT_EXIST'
        self.maps['Beta']['warp_events'][0]['warp_id'] = 'WARP_DOES_NOT_EXIST'
        self.assertEqual(self.portal()['status'], 'fixed')  # Repeated identical C constant.
        self.maps['Beta']['warp_events'].insert(0, self.warp('Alpha', 0))
        p = self.portal()
        self.assertEqual(p['status'], 'unresolved')
        self.assertIn('multiple definitions', p['reason'])

    def test_map_dynamic_ignores_even_unresolved_id(self):
        for value in ('WARP_ID_DYNAMIC', 'WARP_ID_SECRET_BASE', 'UNKNOWN', -1, 200):
            self.maps['Alpha']['warp_events'][0].update(dest_map='MAP_DYNAMIC', dest_warp_id=value)
            p = self.portal()
            self.assertEqual(p['status'], 'dynamic')
            self.assertIsNone(p['destination'])
            self.assertIsNone(p['dest_index'])

    def test_undefined_map_and_minus_one_are_scripted_not_broken(self):
        for value in (-1, '-1', 'WARP_ID_NONE'):
            self.maps['Alpha']['warp_events'][0]['dest_warp_id'] = value
            p = self.portal()
            self.assertEqual(p['status'], 'scripted')
            self.assertEqual(p['dest_name'], 'Beta')
            self.assertIsNone(p['destination'])
        self.maps['Alpha']['warp_events'][0].update(dest_map='MAP_UNDEFINED', dest_warp_id='DUMMY')
        self.assertEqual(self.portal()['status'], 'scripted')
        self.assertEqual(self.audit()['counts']['broken'], 0)

    def test_dynamic_id_alone_does_not_make_a_fixed_map_dynamic(self):
        self.maps['Alpha']['warp_events'][0]['dest_warp_id'] = 'WARP_ID_DYNAMIC'
        p = self.portal()
        self.assertEqual(p['dest_index'], 127)
        self.assertEqual(p['status'], 'unresolved')

    def test_missing_target_or_warp_is_broken_without_proximity_guess(self):
        self.maps['Alpha']['warp_events'][0]['dest_map'] = 'MAP_GONE'
        p = self.portal(Beta={'x': 0, 'y': 0})
        self.assertEqual(p['status'], 'unresolved')
        self.assertIsNone(p['destination'])
        self.maps['Alpha']['warp_events'][0].update(dest_map='MAP_BETA', dest_warp_id=4)
        self.assertEqual(self.portal()['status'], 'unresolved')

    def test_source_and_destination_coordinates_checked_against_own_layout(self):
        self.maps['Alpha']['warp_events'][0]['x'] = 10
        p = self.portal()
        self.assertEqual(p['status'], 'out_of_bounds')
        self.assertIn('Source warp', p['reason'])
        self.maps['Alpha']['warp_events'][0]['x'] = 2
        self.maps['Beta']['warp_events'][0]['y'] = -1
        p = self.portal()
        self.assertEqual(p['status'], 'out_of_bounds')
        self.assertIn('Destination warp', p['reason'])
        self.assertFalse(p['destination']['in_bounds'])

    def test_invalid_dynamic_source_still_needs_review(self):
        self.maps['Alpha']['warp_events'][0].update(dest_map='MAP_DYNAMIC', x=True)
        p = self.portal()
        self.assertEqual(p['status'], 'out_of_bounds')
        self.assertEqual(p['resolution'], 'dynamic')

    def test_one_way_fixed_warps_are_not_broken(self):
        self.maps['Beta']['warp_events'][0]['dest_map'] = 'MAP_DYNAMIC'
        p = self.portal()
        self.assertEqual(p['status'], 'fixed')
        self.assertFalse(p['reciprocal'])
        audit = self.audit()
        self.assertEqual(audit['counts']['one_way'], 1)
        self.assertEqual(audit['counts']['broken'], 0)

    def test_invalid_boolean_warp_index_is_not_treated_as_one(self):
        self.maps['Alpha']['warp_events'][0]['dest_warp_id'] = True
        self.assertEqual(self.portal()['status'], 'unresolved')

    def test_snapshot_is_not_mutated_and_revision_is_preserved(self):
        snapshot = Connections(self.source)._snapshot()
        original = deepcopy(snapshot)
        audit = audit_warps(snapshot)
        self.assertEqual(snapshot, original)
        self.assertEqual(audit['revision'], snapshot['revision'])

    @unittest.skipUnless(SOURCE.exists(), 'Local game source is unavailable')
    def test_real_source_audit_is_read_only_and_covers_shared_warps(self):
        paths = list((SOURCE / 'data/maps').glob('*/map.json'))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        snapshot = Connections(SOURCE)._snapshot()
        audit = audit_warps(snapshot)
        self.assertEqual(audit['counts']['total'], sum(len(snapshot['maps'][owner].get('warp_events', []))
                                                     for owner in snapshot['owners'].values()))
        self.assertEqual(audit['counts']['total'], sum(audit['counts'][k] for k in
                         ('fixed', 'dynamic', 'scripted', 'unresolved', 'out_of_bounds')))
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        house = next(p for p in audit['portals'] if p['id'] == 'warp:LittlerootTown_BrendansHouse_1F:0')
        self.assertEqual(house['dest_name'], 'LittlerootTown')
        self.assertEqual(house['dest_index'], 1)


if __name__ == '__main__':
    unittest.main()
