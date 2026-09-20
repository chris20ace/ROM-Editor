"""One terrain canvas assembled from source map connections, without source writes.

Each connected component uses real metatile coordinates. Disconnected areas are
packed separately for browsing; those display offsets do not imply travel links.
Inconsistent source cycles and overlapping rectangles are exposed to the UI.
"""
from collections import deque
import re

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


class WorldMap:
    def __init__(self, source):
        self.connections = Connections(source)

    def catalog(self):
        snapshot = self.connections._snapshot()
        all_maps = snapshot['maps']
        maps = {name: data for name, data in all_maps.items()
                if data.get('map_type') in OUTDOOR_TYPES or name in OUTDOOR_EXCEPTIONS}
        dimensions = {}
        warnings = []
        for name, data in maps.items():
            layout = snapshot['layouts'][data['layout']]
            width, height = layout['width'], layout['height']
            if type(width) is not int or type(height) is not int or not 1 <= width <= 255 or not 1 <= height <= 255:
                raise ValueError(f'Invalid dimensions for outdoor map {name}.')
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

        # Keep Hoenn first, then larger detached groups, then individual areas and
        # archives. Each component receives one translation, preserving every
        # within-component coordinate and all source seam inconsistencies.
        components.sort(key=lambda component: ('LittlerootTown' not in component['names'],
                                                component['archived'], -len(component['names']), component['id']))
        canvas_width = max((_bounds(c['names'], positions, dimensions)['width'] for c in components), default=0)
        cursor_x, cursor_y, row_height = 0, 0, 0
        archived_row = False
        for index, component in enumerate(components):
            bounds = _bounds(component['names'], positions, dimensions)
            if index == 0:
                target_x, target_y = 0, 0
                cursor_y = bounds['height'] + COMPONENT_GAP
            else:
                if component['archived'] and not archived_row:
                    if cursor_x:
                        cursor_y += row_height + COMPONENT_GAP
                    cursor_x, row_height, archived_row = 0, 0, True
                if cursor_x and cursor_x + bounds['width'] > canvas_width:
                    cursor_x, cursor_y, row_height = 0, cursor_y + row_height + COMPONENT_GAP, 0
                target_x, target_y = cursor_x, cursor_y
                cursor_x += bounds['width'] + COMPONENT_GAP
                row_height = max(row_height, bounds['height'])
            shift_x, shift_y = target_x - bounds['x'], target_y - bounds['y']
            for name in component['names']:
                x, y = positions[name]
                positions[name] = (x + shift_x, y + shift_y)
            component['bounds'] = _bounds(component['names'], positions, dimensions)

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
                               'overlaps': sorted(map_overlaps[name]),
                               'preview_url': f'/api/world/maps/{name}/preview.png'})
        if conflicts:
            warnings.append(f'{len(conflicts)} map connection seams disagree around map cycles. Positions follow a consistent traversal; no source connections were changed.')
        if overlaps:
            warnings.append(f'{len(overlaps)} map rectangles overlap. Select the map you want to edit to bring its terrain forward.')
        if len(components) > 1:
            warnings.append('Detached areas are displayed below the main world. Their display spacing does not create travel connections.')
        bounds = _bounds(list(maps), positions, dimensions)
        return {'revision': snapshot['revision'], 'maps': result, 'components': components, 'bounds': bounds,
                'initial_bounds': components[0]['bounds'] if components else bounds,
                'conflicts': conflicts, 'overlaps': overlaps, 'warnings': warnings}
