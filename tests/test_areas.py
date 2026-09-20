import json
from pathlib import Path
import tempfile
import unittest

from areas import Areas, TOWNS, humanize


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"


class AreasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Areas(SOURCE).catalog()
        cls.areas = {area["id"]: area for area in cls.catalog["areas"]}
        cls.maps = {row["name"]: row for area in cls.catalog["areas"] for row in area["maps"]}

    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "data/layouts").mkdir(parents=True)
        (root / "data/layouts/layouts.json").write_bytes((SOURCE / "data/layouts/layouts.json").read_bytes())
        for name in ("LittlerootTown", "LittlerootTown_BrendansHouse_1F", "Route104", "Route104_MrBrineysHouse"):
            directory = root / "data/maps" / name
            directory.mkdir(parents=True)
            (directory / "map.json").write_bytes((SOURCE / "data/maps" / name / "map.json").read_bytes())
        return root

    def add_map(self, root, name, template="LittlerootTown", **updates):
        data = json.loads((SOURCE / "data/maps" / template / "map.json").read_text(encoding="utf-8"))
        data.update(name=name, id="MAP_TEST_" + name.upper(), **updates)
        path = root / "data/maps" / name / "map.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_every_original_map_belongs_to_exactly_one_area(self):
        all_names = [row["name"] for area in self.catalog["areas"] for row in area["maps"]]
        expected = {path.parent.name for path in (SOURCE / "data/maps").glob("*/map.json")}
        self.assertEqual(len(all_names), 518)
        self.assertEqual(self.catalog["map_count"], 518)
        self.assertEqual(len(all_names), len(set(all_names)))
        self.assertEqual(set(all_names), expected)
        self.assertEqual(sum(area["kind"] == "town" for area in self.catalog["areas"]), 16)
        self.assertEqual(sum(area["kind"] == "route" for area in self.catalog["areas"]), 34)
        for area in self.catalog["areas"]:
            self.assertEqual(area["count"], len(area["maps"]))
            if area["exterior"]:
                self.assertEqual(area["maps"][0]["name"], area["exterior"])

    def test_towns_and_routes_include_their_interiors_and_underwater_maps(self):
        for town in TOWNS:
            area = self.areas[town]
            self.assertEqual(area["kind"], "town")
            self.assertEqual(area["exterior"], town)
        little = {m["name"]:m["role"] for m in self.areas["LittlerootTown"]["maps"]}
        self.assertEqual(little["LittlerootTown_BrendansHouse_1F"], "homes")
        self.assertEqual(little["LittlerootTown_ProfessorBirchsLab"], "story")
        self.assertEqual(self.maps["OldaleTown_Mart"]["role"], "shops")
        self.assertEqual(self.maps["OldaleTown_PokemonCenter_2F"]["role"], "pokemon_centers")
        self.assertEqual(self.maps["SootopolisCity_Gym_B1F"]["role"], "gyms")
        self.assertEqual(self.maps["EverGrandeCity_ChampionsRoom"]["role"], "gyms")
        self.assertIn("Underwater_Route124", {m["name"] for m in self.areas["Route124"]["maps"]})
        self.assertIn("Underwater_SootopolisCity", {m["name"] for m in self.areas["SootopolisCity"]["maps"]})
        route104 = self.areas["Route104"]["maps"]
        self.assertTrue(any(m["name"] == "Route104_Prototype" and "Prototype" in m["display_name"] for m in route104))

    def test_landmarks_and_special_spaces_are_not_lost_in_dynamic_sections(self):
        self.assertEqual(self.areas["RusturfTunnel"]["kind"], "dungeon")
        self.assertEqual(self.areas["MtPyre"]["count"], 8)
        self.assertEqual(self.areas["NavelRock"]["count"], 22)
        self.assertEqual(self.areas["NavelRock"]["exterior"], "NavelRock_Exterior")
        self.assertEqual(self.areas["ShoalCave"]["count"], 7)
        self.assertEqual(self.areas["SecretBases"]["count"], 24)
        frontier = self.areas["BattleFrontier"]
        self.assertEqual(frontier["kind"], "special")
        self.assertEqual(frontier["count"], 63)
        self.assertIn("BattlePyramidSquare01", {m["name"] for m in frontier["maps"]})
        self.assertEqual(self.areas["SSTidal"]["count"], 3)
        self.assertEqual(self.areas["LinkFacilities"]["count"], 5)
        self.assertEqual(self.areas["ContestHalls"]["count"], 12)

    def test_map_metadata_has_actual_layout_counts_and_neighbor_links(self):
        town = self.maps["LittlerootTown"]
        self.assertEqual((town["width"], town["height"]), (20,20))
        self.assertEqual(town["event_counts"], {"objects":8,"warps":3,"triggers":9,"interactions":4})
        self.assertEqual(town["connections"], [{"map":"MAP_ROUTE101","offset":0,"direction":"up"}])
        self.assertEqual(self.maps["ContestHallBeauty"]["event_counts"], self.maps["ContestHall"]["event_counts"])
        self.assertGreater(self.maps["ContestHallBeauty"]["event_counts"]["objects"], 0)
        self.assertEqual(self.maps["LittlerootTown_BrendansHouse_1F"]["connections"], [])

    def test_custom_places_do_not_inherit_template_region_ownership(self):
        root = self.fixture()
        town = self.add_map(root, "MyNewTown")
        self.add_map(root, "MyNewTown_House1", "LittlerootTown_BrendansHouse_1F")
        self.add_map(root, "MyNewRoute", "Route104")
        self.add_map(root, "MyNewRoute_Shop", "LittlerootTown_BrendansHouse_1F")
        self.add_map(root, "LittlerootTown_NewShop", "LittlerootTown_BrendansHouse_1F")
        before = town.read_bytes()
        areas = {a["id"]:a for a in Areas(root).catalog()["areas"]}
        self.assertEqual(areas["MyNewTown"]["kind"], "town")
        self.assertEqual(areas["MyNewTown"]["count"], 2)
        self.assertEqual(areas["MyNewTown"]["exterior"], "MyNewTown")
        self.assertEqual(areas["MyNewRoute"]["kind"], "route")
        self.assertEqual(areas["MyNewRoute"]["count"], 2)
        self.assertNotIn("MyNewTown", {m["name"] for m in areas["LittlerootTown"]["maps"]})
        self.assertIn("LittlerootTown_NewShop", {m["name"] for m in areas["LittlerootTown"]["maps"]})
        self.assertEqual(town.read_bytes(), before)
        self.assertEqual(json.loads(before)["region_map_section"], "MAPSEC_LITTLEROOT_TOWN")

    def test_route_name_matching_is_anchored_and_prototype_is_explicit(self):
        root = self.fixture()
        self.add_map(root, "Route104Prototype", "Route104")
        self.add_map(root, "Route1040", "Route104")
        self.add_map(root, "LittlerootTownship", "LittlerootTown")
        areas = {a["id"]:a for a in Areas(root).catalog()["areas"]}
        self.assertIn("Route104Prototype", {m["name"] for m in areas["Route104"]["maps"]})
        self.assertIn("Route1040", areas)
        self.assertIn("LittlerootTownship", areas)

    def test_unknown_underscore_names_are_independent_without_a_real_parent_map(self):
        root = self.fixture()
        self.add_map(root, "Custom_TownOne")
        self.add_map(root, "Custom_TownTwo")
        self.add_map(root, "Custom_TownOne_House", "LittlerootTown_BrendansHouse_1F")
        areas = {a["id"]:a for a in Areas(root).catalog()["areas"]}
        self.assertNotIn("Custom", areas)
        self.assertEqual(areas["Custom_TownOne"]["count"], 2)
        self.assertEqual(areas["Custom_TownTwo"]["count"], 1)
        self.assertEqual(areas["Custom_TownOne"]["kind"], "town")

    def test_human_labels_preserve_floor_markers_and_sort_numbers(self):
        self.assertEqual(humanize("LittlerootTown_BrendansHouse_1F"), "Littleroot Town · Brendan's House · 1F")
        self.assertEqual(humanize("MagmaHideout_B1F_1R"), "Magma Hideout · B1F · 1R")
        self.assertEqual(humanize("Route104_PrototypePrettyPetalFlowerShop"), "Route 104 · Prototype Pretty Petal Flower Shop")
        self.assertEqual(humanize("PokemonCenter_2F"), "Pokémon Center · 2F")
        self.assertEqual(humanize("SSTidal"), "S.S. Tidal")
        routes = [a["id"] for a in self.catalog["areas"] if a["kind"] == "route"]
        self.assertEqual(routes, [f"Route{i}" for i in range(101,135)])

    def test_shared_event_cycles_are_reported_without_source_writes(self):
        root = self.fixture()
        path = self.add_map(root, "CycleTown", shared_events_map="CycleTown")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "cycle"):
            Areas(root).catalog()
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
