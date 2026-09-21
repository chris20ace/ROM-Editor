"""Every source map on one terrain canvas, without source writes.

Each connected component uses real metatile coordinates. Detached interiors,
floors and other maps are packed by area; those offsets do not imply travel links.
Inconsistent source cycles use the fewest practical display breaks; every
unsatisfied source connection and overlapping rectangle is exposed to the UI.
"""
from collections import Counter, deque
import math
import re

from areas import Areas
from connections import Connections, OPPOSITE


OUTDOOR_TYPES = frozenset({'MAP_TYPE_TOWN', 'MAP_TYPE_CITY', 'MAP_TYPE_ROUTE', 'MAP_TYPE_OCEAN_ROUTE'})
# Emerald uses INDOOR/UNDERGROUND for some actual outdoor spaces to control field
# rules. An explicit list avoids treating every General-tileset cave as outdoors.
OUTDOOR_EXCEPTIONS = frozenset({
    'BirthIsland_Exterior', 'BirthIsland_Harbor',
    'FarawayIsland_Entrance', 'FarawayIsland_Interior',
    'NavelRock_Exterior', 'NavelRock_Harbor', 'NavelRock_Top',
    'AbandonedShip_Deck', 'LilycoveCity_DepartmentStoreRooftop', 'TrainerHill_Roof',
})
COMPONENT_GAP = 24
MAP_GAP = 8
SECTION_LABELS = {'town': 'Towns & cities', 'route': 'Routes',
                  'dungeon': 'Caves & landmarks', 'special': 'Other areas'}


def _is_archive(name):
    return 'Prototype' in name or 'Unused' in name


def _human_name(name):
    value = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', name.replace('_', ' '))
    return value.replace('Mt ', 'Mt. ')


def _bounds(names, positions, dimensions):
    if not names:
        return {'x': 0, 'y': 0, 'width': 0, 'height': 0}
    left = min(positions[name][0] for name in names)
    top = min(positions[name][1] for name in names)
    right = max(positions[name][0] + dimensions[name][0] for name in names)
    bottom = max(positions[name][1] + dimensions[name][1] for name in names)
    return {'x': left, 'y': top, 'width': right - left, 'height': bottom - top}


def _delta(direction, offset, source_size, target_size):
    if direction == 'up':
        return offset, -target_size[1]
    if direction == 'down':
        return offset, source_size[1]
    if direction == 'left':
        return -target_size[0], offset
    if direction == 'right':
        return source_size[0], offset
    raise ValueError('Expected a cardinal map connection.')


def _improve_component_positions(names, positions, dimensions, maps, edges):
    """Reduce arbitrary BFS seams without changing a single source constraint.

    Emerald's local connections need not close in a flat global embedding. A
    breadth-first traversal can spread one contradictory offset over several
    distant town boundaries. Try leaving individual constraints out of placement
    (never out of validation), and keep only a strictly better arrangement.
    Reciprocal connection records are one physical constraint. Bridge omissions
    are rejected, so a connected world can never be split into display shelves.
    """
    members = set(names)
    constraints = {}
    for a, b, _direction, _offset, dx, dy in edges:
        if a not in members or b not in members:
            continue
        if a > b:
            a, b, dx, dy = b, a, -dx, -dy
        key = (a, b, dx, dy)
        constraints.setdefault(key, (a, b, dx, dy))
    if not constraints:
        return
    ordered = sorted(constraints)
    adjacency = {name: [] for name in names}
    for key in ordered:
        a, b, dx, dy = constraints[key]
        adjacency[a].append((b, dx, dy, key))
        adjacency[b].append((a, -dx, -dy, key))

    def score(candidate):
        conflicts, town_seams, distance = 0, 0, 0
        for a, b, dx, dy in constraints.values():
            ax, ay = candidate[a]
            bx, by = candidate[b]
            ex, ey = bx - ax - dx, by - ay - dy
            if ex or ey:
                conflicts += 1
                town_seams += any(maps[name].get('map_type') in {'MAP_TYPE_TOWN', 'MAP_TYPE_CITY'} for name in (a, b))
                distance += abs(ex) + abs(ey)
        overlap_area = 0
        for index, a in enumerate(names):
            ax, ay = candidate[a]
            aw, ah = dimensions[a]
            for b in names[index + 1:]:
                bx, by = candidate[b]
                bw, bh = dimensions[b]
                width = min(ax + aw, bx + bw) - max(ax, bx)
                height = min(ay + ah, by + bh) - max(ay, by)
                if width > 0 and height > 0:
                    overlap_area += width * height
        return conflicts, town_seams, overlap_area, distance

    current = {name: positions[name] for name in names}
    current_score = score(current)
    if current_score[0] == 0:
        return  # Preserve exact placement for already consistent components.
    omitted = frozenset()
    while True:
        best, best_score, best_omitted = None, current_score, omitted
        for excluded in ordered:
            if excluded in omitted:
                continue
            trial_omitted = omitted | {excluded}
            candidate = {names[0]: (0, 0)}
            queue = deque([names[0]])
            while queue:
                name = queue.popleft()
                x, y = candidate[name]
                for target, dx, dy, key in adjacency[name]:
                    if key not in trial_omitted and target not in candidate:
                        candidate[target] = (x + dx, y + dy)
                        queue.append(target)
            if len(candidate) != len(names):
                continue
            candidate_score = score(candidate)
            if candidate_score < best_score:
                best, best_score, best_omitted = candidate, candidate_score, trial_omitted
        if best is None:
            break
        current, current_score, omitted = best, best_score, best_omitted
    positions.update(current)


class WorldMap:
    def __init__(self, source):
        self.connections = Connections(source)

    def catalog(self):
        snapshot = self.connections._snapshot()
        maps = snapshot['maps']
        areas = Areas(self.connections.source).catalog()['areas']
        area_order = {area['id']: index for index, area in enumerate(areas)}
        map_metadata, map_order = {}, {}
        for area in areas:
            for index, row in enumerate(area['maps']):
                map_metadata[row['name']] = {'area_id': area['id'], 'area_name': area['name'], 'area_kind': area['kind'],
                                             'role': row['role'], 'role_label': row['role_label'], 'display_name': row['display_name']}
                map_order[row['name']] = (area_order[area['id']], index)
        if set(map_metadata) != set(maps):
            raise ValueError('Maps changed while their areas were being loaded. Reload the world canvas.')
        dimensions = {}
        warnings = []
        for name, data in maps.items():
            layout = snapshot['layouts'][data['layout']]
            width, height = layout['width'], layout['height']
            if type(width) is not int or type(height) is not int or not 1 <= width <= 255 or not 1 <= height <= 255:
                raise ValueError(f'Invalid dimensions for map {name}.')
            dimensions[name] = (width, height)

        adjacency = {name: [] for name in maps}
        edges = []
        for name, data in maps.items():
            for connection in data.get('connections') or []:
                direction = connection.get('direction')
                if direction not in OPPOSITE:
                    continue  # Dive/emerge travel changes layers, not surface position.
                target = snapshot['ids'].get(connection.get('map'))
                if target not in maps:
                    if target is None:
                        warnings.append(f'{name} has an unresolved {direction} connection.')
                    continue
                offset = connection.get('offset')
                if type(offset) is not int:
                    warnings.append(f'{name} has an invalid connection offset to {target}.')
                    continue
                dx, dy = _delta(direction, offset, dimensions[name], dimensions[target])
                adjacency[name].append((target, dx, dy))
                # A one-way walking edge still constrains the physical arrangement.
                adjacency[target].append((name, -dx, -dy))
                edges.append((name, target, direction, offset, dx, dy))

        positions, components = {}, []
        seeds = (['LittlerootTown'] if 'LittlerootTown' in maps else [])
        seeds += sorted(maps, key=lambda name: (_is_archive(name), name))
        for seed in seeds:
            if seed in positions:
                continue
            positions[seed] = (0, 0)
            queue, names = deque([seed]), []
            while queue:
                name = queue.popleft()
                names.append(name)
                x, y = positions[name]
                for target, dx, dy in adjacency[name]:
                    if target not in positions:
                        positions[target] = (x + dx, y + dy)
                        queue.append(target)
            archived = all(_is_archive(name) for name in names)
            if 'LittlerootTown' in names:
                label = 'Hoenn'
            elif all(name.startswith('SafariZone_') for name in names):
                label = 'Safari Zone'
            elif all(name.startswith('BattleFrontier_') for name in names):
                label = 'Battle Frontier'
            elif archived:
                label = 'Archive: ' + _human_name(seed)
            else:
                label = _human_name(seed) + (' area' if len(names) > 1 else '')
            components.append({'id': seed, 'names': names, 'label': label, 'archived': archived})

        for component in components:
            _improve_component_positions(component['names'], positions, dimensions, maps, edges)

        def translate(component, target_x, target_y):
            bounds = _bounds(component['names'], positions, dimensions)
            shift_x, shift_y = target_x - bounds['x'], target_y - bounds['y']
            for name in component['names']:
                x, y = positions[name]
                positions[name] = (x + shift_x, y + shift_y)
            component['bounds'] = _bounds(component['names'], positions, dimensions)

        # Hoenn anchors the main canvas. Everything else receives one rigid
        # component translation into four category shelves.
        # Even a custom connection between different areas keeps its geometry.
        main = next((component for component in components if 'LittlerootTown' in component['names']), None)
        if main is None and components:
            main = max(components, key=lambda component: len(component['names']))
        if main:
            translate(main, 0, 0)
        detached = [component for component in components if component is not main]
        cluster_components = {}
        for component in detached:
            owners = Counter(map_metadata[name]['area_id'] for name in component['names'])
            owner = min(owners, key=lambda area_id: (-owners[area_id], area_order[area_id]))
            cluster_components.setdefault(owner, []).append(component)

        groups = []
        for area in areas:
            members = cluster_components.get(area['id'], [])
            if not members:
                continue
            members.sort(key=lambda component: min(map_order[name] for name in component['names']))
            sizes = [_bounds(component['names'], positions, dimensions) for component in members]
            # Compact place-sized mosaics keep floors together without making a
            # large facility into a single very tall strip of rooms.
            padded_area = sum((box['width'] + MAP_GAP) * (box['height'] + MAP_GAP) for box in sizes)
            target_width = max(max(box['width'] for box in sizes), min(320, math.ceil(math.sqrt(padded_area) * 1.6)))
            cursor_x, cursor_y, row_height = 0, 0, 0
            for component, box in zip(members, sizes):
                if cursor_x and cursor_x + box['width'] > target_width:
                    cursor_x, cursor_y, row_height = 0, cursor_y + row_height + MAP_GAP, 0
                translate(component, cursor_x, cursor_y)
                cursor_x += box['width'] + MAP_GAP
                row_height = max(row_height, box['height'])
            names = sorted((name for component in members for name in component['names']), key=map_order.__getitem__)
            group = {'id': 'area:' + area['id'], 'area_id': area['id'], 'name': area['name'], 'kind': area['kind'],
                     'names': names, 'all_names': [row['name'] for row in area['maps']],
                     'main_names': [row['name'] for row in area['maps'] if main and row['name'] in main['names']],
                     'area_ids': sorted({map_metadata[name]['area_id'] for name in names}, key=area_order.__getitem__),
                     'bounds': _bounds(names, positions, dimensions), '_components': members}
            groups.append(group)

        canvas_width = max([800, main['bounds']['width'] if main else 0] + [group['bounds']['width'] for group in groups])
        sections = []
        if main:
            sections.append({'id': 'main', 'kind': 'main', 'label': main['label'] + ' · Connected world',
                             'bounds': main['bounds'], 'names': main['names'], 'group_ids': []})
        shelf_y = (main['bounds']['height'] if main else 0) + COMPONENT_GAP
        for kind, label in SECTION_LABELS.items():
            category_groups = [group for group in groups if group['kind'] == kind]
            if not category_groups:
                continue
            cursor_x, cursor_y, row_height = 0, shelf_y + MAP_GAP, 0
            for group in category_groups:
                box = group['bounds']
                if cursor_x and cursor_x + box['width'] > canvas_width:
                    cursor_x, cursor_y, row_height = 0, cursor_y + row_height + COMPONENT_GAP, 0
                shift_x, shift_y = cursor_x - box['x'], cursor_y - box['y']
                for component in group['_components']:
                    old = component['bounds']
                    translate(component, old['x'] + shift_x, old['y'] + shift_y)
                group['bounds'] = _bounds(group['names'], positions, dimensions)
                cursor_x += box['width'] + COMPONENT_GAP
                row_height = max(row_height, box['height'])
            names = [name for group in category_groups for name in group['names']]
            section_bounds = _bounds(names, positions, dimensions)
            sections.append({'id': kind, 'kind': kind, 'label': label, 'bounds': section_bounds, 'names': names,
                             'group_ids': [group['id'] for group in category_groups]})
            shelf_y = section_bounds['y'] + section_bounds['height'] + COMPONENT_GAP

        # A two-column overview gives the whole collection a useful shape in a
        # landscape editor: Hoenn stays upper left, towns upper right, and the
        # remaining category shelves continue down those columns.
        column_bottoms = [(main['bounds']['height'] + COMPONENT_GAP) if main else 0, 0]
        category_column = {'town': 1, 'route': 0, 'dungeon': 1, 'special': 0}
        for section in sections:
            if section['id'] == 'main':
                continue
            column = category_column[section['kind']]
            target_x, target_y = column * (canvas_width + COMPONENT_GAP), column_bottoms[column]
            old_bounds = section['bounds']
            shift_x, shift_y = target_x - old_bounds['x'], target_y - old_bounds['y']
            category_groups = [group for group in groups if group['id'] in section['group_ids']]
            for group in category_groups:
                for component in group['_components']:
                    old = component['bounds']
                    translate(component, old['x'] + shift_x, old['y'] + shift_y)
                group['bounds'] = _bounds(group['names'], positions, dimensions)
            section['bounds'] = _bounds(section['names'], positions, dimensions)
            column_bottoms[column] = target_y + section['bounds']['height'] + COMPONENT_GAP

        components = ([main] if main else []) + [component for group in groups for component in group['_components']]
        group_by_map = {name: group['id'] for group in groups for name in group['names']}
        for group in groups:
            del group['_components']

        conflicts, seen_conflicts = [], set()
        for a, b, direction, offset, dx, dy in edges:
            expected = (positions[a][0] + dx, positions[a][1] + dy)
            actual = positions[b]
            if actual == expected:
                continue
            # Reciprocal records represent the same unsatisfied seam.
            key = (a, b, direction, offset) if a <= b else (b, a, OPPOSITE[direction], -offset)
            if key in seen_conflicts:
                continue
            seen_conflicts.add(key)
            conflicts.append({'a': a, 'b': b, 'direction': direction, 'offset': offset,
                              'expected': {'x': expected[0], 'y': expected[1]},
                              'actual': {'x': actual[0], 'y': actual[1]},
                              'delta': {'x': actual[0] - expected[0], 'y': actual[1] - expected[1]}})

        overlaps = []
        map_overlaps = {name: [] for name in maps}
        for component in components:
            for index, a in enumerate(component['names']):
                ax, ay = positions[a]
                aw, ah = dimensions[a]
                for b in component['names'][index + 1:]:
                    bx, by = positions[b]
                    bw, bh = dimensions[b]
                    x, y = max(ax, bx), max(ay, by)
                    width, height = min(ax + aw, bx + bw) - x, min(ay + ah, by + bh) - y
                    if width > 0 and height > 0:
                        overlaps.append({'a': a, 'b': b, 'x': x, 'y': y, 'width': width, 'height': height})
                        map_overlaps[a].append(b)
                        map_overlaps[b].append(a)

        result = []
        for component in components:
            for name in component['names']:
                data = maps[name]
                x, y = positions[name]
                width, height = dimensions[name]
                result.append({'name': name, 'id': data['id'], 'x': x, 'y': y, 'width': width, 'height': height,
                               'map_type': data.get('map_type'), 'region_map_section': data.get('region_map_section'),
                               'component': component['id'], 'archived': component['archived'],
                               **map_metadata[name], 'group': group_by_map.get(name),
                               'is_outdoor': data.get('map_type') in OUTDOOR_TYPES or name in OUTDOOR_EXCEPTIONS,
                               'overlaps': sorted(map_overlaps[name]),
                               'preview_url': f'/api/world/maps/{name}/preview.png'})
        if conflicts:
            warnings.append(f'{len(conflicts)} map connection seams disagree around map cycles. The canvas reduces these display breaks while preserving town boundaries where possible; no source connections were changed.')
        if overlaps:
            warnings.append(f'{len(overlaps)} map rectangles overlap. Select the map you want to edit to bring its terrain forward.')
        if len(components) > 1:
            warnings.append('Rooms, floors, underwater maps and other detached areas are grouped by place around the main world. Display spacing does not create travel connections.')
        bounds = _bounds(list(maps), positions, dimensions)
        return {'revision': snapshot['revision'], 'maps': result, 'components': components, 'groups': groups,
                'sections': sections, 'bounds': bounds,
                'initial_bounds': components[0]['bounds'] if components else bounds,
                'conflicts': conflicts, 'overlaps': overlaps, 'warnings': warnings}
