"""Plan a lossless, source-backed rectangular map split without writing files.

The original map keeps the left/top portion and its ID. The new map receives
the other portion. All connection and warp edits are included in one plan so
the caller can commit them through the existing backed-up source transaction.
"""
from copy import deepcopy
import hashlib
import json
import re

from world import EVENT_KEYS, GROUPS, LAYOUTS, integer, json_bytes, pack_words


CARDINAL = {'up', 'down', 'left', 'right'}
ENCOUNTERS = 'src/data/wild_encounters.json'
REMATCHES = 'src/battle_setup.c'


def _number(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value):
        return int(value)
    return None


class RouteSplitter:
    def __init__(self, world):
        self.world = world

    def _available_name(self, name, maps):
        names = {item.casefold() for item in maps}
        for number in range(2, 10000):
            candidate = name[:56] + 'Part' + str(number)
            if candidate.casefold() not in names and not any(
                    self.world._path(f'data/{kind}/{candidate}').exists()
                    for kind in ('maps', 'layouts')):
                return candidate
        raise ValueError('No available name for the new route part')

    def _revision(self, current, maps):
        # A split can update distant incoming warps and external links, not just
        # the selected map. Hash every map plus the registries and script inputs.
        paths = {LAYOUTS, GROUPS, 'data/event_scripts.s',
                 current['layout']['blockdata_filepath'], current['layout']['border_filepath']}
        paths.update(f'data/maps/{name}/map.json' for name in maps)
        paths.update(path.relative_to(self.world.source).as_posix()
                     for path in (self.world.source / 'data/maps').glob('*/scripts.inc'))
        for path in (ENCOUNTERS, REMATCHES):
            if self.world._path(path).is_file():
                paths.add(path)
        digest = hashlib.sha256()
        for path in sorted(paths):
            digest.update(path.encode() + b'\0' + self.world._read(path) + b'\0')
        return digest.hexdigest()

    def preview(self, body):
        """Return concrete dimensions, affected files and a required save token."""
        _, details = self._build(body, checked=False)
        return details

    def plan(self, body):
        """Return an atomic source plan after validating the preview token."""
        plan, _ = self._build(body, checked=True)
        return plan

    def _build(self, body, checked):
        if not isinstance(body, dict):
            raise ValueError('Route split must be an object')
        world = self.world
        name = body.get('name')
        current = world.get_map(name)
        if body.get('revision') != current['revision']:
            raise ValueError('This map changed. Reload it before splitting.')
        if current['shared_with'] or current['events_owner'] != name or current['events_shared_with']:
            raise ValueError('This map shares a layout or events. Give it an independent layout and events before splitting.')
        maps = world._maps()
        original = maps[name]
        axis = body.get('axis')
        if axis not in ('vertical', 'horizontal'):
            raise ValueError('Split axis must be vertical or horizontal')
        dimension = current['width' if axis == 'vertical' else 'height']
        # fieldmap.c copies fixed edge depths into its camera buffer: east is
        # eight columns, west seven, north/south seven rows. Narrower maps can
        # cause out-of-bounds reads. The horizontal minimum also keeps Wally's
        # original Route102 tutorial grass beside Petalburg.
        minimum = 8 if axis == 'vertical' else 7
        if dimension < 2 * minimum:
            raise ValueError(f'Each connected half needs at least {minimum} tiles for Emerald’s camera edge padding.')
        cut = integer(body.get('cut'), minimum, dimension - minimum, 'Split position (camera edge padding)')
        revision = self._revision(current, maps)
        if (checked or body.get('world_revision') is not None) and body.get('world_revision') != revision:
            raise ValueError('Maps, links or scripts changed after the split preview. Preview the split again.')
        new_name = body.get('new_name') or self._available_name(name, maps)
        width, height = current['width'], current['height']
        first = (0, 0, cut, height) if axis == 'vertical' else (0, 0, width, cut)
        second = (cut, 0, width - cut, height) if axis == 'vertical' else (0, cut, width, height - cut)
        world._validate_dimensions(first[2], first[3])
        world._validate_dimensions(second[2], second[3])
        plan = world.plan_new(new_name, name, second[2], second[3])
        new_map_path = f'data/maps/{new_name}/map.json'
        new_data = json.loads(plan[new_map_path])
        new_id = new_data['id']
        old_id = original['id']
        halves = [(name, old_id, first), (new_name, new_id, second)]
        layouts = json.loads(plan[LAYOUTS])
        old_layout = deepcopy(current['layout'])
        old_layout.update(width=first[2], height=first[3])
        layouts['layouts'] = [old_layout if item['id'] == old_layout['id'] else item for item in layouts['layouts']]
        new_layout = next(item for item in layouts['layouts'] if item['id'] == new_data['layout'])
        plan[LAYOUTS] = json_bytes(layouts)
        for layout, bounds in ((old_layout, first), (new_layout, second)):
            x, y, w, h = bounds
            cells = [current['cells'][row * width + column]
                     for row in range(y, y + h) for column in range(x, x + w)]
            plan[layout['blockdata_filepath']] = pack_words(cells)
        # Use existing labels exactly once. The header can reference another
        # map's callback table; individual event scripts remain their old labels.
        new_data['shared_scripts_map'] = original.get('shared_scripts_map', name)
        edited = deepcopy(maps)
        edited[name] = deepcopy(original)
        edited[new_name] = new_data
        warp_lookup, object_lookup = {}, {}
        for key in EVENT_KEYS:
            for half_name, _, _ in halves:
                edited[half_name][key] = []
            for index, event in enumerate(original.get(key, [])):
                if not isinstance(event, dict) or type(event.get('x')) is not int or type(event.get('y')) is not int:
                    raise ValueError(f'{key} #{index + 1} has invalid coordinates')
                if not (0 <= event['x'] < width and 0 <= event['y'] < height):
                    raise ValueError(f'{key} #{index + 1} is an off-map scripted actor. Move or review it before splitting.')
                part = 1 if event['x' if axis == 'vertical' else 'y'] >= cut else 0
                half_name, half_id, bounds = halves[part]
                target = deepcopy(event)
                target['x'] -= bounds[0]
                target['y'] -= bounds[1]
                target_index = len(edited[half_name][key])
                edited[half_name][key].append(target)
                if key == 'warp_events':
                    warp_lookup[index] = (half_id, target_index)
                    if event.get('warp_id'):
                        warp_lookup[event['warp_id']] = (half_id, target_index)
                elif key == 'object_events':
                    object_lookup[index + 1] = (half_id, target_index + 1)
                    if event.get('local_id'):
                        object_lookup[event['local_id']] = (half_id, target_index + 1)

        all_layouts = {layout['id']: layout for layout in world._json(LAYOUTS)['layouts']}
        ids = {data['id']: map_name for map_name, data in maps.items()}

        def pieces(map_name):
            if map_name == name:
                return halves
            data = maps[map_name]
            layout = all_layouts[data['layout']]
            return [(map_name, data['id'], (0, 0, layout['width'], layout['height']))]

        # Transform both ends of every existing edge. This also preserves
        # one-way links, rather than accidentally adding a new reverse link.
        for map_name, data in maps.items():
            for half_name, _, _ in pieces(map_name):
                edited[half_name]['connections'] = [] if data.get('connections') is not None else None
            for link in data.get('connections') or []:
                target_name = ids.get(link.get('map'))
                touched = map_name == name or target_name == name
                if not touched:
                    edited[map_name]['connections'].append(deepcopy(link))
                    continue
                if target_name is None or link.get('direction') not in CARDINAL or type(link.get('offset')) is not int:
                    raise ValueError('Splitting maps with dive/emerge or unresolved links needs a manual connection review first.')
                source_layout = all_layouts[data['layout']]
                target_layout = all_layouts[maps[target_name]['layout']]
                direction, offset = link['direction'], link['offset']
                target_origin = {'left': (-target_layout['width'], offset),
                                 'right': (source_layout['width'], offset),
                                 'up': (offset, -target_layout['height']),
                                 'down': (offset, source_layout['height'])}[direction]
                transferred = 0
                for from_name, _, (sx, sy, sw, sh) in pieces(map_name):
                    for _, to_id, (tx, ty, tw, th) in pieces(target_name):
                        tx += target_origin[0]
                        ty += target_origin[1]
                        adjacent = {'left': tx + tw == sx, 'right': sx + sw == tx,
                                    'up': ty + th == sy, 'down': sy + sh == ty}[direction]
                        if direction in ('left', 'right'):
                            overlap = min(sy + sh, ty + th) - max(sy, ty)
                            new_offset = ty - sy
                        else:
                            overlap = min(sx + sw, tx + tw) - max(sx, tx)
                            new_offset = tx - sx
                        if adjacent and overlap > 0:
                            integer(new_offset, -32768, 32767, 'Connection offset')
                            if edited[from_name]['connections'] is None:
                                edited[from_name]['connections'] = []
                            edited[from_name]['connections'].append({**deepcopy(link), 'map': to_id, 'offset': new_offset})
                            transferred += 1
                if not transferred:
                    raise ValueError(f'{map_name} has a connection to {target_name} with no shared edge. Fix it before splitting.')
        for from_name, to_id, direction in ((name, new_id, 'right' if axis == 'vertical' else 'down'),
                                            (new_name, old_id, 'left' if axis == 'vertical' else 'up')):
            if edited[from_name]['connections'] is None:
                edited[from_name]['connections'] = []
            edited[from_name]['connections'].append({'map': to_id, 'offset': 0, 'direction': direction})

        for map_name, data in edited.items():
            for warp in data.get('warp_events', []):
                if warp.get('dest_map') != old_id:
                    continue
                value = warp.get('dest_warp_id')
                index = _number(value)
                target = warp_lookup.get(index if index is not None else value)
                if target is None:
                    raise ValueError(f'{map_name} has a dynamic or unresolved warp into {name}. Set its landing warp before splitting.')
                warp['dest_map'], new_index = target
                # Symbolic warp aliases are regenerated from their new array.
                if index is not None:
                    warp['dest_warp_id'] = str(new_index) if isinstance(value, str) else new_index
            for event in data.get('object_events', []):
                if event.get('type') != 'clone' or event.get('target_map') != old_id:
                    continue
                value = event.get('target_local_id')
                index = _number(value)
                target = object_lookup.get(index if index is not None else value)
                if target is None:
                    raise ValueError(f'{map_name} has an unresolved cloned actor in {name}. Review it before splitting.')
                event['target_map'], new_index = target
                if index is not None:
                    event['target_local_id'] = str(new_index) if isinstance(value, str) else new_index
            if len(data.get('connections') or []) > 255:
                raise ValueError(f'Splitting would exceed the connection limit on {map_name}')

        # Also check inherited edges: a horizontal split keeps its old width,
        # and a vertical split keeps its old height. Those dimensions must be
        # large enough for any existing perpendicular walking connections.
        planned_layouts = {item['id']: item for item in layouts['layouts']}
        planned_ids = {data['id']: data for data in edited.values()}
        for map_name, data in edited.items():
            for link in data.get('connections') or []:
                if map_name not in (name, new_name) and link.get('map') not in (old_id, new_id):
                    continue
                direction = link.get('direction')
                if direction not in CARDINAL:
                    continue
                target = planned_ids[link['map']]
                layout = planned_layouts[target['layout']]
                extent = layout['width' if direction in ('left', 'right') else 'height']
                depth = 8 if direction == 'right' else 7
                if extent < depth:
                    raise ValueError(f'{map_name} → {target["name"]} needs {depth} tiles of camera edge padding. Enlarge that connected map before splitting.')

        warnings = ['Map IDs and coordinates written directly in story scripts are not automatically rewritten; review scripted movement and cutscenes before building the game.']
        if original.get('object_events'):
            warnings.append('Objects keep their scripts, flags and named IDs. Numeric object numbers are reassigned within each half; scripts that use literal object numbers need review.')
        script_path = current['scripts_path']
        script = world._text(script_path)
        owner = original.get('shared_scripts_map', name)
        table = re.search(r'^' + re.escape(owner) + r'_MapScripts::?\s*\n(.*?)(?=^\w+::?|\Z)', script, re.M | re.S)
        if table and re.search(r'^\s*map_script\b', table.group(1), re.M):
            warnings.append(f'Both halves reuse {owner} map callbacks. Check callback coordinates and map-state assumptions in the story editor.')
        encounter_count = self._copy_encounters(plan, old_id, new_id, new_name)
        rematch_count = self._retarget_rematches(plan, original, edited, halves, script, warnings)
        changed_maps = []
        for map_name, data in edited.items():
            if map_name not in maps or data != maps[map_name]:
                plan[f'data/maps/{map_name}/map.json'] = json_bytes(data)
                changed_maps.append(map_name)
        details = {'name': name, 'new_name': new_name, 'axis': axis, 'cut': cut,
                   'revision': current['revision'], 'world_revision': revision, 'minimum_part_size': minimum,
                   'parts': [{'name': half_name, 'id': half_id, 'width': bounds[2], 'height': bounds[3],
                              'origin_x': bounds[0], 'origin_y': bounds[1],
                              'events': {key: len(edited[half_name].get(key, [])) for key in EVENT_KEYS}}
                             for half_name, half_id, bounds in halves],
                   'warnings': warnings, 'affected_maps': changed_maps, 'files': sorted(plan),
                   'encounter_tables_copied': encounter_count, 'rematches_retargeted': rematch_count}
        return plan, details

    def _copy_encounters(self, plan, old_id, new_id, new_name):
        if not self.world._path(ENCOUNTERS).is_file():
            return 0
        data = self.world._json(ENCOUNTERS)
        labels = {entry.get('base_label') for group in data['wild_encounter_groups'] for entry in group.get('encounters', [])}
        copied = 0
        for group in data['wild_encounter_groups']:
            if not group.get('for_maps'):
                continue
            additions = []
            for entry in group.get('encounters', []):
                if entry.get('map') != old_id:
                    continue
                clone = deepcopy(entry)
                clone['map'] = new_id
                base, suffix = 'g' + new_name, 2
                label = base
                while label in labels:
                    label = base + '_' + str(suffix)
                    suffix += 1
                labels.add(label)
                clone['base_label'] = label
                additions.append(clone)
                copied += 1
            group['encounters'].extend(additions)
        if copied:
            plan[ENCOUNTERS] = json_bytes(data)
        return copied

    def _retarget_rematches(self, plan, original, edited, halves, script, warnings):
        if not self.world._path(REMATCHES).is_file():
            return 0
        # Direct trainer declarations cover ordinary route trainers, including
        # Route102's Calvin. Complex dispatched scripts are reported for review.
        trainer_maps = {}
        for half_name, half_id, _ in halves:
            for event in edited[half_name].get('object_events', []):
                label = event.get('script')
                if not isinstance(label, str):
                    continue
                block = re.search(r'^' + re.escape(label) + r'::?\s*\n(.*?)(?=^\w+::?|\Z)', script, re.M | re.S)
                if block:
                    trainer = re.search(r'\btrainerbattle_(?:single|double)\s+(TRAINER_\w+)', block.group(1))
                    if trainer:
                        trainer_maps.setdefault(trainer.group(1), set()).add(half_id)
        text = self.world._text(REMATCHES)
        changed, unresolved = 0, 0

        def replace(match):
            nonlocal changed, unresolved
            arguments = [part.strip() for part in match.group(1).split(',')]
            if not arguments or arguments[-1] != original['id']:
                return match.group(0)
            destinations = trainer_maps.get(arguments[0], set())
            if len(destinations) != 1:
                unresolved += 1
                return match.group(0)
            destination = next(iter(destinations))
            if destination == original['id']:
                return match.group(0)
            changed += 1
            return match.group(0).rsplit(original['id'], 1)[0] + destination + match.group(0).rsplit(original['id'], 1)[1]

        updated = re.sub(r'\bREMATCH\(([^()]*)\)', replace, text)
        if changed:
            plan[REMATCHES] = updated.encode('utf-8')
        if unresolved:
            warnings.append(f'{unresolved} trainer rematch map binding(s) use indirect scripts and need manual review.')
        return changed


def plan_split(world, body):
    return RouteSplitter(world).plan(body)
