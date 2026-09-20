"""A read-only, place-oriented catalog of Emerald maps.

Map sections describe the in-game town map and are not reliable ownership for
new maps copied from templates. Explicit location names take priority. This
keeps a new MyTown out of Littleroot even when it inherits Littleroot's section.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re


TOWNS = (
    "LittlerootTown", "OldaleTown", "PetalburgCity", "RustboroCity",
    "DewfordTown", "SlateportCity", "MauvilleCity", "VerdanturfTown",
    "FallarborTown", "LavaridgeTown", "FortreeCity", "LilycoveCity",
    "MossdeepCity", "SootopolisCity", "PacifidlogTown", "EverGrandeCity",
)
LANDMARKS = (
    "AbandonedShip", "AlteringCave", "AncientTomb", "AquaHideout", "ArtisanCave",
    "CaveOfOrigin", "DesertRuins", "DesertUnderpass", "FieryPath", "GraniteCave",
    "IslandCave", "JaggedPass", "MagmaHideout", "MarineCave", "MeteorFalls",
    "MirageTower", "MtChimney", "MtPyre", "NavelRock", "NewMauville",
    "PetalburgWoods", "RusturfTunnel", "ScorchedSlab", "SeafloorCavern",
    "SealedChamber", "ShoalCave", "SkyPillar", "TerraCave", "VictoryRoad",
)
SPECIAL_PLACES = ("BattleFrontier", "BirthIsland", "FarawayIsland", "SafariZone",
                  "SouthernIsland", "TrainerHill")
OUTDOOR_TYPES = {"MAP_TYPE_TOWN", "MAP_TYPE_CITY", "MAP_TYPE_ROUTE", "MAP_TYPE_OCEAN_ROUTE"}
ROLE_LABELS = {
    "outdoors": "Outdoors", "homes": "Homes", "shops": "Shops & services",
    "pokemon_centers": "Pokémon Centers", "gyms": "Gyms & League",
    "story": "Story & activities", "dungeon": "Caves & dungeons",
    "floors": "Floors & rooms", "other": "Other locations",
}
ROLE_ORDER = {role: index for index, role in enumerate(ROLE_LABELS)}
EXTERIORS = {
    "BattleFrontier": "BattleFrontier_OutsideWest", "MtPyre": "MtPyre_Exterior",
    "AbandonedShip": "AbandonedShip_Deck", "NavelRock": "NavelRock_Exterior",
    "SafariZone": "SafariZone_South", "BirthIsland": "BirthIsland_Exterior",
    "SouthernIsland": "SouthernIsland_Exterior", "FarawayIsland": "FarawayIsland_Entrance",
    "SkyPillar": "SkyPillar_Outside",
}
SPECIAL_NAMES = {"SecretBases": "Secret Bases", "ContestHalls": "Contest Halls",
                 "LinkFacilities": "Link & Multiplayer", "SSTidal": "S.S. Tidal",
                 "InsideOfTruck": "Moving Truck"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def humanize(name):
    """Split source names without turning a floor such as 1F into '1 F'."""
    if name in SPECIAL_NAMES:
        return SPECIAL_NAMES[name]
    parts = []
    for token in name.split("_"):
        if re.fullmatch(r"(?:B?\d+F|\d+[RP])", token):
            parts.append(token)
            continue
        token = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", token)
        token = re.sub(r"([a-z])([A-Z])", r"\1 \2", token)
        token = re.sub(r"([A-Za-z])(\d+)", r"\1 \2", token)
        token = re.sub(r"(\d)([A-Z][a-z])", r"\1 \2", token)
        parts.append(token)
    value = " · ".join(parts)
    replacements = {"Pokemon": "Pokémon", "Mt": "Mt.", "Mr": "Mr.", "Brendans": "Brendan's", "Mays": "May's",
                    "Birchs": "Birch's", "Stevens": "Steven's", "Scotts": "Scott's", "Wallys": "Wally's",
                    "Wandas": "Wanda's", "Lanettes": "Lanette's", "Cozmos": "Cozmo's", "Brineys": "Briney's",
                    "Captains": "Captain's", "Masters": "Master's", "Familys": "Family's",
                    "Maniacs": "Maniac's", "Hunters": "Hunter's", "Raters": "Rater's", "Cutters": "Cutter's",
                    "Relearners": "Relearner's", "Deleters": "Deleter's", "Ladys": "Lady's", "Sterns": "Stern's",
                    "Sidneys": "Sidney's", "Phoebes": "Phoebe's", "Glacias": "Glacia's", "Drakes": "Drake's", "Champions": "Champion's"}
    for source, target in replacements.items():
        value = re.sub(r"\b" + source + r"\b", target, value)
    return value


def _natural(value):
    return tuple((0, int(part)) if part.isdecimal() else (1, part.lower())
                 for part in re.split(r"(\d+)", value) if part)


def _matches(name, prefix):
    return name == prefix or name.startswith(prefix + "_")


def _kind(data):
    map_type = data.get("map_type", "")
    if map_type in {"MAP_TYPE_TOWN", "MAP_TYPE_CITY"}:
        return "town"
    if map_type in {"MAP_TYPE_ROUTE", "MAP_TYPE_OCEAN_ROUTE"}:
        return "route"
    if map_type in {"MAP_TYPE_UNDERGROUND", "MAP_TYPE_UNDERWATER"}:
        return "dungeon"
    return "special"


def _known_area(name):
    # Underwater companions belong to their route, town or named cave. Their
    # MAPSEC often differs from the surface map, so section alone cannot group.
    canonical = name.removeprefix("Underwater_")
    for town in TOWNS:
        if _matches(canonical, town):
            return town, "town"
    route = re.match(r"^Route(\d+)(?=$|_|Prototype)", canonical)
    if route and 101 <= int(route[1]) <= 134:
        return "Route" + route[1], "route"
    for prefix in LANDMARKS:
        if _matches(canonical, prefix):
            return prefix, "dungeon"
    for prefix in SPECIAL_PLACES:
        if _matches(canonical, prefix):
            return prefix, "special"
    if re.fullmatch(r"BattlePyramidSquare\d+", name):
        return "BattleFrontier", "special"
    if _matches(name, "SecretBase"):
        return "SecretBases", "special"
    if name.startswith("SSTidal"):
        return "SSTidal", "special"
    if re.fullmatch(r"(?:Unused)?ContestHall(?:Beauty|Cool|Cute|Smart|Tough|\d+)?", name):
        return "ContestHalls", "special"
    if name in {"RecordCorner", "TradeCenter", "UnionRoom"} or _matches(name, "BattleColosseum"):
        return "LinkFacilities", "special"
    if name == "InsideOfTruck":
        return name, "special"
    return None


def _role(name, data, area_id, area_kind):
    if data.get("map_type") in OUTDOOR_TYPES or name == EXTERIORS.get(area_id):
        return "outdoors"
    if "PokemonCenter" in name:
        return "pokemon_centers"
    if "Gym" in name or (area_id == "EverGrandeCity" and any(term in name for term in
            ("PokemonLeague", "ChampionsRoom", "SidneysRoom", "PhoebesRoom", "GlaciasRoom", "DrakesRoom", "Hall"))):
        return "gyms"
    if any(term in name for term in ("Mart", "Shop", "DepartmentStore", "GlassWorkshop", "ExchangeService", "SeashoreHouse")):
        return "shops"
    # A puzzle facility named TrickHouse is not a residence.
    if any(term in name for term in ("TrickHouse", "ProfessorBirch", "Lab", "DevonCorp", "Museum", "SpaceCenter",
                                    "WeatherInstitute", "PokemonSchool", "PokemonDayCare", "BattleTent", "GameCorner",
                                    "FanClub", "ContestHall", "ContestLobby", "SternsShipyard", "MysteryEvents")) or name == "InsideOfTruck":
        return "story"
    if any(term in name for term in ("House", "Motel", "Flat", "RestStop")):
        return "homes"
    if re.search(r"_(?:B?\d+F|(?:Up|Down)\d+)(?:_|$)", name) or any(term in name for term in
            ("BattleRoom", "PreBattleRoom", "BattlePyramidSquare", "BattlePyramidFloor", "Corridor", "Rooms")):
        return "floors"
    if data.get("map_type") in {"MAP_TYPE_UNDERGROUND", "MAP_TYPE_UNDERWATER"} or area_kind == "dungeon":
        return "dungeon"
    return "other"


def _display_name(name, area_id):
    if name == area_id:
        return humanize(name)
    if name.startswith("Underwater_"):
        target = name.removeprefix("Underwater_")
        return "Underwater" if target == area_id else "Underwater · " + humanize(target)
    if name.startswith(area_id + "_"):
        return humanize(name[len(area_id) + 1:])
    if area_id == "Route104" and name.startswith("Route104Prototype"):
        return humanize(name[len("Route104"):])
    if area_id == "SecretBases" and name.startswith("SecretBase_"):
        return humanize(name[len("SecretBase_"):])
    if area_id == "SSTidal":
        return humanize(name.removeprefix("SSTidal").lstrip("_"))
    return humanize(name)


class Areas:
    def __init__(self, source, state=None):
        self.source = Path(source).resolve()
        # Accepted for consistency with other catalogs. Grouping is derived from
        # source names; this catalog does not create workspace preference files.
        self.state = Path(state).resolve() if state is not None else None

    def _read(self, path):
        target = Path(path).resolve()
        if not target.is_relative_to(self.source):
            raise ValueError("Area catalog source must stay inside the project")
        try:
            result = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read area source {target.name}: {error}") from error
        if not isinstance(result, dict):
            raise ValueError("Area source must contain an object")
        return result

    @staticmethod
    def _event_owner(name, maps):
        seen = set()
        while name in maps:
            if name in seen:
                raise ValueError("Shared map events form a cycle")
            seen.add(name)
            parent = maps[name].get("shared_events_map")
            if not parent:
                return maps[name]
            name = parent
        raise ValueError("Shared map events reference a missing map")

    def catalog(self):
        layouts = self._read(self.source / "data/layouts/layouts.json").get("layouts")
        if not isinstance(layouts, list):
            raise ValueError("Layout catalog is missing")
        layouts = {row["id"]: row for row in layouts}
        maps = {}
        for path in sorted((self.source / "data/maps").glob("*/map.json")):
            data = self._read(path)
            name = data.get("name")
            if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or name != path.parent.name or name in maps:
                raise ValueError("Map names must be unique and match their source folder")
            maps[name] = data
        areas = {}
        for name, data in maps.items():
            known = _known_area(name)
            if known:
                area_id, area_kind = known
            else:
                # A copied region_map_section is never ownership. Names such as
                # MyTown and MyTown_House1 form their own place without requiring
                # town-map artwork or region constants to be added first.
                parents = [candidate for candidate in maps if name.startswith(candidate + "_")]
                area_id = min(parents, key=len) if parents else name
                root = maps[area_id]
                area_kind = _kind(root)
            if area_id not in areas:
                areas[area_id] = {"id": area_id, "name": humanize(area_id), "kind": area_kind,
                                  "exterior": None, "maps": [], "count": 0}
            area = areas[area_id]
            layout = layouts.get(data.get("layout"))
            if layout is None:
                raise ValueError(f"Map {name} references an unknown layout")
            role = _role(name, data, area_id, area["kind"])
            owner = self._event_owner(name, maps)
            counts = {label: len(owner.get(key, [])) for label, key in
                      (("objects", "object_events"), ("warps", "warp_events"),
                       ("triggers", "coord_events"), ("interactions", "bg_events"))}
            connections = data.get("connections") or []
            if not isinstance(connections, list):
                raise ValueError(f"Map {name} has invalid connections")
            area["maps"].append({"name": name, "id": data.get("id", ""),
                                 "width": layout["width"], "height": layout["height"],
                                 "map_type": data.get("map_type", ""), "region_map_section": data.get("region_map_section", ""),
                                 "role": role, "role_label": ROLE_LABELS[role], "display_name": _display_name(name, area_id),
                                 "event_counts": counts, "connections": deepcopy(connections)})
        for area in areas.values():
            names = {row["name"] for row in area["maps"]}
            outdoors = [row["name"] for row in area["maps"] if row["role"] == "outdoors"]
            if area["id"] in outdoors:
                area["exterior"] = area["id"]
            elif EXTERIORS.get(area["id"]) in outdoors:
                area["exterior"] = EXTERIORS[area["id"]]
            elif outdoors:
                area["exterior"] = sorted(outdoors, key=_natural)[0]
            area["maps"].sort(key=lambda row: (row["name"] != area["exterior"], ROLE_ORDER[row["role"]], _natural(row["display_name"])))
            area["count"] = len(names)
        rank = {"town": 0, "route": 1, "dungeon": 2, "special": 3}
        return {"areas": sorted(areas.values(), key=lambda area: (rank[area["kind"]], _natural(area["name"]))),
                "map_count": len(maps)}
