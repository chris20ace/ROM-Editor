"""Every source map on one terrain canvas, without source writes.

Complete source maps are joined only where all original offsets agree and no
terrain overlaps. Separate sections stay on this canvas, with explicit travel
links between them. Rooms and floors are packed by area; spacing is not travel.
"""
from collections import Counter, deque
import math
import re

from areas import Areas
from connections import Connections, OPPOSITE
from worldmap_sections import partition_maps


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
        cardinal_connections = {name: [] for name in maps}
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
                # Preserve the original direction and offset for section links
                # and same-canvas navigation, independently of display spacing.
                cardinal_connections[name].append({'map': connection['map'], 'name': target,
                                                    'direction': direction, 'offset': offset})
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

        original_components = components
        components = []
        for original in original_components:
            pieces = partition_maps(original['names'], dimensions, edges)
            for index, piece in enumerate(pieces):
                names = piece['names']
                positions.update(piece['positions'])
                components.append({'id': original['id'] if index == 0 else original['id'] + ':' + names[0],
                                   'names': names, 'label': original['label'], 'archived': original['archived'],
                                   'source_component': original['id']})

        def translate(component, target_x, target_y):
            bounds = _bounds(component['names'], positions, dimensions)
            shift_x, shift_y = target_x - bounds['x'], target_y - bounds['y']
            for name in component['names']:
                x, y = positions[name]
                positions[name] = (x + shift_x, y + shift_y)
            component['bounds'] = _bounds(component['names'], positions, dimensions)

        # All pieces of the original main component remain next to each other.
        # Their gutters are explicit editor spacing, never stretched terrain.
        main_source = next((component for component in original_components if 'LittlerootTown' in component['names']), None)
        if main_source is None and original_components:
            main_source = max(original_components, key=lambda component: len(component['names']))
        main_parts = [component for component in components
                      if main_source and component['source_component'] == main_source['id']]
        main = dict(main_source) if main_source else None
        if main_parts:
            sizes = [_bounds(part['names'], positions, dimensions) for part in main_parts]
            target_width = max(800, max(box['width'] for box in sizes))
            cursor_x, cursor_y, row_height = 0, 0, 0
            for part, box in zip(main_parts, sizes):
                if cursor_x and cursor_x + box['width'] > target_width:
                    cursor_x, cursor_y, row_height = 0, cursor_y + row_height + COMPONENT_GAP, 0
                translate(part, cursor_x, cursor_y)
                cursor_x += box['width'] + COMPONENT_GAP
                row_height = max(row_height, box['height'])
            main['bounds'] = _bounds(main['names'], positions, dimensions)
        detached = [component for component in components if component not in main_parts]
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
        for index, part in enumerate(main_parts):
            towns = [name for name in part['names'] if maps[name].get('map_type') in {'MAP_TYPE_TOWN', 'MAP_TYPE_CITY'}]
            landmarks = towns[:3] or part['names'][:2]
            part['label'] = ' / '.join(_human_name(name) for name in landmarks)
            sections.append({'id': 'main' if index == 0 else 'main:' + part['id'], 'kind': 'main',
                             'label': 'Map section · ' + part['label'], 'bounds': part['bounds'],
                             'names': part['names'], 'group_ids': []})
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
            if section['kind'] == 'main':
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

        components = main_parts + [component for group in groups for component in group['_components']]
        group_by_map = {name: group['id'] for group in groups for name in group['names']}
        for group in groups:
            del group['_components']

        component_by_map = {name: component['id'] for component in components for name in component['names']}
        transitions, conflicts, seen_conflicts = [], [], set()
        for a, b, direction, offset, dx, dy in edges:
            expected = (positions[a][0] + dx, positions[a][1] + dy)
            actual = positions[b]
            if actual == expected:
                continue
            # Reciprocal records represent the same physical connection.
            key = (a, b, direction, offset) if a <= b else (b, a, OPPOSITE[direction], -offset)
            if key in seen_conflicts:
                continue
            seen_conflicts.add(key)
            if component_by_map[a] != component_by_map[b]:
                vertical = direction in {'left', 'right'}
                size_index = 1 if vertical else 0
                start = max(0, offset)
                end = min(dimensions[a][size_index], offset + dimensions[b][size_index])
                middle = (start + end) / 2
                aw, ah = dimensions[a]
                bw, bh = dimensions[b]
                source_point = {'left': (0, middle), 'right': (aw, middle),
                                'up': (middle, 0), 'down': (middle, ah)}[direction]
                target_point = {'left': (bw, middle - offset), 'right': (0, middle - offset),
                                'up': (middle - offset, bh), 'down': (middle - offset, 0)}[direction]
                transitions.append({'id': 'link-' + str(len(transitions) + 1), 'a': a, 'b': b,
                                    'direction': direction, 'offset': offset, 'span': max(0, end - start),
                                    'from_component': component_by_map[a], 'to_component': component_by_map[b],
                                    'source': {'x': positions[a][0] + source_point[0], 'y': positions[a][1] + source_point[1]},
                                    'target': {'x': positions[b][0] + target_point[0], 'y': positions[b][1] + target_point[1]}})
                continue
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
                               'connections': cardinal_connections[name],
                               'overlaps': sorted(map_overlaps[name]),
                               'preview_url': f'/api/world/maps/{name}/preview.png'})
        if conflicts or overlaps:
            raise ValueError('Map sections must have exact internal connections and no hidden terrain.')
        if transitions:
            warnings.append(f'{len(transitions)} original connections link separate map sections. Follow their labels on the canvas; the source travel links are unchanged.')
        if len(components) > 1:
            warnings.append('Rooms, floors, underwater maps and other detached areas are grouped by place around the main world. Display spacing does not create travel connections.')
        bounds = _bounds(list(maps), positions, dimensions)
        return {'revision': snapshot['revision'], 'maps': result, 'components': components, 'groups': groups,
                'sections': sections, 'bounds': bounds,
                'initial_bounds': main['bounds'] if main else bounds,
                'transitions': transitions, 'conflicts': conflicts, 'overlaps': overlaps, 'warnings': warnings}
