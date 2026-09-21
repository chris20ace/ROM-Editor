"""Make saved world-canvas contacts into backed-up game walking connections.

Only edges between scoped maps are replaced. Doors, scripts, dive/emerge links,
and links to maps outside the scope remain unchanged. The caller holds the
project lock; no source or canvas writes occur during catalog/preview.
"""
from collections import Counter
from copy import deepcopy
import json

from connections import Connections, OPPOSITE, _json_bytes
from world_positions import MAX_FILE_BYTES, WorldPositions, _encoded
from world_reshape import WorldReshaper, _raw
from worldmap import OUTDOOR_TYPES, WorldMap, _delta


def _key(edge):
    return edge['from_name'], edge['to_name'], edge['direction'], json.dumps(edge['offset'], sort_keys=True)


class WorldLinkSync:
    def __init__(self, project):
        self.project = project
        self.connections = Connections(project.source)
        self.positions = WorldPositions(project.root)

    def _state(self):
        snapshot = self.connections._snapshot()
        catalog = WorldMap(self.project.source).catalog()
        if catalog['revision'] != snapshot['revision']:
            raise ValueError('Maps changed while loading connections. Reload the world canvas.')
        saved = self.positions.read(catalog)
        rows = {}
        for row in catalog['maps']:
            name = row['name']
            position = saved['positions'].get(name, {'x': row['x'], 'y': row['y']})
            rows[name] = {key: row[key] for key in ('name', 'id', 'width', 'height', 'map_type', 'is_outdoor')}
            rows[name].update(position)
            rows[name]['default_scope'] = (row['map_type'] in OUTDOOR_TYPES
                                           and not row.get('archived', False))
        current = {name: {'x': row['x'], 'y': row['y']} for name, row in rows.items()}
        return snapshot, saved, rows, current

    def _edge(self, snapshot, rows, name, link):
        target = snapshot['ids'].get(link.get('map'))
        direction, offset = link.get('direction'), link.get('offset')
        aligned, reciprocal, overlap = False, False, None
        if target and direction in OPPOSITE and type(offset) is int:
            source, dest = rows[name], rows[target]
            axis = 'width' if direction in ('up', 'down') else 'height'
            overlap = Connections._overlap(source[axis], dest[axis], offset)
            dx, dy = _delta(direction, offset, (source['width'], source['height']),
                            (dest['width'], dest['height']))
            aligned = dest['x'] == source['x'] + dx and dest['y'] == source['y'] + dy and overlap is not None
            reciprocal = any(item.get('map') == source['id']
                             and item.get('direction') == OPPOSITE[direction]
                             and item.get('offset') == -offset
                             for item in snapshot['maps'][target].get('connections') or [])
        return {'from_name': name, 'to_name': target, 'direction': direction, 'offset': offset,
                'dest_map': link.get('map'), 'aligned': aligned, 'reciprocal': reciprocal, 'overlap': overlap}

    def _view(self, snapshot, saved, rows, current):
        from world_link_audit import audit_warps
        edges, special = [], []
        for name, data in snapshot['maps'].items():
            for link in data.get('connections') or []:
                edge = self._edge(snapshot, rows, name, link)
                (edges if link.get('direction') in OPPOSITE else special).append(edge)
        return {'revision': snapshot['revision'], 'positions_revision': saved['revision'],
                'maps': list(rows.values()),
                'default_names': sorted(name for name, row in rows.items() if row['default_scope']),
                'edges': edges, 'special_edges': special, 'audit': audit_warps(snapshot, current)}

    def catalog(self):
        return self._view(*self._state())

    def _prepare(self, body):
        if not isinstance(body, dict):
            raise ValueError('Walking connection edits need an object.')
        snapshot, saved, rows, current = self._state()
        if body.get('revision') != snapshot['revision']:
            raise ValueError('Maps changed. Reload the canvas and preview connections again.')
        if body.get('positions_revision') != saved['revision']:
            raise ValueError('World box positions changed. Reload the canvas and preview connections again.')
        names = body.get('names', sorted(name for name, row in rows.items() if row['default_scope']))
        if (not isinstance(names, list) or len(names) < 2 or any(not isinstance(name, str) or name not in rows for name in names)
                or len(set(names)) != len(names)):
            raise ValueError('Select at least two different existing map boxes.')
        names = sorted(names)
        scope = set(names)
        desired = {name: [] for name in names}
        errors, warnings = [], []
        for i, a in enumerate(names):
            first = rows[a]
            for b in names[i + 1:]:
                second = rows[b]
                horizontal = min(first['x'] + first['width'], second['x'] + second['width']) - max(first['x'], second['x'])
                vertical = min(first['y'] + first['height'], second['y'] + second['height']) - max(first['y'], second['y'])
                if horizontal > 0 and vertical > 0:
                    errors.append(f'{a} overlaps {b}. Separate the boxes before connecting them.')
                    continue
                direction = None
                if vertical > 0:
                    if first['x'] + first['width'] == second['x']:
                        direction = 'right'
                    elif second['x'] + second['width'] == first['x']:
                        direction = 'left'
                if horizontal > 0:
                    if first['y'] + first['height'] == second['y']:
                        direction = 'down'
                    elif second['y'] + second['height'] == first['y']:
                        direction = 'up'
                if direction is None:
                    continue
                side = direction in ('left', 'right')
                depth, minimum = ('width', 8) if side else ('height', 7)
                if min(first[depth], second[depth]) < minimum:
                    errors.append(f'{a} and {b} need at least {minimum} tiles of {depth} for this walking edge and its camera padding.')
                    continue
                offset = second['y'] - first['y'] if side else second['x'] - first['x']
                if not -32767 <= offset <= 32767:
                    errors.append(f'{a} and {b} have an offset outside the game connection range.')
                    continue
                desired[a].append({'map': second['id'], 'direction': direction, 'offset': offset})
                desired[b].append({'map': first['id'], 'direction': OPPOSITE[direction], 'offset': -offset})

        edited = deepcopy(snapshot['maps'])
        for name in names:
            original = snapshot['maps'][name].get('connections') or []
            wanted = {(item['map'], item['direction'], item['offset']): item for item in desired[name]}
            updated = []
            for item in original:
                target = snapshot['ids'].get(item.get('map'))
                if item.get('direction') in OPPOSITE and target in scope:
                    key = ((item.get('map'), item.get('direction'), item['offset'])
                           if type(item.get('offset')) is int else None)
                    if key in wanted:
                        updated.append(deepcopy(item))
                        wanted.pop(key)
                else:
                    updated.append(deepcopy(item))
                    if item.get('direction') in OPPOSITE:
                        warnings.append(f'{name} → {target or item.get("map")} stays unchanged because its destination is outside this selection.')
            updated.extend(wanted.values())
            if len(updated) > 255:
                errors.append(f'{name} would exceed the 255-connection limit.')
            # Check retained out-of-scope links as well: the engine chooses the
            # first matching span, so overlapping records silently misroute play.
            spans = {}
            for item in updated:
                direction = item.get('direction')
                if direction not in OPPOSITE:
                    continue
                target = snapshot['ids'].get(item.get('map'))
                if target is None or type(item.get('offset')) is not int:
                    errors.append(f'{name} has an unresolved {direction} connection. Correct it before applying this selection.')
                    continue
                axis = 'width' if direction in ('up', 'down') else 'height'
                span = Connections._overlap(rows[name][axis], rows[target][axis], item['offset'])
                if span is None:
                    errors.append(f'{name} has a {direction} connection to {target} with no shared edge. Include that destination or correct its connection.')
                    continue
                for other, old in spans.setdefault(direction, []):
                    if max(old[0], span[0]) < min(old[1], span[1]):
                        errors.append(f'{name} has conflicting {direction} walking edges to {other} and {target}. Include both destinations or separate their spans.')
                spans[direction].append((target, span))
            if updated != original:
                edited[name]['connections'] = updated if updated or snapshot['maps'][name].get('connections') is not None else None

        changed_snapshot = {**snapshot, 'maps': edited}
        old_edges, new_edges = [], []
        for name in names:
            for data, collection, snap in ((snapshot['maps'][name], old_edges, snapshot), (edited[name], new_edges, changed_snapshot)):
                for link in data.get('connections') or []:
                    if link.get('direction') in OPPOSITE:
                        collection.append(self._edge(snap, rows, name, link))
        old_counts, new_counts = Counter(_key(edge) for edge in old_edges), Counter(_key(edge) for edge in new_edges)
        def selected(items, counts):
            available = counts.copy()
            result = []
            for edge in items:
                key = _key(edge)
                if available[key] > 0:
                    result.append(edge)
                    available[key] -= 1
            return result
        removed = selected(old_edges, old_counts - new_counts)
        added = selected(new_edges, new_counts - old_counts)
        unchanged = selected(new_edges, old_counts & new_counts)
        plan = {f'data/maps/{name}/map.json': _json_bytes(edited[name]) for name in names if edited[name] != snapshot['maps'][name]}
        affected = [name for name in names if edited[name] != snapshot['maps'][name]]
        warnings.insert(0, 'Doors, caves, stairs, dive/emerge links and scripted travel keep their saved destinations. Review any unresolved portal endpoints separately.')
        warnings.insert(1, 'Touching edges need walkable crossing terrain on both sides; this action does not paint terrain or change collision.')
        if removed:
            warnings.append('Walking links between selected boxes that no longer touch will be removed.')
        from world_link_audit import audit_warps
        result = {'revision': snapshot['revision'], 'positions_revision': saved['revision'],
                  'scope_names': names, 'default_names': sorted(name for name, row in rows.items() if row['default_scope']),
                  'added': added, 'removed': removed, 'unchanged': unchanged,
                  'affected_maps': affected, 'counts': {'added': len(added), 'removed': len(removed),
                                                       'unchanged': len(unchanged), 'affected_maps': len(affected)},
                  'warnings': list(dict.fromkeys(warnings)), 'errors': list(dict.fromkeys(errors)),
                  'can_apply': not errors and bool(plan), 'audit': audit_warps(snapshot, current)}
        return result, plan, current

    def preview(self, body):
        return self._prepare(body)[0]

    def commit(self, body):
        details, plan, current = self._prepare(body)
        if details['errors']:
            raise ValueError(' '.join(details['errors']))
        if not plan:
            return {**details, 'transaction': None, 'message': 'Walking connections already match these boxes.',
                    'canvas_positions': self.positions.read(self.project.world._maps())}
        all_positions, revision = self.positions._load()
        if revision != body['positions_revision']:
            raise ValueError('World box positions changed. Preview connections again.')
        proposed = {**all_positions, **current}
        before = _raw(self.positions.path)
        after = before if proposed == all_positions else _encoded(proposed)
        if after is not None and len(after) > MAX_FILE_BYTES:
            raise ValueError('Too many saved world box positions.')

        def save_source():
            if self.connections._snapshot()['revision'] != details['revision']:
                raise ValueError('Maps changed while saving. Preview connections again.')
            return self.project.transactions.commit(plan, 'Connect world boxes: ' + str(len(details['scope_names'])) + ' maps')

        def save_positions():
            self.positions.save({'revision': revision, 'positions': current}, self.project.world._maps())

        saved = WorldReshaper(self.project)._coordinated(save_source, before, after, save_positions)
        return {**details, 'transaction': saved['id'], 'message': saved['message'],
                'canvas_positions': self.positions.read(self.project.world._maps())}
