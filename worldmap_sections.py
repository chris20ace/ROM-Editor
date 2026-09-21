"""Partition map rectangles into exact, undistorted connected sections.

Source connection constraints are never changed or silently omitted inside a
section. Connections between returned sections remain explicit travel links.
"""
from collections import deque
from itertools import combinations


def partition_maps(names, dimensions, edges):
    """Return ``{'names': [...], 'positions': {name: (x, y)}}`` sections.

    ``edges`` uses the world map model's (a, b, direction, offset, dx, dy)
    records. Positions are local to each section, with its alphabetically first
    map at (0, 0); callers choose where to place the complete section.

    Wide shared boundaries are merged first, followed by canonical map names.
    This keeps long contiguous map borders without favoring named locations.
    Every proposed union is checked against *all* its source constraints and
    full map rectangles, not just the edge that proposed the union.
    """
    ordered_names = list(dict.fromkeys(names))
    members = set(ordered_names)
    if not members:
        return []
    for name in members:
        width, height = dimensions[name]
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ValueError(f'Invalid map dimensions for {name}.')

    constraints = {}
    for a, b, direction, offset, dx, dy in edges:
        if a not in members or b not in members:
            continue
        if a == b:
            if dx or dy:
                raise ValueError(f'Map {a} has a self-connection that cannot form a flat section.')
            continue
        axis = 0 if direction in {'up', 'down'} else 1
        span = max(0, min(dimensions[a][axis], offset + dimensions[b][axis]) - max(0, offset))
        if a > b:
            a, b, dx, dy = b, a, -dx, -dy
        key = a, b, dx, dy
        # Reciprocal records describe the same physical boundary. Retaining
        # different translations for the same pair catches malformed cycles.
        constraints[key] = max(span, constraints.get(key, 0))

    adjacency = {name: [] for name in members}
    for a, b, dx, dy in sorted(constraints):
        adjacency[a].append((b, dx, dy))
        adjacency[b].append((a, -dx, -dy))

    def exact_positions(group):
        first = min(group)
        positions = {first: (0, 0)}
        queue = deque([first])
        while queue:
            a = queue.popleft()
            ax, ay = positions[a]
            for b, dx, dy in adjacency[a]:
                if b not in group:
                    continue
                expected = ax + dx, ay + dy
                if b in positions:
                    if positions[b] != expected:
                        return None
                else:
                    positions[b] = expected
                    queue.append(b)
        if len(positions) != len(group):
            return None
        for a, b in combinations(sorted(group), 2):
            ax, ay = positions[a]
            bx, by = positions[b]
            aw, ah = dimensions[a]
            bw, bh = dimensions[b]
            if min(ax + aw, bx + bw) > max(ax, bx) and min(ay + ah, by + bh) > max(ay, by):
                return None
        return positions

    complete_positions = exact_positions(members)
    if complete_positions is not None:
        return [{'names': ordered_names, 'positions': complete_positions}]

    groups = {name: {name} for name in members}
    owner = {name: name for name in members}
    positions_by_group = {name: {name: (0, 0)} for name in members}
    for a, b, dx, dy in sorted(constraints, key=lambda key: (-constraints[key], key)):
        a_group, b_group = owner[a], owner[b]
        if a_group == b_group:
            continue
        union = groups[a_group] | groups[b_group]
        positions = exact_positions(union)
        if positions is None:
            continue
        # A rejected union can never become valid by adding more maps: its
        # contradictory cycle or positive-area overlap would still be present.
        new_owner = min(union)
        del groups[a_group], groups[b_group]
        del positions_by_group[a_group], positions_by_group[b_group]
        groups[new_owner] = union
        positions_by_group[new_owner] = positions
        for name in union:
            owner[name] = new_owner

    result = []
    emitted = set()
    for name in ordered_names:
        group = owner[name]
        if group in emitted:
            continue
        emitted.add(group)
        result.append({'names': [candidate for candidate in ordered_names if candidate in groups[group]],
                       'positions': positions_by_group[group]})
    return result
