"""Plan source-backed map enlargement without changing existing terrain.

Coordinates in the old rectangle are translated by the left/top additions.
Walking offsets translate both endpoint origins; their direction and map IDs
are preserved so links continue to use the enlarged map's actual outer edge.
The caller commits the complete plan through SourceTransactions.
"""
from copy import deepcopy
import hashlib

from route_split import CARDINAL, RouteSplitter
from world import EVENT_KEYS, LAYOUTS, integer, json_bytes, pack_words


class RouteExpander:
    def __init__(self, world):
        self.world = world

    def preview(self, body):
        _, details = self._build(body, checked=False)
        return details

    def plan(self, body):
        plan, _ = self._build(body, checked=True)
        return plan

    def _revision(self, current, maps):
        # Match route splitting's map/registry/script snapshot, also checking
        # artwork because the chosen fill tile must still exist at commit time.
        graph = RouteSplitter(self.world)._revision(current, maps)
        return hashlib.sha256((graph + '\0' + current['tileset']['revision']).encode()).hexdigest()

    def _build(self, body, checked):
        if not isinstance(body, dict):
            raise ValueError('Route expansion must be an object')
        world = self.world
        name = body.get('name')
        current = world.get_map(name)
        if body.get('revision') != current['revision']:
            raise ValueError('This map changed. Reload it before expanding.')
        if current['shared_with'] or current['events_owner'] != name or current['events_shared_with']:
            raise ValueError('This map shares a layout or events. Give it an independent layout and events before expanding.')
        sides = {side: integer(body.get(side, 0), 0, 254, side.title() + ' addition')
                 for side in ('top', 'bottom', 'left', 'right')}
        if not any(sides.values()):
            raise ValueError('Add at least one row or column to expand the map.')
        fill_tile = integer(body.get('fill_tile'), 0, 1023, 'Fill tile')
        if fill_tile not in {tile['id'] for tile in current['tileset']['metatiles'] if tile.get('valid')}:
            raise ValueError(f'Metatile {fill_tile} does not exist in this tileset pair')
        old_width, old_height = current['width'], current['height']
        width = old_width + sides['left'] + sides['right']
        height = old_height + sides['top'] + sides['bottom']
        world._validate_dimensions(width, height)
        maps = world._maps()
        revision = self._revision(current, maps)
        if (checked or body.get('world_revision') is not None) and body.get('world_revision') != revision:
            raise ValueError('Maps, links, scripts or artwork changed after the expansion preview. Preview the expansion again.')
        original = maps[name]
        ids = {data['id']: map_name for map_name, data in maps.items()}
        if len(ids) != len(maps):
            raise ValueError('Map identifiers must be unique before expanding.')
        layouts = world._json(LAYOUTS)
        old_layouts = {item['id']: deepcopy(item) for item in layouts['layouts']}
        new_layout = next(item for item in layouts['layouts'] if item['id'] == original['layout'])
        block_path = world._path(new_layout['blockdata_filepath'])
        if any(item['id'] != new_layout['id'] and world._path(item['blockdata_filepath']) == block_path
               for item in layouts['layouts']):
            raise ValueError('This map shares its terrain file with another layout. Give it independent terrain before expanding.')
        new_layout.update(width=width, height=height)
        planned_layouts = {item['id']: item for item in layouts['layouts']}
        cells = [0x3000 | fill_tile] * (width * height)
        for y in range(old_height):
            start = (y + sides['top']) * width + sides['left']
            cells[start:start + old_width] = current['cells'][y * old_width:(y + 1) * old_width]
        edited = deepcopy(maps)
        for key in EVENT_KEYS:
            events = edited[name].get(key, [])
            if not isinstance(events, list):
                raise ValueError(f'{key} must be an event list')
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    raise ValueError(f'{key} #{index + 1} must be an event object')
                for axis, addition in (('x', sides['left']), ('y', sides['top'])):
                    value = integer(event.get(axis), -32768, 32767, f'{key} #{index + 1} {axis}')
                    event[axis] = integer(value + addition, -32768, 32767, f'{key} #{index + 1} shifted {axis}')

        for map_name, data in maps.items():
            for index, link in enumerate(data.get('connections') or []):
                target_name = ids.get(link.get('map'))
                if map_name != name and target_name != name:
                    continue
                direction = link.get('direction')
                if target_name is None or direction not in CARDINAL or type(link.get('offset')) is not int:
                    raise ValueError('Expanding maps with dive/emerge or unresolved links needs a manual connection review first.')
                tangent = 'top' if direction in ('left', 'right') else 'left'
                shift_from = sides[tangent] if map_name == name else 0
                shift_to = sides[tangent] if target_name == name else 0
                offset = integer(link['offset'] + shift_from - shift_to, -32768, 32767, 'Connection offset')
                edited[map_name]['connections'][index]['offset'] = offset
                source_layout = planned_layouts[data['layout']]
                target_layout = planned_layouts[maps[target_name]['layout']]
                axis = 'height' if direction in ('left', 'right') else 'width'
                if min(source_layout[axis], offset + target_layout[axis]) <= max(0, offset):
                    raise ValueError(f'{map_name} has a connection to {target_name} with no shared edge. Fix it before expanding.')
                normal_axis = 'width' if direction in ('left', 'right') else 'height'
                depth = 8 if direction == 'right' else 7
                if target_layout[normal_axis] < depth:
                    raise ValueError(f'{map_name} → {target_name} needs {depth} tiles of camera edge padding. Enlarge that connected map first.')

        self._check_new_edge_conflicts(name, maps, edited, ids, old_layouts, planned_layouts)
        plan = {LAYOUTS: json_bytes(layouts), current['layout']['blockdata_filepath']: pack_words(cells)}
        changed_maps = []
        for map_name, data in edited.items():
            if data != maps[map_name]:
                plan[f'data/maps/{map_name}/map.json'] = json_bytes(data)
                changed_maps.append(map_name)
        warnings = [
            'Coordinates written directly in story scripts are not automatically shifted; review scripted movement and cutscenes before building the game.',
            'Existing entrances and terrain stay in place inside the enlarged map. Walking connections now cross its new outer edges; paint suitable paths through the added terrain.',
            'Added cells use the selected fill tile with collision 0 (passable) and elevation 3; tile behavior can still restrict movement.'
        ]
        details = {'name': name, 'revision': current['revision'], 'world_revision': revision,
                   'width': width, 'height': height, 'old_width': old_width, 'old_height': old_height,
                   'origin_x': -sides['left'], 'origin_y': -sides['top'],
                   'added_cells': width * height - old_width * old_height,
                   'fill_tile': fill_tile, 'fill_cell': 0x3000 | fill_tile, 'additions': sides,
                   'warnings': warnings, 'affected_maps': sorted(set(changed_maps) | {name}),
                   'files': sorted(plan)}
        return plan, details

    @staticmethod
    def _check_new_edge_conflicts(name, original, edited, ids, old_layouts, new_layouts):
        """Growing an edge must not make two previously separate links overlap."""
        def interval(map_name, link, maps, layouts):
            target_name = ids.get(link.get('map'))
            if target_name is None or type(link.get('offset')) is not int:
                return None
            axis = 'height' if link['direction'] in ('left', 'right') else 'width'
            start = max(0, link['offset'])
            end = min(layouts[maps[map_name]['layout']][axis],
                      link['offset'] + layouts[maps[target_name]['layout']][axis])
            return start, max(start, end)

        def overlap(a, b):
            return max(0, min(a[1], b[1]) - max(a[0], b[0])) if a and b else 0

        for map_name, data in edited.items():
            links = data.get('connections') or []
            for index, first in enumerate(links):
                if first.get('direction') not in CARDINAL:
                    continue
                for other_index in range(index + 1, len(links)):
                    second = links[other_index]
                    if second.get('direction') != first['direction']:
                        continue
                    if map_name != name and name not in (ids.get(first.get('map')), ids.get(second.get('map'))):
                        continue
                    old_links = original[map_name]['connections']
                    before = overlap(interval(map_name, old_links[index], original, old_layouts),
                                     interval(map_name, old_links[other_index], original, old_layouts))
                    after = overlap(interval(map_name, first, edited, new_layouts),
                                    interval(map_name, second, edited, new_layouts))
                    if after > before:
                        raise ValueError(f'Expansion would overlap {map_name}’s {first["direction"]} walking connections. Adjust those links before expanding.')
