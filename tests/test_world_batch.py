from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock

from world import World, words
from world_batch import MAX_BATCH_MAPS, plan_world_batch


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"


class WorldBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(SOURCE)
        layouts = defaultdict(list)
        for row in cls.world.list_maps()["maps"]:
            layouts[row["layout"]].append(row["name"])
        cls.shared = next(names[:2] for names in layouts.values() if len(names) > 1)

    def paint(self, name):
        body = self.world.get_map(name)
        original = body["cells"][22]
        body["cells"][22] = (original & 0xFC00) | (2 if (original & 1023) == 1 else 1)
        return body

    def shared_bodies(self):
        bodies = [self.world.get_map(name) for name in self.shared]
        for body in bodies:
            body["confirm_shared"] = True
        self.assertEqual(bodies[0]["layout"]["blockdata_filepath"], bodies[1]["layout"]["blockdata_filepath"])
        return bodies

    def test_two_map_painting_produces_exactly_two_binary_writes_without_mutating_source(self):
        bodies = [self.paint(name) for name in ("LittlerootTown", "Route101")]
        originals = {body["layout"]["blockdata_filepath"]: (SOURCE / body["layout"]["blockdata_filepath"]).read_bytes() for body in bodies}
        plan = plan_world_batch(self.world, bodies)
        self.assertEqual(set(plan), set(originals))
        self.assertEqual(len(plan), 2)
        for body in bodies:
            relative = body["layout"]["blockdata_filepath"]
            before, after = words(originals[relative]), words(plan[relative])
            self.assertEqual(after[22], body["cells"][22])
            self.assertNotEqual(after[22], before[22])
            self.assertEqual(after[:22], before[:22])
            self.assertEqual(after[23:], before[23:])
            self.assertEqual((SOURCE / relative).read_bytes(), originals[relative])

    def test_stale_second_map_aborts_before_any_commit(self):
        bodies = [self.paint(name) for name in ("LittlerootTown", "Route101")]
        bodies[1]["revision"] = "stale"
        commit = Mock()
        with self.assertRaisesRegex(ValueError, "Route101.*changed"):
            plan = plan_world_batch(self.world, bodies)
            commit(plan)
        commit.assert_not_called()
        current = self.world.get_map("LittlerootTown")
        self.assertNotEqual(current["cells"][22], bodies[0]["cells"][22])

    def test_conflicting_shared_layout_writes_reject_the_entire_batch(self):
        bodies = self.shared_bodies()
        bodies[0]["cells"][0] ^= 0x400
        bodies[1]["cells"][0] ^= 0x800
        original = (SOURCE / bodies[0]["layout"]["blockdata_filepath"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "different contents for shared file"):
            plan_world_batch(self.world, bodies)
        self.assertEqual((SOURCE / bodies[0]["layout"]["blockdata_filepath"]).read_bytes(), original)

    def test_identical_shared_writes_coalesce_once(self):
        bodies = self.shared_bodies()
        for body in bodies:
            body["cells"][0] ^= 0x400
        plan = plan_world_batch(self.world, bodies)
        self.assertEqual(set(plan), {bodies[0]["layout"]["blockdata_filepath"]})

    def test_noop_shared_map_does_not_conflict_with_an_edit(self):
        bodies = self.shared_bodies()
        bodies[0]["cells"][0] ^= 0x400
        plan = plan_world_batch(self.world, bodies)
        self.assertEqual(set(plan), {bodies[0]["layout"]["blockdata_filepath"]})
        self.assertEqual(plan_world_batch(self.world, list(reversed(bodies))), plan)

    def test_each_map_still_requires_shared_edit_confirmation(self):
        bodies = self.shared_bodies()
        bodies[0].pop("confirm_shared")
        bodies[0]["cells"][0] ^= 0x400
        with self.assertRaisesRegex(ValueError, "shared"):
            plan_world_batch(self.world, bodies)

    def test_duplicates_and_malformed_batches_reject_before_planning(self):
        fake = Mock()
        fake.plan_save.return_value = {}
        invalid = [None, {}, [], [None], [{"name":True}], [{"name":"../Map"}],
                   [{"name":"Route101"}, {"name":"Route101"}],
                   [{"name":f"Map{i}"} for i in range(MAX_BATCH_MAPS + 1)]]
        for bodies in invalid:
            with self.subTest(bodies=str(bodies)[:80]), self.assertRaises(ValueError):
                plan_world_batch(fake, bodies)
        fake.plan_save.assert_not_called()
        self.assertEqual(plan_world_batch(fake, [{"name":f"Map{i}"} for i in range(MAX_BATCH_MAPS)]), {})
        self.assertEqual(fake.plan_save.call_count, MAX_BATCH_MAPS)

    def test_noop_batch_is_empty_and_does_not_modify_request_bodies(self):
        bodies = [self.world.get_map(name) for name in ("LittlerootTown", "Route101")]
        before = deepcopy(bodies)
        self.assertEqual(plan_world_batch(self.world, bodies), {})
        self.assertEqual(bodies, before)

    def test_malformed_or_protected_file_plans_are_never_returned(self):
        fake = Mock()
        for plan in ({"../file.bin": b"x"}, {"src/data/pokemon/species_info.h": b"x"},
                     {"data/maps/LittlerootTown/map.json": None}):
            fake.plan_save.return_value = plan
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                plan_world_batch(fake, [{"name":"LittlerootTown"}])


if __name__ == "__main__":
    unittest.main()
