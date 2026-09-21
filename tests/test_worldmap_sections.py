"""Exact source-connected sections, independent of full-canvas packing."""
from collections import deque
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import unittest

from worldmap_sections import partition_maps


SOURCE = Path(__file__).resolve().parents[1] / 'source/pokeemerald'


class WorldMapSectionTests(unittest.TestCase):
    def assert_valid(self, names, dimensions, edges, parts):
        flattened = [name for part in parts for name in part['names']]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), set(names))
        for part in parts:
            positions = part['positions']
            self.assertEqual(set(positions), set(part['names']))
            for a, b, _direction, _offset, dx, dy in edges:
                if a in positions and b in positions:
                    self.assertEqual((positions[b][0] - positions[a][0], positions[b][1] - positions[a][1]), (dx, dy))
            for a, b in combinations(part['names'], 2):
                ax, ay = positions[a]
                bx, by = positions[b]
                aw, ah = dimensions[a]
                bw, bh = dimensions[b]
                self.assertTrue(min(ax + aw, bx + bw) <= max(ax, bx) or min(ay + ah, by + bh) <= max(ay, by))

    def test_consistent_cycle_stays_one_complete_section_and_inputs_are_unchanged(self):
        names = ['A', 'B', 'C', 'D']
        dimensions = {name: (20, 20) for name in names}
        edges = [('A', 'B', 'right', 0, 20, 0), ('B', 'D', 'down', 0, 0, 20),
                 ('D', 'C', 'left', 0, -20, 0), ('C', 'A', 'up', 0, 0, -20)]
        before = deepcopy((names, dimensions, edges))
        parts = partition_maps(names, dimensions, edges)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['positions'], {'A': (0, 0), 'B': (20, 0), 'C': (0, 20), 'D': (20, 20)})
        self.assertEqual((names, dimensions, edges), before)
        self.assert_valid(names, dimensions, edges, parts)

    def test_inconsistent_cycle_is_split_and_all_induced_constraints_are_checked(self):
        names = ['A', 'B', 'C']
        dimensions = {name: (20, 20) for name in names}
        edges = [('A', 'B', 'right', 0, 20, 0), ('A', 'C', 'down', 10, 10, 20),
                 ('B', 'C', 'down', 0, 0, 20)]
        parts = partition_maps(names, dimensions, edges)
        self.assertEqual(len(parts), 2)
        self.assert_valid(names, dimensions, edges, parts)
        self.assertEqual(partition_maps(names, dimensions, list(reversed(edges))), parts)

    def test_overlapping_neighbors_are_not_merged_even_with_consistent_offsets(self):
        names = ['A', 'B', 'C']
        dimensions = {name: (20, 20) for name in names}
        edges = [('A', 'B', 'up', 0, 0, -20), ('A', 'C', 'up', 10, 10, -20)]
        parts = partition_maps(names, dimensions, edges)
        self.assertEqual(len(parts), 2)
        self.assert_valid(names, dimensions, edges, parts)

    def test_disconnected_maps_remain_present_and_incoming_edges_work(self):
        names = ['A', 'B', 'C']
        dimensions = {'A': (12, 10), 'B': (20, 15), 'C': (3, 4)}
        edges = [('B', 'A', 'left', -2, -12, -2), ('External', 'A', 'right', 0, 7, 0)]
        parts = partition_maps(names, dimensions, edges)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]['names'], ['A', 'B'])
        self.assert_valid(names, dimensions, edges, parts)
        self.assertEqual(partition_maps([], {}, []), [])

    def test_contradictory_reciprocal_edges_are_not_hidden(self):
        names = ['A', 'B']
        dimensions = {name: (20, 20) for name in names}
        edges = [('A', 'B', 'right', 0, 20, 0), ('B', 'A', 'left', 2, -20, 2)]
        parts = partition_maps(names, dimensions, edges)
        self.assertEqual(len(parts), 2)
        self.assert_valid(names, dimensions, edges, parts)

    def test_invalid_self_connection_cannot_be_claimed_to_fit(self):
        with self.assertRaisesRegex(ValueError, 'self-connection'):
            partition_maps(['A'], {'A': (20, 20)}, [('A', 'A', 'right', 0, 20, 0)])

    @unittest.skipUnless((SOURCE / 'data/maps/map_groups.json').exists(), 'Emerald source not installed')
    def test_real_source_yields_two_exact_hoenn_sections_and_all_518_maps_once(self):
        layouts = {row['id']: row for row in json.loads((SOURCE / 'data/layouts/layouts.json').read_bytes())['layouts']}
        maps = {path.parent.name: json.loads(path.read_bytes()) for path in (SOURCE / 'data/maps').glob('*/map.json')}
        ids = {row['id']: name for name, row in maps.items()}
        dimensions = {name: (layouts[row['layout']]['width'], layouts[row['layout']]['height']) for name, row in maps.items()}
        edges = []
        adjacency = {name: [] for name in maps}
        for name, row in maps.items():
            for link in row.get('connections') or []:
                direction, offset = link['direction'], link['offset']
                if direction not in {'up', 'down', 'left', 'right'}:
                    continue
                target = ids[link['map']]
                width, height = dimensions[name]
                target_width, target_height = dimensions[target]
                dx, dy = {'up': (offset, -target_height), 'down': (offset, height),
                          'left': (-target_width, offset), 'right': (width, offset)}[direction]
                edges.append((name, target, direction, offset, dx, dy))
                adjacency[name].append(target)
                adjacency[target].append(name)
        hoenn = {'LittlerootTown'}
        queue = deque(hoenn)
        while queue:
            for target in adjacency[queue.popleft()]:
                if target not in hoenn:
                    hoenn.add(target)
                    queue.append(target)
        parts = partition_maps(sorted(hoenn), dimensions, edges)
        self.assertEqual(sorted(len(part['names']) for part in parts), [4, 45])
        self.assert_valid(hoenn, dimensions, edges, parts)
        small = next(part for part in parts if len(part['names']) == 4)
        self.assertEqual(set(small['names']), {'LittlerootTown', 'OldaleTown', 'Route101', 'Route102'})
        owners = {name: i for i, part in enumerate(parts) for name in part['names']}
        transitions = {frozenset((a, b)) for a, b, *_ in edges if a in hoenn and owners[a] != owners[b]}
        self.assertEqual(transitions, {frozenset(('OldaleTown', 'Route103')), frozenset(('PetalburgCity', 'Route102'))})
        for a, b in [('Route103', 'Route110'), ('DewfordTown', 'Route107'), ('FallarborTown', 'Route114'), ('Route116', 'VerdanturfTown')]:
            self.assertEqual(owners[a], owners[b])
        self.assertEqual(parts, partition_maps(sorted(hoenn), dimensions, list(reversed(edges))))
        all_parts = partition_maps(sorted(maps), dimensions, edges)
        self.assert_valid(maps, dimensions, edges, all_parts)
        self.assertEqual(len([name for part in all_parts for name in part['names']]), len(maps))


if __name__ == '__main__':
    unittest.main()
