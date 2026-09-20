"""Read and plan reciprocal Emerald map links without modifying source files.

Cardinal offsets follow src/fieldmap.c: the destination's left/top origin is
offset tiles from the source origin. A reciprocal connection negates that offset.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


OPPOSITE = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
LAYOUTS = 'data/layouts/layouts.json'


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}.')
    return value


def _warp_index(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value):
        return int(value)
    return None


class Connections:
    def __init__(self, source):
        self.source = Path(source).resolve()

    def _snapshot(self):
        digest = hashlib.sha256()
        maps = {}
        layout_bytes = (self.source / LAYOUTS).read_bytes()
        digest.update(LAYOUTS.encode() + b'\0' + layout_bytes)
        layouts = {item['id']: item for item in json.loads(layout_bytes)['layouts']}
        for path in sorted((self.source / 'data/maps').glob('*/map.json')):
            if not path.resolve().is_relative_to(self.source):
                raise ValueError('Map file must stay inside the source project.')
            raw = path.read_bytes()
            digest.update(path.relative_to(self.source).as_posix().encode() + b'\0' + raw)
            data = json.loads(raw)
            if data.get('name') != path.parent.name:
                raise ValueError(f'Map folder and name disagree: {path.parent.name}.')
            if data.get('layout') not in layouts:
                raise ValueError(f'Unknown layout for {data["name"]}.')
            maps[data['name']] = data
        ids = {data['id']: name for name, data in maps.items()}
        if len(ids) != len(maps):
            raise ValueError('Map identifiers must be unique before connecting maps.')
        owners = {}
        for name in maps:
            owner, visited = name, set()
            while maps[owner].get('shared_events_map'):
                if owner in visited:
                    raise ValueError('Circular shared-events map reference.')
                visited.add(owner)
                owner = maps[owner]['shared_events_map']
                if owner not in maps:
                    raise ValueError(f'Missing shared events map: {owner}.')
            owners[name] = owner
        return {'revision': digest.hexdigest(), 'maps': maps, 'ids': ids, 'layouts': layouts, 'owners': owners}

    @staticmethod
    def _layout(snapshot, name):
        return snapshot['layouts'][snapshot['maps'][name]['layout']]

    @staticmethod
    def _warps(snapshot, name):
        return snapshot['maps'][snapshot['owners'][name]].get('warp_events', [])

    @staticmethod
    def _overlap(source_length, destination_length, offset):
        first, last = max(0, offset), min(source_length, offset + destination_length)
        return [first, last] if last > first else None

    def catalog(self):
        snapshot = self._snapshot()
        maps, ids = snapshot['maps'], snapshot['ids']
        result, broken_count, one_way_count = [], 0, 0
        groups = {}
        for name, owner in snapshot['owners'].items():
            groups.setdefault(owner, []).append(name)
        for name, data in maps.items():
            layout = self._layout(snapshot, name)
            row = {'name': name, 'id': data['id'], 'width': layout['width'], 'height': layout['height'],
                   'map_type': data.get('map_type'), 'region_map_section': data.get('region_map_section'),
                   'events_owner': snapshot['owners'][name],
                   'events_shared_with': [other for other in groups[snapshot['owners'][name]] if other != name],
                   'connections': [], 'warps': [], 'warnings': [],
                   'preview_url': f'/api/world/maps/{name}/preview.png'}
            for connection in data.get('connections') or []:
                target_name = ids.get(connection.get('map'))
                direction, offset = connection.get('direction'), connection.get('offset')
                reciprocal, overlap = False, None
                if target_name:
                    reverse = OPPOSITE.get(direction, {'dive': 'emerge', 'emerge': 'dive'}.get(direction))
                    reciprocal = any(link.get('map') == data['id'] and link.get('direction') == reverse
                                     and link.get('offset') == -offset
                                     for link in maps[target_name].get('connections') or []) if type(offset) is int else False
                    if direction in OPPOSITE and type(offset) is int:
                        axis = 'width' if direction in ('up', 'down') else 'height'
                        overlap = self._overlap(layout[axis], self._layout(snapshot, target_name)[axis], offset)
                        if overlap is None:
                            row['warnings'].append(f'{direction.title()} connection to {target_name} has no shared edge.')
                            broken_count += 1
                else:
                    row['warnings'].append(f'Connection points to unknown map {connection.get("map")}.')
                    broken_count += 1
                if target_name and not reciprocal:
                    row['warnings'].append(f'{direction} connection to {target_name} has no matching return connection.')
                    one_way_count += 1
                row['connections'].append({**deepcopy(connection), 'dest_name': target_name,
                                           'reciprocal': reciprocal, 'overlap': overlap})
            for index, warp in enumerate(self._warps(snapshot, name)):
                target_name = ids.get(warp.get('dest_map'))
                target_index = _warp_index(warp.get('dest_warp_id'))
                dynamic = warp.get('dest_map') == 'MAP_DYNAMIC'
                reciprocal = False
                if target_name and target_index is not None:
                    target_warps = self._warps(snapshot, target_name)
                    if 0 <= target_index < len(target_warps):
                        target = target_warps[target_index]
                        reciprocal = target.get('dest_map') == data['id'] and _warp_index(target.get('dest_warp_id')) == index
                    elif target_index != -1:
                        row['warnings'].append(f'Warp {index} points to missing warp {target_index} on {target_name}.')
                        broken_count += 1
                elif not dynamic and warp.get('dest_map') != 'MAP_UNDEFINED':
                    row['warnings'].append(f'Warp {index} has an unresolved destination.')
                    broken_count += 1
                if target_name and not reciprocal:
                    one_way_count += 1
                row['warps'].append({**deepcopy(warp), 'index': index, 'dest_name': target_name,
                                     'dest_index': target_index, 'reciprocal': reciprocal, 'dynamic': dynamic})
            result.append(row)
        warnings = []
        if broken_count:
            warnings.append(f'{broken_count} existing link references need review; details are listed on their maps.')
        if one_way_count:
            warnings.append(f'{one_way_count} links have no matching return link. Some original events intentionally work one way.')
        return {'revision': snapshot['revision'], 'maps': result, 'warnings': warnings,
                'guidance': ['Walking connections join map edges. Keep the crossing tiles walkable on both maps.',
                             'Door and cave warps need suitable warp terrain at the source tile and a reachable landing tile.',
                             'Positive offsets move the other map right on north/south edges, or down on east/west edges.']}

    def _checked_snapshot(self, body):
        if not isinstance(body, dict):
            raise ValueError('Connection edit must be an object.')
        snapshot = self._snapshot()
        if body.get('revision') != snapshot['revision']:
            raise ValueError('Maps changed since this view was opened. Reload before saving connections.')
        return snapshot

    @staticmethod
    def _map_name(snapshot, value):
        if not isinstance(value, str) or value not in snapshot['maps']:
            raise ValueError('Choose an existing map.')
        return value

    def _check_edge_conflicts(self, snapshot, source_name, target_name, direction, offset):
        axis = 'width' if direction in ('up', 'down') else 'height'
        source_length = self._layout(snapshot, source_name)[axis]
        target_length = self._layout(snapshot, target_name)[axis]
        proposed = self._overlap(source_length, target_length, offset)
        if proposed is None:
            raise ValueError('That offset leaves the maps with no shared edge. Move them closer together.')
        target_id = snapshot['maps'][target_name]['id']
        for link in snapshot['maps'][source_name].get('connections') or []:
            if link.get('direction') != direction or link.get('map') == target_id:
                continue
            other_name = snapshot['ids'].get(link.get('map'))
            if other_name is None or type(link.get('offset')) is not int:
                raise ValueError(f'{source_name} has an unresolved {direction} link. Fix or unlink it first.')
            existing = self._overlap(source_length, self._layout(snapshot, other_name)[axis], link['offset'])
            if existing and max(proposed[0], existing[0]) < min(proposed[1], existing[1]):
                raise ValueError(f'{source_name} already connects {direction} to {other_name} on those edge tiles. Unlink it first or choose a non-overlapping offset.')

    def plan_edge(self, body):
        snapshot = self._checked_snapshot(body)
        a = self._map_name(snapshot, body.get('a'))
        b = self._map_name(snapshot, body.get('b'))
        if a == b:
            raise ValueError('Choose two different maps for a walking connection.')
        direction = body.get('direction')
        if direction not in OPPOSITE:
            raise ValueError('Walking direction must be up, down, left, or right.')
        action = body.get('action', 'connect')
        if action not in ('connect', 'disconnect'):
            raise ValueError('Action must be connect or disconnect.')
        offset = _integer(body.get('offset', 0), -32767, 32767, 'Connection offset')
        reverse = OPPOSITE[direction]
        if action == 'connect':
            depth = 'height' if direction in ('up', 'down') else 'width'
            if any(self._layout(snapshot, name)[depth] < 7 for name in (a, b)):
                raise ValueError('Walking connections need both maps at least 7 tiles deep along the crossing direction.')
            self._check_edge_conflicts(snapshot, a, b, direction, offset)
            self._check_edge_conflicts(snapshot, b, a, reverse, -offset)
        plan = {}
        for source_name, target_name, edge, value in ((a, b, direction, offset), (b, a, reverse, -offset)):
            original = snapshot['maps'][source_name]
            data = deepcopy(original)
            target_id = snapshot['maps'][target_name]['id']
            old = data.get('connections') or []
            matching = [link for link in old if link.get('direction') == edge and link.get('map') == target_id]
            if action == 'connect' and any(link.get('offset') != value for link in matching) and body.get('replace') is not True:
                raise ValueError('These maps already connect at a different offset. Confirm replacement to move that connection.')
            new_link = {**(matching[0] if matching else {}), 'map': target_id, 'direction': edge, 'offset': value}
            updated, inserted = [], False
            for link in old:
                if link.get('direction') == edge and link.get('map') == target_id:
                    if action == 'connect' and not inserted:
                        updated.append(new_link)
                        inserted = True
                else:
                    updated.append(link)
            if action == 'connect' and not inserted:
                if len(updated) >= 255:
                    raise ValueError(f'{source_name} has reached the connection limit.')
                updated.append(new_link)
            # Preserve null versus [] when no connection was actually changed.
            if updated != old:
                data['connections'] = updated if updated or original.get('connections') is not None else None
                plan[f'data/maps/{source_name}/map.json'] = _json_bytes(data)
        return plan

    def _endpoint(self, snapshot, value, label):
        if not isinstance(value, dict):
            raise ValueError(f'{label} must identify a map and tile.')
        name = self._map_name(snapshot, value.get('map'))
        layout = self._layout(snapshot, name)
        x = _integer(value.get('x'), 0, min(255, layout['width'] - 1), f'{label} x')
        y = _integer(value.get('y'), 0, min(255, layout['height'] - 1), f'{label} y')
        elevation = _integer(value.get('elevation', 0), 0, 15, f'{label} elevation')
        index = value.get('index')
        warps = self._warps(snapshot, name)
        if index is not None:
            _integer(index, 0, len(warps) - 1, f'{label} existing warp index')
        return {'map': name, 'owner': snapshot['owners'][name], 'x': x, 'y': y, 'elevation': elevation, 'index': index}

    def plan_warp(self, body):
        snapshot = self._checked_snapshot(body)
        a = self._endpoint(snapshot, body.get('a'), 'First door')
        b = self._endpoint(snapshot, body.get('b'), 'Second door')
        owners = {endpoint['owner']: deepcopy(snapshot['maps'][endpoint['owner']]) for endpoint in (a, b)}
        for endpoint in (a, b):
            warps = owners[endpoint['owner']].setdefault('warp_events', [])
            if endpoint['index'] is None:
                if len(warps) >= 255:
                    raise ValueError(f'{endpoint["map"]} has reached the 255-warp limit.')
                endpoint['index'] = len(warps)
                endpoint['new'] = True
                warps.append({})
            else:
                endpoint['new'] = False
        if (a['owner'], a['index']) == (b['owner'], b['index']):
            raise ValueError('Both endpoints refer to the same actual warp. Choose two different warp slots.')
        for endpoint, other in ((a, b), (b, a)):
            warps = owners[endpoint['owner']]['warp_events']
            previous = warps[endpoint['index']]
            target_id = snapshot['maps'][other['map']]['id']
            if not endpoint['new'] and (previous.get('dest_map') != target_id or _warp_index(previous.get('dest_warp_id')) != other['index']):
                if body.get('confirm_replace') is not True:
                    raise ValueError('An existing door leads somewhere else. Confirm replacement to rewire its destination.')
            updated = {**previous, 'x': endpoint['x'], 'y': endpoint['y'], 'elevation': endpoint['elevation'],
                       'dest_map': target_id,
                       'dest_warp_id': previous.get('dest_warp_id') if _warp_index(previous.get('dest_warp_id')) == other['index'] else other['index']}
            warps[endpoint['index']] = updated
        for endpoint in (a, b):
            warps = owners[endpoint['owner']]['warp_events']
            updated = warps[endpoint['index']]
            # Avoid duplicate warps on the same tile; elevation 0 matches every
            # elevation in the engine, so it also conflicts with nonzero entries.
            for index, warp in enumerate(warps):
                if index == endpoint['index'] or not warp:
                    continue
                same_tile = warp.get('x') == updated['x'] and warp.get('y') == updated['y']
                same_level = warp.get('elevation') in (0, updated['elevation']) or updated['elevation'] == 0
                if same_tile and same_level:
                    raise ValueError(f'{endpoint["map"]} already has warp {index} on that tile. Select that existing warp instead.')
        plan = {}
        for owner, data in owners.items():
            original = snapshot['maps'][owner]
            if data == original:
                continue
            shared_names = [name for name, candidate in snapshot['owners'].items() if candidate == owner]
            if len(shared_names) > 1 and body.get('confirm_shared') is not True:
                raise ValueError('These warp events are shared by ' + ', '.join(shared_names) + '. Confirm shared changes before saving.')
            for endpoint in (a, b):
                if endpoint['owner'] != owner:
                    continue
                for name in shared_names:
                    layout = self._layout(snapshot, name)
                    if endpoint['x'] >= layout['width'] or endpoint['y'] >= layout['height']:
                        raise ValueError(f'That warp tile is outside shared map {name}. Choose a tile inside every shared map.')
            plan[f'data/maps/{owner}/map.json'] = _json_bytes(data)
        return plan
