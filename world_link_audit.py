"""Resolve the actual endpoints of Emerald warp events without changing them.

Coordinates are local metatiles; optional world coordinates point at tile centers.
Dynamic exits and coordinate/script-driven warps intentionally have no fixed
landing tile. A missing reciprocal warp is informational, not a broken link.
"""
from collections import Counter
import re

from connections import Connections


_CONSTANTS = {'WARP_ID_NONE': -1, 'WARP_ID_SECRET_BASE': 126, 'WARP_ID_DYNAMIC': 127}
_STATUSES = ('fixed', 'dynamic', 'scripted', 'unresolved', 'out_of_bounds')


def _number(value):
    if type(value) is int:
        return value
    if isinstance(value, str):
        if re.fullmatch(r'-?\d+', value):
            return int(value)
        if re.fullmatch(r'0[xX][0-9a-fA-F]+', value):
            return int(value, 16)
    return None


def _warps(snapshot, name):
    return snapshot['maps'][snapshot['owners'][name]].get('warp_events', [])


def _symbols(snapshot):
    # mapjson generates globally scoped constants from each raw event's warp_id.
    # Do not count maps sharing that owner's event list as duplicate definitions.
    symbols = {}
    for data in snapshot['maps'].values():
        for index, warp in enumerate(data.get('warp_events', [])):
            symbol = warp.get('warp_id')
            if isinstance(symbol, str):
                symbols.setdefault(symbol, []).append(index)
    return symbols


def _resolve_index(value, symbols):
    number = _number(value)
    if number is not None:
        return number, None
    if not isinstance(value, str):
        return None, 'Destination warp index is not a number or warp label.'
    if value in _CONSTANTS:
        return _CONSTANTS[value], None
    definitions = set(symbols.get(value, []))
    if len(definitions) == 1:
        return next(iter(definitions)), None
    if definitions:
        return None, f'Warp label {value} has multiple definitions.'
    return None, f'Warp label {value} is not defined by an event warp_id.'


def _endpoint(snapshot, positions, name, index):
    warp = _warps(snapshot, name)[index]
    layout = snapshot['layouts'][snapshot['maps'][name]['layout']]
    x, y = warp.get('x'), warp.get('y')
    valid = (type(x) is int and type(y) is int
             and 0 <= x < layout['width'] and 0 <= y < layout['height'])
    endpoint = {'map': name, 'index': index, 'x': x, 'y': y,
                'elevation': warp.get('elevation', 0),
                'events_owner': snapshot['owners'][name], 'in_bounds': valid}
    position = positions.get(name) if positions is not None else None
    if position is not None and type(x) is int and type(y) is int:
        endpoint.update(world_x=position['x'] + x + .5,
                        world_y=position['y'] + y + .5)
    return endpoint


def audit_warps(snapshot, positions=None):
    """Return portal endpoints from a Connections snapshot and optional positions.

    Symbolic IDs follow mapjson's generated constants, including when the target
    map shares another map's event list. This never selects targets by proximity
    and never edits source files. Status describes reference resolution only;
    fixed endpoints still need appropriate warp terrain and story behavior.
    """
    portals, issues = [], []
    symbols = _symbols(snapshot)
    ids = snapshot['ids']
    for name, data in snapshot['maps'].items():
        for index, warp in enumerate(_warps(snapshot, name)):
            source = _endpoint(snapshot, positions, name, index)
            dest_map, value = warp.get('dest_map'), warp.get('dest_warp_id')
            target_name = ids.get(dest_map)
            target_index, index_error = _resolve_index(value, symbols)
            destination, reciprocal = None, False
            problems = []
            if dest_map == 'MAP_DYNAMIC':
                resolution = 'dynamic'
                reason = 'The game chooses the saved return destination at runtime.'
                target_index = None  # The engine ignores this field for MAP_DYNAMIC.
            elif dest_map == 'MAP_UNDEFINED':
                resolution = 'scripted'
                reason = 'Dummy or script-controlled warp; no fixed destination is declared.'
                target_index = None
            elif target_name is None:
                resolution = 'unresolved'
                reason = f'Destination map {dest_map!r} does not exist.'
                problems.append(reason)
            elif target_index == -1:
                resolution = 'scripted'
                reason = 'The game uses supplied coordinates or special handling instead of a destination warp.'
            elif index_error:
                resolution = 'unresolved'
                reason = index_error
                problems.append(reason)
            elif not 0 <= target_index < len(_warps(snapshot, target_name)):
                resolution = 'unresolved'
                reason = f'{target_name} has no warp at index {target_index}.'
                problems.append(reason)
            else:
                resolution = 'fixed'
                destination = _endpoint(snapshot, positions, target_name, target_index)
                target = _warps(snapshot, target_name)[target_index]
                reverse_index, reverse_error = _resolve_index(target.get('dest_warp_id'), symbols)
                reciprocal = (target.get('dest_map') == data['id']
                              and reverse_error is None and reverse_index == index)
                reason = ('Destination returns to this warp.' if reciprocal else
                          'Fixed destination; it does not return directly to this same warp.')
                if not destination['in_bounds']:
                    problems.append(f'Destination warp {target_name} #{target_index} is outside its map bounds.')
            if not source['in_bounds']:
                problems.append(f'Source warp {name} #{index} is outside its map bounds.')
            status = ('out_of_bounds' if not source['in_bounds'] or
                      (destination is not None and not destination['in_bounds']) else resolution)
            portal = {'id': f'warp:{name}:{index}', 'kind': 'warp', 'status': status,
                      'resolution': resolution, 'source': source, 'destination': destination,
                      'dest_map': dest_map, 'dest_warp_id': value, 'dest_name': target_name,
                      'dest_index': target_index, 'reciprocal': reciprocal,
                      'symbolic': isinstance(value, str) and _number(value) is None,
                      'reason': ' '.join(problems) if problems else reason}
            portals.append(portal)
            if status in ('unresolved', 'out_of_bounds'):
                issues.append({'id': portal['id'], 'map': name, 'index': index,
                               'status': status, 'reason': portal['reason']})
    totals = Counter(portal['status'] for portal in portals)
    counts = {status: totals[status] for status in _STATUSES}
    counts.update(total=len(portals), broken=len(issues),
                  one_way=sum(portal['status'] == 'fixed' and not portal['reciprocal'] for portal in portals),
                  reciprocal=sum(portal['status'] == 'fixed' and portal['reciprocal'] for portal in portals))
    return {'revision': snapshot['revision'], 'portals': portals, 'counts': counts, 'issues': issues}


class WorldLinkAudit:
    def __init__(self, source):
        self.connections = Connections(source)

    def catalog(self, positions=None):
        return audit_warps(self.connections._snapshot(), positions)
