"""Plan a lossless rectangular merge of maps, without writing source files."""
from copy import deepcopy
import hashlib
import json
import re

from route_split import CARDINAL, ENCOUNTERS, _number
from world import EVENT_KEYS, GROUPS, LAYOUTS, integer, json_bytes, pack_words
from workspace_edits import protected_species


TEXT_SUFFIXES = {'.c', '.h', '.cpp', '.hpp', '.inc', '.s', '.json', '.txt', '.ld', '.mk'}


def _generated_map_output(path):
    # map_data_rules.mk rebuilds these from the changed registries/map JSON.
    # Stale output from a previous build is not a hand-authored reference.
    return path in {'include/constants/map_groups.h', 'include/constants/layouts.h',
                    'include/constants/map_event_ids.h', 'data/maps/headers.inc',
                    'data/maps/events.inc', 'data/maps/connections.inc', 'data/maps/groups.inc',
                    'data/layouts/layouts.inc', 'data/layouts/layouts_table.inc'} or bool(
        re.fullmatch(r'data/maps/[^/]+/(?:header|events|connections)\.inc', path))


class RouteMerger:
    def __init__(self, world):
        self.world = world

    def preview(self, body):
        return self._build(body, False)[1]

    def plan(self, body):
        return self._build(body, True)[0]

    def _inputs(self, current):
        # All source text participates in the token audit and preview token.
        paths = {path.relative_to(self.world.source).as_posix()
                 for path in self.world.source.rglob('*')
                 if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
                 and not any(part.startswith('.') for part in path.relative_to(self.world.source).parts)}
        for item in current.values():
            paths.update((item['layout']['blockdata_filepath'], item['layout']['border_filepath']))
        inputs = {path: self.world._read(path) for path in sorted(paths)}
        digest = hashlib.sha256()
        for path, data in inputs.items():
            digest.update(path.encode() + b'\0' + data + b'\0')
        return inputs, digest.hexdigest()

    def _build(self, body, checked):
        if not isinstance(body, dict):
            raise ValueError('Map merge must be an object')
        entries = body.get('maps')
        if not isinstance(entries, list) or not 2 <= len(entries) <= 64:
            raise ValueError('Select between 2 and 64 map boxes to combine')
        if any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in entries):
            raise ValueError('Every selected map needs its name, position and revision')
        names = [item['name'] for item in entries]
        if len(set(names)) != len(names):
            raise ValueError('A map can only appear once in a merge')
        name = body.get('name')
        if name not in names:
            raise ValueError('Choose a selected map to keep as the combined map')
        world = self.world
        maps = world._maps()
        current = {item: world.get_map(item) for item in names}
        positions = {}
        for entry in entries:
            item = current[entry['name']]
            if entry.get('revision') != item['revision']:
                raise ValueError(f'{item["name"]} changed. Reload it before combining maps.')
            if item['shared_with'] or item['events_owner'] != item['name'] or item['events_shared_with']:
                raise ValueError(f'{item["name"]} shares a layout or events. Make it independent before combining maps.')
            positions[item['name']] = (integer(entry.get('x'), -100000, 100000, 'Map x'),
                                       integer(entry.get('y'), -100000, 100000, 'Map y'))
        primary = current[name]
        registered_layouts = world._json(LAYOUTS)['layouts']
        primary_terrain = world._path(primary['layout']['blockdata_filepath'])
        if any(item['id'] != primary['layout']['id']
               and world._path(item['blockdata_filepath']) == primary_terrain for item in registered_layouts):
            raise ValueError(f'{name} shares its terrain file with another layout. Give it independent terrain before combining maps.')
        for item in current.values():
            if any(item['layout'][key] != primary['layout'][key] for key in ('primary_tileset', 'secondary_tileset')):
                raise ValueError('Combined maps must use the same primary and secondary tilesets to preserve their artwork')
        x = min(point[0] for point in positions.values())
        y = min(point[1] for point in positions.values())
        width = max(positions[n][0] + current[n]['width'] for n in names) - x
        height = max(positions[n][1] + current[n]['height'] for n in names) - y
        world._validate_dimensions(width, height)
        origins = {n: (positions[n][0] - x, positions[n][1] - y) for n in names}
        cells = [None] * (width * height)
        for n in names:
            item = current[n]
            dx, dy = origins[n]
            for row in range(item['height']):
                for column in range(item['width']):
                    index = (row + dy) * width + column + dx
                    if cells[index] is not None:
                        raise ValueError('Map boxes overlap. Place them edge to edge before combining.')
                    cells[index] = item['cells'][row * item['width'] + column]
        if any(cell is None for cell in cells):
            raise ValueError('Map boxes must fill one rectangle with no gaps. Add tiles or move the boxes first.')
        inputs, revision = self._inputs(current)
        # Also bind the preview to geometry and policy; positions are editor data.
        revision = hashlib.sha256((revision + json.dumps({'name': name, 'maps': entries,
                                   'encounter_policy': body.get('encounter_policy'),
                                   'tilesets': {n: item['tileset']['revision'] for n, item in current.items()}},
                                   sort_keys=True)).encode()).hexdigest()
        if (checked or body.get('world_revision') is not None) and body.get('world_revision') != revision:
            raise ValueError('Maps, positions or scripts changed after the merge preview. Preview the merge again.')
        donors = set(names) - {name}
        ids = {data['id']: n for n, data in maps.items()}
        merged_id = maps[name]['id']
        removed_ids = {maps[n]['id'] for n in donors}
        edited = deepcopy(maps)
        merged = edited[name]
        # Keep the primary event indices stable whenever possible.
        ordered = [name] + [n for n in names if n != name]
        warp_lookup, object_lookup = {}, {}
        aliases = set()
        for key in EVENT_KEYS:
            merged[key] = []
            for n in ordered:
                dx, dy = origins[n]
                for index, event in enumerate(maps[n].get(key, [])):
                    if not isinstance(event, dict) or type(event.get('x')) is not int or type(event.get('y')) is not int:
                        raise ValueError(f'{n} {key} #{index + 1} has invalid coordinates')
                    if not (0 <= event['x'] < current[n]['width'] and 0 <= event['y'] < current[n]['height']):
                        raise ValueError(f'{n} has an off-map scripted actor or event. Review it before combining maps.')
                    target = deepcopy(event)
                    target['x'] += dx
                    target['y'] += dy
                    target_index = len(merged[key])
                    alias_key = 'local_id' if key == 'object_events' else 'warp_id' if key == 'warp_events' else None
                    alias = event.get(alias_key) if alias_key else None
                    if alias:
                        if not isinstance(alias, str) or alias in aliases:
                            raise ValueError(f'Combined {key} have conflicting named IDs ({alias}). Give them unique names first.')
                        aliases.add(alias)
                    merged[key].append(target)
                    if key in ('object_events', 'warp_events'):
                        lookup = object_lookup if key == 'object_events' else warp_lookup
                        offset = 1 if key == 'object_events' else 0
                        lookup[(maps[n]['id'], index + offset)] = target_index + offset
                        if alias:
                            lookup[(maps[n]['id'], alias)] = target_index + offset
            limit = 126 if key == 'object_events' else 255
            if len(merged[key]) > limit:
                raise ValueError(f'Combined {key} exceed Emerald’s limit of {limit}')

        layouts = world._json(LAYOUTS)
        all_layouts = {layout['id']: layout for layout in layouts['layouts']}
        new_layout = deepcopy(primary['layout'])
        new_layout.update(width=width, height=height)
        layouts['layouts'] = [new_layout if item['id'] == new_layout['id'] else item for item in layouts['layouts']]
        # Removing unused donor layouts would renumber every subsequent layout.
        plan = {LAYOUTS: json_bytes(layouts), new_layout['blockdata_filepath']: pack_words(cells)}
        for n in donors:
            del edited[n]
            plan[f'data/maps/{n}/map.json'] = None
        for n, data in edited.items():
            data['connections'] = [] if data.get('connections') is not None else None
        edge_spans = {n: {} for n in edited}

        def record_old_span(from_name, destination_name, old_link, new_link):
            target_name = ids.get(old_link.get('map'))
            direction, offset = old_link.get('direction'), old_link.get('offset')
            if target_name is None or direction not in CARDINAL or type(offset) is not int:
                return
            axis = 'height' if direction in ('left', 'right') else 'width'
            source_extent = all_layouts[maps[from_name]['layout']][axis]
            target_extent = all_layouts[maps[target_name]['layout']][axis]
            shift = origins.get(from_name, (0, 0))[1 if axis == 'height' else 0]
            start, end = max(0, offset), min(source_extent, offset + target_extent)
            key = json.dumps(new_link, sort_keys=True)
            edge_spans[destination_name].setdefault(key, set()).update(range(start + shift, end + shift))

        def extent(n):
            if n in current:
                return width, height
            layout = all_layouts[maps[n]['layout']]
            return layout['width'], layout['height']

        for from_name, data in maps.items():
            destination_name = name if from_name in current else from_name
            for link in data.get('connections') or []:
                to_name = ids.get(link.get('map'))
                touched = from_name in current or to_name in current
                if not touched:
                    if edited[destination_name]['connections'] is None:
                        edited[destination_name]['connections'] = []
                    edited[destination_name]['connections'].append(deepcopy(link))
                    record_old_span(from_name, destination_name, link, link)
                    continue
                direction, offset = link.get('direction'), link.get('offset')
                if to_name is None or direction not in CARDINAL or type(offset) is not int:
                    raise ValueError('Combining maps with dive/emerge or unresolved connections needs a manual review first.')
                if from_name in current and to_name in current:
                    continue
                source_layout, target_layout = all_layouts[data['layout']], all_layouts[maps[to_name]['layout']]
                tx, ty = {'left': (-target_layout['width'], offset), 'right': (source_layout['width'], offset),
                          'up': (offset, -target_layout['height']), 'down': (offset, source_layout['height'])}[direction]
                sx, sy = origins.get(from_name, (0, 0))
                dx, dy = origins.get(to_name, (0, 0))
                tx, ty = tx + sx - dx, ty + sy - dy
                sw, sh = extent(from_name)
                tw, th = extent(to_name)
                adjacent = {'left': tx + tw == 0, 'right': tx == sw, 'up': ty + th == 0, 'down': ty == sh}[direction]
                overlap = min(sh, ty + th) - max(0, ty) if direction in ('left', 'right') else min(sw, tx + tw) - max(0, tx)
                if not adjacent or overlap <= 0:
                    raise ValueError(f'{from_name} → {to_name} would become an interior or disconnected edge. Update that walking connection before combining.')
                depth = 8 if direction == 'right' else 7
                if (tw if direction in ('left', 'right') else th) < depth:
                    raise ValueError(f'{from_name} → {to_name} needs {depth} tiles of camera edge padding')
                target = {**deepcopy(link), 'map': merged_id if to_name in current else link['map'],
                          'offset': integer(ty if direction in ('left', 'right') else tx, -32768, 32767, 'Connection offset')}
                links = edited[destination_name]['connections']
                if links is None:
                    links = edited[destination_name]['connections'] = []
                if target not in links:
                    links.append(target)
                record_old_span(from_name, destination_name, link, target)
        self._check_edge_conflicts(name, edited, all_layouts, new_layout, edge_spans)
        for n, data in edited.items():
            if len(data.get('connections') or []) > 255:
                raise ValueError(f'Combining maps would exceed the connection limit on {n}')
            for key, map_key, index_key, lookup in (
                    ('warp_events', 'dest_map', 'dest_warp_id', warp_lookup),
                    ('object_events', 'target_map', 'target_local_id', object_lookup)):
                for event in data.get(key, []):
                    target_id = event.get(map_key)
                    if target_id not in {maps[item]['id'] for item in names}:
                        continue
                    if key == 'object_events' and event.get('type') != 'clone':
                        continue
                    value = event.get(index_key)
                    number = _number(value)
                    new_index = lookup.get((target_id, number if number is not None else value))
                    if new_index is None:
                        raise ValueError(f'{n} has a dynamic or unresolved {key} reference into {ids[target_id]}. Set its destination before combining.')
                    event[map_key] = merged_id
                    if number is not None:
                        event[index_key] = str(new_index) if isinstance(value, str) else new_index
        groups = world._json(GROUPS)
        for group in groups['group_order']:
            groups[group] = [n for n in groups[group] if n not in donors]
        plan[GROUPS] = json_bytes(groups)
        warnings = [f'{name} keeps its name, header settings, border and map callbacks. Other callback tables are preserved in the script files but are not combined.',
                    'Map group numbers can change when boxes are removed. Start a new game; existing save files are not guaranteed compatible.',
                    'Script map constants are retargeted, but literal coordinates, warp numbers, numeric object IDs and movement/cutscene assumptions need review before building.']
        different_settings = sorted({key for n in donors for key in set(maps[name]) | set(maps[n])
                                     if key not in {*EVENT_KEYS, 'connections', 'name', 'id', 'layout', 'shared_scripts_map'}
                                     and maps[name].get(key) != maps[n].get(key)})
        if different_settings:
            warnings.append(f'Using {name} values for differing settings: ' + ', '.join(different_settings) + '.')
        encounter_count, different_encounters = self._encounters(plan, names, maps, name, body.get('encounter_policy'))
        if different_encounters:
            warnings.append(f'Encounter tables differ. The combined map uses {name} encounters throughout; other selected encounter tables are removed.')
        changed_maps = []
        for n, data in edited.items():
            if data != maps[n]:
                plan[f'data/maps/{n}/map.json'] = json_bytes(data)
                changed_maps.append(n)
        # Scripts and includes stay at their original paths, retaining every
        # story label, including shared callback owners. Only MAP_* tokens move.
        token = re.compile(r'\b(?:' + '|'.join(re.escape(item) for item in sorted(removed_ids)) + r')\b')
        retargeted = []
        for path, before in inputs.items():
            if path in plan or _generated_map_output(path):
                continue
            text = before.decode('utf-8', errors='ignore')
            # The deleted map headers/events/connections are generated by
            # mapjson and will no longer be emitted. Script labels stay valid.
            for donor in donors:
                if re.search(r'\b' + re.escape(donor) + r'_(?:MapHeader|MapEvents|MapConnections(?:List)?|ObjectEvents|WarpEvents|CoordEvents|BGEvents)\b', text):
                    raise ValueError(f'{path} directly references generated data for {donor}. Review that reference before combining.')
            if not token.search(text):
                continue
            if protected_species(path):
                raise ValueError(f'{path} contains a selected map reference in protected Pokémon data. Review it before combining.')
            try:
                text = before.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError(f'{path} contains a selected map reference but is not editable UTF-8 source') from None
            updated = token.sub(merged_id, text)
            # Distinct switch branches cannot collapse to the same case label.
            old_cases = re.findall(r'\bcase\s+([^:]+):', text)
            new_cases = [token.sub(merged_id, item) for item in old_cases]
            for i, original in enumerate(old_cases):
                if original != new_cases[i] and new_cases[i] in new_cases[:i] + new_cases[i + 1:]:
                    raise ValueError(f'{path} has map-specific switch cases that would conflict. Review those cases before combining.')
            plan[path] = updated.encode('utf-8')
            retargeted.append(path)
        # JSON map edits also carry arbitrary header references, if present.
        for path, data in list(plan.items()):
            if data is not None and path.endswith('.json'):
                plan[path] = token.sub(merged_id, data.decode('utf-8')).encode('utf-8')
        details = {'name': name, 'id': merged_id, 'width': width, 'height': height, 'x': x, 'y': y,
                   'world_revision': revision, 'removed_maps': sorted(donors), 'affected_maps': changed_maps + sorted(donors),
                   'parts': [{'name': n, 'origin_x': origins[n][0], 'origin_y': origins[n][1],
                              'width': current[n]['width'], 'height': current[n]['height']} for n in names],
                   'events': {key: len(merged[key]) for key in EVENT_KEYS}, 'warnings': warnings, 'files': sorted(plan),
                   'retargeted_source_files': retargeted, 'encounter_tables_removed': encounter_count,
                   'different_encounters': different_encounters}
        return plan, details

    @staticmethod
    def _check_edge_conflicts(name, edited, old_layouts, new_layout, edge_spans):
        """Larger edge buffers must not introduce ambiguous walking exits."""
        ids = {data['id']: n for n, data in edited.items()}
        layouts = {**old_layouts, new_layout['id']: new_layout}

        def span(map_name, link):
            target = ids.get(link.get('map'))
            offset, direction = link.get('offset'), link.get('direction')
            if target is None or type(offset) is not int or direction not in CARDINAL:
                return set()
            axis = 'height' if direction in ('left', 'right') else 'width'
            start = max(0, offset)
            end = min(layouts[edited[map_name]['layout']][axis], offset + layouts[edited[target]['layout']][axis])
            return set(range(start, end))

        for map_name, data in edited.items():
            links = data.get('connections') or []
            for index, first in enumerate(links):
                if first.get('direction') not in CARDINAL:
                    continue
                for second in links[index + 1:]:
                    if second.get('direction') != first['direction']:
                        continue
                    if map_name != name and name not in (ids.get(first.get('map')), ids.get(second.get('map'))):
                        continue
                    after = span(map_name, first) & span(map_name, second)
                    before_first = edge_spans[map_name].get(json.dumps(first, sort_keys=True), set())
                    before_second = edge_spans[map_name].get(json.dumps(second, sort_keys=True), set())
                    if after - (before_first & before_second):
                        raise ValueError(f'Combining maps would overlap {map_name}’s {first["direction"]} walking connections. Adjust those links before combining.')

    def _encounters(self, plan, names, maps, primary, policy):
        if policy not in (None, 'keep_primary'):
            raise ValueError('Encounter policy must be keep_primary')
        if not self.world._path(ENCOUNTERS).is_file():
            return 0, False
        data = self.world._json(ENCOUNTERS)
        primary_id = maps[primary]['id']
        donor_ids = {maps[n]['id'] for n in names if n != primary}
        removed, different = 0, False
        for group in data['wild_encounter_groups']:
            if not group.get('for_maps'):
                continue
            entries = group.get('encounters', [])
            def contents(map_id):
                return [{key: value for key, value in entry.items() if key not in ('map', 'base_label')}
                        for entry in entries if entry.get('map') == map_id]
            kept = contents(primary_id)
            different |= any(contents(map_id) != kept for map_id in donor_ids)
            retained = [entry for entry in entries if entry.get('map') not in donor_ids]
            removed += len(entries) - len(retained)
            group['encounters'] = retained
        if different and policy != 'keep_primary':
            raise ValueError(f'Encounter tables differ. Explicitly choose to use {primary} encounters for the entire combined map.')
        if removed:
            plan[ENCOUNTERS] = json_bytes(data)
        return removed, different


def plan_merge(world, body):
    return RouteMerger(world).plan(body)
